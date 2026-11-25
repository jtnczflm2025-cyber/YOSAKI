"""A3.0 local contract coordinator. No provider, scheduler or API access."""
import hashlib
import json
import time
import uuid
from . import memory_v2_metadata_contract as contract
from . import memory_v2_processing_schema as schema


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _transaction(db):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')


def _owned(db, job_id, character_id):
    row = db.execute('''SELECT j.* FROM mv3_jobs j JOIN characters c ON c.id=j.character_id
        WHERE j.id=? AND j.character_id=? AND c.enabled=1''', (job_id, character_id)).fetchone()
    if row is None or row['contract_version'] != contract.A3_CONTRACT:
        raise ValueError('job_not_accessible')
    return dict(row)


def _scope(character_id, target_kind, target_id, fields):
    if target_kind not in ('entry', 'period') or not isinstance(target_id, str) or not target_id:
        raise ValueError('invalid_work_scope')
    allowed = {'digest'} if target_kind == 'period' else set(contract.FIELDS)
    if not fields or len(fields) != len(set(fields)) or not set(fields) <= allowed:
        raise ValueError('invalid_target_fields')
    return _hash(['a3-scope-v1', character_id, target_kind, target_id])


def _block_overlaps(db, character_id, scope_key, fields, *, except_id=None):
    if except_id:
        from . import memory_v2_synopsis_replacement as replacement
        replacement.require_business_current(db, except_id)
    if db.execute("SELECT 1 FROM mv3_jobs WHERE character_id=? AND scope_key IS NULL AND status='unknown' AND id<>?",
                  (character_id, except_id or '')).fetchone():
        raise ValueError('legacy_unknown_scope')
    for row in db.execute('''SELECT id,write_set_json FROM mv3_jobs WHERE character_id=?
        AND scope_key=? AND status='unknown' ''', (character_id, scope_key)):
        if row['id'] != except_id and set(json.loads(row['write_set_json'])['fields']) & set(fields):
            from . import memory_v2_synopsis_replacement as replacement
            if not (except_id and replacement.may_ignore_original(db, except_id, row['id'])):
                raise ValueError('semantic_scope_unknown')


def create(db, character_id, target_kind, target_id, fields, read_set,
           *, rule_version, input_version, origin='manual', execution_kind=None):
    """Scheduler/manual/new-id entrypoints share this single scope check."""
    if not schema.installed(db):
        raise ValueError('a3_migration_required')
    _transaction(db)
    if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1', (character_id,)).fetchone():
        raise ValueError('character_not_accessible')
    if origin not in ('manual', 'scheduler'):
        raise ValueError('invalid_origin')
    fields = sorted(fields)
    scope = _scope(character_id, target_kind, target_id, fields)
    _block_overlaps(db, character_id, scope, fields)
    return _create_record(db, character_id, target_kind, target_id, fields, read_set,
                          rule_version=rule_version, input_version=input_version,
                          execution_kind=execution_kind)


def _create_record(db, character_id, target_kind, target_id, fields, read_set, *,
                   rule_version, input_version, execution_kind):
    scope = _scope(character_id, target_kind, target_id, fields)
    if not isinstance(read_set, dict) or not read_set:
        raise ValueError('read_set_required')
    payload = {'targetKind':target_kind,'targetId':target_id,'fields':fields,
               'readSet':read_set,'ruleVersion':rule_version,'inputVersion':input_version}
    if execution_kind is not None:
        if execution_kind not in ('local_categories_v1', 'local_tags_v1', 'local_keywords_v1',
                                  'mock_synopsis_v1', 'responses_synopsis_v1', 'mock_narrative_v1',
                                  'mock_temporal_digest_v1', 'chronicle_embedding_v1',
                                  'chronicle_semantic_supplement_v1','responses_temporal_digest_v1','chronicle_calendar_synopsis_v1','chronicle-import-plan-synopsis-v1'):
            raise ValueError('invalid_execution_kind')
        payload['executionKind'] = execution_kind
    fingerprint = _hash(['a3-job-v1', character_id, payload])
    row = db.execute('SELECT id FROM mv3_jobs WHERE character_id=? AND kind=? AND input_hash=?',
                     (character_id,'A3_PROCESSING',fingerprint)).fetchone()
    if row:
        return row['id']
    job_id = str(uuid.uuid4())
    db.execute('''INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,
        created_at,contract_version,scope_key,read_set_json,write_set_json)
        VALUES(?,?,?,?,?,'queued',datetime('now'),?,?,?,?)''',
        (job_id,character_id,'A3_PROCESSING',fingerprint,_canonical(payload),
         contract.A3_CONTRACT,scope,_canonical(read_set),_canonical({'fields':fields})))
    return job_id


def prepare_chunks(db, job_id, character_id, chunks, *, lease_token=None):
    _transaction(db)
    job = _owned(db, job_id, character_id)
    live_claim = (lease_token is not None and job['status'] == 'running'
                  and job['lease_token'] == lease_token and job['lease_until'] is not None
                  and job['lease_until'] > time.time() and not job['cancel_intent'])
    if not chunks or not (job['status'] == 'queued' if lease_token is None else live_claim):
        raise ValueError('not_preparable')
    prior = db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(job_id,)).fetchone()[0]
    if prior:
        if prior != len(chunks):
            raise ValueError('chunk_plan_changed')
        return prior
    for ordinal, item in enumerate(chunks):
        if type(item.get('reservedTokens')) is not int or item['reservedTokens'] <= 0:
            raise ValueError('invalid_reservation')
        if type(item.get('reservedCostUnits',0)) is not int or item.get('reservedCostUnits',0) < 0:
            raise ValueError('invalid_reservation')
        db.execute('''INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,
            state,reserved_tokens,reserved_cost_units) VALUES(?,?,?,?,?,'prepared',?,?)''',
            (job_id,ordinal,str(uuid.uuid4()),_hash(item),_canonical({'inputHash':_hash(item)}),
             item['reservedTokens'],item.get('reservedCostUnits',0)))
    return len(chunks)


