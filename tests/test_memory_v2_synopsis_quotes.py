"""Self-contained exact quote protocol and real coordinator Mock regressions."""
import copy,json,unittest
from unittest.mock import patch
import httpx
import test_memory_v2_a34_diagnostics as diagnostics
from test_memory_v2_h32 import app_module
from test_memory_v2_a34_responses_wiring import FakeWire,MODEL,PRICE
from backend.app import memory_v2_synopsis as s,memory_v2_synopsis_quotes as q
from backend.app import memory_v2_responses_adapter as r,memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_contract as contract

class Quotes(unittest.TestCase):
    def source(self,text):return dict(kind='message',id='a',speaker='user',text=text,sourceStatus='imported')
    def wire(self,quote):
        return {'text':'她提供原话。','units':[{'start':0,'end':7,'evidence':[dict(kind='message',id='a',speaker='user',quote=quote)]}],
            'sourceCoverage':[dict(kind='message',id='a',used=True)]}
    def test_codepoints_punctuation_parentheses_real_newlines_and_combining(self):
        for raw,quote,start in [('前（甲。乙）后','（甲。乙）',1),('甲\n\n乙','甲\n\n乙',0),
                ('😀e\u0301中文。','e\u0301中文。',1),('ＡＰＩ，原样。','ＡＰＩ，原样。',0)]:
            with self.subTest(raw=raw):
                result,adjust=q.resolve(self.wire(quote),[self.source(raw)])
                e=result['units'][0]['evidence'][0]
                self.assertEqual((e['start'],e['end']),(start,start+len(quote)))
                self.assertEqual(raw[e['start']:e['end']],quote)
                self.assertEqual(q.checked_artifact(result,q.artifact(self.wire(quote),result,adjust),[self.source(raw)]),result)
    def test_repeated_overlapping_and_empty_quotes_rejected(self):
        for raw,quote,reason in [('甲甲甲','甲甲','ambiguous'),('同文。同文。','同文。','ambiguous'),('甲','','invalid')]:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError,reason):q.resolve(self.wire(quote),[self.source(raw)])
    def test_no_nfkc_trim_escape_or_nearby_search(self):
        for raw,quote in [('ＡＰＩ','API'),('甲\n乙',r'甲\n乙'),('甲，不执行。','甲，执行。'),('甲。','甲！')]:
            with self.assertRaisesRegex(ValueError,'not_found'):q.resolve(self.wire(quote),[self.source(raw)])
    def test_identity_and_model_supplied_offsets_rejected(self):
        for key,value in [('speaker','assistant'),('id','other'),('start',0)]:
            out=self.wire('甲');out['units'][0]['evidence'][0][key]=value
            with self.assertRaises(ValueError):q.resolve(out,[self.source('甲')])
    def test_explicit_legacy_repair_unique_only_and_body_unchanged(self):
        original=self.wire('（甲）');original['units'][0]['evidence'][0].update(start=0,end=2)
        fixed,changes=q.repair_legacy(original,[self.source('前（甲）后')])
        self.assertEqual(changes[0]['after'],[1,4]);self.assertEqual(fixed['text'],original['text'])
        self.assertEqual(original['units'][0]['evidence'][0]['start'],0)
        with self.assertRaisesRegex(ValueError,'ambiguous'):q.repair_legacy(original,[self.source('（甲）（甲）')])
    def test_legacy_newline_conversion_opt_in_and_no_general_unescape(self):
        out=self.wire(r'甲\n乙');out['units'][0]['evidence'][0].update(start=0,end=9)
        with self.assertRaisesRegex(ValueError,'not_found'):q.repair_legacy(out,[self.source('甲\n乙')])
        repaired,changes=q.repair_legacy(out,[self.source('甲\n乙')],allow_escaped_newlines=True)
        self.assertEqual(repaired['units'][0]['evidence'][0]['quote'],'甲\n乙')
        self.assertTrue(changes[0]['quoteChanged'])
        out['units'][0]['evidence'][0]['quote']=r'甲\t乙'
        with self.assertRaisesRegex(ValueError,'not_found'):q.repair_legacy(out,[self.source('甲\t乙')],allow_escaped_newlines=True)
    def test_artifact_tamper_rejected(self):
        wire=self.wire('甲');sources=[self.source('甲')];out,changes=q.resolve(wire,sources);record=q.artifact(wire,out,changes)
        record['adjustments'][0]['end']=2
        with self.assertRaisesRegex(ValueError,'binding'):q.checked_artifact(out,record,sources)

