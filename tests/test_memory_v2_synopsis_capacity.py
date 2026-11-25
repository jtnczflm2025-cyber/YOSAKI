"""Versioned8192 output capacity; synthetic A3/three-history-chain only."""
import json,copy,time,unittest
from unittest.mock import patch
import test_memory_v2_synopsis_chain as chain
from test_memory_v2_synopsis_stream import Wire
from test_memory_v2_a34_responses_wiring import PRICE,MODEL
from test_memory_v2_h32 import app_module
from backend.app import memory_v2_synopsis as s,memory_v2_synopsis_replacement as r
from backend.app import memory_v2_responses_adapter as responses,memory_v2_metadata as m

class Capacity(unittest.TestCase):
    def setUp(self):
        self.fixture=chain.Chain('test_two_unknowns_new_stream_field_and_idempotence');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.cid=self.fixture.cid;self.owner=self.fixture.owner;self.entry=self.fixture.entries[0]
    def direct(self):
        with app_module.connect() as db:return s.create_real_trial(db,self.entry,self.cid,self.owner,model=MODEL,price=PRICE,
            profile=s.REAL_STREAM_8192_PROFILE,batch_input_cap=12000,output_cap=8192,max_microusd=147456)
    def execute(self,trial,wire):
        self.addCleanup(wire.adapter.close);s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        return s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter,diagnostic_sink=lambda x:None)
    def test_actual_request_budget_persistence_and_read_binding(self):
        wire=Wire();trial=self.direct();out=self.execute(trial,wire);self.assertEqual(out['status'],'completed',out)
        self.assertEqual(wire.generations[0]['max_output_tokens'],8192)
        with app_module.connect() as db:
            job,p=s._real_job(db,trial['jobId'],self.cid);self.assertEqual(p['readSet']['capacityVersion'],s.STREAM_8192_CAPACITY_VERSION)
            self.assertEqual(p['readSet']['realConfig']['inputTokenCap'],8192)
            self.assertEqual(p['readSet']['realConfig']['partCharsCap'],24000)
            c=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            self.assertEqual(c['reserved_usd_micros'],147456)
            field=m.read_draft(db,self.entry,self.cid)['fields']['synopsis'];self.assertTrue(field['value']);self.assertEqual(field['reviewStatus'],'needs_review')
            stored=db.execute('SELECT * FROM mv3_entry_fields WHERE id=?',(field['fieldId'],)).fetchone()
            deps=json.loads(stored['dependencies_json']);self.assertEqual(deps['synopsisCapacityVersion'],s.STREAM_8192_CAPACITY_VERSION)
            evidence=json.loads(stored['evidence_json']);evidence[0].pop('capacityVersion')
            db.execute('UPDATE mv3_entry_fields SET evidence_json=? WHERE id=?',(json.dumps(evidence),field['fieldId']))
            self.assertIsNone(m.read_draft(db,self.entry,self.cid)['fields']['synopsis']['value'])
    def test_old_request_unchanged_new_only_changes_max_output(self):
        with app_module.connect() as db:
            snap=s.confirmed_snapshot(db,self.entry,self.cid);sources=s.input_sources(db,self.cid,snap)
        actual=s._batch_actual(sources,0,snap['sourceToken'])
        old=responses.synopsis_request_payload(MODEL,'batch',actual,6144,profile=s.REAL_STREAM_PROFILE)
        new=responses.synopsis_request_payload(MODEL,'batch',actual,8192,profile=s.REAL_STREAM_8192_PROFILE)
        self.assertEqual(dict(old,max_output_tokens=8192),new)
        for profile,cap in ((s.REAL_STREAM_PROFILE,8192),(s.REAL_STREAM_8192_PROFILE,6144),(s.REAL_STREAM_8192_PROFILE,True)):
            with self.assertRaises(ValueError):responses.synopsis_request_payload(MODEL,'batch',actual,cap,profile=profile)
    def test_capacity_readset_tamper_zero_count(self):
        trial=self.direct();wire=Wire();self.addCleanup(wire.adapter.close)
        with app_module.connect() as db:
            p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0]);p['readSet']['capacityVersion']='unknown'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(p),trial['jobId']))
        with self.assertRaises(ValueError):s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        self.assertEqual(wire.counts,[])
    def test_three_old_requests_and_incomplete_settlement_new_once(self):
        f=self.fixture;_,oldlatest,a,_=f.chain();middle=f.create(oldlatest,a)
        out=f.execute(middle,Wire(terminal='incomplete'));self.assertEqual(out['status'],'failed')
        with app_module.connect() as db:
            refs=r.chain_references(db,middle['jobId'],self.cid,self.entry,self.owner);self.assertEqual(len(refs),3)
            snap=s.confirmed_snapshot(db,self.entry,self.cid)
            approval=dict(a,version=r.CHAIN_8192_EVENT,approvalId='capacity-one-human-decision',originalJobId=middle['jobId'],requests=refs,
                sourceToken=snap['sourceToken'],profile=s.REAL_STREAM_8192_PROFILE,maxMicrousd=147456,
                capacityVersion=s.STREAM_8192_CAPACITY_VERSION,inputTokenCap=8192,outputTokenCap=8192)
        with app_module.connect() as db:trial=r.create_stream_replacement_trial(db,middle['jobId'],self.cid,self.owner,price=PRICE,approval=approval)
        self.assertEqual(self.execute(trial,Wire())['status'],'completed')
        with app_module.connect() as db:
            self.assertEqual(r.create_stream_replacement_trial(db,middle['jobId'],self.cid,self.owner,price=PRICE,approval=approval),trial)
            for ref in refs:
                self.assertTrue(r.may_ignore_original(db,trial['jobId'],ref['jobId']))
                with self.assertRaisesRegex(ValueError,'superseded'):s.guard_field(db,self.entry,self.cid,except_id=ref['jobId'])
            self.assertTrue(m.read_draft(db,self.entry,self.cid)['fields']['synopsis']['value'])
    def test_8192_incomplete_and_missing_usage_keep_correct_money(self):
        trial=self.direct();out=self.execute(trial,Wire(terminal='incomplete'));self.assertEqual(out['status'],'failed')
        with app_module.connect() as db:
            b=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            self.assertEqual(b['spent_estimate_microusd'],1400);self.assertEqual(b['reserved_microusd'],0)
            self.assertIsNone(m.read_draft(db,self.entry,self.cid)['fields']['synopsis']['value'])
    def test_json_body_limits_not_raised_with_provider_cap(self):
        self.assertEqual(s.MAX_OUTPUT_CHARS,3000);self.assertEqual(s._real_part_cap(s.REAL_STREAM_8192_PROFILE),24000)
        self.assertEqual(s.quotes.ARTIFACT_CAP,60000);self.assertGreater(s.streams.MAX_PUBLIC_CHARS,32768)
        self.assertEqual(responses.synopsis_quote_microusd(8192,8192,'2','10'),147456)

if __name__=='__main__':unittest.main()
