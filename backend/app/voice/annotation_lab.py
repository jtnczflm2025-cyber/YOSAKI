"""Opt-in network-denied lab, allowing existing adapters with fake transport only.

Not installed by the daily application. No credentials or OAuth are loaded.
All asynchronous results revalidate source and revision.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import io
import json
import math
from pathlib import Path
import secrets
import struct
import time
import wave

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .annotation_planner import (AnnotationError, Source, TAGS,
                                 fingerprint, prepare, render, validate)
from .annotation_store import AnnotationStore

PREFIX = "/api/voice-expression-lab"
FIXTURES = {
    101: ("整条零标注", "今夜は静かだ。\n\n急がなくていい。ここで少し休もう。"),
    102: ("只标中间一句", "窓の外は静かだ。少し考えてから話そう。今はここにいればいい。"),
    103: ("不同段落不同意图", "急がなくていい。静かに話そう。\n\n無理に笑わなくてもいい。今は休もう。"),
    104: ("相邻句不同意图", "少し考えてみよう。肩の力を抜いていい。今夜は静かに過ごそう。"),
}


class FixtureRepository:
    """Trusted synthetic saved replies, separate from all daily records."""
    def __init__(self):
        self.items = {identity: Source(1, 1, 1, identity, text)
                      for identity, (_, text) in FIXTURES.items()}

    def read(self, identity: int, owner: int) -> Source:
        source = self.items.get(identity)
        if source is None or source.owner_id != owner:
            raise AnnotationError("source_not_found", 404)
        return source


class FakeModel:
    mock_only = True
    supports_output_cap = True
    def __init__(self):
        self.requests = []

    async def generate(self, request: dict) -> tuple[object, dict]:
        self.requests.append(request)
        await asyncio.sleep(.05)
        value = json.loads(request["messages"][1]["content"])
        text = value["public_reply"]
        paragraphs = [f for f in value["fragments"] if f["kind"] == "paragraph"]
        sentences = [f for f in value["fragments"] if f["kind"] == "sentence"]
        choices = []
        if text == FIXTURES[102][1]:
            choices = [(sentences[1], "thoughtful")]
        elif text == FIXTURES[103][1]:
            choices = [(paragraphs[0], "thoughtful"), (paragraphs[1], "resigned")]
        elif text == FIXTURES[104][1]:
            choices = [(sentences[0], "thoughtful"), (sentences[1], "lazy")]
        if value["granularity"] == "paragraph":
            choices = [(paragraphs[i], intent) for i, (_, intent) in enumerate(choices)
                       if i < len(paragraphs)]
        elif value["granularity"] == "sentence":
            choices = [(next(f for f in sentences if f["start"] == fragment["start"]), intent)
                       for fragment, intent in choices]
        return {"schema_version": 1, "input_sha256": value["input_sha256"],
                "annotations": [{"fragment_id": fragment["fragment_id"], "intent": intent}
                                for fragment, intent in choices]}, {"input_tokens": 100,
                                "output_tokens": 30, "total_tokens": 130}


class FakeTTS:
    mock_only = True
    def __init__(self):
        self.requests = []

    async def synthesize(self, text: str, parameters: dict) -> bytes:
        self.requests.append({"text": text, "parameters": parameters})
        await asyncio.sleep(.05)
        # Audible local test tone, deliberately not fake "阿夜" speech or MP3.
        target = io.BytesIO()
        with wave.open(target, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"".join(struct.pack("<h", int(1500 * math.sin(i * .08)))
                                       for i in range(9600)))
        return target.getvalue()


class LabService:
    def __init__(self, store: AnnotationStore, repository=None, model=None, tts=None,
                 *, enabled=False, deadline=20):
        self.store = store
        self.repository = repository or FixtureRepository()
        self.model, self.tts = model or FakeModel(), tts or FakeTTS()
        if not getattr(self.model, "mock_only", False) or not getattr(self.tts, "mock_only", False):
            raise ValueError("lab_requires_non_network_providers")
        self.enabled, self.deadline = enabled, min(deadline, 20)
        self.tasks: dict[str, asyncio.Task] = {}

    def gate(self):
        if not self.enabled:
            raise AnnotationError("ai_expression_lab_disabled", 403)

    def source(self, identity: int, owner: int, granularity="auto", mode="ai_expression") -> dict:
        self.gate()
        binding = self.model.binding() if hasattr(self.model, "binding") else None
        return prepare(self.repository.read(identity, owner), granularity, mode, model_binding=binding)

    def current(self, identity: str, owner: int) -> dict:
        self.gate()
        row = self.store.get(identity, owner)
        old = row["prepared"]
        try:
            current = self.source(old["source"]["message_id"], owner, old["granularity"], old["mode"])
        except AnnotationError:
            self.store.stop(identity, "restricted", "source_access_lost")
            raise
        if current["input_sha256"] != row["input_sha"]:
            self.store.stop(identity, "stale", "binding_changed")
            raise AnnotationError("source_or_profile_changed", 409)
        return row

    def start(self, owner: int, message: int, payload) -> dict:
        prepared = self.source(message, owner, payload.granularity, payload.mode)
        if payload.input_sha256 != prepared["input_sha256"] or \
                payload.profile_revision != prepared["source"]["profile_revision"]:
            raise AnnotationError("preview_expired", 409)
        if payload.mode == "ai_expression" and not payload.planning_consent:
            raise AnnotationError("planning_consent_required", 403)
        if payload.mode == "ai_expression" and not getattr(self.model, "supports_output_cap", False):
            raise AnnotationError("model_output_cap_unsupported", 422)
        if prepared["model"].get("profile_id") is not None and payload.mode == "ai_expression" and \
                payload.model_profile_id != prepared["model"]["profile_id"]:
            raise AnnotationError("selected_model_profile_mismatch", 409)
        row, fresh = self.store.claim(owner, payload.operation_id, prepared)
        if fresh:
            if payload.mode != "ai_expression":
                self.store.finish(row["id"], "empty", [])
            else:
                self.tasks[row["id"]] = asyncio.create_task(self._plan(row["id"], owner))
        return self.view(self.store.get(row["id"], owner))

    async def _plan(self, identity: str, owner: int):
        try:
            row = self.current(identity, owner)
            prepared = row["prepared"]
            model_input = {**prepared["model_input"], "input_sha256": prepared["input_sha256"],
                           "granularity": prepared["granularity"]}
            instruction = model_input.pop("instruction")
            request = {"model": prepared["model"]["model"],
                       "messages": [{"role": "system", "content": instruction},
                                    {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)}],
                       "max_output_tokens": 512,
                       "total_timeout_seconds": self.deadline, "retries": 0}
            if prepared["model"].get("profile_id") is not None:
                request.update(model_profile_id=prepared["model"]["profile_id"], model_binding=prepared["model"])
            if request["max_output_tokens"] != 512 or not getattr(self.model, "supports_output_cap", False):
                raise AnnotationError("model_cap_missing")
            if sum(len(message["content"]) for message in request["messages"]) > 6000:
                raise AnnotationError("model_input_limit")
            self.store.model_started(identity)
            started = time.monotonic()
            async with asyncio.timeout(self.deadline):
                raw, usage = await self.model.generate(request)
            self.store.usage(identity, usage)
            if time.monotonic() - started > self.deadline:
                raise TimeoutError
            current = self.current(identity, owner)
            if current["state"] != "planning":
                return
            try:
                annotations = validate(current["prepared"], raw)
                render(current["prepared"], annotations, 1)
            except AnnotationError as exc:
                self.store.finish(identity, "fallback", [], exc.code)
                return
            self.store.finish(identity, "ready" if annotations else "empty", annotations)
        except asyncio.CancelledError:
            # Cancellation already persisted by cancel()/shutdown. No fallback.
            pass
        except AnnotationError as exc:
            current = self.store.get(identity, owner)
            if current["state"] == "planning":
                if current["model_attempts"] == 0:
                    self.store.finish(identity, "fallback", [], exc.code)
                else:
                    self.store.stop(identity, "failed_unknown", exc.code)
        except Exception as exc:
            # Safe class only; never stringify arbitrary exception/provider data.
            from .annotation_model import PlanningError
            current = self.store.get(identity, owner)
            if isinstance(exc, PlanningError):
                self.store.usage(identity, exc.usage)
                if current["state"] == "planning":
                    if exc.outcome in ("invalid", "failed", "not_dispatched"):
                        self.store.finish(identity, "fallback", [], exc.code)
                    else:
                        self.store.stop(identity, {"restricted": "restricted", "cancelled": "cancelled"}.get(
                            exc.outcome, "failed_unknown"), exc.code)
                return
            if current["state"] == "planning":
                code = "model_timeout" if isinstance(exc, TimeoutError) else "model_or_storage_unknown"
                self.store.stop(identity, "failed_unknown", code)
        finally:
            self.tasks.pop(identity, None)

    @staticmethod
    def view(row: dict) -> dict:
        return {k: row[k] for k in ("id", "state", "reason", "revision", "model_attempts", "usage", "versions")}

    def preview(self, identity: str, owner: int, revision: int | None = None) -> dict:
        row = self.current(identity, owner)
        if row["state"] not in ("ready", "empty", "fallback"):
            raise AnnotationError("plan_not_renderable", 409)
        revision = row["revision"] if revision is None else revision
        selected = next((r for r in row["versions"] if r["revision"] == revision), None)
        if selected is None:
            raise AnnotationError("revision_not_found", 404)
        prepared = row["prepared"]
        if selected["metadata"]["fallback"]:
            prepared = {**prepared, "mode": "none"}
        value = render(prepared, selected["annotations"], revision)
        value.update({"plan_id": identity, "effective_mode": prepared["mode"],
                      "requested_mode": row["prepared"]["mode"],
                      "fallback_reason": selected["metadata"]["reason"]})
        value["preview_sha256"] = fingerprint(value)
        value["historical"] = revision != row["revision"]
        audio = self.store.audio(value["preview_sha256"])
        value["audio_state"] = audio["state"] if audio else "not_generated"
        return value

    def edit(self, identity: str, owner: int, payload):
        row = self.current(identity, owner)
        if row["prepared"]["mode"] != "ai_expression":
            raise AnnotationError("manual_mode_not_ai_annotations", 409)
        annotations = validate(row["prepared"], {"schema_version": 1,
            "input_sha256": row["input_sha"], "annotations": payload.annotations})
        render(row["prepared"], annotations, payload.expected_revision + 1)
        self.store.edit(identity, payload.expected_revision, annotations)
        return self.view(self.store.get(identity, owner))

    async def synthesize(self, identity: str, owner: int, payload):
        preview = self.preview(identity, owner)
        if payload.revision != preview["plan_revision"] or payload.preview_sha256 != preview["preview_sha256"]:
            raise AnnotationError("audio_preview_expired", 409)
        fresh = self.store.claim_audio(identity, payload.revision, preview)
        if fresh:
            try:
                async with asyncio.timeout(30):
                    audio = await self.tts.synthesize(preview["request_text"], preview["parameters"])
                if len(audio) > 16 * 1024 * 1024 or not audio:
                    raise AnnotationError("audio_limit_or_empty")
                current = self.current(identity, owner)
                if current["state"] not in ("ready", "empty", "fallback") or current["revision"] != payload.revision:
                    self.store.finish_audio(preview["preview_sha256"], "cancelled", None, "late_audio")
                    raise AnnotationError("late_audio_rejected", 409)
                self.store.finish_audio(preview["preview_sha256"], "ready", audio)
            except BaseException:
                self.store.finish_audio(preview["preview_sha256"], "failed_unknown", None, "audio_result_unknown")
                raise AnnotationError("audio_result_unknown", 409) from None
        audio = self.store.audio(preview["preview_sha256"])
        if audio["state"] != "ready":
            raise AnnotationError("audio_already_attempted_" + audio["state"], 409)
        return {"preview_sha256": preview["preview_sha256"], "state": "ready", "cached": not fresh}

    def cancel(self, identity: str, owner: int):
        self.current(identity, owner)
        self.store.stop(identity, "cancelled", "explicit_cancel")
        task = self.tasks.get(identity)
        if task:
            task.cancel()

    async def close(self):
        for identity, task in list(self.tasks.items()):
            self.store.stop(identity, "cancelled", "lab_shutdown")
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_revision: StrictInt
    planning_consent: StrictBool = False
    granularity: str = "auto"
    mode: str = "ai_expression"
    model_profile_id: StrictInt | None = None


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt
    annotations: list[dict] = Field(max_length=8)


class AudioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt
    preview_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def create_app(data_dir: Path, *, enabled=False, repository=None, model=None, tts=None,
               deadline=20, allowed_origins=("http://127.0.0.1:18889",)) -> FastAPI:
    service = LabService(AnnotationStore(data_dir / "expression-lab.sqlite3"),
                         repository, model, tts, enabled=enabled, deadline=deadline)
    session = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_app):
        service.store.recover()
        yield
        await service.close()

    app = FastAPI(lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(AnnotationError)
    async def annotation_error(_request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD") and request.headers.get("origin") not in (None, *allowed_origins):
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        if request.url.path not in (PREFIX + "/session", PREFIX + "/status"):
            if not secrets.compare_digest(request.cookies.get("expression_lab", ""), session):
                return JSONResponse({"error": "login_required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get(PREFIX + "/session")
    async def bootstrap():
        response = JSONResponse({"owner_id": 1, "isolated": True})
        response.set_cookie("expression_lab", session, httponly=True, samesite="strict")
        return response

    @app.get(PREFIX + "/status")
    async def status():
        return {"enabled": service.enabled, "mock_only": True,
                "counters": service.store.counters(), "intents": list(TAGS),
                "fixtures": [{"id": k, "label": v[0]} for k, v in FIXTURES.items()]}

    @app.get(PREFIX + "/messages/{message}/expression-source")
    async def source(message: int, granularity="auto", mode="ai_expression"):
        value = service.source(message, 1, granularity, mode)
        return {k: value[k] for k in ("safe_text", "fragments", "input_sha256", "source", "parameters")}

    @app.post(PREFIX + "/messages/{message}/expression-plans")
    async def start(message: int, payload: PlanRequest):
        return service.start(1, message, payload)

    @app.get(PREFIX + "/expression-plans/{identity}")
    async def get(identity: str):
        return service.view(service.current(identity, 1))

    @app.patch(PREFIX + "/expression-plans/{identity}")
    async def edit(identity: str, payload: EditRequest):
        return service.edit(identity, 1, payload)

    @app.delete(PREFIX + "/expression-plans/{identity}")
    async def cancel(identity: str):
        service.cancel(identity, 1)
        return {"state": "cancelled"}

    @app.get(PREFIX + "/expression-plans/{identity}/speech-preview")
    async def preview(identity: str, revision: int | None = None):
        return service.preview(identity, 1, revision)

    @app.post(PREFIX + "/expression-plans/{identity}/speak")
    async def synthesize(identity: str, payload: AudioRequest):
        return await service.synthesize(identity, 1, payload)

    @app.get(PREFIX + "/expression-plans/{identity}/audio/{sha}")
    async def cached(identity: str, sha: str):
        row = service.current(identity, 1)
        if row["state"] not in ("ready", "empty", "fallback"):
            raise AnnotationError("plan_not_playable", 409)
        audio = service.store.audio(sha)
        if audio is None or audio["plan_id"] != identity or audio["state"] != "ready":
            raise AnnotationError("audio_not_ready", 404)
        return Response(audio["audio"], media_type="audio/wav")

    return app
