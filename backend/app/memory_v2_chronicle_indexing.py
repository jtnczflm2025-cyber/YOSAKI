"""Scoped incremental document embeddings and corpus-independent query cache.

Uses existing A3 jobs, durable dispatch proofs and trial ledger. Planning is
read-only. A committed possibly-dispatched request is never sent a second time.
"""
import json
import time
import uuid
from decimal import Decimal, ROUND_CEILING
from . import memory_v2_chronicle_embedding as embedding
from . import memory_v2_chronicle_vectors as vectors, memory_v2_chronicle_channel as channel
from . import memory_v2_processing as processing, memory_v2_jobs as jobs
from . import memory_v2_m36_trial as trials

VERSION = 'chronicle-document-index-v2'
QUERY_VERSION = 'chronicle-query-vector-v2'
CAP = 8192
MAX_ENTRIES = 50
MODEL = embedding.MODEL


def cost(tokens):
    return int((Decimal(tokens)*Decimal('0.02')).to_integral_value(rounding=ROUND_CEILING))


def _price(price):
    if (price.get('provider'), price.get('model'), price.get('mode'),
            Decimal(str(price.get('inputUsdPerMillion'))), price.get('outputUsdPerMillion')) != (
            'openai_embeddings', MODEL, 'standard', Decimal('0.02'), 0):
        raise ValueError('embedding_price_changed')


def _valid_vector(db, cid, member):
    row = db.execute('SELECT * FROM mv3_chronicle_vectors WHERE character_id=? AND entry_id=? AND model_version=?',
                     (cid, member['entryId'], MODEL)).fetchone()
    if row is None or row['input_hash'] != member['inputHash'] or row['dimensions'] != 1536:
        return False
    try: return len(vectors.unit(json.loads(row['vector_json']))) == 1536
    except (ValueError, TypeError): return False


def plan(db, cid, owner_id, entry_ids):
    channel.authorized(db, cid, owner_id)
    if (not isinstance(entry_ids, list) or not 1 <= len(entry_ids) <= MAX_ENTRIES or
            any(not isinstance(x, str) or not x for x in entry_ids) or len(set(entry_ids)) != len(entry_ids)):
        raise ValueError('embedding_scope_invalid')
    members, reused, batches, part, blocked = [], [], [], [], []
    prior=db.execute("""SELECT j.read_set_json,c.state,c.dispatch_attempt_id,c.output_json
        FROM mv3_jobs j JOIN mv3_model_chunks c ON c.job_id=j.id
        WHERE j.character_id=? AND json_extract(j.read_set_json,'$.version')=?""",(cid,VERSION)).fetchall()
    for eid in sorted(entry_ids):
        text, fingerprint = vectors._input(db, cid, eid)
        member = dict(entryId=eid, inputHash=fingerprint, text=text)
        members.append(member)
        if _valid_vector(db, cid, member):
            reused.append(eid)
            continue
        if any(r['dispatch_attempt_id'] is not None and any(m['entryId']==eid and m['inputHash']==fingerprint
                for m in json.loads(r['read_set_json'])['members']) for r in prior):
            blocked.append(dict(entryId=eid,reason='previous_dispatch_without_current_vector'))
            continue
        if len(processing._canonical([text]).encode('utf-8')) > CAP:
            raise ValueError('embedding_single_document_capacity')
        proposed = part + [member]
        if len(processing._canonical([m['text'] for m in proposed]).encode('utf-8')) > CAP:
            batches.append(part);part = []
        part.append(member)
    if part: batches.append(part)
    bound = dict(version=VERSION, model=MODEL, dimensions=1536, characterId=cid,
                 ownerId=owner_id, entryIds=sorted(entry_ids), members=members, reused=reused, blocked=blocked,
                 batches=[dict(ordinal=i, members=b, inputBytes=len(processing._canonical(
                     [m['text'] for m in b]).encode('utf-8'))) for i,b in enumerate(batches)])
    bound['maxMicrousd'] = sum(cost(b['inputBytes']) for b in bound['batches'])
    return dict(bound, planHash=processing._hash(bound), providerRequests=len(batches), readOnly=True)


