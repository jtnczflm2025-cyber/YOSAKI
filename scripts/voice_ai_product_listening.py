"""Opt-in, one-batch guard around the actual ordinary-chat product application.

Not imported by any daily entry. No approval creation, OAuth or provider probe.
The existing exclusive receipt claim, planner, renderer, HTTP adapter, daily
meter and product routes are reused. A caller must explicitly install this
guard into a dedicated single-worker product process after approval.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import types
import time
from unittest.mock import patch

import httpx
from fastapi import HTTPException,Request
from starlette.responses import JSONResponse

from backend.app.voice.annotation_lab import AudioRequest
from backend.app.voice.annotation_planner import AnnotationError, PARAMETERS, fingerprint
from backend.app.voice.realtime_receipt import occupy_once, ApprovalError

STAGE = ContextVar('voice_listening_stage', default=None)
CHAT_INPUT = '日本語の短い二文で、夜更けに静かに話しかけてください。全体を六十文字以内にしてください。'


def digest(value):
    return fingerprint(value)


def freeze_context(main, message=CHAT_INPUT):
    """The product's actual context-preview/finalizer/serializer, no transport."""
    from backend.app import chat_protocol
    original=main.memory_context;serialize=chat_protocol.serialize;captured={}
    def context(db,*args,**kwargs):
        _,_,settings=original(db,*args,**kwargs)
        return [],None,{**dict(settings),'memory_enabled':False}
    def capture(profile,messages,tools):
        body,url=serialize(profile,messages,tools);body['max_output_tokens']=2048
        captured.update(messages=messages,body=body,url=url)
        return body,url
    with patch.object(main,'memory_context',context),patch.object(main,'available_model_tools',lambda db:([],{})),patch.object(main.desires,'chat_advisory',lambda *a,**k:None),patch.object(chat_protocol,'serialize',capture):
        main.context_preview(main.PreviewInput(character_id=1,model_profile_id=5,message=message),
            types.SimpleNamespace(state=types.SimpleNamespace(user={'id':1})))
    return captured


