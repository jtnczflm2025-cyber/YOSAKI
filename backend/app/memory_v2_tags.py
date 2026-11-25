"""A3.2 internal Tag decisions and management-only proposals."""
import hashlib
import json
import uuid

from . import memory_v2_metadata as metadata
from . import memory_v2_vocabulary as vocabulary
from . import memory_v2_derivation as derivation
from . import memory_v2_tag_schema as schema
from . import memory_v2_tag_rules as rules

MERGE_GUARD_VERSION = 'tag-merge-guard-v2'
MAX_MERGE_NODES = 2048


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _begin(db):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')


def _authorized(db, entry_id, character_id, owner_id):
    schema.require(db)
    metadata._owner(db, owner_id)
    return metadata.read_draft(db, entry_id, character_id)


def _latest_decision(db, character_id, entry_id, tag_id):
    return db.execute('''SELECT * FROM mv3_entry_tag_decisions
        WHERE character_id=? AND entry_id=? AND tag_id=? ORDER BY revision DESC LIMIT 1''',
        (character_id,entry_id,tag_id)).fetchone()


def _merge_graph(db):
    """Current vocabulary graph; rows from other roles detect corrupt incoming edges."""
    return {row['id']:dict(row) for row in db.execute('''SELECT r.id,r.character_id,r.kind,
        v.status,v.merged_into,v.name,v.language,v.aliases_json
        FROM mv3_vocabulary r JOIN mv3_vocabulary_versions v
          ON v.vocabulary_id=r.id AND v.version=r.current_version''')}


def _root(graph, start_id, character_id):
    seen=set(); current=start_id
    while True:
        if current in seen or len(seen)>=MAX_MERGE_NODES:
            raise ValueError('tag_merge_invalid')
        seen.add(current)
        row=graph.get(current)
        if not row or row['character_id']!=character_id or row['kind']!='tag':
            raise ValueError('tag_merge_invalid')
        next_id=row['merged_into']
        if next_id is None:
            if row['status']!='active':
                raise ValueError('tag_merge_invalid')
            return current
        if row['status']!='deprecated':
            raise ValueError('tag_merge_invalid')
        current=next_id


def merge_guard(db, character_id, entry_id, target_id, *, graph=None):
    """Return a stable blocker or None; reject malformed relevant merge graphs.

    The reverse closure includes *all* incoming sources, including multiple
    branches. A target keep never cancels an ancestor's suppress. Call under
    the final write transaction as well as at management read time.
    """
    graph=_merge_graph(db) if graph is None else graph
    if _root(graph,target_id,character_id)!=target_id:
        raise ValueError('tag_merge_invalid')
    incoming={}
    for row in graph.values():
        if row['merged_into'] is not None:
            incoming.setdefault(row['merged_into'],[]).append(row['id'])
    related=set(); queue=[target_id]
    while queue:
        current=queue.pop()
        if current in related or len(related)>=MAX_MERGE_NODES:
            raise ValueError('tag_merge_invalid')
        related.add(current)
        for child_id in incoming.get(current,()):
            child=graph[child_id]
            if (child['character_id']!=character_id or child['kind']!='tag' or
                    child['status']!='deprecated' or
                    _root(graph,child_id,character_id)!=target_id):
                raise ValueError('tag_merge_invalid')
            queue.append(child_id)
    for tag_id in related:
        decision=_latest_decision(db,character_id,entry_id,tag_id)
        if decision and decision['action']=='suppress':
            return 'tag_merge_suppressed'
    return None


def suggestion_guard(db, character_id, entry_id, surface, language, *, graph=None):
    """A no-ID suggestion cannot resurrect an existing or merged Tag name."""
    graph=_merge_graph(db) if graph is None else graph
    key=rules.normalized(surface)
    matches=[]
    for row in graph.values():
        if row['character_id']!=character_id or row['kind']!='tag' or row['language']!=language:
            continue
        names=[row['name'],*json.loads(row['aliases_json'])]
        if any(rules.normalized(name)==key for name in names):
            matches.append(row['id'])
    if not matches:
        return None
    for tag_id in matches:
        target=_root(graph,tag_id,character_id)
        reason=merge_guard(db,character_id,entry_id,target,graph=graph)
        if reason:
            return reason
    return 'tag_existing_vocabulary_reference'


def decisions_snapshot(db, character_id, entry_id):
    rows=db.execute('''SELECT tag_id,revision,action FROM mv3_entry_tag_decisions
        WHERE character_id=? AND entry_id=? ORDER BY tag_id,revision DESC''',
        (character_id,entry_id)).fetchall()
    latest={}
    for row in rows:
        latest.setdefault(row['tag_id'], (row['revision'],row['action']))
    return _digest(latest)


