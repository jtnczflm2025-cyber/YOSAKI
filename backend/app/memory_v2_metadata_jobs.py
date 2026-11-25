"""Explicit metadata work reuses mv3_jobs leases. Model mode is unavailable here."""
import json
import uuid
import sqlite3
from . import memory_v2_jobs as jobs, memory_v2_metadata as metadata
from . import memory_v2_derivation as m3, memory_v2_metadata_contract as c


def _tag_pipeline_state(db):
    from . import memory_v2_tag_schema as tag_schema
    state=tag_schema.state(db)
    if state=='invalid': raise ValueError('a32_schema_invalid')
    return state


def _lexical_pipeline_state(db):
    from . import memory_v2_lexical_schema as lexical_schema
    current = lexical_schema.state(db)
    if current == 'invalid':
        raise ValueError('a33_schema_invalid')
    return current


def init_db(db):
    jobs.init_db(db)
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_job_field_results(
      job_id TEXT PRIMARY KEY REFERENCES mv3_jobs(id) ON DELETE CASCADE,
      set_id TEXT NOT NULL REFERENCES mv3_entry_field_sets(id))''')


def enqueue(db, entry_id, character_id, editor_id, kind, *, mock_model=False, token_budget=4096):
    from .memory_v2_entries import _begin
    _begin(db); metadata._owner(db, editor_id)
    _tag_pipeline_state(db)
    if kind not in ('METADATA', 'SYNOPSIS'): raise ValueError('unsupported_metadata_job')
    if kind == 'SYNOPSIS':
        from . import memory_v2_synopsis as synopsis
        synopsis.guard_field(db, entry_id, character_id)
    if kind == 'METADATA':
        if _lexical_pipeline_state(db) == 'valid':
            raise ValueError('keyword_pipeline_required')
        _keyword_scope_guard(db, character_id, entry_id)
    detail=metadata.read_draft(db,entry_id,character_id)
    payload={'target_entry':entry_id,'expected_version':detail['versionId'],
        'editor_id':editor_id,'rule_version':'metadata-extractive-v1',
        'refs':[{'kind':r['source_kind'],'id':r['source_id']} for r in detail['sourceRefs']]}
    if mock_model:
        if type(token_budget) is not int or not 1<=token_budget<=8192:
            raise ValueError('invalid_mock_budget')
        payload.update(model_mode=True,metadata_mock_only=True,token_budget=token_budget)
    fingerprint=jobs.snapshot(db,character_id,payload)
    prior=db.execute('SELECT * FROM mv3_jobs WHERE character_id=? AND kind=? AND input_hash=?',
        (character_id,kind,fingerprint)).fetchone()
    if prior: return dict(prior)
    jid=str(uuid.uuid4())
    db.execute('''INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at)
        VALUES(?,?,?,?,?,'queued',?)''',(jid,character_id,kind,fingerprint,json.dumps(payload),m3._now()))
    return dict(jobs.owned(db,jid,character_id))


def input_state(db, character_id, payload):
    detail=metadata.read_draft(db,payload['target_entry'],character_id)
    if detail['versionId']!=payload['expected_version']:
        raise ValueError('manual_revision_conflict')
    lexicon=[tuple(row) for row in db.execute('''SELECT v.vocabulary_id,v.semantic_token,v.alias_token
        FROM mv3_vocabulary r JOIN mv3_vocabulary_versions v ON v.vocabulary_id=r.id AND v.version=r.current_version
        WHERE r.character_id=? ORDER BY r.id''',(character_id,))]
    return [detail['versionId'],detail['sourceToken'],detail['timeEvidenceToken'],lexicon]


def _keyword_scope_guard(db, character_id, entry_id, *, except_id=None):
    if jobs._a3_installed(db):
        from . import memory_v2_processing as processing
        scope = processing._scope(character_id, 'entry', entry_id, ['keywords'])
        processing._block_overlaps(db, character_id, scope, ['keywords'], except_id=except_id)


def _automatic_keyword_guard(db, job, payload, changes):
    """Legacy writes stay available but cannot displace protected A3 Keywords."""
    if 'keywords' not in changes:
        return
    if _lexical_pipeline_state(db) == 'valid':
        raise ValueError('keyword_pipeline_required')
    _keyword_scope_guard(db, job['character_id'], payload['target_entry'], except_id=job['id'])
    detail = metadata.read_draft(db, payload['target_entry'], job['character_id'])
    old = metadata._fields(db, detail['versionId']).get('keywords')
    if old and (old['locked'] or old['provenance'] in ('manual', 'user_corrected') or
                db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?', (old['id'],)).fetchone()):
        raise ValueError('keyword_field_protected')
    if old and old['provenance'] == 'rule' and old['rule_version'].startswith('keyword-literal-raw-'):
        raise ValueError('keyword_pipeline_candidate_protected')


def execute(db, job, payload):
    tag_state=_tag_pipeline_state(db)
    if _lexical_pipeline_state(db) == 'valid' and job['kind'] == 'METADATA':
        raise ValueError('keyword_pipeline_required')
    if job['kind'] == 'SYNOPSIS':
        from . import memory_v2_synopsis as synopsis
        synopsis.guard_field(db, payload['target_entry'], job['character_id'], except_id=job['id'])
    detail=metadata.read_draft(db,payload['target_entry'],job['character_id'])
    sources=[m3.live_source(db,job['character_id'],r['kind'],r['id']) for r in payload['refs']]
    if job['kind']=='SYNOPSIS':
        # Complete, short source messages only: no free rewrite or silent truncation.
        body='\n'.join(('她说：' if s.role=='user' else '我说：')+s.text for s in sources)
        if len(body)>3000: raise ValueError('extractive_synopsis_budget_exceeded')
        changes={'synopsis':body}
    else:
        changes={'keywords':[]}
        from .memory_v2_sources import terms
        seen=set()
        for source in sources:
            for term in sorted(terms(source.text)):
                start=source.text.find(term)
                if start<0 or term in seen: continue
                seen.add(term)
                changes['keywords'].append({'text':term,'language':'und','kind':'literal','weight':1,
                    'field':'raw','evidence':[{'kind':source.kind,'id':source.source_id,'start':start,'end':start+len(term)}]})
                if len(changes['keywords'])==32: break
            if len(changes['keywords'])==32: break
        # Existing controlled entities only. Uncertain new themes remain suggestions.
        from . import memory_v2_vocabulary as vocabulary
        if tag_state=='absent':
            changes['tags']=[]
            for row in db.execute("SELECT id FROM mv3_vocabulary WHERE character_id=? AND kind='tag' ORDER BY id",(job['character_id'],)):
                term=vocabulary.get(db,job['character_id'],row['id'])
                if term['status']!='active': continue
                evidence=[]
                for source in sources:
                    for spelling in [term['name'],*json.loads(term['aliases_json'])]:
                        start=source.text.find(spelling)
                        if start>=0:
                            evidence.append({'kind':source.kind,'id':source.source_id,'start':start,'end':start+len(spelling)})
                            break
                if evidence: changes['tags'].append({'id':row['id'],'evidence':evidence})
                if len(changes['tags'])==32: break
    _automatic_keyword_guard(db, job, payload, changes)
    result=metadata.revise(db,payload['target_entry'],job['character_id'],payload['expected_version'],
        changes,payload['editor_id'],provenance='rule',rule=payload['rule_version'])
    # These candidates remain drafts. Explicit verification/publication is a separate operation.
    db.execute('INSERT INTO mv3_job_field_results VALUES(?,?)',(job['id'],result['versionId']))
    if job['kind']=='METADATA' and tag_state=='valid':
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,'legacy_tags_deferred',?,'running',datetime('now'))''',
            (job['id'],job['input_hash']))
    return result


