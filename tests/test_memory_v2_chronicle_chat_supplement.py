"""Real chat entrypoint with synthetic data and offline provider transports."""
import json
import os
import time
from datetime import datetime, timezone
from threading import Event
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_chronicle_curation as curation
from backend.app import memory_v2_chronicle_embedding as embedding
from backend.app import memory_v2_chronicle_schema as schema
from backend.app import memory_v2_chronicle_semantic as semantic
from backend.app import memory_v2_chronicle_vectors as vectors
from backend.app import memory_v2_chronicle_workspace as workspace
from backend.app import memory_v2_dialogue_refinement as finite
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_m36_trial as trials
from backend.app import memory_v2_metadata as metadata


class ChronicleChatSupplement(EntryBase):
    query = '两道关都过了，是直接动工还是只把施工这事拿出来商量？'
    unrelated = '面包烤好后，放在架上晾几分钟才不烫手？'
    synopsis = '馆方批准且夜间测试通过后才讨论是否安装，目前暂缓。'
    raw = '只供本地核对的原始首轮，不能送出。'
    vector = [1.0] + [0.0]*1535

    def setUp(self):
        super().setUp()
        self.chat = []
        self.calls = []
        self.mode = 'match'
        self.late_stage_finished = Event()
        self.chat_started_before_late_stage = None
        with app_module.connect() as db:
            schema.install(db)
            db.execute('UPDATE messages SET content=? WHERE id=?', (self.raw, self.user))
            self._index(db)
            draft = metadata.create_draft(db,self.cid,self.refs(),'银叶馆蓝灯指示牌',1,'chat-supplement')
            self.entry = draft['entryId']
            v = finite.create(db,self.cid,1,self.entry,draft['versionId'])
            workspace.confirm_narrative(db,self.entry,workspace.Confirm(characterId=self.cid,
                expectedVersion=draft['versionId'],versionId=v['id'],inputHash=v['input_hash'],
                outputHash=v['output_hash']),1)
            draft = metadata.read_draft(db,self.entry,self.cid)
            draft = metadata.revise(db,self.entry,self.cid,draft['versionId'],{'synopsis':self.synopsis},1)
            for field in ('title','synopsis'):
                metadata.review_field(db,self.entry,self.cid,field,draft['fields'][field]['token'],1)
            metadata.publish(db,self.entry,self.cid,draft['versionId'],1)
            _, fingerprint = vectors._input(db, self.cid, self.entry)
            vectors.store(db, self.cid, 1, self.entry, embedding.MODEL, fingerprint, self.vector)
        self.client = TestClient(app_module.app)
        self.owner = {'id':1,'username':'synthetic'}
        self.start_patch(patch.object(app_module,'current_session',return_value=self.owner))
        model = self.client.post('/api/models', json={'name':'C6 test','base_url':'https://api.openai.com/v1',
            'api_key':'local-test','model_name':semantic.MODEL,'protocol':'openai_responses',
            'max_tokens':512,'reasoning_effort':'medium'})
        self.assertEqual(model.status_code,201,model.text)
        self.model = model.json()['id']
        self.start_patch(patch.dict(os.environ, {'YOSAKI_CHRONICLE_CONTEXT':'1',
            'YOSAKI_CHRONICLE_WORKSPACE':'1','YOSAKI_CHRONICLE_CHAT_SUPPLEMENT':'1',
            'YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE':datetime.now(timezone.utc).date().isoformat()}))
        self.start_patch(patch.object(app_module,'chronicle_provider',self.provider,create=True))
        self.start_patch(patch.object(app_module,'chronicle_embedding_provider',self.embedding_provider,create=True))
        client_class = httpx.AsyncClient
        self.start_patch(patch.object(app_module.httpx,'AsyncClient',
            side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(self.chat_wire), **kwargs)))

    def start_patch(self, editing):
        editing.start()
        self.addCleanup(editing.stop)

    def price(self, kind, model, rate_in, rate_out):
        return {'provider':kind,'model':model,'mode':'standard','checkedAt':'2026-11-17',
            'sourceUrl':'https://developers.openai.com/api/docs/pricing','inputUsdPerMillion':rate_in,'outputUsdPerMillion':rate_out}

    def provider(self, model_id):
        self.assertEqual(model_id,self.model)
        return (responses.ResponsesAdapter(model=semantic.MODEL,transport=httpx.MockTransport(self.wire)),
                self.price('openai_responses',semantic.MODEL,'2.00','10.00'))

    def embedding_provider(self, model_id):
        self.assertEqual(model_id,self.model)
        return (embedding.Adapter('local-test',transport=httpx.MockTransport(self.wire)),
                self.price('openai_embeddings',embedding.MODEL,'0.02',0))

    def wire(self, request):
        body = json.loads(request.content)
        self.assertNotIn(self.raw, str(body))
        path = request.url.path
        self.calls.append(path)
        if path.endswith('/embeddings'):
            self.assertIn(body['input'], ([self.query], [self.unrelated]))
            return httpx.Response(200, json={'model':embedding.MODEL,'usage':{'prompt_tokens':10,'total_tokens':10},
                'data':[{'index':0,'embedding':self.vector}]})
        data = json.loads(body['input'][0]['content'])
        self.assertEqual(data['candidates'], [{'id':'1','text':'银叶馆蓝灯指示牌\n'+self.synopsis}])
        matched = data['query']==self.query
        if path.endswith('/input_tokens'):
            if self.mode=='slow_count':
                time.sleep(.38)
                self.late_stage_finished.set()
            return httpx.Response(200,json={'input_tokens':5000 if self.mode=='over_budget' else 250})
        if self.mode=='transport_failure':
            raise httpx.ReadTimeout('synthetic upstream timeout')
        if self.mode=='slow_generation':
            time.sleep(.65)
            self.late_stage_finished.set()
        decisions = [{'id':'1','relevant':matched,'evidence':self.synopsis if matched else ''}]
        return httpx.Response(200,json={'id':'resp_offline','status':'completed','model':semantic.MODEL,
            'service_tier':'default','usage':{'input_tokens':250,'output_tokens':40},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'judgments':decisions})}]}]})

    def chat_wire(self, request):
        body = json.loads(request.content)
        self.assertNotIn(self.raw,str(body))
        self.chat.append(body)
        if self.mode in ('slow_count','slow_generation'):
            self.chat_started_before_late_stage = not self.late_stage_finished.is_set()
        events = [{'type':'response.output_text.delta','delta':'已回答。'},
                  {'type':'response.completed','response':{'status':'completed','id':'mock_chat',
                    'usage':{'input_tokens':350,'output_tokens':20}}}]
        return httpx.Response(200, headers={'Content-Type':'text/event-stream'},
            content=''.join('data: '+json.dumps(event)+'\n\n' for event in events).encode())

    def send(self, query, approved=True):
        payload = {'character_id':self.cid,'message':query,'model_profile_id':self.model}
        consent=self.client.put(f'/api/characters/{self.cid}/paid-memory-lookup',params={'enabled':approved})
        self.assertEqual(consent.status_code,200)
        if not approved:
            # A stale client still transmitting its old per-message consent cannot authorize spending.
            payload['chronicle_supplement_max_microusd'] = semantic.BUDGET
        result = self.client.post('/api/chat/stream',json=payload)
        self.assertEqual(result.status_code,200,result.text)
        self.assertIn('event: message_complete',result.text)
        return self.synopsis in str(self.chat[-1])

    def test_automatic_recall_enters_actual_chat_and_unrelated_stays_empty(self):
        self.assertTrue(self.send(self.query))
        self.assertFalse(self.send(self.unrelated))
        self.assertEqual(self.calls,['/v1/embeddings','/v1/responses/input_tokens','/v1/responses']*2)

    def test_missing_approval_and_ordinary_hit_never_call_supplement(self):
        self.assertFalse(self.send(self.query,approved=False))
        self.assertTrue(self.send('银叶馆蓝灯指示牌'))
        self.assertEqual(self.calls, [])

    def test_tool_and_passive_content_share_one_allowance(self):
        from backend.app.memory_v2_chronicle_tools import NAMES,pack_context
        body='界'*2000
        passive=[{'id':'chronicle:a','content':'概述','channel':'chronicle'}]
        rows=[{'role':'user','content':'此前的经历'},
              {'role':'tool','tool_name':NAMES[0],'content':json.dumps({'blocks':[{'entryIds':['a'],'text':'概述'}]})},
              {'role':'tool','tool_name':NAMES[1],'content':json.dumps({'view':'NARRATIVE','entryId':'a','text':body,'offset':0,'nextOffset':None})}]
        history,selected=pack_context(rows,passive,False)
        self.assertFalse(selected)
        self.assertNotIn('text',json.loads(history[1]['content'])['blocks'][0])
        page=json.loads(history[2]['content'])
        self.assertEqual(page['text'],body[:page['nextOffset']])
        self.assertEqual(page['nextOffset'],1012)
        fresh,selected=pack_context(rows,[],True)
        self.assertFalse(selected)
        self.assertEqual(json.loads(fresh[2]['content']),{'state':'previous_turn'})

    def test_permanent_on_and_cancel_change_actual_requests_for_same_unrelated_query(self):
        self.assertFalse(self.send(self.unrelated,approved=False))
        with app_module.connect() as db:
            flags = dict.fromkeys(curation.FLAGS,False)
            flags['permanent'] = True
            setting = curation.save(db,self.cid,1,self.entry,0,flags,'合成对照')
        self.assertTrue(self.send(self.unrelated,approved=False))
        with app_module.connect() as db:
            flags['permanent'] = False
            curation.save(db,self.cid,1,self.entry,setting['revision'],flags,'取消合成对照')
        self.assertFalse(self.send(self.unrelated,approved=False))
        self.assertEqual(self.calls,[])

    def test_slow_count_does_not_hold_chat_or_start_generation(self):
        self.mode = 'slow_count'
        with patch.object(semantic, 'chat_deadline', side_effect=lambda seconds: time.monotonic()+.24):
            self.assertFalse(self.send(self.query))
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            with app_module.connect() as db:
                settled = db.execute("""SELECT 1 FROM mv3_jobs WHERE status='failed'
                    AND json_extract(read_set_json,'$.phase')='judge'""").fetchone()
            if settled:
                break
            time.sleep(.03)
        self.assertIsNotNone(settled)
        self.assertEqual(self.calls,['/v1/embeddings','/v1/responses/input_tokens'])
        self.assertTrue(self.chat_started_before_late_stage)

    def test_late_generation_settles_without_delaying_chat_or_injecting(self):
        self.mode = 'slow_generation'
        with patch.object(semantic, 'chat_deadline', side_effect=lambda seconds: time.monotonic()+.4):
            self.assertFalse(self.send(self.query))
        deadline = time.monotonic()+3
        row = None
        while time.monotonic() < deadline:
            with app_module.connect() as db:
                row = db.execute("""SELECT c.state,b.reserved_microusd,b.spent_estimate_microusd
                    FROM mv3_model_chunks c JOIN mv3_trial_budgets b ON c.trial_id=b.id
                    WHERE b.provider='openai_responses' AND c.state='rejected'""").fetchone()
            if row:
                break
            time.sleep(.03)
        self.assertIsNotNone(row)
        self.assertEqual(tuple(row),('rejected',0,1650))
        self.assertTrue(self.chat_started_before_late_stage)

    def test_over_budget_and_unknown_do_not_block_normal_chat(self):
        self.mode = 'over_budget'
        self.assertFalse(self.send(self.query))
        self.assertEqual(self.calls,['/v1/embeddings','/v1/responses/input_tokens'])
        self.mode = 'transport_failure'
        self.assertFalse(self.send(self.query))
        with app_module.connect() as db:
            hold = db.execute("""SELECT c.state,b.reserved_microusd FROM mv3_model_chunks c
                JOIN mv3_trial_budgets b ON b.id=c.trial_id WHERE c.state='unknown'""").fetchone()
            self.assertIsNotNone(hold)
            self.assertGreater(hold['reserved_microusd'],0)
        self.assertFalse(self.send(self.query))
        self.assertEqual(self.calls,['/v1/embeddings','/v1/responses/input_tokens',
            '/v1/responses/input_tokens','/v1/responses'])

    def test_embedding_budget_sync_repairs_only_status_and_is_idempotent(self):
        def wire(request):
            return httpx.Response(200,json={'model':embedding.MODEL,'usage':{'prompt_tokens':147,'total_tokens':147},
                'data':[{'index':i,'embedding':self.vector} for i in range(2)]})
        price = self.price('openai_embeddings',embedding.MODEL,'0.02',0)
        with app_module.connect() as db:
            trial = trials.create_trial(db,self.cid,embedding.MODEL,price,max_requests=1,max_microusd=20)
        adapter = embedding.Adapter('local-test',transport=httpx.MockTransport(wire))
        try:
            embedding.run(app_module.connect,self.cid,1,self.query,trial,adapter)
        finally:
            adapter.close()
        with app_module.connect() as db:
            before = trials.trial_status(db,trial,self.cid)
            self.assertEqual((before['status'],before['reserved_microusd'],before['spent_estimate_microusd']),
                             ('completed',0,3))
            db.execute("UPDATE mv3_trial_budgets SET status='running' WHERE id=?", (trial,))
            self.assertEqual(embedding.sync_budget_status(db,trial),1)
            self.assertEqual(trials.trial_status(db,trial,self.cid),before)
            self.assertEqual(embedding.sync_budget_status(db,trial),0)
