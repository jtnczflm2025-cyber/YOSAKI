"""Explicit Owner-approved, single replacement; never reconciliation or a retry loop.

Internal coordinator entry only. No HTTP route accepts an approval/bypass flag.
The trusted paid runner must first verify the separate human approval and its
frozen manifest. Reading a plan never calls this function or creates an approval.
"""
import json
import math
import time
from . import memory_v2_processing as processing
from . import memory_v2_metadata as metadata
from . import memory_v2_synopsis as synopsis
from . import memory_v2_m36_trial as trials
from . import memory_v2_jobs as jobs

EVENT='synopsis_one_replacement_v1'
OLD_EVENT='synopsis_original_business_retired_v1'
CHAIN_EVENT='synopsis_stream_replacement_chain_v2'
CHAIN_8192_EVENT='synopsis_stream_replacement_chain_capacity_v3'
_CHAIN_EVENTS=(CHAIN_EVENT, CHAIN_8192_EVENT)

def _stream_contract(version):
    if version==CHAIN_EVENT:return synopsis.REAL_STREAM_PROFILE,6144,122880,None
    if version==CHAIN_8192_EVENT:return synopsis.REAL_STREAM_8192_PROFILE,8192,147456,synopsis.STREAM_8192_CAPACITY_VERSION
    raise ValueError('synopsis_unknown_chain_version')

def require_business_current(db, job_id):
    if db.execute('SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event=?',
                  (job_id,OLD_EVENT)).fetchone():
        raise ValueError('synopsis_business_superseded')

def _binding(db, job_id):
    row=db.execute('SELECT * FROM mv3_jobs WHERE id=?',(job_id,)).fetchone()
    if row is None: return None
    payload=json.loads(row['payload_json'])
    binding=payload.get('readSet',{}).get('replacement')
    if not binding: return None
    if binding.get('version') in _CHAIN_EVENTS:
        return _chain_binding(db,row,payload,binding)
    if (processing._hash(['a3-job-v1',row['character_id'],payload])!=row['input_hash'] or
        processing._canonical(payload['readSet'])!=row['read_set_json'] or
        payload.get('fields')!=['synopsis'] or
        json.loads(row['write_set_json'])!={'fields':['synopsis']}): return None
    digest=processing._hash([EVENT,job_id,binding])
    if not db.execute('SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event=? AND evidence_hash=?',
                      (job_id,EVENT,digest)).fetchone(): return None
    old=db.execute('SELECT * FROM mv3_jobs WHERE id=?',(binding['originalJobId'],)).fetchone()
    child=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                     (binding['originalJobId'],)).fetchone()
    if (old is None or child is None or old['character_id']!=row['character_id'] or
        old['scope_key']!=row['scope_key'] or
        processing._hash(json.loads(old['payload_json']))!=binding['originalPayloadHash'] or
        child['request_id']!=binding['originalRequestId'] or
        child['dispatch_attempt_id']!=binding['originalAttemptId'] or
        child['input_hash']!=binding['originalInputHash'] or
        not db.execute('SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event=? AND evidence_hash=?',
                       (old['id'],OLD_EVENT,digest)).fetchone()): return None
    prepared=db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state='prepared'",(job_id,)).fetchone()
    if prepared and time.time()>=binding['expiresAtUnix']: return None
    config=payload['readSet']['realConfig']
    if (payload['ruleVersion']!=synopsis.REAL_SPEAKER_PROFILE or
        config['ownerId']!=binding['ownerId'] or config['trialId']!=binding['newTrialId'] or
        config['priceHash']!=binding['priceHash']): return None
    owner_hash=jobs._dispatch_owner_hash(binding['ownerId'],old['id'],row['character_id'],0,
        child['request_id'],child['dispatch_attempt_id'],child['input_hash'])
    if (binding.get('originalOwnerBindingHash')!=owner_hash or not db.execute(
        "SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1' AND evidence_hash=?",
        (old['id'],owner_hash)).fetchone()): return None
    return binding

def may_ignore_original(db, job_id, original_id):
    binding=_binding(db,job_id)
    return bool(binding and (binding['originalJobId']==original_id or
        (binding.get('version') in _CHAIN_EVENTS and original_id in
         [r['jobId'] for r in binding['requests']])))

