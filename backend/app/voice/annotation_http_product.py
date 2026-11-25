"""Step 4 isolated saved-reply -> existing planner -> HTTP -> versioned audio.

Not installed in main/router. Only fake transports are accepted, even when the
local feature is explicitly enabled. No credentials, daily schema or bindings.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request

from . import audio_cache, auto_budget, manager, storage
from .annotation_lab import AudioRequest, LabService, PlanRequest
from .annotation_planner import AnnotationError, PARAMETERS, Source, fingerprint, prepare
from .annotation_store import AnnotationStore
from .elevenlabs_adapter import ElevenLabsAdapter


@dataclass(frozen=True)
class CompletedSource(Source):
    generation_id: str = ''
    completion_sha256: str = ''


class SavedReplyRepository:
    """Existing saved-message checks plus a trusted backend completion proof.

    read_completion is a backend dependency, never accepted from a client.
    Future installation must supply it from the existing display-gated lifecycle.
    Here the application and proof are synthetic and the database is isolated.
    """
    def __init__(self, database: Path, connect, read_completion):
        project = Path(__file__).resolve().parents[3]
        self.database = database.resolve()
        if self.database.is_relative_to(project) or database.is_symlink():
            raise ValueError('isolated_saved_database_required')
        self.connect, self.read_completion = connect, read_completion

    def read(self, message: int, owner: int) -> Source:
        with self.connect() as db:
            if Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve() != self.database:
                raise AnnotationError('isolated_database_mismatch', 403)
            if not db.execute('SELECT 1 FROM users WHERE id=?', (owner,)).fetchone():
                raise AnnotationError('owner_not_found', 403)
        try:
            context = manager.speech_context(self.connect, message, owner, 'none')
        except HTTPException as exc:
            raise AnnotationError('saved_reply_not_eligible', exc.status_code) from None
        with self.connect() as db:
            row = db.execute('SELECT conversation_id,generation_status FROM messages WHERE id=?',
                             (message,)).fetchone()
            profile = storage.profile(db, context['character_id'])
            consent = storage.consent(db, owner)
        proof = self.read_completion(message, owner)
        expected = dict(owner_id=owner, character_id=context['character_id'],
            conversation_id=row['conversation_id'], message_id=message,
            body_sha256=hashlib.sha256(context['body'].encode()).hexdigest(),
            model_completed=True, stream_ended=True, display_action='allow')
        if (row['generation_status'] != 'completed' or not isinstance(proof, dict)
                or any(type(proof.get(k)) is not type(v) or proof[k] != v for k, v in expected.items())
                or not isinstance(proof.get('generation_id'), str) or not proof['generation_id']):
            raise AnnotationError('complete_display_binding_required', 409)
        return CompletedSource(owner, context['character_id'], row['conversation_id'], message,
            context['body'], profile_revision=context['revision'],
            voice_sha256=hashlib.sha256(context['voice_id'].encode()).hexdigest(),
            consent_version=consent['notice_version'], personality=profile['configuration']['personality'],
            generation_id=proof['generation_id'], completion_sha256=fingerprint(proof))

    def voice(self, message: int, owner: int) -> str:
        self.read(message, owner)
        return manager.speech_context(self.connect, message, owner, 'none')['voice_id']


class _BoundHTTP:
    mock_only = True

    def __init__(self, transport, reserve):
        if not isinstance(transport, (httpx.MockTransport, httpx.ASGITransport)):
            raise ValueError('isolated_http_requires_fake_transport')
        self.transport, self.reserve = transport, reserve
        self.binding = ContextVar('expression_http_binding')
        self.service = None

    def adapter(self):
        return ElevenLabsAdapter('synthetic-only', transport=self.transport)

    async def synthesize(self, text, parameters):
        if parameters != PARAMETERS or not 1 <= len(text) <= 2000:
            raise AnnotationError('single_japanese_http_parameters_required')
        identity, owner = self.binding.get()
        row = self.service.current(identity, owner)
        preview = self.service.preview(identity, owner)
        if preview['request_text'] != text:
            raise AnnotationError('rendered_text_binding_changed', 409)
        voice = self.service.repository.voice(row['prepared']['source']['message_id'], owner)
        if hashlib.sha256(voice.encode()).hexdigest() != row['prepared']['source']['voice_sha256']:
            raise AnnotationError('voice_binding_changed', 409)
        self.service.current(identity, owner)
        # Existing atomic daily-budget callback; a ready cache never enters here.
        try:
            self.reserve(owner, len(text))
        except (auto_budget.BudgetExhausted, auto_budget.BudgetUnavailable) as exc:
            reason = ('auto_budget_exhausted' if isinstance(exc, auto_budget.BudgetExhausted)
                      else 'auto_budget_unavailable')
            self.service.store.finish_audio(preview['preview_sha256'], 'failed', None, reason)
            raise AnnotationError(reason, 409) from None
        audio = await self.adapter().synthesize_http(text, voice, 'ja')
        audio_cache.validate_mp3(audio)
        return audio


class _ProductPlans(LabService):
    def source(self, identity, owner, granularity='auto', mode='ai_expression'):
        self.gate()
        binding = self.model.binding() if mode == 'ai_expression' and hasattr(self.model, 'binding') else None
        return prepare(self.repository.read(identity, owner), granularity, mode, model_binding=binding)

    async def synthesize(self, identity, owner, payload):
        token = self.tts.binding.set((identity, owner))
        try:
            return await super().synthesize(identity, owner, payload)
        finally:
            self.tts.binding.reset(token)


class HTTPReplyBridge:
    """One whole-reply plan, at most one HTTP synthesis, no fallback re-dispatch.

    Reuses Step 2's claims, validation, fallback and append-only revisions. TTS
    unknown is terminal; ordinary/manual selections do not invoke the planner.
    """
    def __init__(self, root, repository, model, transport, reserve, *, enabled=False, plans=None):
        self.root = Path(root)
        if plans is None:
            tts = _BoundHTTP(transport, reserve)
            self.plans = _ProductPlans(AnnotationStore(self.root / 'expression-http.sqlite3'),
                repository, model, tts, enabled=enabled)
            tts.service = self.plans
        else:
            self.plans = plans
        self.tasks = {}
        self.owners = {}
        self.errors = {}

    def source(self, message, owner, mode='none'):
        value = self.plans.source(message, owner, mode=mode)
        return {k: value[k] for k in ('safe_text', 'input_sha256', 'fragments', 'source', 'parameters', 'model')}

    def start(self, message, owner, payload):
        row = self.plans.start(owner, message, payload)
        identity = row['id']
        if identity not in self.tasks and row['state'] in ('planning', 'ready', 'empty', 'fallback'):
            self.owners[identity] = owner
            self.tasks[identity] = asyncio.create_task(self._finish(identity, owner))
        return self.status(identity, owner)

    async def _finish(self, identity, owner):
        try:
            task = self.plans.tasks.get(identity)
            if task:
                await asyncio.shield(task)
            row = self.plans.current(identity, owner)
            if row['state'] not in ('ready', 'empty', 'fallback'):
                return
            preview = self.plans.preview(identity, owner)
            await self.plans.synthesize(identity, owner, AudioRequest(
                revision=preview['plan_revision'], preview_sha256=preview['preview_sha256']))
        except asyncio.CancelledError:
            pass
        except AnnotationError as exc:
            self.errors[identity] = exc.code
        except Exception:
            self.errors[identity] = 'local_pipeline_unknown'
        finally:
            self.tasks.pop(identity, None)

    def status(self, identity, owner):
        row = self.plans.current(identity, owner)
        result = self.plans.view(row)
        result.update(bridge_state=row['state'], audio=[], preview=None,
                      error=self.errors.get(identity), real_provider_requests=0)
        if row['state'] in ('ready', 'empty', 'fallback'):
            preview = self.plans.preview(identity, owner)
            result['preview'] = preview
            result['bridge_state'] = ('completed' if preview['audio_state'] == 'ready' else
                'unknown' if preview['audio_state'] == 'failed_unknown' else
                'failed' if preview['audio_state'] == 'failed' else
                'synthesizing' if identity in self.tasks else 'not_generated')
            if preview['audio_state'] == 'failed':
                result['error'] = self.plans.store.audio(preview['preview_sha256'])['reason']
            if preview['audio_state'] == 'ready':
                result['audio'] = [{'sequence': 0, 'url':
                    f'/api/voice-ai-http-lab/plans/{identity}/audio/{preview["preview_sha256"]}'}]
        return result

    def audio(self, identity, owner, sha):
        row = self.plans.current(identity, owner)
        if row['state'] not in ('ready', 'empty', 'fallback'):
            raise AnnotationError('plan_not_playable', 409)
        audio = self.plans.store.audio(sha)
        if not audio or audio['plan_id'] != identity or audio['state'] != 'ready':
            raise AnnotationError('audio_not_ready', 404)
        audio_cache.validate_mp3(audio['audio'])
        return audio['audio']

    async def cancel(self, identity, owner):
        self.plans.store.get(identity, owner)
        self.plans.store.stop(identity, 'cancelled', 'explicit_cancel')
        tasks = [t for t in (self.tasks.get(identity), self.plans.tasks.get(identity)) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        for identity in list(self.tasks):
            await self.cancel(identity, self.owners[identity])
        await self.plans.close()


def create_app(bridge, *, message_ids=(), model_profile_id=None):
    """Separate authenticated synthetic app; no installation in daily routes."""
    from contextlib import asynccontextmanager
    import secrets
    from fastapi.responses import JSONResponse, Response

    prefix = '/api/voice-ai-http-lab'
    session = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        bridge.plans.store.recover()
        yield
        await bridge.close()

    app = FastAPI(lifespan=lifespan)
    app.state.bridge = bridge

    @app.exception_handler(AnnotationError)
    async def error(request, exc):
        return JSONResponse({'error': exc.code}, status_code=exc.status)

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        if request.headers.get('origin') not in (None, 'http://127.0.0.1:18897'):
            return JSONResponse({'error': 'origin_not_allowed'}, status_code=403)
        if request.url.path != prefix + '/session' and not secrets.compare_digest(
                request.cookies.get('ai_http_lab', ''), session):
            return JSONResponse({'error': 'login_required'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get(prefix + '/session')
    async def login():
        response = JSONResponse({'isolated': True, 'owner_id': 1})
        response.set_cookie('ai_http_lab', session, httponly=True, samesite='strict')
        return response

    @app.get(prefix + '/status')
    async def status():
        return {'enabled': bridge.plans.enabled, 'mock_only': True, 'messages': list(message_ids),
                'model_profile_id': model_profile_id, 'counters': bridge.plans.store.counters()}

    @app.get(prefix + '/messages/{message}/source')
    async def source(message: int, mode: str = 'none'):
        return bridge.source(message, 1, mode)

    @app.post(prefix + '/messages/{message}/read')
    async def start(message: int, payload: PlanRequest):
        return bridge.start(message, 1, payload)

    @app.get(prefix + '/plans/{identity}')
    async def result(identity: str):
        return bridge.status(identity, 1)

    @app.delete(prefix + '/plans/{identity}')
    async def cancel(identity: str):
        await bridge.cancel(identity, 1)
        return {'bridge_state': 'cancelled'}

    @app.get(prefix + '/plans/{identity}/audio/{sha}')
    async def audio(identity: str, sha: str):
        return Response(bridge.audio(identity, 1, sha), media_type='audio/mpeg')

    return app