def _load(db, jid, cid, owner_id):
    channel.authorized(db, cid, owner_id)
    job = jobs.owned(db, jid, cid)
    payload = json.loads(job['payload_json']);snap = payload['readSet']
    if (payload.get('executionKind') != 'chronicle_embedding_v1' or
            payload.get('ruleVersion') not in (VERSION, QUERY_VERSION) or
            snap.get('version') != payload['ruleVersion'] or
            processing._canonical(snap) != job['read_set_json'] or
            processing._hash(['a3-job-v1',cid,payload]) != job['input_hash'] or
            snap['realConfig']['ownerId'] != owner_id or
            payload['inputVersion'] != snap['version']):
        raise ValueError('embedding_task_binding_invalid')
    trial=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=? AND character_id=?',
        (snap['realConfig']['trialId'],cid)).fetchone()
    if trial is None or processing._hash(json.loads(trial['price_json']))!=snap['priceHash']:
        raise ValueError('embedding_price_changed')
    _price(json.loads(trial['price_json']))
    if snap['model']!=MODEL or snap.get('dimensions',1536)!=1536 or trial['model']!=MODEL or trial['max_requests']!=1:
        raise ValueError('embedding_task_binding_invalid')
    return job, snap


def create(db, cid, owner_id, entry_ids, expected_plan, approval_id, price, max_microusd, *, model_id=None):
    processing._transaction(db);channel.authorized(db, cid, owner_id);_price(price)
    try: uuid.UUID(approval_id)
    except (ValueError, TypeError, AttributeError): raise ValueError('embedding_approval_invalid') from None
    # An approval binds one immutable action and scope. Re-obtaining a plan is
    # not permission to re-dispatch an existing request.
    prior = db.execute("""SELECT id,read_set_json FROM mv3_jobs WHERE character_id=?
        AND json_extract(read_set_json,'$.approvalId')=?
        AND json_extract(read_set_json,'$.version')=? ORDER BY json_extract(read_set_json,'$.ordinal')""",
        (cid,approval_id,VERSION)).fetchall()
    if prior:
        for row in prior:
            snap=json.loads(row['read_set_json'])
            if (snap['planHash'] != expected_plan or snap['entryIds'] != sorted(entry_ids) or
                    snap['realConfig']['ownerId'] != owner_id or
                    snap['groupMaxMicrousd'] != max_microusd or snap['priceHash'] != processing._hash(price) or
                    snap['realConfig'].get('modelId') != model_id):
                raise ValueError('embedding_approval_conflict')
        return {'jobs':[r['id'] for r in prior], 'reused':True, 'approvalId':approval_id}
    bound = plan(db,cid,owner_id,entry_ids)
    if bound['planHash'] != expected_plan: raise ValueError('embedding_plan_expired')
    from .owner_billing import metered
    if not metered() and (type(max_microusd) is not int or not bound['maxMicrousd'] <= max_microusd <= 5_000_000):
        raise ValueError('embedding_budget_exhausted')
    active = db.execute("""SELECT read_set_json FROM mv3_jobs WHERE character_id=?
        AND json_extract(read_set_json,'$.version')=? AND status IN ('queued','running','paused','unknown')""",
        (cid,VERSION)).fetchall()
    wanted={m['entryId'] for b in bound['batches'] for m in b['members']}
    if any(wanted.intersection(m['entryId'] for m in json.loads(r[0])['members']) for r in active):
        raise ValueError('embedding_scope_already_planned')
    result=[]
    for batch in bound['batches']:
        reserve=cost(batch['inputBytes'])
        tid=trials.create_trial(db,cid,MODEL,price,max_microusd=reserve,max_requests=1)
        snap=dict(version=VERSION,model=MODEL,dimensions=1536,members=batch['members'],
            inputBytes=batch['inputBytes'],entryIds=bound['entryIds'],planHash=expected_plan,
            ordinal=batch['ordinal'],approvalId=approval_id,groupMaxMicrousd=max_microusd,
            priceHash=processing._hash(price),realConfig=dict(ownerId=owner_id,trialId=tid,modelId=model_id))
        jid=processing.create(db,cid,'entry','chronicle:document-index',['synopsis','title'],snap,
                rule_version=VERSION,input_version=VERSION,execution_kind='chronicle_embedding_v1')
        result.append(jid)
    return dict(jobs=result,reused=False,approvalId=approval_id,reusedEntries=bound['reused'],blocked=bound['blocked'])


def _current(db, cid, owner, snap):
    channel.authorized(db,cid,owner)
    if snap['version'] == VERSION:
        return all(vectors._input(db,cid,m['entryId']) == (m['text'],m['inputHash']) for m in snap['members'])
    return (snap['version']==QUERY_VERSION and snap['queryHash']==processing._hash(snap['query'])
            and len(snap['query'].encode('utf-8')) <= 1200)


