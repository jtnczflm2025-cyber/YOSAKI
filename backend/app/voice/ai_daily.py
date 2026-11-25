"""Opt-in ordinary-chat AI expression candidate. No automatic schema migration.

The planner, trusted renderer and HTTP composition are reused. A backend receipt
is written only at the real display-gated chat completion branch, never inferred
from historical text/completed rows. Calls/WS are deliberately not installed.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from . import audio_cache, auto_budget, config
from .annotation_http_product import HTTPReplyBridge, SavedReplyRepository, _BoundHTTP, _ProductPlans
from .annotation_lab import PlanRequest, EditRequest
from .annotation_model import ProfilePlanningAdapter
from .annotation_planner import AnnotationError, fingerprint, render
from .performance_preferences import CANDIDATE, apply as apply_performance_preferences
from .annotation_store import AnnotationStore, ANNOTATION_TABLE_SQL
from .elevenlabs_adapter import ElevenLabsAdapter
from .realtime_answer_display import DisplayResult

TABLES = {'annotation_plans','annotation_operations','annotation_revisions','annotation_audio',
          'voice_ai_completion_receipts','voice_ai_owner_settings'}
ACTOR = ContextVar('voice_ai_actor', default=None)


def enabled():
    return os.getenv('YOSAKI_VOICE_AI_HTTP_ENABLED','0') == '1'


def schema_present(db):
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES <= names or 'annotation_lab_identity' in names:return False
    required={'annotation_audio':{'expires_at','audio_sha256','preview_sha','plan_id','revision','state','audio'},'voice_ai_completion_receipts':{'message_id','generation_id','body_sha256','answer_binding','display_action','stream_ended','model_completed'},'voice_ai_owner_settings':{'owner_id','enabled','model_profile_id','revision'}}
    return all(columns <= {r[1] for r in db.execute('PRAGMA table_info('+table+')')} for table,columns in required.items())


def migrate(db):
    """Explicit caller-owned transaction only. Never called from main/init_db."""
    if not db.in_transaction:
        raise ValueError('explicit_ai_migration_transaction_required')
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if TABLES & names:
        raise ValueError('ai_schema_already_present_or_partial')
    for sql in ANNOTATION_TABLE_SQL.split(';'):
        if sql.strip(): db.execute(sql)
    db.execute('ALTER TABLE annotation_audio ADD COLUMN expires_at TEXT')
    db.execute('ALTER TABLE annotation_audio ADD COLUMN audio_sha256 TEXT')
    db.execute('''CREATE TABLE voice_ai_completion_receipts(
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id),conversation_id INTEGER NOT NULL REFERENCES conversations(id),
        generation_id TEXT NOT NULL UNIQUE,body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
        model_completed INTEGER NOT NULL CHECK(model_completed=1),stream_ended INTEGER NOT NULL CHECK(stream_ended=1),
        display_action TEXT NOT NULL CHECK(display_action='allow'),answer_binding TEXT NOT NULL CHECK(answer_binding='matched'))''')
    db.execute('''CREATE TABLE voice_ai_owner_settings(
        owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),model_profile_id INTEGER REFERENCES model_profiles(id),
        revision INTEGER NOT NULL CHECK(revision>0))''')


def record_completion(db,message,owner,generation,result,*,model_completed,stream_ended):
    if (not enabled() or not schema_present(db) or not isinstance(result,DisplayResult)
            or result.action!='allow' or result.answer_binding_status!='matched'
            or model_completed is not True or stream_ended is not True): return False
    row=db.execute('''SELECT m.content,m.role,m.generation_status,m.conversation_id,c.character_id
        FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=?''',(message,)).fetchone()
    if not row or row['role']!='assistant' or row['generation_status']!='completed':return False
    # Failure of this optional receipt must not roll back or corrupt the reply.
    db.execute('SAVEPOINT voice_ai_receipt')
    try:
        db.execute('INSERT INTO voice_ai_completion_receipts VALUES(?,?,?,?,?,?,?,?,?,?)',
            (message,owner,row['character_id'],row['conversation_id'],generation,
             hashlib.sha256(row['content'].encode()).hexdigest(),1,1,'allow','matched'))
        db.execute('RELEASE voice_ai_receipt')
        return True
    except Exception:
        db.execute('ROLLBACK TO voice_ai_receipt');db.execute('RELEASE voice_ai_receipt')
        return False


class ProductStore(AnnotationStore):
    def __init__(self,path):
        self.path=Path(path).resolve()
        with self.connect() as db:
            if not schema_present(db):raise ValueError('voice_ai_schema_missing')

    def claim_audio(self,identity,revision,preview):
        # Serialize capacity check and the original durable claim together.
        with self.connect() as db:
            old=db.execute('SELECT 1 FROM annotation_audio WHERE preview_sha=?',(preview['preview_sha256'],)).fetchone()
            row=db.execute('SELECT revision,state FROM annotation_plans WHERE id=?',(identity,)).fetchone()
            if not row or row[0]!=revision or row[1] not in ('ready','empty','fallback'):
                raise AnnotationError('plan_revision_expired',409)
            if old:return False
            used=db.execute("SELECT COALESCE(SUM(CASE WHEN state='attempted' THEN 16777216 ELSE length(audio) END),0) FROM annotation_audio").fetchone()[0]
            legacy=db.execute("SELECT COALESCE(SUM(COALESCE(audio_bytes,16777216)),0) FROM voice_audio_cache WHERE status IN('ready','generating')").fetchone()[0]
            if used+legacy+audio_cache.MAX_OBJECT_BYTES>audio_cache.MAX_CACHE_BYTES or shutil.disk_usage(self.path.parent).free<audio_cache.MAX_OBJECT_BYTES:
                raise AnnotationError('cache_capacity_before_dispatch',409)
            db.execute("INSERT INTO annotation_audio(preview_sha,plan_id,revision,state,characters) VALUES(?,?,?,'attempted',?)",
                (preview['preview_sha256'],identity,revision,preview['request_characters']))
            return True

    def finish_audio(self,sha,state,data,reason=None):
        expiry=(datetime.now(timezone.utc)+timedelta(days=audio_cache.TTL_DAYS)).isoformat() if data else None
        digest=hashlib.sha256(data).hexdigest() if data else None
        with self.connect() as db:
            db.execute("UPDATE annotation_audio SET state=?,audio=?,reason=?,expires_at=?,audio_sha256=? WHERE preview_sha=? AND state='attempted'",
                       (state,data,reason,expiry,digest,sha))


class ProductRepository(SavedReplyRepository):
    def __init__(self,path,connect):
        self.database=Path(path).resolve();self.connect=connect;self.read_completion=self.proof
    def proof(self,message,owner):
        with self.connect() as db:
            row=db.execute('SELECT * FROM voice_ai_completion_receipts WHERE message_id=? AND owner_id=?',(message,owner)).fetchone()
        if not row:return None
        value=dict(row);value.pop('answer_binding')
        value['model_completed']=bool(value['model_completed']);value['stream_ended']=bool(value['stream_ended'])
        return value
    def read(self,message,owner):
        actor=ACTOR.get()
        if not actor or actor['id']!=owner:raise AnnotationError('login_required',401)
        with self.connect() as db:
            session=db.execute('SELECT user_id,expires_at FROM auth_sessions WHERE token_hash=?',(actor['token_hash'],)).fetchone()
        if not session or session['user_id']!=owner or session['expires_at']<=datetime.now(timezone.utc).isoformat():
            raise AnnotationError('login_expired',401)
        return super().read(message,owner)


class ProductPlans(_ProductPlans):
    def __init__(self,store,repository,model,tts):
        self.store,self.repository,self.model,self.tts=store,repository,model,tts
        self.enabled,self.deadline,self.tasks=True,20,{}
        # Advisory delivery only, used after the owner's existing AI opt-in.
        # Never changes Profile/personality, settings, or an existing plan.
        self.performance_preferences=dict(CANDIDATE)

    def source(self,identity,owner,granularity='auto',mode='ai_expression'):
        prepared=super().source(identity,owner,granularity,mode)
        return apply_performance_preferences(prepared,
            self.performance_preferences if mode=='ai_expression' else None)


class SelectedModel:
    supports_output_cap=True
    def __init__(self,runtime):self.runtime=runtime
    def adapter(self):
        settings=self.runtime.settings(ACTOR.get()['id'])
        if not settings['enabled'] or settings['model_profile_id'] is None:
            raise AnnotationError('ai_planning_disabled',403)
        return ProfilePlanningAdapter(settings['model_profile_id'],self.runtime.resolve,self.runtime.token,
            enabled=True,transport=self.runtime.model_transport)
    def binding(self):return self.adapter().binding()
    async def generate(self,request):return await self.adapter().generate(request)


class ProductTTS(_BoundHTTP):
    def __init__(self,runtime):
        self.runtime=runtime;self.binding=ContextVar('product_ai_http_binding');self.service=None
        self.reserve=lambda owner,n:auto_budget.reserve(runtime.connect,ACTOR.get()['token_hash'],owner,n,first_request=True)
    def adapter(self):
        return ElevenLabsAdapter(self.runtime.tts_key(),transport=self.runtime.tts_transport)


class Runtime(HTTPReplyBridge):
    def __init__(self,path,connect,resolve,token,tts_key):
        self.connect,self.resolve,self.token,self.tts_key=connect,resolve,token,tts_key
        self.model_transport=self.tts_transport=None
        tts=ProductTTS(self)
        plans=ProductPlans(ProductStore(path),ProductRepository(path,connect),SelectedModel(self),tts)
        tts.service=plans
        super().__init__(Path(path).parent, None,None,None,None,plans=plans)
    def settings(self,owner):
        with self.connect() as db:
            row=db.execute('SELECT * FROM voice_ai_owner_settings WHERE owner_id=?',(owner,)).fetchone()
            profiles=[dict(r) for r in db.execute("SELECT id,name,model_name,protocol FROM model_profiles WHERE enabled=1 AND protocol IN('chatgpt_plan','openai_responses')")]
        return {'available':True,'enabled':bool(row['enabled']) if row else False,
                'model_profile_id':row['model_profile_id'] if row else None,'revision':row['revision'] if row else 0,'profiles':profiles,
                'performance_preferences':dict(self.plans.performance_preferences)}
    def status(self,identity,owner):
        value=super().status(identity,owner);value.pop('real_provider_requests',None)
        for item in value['audio']:item['url']=item['url'].replace('/api/voice-ai-http-lab','/api/voice/ai-http')
        row=self.plans.store.get(identity,owner)
        text=row['prepared']['safe_text']
        value['fragments']=[{**f,'text':text[f['start']:f['end']]} for f in row['prepared']['fragments']]
        from .annotation_planner import TAGS
        value['intent_tags']=dict(TAGS)
        value['performance_preferences']=row['prepared'].get('performance_preferences')
        with self.connect() as db:
            value['cached_versions']=[{'revision':r['revision'],'url':f"/api/voice/ai-http/plans/{identity}/audio/{r['preview_sha']}"} for r in db.execute("SELECT preview_sha,revision FROM annotation_audio WHERE plan_id=? AND state='ready' ORDER BY revision",(identity,))]
        return value
    def audio(self,identity,owner,sha):
        # Historical versions remain playable without resolving today's planner.
        row=self.plans.store.get(identity,owner)
        current=self.plans.repository.read(row['prepared']['source']['message_id'],owner)
        if fingerprint(asdict(current))!=fingerprint(row['prepared']['source']):raise AnnotationError('source_or_profile_changed',409)
        audio=self.plans.store.audio(sha)
        if not audio or audio['plan_id']!=identity or audio['state']!='ready':raise AnnotationError('audio_not_ready',404)
        if not audio['expires_at'] or audio['expires_at']<=datetime.now(timezone.utc).isoformat():raise AnnotationError('audio_expired',409)
        if hashlib.sha256(audio['audio']).hexdigest()!=audio['audio_sha256']:raise AnnotationError('audio_integrity',409)
        audio_cache.validate_mp3(audio['audio']);return audio['audio']

    def saved_audio(self,message,owner):
        """Read-only historical expression; never resolves or calls a planner.

        Turning planning off must stop new work, not invalidate a saved audio.
        Source/Voice/consent/session, expiry and integrity still apply.
        """
        source=self.plans.repository.read(message,owner)
        with self.connect() as db:
            identities=[r[0] for r in db.execute("SELECT id FROM annotation_plans WHERE owner_id=? AND json_extract(prepared,'$.source.message_id')=? ORDER BY rowid DESC",(owner,message))]
        for identity in identities:
            row=self.plans.store.get(identity,owner)
            if fingerprint(asdict(source))!=fingerprint(row['prepared']['source']):continue
            with self.connect() as db:
                audios=[dict(r) for r in db.execute("SELECT preview_sha,revision FROM annotation_audio WHERE plan_id=? AND state='ready' AND expires_at>? ORDER BY revision DESC",(identity,datetime.now(timezone.utc).isoformat()))]
            if not audios:continue
            cached=[]
            for audio in audios:
                self.audio(identity,owner,audio['preview_sha'])
                cached.append({'revision':audio['revision'],'url':f"/api/voice/ai-http/plans/{identity}/audio/{audio['preview_sha']}"})
            revision=cached[0]['revision']
            selected=next(v for v in row['versions'] if v['revision']==revision)
            prepared=row['prepared']
            if selected['metadata']['fallback']:prepared={**prepared,'mode':'none'}
            preview=render(prepared,selected['annotations'],revision)
            preview.update(plan_id=identity,effective_mode=prepared['mode'],
                requested_mode=row['prepared']['mode'],fallback_reason=selected['metadata']['reason'])
            preview['preview_sha256']=fingerprint(preview)
            if preview['preview_sha256']!=audios[0]['preview_sha']:raise AnnotationError('audio_preview_binding_changed',409)
            from .annotation_planner import TAGS
            text=row['prepared']['safe_text']
            return {'id':identity,'revision':revision,'state':row['state'],'bridge_state':'completed',
                'error':None,'reason':None,'cache_read_only':True,'versions':row['versions'],
                'preview':preview,'audio':[cached[0]],'cached_versions':cached,
                'fragments':[{**f,'text':text[f['start']:f['end']]} for f in row['prepared']['fragments']],
                'intent_tags':dict(TAGS),'performance_preferences':row['prepared'].get('performance_preferences')}
        return None


class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool
    model_profile_id:StrictInt|None=None
    expected_revision:StrictInt
    confirm_planning:StrictBool=False


def install(app,connect,resolve,token,tts_key):
    runtime=None;reason='service_disabled'
    if enabled():
        with connect() as db:
            path=Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
            ready=schema_present(db)
        if not ready:reason='schema_missing'
        elif str(path)!=os.getenv('YOSAKI_VOICE_AI_HTTP_DATABASE'):reason='database_path_not_bound'
        else:runtime=Runtime(path,connect,resolve,token,tts_key);runtime.plans.store.recover()
    router=APIRouter(prefix='/api/voice/ai-http')
    def actor(request):
        if runtime is None:raise HTTPException(409,reason)
        return ACTOR.set(request.state.user)
    def convert(exc):return HTTPException(exc.status,exc.code)
    @router.get('/settings')
    async def settings(request:Request):
        return runtime.settings(request.state.user['id']) if runtime else {'available':False,'enabled':False,'revision':0,'model_profile_id':None,'profiles':[],'reason':reason}
    @router.put('/settings')
    async def save(payload:Settings,request:Request):
        scope=actor(request);owner=request.state.user['id']
        try:
            old=runtime.settings(owner)
            if payload.enabled and (not payload.confirm_planning or payload.model_profile_id not in [p['id'] for p in old['profiles']]):
                raise HTTPException(422,'请明确选择规划档案，并确认每条回复可能新增一次模型规划')
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT revision FROM voice_ai_owner_settings WHERE owner_id=?',(owner,)).fetchone()
                if (row[0] if row else 0)!=payload.expected_revision:raise HTTPException(409,'设置版本已变化')
                db.execute('INSERT INTO voice_ai_owner_settings VALUES(?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET enabled=excluded.enabled,model_profile_id=excluded.model_profile_id,revision=excluded.revision',
                    (owner,int(payload.enabled),payload.model_profile_id,payload.expected_revision+1))
            for identity in list(runtime.tasks):
                if runtime.owners[identity]==owner:await runtime.cancel(identity,owner)
            return runtime.settings(owner)
        finally:ACTOR.reset(scope)
    @router.get('/messages/{message}/state')
    async def state(message:int,request:Request):
        scope=actor(request);owner=request.state.user['id']
        try:
            prepared=runtime.source(message,owner,'ai_expression')
            with connect() as db:
                row=db.execute("SELECT id FROM annotation_plans WHERE owner_id=? AND input_sha=? LIMIT 1",(owner,prepared['input_sha256'])).fetchone()
            return {'eligible':True,'plan':runtime.status(row[0],owner) if row else None}
        except AnnotationError as exc:return {'eligible':False,'reason':exc.code,'plan':None}
        finally:ACTOR.reset(scope)
    @router.get('/messages/{message}/saved-audio')
    async def saved_audio(message:int,request:Request):
        scope=actor(request)
        try:return {'plan':runtime.saved_audio(message,request.state.user['id'])}
        except AnnotationError as exc:raise convert(exc)
        finally:ACTOR.reset(scope)
    @router.post('/messages/{message}/read')
    async def read(message:int,request:Request):
        scope=actor(request);owner=request.state.user['id']
        try:
            prepared=runtime.source(message,owner,'ai_expression')
            settings=runtime.settings(owner)
            payload=PlanRequest(operation_id=prepared['input_sha256'],input_sha256=prepared['input_sha256'],
                profile_revision=prepared['source']['profile_revision'],planning_consent=True,model_profile_id=settings['model_profile_id'])
            return runtime.start(message,owner,payload)
        except AnnotationError as exc:raise convert(exc)
        finally:ACTOR.reset(scope)
    @router.get('/plans/{identity}')
    async def status(identity:str,request:Request):
        scope=actor(request)
        try:
            value=runtime.status(identity,request.state.user['id'])
            for item in value['audio']:item['url']=item['url'].replace('/api/voice-ai-http-lab','/api/voice/ai-http')
            return value
        except AnnotationError as exc:raise convert(exc)
        finally:ACTOR.reset(scope)
    @router.patch('/plans/{identity}')
    async def edit(identity:str,payload:EditRequest,request:Request):
        scope=actor(request)
        try:return runtime.plans.edit(identity,request.state.user['id'],payload)
        except AnnotationError as exc:raise convert(exc)
        finally:ACTOR.reset(scope)
    @router.delete('/plans/{identity}')
    async def cancel(identity:str,request:Request):
        scope=actor(request)
        try:await runtime.cancel(identity,request.state.user['id']);return {'status':'cancelled'}
        except AnnotationError as exc:raise convert(exc)
        finally:ACTOR.reset(scope)
    @router.get('/plans/{identity}/audio/{sha}')
    async def audio(identity:str,sha:str,request:Request):
        scope=actor(request)
        try:return Response(runtime.audio(identity,request.state.user['id'],sha),media_type='audio/mpeg')
        except AnnotationError as exc:raise convert(exc)
        finally:ACTOR.reset(scope)
    app.include_router(router)
    if runtime:app.add_event_handler('shutdown',runtime.close)
    return runtime
