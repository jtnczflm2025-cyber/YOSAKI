"""Explicit bounded embedding action, using A3 identities and the existing ledger.

No credential lookup, GET side effects, retries or automatic provider fallback.
Caller supplies a trusted adapter and an explicitly approved existing budget.
"""
import json
import math
import time
import uuid
from decimal import Decimal, ROUND_CEILING
import httpx
from . import memory_v2_chronicle_vectors as vectors, memory_v2_chronicle_channel as channel
from . import memory_v2_derivation as core, memory_v2_processing as processing, memory_v2_jobs as jobs

MODEL='text-embedding-3-small'
VERSION='chronicle-embedding-v1'


class Adapter:
    def __init__(self, key, *, transport=None, allow_network=False):
        if allow_network and transport is not None:
            raise ValueError('embedding_network_transport_conflict')
        if transport is None and not (allow_network and key):
            raise ValueError("embedding_network_not_authorized")
        self.client=httpx.Client(base_url='https://api.openai.com/v1',
            headers={'Authorization':'Bearer '+key}, transport=transport,
            follow_redirects=False, trust_env=False, timeout=httpx.Timeout(60,connect=10))

    def embed(self, texts):
        response=self.client.post('/embeddings',json={'model':MODEL,'input':texts,'encoding_format':'float','dimensions':1536})
        response.raise_for_status()
        return response.json()

    def close(self): self.client.close()


def snapshot(db,cid,owner_id,query):
    channel.authorized(db,cid,owner_id)
    if not isinstance(query,str) or not 1<=len(query.strip())<=1000: raise ValueError('embedding_query_invalid')
    rows=db.execute("SELECT id FROM mv3_entries WHERE character_id=? AND status='active' ORDER BY id LIMIT 51",(cid,)).fetchall()
    if len(rows)>50: raise ValueError('embedding_capacity_exceeded')
    texts=[query]; members=[]
    for row in rows:
        try: text,fingerprint=vectors._input(db,cid,row['id'])
        except ValueError: continue
        members.append({'entryId':row['id'],'inputHash':fingerprint});texts.append(text)
    if not members: raise ValueError('embedding_no_eligible_entries')
    bound=sum(len(t.encode('utf-8')) for t in texts)
    if bound>8192: raise ValueError('embedding_input_capacity')
    return {'queryHash':core._hash(query),'members':members,'textsHash':core._hash(texts),
            'model':MODEL,'version':VERSION,'byteTokenUpperBound':bound},texts