def dispatch(db, job_id, character_id, ordinal, *, original_owner_id=None):
    """Commit this transaction before any adapter call; return durable identity."""
    _transaction(db)
    job = _owned(db, job_id, character_id)
    fields=json.loads(job['write_set_json'])['fields']
    _block_overlaps(db, character_id, job['scope_key'], fields, except_id=job_id)
    if job['cancel_intent'] or job['status'] not in ('queued','running','unknown'):
        raise ValueError('not_dispatchable')
    if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')",(job_id,)).fetchone():
        raise ValueError('unsettled_request')
    row=db.execute("SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=? AND state='prepared'",(job_id,ordinal)).fetchone()
    if row is None:
        raise ValueError('chunk_not_prepared')
    from . import memory_v2_jobs as jobs
    proof=jobs.persist_dispatch_identity(db,job_id,character_id,ordinal,
                                         original_owner_id=original_owner_id)
    return {'requestId':row['request_id'],'inputHash':row['input_hash'],
            'dispatchProof':proof,
            'reservedTokens':row['reserved_tokens'],'reservedCostUnits':row['reserved_cost_units']}


def mark_unknown(db,job_id,character_id,ordinal,request_id,*,dispatch_proof=None):
    _transaction(db)
    _owned(db,job_id,character_id)
    from . import memory_v2_jobs as jobs
    return jobs.settle_dispatch(db,job_id,character_id,ordinal,request_id,dispatch_proof,state='unknown')


def cancel(db,job_id,character_id):
    _transaction(db)
    job=_owned(db,job_id,character_id)
    if job['status']=='unknown':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(job_id,))
        return 'unknown'
    if job['status'] not in ('queued','paused'):
        raise ValueError('not_cancellable')
    db.execute("UPDATE mv3_jobs SET status='cancelled',cancel_intent=1 WHERE id=?",(job_id,))
    return 'cancelled'


def resume(db,job_id,character_id,current_read_set):
    _transaction(db)
    job=_owned(db,job_id,character_id)
    from .memory_v2_jobs import require_settled_for_resume
    require_settled_for_resume(db,job)
    _block_overlaps(db,character_id,job['scope_key'],json.loads(job['write_set_json'])['fields'],except_id=job_id)
    if job['status']!='paused' or job['cancel_intent'] or _canonical(current_read_set)!=job['read_set_json']:
        raise ValueError('not_resumable')
    if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')",(job_id,)).fetchone():
        raise ValueError('unsettled_request')
    db.execute("UPDATE mv3_jobs SET status='queued' WHERE id=?",(job_id,))
    return 'queued'