def binding_current(db, character_id, payload):
    row=db.execute('SELECT id,read_set_json FROM mv3_jobs WHERE character_id=? AND input_hash=?',
        (character_id,processing._hash(['a3-job-v1',character_id,payload]))).fetchone()
    if row is None or processing._canonical(payload['readSet'])!=row['read_set_json']: return False
    return _binding(db,row['id']) is not None

def create_replacement_trial(db, original_job_id, character_id, editor_id, *, price, approval):
    """One committed human allowance per original; old request and money untouched."""
    processing._transaction(db)
    metadata._owner(db,editor_id)
    old=processing._owned(db,original_job_id,character_id)
    prior=json.loads(old['payload_json'])
    expected={'version','approvalId','originalJobId','originalRequestId','originalAttemptId',
              'entryId','ownerId','sourceToken','boundaryBinding','maxMicrousd','maxGenerations',
              'maxCounts','expiresAtUnix','acknowledgeDuplicateCost','approved'}
    if (type(approval) is not dict or set(approval)!=expected or
        approval['version']!=EVENT or approval['approved'] is not True or
        approval['acknowledgeDuplicateCost'] is not True or
        not isinstance(approval['approvalId'],str) or not approval['approvalId'] or
        len(approval['approvalId'])>160 or type(approval['ownerId']) is not int or approval['ownerId']!=editor_id or
        approval['originalJobId']!=original_job_id or
        type(approval['expiresAtUnix']) not in (int,float) or
        not math.isfinite(approval['expiresAtUnix']) or
        not time.time()<approval['expiresAtUnix']<=time.time()+7*86400 or
        type(approval['maxMicrousd']) is not int or not 0<approval['maxMicrousd']<=122880 or
        type(approval['maxGenerations']) is not int or approval['maxGenerations']!=1 or
        type(approval['maxCounts']) is not int or approval['maxCounts']!=1):
        raise ValueError('synopsis_explicit_replacement_approval_required')
    if (prior.get('executionKind')!='responses_synopsis_v1' or prior.get('fields')!=['synopsis'] or
        old['status'] not in ('unknown','failed') or old['cancel_intent'] or
        approval['entryId']!=prior['targetId'] or
        prior['readSet']['realConfig']['ownerId']!=editor_id):
        raise ValueError('synopsis_original_not_replaceable')
    prior_allowance=db.execute('SELECT evidence_hash FROM mv3_a3_audit WHERE job_id=? AND event=?',
                               (original_job_id,OLD_EVENT)).fetchone()
    if prior_allowance:
        issued=db.execute('SELECT job_id FROM mv3_a3_audit WHERE event=? AND evidence_hash=?',
                          (EVENT,prior_allowance[0])).fetchone()
        binding=_binding(db,issued[0]) if issued else None
        if (binding is None or any(binding.get(k)!=v for k,v in approval.items()) or
            synopsis.confirmed_snapshot(db,approval['entryId'],character_id)['sourceToken']!=approval['sourceToken'] or
            synopsis.boundary_binding(db,approval['entryId'],character_id)!=approval['boundaryBinding']):
            raise ValueError('synopsis_replacement_allowance_already_used')
        return dict(jobId=issued[0],trialId=binding['newTrialId'],generationLimit=1,countLimit=1,
                    maxMicrousd=binding['maxMicrousd'],originalJobId=original_job_id)
    require_business_current(db,original_job_id)
    chunks=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(original_job_id,)).fetchall()
    if (len(chunks)!=1 or not chunks[0]['dispatch_attempt_id'] or
        chunks[0]['request_id']!=approval['originalRequestId'] or
        chunks[0]['dispatch_attempt_id']!=approval['originalAttemptId'] or
        chunks[0]['state'] not in ('unknown','rejected') or
        db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',(original_job_id,)).fetchone()):
        raise ValueError('synopsis_original_identity_invalid')
    owner_hash=jobs._dispatch_owner_hash(editor_id,old['id'],character_id,0,
        chunks[0]['request_id'],chunks[0]['dispatch_attempt_id'],chunks[0]['input_hash'])
    if not db.execute("SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1' AND evidence_hash=?",
                      (old['id'],owner_hash)).fetchone():
        raise ValueError('synopsis_original_owner_proof_missing')
    if db.execute('SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=?',(original_job_id,)).fetchone():
        raise ValueError('legacy_reconciliation_required')
    # Existing scope and human-field checks, excluding only this identified old job.
    synopsis.guard_field(db,prior['targetId'],character_id,except_id=original_job_id)
    snap=synopsis.confirmed_snapshot(db,prior['targetId'],character_id)
    boundary=synopsis.boundary_binding(db,prior['targetId'],character_id)
    if (snap['sourceToken']!=approval['sourceToken'] or
        boundary!=approval['boundaryBinding'] or snap['sourceToken']!=prior['readSet']['sourceToken'] or
        boundary!=prior['readSet']['boundaryBinding']):
        raise ValueError('synopsis_replacement_sources_changed')
    trial_id=trials.create_trial(db,character_id,'gpt-6.1-sol',price,
                                max_microusd=approval['maxMicrousd'],max_requests=1)
    read_set,_,batches=synopsis._real_read_set(db,prior['targetId'],character_id,
        12000,4096,6144,trial_id,'gpt-6.1-sol',synopsis._hash(price),editor_id,
        profile=synopsis.REAL_SPEAKER_PROFILE)
    if len(batches)!=1: raise ValueError('synopsis_replacement_requires_one_batch')
    binding=dict(approval,originalPayloadHash=processing._hash(prior),
                 originalInputHash=chunks[0]['input_hash'],newTrialId=trial_id,
                 priceHash=synopsis._hash(price),originalOwnerBindingHash=owner_hash)
    read_set['replacement']=binding
    job_id=processing._create_record(db,character_id,'entry',prior['targetId'],['synopsis'],
        read_set,rule_version=synopsis.REAL_SPEAKER_PROFILE,
        input_version='confirmed-ordered-raw-v1',execution_kind='responses_synopsis_v1')
    digest=processing._hash([EVENT,job_id,binding])
    for jid,event in ((job_id,EVENT),(original_job_id,OLD_EVENT)):
        db.execute('INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at) '
                   'VALUES(?,?,?,?,datetime(\'now\'))',
                   (jid,event,digest,old['status'] if jid==original_job_id else 'queued'))
    return dict(jobId=job_id,trialId=trial_id,generationLimit=1,countLimit=1,
                maxMicrousd=approval['maxMicrousd'],originalJobId=original_job_id)


def _request_reference(db,job_id,character_id,entry_id,owner_id):
    row=processing._owned(db,job_id,character_id);p=json.loads(row['payload_json'])
    children=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',(job_id,)).fetchall()
    if (processing._hash(['a3-job-v1',character_id,p])!=row['input_hash'] or
        processing._canonical(p['readSet'])!=row['read_set_json'] or
        p.get('executionKind')!='responses_synopsis_v1' or p.get('targetId')!=entry_id or
        p.get('fields')!=['synopsis'] or p['readSet']['realConfig']['ownerId']!=owner_id or
        len(children)!=1 or not children[0]['request_id'] or not children[0]['dispatch_attempt_id'] or
        children[0]['state'] not in ('unknown','rejected') or
        db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',(job_id,)).fetchone() or
        db.execute('SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=?',(job_id,)).fetchone()):
        raise ValueError('synopsis_chain_request_invalid')
    c=children[0]
    owner_hash=jobs._dispatch_owner_hash(owner_id,job_id,character_id,0,c['request_id'],c['dispatch_attempt_id'],c['input_hash'])
    if not db.execute("SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1' AND evidence_hash=?",(job_id,owner_hash)).fetchone():
        raise ValueError('synopsis_chain_owner_proof_missing')
    return dict(jobId=job_id,requestId=c['request_id'],attemptId=c['dispatch_attempt_id'],
        inputHash=c['input_hash'],payloadHash=processing._hash(p),ownerBindingHash=owner_hash),row,p


def chain_references(db,latest_job_id,character_id,entry_id,owner_id):
    """Read-only exact ancestry; no allowance or reservation is created."""
    seen=set();refs=[];current=latest_job_id
    while current:
        if current in seen or len(seen)>=8:raise ValueError('synopsis_chain_cycle_or_limit')
        seen.add(current);ref,row,p=_request_reference(db,current,character_id,entry_id,owner_id)
        refs.append(ref);binding=p['readSet'].get('replacement')
        if binding:
            if _binding(db,current) is None:raise ValueError('synopsis_chain_binding_invalid')
            current=binding['originalJobId']
        else:current=None
    return list(reversed(refs))


def _chain_binding(db,row,payload,binding):
    try:
        profile,output_cap,budget_cap,capacity_version=_stream_contract(binding['version'])
        if (processing._hash(['a3-job-v1',row['character_id'],payload])!=row['input_hash'] or
            processing._canonical(payload['readSet'])!=row['read_set_json'] or payload.get('fields')!=['synopsis'] or
            json.loads(row['write_set_json'])!={'fields':['synopsis']} or
            payload['ruleVersion']!=profile or
            payload['readSet']['profile']!=profile or
            payload['readSet']['validator']!=synopsis.QUOTE_VALIDATOR):return None
        digest=processing._hash([binding['version'],row['id'],binding])
        if not db.execute('SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event=? AND evidence_hash=?',(row['id'],binding['version'],digest)).fetchone():return None
        config=payload['readSet']['realConfig']
        if (config['outputTokenCap']!=output_cap or config['inputTokenCap']!=8192 or
            payload['readSet'].get('capacityVersion')!=capacity_version or
            (capacity_version is not None and (binding.get('capacityVersion')!=capacity_version or binding.get('inputTokenCap')!=8192 or binding.get('outputTokenCap')!=8192))):return None
        if (config['ownerId']!=binding['ownerId'] or config['trialId']!=binding['newTrialId'] or config['priceHash']!=binding['priceHash']):return None
        # Settlement state and budget may change through legal late usage; identity stays fixed.
        for ref in binding['requests']:
            old=db.execute('SELECT * FROM mv3_jobs WHERE id=?',(ref['jobId'],)).fetchone()
            c=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(ref['jobId'],)).fetchone()
            if (old is None or c is None or old['character_id']!=row['character_id'] or old['scope_key']!=row['scope_key'] or
                processing._hash(json.loads(old['payload_json']))!=ref['payloadHash'] or c['request_id']!=ref['requestId'] or
                c['dispatch_attempt_id']!=ref['attemptId'] or c['input_hash']!=ref['inputHash'] or
                jobs._dispatch_owner_hash(binding['ownerId'],old['id'],row['character_id'],0,c['request_id'],c['dispatch_attempt_id'],c['input_hash'])!=ref['ownerBindingHash'] or
                not db.execute("SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1' AND evidence_hash=?",(old['id'],ref['ownerBindingHash'])).fetchone() or
                not db.execute('SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event=? AND evidence_hash=?',(old['id'],OLD_EVENT,digest)).fetchone()):return None
        if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state='prepared'",(row['id'],)).fetchone() and time.time()>=binding['expiresAtUnix']:return None
        return binding
    except (KeyError,TypeError,ValueError):return None