def sync_budget_status(db, trial_id):
    """Only reconcile a single, fully settled Chronicle embedding request."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    return db.execute("""UPDATE mv3_trial_budgets AS b
        SET status=CASE (SELECT state FROM mv3_model_chunks WHERE trial_id=b.id)
            WHEN 'completed' THEN 'completed' ELSE 'stopped' END
        WHERE b.id=? AND b.provider='openai_embeddings' AND b.model=? AND b.status='running'
          AND b.dispatched_requests=1 AND b.reserved_microusd=0
          AND (SELECT COUNT(*) FROM mv3_model_chunks WHERE trial_id=b.id)=1
          AND EXISTS (SELECT 1 FROM mv3_model_chunks c JOIN mv3_jobs j ON j.id=c.job_id
             WHERE c.trial_id=b.id AND j.character_id=b.character_id
               AND json_extract(j.payload_json,'$.executionKind')='chronicle_embedding_v1'
               AND c.used_input_tokens>=0 AND c.used_output_tokens=0
               AND b.spent_estimate_microusd=(c.used_input_tokens+49)/50
               AND c.settlement_hash IS NOT NULL
               AND ((c.state='completed' AND j.status='completed' AND c.output_json IS NOT NULL)
                    OR (c.state='rejected' AND j.status IN ('failed','cancelled'))))""",
        (trial_id, MODEL)).rowcount


def run(connect,cid,owner_id,query,trial_id,adapter):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        bound,texts=snapshot(db,cid,owner_id,query)
        trial=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=? AND character_id=?',(trial_id,cid)).fetchone()
        if trial is None or trial['provider']!='openai_embeddings' or trial['model']!=MODEL:
            raise ValueError('embedding_approval_required')
        price=json.loads(trial['price_json'])
        if Decimal(str(price['inputUsdPerMillion']))!=Decimal('0.02') or price['outputUsdPerMillion']!=0 or price['mode']!='standard':
            raise ValueError('embedding_price_changed')
        reserve=int((Decimal(bound['byteTokenUpperBound'])*Decimal('0.02')).to_integral_value(rounding=ROUND_CEILING))
        read_set={**bound,'realConfig':{'ownerId':owner_id,'trialId':trial_id}}
        jid=processing.create(db,cid,'entry',bound['members'][0]['entryId'],['synopsis','title','narrative'],read_set,
            rule_version=VERSION,input_version=VERSION,execution_kind='chronicle_embedding_v1')
        old=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(jid,)).fetchone()
        if old:
            if old['state']=='completed' and old['output_json']:
                output=json.loads(old['output_json'])
                return vectors.hybrid(db,cid,owner_id,query,query_vector=output['query'],model_version=MODEL)
            raise ValueError('embedding_request_not_repeatable')
        if trial['status'] not in ('created','prepared','running') or trial['dispatched_requests']>=trial['max_requests'] or trial['spent_estimate_microusd']+trial['reserved_microusd']+reserve>trial['max_microusd']:
            raise ValueError('embedding_budget_exhausted')
        processing.prepare_chunks(db,jid,cid,[{'reservedTokens':bound['byteTokenUpperBound']}])
        db.execute('UPDATE mv3_model_chunks SET trial_id=?,reserved_usd_micros=?,input_json=? WHERE job_id=?',
                   (trial_id,reserve,json.dumps(bound,sort_keys=True),jid))
        db.execute("UPDATE mv3_trial_budgets SET reserved_microusd=reserved_microusd+?,dispatched_requests=dispatched_requests+1,status='running' WHERE id=?",(reserve,trial_id))
        lease=str(uuid.uuid4())
        if db.execute("UPDATE mv3_jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=? AND status='queued'",(lease,time.time()+120,jid)).rowcount!=1:
            raise ValueError('embedding_claim_conflict')
        identity=processing.dispatch(db,jid,cid,0,original_owner_id=owner_id)
    # Identity + reservation transaction has committed before any outbound call.
    result=None
    try: result=adapter.embed(texts)
    except Exception: pass
    usage=result.get('usage',{}) if isinstance(result,dict) else {}
    used=usage.get('prompt_tokens') if isinstance(usage,dict) else None
    reliable=type(used) is int and used>=0 and usage.get('total_tokens')==used
    cost=int((Decimal(used)*Decimal('0.02')).to_integral_value(rounding=ROUND_CEILING)) if reliable else None
    valid=False; normalized=[]
    if reliable:
        try:
            data=result['data']
            if result.get('model')!=MODEL or len(data)!=len(texts): raise ValueError('embedding_response_mismatch')
            if [d['index'] for d in data]!=list(range(len(texts))): raise ValueError('embedding_order_invalid')
            normalized=[vectors.unit(d['embedding']) for d in data]
            if any(len(v)!=1536 for v in normalized) or used>bound['byteTokenUpperBound']: raise ValueError('embedding_shape_invalid')
            valid=True
        except (ValueError,KeyError,TypeError): pass
    def settle(db,state):
        revoked=not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',(cid,)).fetchone()
        return jobs.settle_dispatch(db,jid,cid,0,identity['requestId'],identity['dispatchProof'],
            state='unknown' if not reliable else ('rejected' if revoked else state),
            used_input=used,used_output=0 if reliable else None,
            trial_id=trial_id,usd_estimate=cost,original_owner_id=owner_id if revoked else None,
            result_hash=core._hash(result) if reliable else None)
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                current,_=snapshot(db,cid,owner_id,query)
                job=jobs.owned(db,jid,cid)
                allowed=(current==bound and not job['cancel_intent'] and job['lease_token']==lease and job['lease_until'] is not None and job['lease_until']>time.time())
            except ValueError: allowed=False
            if reliable and valid and allowed:
                settle(db,'completed')
                for member,value in zip(bound['members'],normalized[1:]):
                    vectors.store(db,cid,owner_id,member['entryId'],MODEL,member['inputHash'],value)
                db.execute('UPDATE mv3_model_chunks SET output_json=?,provider_usage_json=?,usage_estimated_usd_micros=? WHERE job_id=?',
                    (json.dumps({'query':normalized[0]}),json.dumps(usage),cost,jid))
                jobs._recompute_parent(db,jobs.owned(db,jid,cid),output_ready=True)
            else:
                settle(db,'rejected')
            sync_budget_status(db,trial_id)
    except Exception:
        # A rolled-back output cannot erase observed reliable cost. This is
        # settlement only: never re-send or resurrect the business result.
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');settle(db,'rejected')
            sync_budget_status(db,trial_id)
        raise
    if not (reliable and valid and allowed): raise ValueError('embedding_unknown' if not reliable else 'embedding_rejected')
    with connect() as db:
        return vectors.hybrid(db,cid,owner_id,query,query_vector=normalized[0],model_version=MODEL)


def cached_query_vector(db,cid,owner_id,query):
    """Pure read: only exact query and current source snapshot reuse vectors."""
    if not all(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() for name in ('mv3_model_chunks','mv3_jobs','mv3_chronicle_vectors')):
        return None
    from . import memory_v2_chronicle_indexing as indexing
    current = indexing.cached_query(db,cid,owner_id,query)
    if current is not None:
        return current
    try: bound,_=snapshot(db,cid,owner_id,query)
    except ValueError: return None
    rows=db.execute("SELECT j.payload_json,c.output_json FROM mv3_jobs j JOIN mv3_model_chunks c ON c.job_id=j.id WHERE j.character_id=? AND j.kind='A3_PROCESSING' AND c.state='completed' AND c.output_json IS NOT NULL ORDER BY j.created_at DESC LIMIT 200",(cid,)).fetchall()
    for row in rows:
        try:
            payload=json.loads(row['payload_json']);prior=payload['readSet']
            if payload.get('executionKind')!='chronicle_embedding_v1': continue
            if any(prior.get(k)!=v for k,v in bound.items()):continue
            return vectors.unit(json.loads(row['output_json'])['query'])
        except (ValueError,KeyError,TypeError):continue
    return None


def cached_search(db,cid,owner_id,query,**options):
    vector=cached_query_vector(db,cid,owner_id,query)
    if vector is not None:
        try:
            return vectors.hybrid(db,cid,owner_id,query,query_vector=vector,model_version=MODEL,**options)
        except ValueError:
            pass
    return channel.search(db,cid,owner_id,query,**options)