def _sync(db, jid, tid):
    job=db.execute('SELECT * FROM mv3_jobs WHERE id=?',(jid,)).fetchone()
    rows=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchall()
    if rows and all(r['state'] in ('completed','rejected') for r in rows):
        jobs._recompute_parent(db,job,output_ready=all(r['state']=='completed' and r['output_json'] for r in rows))
        db.execute("UPDATE mv3_trial_budgets SET status=? WHERE id=? AND reserved_microusd=0",
            ('completed' if all(r['state']=='completed' and r['output_json'] for r in rows) else 'stopped',tid))


def execute(connect, jid, cid, owner, adapter):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');job,snap=_load(db,jid,cid,owner)
        tid=snap['realConfig']['trialId'];trial=trials.trial_status(db,tid,cid)
        row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()
        if row:
            if row['state']=='completed' and row['output_json']:
                try: current=_current(db,cid,owner,snap)
                except ValueError: current=False
                return dict(status='completed' if current else 'stale',reused=True,jobId=jid)
            # Never recreate a proof after a restart, lease change or unknown.
            if row['state']!='prepared' or row['dispatch_attempt_id'] is not None:
                return dict(status='unknown' if row['state'] in ('unknown','possibly_dispatched') else 'failed',jobId=jid)
        if job['cancel_intent'] or job['status'] in ('failed','cancelled','completed','unknown'):
            return dict(status=job['status'],jobId=jid)
        if job['lease_until'] and job['lease_until']>time.time(): raise ValueError('embedding_worker_active')
        try: current=_current(db,cid,owner,snap)
        except ValueError: current=False
        if not current:
            jobs._recompute_parent(db,job,error='embedding_input_changed')
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code='embedding_input_changed' WHERE id=?",(tid,))
            return dict(status='failed',reason='embedding_input_changed',jobId=jid)
        texts=[m['text'] for m in snap['members']] if snap['version']==VERSION else [snap['query']]
        if len(processing._canonical(texts).encode('utf-8')) != snap['inputBytes'] or snap['inputBytes']>CAP:
            raise ValueError('embedding_input_capacity')
        if not row:
            processing.prepare_chunks(db,jid,cid,[{'reservedTokens':snap['inputBytes'],'textsHash':processing._hash(texts)}])
            reserve=cost(snap['inputBytes'])
            if reserve>trial['max_microusd'] or trial['dispatched_requests'] or trial['reserved_microusd'] or trial['spent_estimate_microusd']:
                raise ValueError('embedding_budget_exhausted')
            db.execute("UPDATE mv3_model_chunks SET trial_id=?,reserved_usd_micros=?,input_json=?,provider_status='prepared' WHERE job_id=?",
                (tid,reserve,processing._canonical(snap),jid))
            db.execute("UPDATE mv3_trial_budgets SET reserved_microusd=?,status='prepared' WHERE id=?",(reserve,tid))
        lease=str(uuid.uuid4())
        db.execute("UPDATE mv3_jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=?",(lease,time.time()+120,jid))
        identity=processing.dispatch(db,jid,cid,0,original_owner_id=owner)
        db.execute("UPDATE mv3_trial_budgets SET dispatched_requests=1,status='running' WHERE id=? AND dispatched_requests=0",(tid,))
    result=None
    try: result=adapter.embed(texts)
    except Exception: pass
    usage=result.get('usage',{}) if isinstance(result,dict) else {}
    used=usage.get('prompt_tokens') if isinstance(usage,dict) else None
    reliable=type(used) is int and used>=0 and usage.get('total_tokens')==used
    charge=cost(used) if reliable else None
    values=[];valid=False
    if reliable:
        try:
            if result['model']!=MODEL or [x['index'] for x in result['data']]!=list(range(len(texts))):
                raise ValueError('embedding_response_invalid')
            values=[vectors.unit(x['embedding']) for x in result['data']]
            valid=all(len(x)==1536 for x in values) and used<=snap['inputBytes']
        except (ValueError,KeyError,TypeError): pass
    def settle(db,state):
        revoked=not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',(cid,)).fetchone()
        jobs.settle_dispatch(db,jid,cid,0,identity['requestId'],identity['dispatchProof'],
            state='unknown' if not reliable else 'rejected' if revoked else state,
            used_input=used,used_output=0 if reliable else None,trial_id=tid,usd_estimate=charge,
            result_hash=processing._hash(result) if reliable else None,original_owner_id=owner if revoked else None)
    allowed=False
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                job,_=_load(db,jid,cid,owner)
                allowed=(not job['cancel_intent'] and job['lease_token']==lease and
                    job['lease_until']>time.time() and _current(db,cid,owner,snap))
            except ValueError: allowed=False
            settle(db,'completed' if valid and allowed else 'rejected')
            if reliable and valid and allowed:
                if snap['version']==VERSION:
                    for m,v in zip(snap['members'],values,strict=True):
                        vectors.store(db,cid,owner,m['entryId'],MODEL,m['inputHash'],v)
                    output={'members':[dict(entryId=m['entryId'],inputHash=m['inputHash']) for m in snap['members']]}
                else: output={'query':values[0]}
                db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=?',(json.dumps(output),jid))
            db.execute('UPDATE mv3_model_chunks SET provider_usage_json=?,usage_estimated_usd_micros=? WHERE job_id=?',
                       (json.dumps(usage),charge,jid))
            _sync(db,jid,tid)
    except Exception:
        # Failed business/storage transaction has rolled back. Known usage has
        # its own settlement transaction; absent storage cannot be guaranteed.
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()
            if old and old[0] not in ('completed','rejected'):
                settle(db,'rejected');_sync(db,jid,tid)
        raise
    with connect() as db:
        state=db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0]
    return dict(status=state,jobId=jid,usedInput=used,estimatedMicrousd=charge)