def create_stream_replacement_trial(db,latest_job_id,character_id,editor_id,*,price,approval):
    """One explicit, fully bound allowance for this chain; no global unknown bypass."""
    processing._transaction(db);metadata._owner(db,editor_id)
    profile,output_cap,budget_cap,capacity_version=_stream_contract(approval.get('version') if isinstance(approval,dict) else None)
    expected={'version','approvalId','originalJobId','entryId','ownerId','sourceToken','boundaryBinding',
        'requests','profile','validator','quoteLocatorVersion','streamVersion','maxMicrousd','maxGenerations',
        'maxCounts','expiresAtUnix','acknowledgeDuplicateCost','approved'}
    if capacity_version is not None:expected|={'capacityVersion','inputTokenCap','outputTokenCap'}
    if (type(approval) is not dict or set(approval)!=expected or approval['version'] not in _CHAIN_EVENTS or
        approval['approved'] is not True or approval['acknowledgeDuplicateCost'] is not True or
        type(approval['ownerId']) is not int or approval['ownerId']!=editor_id or approval['originalJobId']!=latest_job_id or
        not isinstance(approval['approvalId'],str) or not 0<len(approval['approvalId'])<=160 or
        approval['profile']!=profile or approval['validator']!=synopsis.QUOTE_VALIDATOR or
        approval['quoteLocatorVersion']!=synopsis.quotes.VERSION or approval['streamVersion']!=synopsis.streams.VERSION or
        type(approval['maxMicrousd']) is not int or not 0<approval['maxMicrousd']<=budget_cap or
        type(approval['maxGenerations']) is not int or approval['maxGenerations']!=1 or
        type(approval['maxCounts']) is not int or approval['maxCounts']!=1 or
        type(approval['expiresAtUnix']) not in (int,float) or not math.isfinite(approval['expiresAtUnix']) or
        not time.time()<approval['expiresAtUnix']<=time.time()+7*86400):
        raise ValueError('synopsis_explicit_chain_approval_required')
    if capacity_version is not None and (approval['capacityVersion']!=capacity_version or type(approval['inputTokenCap']) is not int or approval['inputTokenCap']!=8192 or type(approval['outputTokenCap']) is not int or approval['outputTokenCap']!=8192):
        raise ValueError('synopsis_chain_capacity_invalid')
    # Completed-operation retries are checked before the operation's own retirement audit.
    for issued in db.execute('SELECT j.* FROM mv3_a3_audit a JOIN mv3_jobs j ON j.id=a.job_id WHERE a.event IN (?,?) AND j.character_id=?',(*_CHAIN_EVENTS,character_id)):
        p=json.loads(issued['payload_json']);b=p.get('readSet',{}).get('replacement',{})
        if b.get('originalJobId')==latest_job_id or b.get('approvalId')==approval['approvalId']:
            current=_binding(db,issued['id'])
            if (current is None or any(current.get(k)!=v for k,v in approval.items()) or
                current['priceHash']!=synopsis._hash(price)):
                raise ValueError('synopsis_chain_allowance_already_used')
            synopsis.guard_field(db,approval['entryId'],character_id,except_id=issued['id'])
            return dict(jobId=issued['id'],trialId=current['newTrialId'],generationLimit=1,countLimit=1,maxMicrousd=current['maxMicrousd'])
    require_business_current(db,latest_job_id)
    latest=processing._owned(db,latest_job_id,character_id)
    if latest['status'] not in ('unknown','failed') or latest['cancel_intent']:raise ValueError('synopsis_chain_latest_invalid')
    refs=chain_references(db,latest_job_id,character_id,approval['entryId'],editor_id)
    if refs!=approval['requests']:raise ValueError('synopsis_chain_incomplete_or_conflicting')
    synopsis.guard_field(db,approval['entryId'],character_id,except_id=latest_job_id)
    snap=synopsis.confirmed_snapshot(db,approval['entryId'],character_id);boundary=synopsis.boundary_binding(db,approval['entryId'],character_id)
    if snap['sourceToken']!=approval['sourceToken'] or boundary!=approval['boundaryBinding']:raise ValueError('synopsis_chain_sources_changed')
    for ref in refs:
        p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(ref['jobId'],)).fetchone()[0])
        if p['readSet']['sourceToken']!=snap['sourceToken'] or p['readSet']['boundaryBinding']!=boundary:raise ValueError('synopsis_chain_sources_changed')
    trial=trials.create_trial(db,character_id,'gpt-6.1-sol',price,max_microusd=approval['maxMicrousd'],max_requests=1)
    read_set,_,batches=synopsis._real_read_set(db,approval['entryId'],character_id,12000,4096,output_cap,
        trial,'gpt-6.1-sol',synopsis._hash(price),editor_id,profile=profile)
    if len(batches)!=1:raise ValueError('synopsis_chain_requires_one_batch')
    binding=dict(approval,newTrialId=trial,priceHash=synopsis._hash(price));read_set['replacement']=binding
    job=processing._create_record(db,character_id,'entry',approval['entryId'],['synopsis'],read_set,
        rule_version=profile,input_version='confirmed-ordered-raw-v1',execution_kind='responses_synopsis_v1')
    digest=processing._hash([approval['version'],job,binding])
    for jid,event in [(job,approval['version'])]+[(ref['jobId'],OLD_EVENT) for ref in refs]:
        db.execute('INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at) VALUES(?,?,?,?,datetime(\'now\'))',
            (jid,event,digest,latest['status'] if jid!=job else 'queued'))
    return dict(jobId=job,trialId=trial,generationLimit=1,countLimit=1,maxMicrousd=approval['maxMicrousd'])