def reopened_snapshot(db, character_id, entry_id):
    """A deliberate reopen changes the next task input without rekeying evidence."""
    rows=db.execute('''SELECT proposal_key,MAX(revision) AS revision
        FROM mv3_tag_proposal_revisions WHERE character_id=? AND entry_id=?
        AND state='reopened' GROUP BY proposal_key ORDER BY proposal_key''',
        (character_id,entry_id)).fetchall()
    return _digest([(row['proposal_key'],row['revision']) for row in rows])


def decide(db, entry_id, character_id, tag_id, action, expected_revision,
           expected_field_token, owner_id):
    """Optimistic owner decision; suppression and field removal share one transaction."""
    _begin(db)
    detail=_authorized(db,entry_id,character_id,owner_id)
    if action not in ('keep','suppress','revoke') or type(expected_revision) is not int or expected_revision<0:
        raise ValueError('tag_decision_invalid')
    term=vocabulary.get(db,character_id,tag_id)
    if term['kind']!='tag': raise ValueError('tag_unavailable')
    field=detail['fields']['tags']
    if field.get('token')!=expected_field_token:
        raise ValueError('tag_field_changed')
    prior=_latest_decision(db,character_id,entry_id,tag_id)
    if expected_revision!=(prior['revision'] if prior else 0):
        raise ValueError('tag_decision_conflict')
    current=field['value'] if isinstance(field['value'],list) else []
    present=any(isinstance(item,dict) and item.get('id')==tag_id for item in current)
    if action=='keep' and (not present or field['lifecycleStatus']=='stale'
                           or term['status']!='active'):
        raise ValueError('tag_keep_requires_current_evidence')
    prior_field=field.get('fieldId')
    db.execute('SAVEPOINT a32_owner_decision')
    try:
        db.execute('''INSERT INTO mv3_entry_tag_decisions
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (str(uuid.uuid4()),character_id,entry_id,tag_id,expected_revision+1,action,
             detail['sourceToken'],prior_field,owner_id,derivation._now()))
        result_set=detail['versionId']
        if action=='suppress' and present:
            remaining=[item for item in current if item['id']!=tag_id]
            result=metadata.revise(db,entry_id,character_id,detail['versionId'],
                {'tags':remaining},owner_id,provenance='user_corrected',rule='a32-owner-suppress-v1')
            result_set=result['versionId']
        db.execute('RELEASE a32_owner_decision')
    except BaseException:
        db.execute('ROLLBACK TO a32_owner_decision')
        db.execute('RELEASE a32_owner_decision')
        raise
    return {'revision':expected_revision+1,'action':action,'fieldSetId':result_set}


def proposal_key(character_id, entry_id, item):
    point=item['evidence'][0]
    # No offset, source token, task ID, alias version or rule version: an
    # unrelated edit cannot make rejected evidence look new.
    return _digest(['a32-evidence-v1',character_id,entry_id,
        point['kind'],point['id'],item['tagId'] or
        [rules.normalized(item['surface']),item['language']],
        rules.normalized(item['evidenceText'])])


def _latest_proposal(db, character_id, entry_id, key):
    return db.execute('''SELECT * FROM mv3_tag_proposal_revisions
        WHERE character_id=? AND entry_id=? AND proposal_key=?
        ORDER BY revision DESC LIMIT 1''', (character_id,entry_id,key)).fetchone()


def add_proposal(db, character_id, entry_id, item, source_token, dependencies,
                 job_id, job_hash):
    """Only call from a final validated local write transaction."""
    schema.require(db)
    if dependencies.get('mergeGuardVersion')!=MERGE_GUARD_VERSION:
        raise ValueError('tag_merge_guard_version_required')
    if item['tagId']:
        if merge_guard(db,character_id,entry_id,item['tagId']):
            return False
    elif suggestion_guard(db,character_id,entry_id,item['surface'],item['language']):
        return False
    key=proposal_key(character_id,entry_id,item)
    old=_latest_proposal(db,character_id,entry_id,key)
    if old and old['state'] in ('rejected','handled'):
        return False
    deps=_json(dependencies)
    evidence=_json(item['evidence'])
    if old and old['state']=='pending' and old['source_token']==source_token and old['dependency_json']==deps:
        return False
    rev=old['revision']+1 if old else 1
    db.execute('''INSERT INTO mv3_tag_proposal_revisions VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (str(uuid.uuid4()),character_id,entry_id,key,rev,item['tagId'],item['surface'],
         item['language'],evidence,source_token,deps,rules.RULE_VERSION,'pending',
         job_id,job_hash,None,derivation._now()))
    return True


