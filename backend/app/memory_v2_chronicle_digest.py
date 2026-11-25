"""Bounded Responses transport for the existing Temporal DIGEST authority.

No new schema, automatic review, composite choice or alternate lifecycle.
The initial paid action accepts one bounded request; larger plans refuse before
counting. The existing input manifest and M3 binding remain authoritative.
"""
import json,time,uuid,sqlite3
from . import memory_v2_temporal_digest as temporal, memory_v2_processing as processing
from . import memory_v2_jobs as jobs, memory_v2_m36_trial as trials
from . import memory_v2_responses_adapter as responses, memory_v2_metadata as metadata
VERSION='chronicle-temporal-responses-v1'
MODEL='gpt-6.1-sol'
SCHEMA={'type':'object','additionalProperties':False,'required':['items'],'properties':{'items':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['inputId','text'],'properties':{'inputId':{'type':'string'},'text':{'type':'string'}}}}}}

def validate_config(db,cid,owner,config):
    metadata._owner(db,owner)
    if (set(config)!= {'version','ownerId','trialId','model','priceHash','inputTokenCap','outputTokenCap'} or
        config['version']!=VERSION or config['ownerId']!=owner or config['model']!=MODEL or
        config['inputTokenCap']!=8192 or config['outputTokenCap']!=6144):raise ValueError('digest_real_config_invalid')
    row=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=? AND character_id=?',(config['trialId'],cid)).fetchone()
    if row is None or row['model']!=MODEL or row['provider']!='openai_responses' or temporal._hash(json.loads(row['price_json']))!=config['priceHash']:
        raise ValueError('digest_real_budget_changed')
    return row

def create(db,cid,node_id,owner,*,now,price,max_microusd=122880):
    processing._transaction(db);metadata._owner(db,owner)
    if not 0<max_microusd<=122880:raise ValueError('digest_real_budget_invalid')
    tid=trials.create_trial(db,cid,MODEL,price,max_microusd=max_microusd,max_requests=1)
    config={'version':VERSION,'ownerId':owner,'trialId':tid,'model':MODEL,'priceHash':temporal._hash(price),'inputTokenCap':8192,'outputTokenCap':6144}
    jid=temporal.create_job(db,cid,node_id,owner,now=now,input_cap=12000,output_cap=12000,real_config=config)
    return {'jobId':jid,'trialId':tid}

def request_for(payload,batch):
    content={'inputs':batch,'manifestHash':payload['readSet']['manifestHash'],'inputToken':payload['readSet']['inputToken']}
    return {'model':MODEL,'instructions':temporal.PROMPT,'input':[{'role':'user','content':temporal._canonical(content)}],
        'text':{'format':{'type':'json_schema','name':'chronicle_period_digest','strict':True,'schema':SCHEMA}},
        'max_output_tokens':6144,'service_tier':'default','reasoning':{'mode':'standard','effort':'medium'},
        'store':False,'background':False,'stream':False,'truncation':'disabled'}

