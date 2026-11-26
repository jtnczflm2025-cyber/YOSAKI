"""Final actual-main/actual-App fixture. Synthetic DB, tokens and raw SSE only."""
import asyncio
import importlib
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from scripts.voice_ai_http_lab import BODY,MP3,SSE,events


class Fixture:
    def __init__(self,root,*,public_reply=None,annotation_selector=None):
        self.public_reply,self.annotation_selector=public_reply,annotation_selector
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        if (self.root/'yosaki.sqlite3').exists():raise ValueError('fresh_synthetic_database_required')
        self.env=patch.dict(os.environ,{'YOSAKI_DATA_DIR':str(self.root),'YOSAKI_CHATGPT_PLAN_ENABLED':'1',
            'YOSAKI_VOICE_ENABLED':'1','ELEVENLABS_API_KEY':'synthetic-only',
            'YOSAKI_V3_EXPRESSION_ENABLED':'1','YOSAKI_VOICE_AUTO_SPEAK_ENABLED':'1',
            'YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED':'1','YOSAKI_BACKGROUND_TASKS_ENABLED':'0',
            'YOSAKI_VOICE_AI_HTTP_ENABLED':'0','YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED':'1',
            'YOSAKI_ALLOWED_ORIGINS':'http://127.0.0.1:18902',
            'YOSAKI_CHRONICLE_PAID_ENABLED':'0'})
        self.env.start()
        from backend.app import main
        self.main=importlib.reload(main)
        from backend.app.voice import storage,auto_budget,expression_product
        with main.connect() as db:
            storage.init_db(db);auto_budget.init_db(db);auto_budget.init_daily_db(db);expression_product.init_db(db)
        self.seed=TestClient(main.app)
        r=self.seed.post('/api/auth/setup',json={'username':'synthetic','password':'synthetic-only-password-2026'})
        if r.status_code not in (200,201):raise RuntimeError('synthetic_setup_failed_'+str(r.status_code))
        r=self.seed.post('/api/models',json={'name':'规划与聊天合成档案','provider_name':'synthetic','protocol':'chatgpt_plan',
            'base_url':'https://synthetic.invalid/v1','model_name':'gpt-6.1-sol','max_tokens':2048,'enabled':True})
        if r.status_code not in (200,201):raise RuntimeError('synthetic_model_failed_'+str(r.status_code))
        self.model_id=r.json()['id']
        self.seed.patch('/api/characters/1',json={'default_model_id':self.model_id})
        from backend.app.voice.schemas import ProfileInput
        with main.connect() as db:
            storage.put_profile(db,1,ProfileInput(expected_revision=0,voice_id='synthetic-voice',active=True))
        with main.connect() as db:
            db.execute('UPDATE voice_profiles SET revision=3');storage.put_consent(db,1)
            db.execute("INSERT INTO voice_auto_owner_settings VALUES(1,20,40,4000,1,'synthetic','synthetic')")
        from backend.app.voice.ai_daily import migrate
        with main.connect() as db:
            db.execute('BEGIN IMMEDIATE');migrate(db)
        self.seed.close()
        os.environ.update(YOSAKI_VOICE_AI_HTTP_ENABLED='1',YOSAKI_VOICE_AI_HTTP_DATABASE=str(main.DB.resolve()))
        self.main=importlib.reload(main)
        self.chat_count=self.plan_count=self.tts_count=0
        self.scenario='normal';self.hold=None;self.entered=threading.Event();self.release=threading.Event()
        self.plan_requests=[];self.tts_requests=[];self.tts_entered=threading.Event()
        self.original_client=httpx.AsyncClient
        def client(*args,**kwargs):
            kwargs['transport']=httpx.MockTransport(self.handle);kwargs.pop('proxy',None)
            return self.original_client(*args,**kwargs)
        async def token(**kwargs):return 'synthetic-token'
        from backend.app import chatgpt_plan
        self.patches=[patch.object(httpx,'AsyncClient',client),patch.object(chatgpt_plan,'access_token',token)]
        for p in self.patches:p.start()
        self.client=TestClient(self.main.app)
        self.client.__enter__()
        r=self.client.post('/api/auth/login',json={'username':'synthetic','password':'synthetic-only-password-2026'})
        if r.status_code!=200:raise RuntimeError('synthetic_login_failed')

    def counters(self):
        return {'chat':self.chat_count,'planning':self.plan_count,'tts':self.tts_count,
                'planning_caps':[r.get('max_output_tokens') for r in self.plan_requests],
                'tts_characters':[sum(len(i['text']) for i in r.get('inputs',[])) for r in self.tts_requests]}

    async def handle(self,request):
        scenario=self.root/'scenario.txt'
        if scenario.is_file():self.scenario=scenario.read_text(encoding='utf-8').strip()
        data=json.loads(request.content)
        if request.url.path.endswith('/responses'):
            inputs=data.get('input',[])
            try:content=json.loads(next(m['content'] for m in inputs if m.get('role')=='user'))
            except (ValueError,StopIteration,TypeError):content={}
            if isinstance(content,dict) and 'public_reply' in content:
                self.plan_count+=1;self.plan_requests.append(data)
                if self.scenario=='hold':
                    self.entered.set()
                    while not self.release.is_set() and not (self.root/'release-planning').exists():await asyncio.sleep(.01)
                if self.scenario=='unknown':raise httpx.ReadTimeout('synthetic')
                if self.scenario=='failed':return httpx.Response(400,json={'error':'synthetic_planning_failure'})
                f=next(f for f in content['fragments'] if f['kind']=='sentence')
                output={'schema_version':1,'input_sha256':content['input_sha256'],'annotations':[
                    {'fragment_id':'invalid' if self.scenario=='invalid' else f['fragment_id'],'intent':'thoughtful'}]}
                if self.annotation_selector is not None and self.scenario=='normal':
                    output['annotations']=self.annotation_selector(content)
                return httpx.Response(200,stream=SSE(events(json.dumps(output,ensure_ascii=False))))
            self.chat_count+=1
            if self.scenario=='chat_restricted':
                values=events(BODY if self.public_reply is None else self.public_reply)
                values.insert(1,{'type':'response.synthetic_unknown'})
                return httpx.Response(200,stream=SSE(values))
            return httpx.Response(200,stream=SSE(events(BODY if self.public_reply is None else self.public_reply)))
        if request.url.path.endswith('/text-to-dialogue'):
            self.tts_count+=1;self.tts_requests.append(data)
            if self.scenario=='late_tts':
                self.tts_entered.set()
                try:
                    while not (self.root/'release-audio').exists():await asyncio.sleep(.02)
                except asyncio.CancelledError:pass
            return httpx.Response(200,content=MP3)
        raise AssertionError('no_extra_fake_provider_request')

    def chat(self):
        response=self.client.post('/api/chat/stream',json={'character_id':1,'message':'短い日本語で返事してください。',
            'model_profile_id':self.model_id})
        values=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        complete=next((v for v in values if 'message_id' in v and 'public_text_sha256' in v),None)
        if complete is None:raise AssertionError('chat_not_completed: '+response.text[-700:])
        return complete

    def close(self):
        if getattr(self,'closed',False):return
        self.closed=True
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.env.stop()


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,required=True);args=parser.parse_args()
    fixture=Fixture(args.data_dir)
    # Auto-login a synthetic account in the isolated page only.
    @fixture.main.app.get('/synthetic-login')
    async def login():
        from fastapi.responses import RedirectResponse
        response=RedirectResponse('/api/auth/status')
        response.set_cookie('yosaki_session',fixture.client.cookies.get('yosaki_session'),httponly=True,samesite='strict')
        return response
    async def serve():
        import sys,uvicorn
        def deny(event,arguments):
            if event=='socket.connect':raise PermissionError('synthetic_server_outbound_denied')
        sys.addaudithook(deny)
        await uvicorn.Server(uvicorn.Config(fixture.main.app,host='127.0.0.1',port=18901,workers=1,access_log=False)).serve()
    asyncio.run(serve())


if __name__=='__main__':main()