def review_proposal(db, entry_id, character_id, proposal_key_value,
                    expected_revision, action, owner_id):
    _begin(db)
    detail=_authorized(db,entry_id,character_id,owner_id)
    if action not in ('rejected','handled','reopened') or type(expected_revision) is not int:
        raise ValueError('proposal_action_invalid')
    old=_latest_proposal(db,character_id,entry_id,proposal_key_value)
    if not old or old['revision']!=expected_revision:
        raise ValueError('proposal_revision_conflict')
    if old['source_token']!=detail['sourceToken']:
        raise ValueError('proposal_source_changed')
    reason=_proposal_invalid_reason(db,character_id,entry_id,old,detail)
    if reason:
        raise ValueError(reason)
    # Reopening is a management decision, not an approval or an active Tag.
    db.execute('''INSERT INTO mv3_tag_proposal_revisions VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (str(uuid.uuid4()),character_id,entry_id,proposal_key_value,expected_revision+1,
         old['tag_id'],old['surface'],old['language'],old['evidence_json'],
         old['source_token'],old['dependency_json'],old['rule_version'],action,
         old['origin_job_id'],old['origin_job_hash'],owner_id,derivation._now()))
    return {'revision':expected_revision+1,'state':action}


def _proposal_invalid_reason(db, character_id, entry_id, row, detail):
    if row['source_token']!=detail['sourceToken'] or row['rule_version']!=rules.RULE_VERSION:
        return 'proposal_stale'
    deps=json.loads(row['dependency_json'])
    if (deps.get('fieldToken')!=detail['fields']['tags'].get('token') or
            deps.get('mergeGuardVersion')!=MERGE_GUARD_VERSION):
        return 'proposal_stale'
    graph=_merge_graph(db)
    if row['tag_id']:
        root=_root(graph,row['tag_id'],character_id)
        reason=merge_guard(db,character_id,entry_id,root,graph=graph)
        if reason:
            return reason
        term=vocabulary.get(db,character_id,row['tag_id'])
        if (term['status']!='active' or term['kind']!='tag' or
                term['semantic_token']!=deps.get('semanticToken') or
                term['alias_token']!=deps.get('aliasToken')):
            return 'proposal_stale'
    else:
        reason=suggestion_guard(db,character_id,entry_id,row['surface'],row['language'],graph=graph)
        if reason:
            return reason
    for point in json.loads(row['evidence_json']):
        source=derivation.live_source(db,character_id,point['kind'],point['id'])
        if source is None or not 0<=point['start']<point['end']<=len(source.text):
            return 'proposal_stale'
        if _digest(source.text[point['start']:point['end']])!=deps.get('evidenceHash'):
            return 'proposal_stale'
    return None


def list_proposals(db, entry_id, character_id, owner_id, *, limit=20, offset=0):
    """Authorized internal read, with live source and dependency revalidation."""
    if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or offset<0:
        raise ValueError('proposal_page_invalid')
    # A read transaction prevents authorization and pagination from observing
    # different committed role/source states.
    if not db.in_transaction:
        db.execute('BEGIN')
    detail=_authorized(db,entry_id,character_id,owner_id)
    rows=db.execute('''SELECT p.* FROM mv3_tag_proposal_revisions p
      WHERE p.character_id=? AND p.entry_id=? AND p.revision=(
        SELECT MAX(q.revision) FROM mv3_tag_proposal_revisions q
        WHERE q.character_id=p.character_id AND q.entry_id=p.entry_id
          AND q.proposal_key=p.proposal_key)
      ORDER BY p.created_at,p.id LIMIT ? OFFSET ?''',
      (character_id,entry_id,limit,offset)).fetchall()
    output=[]
    for row in rows:
        safe={'proposalKey':row['proposal_key'],'revision':row['revision'],'state':row['state']}
        try:
            reason=_proposal_invalid_reason(db,character_id,entry_id,row,detail)
            if reason:
                safe.update(valid=False,reason=reason)
            else:
                safe.update(surface=row['surface'],language=row['language'],tagId=row['tag_id'],
                            evidence=json.loads(row['evidence_json']),valid=True)
        except (ValueError,TypeError,KeyError):
            safe.update(valid=False,reason='tag_merge_invalid')
        output.append(safe)
    return output