def run(connect,jid,cid,owner,adapter):
    if not isinstance(adapter,responses.ResponsesAdapter) or adapter.model!=MODEL:raise ValueError('digest_real_adapter_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');metadata._owner(db,owner)
        job,payload=temporal._job(db,jid,cid);config=payload['readSet'].get('realConfig',{})
        trial=validate_config(db,cid,owner,config);tid=trial['id']
        if job['status']=='completed':
            if temporal.m3._validity(db,job['result_version_id'],cid,set(),draft=True):raise ValueError('digest_result_stale')
            return {'status':'completed','versionId':job['result_version_id'],'reused':True}
        if job['status']!='queued' or trial['status']!='created' or job['cancel_intent']:raise ValueError('digest_not_repeatable')
        price=json.loads(trial['price_json'])
        reserve=responses.synopsis_quote_microusd(8192,6144,price['inputUsdPerMillion'],price['outputUsdPerMillion'])
        if reserve>trial['max_microusd']:raise ValueError('period_budget_insufficient')
        same,batches=temporal._same_inputs(db,payload,cid)
        if not same or len(batches)!=1:raise ValueError('digest_input_changed')
        request=request_for(payload,batches[0]);request_hash=temporal._hash(request)
        processing.prepare_chunks(db,jid,cid,[{'reservedTokens':14336,'requestHash':request_hash}])
        db.execute("UPDATE mv3_model_chunks SET trial_id=?,input_hash=?,preflight_input_tokens=-1 WHERE job_id=?",(tid,request_hash,jid))
        db.execute("UPDATE mv3_trial_budgets SET status='counting' WHERE id=?",(tid,))
    try:
        count=adapter.count_input(request)
        if count>8192:raise ValueError('digest_input_token_limit')
    except Exception:
        with connect() as db:db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code='count_failed' WHERE id=?",(tid,))
        raise ValueError('digest_count_failed') from None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');job,payload=temporal._job(db,jid,cid)
        trial=validate_config(db,cid,owner,config);same,batches=temporal._same_inputs(db,payload,cid)
        if not same or job['cancel_intent'] or trial['status']!='counting' or reserve>trial['max_microusd']:
            raise ValueError('digest_preflight_changed_or_budget')
        if temporal._hash(request_for(payload,batches[0]))!=request_hash:raise ValueError('digest_request_changed')
        lease=str(uuid.uuid4())
        if db.execute("UPDATE mv3_jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=? AND status='queued'",(lease,time.time()+120,jid)).rowcount!=1:raise ValueError('digest_claim_conflict')
        db.execute('UPDATE mv3_model_chunks SET reserved_usd_micros=?,preflight_input_tokens=? WHERE job_id=?',(reserve,count,jid))
        db.execute("UPDATE mv3_trial_budgets SET reserved_microusd=?,dispatched_requests=1,status='running' WHERE id=?",(reserve,tid))
        identity=processing.dispatch(db,jid,cid,0,original_owner_id=owner)
    result=None
    try:result=adapter.generate(request)
    except Exception:pass
    usage=result.get('usage') if isinstance(result,dict) else None
    reliable=(isinstance(usage,dict) and type(usage.get('input_tokens')) is int and type(usage.get('output_tokens')) is int and min(usage['input_tokens'],usage['output_tokens'])>=0)
    cost=responses.synopsis_usage_estimate(usage,price['inputUsdPerMillion'],price['outputUsdPerMillion'])['microusd'] if reliable else None
    items=None;reason='provider_unknown'
    if reliable:
        reason='provider_result_rejected'
        if result.get('status')=='completed' and result.get('modelId')==MODEL and result.get('serviceTier')=='default' and usage['input_tokens']<=8192 and usage['output_tokens']<=6144:
            try:items=temporal._validated_items(batches[0],{'finishReason':'stop','output':json.loads(result['text'])},12000);reason=None
            except (ValueError,TypeError,KeyError):reason='digest_output_invalid'
    def settle(db,state):
        revoked=not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',(cid,)).fetchone()
        return jobs.settle_dispatch(db,jid,cid,0,identity['requestId'],identity['dispatchProof'],
            state='unknown' if not reliable else ('rejected' if revoked else state),used_input=usage['input_tokens'] if reliable else None,
            used_output=usage['output_tokens'] if reliable else None,trial_id=tid if reliable else None,usd_estimate=cost,
            result_hash=temporal._hash(result) if reliable else None,original_owner_id=owner if revoked else None)
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                job,payload=temporal._job(db,jid,cid);same,_=temporal._same_inputs(db,payload,cid)
                allowed=(same and not job['cancel_intent'] and job['lease_token']==lease and job['lease_until'] is not None and job['lease_until']>time.time())
            except ValueError:allowed=False
            if reason is None and allowed:
                settle(db,'completed')
                db.execute('UPDATE mv3_model_chunks SET output_json=?,provider_usage_json=?,usage_estimated_usd_micros=? WHERE job_id=?',(temporal._canonical(items),json.dumps(usage),cost,jid))
                # Business savepoint in _finish cannot undo confirmed settlement.
                return temporal._finish(db,job,payload,lease)
            settle(db,'rejected')
            return {'status':'unknown' if not reliable else 'rejected','reason':reason or 'digest_business_qualification_changed'}
    except sqlite3.DatabaseError:
        with connect() as db:db.execute('BEGIN IMMEDIATE');settle(db,'rejected')
        return {'status':'rejected','reason':'digest_output_persistence_failed'}