def reconcile(db,job_id,character_id,ordinal,request_id,*,evidence,dispatch_proof=None,
              used_input_tokens=None,used_output_tokens=None,current_read_set=None):
    """Only mock-confirmed outcome or proven non-dispatch; no client state toggle."""
    _transaction(db)
    job=_owned(db,job_id,character_id)
    from . import memory_v2_jobs as jobs
    row=jobs.validate_dispatch_identity(db,job_id,character_id,ordinal,request_id,dispatch_proof)
    if not isinstance(evidence,dict) or evidence.get('requestId')!=request_id or evidence.get('kind') not in ('mock_completed','mock_not_dispatched'):
        raise ValueError('reconciliation_evidence_invalid')
    if evidence['kind']=='mock_completed':
        if any(type(x) is not int or x<0 for x in (used_input_tokens,used_output_tokens)):
            raise ValueError('usage_missing')
        if used_input_tokens+used_output_tokens>row['reserved_tokens']:
            raise ValueError('budget_exceeded')
        state='completed'
    else:
        state='rejected'
    if state=='rejected':
        used_input_tokens=used_output_tokens=0
    jobs.settle_dispatch(db,job_id,character_id,ordinal,request_id,dispatch_proof,
        state=state,used_input=used_input_tokens,used_output=used_output_tokens,
        used_cost=0 if state=='rejected' else row['reserved_cost_units'],result_hash=_hash(evidence))
    db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
        VALUES(?,?,?,?,datetime('now'))''',(job_id,'mock_reconcile',_hash(evidence),job['status']))
    unchanged=current_read_set is not None and _canonical(current_read_set)==job['read_set_json']
    from .memory_v2_jobs import recompute_parent
    return recompute_parent(db,job_id,character_id,
        error=None if unchanged else 'job_input_changed',safe_pause=True)


def _category_snapshot(db, entry_id, character_id):
    """Only confirmed, ordered entry sources and versioned controlled IDs."""
    from . import memory_v2_metadata as metadata, memory_v2_vocabulary as vocab
    from . import memory_v2_category_rules as rules
    import uuid as _uuid
    detail = metadata.read_draft(db, entry_id, character_id)
    boundary = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv2_import_segments'").fetchone():
        row = db.execute('''SELECT s.*,f.status AS file_status,f.character_id AS file_character,
            b.plan_version,b.plan_hash,b.source_snapshot_hash
            FROM mv2_import_segments s JOIN mv2_import_files f ON f.id=s.file_id
            LEFT JOIN mv2_import_boundary_confirmations b ON b.file_id=s.file_id
            WHERE s.entry_id=?''', (entry_id,)).fetchone()
        if row:
            if (row['file_status'] != 'raw_complete' or row['file_character'] != character_id
                    or row['plan_version'] is None):
                raise ValueError('boundary_not_confirmed')
            receipt_rows = db.execute('''SELECT r.file_id,r.ordinal FROM json_each(?) j
                LEFT JOIN mv2_import_receipts r
                  ON r.message_id=CAST(json_extract(j.value,'$.source_id') AS INTEGER)
                ORDER BY CAST(j.key AS INTEGER)''',
                (_canonical(detail['sourceRefs']),)).fetchall()
            expected = list(range(row['start_ordinal'], row['end_ordinal']+1))
            if (any(r['source_kind'] != 'message' for r in detail['sourceRefs']) or
                    len(receipt_rows) != len(detail['sourceRefs']) or
                    [r['file_id'] for r in receipt_rows] != [row['file_id']]*len(expected) or
                    [r['ordinal'] for r in receipt_rows] != expected):
                raise ValueError('boundary_sources_changed')
            boundary = {'segmentId': row['id'], 'fileId': row['file_id'],
                        'planVersion': row['plan_version'], 'planHash': row['plan_hash'],
                        'sourceSnapshotHash': row['source_snapshot_hash'],
                        'start': row['start_ordinal'], 'end': row['end_ordinal']}
    lexicon = []
    for dimension, name in rules.RULE_DEFINITIONS:
        position = contract.CATEGORIES[dimension].index(name)
        vid = str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                              f'yosaki/category/{character_id}/{dimension}/{position}'))
        term = vocab.get(db, character_id, vid)
        if not rules.compatible_vocabulary(term, (dimension, name)):
            raise ValueError('category_vocabulary_unavailable')
        lexicon.append({'dimension':dimension,'name':name,'id':vid,
                        'semanticToken':term['semantic_token'],'aliasToken':term['alias_token'],
                        'normalizationVersion':term['normalization_version']})
    field = detail['fields']['categories']
    return {'entryId':entry_id,'versionId':detail['versionId'],
            'sourceToken':detail['sourceToken'],'sourceRefs':detail['sourceRefs'],
            'boundary':boundary,'categoryFieldToken':field.get('token'),
            'categoryFieldState':field['lifecycleStatus'],
            'ruleVersion':rules.RULE_VERSION,'lexicon':lexicon}


def create_category_job(db, entry_id, character_id, editor_id):
    """Owner-scoped local job. No model chunks, request identity or cost."""
    from . import memory_v2_metadata as metadata, memory_v2_category_rules as rules
    _transaction(db)
    metadata._owner(db, editor_id)
    read_set = _category_snapshot(db, entry_id, character_id)
    return create(db, character_id, 'entry', entry_id, ['categories'], read_set,
                  rule_version=rules.RULE_VERSION, input_version='entry-sources-v1',
                  execution_kind='local_categories_v1')


def _category_job(db, job_id, character_id):
    from . import memory_v2_category_rules as rules
    job = _owned(db, job_id, character_id)
    payload = json.loads(job['payload_json'])
    if (payload.get('targetKind') != 'entry' or payload.get('fields') != ['categories']
            or payload.get('inputVersion') != 'entry-sources-v1'
            or payload.get('executionKind') != 'local_categories_v1'
            or payload.get('ruleVersion') not in ('category-direct-evidence-v1',
                                                  'category-direct-evidence-v2')
            or payload.get('readSet', {}).get('ruleVersion') != payload.get('ruleVersion')
            or job['kind'] != 'A3_PROCESSING'):
        raise ValueError('not_category_job')
    return job, payload


def cancel_category_job(db, job_id, character_id, editor_id):
    """Local running work records intent; the short final transaction obeys it."""
    from . import memory_v2_metadata as metadata
    _transaction(db); metadata._owner(db, editor_id)
    job, _ = _category_job(db, job_id, character_id)
    if job['status'] == 'running':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?', (job_id,))
        return 'running'
    return cancel(db, job_id, character_id)


def recover_category_job(db, job_id, character_id, editor_id, *, now=None):
    """Only local work with no request/chunk may be safely paused after crash."""
    from . import memory_v2_metadata as metadata
    _transaction(db); metadata._owner(db, editor_id)
    job, _ = _category_job(db, job_id, character_id)
    _block_overlaps(db, character_id, job['scope_key'], ['categories'], except_id=job_id)
    if (job['status'] != 'running' or job['lease_until'] is None or
            job['lease_until'] > (time.time() if now is None else now) or
            db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?', (job_id,)).fetchone()):
        raise ValueError('local_recovery_not_proven')
    state = 'cancelled' if job['cancel_intent'] else 'paused'
    db.execute('UPDATE mv3_jobs SET status=?,lease_token=NULL,lease_until=NULL WHERE id=?',
               (state, job_id))
    return state


def run_category_job(connect, job_id, character_id, editor_id):
    """Claim, evaluate off-transaction, then atomically check and commit."""
    from . import memory_v2_metadata as metadata, memory_v2_derivation as m3
    from . import memory_v2_category_rules as rules
    from . import memory_v2_vocabulary as vocab
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _category_job(db, job_id, character_id)
        if job['status'] == 'completed':
            row = db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?', (job_id,)).fetchone()
            if row is None: raise ValueError('completed_result_missing')
            detail=metadata.read_draft(db,payload['targetId'],character_id)
            if (detail['versionId']!=row['set_id'] or
                    detail['sourceToken']!=json.loads(job['read_set_json'])['sourceToken'] or
                    detail['fields']['categories']['lifecycleStatus']=='stale'):
                raise ValueError('category_result_superseded')
            return {'status':'completed','fieldSetId':row['set_id'],'reused':True}
        _block_overlaps(db, character_id, job['scope_key'], ['categories'], except_id=job_id)
        if job['status'] != 'queued' or job['cancel_intent']:
            raise ValueError('category_job_not_claimable')
        if db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?',(job_id,)).fetchone():
            raise ValueError('local_job_has_model_chunks')
        if _category_snapshot(db, payload['targetId'], character_id) != json.loads(job['read_set_json']):
            raise ValueError('category_input_changed')
        lease = str(uuid.uuid4())
        db.execute('''UPDATE mv3_jobs SET status='running',attempts=attempts+1,
            lease_token=?,lease_until=? WHERE id=? AND status='queued' ''',
            (lease,time.time()+60,job_id))
    with connect() as db:
        detail = metadata.read_draft(db, payload['targetId'], character_id)
        sources = []
        for ref in detail['sourceRefs']:
            source = m3.live_source(db, character_id, ref['source_kind'], ref['source_id'])
            sources.append({'kind':source.kind,'id':source.source_id,
                            'role':source.role,'text':source.text})
        evaluation = rules.evaluate(sources)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        current, payload = _category_job(db, job_id, character_id)
        if current['status'] != 'running' or current['lease_token'] != lease or current['lease_until'] <= time.time():
            raise ValueError('category_lease_changed')
        _block_overlaps(db, character_id, current['scope_key'], ['categories'], except_id=job_id)
        if current['cancel_intent']:
            db.execute("UPDATE mv3_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
            return {'status':'cancelled'}
        try:
            unchanged = (_category_snapshot(db, payload['targetId'], character_id)
                         == json.loads(current['read_set_json']))
        except ValueError:
            unchanged = False
        if not unchanged:
            db.execute("UPDATE mv3_jobs SET status='failed',error_code='category_input_changed',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
            return {'status':'failed','reason':'category_input_changed'}
        detail = metadata.read_draft(db,payload['targetId'],character_id)
        old = metadata._fields(db,detail['versionId']).get('categories')
        if old and (old['locked'] or old['provenance'] in ('manual','user_corrected') or
                    db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',(old['id'],)).fetchone()):
            evaluation = {'hits':[],'reasonCodes':['existing_field_protected']}
        lexicon = {(x['dimension'],x['name']):x for x in json.loads(current['read_set_json'])['lexicon']}
        changes=[]
        for hit in evaluation['hits']:
            item=lexicon[(hit['dimension'],hit['name'])]
            term=vocab.get(db,character_id,item['id'])
            if (not rules.compatible_vocabulary(term, (hit['dimension'],hit['name']))
                    or term['semantic_token']!=item['semanticToken']):
                raise ValueError('category_vocabulary_changed')
            for point in hit['evidence']:
                source=m3.live_source(db,character_id,point['kind'],point['id'])
                if not source.text[point['start']:point['end']]:
                    raise ValueError('category_evidence_changed')
            changes.append({'id':item['id'],'evidence':hit['evidence']})
        result_set = detail['versionId']
        if changes:
            # An existing identical automatic candidate is reused even if a
            # new caller chose a different idempotency key or task ID.
            old_value = json.loads(old['value_json']) if old else None
            if not (old and old['provenance']=='rule' and old['rule_version']==rules.RULE_VERSION
                    and old_value==changes and
                    detail['fields']['categories']['lifecycleStatus']!='stale'):
                points=[point for item in changes for point in item['evidence']]
                result=metadata.revise(db,payload['targetId'],character_id,detail['versionId'],
                    {'categories':changes},editor_id,provenance='rule',
                    rule=rules.RULE_VERSION,evidence=points)
                result_set=result['versionId']
        db.execute('INSERT INTO mv3_job_field_results(job_id,set_id) VALUES(?,?)',(job_id,result_set))
        event = ('category_candidate' if changes else
                 'category_'+(evaluation['reasonCodes'][0] if evaluation['reasonCodes'] else 'no_supported_pair'))
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,?,?,?,datetime('now'))''',
            (job_id,event,_hash([rules.RULE_VERSION,detail['sourceToken'],evaluation['reasonCodes']]),'running'))
        db.execute("UPDATE mv3_jobs SET status='completed',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
        return {'status':'completed','fieldSetId':result_set,'reused':False,
                'candidateCount':len(changes),'reasonCodes':evaluation['reasonCodes']}


def _tag_snapshot(db, entry_id, character_id):
    from . import memory_v2_metadata as metadata, memory_v2_vocabulary as vocab
    from . import memory_v2_tag_rules as rules, memory_v2_tags as tags
    from . import memory_v2_tag_schema as tag_schema
    tag_schema.require(db)
    detail=metadata.read_draft(db,entry_id,character_id)
    boundary=None
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv2_import_segments'").fetchone():
        row=db.execute('''SELECT s.*,f.status AS file_status,f.character_id AS file_character,
            b.plan_version,b.plan_hash,b.source_snapshot_hash
            FROM mv2_import_segments s JOIN mv2_import_files f ON f.id=s.file_id
            LEFT JOIN mv2_import_boundary_confirmations b ON b.file_id=s.file_id
            WHERE s.entry_id=?''',(entry_id,)).fetchone()
        if row:
            if row['file_status']!='raw_complete' or row['file_character']!=character_id or row['plan_version'] is None:
                raise ValueError('boundary_not_confirmed')
            receipt_rows=db.execute('''SELECT r.file_id,r.ordinal FROM json_each(?) j
                LEFT JOIN mv2_import_receipts r
                  ON r.message_id=CAST(json_extract(j.value,'$.source_id') AS INTEGER)
                ORDER BY CAST(j.key AS INTEGER)''',(_canonical(detail['sourceRefs']),)).fetchall()
            expected=list(range(row['start_ordinal'],row['end_ordinal']+1))
            if (any(r['source_kind']!='message' for r in detail['sourceRefs']) or
                len(receipt_rows)!=len(expected) or
                [r['file_id'] for r in receipt_rows]!=[row['file_id']]*len(expected) or
                [r['ordinal'] for r in receipt_rows]!=expected):
                raise ValueError('boundary_sources_changed')
            boundary={'segmentId':row['id'],'fileId':row['file_id'],
                'planVersion':row['plan_version'],'planHash':row['plan_hash'],
                'sourceSnapshotHash':row['source_snapshot_hash'],
                'start':row['start_ordinal'],'end':row['end_ordinal']}
    lexicon=[]
    for row in db.execute("SELECT id FROM mv3_vocabulary WHERE character_id=? AND kind='tag' ORDER BY id",(character_id,)):
        term=vocab.get(db,character_id,row['id'])
        lexicon.append({k:term[k] for k in ('vocabulary_id','kind','status','name','language',
            'aliases_json','semantic_token','alias_token','normalization_version','merged_into')})
    field=detail['fields']['tags']
    return {'entryId':entry_id,'versionId':detail['versionId'],
        'sourceToken':detail['sourceToken'],'sourceRefs':detail['sourceRefs'],
        'boundary':boundary,'tagFieldToken':field.get('token'),
        'tagFieldState':field['lifecycleStatus'],
        'decisionToken':tags.decisions_snapshot(db,character_id,entry_id),
        'reopenedToken':tags.reopened_snapshot(db,character_id,entry_id),
        'mergeGuardVersion':tags.MERGE_GUARD_VERSION,
        'ruleVersion':rules.RULE_VERSION,'lexicon':lexicon}


def create_tag_job(db,entry_id,character_id,editor_id):
    from . import memory_v2_metadata as metadata, memory_v2_tag_rules as rules
    _transaction(db); metadata._owner(db,editor_id)
    read_set=_tag_snapshot(db,entry_id,character_id)
    return create(db,character_id,'entry',entry_id,['tags'],read_set,
        rule_version=rules.RULE_VERSION,input_version='entry-sources-v1',
        execution_kind='local_tags_v1')


def _tag_job(db,job_id,character_id):
    from . import memory_v2_tag_rules as rules, memory_v2_tags as tags
    job=_owned(db,job_id,character_id)
    payload=json.loads(job['payload_json'])
    if (job['kind']!='A3_PROCESSING' or payload.get('targetKind')!='entry' or
        payload.get('fields')!=['tags'] or payload.get('inputVersion')!='entry-sources-v1' or
        payload.get('executionKind')!='local_tags_v1' or
        payload.get('ruleVersion')!=rules.RULE_VERSION or
        payload.get('readSet',{}).get('ruleVersion')!=rules.RULE_VERSION or
        payload.get('readSet',{}).get('mergeGuardVersion')!=tags.MERGE_GUARD_VERSION):
        raise ValueError('not_tag_job')
    return job,payload


def cancel_tag_job(db,job_id,character_id,editor_id):
    from . import memory_v2_metadata as metadata
    _transaction(db); metadata._owner(db,editor_id)
    job,_=_tag_job(db,job_id,character_id)
    if job['status']=='running':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(job_id,))
        return 'running'
    return cancel(db,job_id,character_id)