class BatchGate:
    def __init__(self, manifest, directory, *, mock_only=False):
        self.manifest=manifest;self.directory=Path(directory).resolve()
        self.mock_only=mock_only;self.preview=None;self.occupied=False;self.started=None
        if not self.directory.is_dir() or self.directory.is_symlink():
            raise ValueError('existing_private_receipt_directory_required')

    def claim(self, stage, record):
        if stage not in ('chat','planning','tts'):
            raise ApprovalError('batch_stage_forbidden')
        expected={'run_id':self.manifest['run_id'],'manifest_sha256':digest(self.manifest),
                  'code_sha256':self.manifest['code_sha256'],'mock_only':self.mock_only}
        approval=self.directory/'approval.json'
        if not approval.is_file() or json.loads(approval.read_text(encoding='utf-8'))!=expected:
            raise ApprovalError('batch_approval_missing_or_mismatch')
        if not self.mock_only:
            root=Path(__file__).resolve().parents[1]
            if any(hashlib.sha256((root/p).read_bytes()).hexdigest()!=h for p,h in self.manifest['code_files'].items()):
                raise ApprovalError('frozen_code_changed')
        if stage=='chat':
            occupy_once(self.directory/'occupied.json',{**expected,'state':'occupied_outcome_unknown'})
            self.occupied=True;self.started=time.monotonic()
        elif not self.occupied or not (self.directory/'occupied.json').exists():
            raise ApprovalError('whole_batch_not_occupied')
        if self.started is None or time.monotonic()-self.started>160:
            raise ApprovalError('whole_batch_deadline')
        occupy_once(self.directory/(stage+'-attempt.json'),{'stage':stage,**record})

    def validate(self, request):
        if hasattr(self,'validate_binding'):self.validate_binding()
        body=json.loads(request.content)
        stage=STAGE.get()
        if stage not in ('chat','planning','tts'):
            raise ApprovalError('unbound_provider_dispatch_forbidden')
        if stage in ('chat','planning'):
            if str(request.url)!=self.manifest['model_endpoint']:
                raise ApprovalError('model_endpoint_changed')
            maximum=2048 if stage=='chat' else 512
            if (type(body.get('max_output_tokens')) is not int or body['max_output_tokens']!=maximum
                or body.get('model')!=self.manifest['model']['model_name']
                or body.get('tools') or body.get('stream') is not True):
                raise ApprovalError('model_request_limits_invalid')
            if stage=='chat' and digest(body)!=self.manifest['chat_body_sha256']:
                raise ApprovalError('frozen_chat_context_changed')
            if len(request.content)>self.manifest['input_byte_limits'][stage]:
                raise ApprovalError('full_model_input_budget_exceeded')
        else:
            if str(request.url)!=self.manifest['tts_endpoint']:
                raise ApprovalError('tts_endpoint_changed')
            inputs=body.get('inputs')
            if (not isinstance(inputs,list) or len(inputs)!=1 or
                not isinstance(inputs[0].get('text'),str) or not self.preview or
                inputs[0]['text']!=self.preview['request_text'] or
                body.get('model_id')!='eleven_v4' or body.get('language_code')!='ja' or
                set(body)!={'model_id','inputs','language_code'} or
                hashlib.sha256(inputs[0]['voice_id'].encode()).hexdigest()!=self.manifest['voice']['voice_id_sha256']):
                raise ApprovalError('tts_preview_or_voice_changed')
            if not 1<=len(inputs[0]['text'])<=120:
                raise ApprovalError('batch_tts_character_limit')
        return stage,body

    def bind_conversation(self,identity):
        path=self.directory/'target-conversation.json'
        if path.exists():
            if json.loads(path.read_text())['conversation_id']!=identity:
                raise ApprovalError('batch_conversation_already_bound')
        else:occupy_once(path,{'conversation_id':identity})


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, gate, inner):self.gate,self.inner=gate,inner
    async def handle_async_request(self, request):
        stage,body=self.gate.validate(request)
        self.gate.claim(stage,{'body_sha256':digest(body),
            'characters':sum(len(i['text']) for i in body.get('inputs',[])),
            'max_output_tokens':body.get('max_output_tokens')})
        deadline=self.gate.started+160
        async with asyncio.timeout_at(deadline):response=await self.inner.handle_async_request(request)
        original=response.stream
        class BoundedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                async with asyncio.timeout_at(deadline):
                    async for chunk in original:yield chunk
            async def aclose(self):await original.aclose()
        response.stream=BoundedStream()
        return response
    async def aclose(self):await self.inner.aclose()