def cancel(db,jid,cid,owner):
    processing._transaction(db);job,snap=_load(db,jid,cid,owner)
    if job['status'] in ('queued','paused','unknown'):
        state=processing.cancel(db,jid,cid)
    elif job['status']=='running':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(jid,))
        state=jobs._recompute_parent(db,jobs.owned(db,jid,cid))
    else:state=job['status']
    # No future-batch reservations were created; dispatched unknowns retain
    # their exact request reservation. This does not settle or fabricate usage.
    if state=='cancelled':
        db.execute("UPDATE mv3_trial_budgets SET status='stopped' WHERE id=? AND reserved_microusd=0",
            (snap['realConfig']['trialId'],))
    return dict(status=state,jobId=jid)


def cached_query(db,cid,owner,query):
    channel.authorized(db,cid,owner)
    row=db.execute("""SELECT j.id,c.output_json FROM mv3_jobs j JOIN mv3_model_chunks c ON c.job_id=j.id
        WHERE j.character_id=? AND j.status='completed' AND c.state='completed'
        AND json_extract(j.read_set_json,'$.version')=?
        AND json_extract(j.read_set_json,'$.queryHash')=?
        AND json_extract(j.read_set_json,'$.realConfig.ownerId')=?
        ORDER BY j.rowid DESC LIMIT 1""",(cid,QUERY_VERSION,processing._hash(query),owner)).fetchone()
    if row:
        try:
            _load(db,row['id'],cid,owner)
            v=vectors.unit(json.loads(row['output_json'])['query'])
            if len(v)==1536:return v
        except (ValueError,TypeError,KeyError): pass
    return None


def create_query(db,cid,owner,query,price,max_microusd):
    processing._transaction(db);channel.authorized(db,cid,owner);_price(price)
    if not isinstance(query,str) or not query.strip() or len(query.encode('utf-8'))>1200:
        raise ValueError('embedding_query_invalid')
    size=len(processing._canonical([query]).encode('utf-8'))
    from .owner_billing import metered
    if not metered() and not cost(size)<=max_microusd<=164: raise ValueError('embedding_budget_exhausted')
    prior=db.execute("""SELECT id FROM mv3_jobs WHERE character_id=?
        AND json_extract(read_set_json,'$.version')=? AND json_extract(read_set_json,'$.queryHash')=?
        AND json_extract(read_set_json,'$.realConfig.ownerId')=? ORDER BY rowid DESC LIMIT 1""",
        (cid,QUERY_VERSION,processing._hash(query),owner)).fetchone()
    if prior:return prior[0]
    tid=trials.create_trial(db,cid,MODEL,price,max_microusd=cost(size),max_requests=1)
    snap=dict(version=QUERY_VERSION,model=MODEL,query=query,queryHash=processing._hash(query),
              inputBytes=size,priceHash=processing._hash(price),realConfig=dict(ownerId=owner,trialId=tid))
    return processing.create(db,cid,'entry','chronicle:query-vector',['title'],snap,
        rule_version=QUERY_VERSION,input_version=QUERY_VERSION,execution_kind='chronicle_embedding_v1')