def recover_tag_job(db,job_id,character_id,editor_id,*,now=None):
    from . import memory_v2_metadata as metadata
    _transaction(db); metadata._owner(db,editor_id)
    job,_=_tag_job(db,job_id,character_id)
    _block_overlaps(db,character_id,job['scope_key'],['tags'],except_id=job_id)
    if (job['status']!='running' or job['lease_until'] is None or
        job['lease_until']>(time.time() if now is None else now) or
        db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?',(job_id,)).fetchone()):
        raise ValueError('local_recovery_not_proven')
    state='cancelled' if job['cancel_intent'] else 'paused'
    db.execute('UPDATE mv3_jobs SET status=?,lease_token=NULL,lease_until=NULL WHERE id=?',
               (state,job_id))
    return state


def run_tag_job(connect,job_id,character_id,editor_id):
    """Claim, compute without a write lock, then atomically revalidate and commit."""
    from . import memory_v2_metadata as metadata, memory_v2_derivation as m3
    from . import memory_v2_tag_rules as rules, memory_v2_tags as tags
    with connect() as db:
        db.execute('BEGIN IMMEDIATE'); metadata._owner(db,editor_id)
        job,payload=_tag_job(db,job_id,character_id)
        if job['status']=='completed':
            row=db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',(job_id,)).fetchone()
            if not row: raise ValueError('completed_result_missing')
            detail=metadata.read_draft(db,payload['targetId'],character_id)
            if detail['versionId']!=row['set_id'] or detail['sourceToken']!=json.loads(job['read_set_json'])['sourceToken']:
                raise ValueError('tag_result_superseded')
            return {'status':'completed','fieldSetId':row['set_id'],'reused':True}
        _block_overlaps(db,character_id,job['scope_key'],['tags'],except_id=job_id)
        if job['status']!='queued' or job['cancel_intent']:
            raise ValueError('tag_job_not_claimable')
        if db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?',(job_id,)).fetchone():
            raise ValueError('local_job_has_model_chunks')
        if _tag_snapshot(db,payload['targetId'],character_id)!=json.loads(job['read_set_json']):
            raise ValueError('tag_input_changed')
        lease=str(uuid.uuid4())
        db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                   (lease,time.time()+60,job_id))
    with connect() as db:
        detail=metadata.read_draft(db,payload['targetId'],character_id)
        sources=[]
        for ref in detail['sourceRefs']:
            source=m3.live_source(db,character_id,ref['source_kind'],ref['source_id'])
            sources.append({'kind':source.kind,'id':source.source_id,
                            'role':source.role,'text':source.text})
        evaluation=rules.evaluate(sources,json.loads(job['read_set_json'])['lexicon'])
    with connect() as db:
        db.execute('BEGIN IMMEDIATE'); metadata._owner(db,editor_id)
        current,payload=_tag_job(db,job_id,character_id)
        if current['status']!='running' or current['lease_token']!=lease or current['lease_until']<=time.time():
            raise ValueError('tag_lease_changed')
        _block_overlaps(db,character_id,current['scope_key'],['tags'],except_id=job_id)
        if current['cancel_intent']:
            db.execute("UPDATE mv3_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
            return {'status':'cancelled'}
        try:
            unchanged=_tag_snapshot(db,payload['targetId'],character_id)==json.loads(current['read_set_json'])
        except ValueError:
            unchanged=False
        if not unchanged:
            db.execute("UPDATE mv3_jobs SET status='failed',error_code='tag_input_changed',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
            return {'status':'failed','reason':'tag_input_changed'}
        detail=metadata.read_draft(db,payload['targetId'],character_id)
        field=detail['fields']['tags']
        old=metadata._fields(db,detail['versionId']).get('tags')
        protected=bool(old and (old['locked'] or old['provenance'] in ('manual','user_corrected') or
            db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',(old['id'],)).fetchone()))
        whole_suppressed=bool(old and json.loads(old['value_json']) is None)
        if field['lifecycleStatus']=='stale':
            raise ValueError('tag_field_stale')
        existing=json.loads(old['value_json']) if old and not whole_suppressed else []
        current_ids={x['id'] for x in existing}
        # The existing field stores one provenance for the whole list. When
        # an older automatic item cannot be reproduced by this rule, do not
        # relabel that item as direct-topic evidence in a merged revision.
        supported_ids={hit['tagId'] for hit in evaluation['hits']}
        if current_ids-supported_ids and not protected:
            protected=True
            evaluation['reasonCodes'].append('existing_auto_tags_not_revalidated')
        additions={}
        proposal_items=[]
        lexicon={x['vocabulary_id']:x for x in json.loads(current['read_set_json'])['lexicon']}
        merge_graph=tags._merge_graph(db)
        merge_reasons={}
        try:
            for tag_id in {hit['tagId'] for hit in evaluation['hits']}:
                merge_reasons[tag_id]=tags.merge_guard(
                    db,character_id,payload['targetId'],tag_id,graph=merge_graph)
        except ValueError:
            db.execute("UPDATE mv3_jobs SET status='failed',error_code='tag_merge_invalid',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
            return {'status':'failed','reason':'tag_merge_invalid'}
        if current_ids & {tag_id for tag_id,reason in merge_reasons.items() if reason}:
            protected=True
        for hit in evaluation['hits']:
            tag_id=hit['tagId']
            if merge_reasons[tag_id]:
                if merge_reasons[tag_id] not in evaluation['reasonCodes']:
                    evaluation['reasonCodes'].append(merge_reasons[tag_id])
                continue
            if whole_suppressed: continue
            if protected:
                if tag_id not in current_ids: proposal_items.append(hit)
            else:
                additions.setdefault(tag_id,[]).extend(hit['evidence'])
        if not whole_suppressed:
            for item in evaluation['suggestions']:
                try:
                    reason=tags.suggestion_guard(db,character_id,payload['targetId'],
                        item['surface'],item['language'],graph=merge_graph)
                except ValueError:
                    db.execute("UPDATE mv3_jobs SET status='failed',error_code='tag_merge_invalid',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
                    return {'status':'failed','reason':'tag_merge_invalid'}
                if reason:
                    if reason not in evaluation['reasonCodes']:
                        evaluation['reasonCodes'].append(reason)
                    continue
                proposal_items.append(item)
        for tag_id,points in additions.items():
            seen=set(); unique=[]
            for point in points:
                key=_canonical(point)
                if key not in seen:
                    seen.add(key); unique.append(point)
            additions[tag_id]=unique
        updated=[{'id':item['id'],'evidence':additions.get(item['id'],item['evidence'])}
                 for item in existing] if not protected else list(existing)
        if not protected and not whole_suppressed:
            for tag_id,points in sorted(additions.items()):
                if tag_id not in current_ids:
                    updated.append({'id':tag_id,'evidence':points})
            if len(updated)>32: raise ValueError('tag_limit_exceeded')
        result_set=detail['versionId']
        if updated!=existing and not protected and not whole_suppressed:
            points=[point for item in updated for point in item['evidence']]
            result=metadata.revise(db,payload['targetId'],character_id,detail['versionId'],
                {'tags':updated},editor_id,provenance='rule',rule=rules.RULE_VERSION,evidence=points,
                dependencies={'tagMergeGuard':tags.MERGE_GUARD_VERSION})
            result_set=result['versionId']
            detail=result
        proposal_count=0
        for item in proposal_items:
            point=item['evidence'][0]
            source=m3.live_source(db,character_id,point['kind'],point['id'])
            if source.text[point['start']:point['end']]!=item['evidenceText']:
                raise ValueError('tag_evidence_changed')
            term=lexicon.get(item['tagId']) if item['tagId'] else None
            deps={'fieldToken':detail['fields']['tags'].get('token'),
                  'mergeGuardVersion':tags.MERGE_GUARD_VERSION,
                  'semanticToken':term['semantic_token'] if term else None,
                  'aliasToken':term['alias_token'] if term else None,
                  'evidenceHash':tags._digest(item['evidenceText'])}
            proposal_count+=tags.add_proposal(db,character_id,payload['targetId'],item,
                detail['sourceToken'],deps,job_id,current['input_hash'])
        db.execute('INSERT INTO mv3_job_field_results(job_id,set_id) VALUES(?,?)',(job_id,result_set))
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,?,?,?,datetime('now'))''',(job_id,'tag_candidate' if updated!=existing else
            'tag_management_proposal' if proposal_count else
            'tag_merge_suppressed' if 'tag_merge_suppressed' in evaluation['reasonCodes'] else
            'tag_no_supported_topic',
            _hash([rules.RULE_VERSION,detail['sourceToken'],evaluation['reasonCodes']]),'running'))
        db.execute("UPDATE mv3_jobs SET status='completed',lease_token=NULL,lease_until=NULL WHERE id=?",(job_id,))
        return {'status':'completed','fieldSetId':result_set,'candidateCount':len(updated)-len(existing),
                'proposalCount':proposal_count,'reasonCodes':evaluation['reasonCodes'],'reused':False}


def _keyword_snapshot(db, entry_id, character_id, *, rule_version=None):
    """Versioned RAW and optional confirmed import boundary; no Tag dependency."""
    from . import memory_v2_metadata as metadata, memory_v2_keyword_rules as rules
    detail = metadata.read_draft(db, entry_id, character_id)
    boundary = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv2_import_segments'").fetchone():
        row = db.execute('''SELECT s.*,f.status AS file_status,f.character_id AS file_character,
            b.plan_version,b.plan_hash,b.source_snapshot_hash
            FROM mv2_import_segments s JOIN mv2_import_files f ON f.id=s.file_id
            LEFT JOIN mv2_import_boundary_confirmations b ON b.file_id=s.file_id
            WHERE s.entry_id=?''', (entry_id,)).fetchone()
        receipt_rows = db.execute('''SELECT r.file_id,r.ordinal FROM json_each(?) j
            LEFT JOIN mv2_import_receipts r
              ON r.message_id=CAST(json_extract(j.value,'$.source_id') AS INTEGER)
            ORDER BY CAST(j.key AS INTEGER)''', (_canonical(detail['sourceRefs']),)).fetchall()
        if any(item['file_id'] is not None for item in receipt_rows) and row is None:
            raise ValueError('boundary_not_confirmed')
        if row:
            if (row['file_status'] != 'raw_complete' or row['file_character'] != character_id
                    or row['plan_version'] is None):
                raise ValueError('boundary_not_confirmed')
            expected = list(range(row['start_ordinal'], row['end_ordinal']+1))
            if (any(ref['source_kind'] != 'message' for ref in detail['sourceRefs']) or
                    len(receipt_rows) != len(expected) or
                    [item['file_id'] for item in receipt_rows] != [row['file_id']]*len(expected) or
                    [item['ordinal'] for item in receipt_rows] != expected):
                raise ValueError('boundary_sources_changed')
            boundary = {'segmentId': row['id'], 'fileId': row['file_id'],
                        'planVersion': row['plan_version'], 'planHash': row['plan_hash'],
                        'sourceSnapshotHash': row['source_snapshot_hash'],
                        'start': row['start_ordinal'], 'end': row['end_ordinal']}
    field = detail['fields']['keywords']
    return {'entryId': entry_id, 'versionId': detail['versionId'],
            'sourceToken': detail['sourceToken'], 'sourceRefs': detail['sourceRefs'],
            'boundary': boundary, 'keywordFieldToken': field.get('token'),
            'keywordFieldState': field['lifecycleStatus'],
            'ruleVersion': rule_version or rules.RULE_VERSION}


def create_keyword_job(db, entry_id, character_id, editor_id, *, rule_version=None):
    from . import memory_v2_metadata as metadata, memory_v2_keyword_rules as rules
    _transaction(db); metadata._owner(db, editor_id)
    selected=rule_version or rules.RULE_VERSION
    if selected not in (rules.RULE_VERSION,rules.RETRIEVAL_RULE_VERSION,rules.SELECTION_RULE_VERSION): raise ValueError('keyword_rule_outdated')
    read_set = _keyword_snapshot(db, entry_id, character_id,rule_version=selected)
    return create(db, character_id, 'entry', entry_id, ['keywords'], read_set,
                  rule_version=selected, input_version='entry-sources-v1',
                  execution_kind='local_keywords_v1')


def _keyword_job(db, job_id, character_id):
    from . import memory_v2_keyword_rules as rules
    job = _owned(db, job_id, character_id)
    payload = json.loads(job['payload_json'])
    if (job['kind'] != 'A3_PROCESSING' or payload.get('targetKind') != 'entry' or
            payload.get('fields') != ['keywords'] or
            payload.get('inputVersion') != 'entry-sources-v1' or
            payload.get('executionKind') != 'local_keywords_v1' or
            payload.get('ruleVersion') not in (rules.RULE_VERSION,rules.RETRIEVAL_RULE_VERSION,rules.SELECTION_RULE_VERSION) or
            payload.get('readSet', {}).get('ruleVersion') != payload.get('ruleVersion')):
        raise ValueError('keyword_rule_outdated')
    return job, payload


def cancel_keyword_job(db, job_id, character_id, editor_id):
    from . import memory_v2_metadata as metadata
    _transaction(db); metadata._owner(db, editor_id)
    job, _ = _keyword_job(db, job_id, character_id)
    if job['status'] == 'running':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?', (job_id,))
        return 'running'
    return cancel(db, job_id, character_id)


def recover_keyword_job(db, job_id, character_id, editor_id, *, now=None):
    from . import memory_v2_metadata as metadata
    _transaction(db); metadata._owner(db, editor_id)
    job, _ = _keyword_job(db, job_id, character_id)
    _block_overlaps(db, character_id, job['scope_key'], ['keywords'], except_id=job_id)
    if (job['status'] != 'running' or job['lease_until'] is None or
            job['lease_until'] > (time.time() if now is None else now) or
            db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?', (job_id,)).fetchone()):
        raise ValueError('local_recovery_not_proven')
    state = 'cancelled' if job['cancel_intent'] else 'paused'
    db.execute('UPDATE mv3_jobs SET status=?,lease_token=NULL,lease_until=NULL WHERE id=?',
               (state, job_id))
    return state


def run_keyword_job(connect, job_id, character_id, editor_id):
    """Use the A3 local claim/lease/scope pattern and one atomic final write."""
    from . import memory_v2_metadata as metadata, memory_v2_derivation as m3
    from . import memory_v2_keyword_rules as rules
    with connect() as db:
        db.execute('BEGIN IMMEDIATE'); metadata._owner(db, editor_id)
        job, payload = _keyword_job(db, job_id, character_id)
        if job['status'] == 'completed':
            row = db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?', (job_id,)).fetchone()
            if not row: raise ValueError('completed_result_missing')
            detail = metadata.read_draft(db, payload['targetId'], character_id)
            if (detail['versionId'] != row['set_id'] or
                    detail['sourceToken'] != json.loads(job['read_set_json'])['sourceToken'] or
                    detail['fields']['keywords']['lifecycleStatus'] == 'stale'):
                raise ValueError('keyword_result_superseded')
            return {'status': 'completed', 'fieldSetId': row['set_id'], 'reused': True}
        _block_overlaps(db, character_id, job['scope_key'], ['keywords'], except_id=job_id)
        if job['status'] != 'queued' or job['cancel_intent']:
            raise ValueError('keyword_job_not_claimable')
        if db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?', (job_id,)).fetchone():
            raise ValueError('local_job_has_model_chunks')
        if _keyword_snapshot(db, payload['targetId'], character_id,rule_version=payload['ruleVersion']) != json.loads(job['read_set_json']):
            raise ValueError('keyword_input_changed')
        lease = str(uuid.uuid4())
        db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                   (lease, time.time()+60, job_id))
    with connect() as db:
        detail = metadata.read_draft(db, payload['targetId'], character_id)
        sources = []
        for ref in detail['sourceRefs']:
            source = m3.live_source(db, character_id, ref['source_kind'], ref['source_id'])
            sources.append({'kind': source.kind, 'id': source.source_id,
                            'role': source.role, 'text': source.text})
        from . import memory_v2_keyword_suggestions as selection
        evaluation = (selection.evaluate(sources) if payload['ruleVersion']==rules.SELECTION_RULE_VERSION else rules.evaluate_retrieval(sources) if payload['ruleVersion']==rules.RETRIEVAL_RULE_VERSION else rules.evaluate(sources))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE'); metadata._owner(db, editor_id)
        current, payload = _keyword_job(db, job_id, character_id)
        if (current['status'] != 'running' or current['lease_token'] != lease or
                current['lease_until'] <= time.time()):
            raise ValueError('keyword_lease_changed')
        _block_overlaps(db, character_id, current['scope_key'], ['keywords'], except_id=job_id)
        if current['cancel_intent']:
            db.execute("UPDATE mv3_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE id=?", (job_id,))
            return {'status': 'cancelled'}
        try:
            unchanged = _keyword_snapshot(db, payload['targetId'], character_id,rule_version=payload['ruleVersion']) == json.loads(current['read_set_json'])
        except ValueError:
            unchanged = False
        if not unchanged:
            db.execute("UPDATE mv3_jobs SET status='failed',error_code='keyword_input_changed',lease_token=NULL,lease_until=NULL WHERE id=?", (job_id,))
            return {'status': 'failed', 'reason': 'keyword_input_changed'}
        detail = metadata.read_draft(db, payload['targetId'], character_id)
        old = metadata._fields(db, detail['versionId']).get('keywords')
        protected = bool(old and (old['locked'] or old['provenance'] in ('manual', 'user_corrected') or
            db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?', (old['id'],)).fetchone()))
        values = evaluation['keywords']
        result_set = detail['versionId']
        reason = 'keyword_field_protected' if protected else (
            evaluation['reasonCodes'][0] if not values else 'keyword_candidate')
        if values and not protected:
            old_value = json.loads(old['value_json']) if old else None
            if not (old and old['provenance'] == 'rule' and old['rule_version'] == payload['ruleVersion'] and
                    old_value == values and detail['fields']['keywords']['lifecycleStatus'] != 'stale'):
                points = [point for item in values for point in item['evidence']]
                result = metadata.revise(db, payload['targetId'], character_id, detail['versionId'],
                    {'keywords': values}, editor_id, provenance='rule', rule=payload['ruleVersion'],
                    evidence=points)
                result_set = result['versionId']
            else:
                reason = 'keyword_candidate_reused'
        db.execute('INSERT INTO mv3_job_field_results(job_id,set_id) VALUES(?,?)', (job_id, result_set))
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,?,?,?,datetime('now'))''',
            (job_id, reason, _hash([payload['ruleVersion'], detail['sourceToken'], evaluation['reasonCodes']]), 'running'))
        db.execute("UPDATE mv3_jobs SET status='completed',lease_token=NULL,lease_until=NULL WHERE id=?", (job_id,))
        return {'status': 'completed', 'fieldSetId': result_set,
                'candidateCount': 0 if protected else len(values),
                'reasonCodes': evaluation['reasonCodes'], 'reused': reason == 'keyword_candidate_reused'}


def create_synopsis_job(db, entry_id, character_id, editor_id, *, input_cap=4096):
    from . import memory_v2_synopsis as synopsis
    return synopsis.create_job(db, entry_id, character_id, editor_id, input_cap=input_cap)


def run_synopsis_job(connect, job_id, character_id, editor_id, adapter):
    from . import memory_v2_synopsis as synopsis
    return synopsis.run_job(connect, job_id, character_id, editor_id, adapter)


def cancel_synopsis_job(db, job_id, character_id, editor_id):
    from . import memory_v2_synopsis as synopsis
    return synopsis.cancel_job(db, job_id, character_id, editor_id)


def recover_synopsis_job(db, job_id, character_id, editor_id, *, now=None):
    from . import memory_v2_synopsis as synopsis
    return synopsis.recover_job(db, job_id, character_id, editor_id, now=now)