def attach(main, gate, *, transport_factory=None, base_client=None):
    """Dedicated process only; return a restoration function for isolation tests.

    No global patch is installed by importing this module. The final product
    routes/components remain unchanged. No settings row is edited.
    """
    if not gate.mock_only and transport_factory is not None:
        raise ValueError('real_batch_cannot_inject_fake_or_alternate_transport')
    from backend.app import chatgpt_plan,chat_protocol
    runtime=main.app.state.voice_ai_http
    if runtime is None:raise ValueError('product_ai_capability_not_installed')
    original={key:getattr(main,key) for key in ('memory_context','available_model_tools')}
    serialize=chat_protocol.serialize;token=chatgpt_plan.access_token
    client=httpx.AsyncClient;settings=runtime.settings;finish=runtime._finish
    old_preferences=runtime.plans.performance_preferences
    runtime.plans.performance_preferences=gate.manifest.get('performance_preferences')
    tts_synthesize=runtime.plans.tts.synthesize;model_generate=runtime.plans.model.generate
    advisory=main.desires.chat_advisory
    # Per-batch context policy; never write normal context/Memory preferences.
    def context(db,*args,**kwargs):
        _,_,config=original['memory_context'](db,*args,**kwargs)
        return [],None,{**dict(config),'memory_enabled':False}
    def wire(profile,messages,tools):
        body,url=serialize(profile,messages,tools)
        if dict(profile)['id']!=5 or tools:
            raise HTTPException(409,'试听档案或工具范围不符，未派发')
        body['max_output_tokens']=2048
        if digest(body)!=gate.manifest['chat_body_sha256']:
            raise HTTPException(409,'试听完整上下文已变化，未派发')
        return body,url
    async def no_refresh(**kwargs):return await token(allow_refresh=False)
    def clients(*args,**kwargs):
        inner=transport_factory() if transport_factory else httpx.AsyncHTTPTransport(retries=0,proxy=gate.manifest.get('model_proxy_url'))
        kwargs.update(transport=GuardedTransport(gate,inner),trust_env=False,follow_redirects=False)
        kwargs.pop('proxy',None)
        return (base_client or client)(*args,**kwargs)
    def selected(owner):
        value=settings(owner)
        if owner!=gate.manifest['owner_id']:return value
        # Explicit one-batch selection, shown in this isolated product process;
        # the persistent daily preference remains untouched.
        return {**value,'enabled':True,'model_profile_id':5}
    async def planned(request):
        if runtime.resolve(5)['model_name']!=gate.manifest['model']['model_name']:
            raise AnnotationError('selected_model_changed',409)
        target=json.loads((gate.directory/'target-conversation.json').read_text())['conversation_id']
        with main.connect() as db:
            receipts=db.execute('SELECT message_id,generation_id,body_sha256 FROM voice_ai_completion_receipts WHERE owner_id=? AND conversation_id=?',
                (gate.manifest['owner_id'],target)).fetchall()
        if len(receipts)!=1:raise AnnotationError('single_new_completion_receipt_required',409)
        prepared=runtime.plans.source(receipts[0]['message_id'],gate.manifest['owner_id'])
        if (gate.manifest.get('expected_public_text') is not None and
                prepared['safe_text']!=gate.manifest['expected_public_text']):
            raise AnnotationError('frozen_public_reply_mismatch',409)
        supplied=json.loads(request['messages'][1]['content'])
        if supplied.get('input_sha256')!=prepared['input_sha256'] or not 1<=len(prepared['safe_text'])<=120:
            raise AnnotationError('new_reply_runtime_binding_changed',409)
        binding={'message_id':receipts[0]['message_id'],'conversation_id':target,
            'generation_id':receipts[0]['generation_id'],'body_sha256':receipts[0]['body_sha256'],
            'planning_input_sha256':prepared['input_sha256']}
        path=gate.directory/'runtime-source.json'
        if path.exists():
            if json.loads(path.read_text())!=binding:raise AnnotationError('runtime_source_changed',409)
        else:occupy_once(path,binding)
        scope=STAGE.set('planning')
        try:return await model_generate(request)
        finally:STAGE.reset(scope)
    async def tts(text,parameters):
        if parameters!=PARAMETERS or not 1<=len(text)<=120:
            raise AnnotationError('batch_tts_character_limit',409)
        scope=STAGE.set('tts')
        try:return await tts_synthesize(text,parameters)
        finally:STAGE.reset(scope)
    async def strict_finish(self,identity,owner):
        try:
            task=self.plans.tasks.get(identity)
            if task:await asyncio.shield(task)
            row=self.plans.current(identity,owner)
            if row['state'] not in ('ready','empty'):
                if row['state']=='fallback':
                    self.plans.store.stop(identity,'restricted','acceptance_planning_failed_no_fallback')
                    self.errors[identity]='acceptance_planning_failed_no_fallback'
                return
            preview=self.plans.preview(identity,owner)
            if preview['fallback_reason'] or not 1<=preview['request_characters']<=120:
                self.plans.store.stop(identity,'restricted','batch_tts_character_limit')
                self.errors[identity]='batch_tts_character_limit'
                return
            gate.preview=preview
            await self.plans.synthesize(identity,owner,AudioRequest(
                revision=preview['plan_revision'],preview_sha256=preview['preview_sha256']))
        except asyncio.CancelledError:pass
        except AnnotationError as exc:self.errors[identity]=exc.code
        except Exception:self.errors[identity]='acceptance_pipeline_unknown'
        finally:self.tasks.pop(identity,None)

    # Scope the unchanged actual chat route, including its StreamingResponse.
    route=next(r for r in main.app.router.routes if getattr(r,'path',None)=='/api/chat/stream')
    original_app=route.app
    async def scoped(scope,receive,send):
        raw=b''
        while True:
            event=await receive()
            if event['type']=='http.disconnect':return
            raw+=event.get('body',b'')
            if len(raw)>16384:raise HTTPException(413,'试听输入超过上限')
            if not event.get('more_body'):break
        payload=json.loads(raw)
        bound=gate.directory/'target-conversation.json'
        target=json.loads(bound.read_text())['conversation_id'] if bound.exists() else None
        user=scope.get('state',{}).get('user') or {}
        if (user.get('id')!=gate.manifest['owner_id'] or target is None or
            payload.get('conversation_id')!=target or payload.get('character_id')!=gate.manifest['character_id'] or
            payload.get('model_profile_id')!=5 or payload.get('message')!=gate.manifest.get('chat_input',CHAT_INPUT) or
            payload.get('reuse_last_user') or payload.get('attachment_ids') or payload.get('quote') or
            payload.get('voice_call_id') or payload.get('chronicle_supplement_max_microusd')):
            raise HTTPException(409,'试听身份、输入或目标对话不符，未派发')
        with main.connect() as db:
            target_row=db.execute('SELECT character_id FROM conversations WHERE id=?',(target,)).fetchone()
            count=db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?',(target,)).fetchone()[0]
        if not target_row or target_row[0]!=gate.manifest['character_id'] or count:
            raise HTTPException(409,'试听必须使用已绑定的新空对话，不重复提交')
        delivered=False
        async def replay():
            nonlocal delivered
            if not delivered:delivered=True;return {'type':'http.request','body':raw,'more_body':False}
            return await receive()
        marker=STAGE.set('chat')
        try:
            # Whole-chat hard limit is limited to this diagnostic product entry.
            async with asyncio.timeout(30):await original_app(scope,replay,send)
        finally:STAGE.reset(marker)
    route.app=scoped
    main.memory_context=context;main.available_model_tools=lambda db:([],{})
    main.desires.chat_advisory=lambda *a,**k:None
    chat_protocol.serialize=wire;chatgpt_plan.access_token=no_refresh;httpx.AsyncClient=clients
    runtime.settings=selected;runtime._finish=types.MethodType(strict_finish,runtime)
    runtime.plans.model.generate=planned;runtime.plans.tts.synthesize=tts
    @main.app.post('/api/voice/listening/bind-conversation')
    async def bind_conversation(request:Request):
        if request.state.user['id']!=gate.manifest['owner_id']:
            raise HTTPException(403,'试听身份不符')
        payload=await request.json();identity=payload.get('conversation_id')
        if type(identity) is not int:raise HTTPException(422,'需要新对话标识')
        with main.connect() as db:
            row=db.execute('SELECT character_id,model_profile_id FROM conversations WHERE id=?',(identity,)).fetchone()
            count=db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?',(identity,)).fetchone()[0]
        if not row or row[0]!=gate.manifest['character_id'] or row[1] not in (None,5) or count:
            raise HTTPException(409,'只能绑定角色正确的新空对话')
        gate.bind_conversation(identity)
        return {'conversation_id':identity,'model_profile_id':5,'run_id':gate.manifest['run_id']}
    def validate_binding():
        profile=dict(runtime.resolve(5))
        public={k:profile.get(k) for k in gate.manifest['model']}
        if public!=gate.manifest['model']:raise ApprovalError('selected_model_profile_changed')
        from backend.app.voice import storage
        with main.connect() as db:
            voice=storage.profile(db,gate.manifest['character_id'])
            consent=storage.consent(db,gate.manifest['owner_id'])
        if (not voice or not voice['active'] or voice['revision']!=gate.manifest['voice']['revision'] or
            hashlib.sha256(voice['voice_id'].encode()).hexdigest()!=gate.manifest['voice']['voice_id_sha256'] or
            not consent or consent['notice_version']!=gate.manifest['voice']['notice_version']):
            raise ApprovalError('voice_or_consent_changed')
        if not gate.mock_only and STAGE.get() in (None,'chat','planning'):
            account=(chatgpt_plan._read().get('account') or {})
            if digest({k:account.get(k) for k in ('client_id','email')})!=gate.manifest['credential_binding']['same_account_sha256']:
                raise ApprovalError('same_account_binding_changed')
            minimum=60 if STAGE.get()=='planning' else 90
            if (not account.get('access_token') or 'chatgpt.tokens.use.direct' not in account.get('scopes',[]) or
                account.get('expires_at',0)<=time.time()+minimum):
                raise ApprovalError('credential_expired_no_refresh_authorized')
    gate.validate_binding=validate_binding
    original_stack=main.app.middleware_stack
    class ListeningScopeFence:
        def __init__(self,app):self.app=app
        async def __call__(self,scope,receive,send):
            if scope['type']=='websocket':
                await send({'type':'websocket.close','code':1008,'reason':'listening_batch_ws_forbidden'})
                return
            if scope['type']=='http':
                path=scope.get('path','');method=scope.get('method','GET')
                forbidden=(path.startswith('/api/chatgpt/') and path!='/api/chatgpt/status')
                if method not in ('GET','HEAD','OPTIONS'):
                    allowed=(path in ('/api/chat/stream','/api/auth/login','/api/auth/logout','/api/conversations','/api/voice/listening/bind-conversation')
                        or method=='POST' and path.startswith('/api/voice/ai-http/messages/') and path.endswith('/read')
                        or method=='DELETE' and path.startswith('/api/voice/ai-http/plans/'))
                    forbidden=forbidden or not allowed
                if forbidden:
                    await JSONResponse({'detail':'本次听评不允许此操作；未连接供应商'},status_code=409)(scope,receive,send)
                    return
            await self.app(scope,receive,send)
    if original_stack is None:
        main.app.add_middleware(ListeningScopeFence)
    else:main.app.middleware_stack=ListeningScopeFence(original_stack)
    def restore():
        for key,value in original.items():setattr(main,key,value)
        main.desires.chat_advisory=advisory;chat_protocol.serialize=serialize
        chatgpt_plan.access_token=token;httpx.AsyncClient=client;route.app=original_app
        runtime.settings=settings;runtime._finish=finish
        runtime.plans.performance_preferences=old_preferences
        runtime.plans.model.generate=model_generate;runtime.plans.tts.synthesize=tts_synthesize
        if original_stack is not None:main.app.middleware_stack=original_stack
    return restore


