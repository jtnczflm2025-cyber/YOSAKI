import asyncio,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from scripts.voice_ai_chat_candidate_fixture import Fixture
from scripts.voice_ai_product_listening import BatchGate,GuardedTransport,STAGE,attach,digest,freeze_context,CHAT_INPUT


class ProductListening(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-listening-fake-')
        self.root=Path(self.temp.name);self.f=Fixture(self.root/'product')
        while self.f.model_id<5:
            self.f.model_id=self.f.client.post('/api/models',json={'name':'explicit profile five','provider_name':'synthetic',
                'protocol':'chatgpt_plan','base_url':'https://synthetic.invalid/v1','model_name':'gpt-6.1-sol','max_tokens':2048,'enabled':True}).json()['id']
        self.f.client.patch('/api/characters/1',json={'default_model_id':5})
        self.context=freeze_context(self.f.main)
        self.directory=self.root/'receipt';self.directory.mkdir()
        profile=dict(self.f.main._voice_ai_profile(5))
        keys=('id','protocol','model_name','enabled','max_tokens','updated_at','reasoning_effort')
        import hashlib
        self.manifest={'run_id':'synthetic-product-listening','owner_id':1,'character_id':1,'code_sha256':'synthetic',
            'model':{k:profile.get(k) for k in keys},'model_endpoint':self.context['url'],
            'chat_body_sha256':digest(self.context['body']),'input_byte_limits':{'chat':10000,'planning':24000},
            'tts_endpoint':'https://api.elevenlabs.io/v1/text-to-dialogue?output_format=mp3_44100_128',
            'voice':{'revision':3,'voice_id_sha256':hashlib.sha256(b'synthetic-voice').hexdigest(),'notice_version':'2026-11-08-v1'}}
        self.gate=BatchGate(self.manifest,self.directory,mock_only=True)
        self.approve()
        self.conv=self.f.client.post('/api/conversations',json={'character_id':1,'title':'synthetic bound empty'}).json()['id']
        self.gate.bind_conversation(self.conv)
        self.restore=attach(self.f.main,self.gate,transport_factory=lambda:httpx.MockTransport(self.f.handle),base_client=self.f.original_client)

    def approve(self):
        (self.directory/'approval.json').write_text(json.dumps({'run_id':self.manifest['run_id'],
            'manifest_sha256':digest(self.manifest),'code_sha256':'synthetic','mock_only':True}))
    def tearDown(self):
        self.restore();self.f.close();self.temp.cleanup()
    def chat(self):
        return self.f.client.post('/api/chat/stream',json={'character_id':1,'conversation_id':self.conv,
            'model_profile_id':5,'message':CHAT_INPUT})
    def message(self):
        response=self.chat();self.assertEqual(response.status_code,200,response.text)
        with self.f.main.connect() as db:
            row=db.execute("SELECT message_id FROM voice_ai_completion_receipts WHERE conversation_id=?",(self.conv,)).fetchone()
        self.assertIsNotNone(row,response.text[-1000:]);return row[0]
    def read(self,message):
        response=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={})
        self.assertEqual(response.status_code,200,response.text);identity=response.json()['id']
        for _ in range(150):
            value=self.f.client.get('/api/voice/ai-http/plans/'+identity).json()
            if value['bridge_state'] not in ('planning','synthesizing'):return value
            time.sleep(.01)
        self.fail('product did not finish')

    def test_actual_product_chat_plan_single_tts_and_cache_replay(self):
        message=self.message();value=self.read(message);self.assertEqual(value['bridge_state'],'completed',value)
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,1))
        self.assertEqual(self.f.plan_requests[0]['max_output_tokens'],512)
        self.assertLessEqual(value['preview']['request_characters'],120)
        self.assertEqual(json.loads((self.directory/'chat-attempt.json').read_text())['max_output_tokens'],2048)
        for _ in range(2):self.assertEqual(self.f.client.get(value['audio'][0]['url']).status_code,200)
        self.read(message)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))
        with self.f.main.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_ai_owner_settings').fetchone()[0],0)

    def test_planning_failure_invalid_and_unknown_never_fall_back(self):
        message=self.message()
        self.f.scenario='failed';value=self.read(message)
        self.assertEqual(value['bridge_state'],'restricted',value)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,0))
        self.read(message);self.assertEqual((self.f.plan_count,self.f.tts_count),(1,0))

    def test_invalid_suggestion_stops(self):
        message=self.message();self.f.scenario='invalid';value=self.read(message)
        self.assertEqual(value['bridge_state'],'restricted',value)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,0))

    def test_unknown_stops_without_more_calls(self):
        message=self.message();self.f.scenario='unknown';value=self.read(message)
        self.assertEqual(value['bridge_state'],'failed_unknown',value)
        self.read(message);self.assertEqual((self.f.plan_count,self.f.tts_count),(1,0))

    def test_missing_approval_zero_provider_requests(self):
        (self.directory/'approval.json').unlink()
        self.chat();self.assertEqual(self.f.chat_count,0)

    def test_caps_context_and_oauth_rejected_before_transport(self):
        async def check():
            marker=STAGE.set('chat')
            try:
                for field,value in [('max_output_tokens',None),('max_output_tokens',512),('model','other')]:
                    body={**self.context['body'],field:value}
                    with self.assertRaises(ValueError):self.gate.validate(httpx.Request('POST',self.context['url'],json=body))
                body={**self.context['body'],'instructions':'changed'}
                with self.assertRaises(ValueError):self.gate.validate(httpx.Request('POST',self.context['url'],json=body))
                with self.assertRaises(ValueError):self.gate.validate(httpx.Request('GET','https://api.openai.com/v1/models',json={}))
            finally:STAGE.reset(marker)
        asyncio.run(check());self.assertEqual(self.f.chat_count,0)

    def test_character_limit_counts_tags_before_daily_reservation(self):
        from backend.app.voice.annotation_planner import PARAMETERS
        with self.assertRaisesRegex(Exception,'batch_tts_character_limit'):asyncio.run(self.f.main.app.state.voice_ai_http.plans.tts.synthesize('[thoughtful] '+'あ'*110,PARAMETERS))
        self.assertEqual(self.f.tts_count,0)
        with self.f.main.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_auto_owner_day_budget').fetchone()[0],0)

    def test_actual_product_rendered_tags_over_120_stops_before_tts(self):
        import scripts.voice_ai_chat_candidate_fixture as fixture
        with patch.object(fixture,'BODY','あ'*109+'。'):
            value=self.read(self.message())
        self.assertEqual(value['bridge_state'],'restricted',value)
        self.assertEqual(value['reason'],'batch_tts_character_limit')
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,0))
        with self.f.main.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_auto_owner_day_budget').fetchone()[0],0)

    def test_zero_annotations_remains_allowed(self):
        original=self.f.handle
        async def empty(request):
            body=json.loads(request.content)
            try:content=json.loads(body.get('input',[{}])[0].get('content',''))
            except (ValueError,TypeError):content={}
            if 'public_reply' in content:
                from scripts.voice_ai_http_lab import SSE,events
                self.f.plan_count+=1
                result={'schema_version':1,'input_sha256':content['input_sha256'],'annotations':[]}
                return httpx.Response(200,stream=SSE(events(json.dumps(result))))
            return await original(request)
        self.f.handle=empty
        value=self.read(self.message())
        self.assertEqual(value['bridge_state'],'completed',value)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))

    def test_durable_claim_rejects_concurrent_or_restart_reuse(self):
        self.gate.claim('chat',{'test':True})
        recreated=BatchGate(self.manifest,self.directory,mock_only=True)
        with self.assertRaises(ValueError):recreated.claim('chat',{'test':True})
        self.assertEqual(self.f.chat_count,0)

    def test_expired_credential_never_refreshes_or_dispatches(self):
        self.restore()
        from backend.app import chatgpt_plan
        observed=[]
        async def expired(**kwargs):
            observed.append(kwargs);raise HTTPException(401,'synthetic expired credential')
        with patch.object(chatgpt_plan,'access_token',expired):
            self.restore=attach(self.f.main,self.gate,transport_factory=lambda:httpx.MockTransport(self.f.handle),base_client=self.f.original_client)
            self.chat();self.restore()
        self.restore=lambda:None
        self.assertEqual(observed,[{'allow_refresh':False}]);self.assertEqual(self.f.chat_count,0)

    def test_product_binding_endpoint_and_wrong_target_are_zero_dispatch(self):
        response=self.f.client.post('/api/voice/listening/bind-conversation',json={'conversation_id':self.conv})
        self.assertEqual(response.status_code,200,response.text)
        other=self.f.client.post('/api/conversations',json={'character_id':1,'title':'not bound'}).json()['id']
        response=self.f.client.post('/api/chat/stream',json={'character_id':1,'conversation_id':other,'model_profile_id':5,'message':CHAT_INPUT})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.f.chat_count,0)
        with self.f.main.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?',(other,)).fetchone()[0],0)

    def test_whole_deadline_and_restart_never_restore_budget(self):
        self.gate.claim('chat',{})
        self.gate.started=time.monotonic()-161
        with self.assertRaisesRegex(ValueError,'whole_batch_deadline'):self.gate.claim('planning',{})
        recreated=BatchGate(self.manifest,self.directory,mock_only=True)
        with self.assertRaisesRegex(ValueError,'whole_batch_not_occupied'):recreated.claim('planning',{})
        self.assertEqual(self.f.plan_count,0)

    def test_real_guard_local_expiry_and_same_account_binding(self):
        from backend.app import chatgpt_plan
        account={'client_id':'synthetic-client','email':'synthetic@example.invalid','access_token':'not-real',
            'scopes':['chatgpt.tokens.use.direct'],'expires_at':time.time()-10}
        self.gate.mock_only=False
        self.manifest['credential_binding']={'same_account_sha256':digest({k:account[k] for k in ('client_id','email')})}
        with patch.object(chatgpt_plan,'_read',lambda:{'account':account}):
            with self.assertRaisesRegex(ValueError,'credential_expired'):self.gate.validate_binding()
            account['email']='changed@example.invalid'
            with self.assertRaisesRegex(ValueError,'same_account_binding_changed'):self.gate.validate_binding()
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(0,0,0))


    def test_scope_fence_blocks_oauth_directory_settings_and_ordinary_synthesis(self):
        for method,path in [('GET','/api/chatgpt/models'),('POST','/api/chatgpt/start'),
            ('PUT','/api/voice/ai-http/settings'),('POST','/api/voice/messages/1/speech'),('POST','/api/voice/calls')]:
            self.assertEqual(self.f.client.request(method,path,json={}).status_code,409,path)
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(0,0,0))

    def test_scope_fence_rejects_scribe_and_ttd_ws_before_upstream(self):
        from starlette.websockets import WebSocketDisconnect
        for path in ('/api/voice/scribe-draft/ws','/api/voice/realtime/output'):
            with self.assertRaises(WebSocketDisconnect) as caught:
                with self.f.client.websocket_connect(path):pass
            self.assertEqual(caught.exception.code,1008)
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(0,0,0))


class ProtectedLauncher(unittest.TestCase):
    def test_uses_existing_no_argument_runtime_main_and_preserves_explicit_ports(self):
        import os,sys
        from scripts import voice_ai_product_listening as entry,yosaki_runtime
        observed=[]
        with tempfile.TemporaryDirectory(prefix='listening-launcher-fake-') as folder:
            root=Path(folder);manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'database':str(root/'yosaki.sqlite3')}))
            (root/'approval.json').write_text('{}') # synthetic; no runtime/transport invoked
            def runtime_main():observed.append(list(sys.argv));return 0
            with patch.dict(os.environ,{'YOSAKI_DATA_DIR':folder}),patch.object(sys,'argv',['entry','--manifest',str(manifest),'--receipt-dir',folder]),patch.object(yosaki_runtime,'main',runtime_main):entry.main()
        self.assertEqual(len(observed),1)
        self.assertIn('--ai-expression',observed[0])
        self.assertEqual(observed[0][observed[0].index('--port')+1],'18085')
        self.assertEqual(observed[0][observed[0].index('--acceptance-frontend-port')+1],'5185')


if __name__=='__main__':unittest.main()
