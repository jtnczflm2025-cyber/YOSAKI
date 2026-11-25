"""Actual A3 chain replacement, synthetic requests and Mock SSE only."""
import copy,json,time,unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import test_memory_v2_synopsis_supplement as supplement
from test_memory_v2_synopsis_stream import Wire
from test_memory_v2_h32 import app_module
from test_memory_v2_a34_responses_wiring import PRICE
from backend.app import memory_v2_synopsis as s,memory_v2_synopsis_replacement as r
from backend.app import memory_v2_jobs as jobs,memory_v2_metadata as m,memory_v2_processing as p

class Chain(unittest.TestCase):
    setUp=supplement.Supplement.setUp
    original_unknown=supplement.Supplement.original_unknown
    approval=supplement.Supplement.approval
    adapter=supplement.Supplement.adapter
    def chain(self):
        root=self.original_unknown()
        with app_module.connect() as db:
            latest=r.create_replacement_trial(db,root['jobId'],self.cid,self.owner,price=PRICE,approval=self.approval(root))
        import httpx
        from test_memory_v2_a34_diagnostics import Wire as OldWire
        oldwire=OldWire()
        def cut(request):
            if request.url.path.endswith('/input_tokens'):return oldwire.handle(request)
            raise httpx.ReadTimeout('synthetic')
        from backend.app import memory_v2_responses_adapter as responses
        a=responses.ResponsesAdapter(model='gpt-6.1-sol',transport=httpx.MockTransport(cut),wait_policy=responses.SLOW_WAIT_POLICY)
        s.prepare_real_trial(app_module.connect,latest['jobId'],self.cid,self.owner,a)
        out=s.run_real_trial(app_module.connect,latest['jobId'],self.cid,self.owner,a)
        a.close();self.assertEqual(out['status'],'unknown')
        with app_module.connect() as db:
            refs=r.chain_references(db,latest['jobId'],self.cid,self.entries[0],self.owner)
            snap=s.confirmed_snapshot(db,self.entries[0],self.cid)
            approval=dict(version=r.CHAIN_EVENT,approvalId='test-chain-once',originalJobId=latest['jobId'],entryId=self.entries[0],
                ownerId=self.owner,sourceToken=snap['sourceToken'],boundaryBinding=s.boundary_binding(db,self.entries[0],self.cid),
                requests=refs,profile=s.REAL_STREAM_PROFILE,validator=s.QUOTE_VALIDATOR,quoteLocatorVersion=s.quotes.VERSION,
                streamVersion=s.streams.VERSION,maxMicrousd=122880,maxGenerations=1,maxCounts=1,
                expiresAtUnix=time.time()+86400,acknowledgeDuplicateCost=True,approved=True)
            budgets=[dict(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(x['trialId'],)).fetchone()) for x in (root,latest)]
        return root,latest,approval,budgets
    def create(self,latest,approval):
        with app_module.connect() as db:return r.create_stream_replacement_trial(db,latest['jobId'],self.cid,self.owner,price=PRICE,approval=approval)
    def execute(self,trial,wire):
        self.addCleanup(wire.adapter.close)
        s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        return s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter,diagnostic_sink=lambda x:None)
    def test_two_unknowns_new_stream_field_and_idempotence(self):
        root,latest,a,budgets=self.chain();trial=self.create(latest,a);wire=Wire()
        out=self.execute(trial,wire);self.assertEqual(out['status'],'completed',out)
        self.assertEqual(self.create(latest,a),trial)
        self.assertTrue(s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)['reused'])
        self.assertEqual(len(wire.generations),1)
        with app_module.connect() as db:
            self.assertEqual(m.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['reviewStatus'],'needs_review')
            for old,b in zip((root,latest),budgets):
                self.assertEqual(dict(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(old['trialId'],)).fetchone()),b)
                with self.assertRaisesRegex(ValueError,'superseded'):s.guard_field(db,self.entries[0],self.cid,except_id=old['jobId'])
    def test_missing_conflicting_chain_or_binding_zero_allowance(self):
        root,latest,a,_=self.chain()
        for change in (dict(a,requests=a['requests'][1:]),dict(a,requests=list(reversed(a['requests']))),dict(a,profile=s.REAL_SPEAKER_PROFILE),dict(a,ownerId=True)):
            with self.assertRaises(ValueError):self.create(latest,change)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0],2)
            db.execute("DELETE FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1'",(root['jobId'],))
        with self.assertRaises(ValueError):self.create(latest,a)
    def test_current_source_change_and_manual_field_protection(self):
        _,latest,a,_=self.chain()
        with app_module.connect() as db:
            draft=m.read_draft(db,self.entries[0],self.cid)
            m.revise(db,self.entries[0],self.cid,draft['versionId'],{'synopsis':'Owner保留的人工梗概。'},self.owner)
        with self.assertRaises(ValueError):self.create(latest,a)
    def test_sources_changed_or_cancelled_prevent_new_attempt(self):
        _,latest,a,_=self.chain()
        with self.assertRaises(ValueError):self.create(latest,dict(a,sourceToken='stale'))
        with app_module.connect() as db:db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(latest['jobId'],))
        with self.assertRaises(ValueError):self.create(latest,a)
    def test_readset_tamper_and_unknown_version_zero_dispatch(self):
        _,latest,a,_=self.chain();trial=self.create(latest,a)
        with app_module.connect() as db:
            row=db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()
            payload=json.loads(row[0]);payload['readSet']['replacement']['requests'][0]['attemptId']='forged'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(payload),trial['jobId']))
        wire=Wire()
        with self.assertRaises(ValueError):s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        self.assertEqual((wire.counts,wire.generations),([],[]));wire.adapter.close()
    def test_other_unknown_and_hold_are_not_ignored(self):
        root,latest,a,_=self.chain()
        with app_module.connect() as db:
            old=dict(db.execute('SELECT * FROM mv3_jobs WHERE id=?',(root['jobId'],)).fetchone());old['id']='unrelated-unknown'
            columns=list(old);db.execute('INSERT INTO mv3_jobs('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[old[k] if k!='input_hash' else 'different' for k in columns])
        with self.assertRaisesRegex(ValueError,'scope_unknown'):self.create(latest,a)
    def test_concurrent_creation_one_task_and_conflicting_approval_rejected(self):
        _,latest,a,_=self.chain()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.create(latest,a),range(2)))
        self.assertEqual(results[0],results[1])
        with self.assertRaises(ValueError):self.create(latest,dict(a,approvalId='another-decision'))
    def test_unknown_keeps_new_and_all_old_reserves(self):
        _,latest,a,budgets=self.chain();trial=self.create(latest,a)
        out=self.execute(trial,Wire(cut=True));self.assertEqual(out['status'],'unknown')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT reserved_microusd FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()[0],122880)
        with self.assertRaises(ValueError):s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,Wire().adapter)
    def test_late_old_proofs_settle_once_never_create_old_output(self):
        proofs=[];persist=jobs.persist_dispatch_identity
        def capture(*args,**kwargs):
            proof=persist(*args,**kwargs);proofs.append(proof);return proof
        with patch.object(jobs,'persist_dispatch_identity',side_effect=capture):root,latest,a,_=self.chain()
        trial=self.create(latest,a)
        for old,proof in zip((root,latest),proofs):
            for _ in range(2):
                with app_module.connect() as db:
                    db.execute('BEGIN IMMEDIATE');jobs.settle_dispatch(db,old['jobId'],self.cid,0,proof.request_id,proof,state='rejected',
                        used_input=20,used_output=20,used_cost=0,result_hash='same-late',trial_id=old['trialId'],usd_estimate=240)
            with app_module.connect() as db:
                self.assertIsNone(db.execute('SELECT output_json FROM mv3_model_chunks WHERE job_id=?',(old['jobId'],)).fetchone()[0])
        self.assertEqual(self.execute(trial,Wire())['status'],'completed')

if __name__=='__main__':unittest.main()
