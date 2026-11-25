"""Self-contained synthetic supplement tests; one slow loopback, no Provider."""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import httpx
import test_memory_v2_a34_diagnostics as diagnostics
from test_memory_v2_a34_responses_wiring import FakeWire,_output_for,MODEL,PRICE
from test_memory_v2_h32 import app_module
from backend.app import memory_v2_synopsis as synopsis
from backend.app import memory_v2_synopsis_replacement as replacement
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_m36_trial as trials
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_jobs as jobs

class Supplement(unittest.TestCase):
    setUp=diagnostics.A34DiagnosticRetention.setUp

    def adapter(self,wire):
        wire.adapter.wait_policy=responses.SLOW_WAIT_POLICY
        wire.adapter.client.timeout=httpx.Timeout(60,connect=10,read=180)
        return wire.adapter

    def create(self,index=0):
        with app_module.connect() as db:
            return synopsis.create_real_trial(db,self.entries[index],self.cid,self.owner,
                model=MODEL,price=PRICE,profile=synopsis.REAL_SPEAKER_PROFILE,
                batch_input_cap=12000,output_cap=6144,max_microusd=122880)

    def execute(self,trial,adapter):
        synopsis.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
        return synopsis.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,
                                       adapter,diagnostic_sink=lambda record:None)

    def original_unknown(self):
        wire=diagnostics.Wire()
        def timeout(request):
            if request.url.path.endswith('/input_tokens'): return wire.handle(request)
            raise httpx.ReadTimeout('synthetic unknown')
        wire.adapter=responses.ResponsesAdapter(model=MODEL,transport=httpx.MockTransport(timeout))
        trial,result=diagnostics.A34DiagnosticRetention.run_case(self,wire,sink=lambda record:None)
        self.assertEqual(result['status'],'unknown')
        return trial

    def approval(self,trial):
        with app_module.connect() as db:
            old=processing._owned(db,trial['jobId'],self.cid);p=json.loads(old['payload_json'])
            child=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(old['id'],)).fetchone()
            return dict(version=replacement.EVENT,approvalId='synthetic-one-human-decision',
                originalJobId=old['id'],originalRequestId=child['request_id'],
                originalAttemptId=child['dispatch_attempt_id'],entryId=p['targetId'],ownerId=self.owner,
                sourceToken=p['readSet']['sourceToken'],boundaryBinding=p['readSet']['boundaryBinding'],
                maxMicrousd=122880,maxGenerations=1,maxCounts=1,expiresAtUnix=time.time()+86400,
                acknowledgeDuplicateCost=True,approved=True)

    def test_mapping_prompt_is_new_and_preserves_quotes_conditions_corrections(self):
        sources=[dict(kind='message',id='1',speaker='user',text='我照顾阿夜。若病好再出门。更正：明天也不出门。',sourceStatus='imported'),
                 dict(kind='message',id='2',speaker='assistant',text='俺がイキヤを看病する。友人は「彼女が私を助けた」と言った。',sourceStatus='imported')]
        actual={'phase':'batch','sources':sources}
        new=responses.synopsis_request_payload(MODEL,'batch',actual,6144,profile=synopsis.REAL_SPEAKER_PROFILE)
        old=responses.synopsis_request_payload(MODEL,'batch',actual,6144,profile=synopsis.REAL_CHRONICLE_REVIEW_PROFILE)
        self.assertIn('speaker=user 的发言者固定为イキヤ',new['instructions'])
        self.assertIn('speaker=assistant 的发言者固定为阿夜',new['instructions'])
        self.assertNotIn('固定为イキヤ',old['instructions'])
        self.assertEqual(json.loads(new['input'][0]['content']),actual)
        for text,speaker,allowed in [
            ('イキヤ说，她照顾阿夜。','user',True),('阿夜说，我照顾イキヤ。','assistant',True),
            ('阿夜转述友人说「彼女が私を助けた」。','assistant',True),
            ('イキヤ说，若病好再出门；更正，明天也不出门。','user',True),
            ('阿夜说，自己照顾イキヤ。','user',False),
            ('イキヤ问阿夜疼不疼。','assistant',False)]:
            out={'text':text,'units':[{'start':0,'end':len(text),'evidence':[{'speaker':speaker}]}]}
            if allowed: synopsis.validate_speaker_anchors(out)
            else:
                with self.assertRaisesRegex(ValueError,'synopsis_explicit_speaker_conflict'):
                    synopsis.validate_speaker_anchors(out)

    def test_new_profile_roundtrip_and_wrong_timeout_zero_send(self):
        wire=FakeWire();trial=self.create()
        with self.assertRaisesRegex(ValueError,'synopsis_wait_policy_mismatch'):
            synopsis.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        self.assertEqual(len(wire.counts),0)
        out=self.execute(trial,self.adapter(wire));self.assertEqual(out['status'],'completed',out)
        with app_module.connect() as db:
            field=metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']
            self.assertEqual(field['reviewStatus'],'needs_review')
            self.assertEqual(field['lifecycleStatus'],'draft')

    def test_unknown_replacement_one_allowance_old_money_and_business_retirement(self):
        old=self.original_unknown();approval=self.approval(old)
        with app_module.connect() as db:
            old_chunk=dict(db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(old['jobId'],)).fetchone())
            old_budget=dict(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(old['trialId'],)).fetchone())
            with self.assertRaisesRegex(ValueError,'semantic_scope_unknown'):
                synopsis.create_chronicle_trial(db,self.entries[0],self.cid,self.owner,price=PRICE)
        with app_module.connect() as db:
            new=replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=approval)
        wire=FakeWire();adapter=self.adapter(wire)
        result=self.execute(new,adapter);self.assertEqual(result['status'],'completed',result)
        again=synopsis.run_real_trial(app_module.connect,new['jobId'],self.cid,self.owner,adapter,diagnostic_sink=lambda r:None)
        self.assertTrue(again['reused']);self.assertEqual(len(wire.generations),1)
        with app_module.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(old['jobId'],)).fetchone()),old_chunk)
            self.assertEqual(dict(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(old['trialId'],)).fetchone()),old_budget)
            with self.assertRaisesRegex(ValueError,'synopsis_business_superseded'):
                synopsis.guard_field(db,self.entries[0],self.cid,except_id=old['jobId'])
            self.assertEqual(replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=approval),new)
            with self.assertRaisesRegex(ValueError,'synopsis_replacement_allowance_already_used'):
                replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=dict(approval,approvalId='another-decision'))

    def test_retired_original_sender_can_settle_only_with_original_proof(self):
        issued=[];persist=jobs.persist_dispatch_identity
        def capture(*args,**kwargs):
            proof=persist(*args,**kwargs);issued.append(proof);return proof
        with patch.object(jobs,'persist_dispatch_identity',side_effect=capture):
            old=self.original_unknown()
        with app_module.connect() as db:
            new=replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=self.approval(old))
        proof=issued[0]
        for _ in range(2):
            with app_module.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                jobs.settle_dispatch(db,old['jobId'],self.cid,0,proof.request_id,proof,
                    state='rejected',used_input=20,used_output=20,used_cost=0,
                    result_hash='synthetic-late-known-usage',trial_id=old['trialId'],usd_estimate=240)
        with app_module.connect() as db:
            budget=trials.trial_status(db,old['trialId'],self.cid)
            self.assertEqual(budget['spent_estimate_microusd'],240)
            self.assertEqual(budget['reserved_microusd'],0)
            self.assertIsNone(db.execute('SELECT output_json FROM mv3_model_chunks WHERE job_id=?',(old['jobId'],)).fetchone()[0])
            with self.assertRaisesRegex(ValueError,'synopsis_business_superseded'):
                synopsis.guard_field(db,self.entries[0],self.cid,except_id=old['jobId'])
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(new['jobId'],)).fetchone()[0],'queued')

    def test_new_profile_unknown_and_diagnostic_disk_failure_keep_accounting(self):
        for i,usage in enumerate((diagnostics.RETAINED_USAGE,None)):
            wire=diagnostics.Wire(usage=usage);adapter=self.adapter(wire);trial=self.create(i)
            synopsis.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
            def fail(_): raise OSError('synthetic disk error')
            result=synopsis.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter,diagnostic_sink=fail)
            self.assertFalse(result['diagnosticStored'])
            with app_module.connect() as db:
                budget=trials.trial_status(db,trial['trialId'],self.cid)
                self.assertEqual(budget['spent_estimate_microusd'],23401 if usage else 0)
                self.assertEqual(budget['reserved_microusd']>0,not bool(usage))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',(trial['jobId'],)).fetchone()[0],0)

    def test_slow_profile_lease_covers_130_second_generation(self):
        now=[time.time()];wire=FakeWire(on_generation=lambda _:now.__setitem__(0,now[0]+130))
        trial=self.create()
        with patch.object(synopsis.time,'time',side_effect=lambda:now[0]):
            result=self.execute(trial,self.adapter(wire))
        self.assertEqual(result['status'],'completed',result)

    def test_missing_approval_forgery_and_source_drift_zero_send(self):
        old=self.original_unknown();approval=self.approval(old)
        for changed in (dict(approval,approved=False),dict(approval,acknowledgeDuplicateCost=False),
                        dict(approval,originalAttemptId='forged'),dict(approval,ownerId=999),dict(approval,ownerId=True),
                        dict(approval,maxGenerations=2),dict(approval,sourceToken='changed')):
            with app_module.connect() as db:
                with self.assertRaises(ValueError):
                    replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=changed)
                db.rollback()
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0],1)

    def test_readset_tamper_does_not_grant_scope_exemption(self):
        old=self.original_unknown()
        with app_module.connect() as db:
            new=replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=self.approval(old))
            row=db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(new['jobId'],)).fetchone()
            payload=json.loads(row[0]);payload['readSet']['replacement']['originalAttemptId']='forged'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(payload),new['jobId']))
        wire=FakeWire()
        with self.assertRaises(ValueError): self.execute(new,self.adapter(wire))
        self.assertEqual(len(wire.counts),0);self.assertEqual(len(wire.generations),0)

    def test_old_request_missing_durable_owner_proof_cannot_get_allowance(self):
        old=self.original_unknown();approval=self.approval(old)
        with app_module.connect() as db:
            db.execute("DELETE FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1'",(old['jobId'],))
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError,'synopsis_original_owner_proof_missing'):
                replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=approval)

    def test_two_simultaneous_approvals_share_one_new_job(self):
        import sqlite3
        from contextlib import contextmanager
        from concurrent.futures import ThreadPoolExecutor
        old=self.original_unknown();approval=self.approval(old)
        def create_once(_):
            db=sqlite3.connect(app_module.DB,timeout=15);db.row_factory=sqlite3.Row
            try:
                with db:
                    return replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=approval)
            finally: db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(create_once,range(2)))
        self.assertEqual(results[0],results[1])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_trial_budgets').fetchone()[0],2)

    def test_changed_sources_or_manual_fields_prevent_replacement(self):
        old=self.original_unknown();approval=self.approval(old)
        with app_module.connect() as db:
            detail=metadata.read_draft(db,self.entries[0],self.cid)
            metadata.revise(db,self.entries[0],self.cid,detail['versionId'],{'synopsis':'Owner 手工内容。'},self.owner)
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError,'synopsis_field_protected'):
                replacement.create_replacement_trial(db,old['jobId'],self.cid,self.owner,price=PRICE,approval=approval)

    def test_process_exit_after_dispatch_keeps_unknown_and_reserve(self):
        trial=self.create();wire=FakeWire();self.adapter(wire)
        synopsis.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        script='''import os,sqlite3,httpx,sys
from contextlib import contextmanager
from backend.app import memory_v2_synopsis as s,memory_v2_responses_adapter as r
@contextmanager
def connect():
 db=sqlite3.connect(sys.argv[1]);db.row_factory=sqlite3.Row
 try:
  with db: yield db
 finally: db.close()
def stop(request): os._exit(77)
a=r.ResponsesAdapter(model="gpt-6.1-sol",transport=httpx.MockTransport(stop),wait_policy=r.SLOW_WAIT_POLICY)
s.run_real_trial(connect,sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),a,diagnostic_sink=lambda x:None)
'''
        result=subprocess.run([sys.executable,'-c',script,str(app_module.DB),trial['jobId'],str(self.cid),str(self.owner)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,77,result.stderr.decode(errors='replace'))
        with app_module.connect() as db:
            row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            self.assertEqual(row['state'],'possibly_dispatched');self.assertTrue(row['dispatch_attempt_id'])
            self.assertIsNone(row['output_json']);self.assertIsNone(row['used_input_tokens'])
            budget=trials.trial_status(db,trial['trialId'],self.cid)
            self.assertEqual(budget['dispatched_requests'],1);self.assertGreater(budget['reserved_microusd'],0)
        with self.assertRaisesRegex(ValueError,'synopsis_trial_not_claimable'):
            synopsis.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter,diagnostic_sink=lambda x:None)

    def test_61_second_response_is_saved_settled_and_not_expired(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(self.path)
                if self.path.endswith('/input_tokens'): body={'input_tokens':100}
                else:
                    time.sleep(61)
                    actual=json.loads(payload['input'][0]['content'])
                    body=dict(id='resp_synthetic_slow',model=MODEL,status='completed',service_tier='default',
                        usage=dict(input_tokens=100,output_tokens=100,input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0)),
                        output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(_output_for(actual),ensure_ascii=False))])])
                encoded=json.dumps(body).encode();self.send_response(200)
                self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(responses,'API_ROOT',f'http://127.0.0.1:{server.server_port}/v1'):
                adapter=responses.ResponsesAdapter(model=MODEL,allow_network=True,api_key='synthetic-loopback-only',wait_policy=responses.SLOW_WAIT_POLICY)
                self.addCleanup(adapter.close);trial=self.create();result=self.execute(trial,adapter)
                self.assertEqual(result['status'],'completed',result)
                self.assertGreaterEqual(adapter.last_transport_diagnostic['elapsedSeconds'],61)
                self.assertTrue(adapter.last_transport_diagnostic['bodyComplete'])
                self.assertEqual(calls,['/v1/responses/input_tokens','/v1/responses'])
                with app_module.connect() as db:
                    budget=trials.trial_status(db,trial['trialId'],self.cid)
                    self.assertEqual(budget['reserved_microusd'],0);self.assertGreater(budget['spent_estimate_microusd'],0)
                    self.assertTrue(db.execute('SELECT output_json FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()[0])
        finally: server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()