def main():
    """Only used after a new approval, through the existing protected runtime."""
    import argparse,os,uvicorn
    from scripts import yosaki_runtime
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--receipt-dir',type=Path,required=True)
    parser.add_argument('--port',type=int,default=18085)
    parser.add_argument('--frontend-port',type=int,default=5185)
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    gate=BatchGate(manifest,args.receipt_dir)
    if not (args.receipt_dir/'approval.json').is_file():raise ApprovalError('no_approved_receipt')
    if (Path(os.getenv('YOSAKI_DATA_DIR','data'))/'yosaki.sqlite3').resolve()!=Path(manifest['database']).resolve():
        raise ApprovalError('runtime_database_changed')
    # Reuse exactly the already-reviewed database path; local status is not a
    # credential test and this entry never performs OAuth or catalogue requests.
    old_run=uvicorn.run
    def run(app,*a,**kwargs):
        from backend.app import main as product
        attach(product,gate)
        return old_run(product.app,*a,**kwargs)
    uvicorn.run=run
    os.environ['YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED']='1'
    import sys
    argv=['yosaki_runtime.py','development','--voice-acceptance','--daily-voice','--daily-ttd-ws','--ai-expression','--port',str(args.port),'--acceptance-frontend-port',str(args.frontend_port)]
    try:
        with patch.object(sys,'argv',argv):yosaki_runtime.main()
    finally:uvicorn.run=old_run


if __name__=='__main__':main()
