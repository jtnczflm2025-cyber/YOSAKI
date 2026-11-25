import json,unittest,httpx
from unittest.mock import patch
import test_memory_v2_a36_temporal_digest as fixture
NOW,app_module,m3=fixture.NOW,fixture.app_module,fixture.m3
from backend.app import memory_v2_chronicle_digest as real,memory_v2_m36_trial as trials,memory_v2_responses_adapter as responses
from test_memory_v2_a34_responses_wiring import PRICE
class RealDigest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.TemporalDigestFlow('test_raw_digest_requires_review_and_preserves_exact_sources');self.fixture.setUp();self.cid=self.fixture.cid
        with app_module.connect() as db:trials.init_db(db)
    def tearDown(self):self.fixture.doCleanups()
    def run_case(self,missing=False,revoked=False,output_failure=False,via_maintenance=False):
        if via_maintenance:
            from backend.app import memory_v2_chronicle_processing as upkeep
            from backend.app import memory_v2_chronicle_schema as schema
            with app_module.connect() as db:
                schema.install(db)
            with patch.object(upkeep, 'datetime') as clock:
                clock.now.return_value = NOW
                self.assertEqual(upkeep.maintain(app_module.connect,self.cid,1)['providerRequests'],0)
        with app_module.connect() as db:p=real.create(db,self.cid,self.fixture.node.id,1,now=NOW,price=PRICE)
        if output_failure:
            with app_module.connect() as db:db.execute("CREATE TRIGGER test_digest_output_failure BEFORE UPDATE OF output_json ON mv3_model_chunks WHEN NEW.output_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        calls=[]
        def wire(req):
            calls.append(req.url.path);payload=json.loads(req.content)
            if req.url.path.endswith('input_tokens'):return httpx.Response(200,json={'input_tokens':80})
            original=json.loads(payload['input'][0]['content'])
            if revoked:
                with app_module.connect() as db:db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
            output={'items':[{'inputId':i['inputId'],'text':'イキヤ说累了，阿夜建议休息。'} for i in original['inputs']]}
            response={'id':'synthetic_digest','model':real.MODEL,'service_tier':'default','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(output)}]}]}
            if not missing:response['usage']={'input_tokens':80,'output_tokens':40}
            return httpx.Response(200,json=response)
        adapter=responses.ResponsesAdapter(model=real.MODEL,transport=httpx.MockTransport(wire))
        if via_maintenance:
            with patch.object(upkeep, 'datetime') as clock:
                clock.now.return_value = NOW
                result = upkeep.maintain(app_module.connect,self.cid,1,
                    approved_period_job=p['jobId'],period_adapter=adapter)['periodResult']
                with self.assertRaisesRegex(ValueError,'period_maintenance_not_needed'):
                    upkeep.maintain(app_module.connect,self.cid,1,
                        approved_period_job=p['jobId'],period_adapter=adapter)
        else:
            result=real.run(app_module.connect,p['jobId'],self.cid,1,adapter)
        return p,result,adapter,calls

    def test_maintenance_dispatches_only_explicit_one_shot_period_job(self):
        _,result,adapter,calls=self.run_case(via_maintenance=True)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(calls,['/v1/responses/input_tokens','/v1/responses'])
        adapter.close()

    def test_workspace_picks_up_approved_period_once_after_restart(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_chronicle_processing as upkeep
        from backend.app import memory_v2_chronicle_schema as schema, memory_v2_chronicle_workspace as workspace
        from backend.app import memory_v2_metadata as metadata
        with app_module.connect() as db:
            schema.install(db)
        calls = []
        def wire(req):
            calls.append(req.url.path)
            if req.url.path.endswith('input_tokens'):
                return httpx.Response(200, json={'input_tokens': 80})
            inputs = json.loads(json.loads(req.content)['input'][0]['content'])['inputs']
            items = [{'inputId': x['inputId'], 'text': 'イキヤ说累了，阿夜建议休息。'} for x in inputs]
            return httpx.Response(200,json={'id':'mock-maintenance','model':real.MODEL,'service_tier':'default',
                'status':'completed','usage':{'input_tokens':80,'output_tokens':40},
                'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'items':items})}]}]})
        def build():
            api = FastAPI()
            workspace.install(api, app_module.connect, lambda _: {'id':1}, provider_factory=lambda _: (
                responses.ResponsesAdapter(model=real.MODEL,transport=httpx.MockTransport(wire)), PRICE))
            return api
        base = '/api/memory-v2/workspace'
        args = {'characterId':self.cid,'nodeId':self.fixture.node.id}
        payload = {**args,'modelId':1,'asOf':NOW.isoformat(),'approveMaxMicrousd':122880,'queueForMaintenance':True}
        with patch.object(upkeep, 'datetime') as clock:
            clock.now.return_value = NOW
            client = TestClient(build())
            self.assertEqual(client.get(base+'/periods/task',params=args).json()['status'],'approval_required')
            self.assertEqual(client.post(base+'/periods/plan', json={**payload,'approveMaxMicrousd':1}).json()['detail'],'period_budget_insufficient')
            self.assertEqual(client.post(base+'/periods/plan',json=payload).status_code,200)
            self.assertEqual(client.get(base+'/periods/task',params=args).json()['status'],'queued')
            self.assertEqual(calls,[])
            with TestClient(build()) as restarted:
                result = restarted.get(base+'/periods/task',params=args).json()
                self.assertEqual(result['status'],'completed',result)
                draft = restarted.get(base+'/period-versions/'+result['versionId'],params={'characterId':self.cid}).json()
                self.assertEqual(draft['status'],'needs_review')
            with TestClient(build()) as restarted:
                self.assertEqual(restarted.post(base+'/maintain',params=args).status_code,200)
                self.assertEqual(restarted.get(base+'/periods/task',params=args).json()['versionId'],result['versionId'])
                version_path = base+'/period-versions/'+result['versionId']
                decision = {'characterId':self.cid,'inputHash':draft['input_hash'],'outputHash':draft['output_hash']}
                self.assertEqual(restarted.post(version_path+'/review',json=decision).json()['review_status'],'human_checked')
                self.assertEqual(restarted.post(version_path+'/activate',json=decision).json()['status'],'active')
                with app_module.connect() as db:
                    entry = db.execute('SELECT active_version_id FROM mv3_entries WHERE character_id=?',(self.cid,)).fetchone()[0]
                    title = metadata._fields(db,entry)['title']
                    db.execute('UPDATE messages SET content=? WHERE id=?',('更正后的合成来源',self.fixture.user))
                    self.fixture._index(db)
                self.assertEqual(restarted.post(base+'/maintain',params=args).status_code,200)
                stale = restarted.get(base+'/periods/task',params=args).json()
                self.assertEqual((stale['status'],stale['versionId']),('stale',None))
                self.assertEqual(restarted.get(version_path,params={'characterId':self.cid}).status_code,409)
                with app_module.connect() as db:
                    preserved = db.execute('SELECT value_json FROM mv3_entry_fields WHERE id=?',(title['id'],)).fetchone()
                    self.assertEqual(title['value_json'],preserved['value_json'])
        self.assertEqual(calls,['/v1/responses/input_tokens','/v1/responses'])
    def test_real_protocol_m3_binding_and_idempotence(self):
        p,r,a,calls=self.run_case();self.assertEqual(r['status'],'completed',r)
        with app_module.connect() as db:
            v=m3._row(db,'mv3_versions',r['versionId']);self.assertEqual(v['model_provider'],'openai_responses');self.assertEqual(v['status'],'needs_review');self.assertIsNone(m3._validity(db,v['id'],self.cid,set(),draft=True))
            self.assertEqual(db.execute('SELECT reserved_microusd FROM mv3_trial_budgets WHERE id=?',(p['trialId'],)).fetchone()[0],0)
        self.assertTrue(real.run(app_module.connect,p['jobId'],self.cid,1,a)['reused']);self.assertEqual(len(calls),2)
    def test_missing_usage_keeps_reservation(self):
        p,r,a,c=self.run_case(missing=True);self.assertEqual(r['status'],'unknown')
        with app_module.connect() as db:
            t=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(p['trialId'],)).fetchone();self.assertGreater(t['reserved_microusd'],0);self.assertEqual(t['spent_estimate_microusd'],0)
            from backend.app.memory_v2_chronicle_processing import reconcile_period_plans
            self.assertEqual(reconcile_period_plans(db,self.cid,1,now=NOW)[1],[])
            self.assertEqual(dict(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(p['trialId'],)).fetchone()),dict(t))

    def test_period_maintenance_retires_undispatched_plan_after_source_change(self):
        from backend.app.memory_v2_chronicle_processing import reconcile_period_plans
        with app_module.connect() as db:
            plan=real.create(db,self.cid,self.fixture.node.id,1,now=NOW,price=PRICE)
        with app_module.connect() as db:
            self.assertEqual(reconcile_period_plans(db,self.cid,1,now=NOW)[1],[])
            db.execute('UPDATE messages SET content=? WHERE id=?',('更正后的合成来源',self.fixture.user))
            self.fixture._index(db)
        with app_module.connect() as db:
            self.assertEqual(reconcile_period_plans(db,self.cid,1,now=NOW)[1],[plan['jobId']])
            trial=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(plan['trialId'],)).fetchone()
            self.assertEqual((trial['status'],trial['dispatched_requests'],trial['reserved_microusd']),('stopped',0,0))
            self.assertFalse(db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?',(plan['jobId'],)).fetchone())
            self.assertEqual(reconcile_period_plans(db,self.cid,1,now=NOW)[1],[])
    def test_revocation_settles_without_version(self):
        p,r,a,c=self.run_case(revoked=True);self.assertEqual(r['status'],'rejected')
        with app_module.connect() as db:
            t=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(p['trialId'],)).fetchone();self.assertGreater(t['spent_estimate_microusd'],0);self.assertEqual(t['reserved_microusd'],0)
            self.assertIsNone(db.execute('SELECT result_version_id FROM mv3_jobs WHERE id=?',(p['jobId'],)).fetchone()[0])

    def test_output_write_failure_settles_and_never_replays(self):
        p,r,a,c=self.run_case(output_failure=True)
        self.assertEqual(r['status'],'rejected')
        with app_module.connect() as db:
            t=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(p['trialId'],)).fetchone()
            self.assertGreater(t['spent_estimate_microusd'],0);self.assertEqual(t['reserved_microusd'],0)
            self.assertIsNone(db.execute('SELECT result_version_id FROM mv3_jobs WHERE id=?',(p['jobId'],)).fetchone()[0])
        with self.assertRaises(ValueError):real.run(app_module.connect,p['jobId'],self.cid,1,a)
        self.assertEqual(len(c),2)
