"""Current product profile and old-contract isolation, no external calls."""
import json
import unittest
import test_memory_v2_a34_responses_wiring as wiring
from backend.app import memory_v2_synopsis as synopsis
from backend.app import memory_v2_metadata as metadata

class ChronicleGeneration(unittest.TestCase):
    def setUp(self):
        self.fixture=wiring.SynopsisResponsesWiring('test_single_batch_request_and_usd_ledger')
        self.fixture.setUp()
    def tearDown(self): self.fixture.tearDown()
    def create(self):
        with wiring.app_module.connect() as db:
            return synopsis.create_chronicle_trial(db,self.fixture.entries[0],self.fixture.cid,self.fixture.owner,price=wiring.PRICE)
    def test_actual_count_dispatch_save_read_and_replay(self):
        planned=self.create(); wire=wiring.FakeWire()
        synopsis.prepare_real_trial(wiring.app_module.connect,planned['jobId'],self.fixture.cid,self.fixture.owner,wire.adapter)
        outcome=synopsis.run_real_trial(wiring.app_module.connect,planned['jobId'],self.fixture.cid,self.fixture.owner,wire.adapter)
        self.assertEqual(outcome['status'],'completed')
        self.assertEqual((len(wire.counts),len(wire.generations)),(1,1))
        self.assertEqual(wire.generations[0]['max_output_tokens'],6144)
        with wiring.app_module.connect() as db:
            d=metadata.read_draft(db,self.fixture.entries[0],self.fixture.cid)
            self.assertNotEqual(d['fields']['synopsis']['lifecycleStatus'],'stale')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0],0)
        synopsis.run_real_trial(wiring.app_module.connect,planned['jobId'],self.fixture.cid,self.fixture.owner,wire.adapter)
        self.assertEqual(len(wire.generations),1)
    def test_unknown_retains_only_request_reserve(self):
        planned=self.create();wire=wiring.FakeWire(missing_usage=True)
        synopsis.prepare_real_trial(wiring.app_module.connect,planned['jobId'],self.fixture.cid,self.fixture.owner,wire.adapter)
        result=synopsis.run_real_trial(wiring.app_module.connect,planned['jobId'],self.fixture.cid,self.fixture.owner,wire.adapter)
        self.assertEqual(result['status'],'unknown')
        with wiring.app_module.connect() as db:
            t=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(planned['trialId'],)).fetchone()
            self.assertGreater(t['reserved_microusd'],0)
            self.assertEqual(t['spent_estimate_microusd'],0)
    def test_config_tamper_zero_count(self):
        planned=self.create();wire=wiring.FakeWire()
        with wiring.app_module.connect() as db:
            p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(planned['jobId'],)).fetchone()[0])
            p['readSet']['realConfig']['inputTokenCap']=99999
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(p),planned['jobId']))
        with self.assertRaises(ValueError): synopsis.prepare_real_trial(wiring.app_module.connect,planned['jobId'],self.fixture.cid,self.fixture.owner,wire.adapter)
        self.assertEqual(wire.counts,[])

    def test_workspace_actual_plan_execute_and_disabled_default(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_chronicle_workspace as workspace
        wire=wiring.FakeWire()
        wire.adapter.close=lambda:None
        app=FastAPI()
        workspace.install(app,wiring.app_module.connect,lambda request:{'id':self.fixture.owner},
            provider_factory=lambda model_id:(wire.adapter,wiring.PRICE))
        client=TestClient(app)
        with wiring.app_module.connect() as db:
            d=metadata.read_draft(db,self.fixture.entries[0],self.fixture.cid)
        planned=client.post('/api/memory-v2/workspace/entries/'+d['entryId']+'/synopsis-plan',json={
            'characterId':self.fixture.cid,'expectedVersion':d['versionId'],'modelId':1,'approveMaxMicrousd':122880})
        self.assertEqual(planned.status_code,200,planned.text)
        path='/api/memory-v2/workspace/synopsis-jobs/'+planned.json()['jobId']+'/execute?characterId='+str(self.fixture.cid)
        outcome=client.post(path)
        self.assertEqual(outcome.status_code,200,outcome.text)
        self.assertEqual(outcome.json()['status'],'completed')
        self.assertEqual(client.post(path).json()['status'],'completed')
        self.assertEqual(len(wire.generations),1)
        disabled=FastAPI();workspace.install(disabled,wiring.app_module.connect,lambda request:{'id':self.fixture.owner})
        self.assertFalse(TestClient(disabled).get('/api/memory-v2/workspace/generation-status').json()['enabled'])

    def test_new_review_policy_keeps_structure_and_old_behavior(self):
        sources=[{'kind':'message','id':'m','speaker':'user','text':'尚未获准。另一件事是白色外壳。','sourceStatus':'ready'}]
        text='白色外壳。'
        out={'text':text,'units':[{'start':0,'end':len(text),'evidence':[{'kind':'message','id':'m','speaker':'user','start':0,'end':len(sources[0]['text']),'quote':sources[0]['text']}]}],'sourceCoverage':[{'kind':'message','id':'m','used':True}]}
        with self.assertRaises(ValueError): synopsis.validate_output(out,sources)
        checked=synopsis.validate_output(out,sources,qualifier_policy=synopsis.CHRONICLE_REVIEW_POLICY)
        self.assertTrue(synopsis.lexical_warnings(checked,sources))
        out['units'][0]['evidence'][0]['speaker']='assistant'
        with self.assertRaisesRegex(ValueError,'evidence_invalid'):
            synopsis.validate_output(out,sources,qualifier_policy=synopsis.CHRONICLE_REVIEW_POLICY)
        planned=self.create()
        with wiring.app_module.connect() as db:
            job,payload=synopsis._real_job(db,planned['jobId'],self.fixture.cid)
            self.assertEqual(payload['ruleVersion'],synopsis.REAL_CHRONICLE_REVIEW_PROFILE)
            self.assertEqual(payload['readSet']['validator'],synopsis.CHRONICLE_REVIEW_VALIDATOR)
            self.assertEqual(payload['readSet']['qualifierVersion'],synopsis.CHRONICLE_REVIEW_POLICY)
            payload['readSet']['qualifierVersion']='unknown'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(payload),job['id']))
        wire=wiring.FakeWire()
        with self.assertRaises(ValueError): synopsis.prepare_real_trial(wiring.app_module.connect,job['id'],self.fixture.cid,self.fixture.owner,wire.adapter)
        self.assertEqual(wire.counts,[])
