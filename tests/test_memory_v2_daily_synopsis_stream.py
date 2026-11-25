"""Normal workspace approval/execute route, existing streaming v4, no network."""
import json
import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_memory_v2_a34_responses_wiring as wiring
PRICE=wiring.PRICE
from test_memory_v2_synopsis_stream import Wire
from test_memory_v2_h32 import app_module
from backend.app import memory_v2_synopsis as s, memory_v2_metadata as metadata
from backend.app import memory_v2_chronicle_workspace as workspace

class DailySynopsisStream(unittest.TestCase):
    def setUp(self):
        self.f=wiring.SynopsisResponsesWiring();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.cid=self.f.cid;self.owner=self.f.owner;self.eid=self.f.entries[0]
        self.logs=tempfile.TemporaryDirectory(prefix='yosaki-daily-stream-');self.addCleanup(self.logs.cleanup)
        self.wire=Wire();self.addCleanup(self.wire.adapter.close)
        self.wire.adapter.close=lambda:None
        # Factory starts as the actual normal (not preconfigured streaming) client.
        self.wire.adapter.wait_policy=None;self.wire.adapter.stream_policy=None
        self.api=FastAPI();self.auth={'id':self.owner}
        workspace.install(self.api,app_module.connect,lambda _:self.auth,
            provider_factory=lambda model:(self.wire.adapter,PRICE),
            diagnostic_sink=lambda record:s.write_isolated_diagnostic(self.logs.name,record))
        self.client=TestClient(self.api)
        self.base='/api/memory-v2/workspace'
    def prepare(self,capacity=6144):
        view=self.client.get(f'{self.base}/entries/{self.eid}',params={'characterId':self.cid}).json()
        preview=self.client.get(f'{self.base}/entries/{self.eid}/synopsis-material',params={'characterId':self.cid,'outputTokens':capacity})
        self.assertEqual(preview.status_code,200,preview.text)
        mat=preview.json()
        approved=self.client.post(f'{self.base}/entries/{self.eid}/synopsis-plan',json=dict(characterId=self.cid,
            expectedVersion=view['entry']['versionId'],modelId=1,approveMaxMicrousd=mat['maxMicrousd'],
            generationVersion='stream-v4',outputTokens=capacity,materialHash=mat['scopeHash']))
        self.assertEqual(approved.status_code,200,approved.text)
        return approved.json(),mat
    def execute(self,trial):
        return self.client.post(f"{self.base}/synopsis-jobs/{trial['jobId']}/execute",params={'characterId':self.cid})
    def test_actual_daily_stream_v4_save_read_receipt_and_idempotence(self):
        trial,mat=self.prepare(8192)
        r=self.execute(trial);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['status'],'completed',r.text)
        self.assertEqual(self.wire.generations[0]['max_output_tokens'],8192)
        self.assertEqual(self.wire.adapter.client.timeout.read,180)
        self.assertTrue(self.wire.generations[0]['stream'])
        self.assertFalse(self.wire.generations[0]['store'])
        view=self.client.get(f'{self.base}/entries/{self.eid}',params={'characterId':self.cid}).json()
        self.assertTrue(view['entry']['fields']['synopsis']['value'])
        self.assertEqual(view['entry']['fields']['synopsis']['reviewStatus'],'needs_review')
        self.assertEqual(self.execute(trial).json()['status'],'completed')
        self.assertEqual((len(self.wire.counts),len(self.wire.generations)),(1,1))
        files=list(Path(self.logs.name).glob('stream_*.json'));self.assertGreater(len(files),1)
        records=[json.loads(p.read_text(encoding='utf-8')) for p in files]
        terminal=[x for x in records if x['event']['type']=='response.completed']
        self.assertEqual(len(terminal),1);self.assertTrue(terminal[0]['event']['response']['usage'])
        self.assertNotIn('PRIVATE_REASONING_NOT_RETAINED',str(records))
        with app_module.connect() as db:
            payload=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0])
            self.assertEqual(payload['ruleVersion'],s.REAL_STREAM_8192_PROFILE)
            self.assertEqual(payload['readSet']['validator'],s.QUOTE_VALIDATOR)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0],0)
    def test_unknown_and_cancel_are_visible_no_repeat(self):
        trial,_=self.prepare();self.wire.usage=False
        r=self.execute(trial);self.assertEqual(r.json()['status'],'unknown')
        status=self.client.get(f'{self.base}/entries/{self.eid}/synopsis-status',params={'characterId':self.cid}).json()
        self.assertEqual(status['status'],'unknown');self.assertGreater(status['reserved_microusd'],0)
        self.assertEqual(self.execute(trial).status_code,409);self.assertEqual(len(self.wire.generations),1)
        r=self.client.post(f"{self.base}/synopsis-jobs/{trial['jobId']}/cancel",params={'characterId':self.cid})
        self.assertEqual(r.status_code,200,r.text)
        with app_module.connect() as db:
            self.assertGreater(db.execute('SELECT reserved_microusd FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()[0],0)
    def test_cancel_before_send_zero_requests(self):
        trial,_=self.prepare()
        r=self.client.post(f"{self.base}/synopsis-jobs/{trial['jobId']}/cancel",params={'characterId':self.cid})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.execute(trial).status_code,409)
        self.assertEqual(self.wire.generations,[]);self.assertEqual(self.wire.counts,[])
    def test_invalid_quote_known_cost_and_diagnostic_failure(self):
        trial,_=self.prepare();self.wire.wrong_quote=True
        # Fail only diagnostics; known cost and rejection must survive.
        def fail(record):raise OSError('synthetic diagnostic storage failure')
        app=FastAPI();workspace.install(app,app_module.connect,lambda _:{'id':self.owner},
            provider_factory=lambda model:(self.wire.adapter,PRICE),diagnostic_sink=fail)
        r=TestClient(app).post(f"{self.base}/synopsis-jobs/{trial['jobId']}/execute",params={'characterId':self.cid})
        self.assertEqual(r.json()['status'],'failed',r.text)
        with app_module.connect() as db:
            row=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            self.assertGreater(row['spent_estimate_microusd'],0);self.assertEqual(row['reserved_microusd'],0)
            self.assertIsNone(metadata.read_draft(db,self.eid,self.cid)['fields']['synopsis']['value'])
    def test_material_drift_and_unknown_capacity_zero_send(self):
        with app_module.connect() as db:
            before=db.total_changes;s.preview_chronicle_stream(db,self.eid,self.cid,self.owner,6144)
            self.assertEqual(db.total_changes,before)
        bad=self.client.get(f'{self.base}/entries/{self.eid}/synopsis-material',params={'characterId':self.cid,'outputTokens':10000})
        self.assertEqual(bad.status_code,409);self.assertEqual(self.wire.counts,[])
        trial,mat=self.prepare()
        with app_module.connect() as db:
            row=db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()
            p=json.loads(row[0]);p['readSet']['realConfig']['outputTokenCap']=8192
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(p),trial['jobId']))
        self.assertEqual(self.execute(trial).status_code,409);self.assertEqual(self.wire.counts,[])
    def test_long_complete_material_capacity_stop_before_generation(self):
        # Source package can be long while fixed full-record and token bounds
        # remain enforced. The count covers actual prompt/schema packaging.
        self.wire.handle_original=self.wire.handle
        original=self.wire.handle
        import httpx
        def handle(request):
            if request.url.path.endswith('/input_tokens'):
                self.wire.counts.append(json.loads(request.content))
                return httpx.Response(200,json={'input_tokens':8193})
            return original(request)
        self.wire.adapter.client._transport=httpx.MockTransport(handle)
        trial,_=self.prepare(8192)
        r=self.execute(trial);self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.wire.generations,[])

    def test_24_sources_use_daily_route_and_unique_quote_readback(self):
        self.eid=self.f.entries[6]
        trial,mat=self.prepare(8192)
        self.assertEqual(mat['sourceCount'],24)
        result=self.execute(trial);self.assertEqual(result.json()['status'],'completed',result.text)
        actual=json.loads(self.wire.generations[0]['input'][0]['content'])
        self.assertEqual(len(actual['sources']),24)
        with app_module.connect() as db:
            row=db.execute('SELECT output_json,warning_json FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            out=json.loads(row['output_json']);self.assertEqual(len(out['sourceCoverage']),24)
            self.assertTrue(all(e['end']>e['start'] for u in out['units'] for e in u['evidence']))