class MockMetadataAdapter:
    """Synthetic fixture only; has no URL, key, HTTP client or provider fallback."""
    def __init__(self, changes, *, input_tokens=20, output_tokens=20, timeout=False):
        self.changes=changes; self.input_tokens=input_tokens; self.output_tokens=output_tokens; self.timeout=timeout

    def generate(self):
        if self.timeout: raise TimeoutError('synthetic_unknown')
        return self.changes,self.input_tokens,self.output_tokens


def dispatch_mock(connect,job_id,character_id,adapter):
    if type(adapter) is not MockMetadataAdapter: raise ValueError('mock_adapter_required')
    with connect() as db:
        _tag_pipeline_state(db)
        job=jobs.owned(db,job_id,character_id); payload=json.loads(job['payload_json'])
        if _lexical_pipeline_state(db) == 'valid' and job['kind'] == 'METADATA':
            raise ValueError('keyword_pipeline_required')
        if not payload.get('metadata_mock_only'): raise ValueError('mock_job_required')
        if job['kind'] == 'METADATA':
            _keyword_scope_guard(db, character_id, payload['target_entry'], except_id=job_id)
        else:
            from . import memory_v2_synopsis as synopsis
            synopsis.guard_field(db, payload['target_entry'], character_id, except_id=job_id)
        lease=jobs.claim(db,job_id,character_id)
        prior=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(job_id,)).fetchone()
        if prior:
            if prior['state']!='prepared' or prior['input_hash']!=job['input_hash']:
                raise ValueError('request_not_resendable')
        else:
            request_id=str(uuid.uuid4())
            db.execute('''INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens)
                VALUES(?,0,?,?,?,'prepared',?)''',(job_id,request_id,job['input_hash'],job['payload_json'],payload['token_budget']))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=jobs.owned(db,job_id,character_id)
        if current['status']!='running' or current['lease_token']!=lease:
            raise ValueError('invalid_job_lease')
        if jobs.snapshot(db,character_id,payload)!=current['input_hash']:
            raise ValueError('job_input_changed')
        if job['kind'] == 'METADATA':
            _keyword_scope_guard(db, character_id, payload['target_entry'], except_id=job_id)
        else:
            from . import memory_v2_synopsis as synopsis
            synopsis.guard_field(db, payload['target_entry'], character_id, except_id=job_id)
        attempt_id=jobs.begin_dispatch(db,job_id,character_id,0,
            expected_request_id=prior['request_id'] if prior else request_id,
            expected_input_hash=job['input_hash'])
    try:
        changes,used_input,used_output=adapter.generate()
        if type(used_input) is not int or type(used_output) is not int or min(used_input,used_output)<0:
            raise ValueError('usage_unknown')
    except (TimeoutError,ValueError):
        with connect() as db:
            jobs.settle_dispatch(db,job_id,character_id,0,
                prior['request_id'] if prior else request_id,attempt_id,state='unknown')
            db.execute("UPDATE mv3_jobs SET error_code='model_result_unknown' WHERE id=?",(job_id,))
        return {'status':'unknown'}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=jobs.owned(db,job_id,character_id)
        try:
            import time
            if current['status']!=('unknown' if jobs._a3_installed(db) else 'running') or current['lease_token']!=lease:
                raise ValueError('invalid_job_lease')
            if used_input+used_output>payload['token_budget']: raise ValueError('budget_exceeded')
            if jobs.snapshot(db,character_id,payload)!=current['input_hash']: raise ValueError('job_input_changed')
            if not isinstance(changes,dict) or set(changes)-({'synopsis'} if job['kind']=='SYNOPSIS' else {'title','categories','tags','keywords'}):
                raise ValueError('model_fields_invalid')
            if _tag_pipeline_state(db)=='valid' and 'tags' in changes:
                raise ValueError('tag_pipeline_required')
            _automatic_keyword_guard(db, current, payload, changes)
            if job['kind'] == 'SYNOPSIS':
                from . import memory_v2_synopsis as synopsis
                synopsis.guard_field(db, payload['target_entry'], character_id, except_id=job_id)
        except ValueError as exc:
            jobs.settle_dispatch(db,job_id,character_id,0,prior['request_id'] if prior else request_id,
                attempt_id,state='rejected',used_input=used_input,used_output=used_output,
                result_hash=m3._hash(['rejected',str(exc)]))
            state=jobs.recompute_parent(db,job_id,character_id,error=str(exc))
            return {'status':'cancelled' if state=='cancelled' else 'rejected','reason':str(exc)}
        state=jobs.settle_dispatch(db,job_id,character_id,0,prior['request_id'] if prior else request_id,
            attempt_id,state='completed',used_input=used_input,used_output=used_output,
            result_hash=m3._hash(['completed',changes]))
        if not jobs.business_result_allowed(db,job_id,character_id):
            return {'status':state}
        db.execute('SAVEPOINT metadata_model')
        try:
            result=metadata.revise(db,payload['target_entry'],character_id,payload['expected_version'],
                changes,payload['editor_id'],provenance='model',rule='metadata-mock-v1')
            db.execute('INSERT INTO mv3_job_field_results VALUES(?,?)',(job_id,result['versionId']))
            db.execute('RELEASE metadata_model')
        except (ValueError, sqlite3.DatabaseError) as exc:
            db.execute('ROLLBACK TO metadata_model'); db.execute('RELEASE metadata_model')
            state=jobs.recompute_parent(db,job_id,character_id,error=str(exc))
            return {'status':'rejected','reason':str(exc)}
        db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=?',(json.dumps(changes,ensure_ascii=False),job_id))
        state=jobs.recompute_parent(db,job_id,character_id,output_ready=True)
        return {'status':state,'fieldSetId':result['versionId'],'needsReview':True}