class QuoteBusiness(unittest.TestCase):
    setUp=diagnostics.A34DiagnosticRetention.setUp
    def adapter(self,wire):
        original=wire.handle
        def handler(request):
            response=original(request)
            if request.url.path.endswith('/input_tokens') or wire.missing_usage:return response
            data=response.json()
            if data.get('status')=='completed' and not wire.bad_json and not wire.refusal:
                output=json.loads(data['output'][0]['content'][0]['text'])
                for unit in output['units']:
                    for e in unit['evidence']:
                        e.pop('start');e.pop('end')
                data['output'][0]['content'][0]['text']=json.dumps(output,ensure_ascii=False)
            return httpx.Response(200,json=data)
        wire.adapter.close();wire.adapter=r.ResponsesAdapter(model=MODEL,transport=httpx.MockTransport(handler),wait_policy=r.SLOW_WAIT_POLICY)
        self.addCleanup(wire.adapter.close);return wire.adapter
    def create(self):
        with app_module.connect() as db:return s.create_real_trial(db,self.entries[0],self.cid,self.owner,model=MODEL,price=PRICE,
            profile=s.REAL_QUOTE_PROFILE,batch_input_cap=12000,output_cap=6144,max_microusd=122880)
    def run_trial(self,wire):
        trial=self.create();adapter=self.adapter(wire)
        s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
        return trial,s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter,diagnostic_sink=lambda r:None)
    def test_actual_request_to_saved_field_read_and_idempotence(self):
        wire=FakeWire();trial,result=self.run_trial(wire);self.assertEqual(result['status'],'completed',result)
        req=wire.generations[0];schema=req['text']['format']['schema']
        self.assertNotIn('start',schema['properties']['units']['items']['properties']['evidence']['items']['properties'])
        with app_module.connect() as db:
            field=metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis'];self.assertTrue(field['value'])
            self.assertEqual(field['reviewStatus'],'needs_review')
            row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            self.assertTrue(json.loads(row['warning_json'])['rawHash']);self.assertTrue(row['settlement_hash'])
            payload=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0])
            self.assertEqual(payload['readSet']['quoteLocatorVersion'],q.VERSION)
        retry=s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        self.assertTrue(retry['reused']);self.assertEqual(len(wire.generations),1)
    def test_unknown_policy_zero_counts(self):
        trial=self.create();wire=FakeWire();adapter=self.adapter(wire)
        with app_module.connect() as db:
            p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0]);p['readSet']['quoteLocatorVersion']='unknown'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(p),trial['jobId']))
        with self.assertRaisesRegex(ValueError,'quote_version'):s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
        self.assertEqual(wire.counts,[])
    def test_missing_usage_keeps_unknown_and_reserve(self):
        trial,result=self.run_trial(FakeWire(missing_usage=True));self.assertEqual(result['status'],'unknown')
        with app_module.connect() as db:
            budget=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            self.assertGreater(budget['reserved_microusd'],0)
            self.assertIsNone(metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['value'])
    def test_persistent_readset_column_drift_zero_send(self):
        trial=self.create();wire=FakeWire();adapter=self.adapter(wire)
        with app_module.connect() as db:
            value=json.loads(db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0])
            value['quoteLocatorVersion']='other'
            db.execute('UPDATE mv3_jobs SET read_set_json=? WHERE id=?',(json.dumps(value),trial['jobId']))
        with self.assertRaisesRegex(ValueError,'quote_task_binding'):s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
        self.assertEqual(wire.counts,[]);self.assertEqual(wire.generations,[])
    def test_strategy_changed_during_generation_settles_but_rejects(self):
        trial=self.create()
        def mutate(request):
            with app_module.connect() as db:
                p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0])
                p['readSet']['quoteLocatorVersion']='unknown'
                db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(p),trial['jobId']))
        wire=FakeWire(on_generation=mutate);adapter=self.adapter(wire)
        s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
        result=s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter,diagnostic_sink=lambda r:None)
        self.assertEqual(result['status'],'failed')
        with app_module.connect() as db:
            budget=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            self.assertGreater(budget['spent_estimate_microusd'],0);self.assertEqual(budget['reserved_microusd'],0)
            self.assertIsNone(metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['value'])
    def test_wrong_quote_rejected_but_usage_settled(self):
        with patch.object(q,'resolve',side_effect=ValueError('synopsis_quote_not_found')):
            trial,result=self.run_trial(FakeWire())
        self.assertEqual(result['status'],'failed')
        with app_module.connect() as db:
            row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            budget=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            self.assertEqual(row['state'],'rejected');self.assertIsNone(row['output_json'])
            self.assertGreater(budget['spent_estimate_microusd'],0);self.assertEqual(budget['reserved_microusd'],0)
    def test_read_dependency_tamper_no_old_body_return(self):
        trial,result=self.run_trial(FakeWire());self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            detail=metadata.read_draft(db,self.entries[0],self.cid);f=metadata._fields(db,detail['versionId'])['synopsis']
            deps=json.loads(f['dependencies_json']);deps['synopsisQuoteLocator']='unknown'
            token=contract.token(['synopsis',json.loads(f['value_json']),f['provenance'],f['rule_version'],json.loads(f['evidence_json']),deps])
            db.execute('UPDATE mv3_entry_fields SET dependencies_json=?,token=? WHERE id=?',(json.dumps(deps),token,f['id']))
            field=metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']
            self.assertEqual(field['lifecycleStatus'],'stale');self.assertIsNone(field['value'])
    def test_legacy_prompt_and_schema_unchanged(self):
        actual={'phase':'batch','sources':[]}
        old=r.synopsis_request_payload(MODEL,'batch',actual,6144,profile=s.REAL_SPEAKER_PROFILE)
        new=r.synopsis_request_payload(MODEL,'batch',actual,6144,profile=s.REAL_QUOTE_PROFILE)
        self.assertIn('start',old['text']['format']['schema']['properties']['units']['items']['properties']['evidence']['items']['properties'])
        self.assertNotIn('本版本的 evidence',old['instructions'])
        self.assertNotEqual(old['instructions'],new['instructions'])
