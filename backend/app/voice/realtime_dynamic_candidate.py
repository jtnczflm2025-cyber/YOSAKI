"""Default-off isolated product route for one bounded real dynamic turn.

The preview and dispatch call the identical frozen model-request builder. A
durable whole-turn receipt is occupied before the first model request. No
approved real receipt or real connector is created at module import/startup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from . import config, realtime_dynamic_candidate_receipt as receipts, storage
from .realtime_chat_bridge import BridgeLimits, ChatSpeechBridge, consume_chat_sse
from .realtime_dynamic_context import FrozenRequest, freeze
from .realtime_dynamic_diagnostics import (
    DiagnosticCapacityError, DiagnosticWriteError, RunDiagnostics,
    bounded_event_type,
)
from .realtime_answer_display import AnswerDisplayGate
from .realtime_output import OutputLimits
from .realtime_ttd_connector import RealTTDConnector


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def network_proxy() -> str | None:
    """Freeze the existing local proxy policy without changing system settings."""
    proxies = urllib.request.getproxies()
    http, https = proxies.get("http"), proxies.get("https")
    if http and https and http != https:
        raise ValueError("proxy_routes_disagree")
    target = https or http
    if not target:
        return None
    url = urlsplit(target if "://" in target else "http://" + target)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or \
            url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("proxy_configuration_unsupported")
    return f"http://127.0.0.1:{url.port}"


def _read_only_plan_token(source_dir: Path) -> str:
    """Read a still-valid token without refresh or writing the daily files."""
    from .realtime_dynamic_candidate_receipt import ROOT
    if source_dir.resolve() != (ROOT / "data").resolve():
        raise ValueError("credential_source_path_invalid")
    key = os.getenv("YOSAKI_SECRET_KEY")
    raw_key = key.encode("utf-8") if key else (source_dir / ".secret_key").read_bytes()
    record = json.loads(Fernet(raw_key).decrypt((source_dir / ".chatgpt_plan").read_bytes()))
    account = record.get("account") or {}
    token = account.get("access_token")
    if not isinstance(token, str) or not token or \
            "chatgpt.tokens.use.direct" not in account.get("scopes", []) or \
            float(account.get("expires_at") or 0) <= time.time() + 90:
        raise ValueError("chatgpt_plan_token_not_fresh")
    return token


class CandidateGate:
    def __init__(self, connect, session_lookup: Callable, origins: list[str],
                 saved_receipt: Callable, build_messages: Callable,
                 plan_payload: Callable, receipt_dir: Path, run_id: str) -> None:
        self.connect = connect
        self.session_lookup = session_lookup
        self.origins = frozenset(origins)
        self.saved_receipt = saved_receipt
        self.build_messages = build_messages
        self.plan_payload = plan_payload
        self.receipt_dir = receipt_dir
        self.run_id = run_id
        self.model_events_factory: Callable[[FrozenRequest], Any] | None = None
        self.ttd_connector_factory: Callable[[str], Any] | None = None
        self.active_owners: set[int] = set()
        self.lock = asyncio.Lock()

    @staticmethod
    def enabled() -> bool:
        return (os.getenv("YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED", "0") == "1" and
                os.getenv("YOSAKI_BACKGROUND_TASKS_ENABLED", "1") == "0" and
                config.enabled())

    @staticmethod
    def real_enabled() -> bool:
        return os.getenv("YOSAKI_V4_DYNAMIC_REAL_EXECUTE", "0") == "1"

    @staticmethod
    def reviewed_display_enabled() -> bool:
        return os.getenv("YOSAKI_V4_REVIEWED_DISPLAY_ENABLED", "0") == "1"

    def binding(self, ws_or_request, owner_id: int, character_id: int,
                conversation_id: int) -> tuple[dict, dict]:
        session = self.session_lookup(ws_or_request)
        if session is None or session["id"] != owner_id:
            raise ValueError("session_revoked")
        with self.connect() as db:
            row = db.execute("SELECT character_id FROM conversations WHERE id=?",
                             (conversation_id,)).fetchone()
            if row is None or row["character_id"] != character_id:
                raise ValueError("conversation_binding_invalid")
            profile = storage.profile(db, character_id)
            consent = storage.consent(db, owner_id)
        if not profile or not profile["active"] or not consent or \
                consent["notice_version"] != config.NOTICE_VERSION:
            raise ValueError("voice_authorization_invalid")
        conf = profile["configuration"]
        if conf["model_realtime"] != "eleven_v4_turbo" or conf["language"] != "ja":
            raise ValueError("voice_configuration_unsupported")
        return profile, consent

    def frozen(self, manifest: dict) -> FrozenRequest:
        with self.connect() as db:
            return freeze(db, owner_id=manifest["owner_id"],
                character_id=manifest["character_id"],
                conversation_id=manifest["conversation_id"],
                model_profile_id=manifest["model_profile_id"],
                prompt=manifest["prompt"],
                max_output_tokens=manifest["limits"]["max_output_tokens"],
                build_messages=self.build_messages,
                plan_payload=self.plan_payload)

    def prepared(self, ws_or_request, owner_id: int, character_id: int,
                 conversation_id: int) -> tuple[receipts.Prepared, FrozenRequest, dict]:
        prepared = receipts.prepare(self.receipt_dir, self.run_id)
        m = prepared.manifest
        if (m["owner_id"], m["character_id"], m["conversation_id"]) != \
                (owner_id, character_id, conversation_id):
            raise ValueError("receipt_identity_changed")
        profile, consent = self.binding(ws_or_request, owner_id, character_id,
                                        conversation_id)
        frozen = self.frozen(m)
        expected = {"context_sha256": frozen.context_sha256,
            "profile_revision": profile["revision"],
            "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
            "notice_version": consent["notice_version"],
            "chat_provider": frozen.provider, "chat_model_id": frozen.model,
            "tts_model_id": profile["configuration"]["model_realtime"],
            "output_format": "pcm_24000",
            "language_code": profile["configuration"]["language"],
            "network_proxy_url": network_proxy()}
        if any(m[key] != value for key, value in expected.items()):
            raise ValueError("receipt_binding_changed")
        if prepared.provider_kind == "real_approved" and not self.real_enabled():
            raise ValueError("real_execution_disabled")
        if prepared.provider_kind == "real_approved" and not self.reviewed_display_enabled():
            raise ValueError("reviewed_display_disabled")
        return prepared, frozen, profile

    def still_authorized(self, ws: WebSocket, manifest: dict) -> bool:
        try:
            profile, consent = self.binding(ws, manifest["owner_id"],
                manifest["character_id"], manifest["conversation_id"])
            return (profile["revision"] == manifest["profile_revision"] and
                hashlib.sha256(profile["voice_id"].encode()).hexdigest() ==
                    manifest["voice_id_sha256"] and
                consent["notice_version"] == manifest["notice_version"])
        except (ValueError, sqlite3.Error, KeyError):
            return False

    def verify_saved(self, manifest: dict, data: dict) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT m.id,m.role,m.conversation_id,m.generation_status,"
                "c.character_id FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE m.id=?", (data.get("message_id"),)).fetchone()
            if not row or row["role"] != "assistant" or row["conversation_id"] != manifest["conversation_id"] or \
                    row["character_id"] != manifest["character_id"] or \
                    row["generation_status"] != "completed":
                return False
            saved = self.saved_receipt(db, row["id"])
        return (saved["message_version_sha256"] == data.get("message_version_sha256") and
                saved["public_text_sha256"] == data.get("public_text_sha256"))

    async def reserve(self, owner_id: int) -> bool:
        async with self.lock:
            if owner_id in self.active_owners:
                return False
            self.active_owners.add(owner_id)
            return True

    async def release(self, owner_id: int) -> None:
        async with self.lock:
            self.active_owners.discard(owner_id)

    async def real_model_events(self, frozen: FrozenRequest):
        from backend.app import responses_adapter
        token = _read_only_plan_token(Path(os.environ["YOSAKI_V4_SOURCE_DATA_DIR"]))
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5),
                                     follow_redirects=False) as client:
            async with client.stream("POST", "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                    json=frozen.body) as response:
                if response.status_code != 200:
                    raise ValueError(f"model_http_{response.status_code}")
                async for event in responses_adapter.iter_sse_events(
                        response.aiter_lines()):
                    yield event

    async def chat_source(self, prepared: receipts.Prepared,
                          frozen: FrozenRequest, budget: receipts.TurnBudget,
                           cancel: asyncio.Event, ws: WebSocket,
                           diagnostic: RunDiagnostics | None = None,
                           cancel_source: dict[str, str] | None = None,
                           usage_sink: dict[str, dict[str, int]] | None = None):
        from backend.app import responses_adapter
        m = prepared.manifest
        generation_id = m["generation_id"]
        yield _frame("message_start", {"owner_id": m["owner_id"],
            "character_id": m["character_id"], "conversation_id": m["conversation_id"],
            "generation_id": generation_id, "model": frozen.model,
            "mode": "isolated_dynamic_candidate"})
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,created_at) "
                       "VALUES (?,'user',?,datetime('now'))",
                       (m["conversation_id"], m["prompt"]))
        state = responses_adapter.StreamState()
        reviewed = self.reviewed_display_enabled()
        display_gate = AnswerDisplayGate() if reviewed else None
        display_result = None
        public = ""
        status = "failed"
        reason = "model_unknown"
        model_usage: dict[str, int] | None = None
        failure_stage = "model_preflight"
        try:
            if not self.still_authorized(ws, m) or \
                    self.frozen(m).context_sha256 != frozen.context_sha256:
                raise ValueError("authorization_or_context_changed_before_model")
            budget.reserve_model()
            if diagnostic:
                diagnostic.record("model_request_attempt", stage="model_dispatch",
                                  model_generations=budget.model_attempts)
            source = (self.model_events_factory(frozen) if self.model_events_factory
                      else self.real_model_events(frozen))
            async with asyncio.timeout(receipts.LIMITS["model_seconds"]):
                async for event in source:
                    failure_stage = "model_stream"
                    if cancel.is_set():
                        status, reason = "stopped", (cancel_source or {}).get(
                            "source", "generation_cancelled")
                        break
                    if not isinstance(event, dict):
                        raise ValueError("model_event_invalid")
                    kind = event.get("type")
                    response_data = event.get("response") or {}
                    if not isinstance(response_data, dict):
                        raise ValueError("model_event_invalid")
                    if reviewed and state.completed and kind in {
                            "response.output_text.delta", "response.output_text.done"}:
                        raise ValueError("model_event_after_completion")
                    raw_usage = (response_data.get("usage") if kind in
                                 responses_adapter.CANDIDATE_TERMINAL else None)
                    if isinstance(raw_usage, dict):
                        observed = {key: raw_usage[key] for key in
                            ("input_tokens", "output_tokens", "total_tokens")
                            if type(raw_usage.get(key)) is int and raw_usage[key] >= 0}
                        if observed:
                            model_usage = observed
                            if usage_sink is not None:
                                usage_sink["value"] = observed
                            if diagnostic:
                                diagnostic.record("model_usage", usage=observed)
                    classification = responses_adapter.classify_candidate_event(event)
                    if diagnostic:
                        diagnostic.record("model_event", stage="model_stream",
                            event_layer=("local_fixture_event" if self.model_events_factory
                                         else "provider_sse_json"),
                            parse_branch=classification,
                            **bounded_event_type(kind))
                    if classification == "unsupported":
                        raise ValueError("model_event_unknown")
                    if display_gate is not None:
                        moderation = display_gate.observe(event)
                        if moderation is not None and diagnostic:
                            diagnostic.record("moderation_event", **moderation.diagnostic())
                        if classification == "metadata_answer_moderation":
                            # Keep reading for the explicit model terminal and usage.
                            # No public text has been sent to UI or TTD yet.
                            continue
                    if classification in responses_adapter.METADATA_STOPS:
                        raise ValueError("model_metadata_unrecognized" if
                            classification == "metadata_unrecognized" else
                            "model_metadata_control_unsupported")
                    if classification == "notice":
                        continue
                    if kind == "error":
                        status, reason = "failed", "provider_stream_error"
                        if diagnostic:
                            code = event.get("code")
                            diagnostic.record("model_failure", stage="provider_event",
                                event_layer=("local_fixture_event" if self.model_events_factory
                                             else "provider_sse_json"),
                                parse_branch="provider_error",
                                error_category="provider_error",
                                error_code=(code if isinstance(code, str) and code in {"invalid_api_key",
                                    "insufficient_quota", "rate_limit_exceeded",
                                    "model_not_found", "server_error"} else "unknown"))
                        break
                    if kind == "response.failed":
                        status, reason = "failed", "provider_reported_failure"
                        if diagnostic:
                            error = (event.get("response") or {}).get("error") or {}
                            code = error.get("code") if isinstance(error, dict) else None
                            diagnostic.record("model_failure", stage="provider_event",
                                error_code=code if isinstance(code, str) and code in {"invalid_api_key",
                                "insufficient_quota", "rate_limit_exceeded",
                                "model_not_found", "server_error"} else "unknown")
                        break
                    if kind == "response.incomplete":
                        status, reason = "incomplete", "model_output_incomplete"
                        break
                    if kind == "response.completed":
                        usage = (event.get("response") or {}).get("usage") or {}
                        if int(usage.get("output_tokens") or 0) > receipts.LIMITS["max_output_tokens"]:
                            raise ValueError("model_output_budget_exceeded")
                    normalized = state.normalize(event)
                    choice = normalized["choices"][0]
                    delta = choice["delta"]
                    if delta.get("tool_calls"):
                        reason = "unexpected_tool_call"
                        break
                    visible = delta.get("content") or ""
                    if visible:
                        public += visible
                        if len(public) > 500:
                            reason = "model_public_text_limit"
                            break
                        if not reviewed:
                            yield _frame("text_delta", {"text": visible})
                    if kind == "response.completed":
                        status, reason = "completed", "model_complete"
                        if not reviewed:
                            break
            if state.completed and status not in {"stopped", "failed", "incomplete"}:
                status, reason = "completed", "model_complete"
            elif status == "failed" and reason == "model_unknown":
                status, reason = "incomplete", "model_stream_without_completion"
        except asyncio.CancelledError:
            status, reason = "stopped", (cancel_source or {}).get(
                "source", "generation_cancelled")
            if diagnostic:
                diagnostic.record("cancel", stage=failure_stage,
                                  cancel_source=reason)
            raise
        except Exception as exc:
            if status != "incomplete":
                code = str(exc)
                if isinstance(exc, TimeoutError):
                    reason = "model_timeout"
                elif isinstance(exc, httpx.TimeoutException):
                    reason = "model_transport_timeout"
                elif isinstance(exc, responses_adapter.ResponseSSEDecodeError):
                    reason = exc.code
                    failure_stage = "model_sse_decode"
                elif isinstance(exc, ValueError) and code.startswith("model_http_") and \
                        code[11:] in {"400", "401", "403", "429", "500", "502", "503"}:
                    reason = code
                elif code in {"model_event_invalid", "model_event_unknown",
                              "model_metadata_unrecognized",
                              "model_metadata_control_unsupported",
                              "model_output_budget_exceeded", "unexpected_tool_call",
                              "model_public_text_limit", "model_event_after_completion",
                              "authorization_or_context_changed_before_model"}:
                    reason = code
                elif isinstance(exc, httpx.HTTPError):
                    reason = "model_transport_error"
                elif isinstance(exc, (KeyError, ValueError, TypeError, AttributeError)):
                    reason = "model_parse_error"
                else:
                    reason = "model_local_error"
                status = "failed"
                if diagnostic:
                    diagnostic.record("model_failure", stage=failure_stage,
                        event_layer=("provider_sse_frame" if isinstance(exc,
                            responses_adapter.ResponseSSEDecodeError) else
                            "local_fixture_event" if self.model_events_factory else
                            "provider_sse_json"),
                        parse_branch=("sse_decode" if isinstance(exc,
                            responses_adapter.ResponseSSEDecodeError) else
                            "candidate_classification" if reason in {
                                "model_event_unknown", "model_metadata_unrecognized",
                                "model_metadata_control_unsupported"} else
                            "model_execution"),
                        error_category=("wire_format" if isinstance(exc,
                            responses_adapter.ResponseSSEDecodeError) else
                            "unknown_event" if reason == "model_event_unknown" else
                            "metadata_unrecognized" if reason == "model_metadata_unrecognized" else
                            "metadata_control" if reason == "model_metadata_control_unsupported" else
                            "model_failure"),
                        **(bounded_event_type(exc.event_type) if isinstance(exc,
                            responses_adapter.ResponseSSEDecodeError) else {}),
                        error_code=reason, exception_type=(type(exc).__name__ if
                            type(exc).__name__ in {"TimeoutError", "ValueError",
                            "KeyError", "TypeError", "AttributeError", "ConnectError",
                            "ReadError", "RemoteProtocolError", "OSError",
                            "ConnectTimeout", "ReadTimeout", "WriteTimeout",
                            "PoolTimeout", "ResponseSSEDecodeError"} else "Other"),
                        timeout_type=("model_generation" if isinstance(exc, TimeoutError)
                            else type(exc).__name__ if isinstance(exc, httpx.TimeoutException)
                            else None))
        finally:
            model_status = status
            if display_gate is not None:
                display_result = display_gate.finish(model_completed=(status == "completed"),
                                                     public_text=public)
                if diagnostic:
                    try:
                        diagnostic.record("display_terminal", **display_result.diagnostic())
                    except (DiagnosticWriteError, DiagnosticCapacityError):
                        pass
                if status == "completed" and display_result.action != "allow":
                    status, reason = "restricted", display_result.reason
            if diagnostic:
                try:
                    diagnostic.record("model_terminal", stage=failure_stage,
                        model_status=model_status, finish_reason=("model_complete" if
                            model_status == "completed" else reason), usage=model_usage,
                        public_characters=len(public),
                        public_sha256=hashlib.sha256(public.encode()).hexdigest())
                except (DiagnosticWriteError, DiagnosticCapacityError):
                    # Diagnostic disk failure cannot erase a completed text reply.
                    pass
            try:
                saved = {"message_id": None, "message_version_sha256": None,
                         "public_text_sha256": None}
                if not reviewed or (display_result is not None and
                                    display_result.action == "allow" and status == "completed"):
                    with self.connect() as db:
                        assistant_id = db.execute("INSERT INTO messages(conversation_id,role,content,"
                            "model,created_at,generation_status,usage_json) "
                            "VALUES (?,'assistant',?,?,datetime('now'),?,?)",
                            (m["conversation_id"], public, frozen.model, status,
                             json.dumps(model_usage) if model_usage is not None else None)).lastrowid
                        saved = self.saved_receipt(db, assistant_id)
                    if diagnostic:
                        try:
                            diagnostic.record("message_saved", stage="message_save",
                                message_id=assistant_id, message_status=status)
                        except (DiagnosticWriteError, DiagnosticCapacityError):
                            pass
            except sqlite3.Error as exc:
                if diagnostic:
                    diagnostic.record("message_save_failed", stage="message_save",
                        exception_type=(type(exc).__name__ if type(exc).__name__ in {
                            "OperationalError", "IntegrityError", "DatabaseError",
                            "ProgrammingError"} else "Other"),
                        error_code="sqlite_error")
                raise
            # Async-generator cancellation cannot reliably yield here. The row
            # remains a partial/stopped record and the receipt remains occupied.
        if reviewed and display_result is not None and display_result.action == "allow" and \
                status == "completed" and public:
            yield _frame("text_delta", {"text": public})
        yield _frame("message_complete", {"conversation_id": m["conversation_id"],
            "generation_id": generation_id, "generation_status": status,
            "model_status": model_status, "display_status": (display_result.action if
                display_result is not None else "legacy_unreviewed"),
            "finish_reason": reason, "model": frozen.model,
            "model_usage": model_usage, **saved})


def install(app, connect, session_lookup: Callable, origins: list[str],
            saved_receipt: Callable, build_messages: Callable,
            plan_payload: Callable) -> CandidateGate:
    root = Path(os.environ.get("YOSAKI_DATA_DIR", "")).resolve()
    project_data = Path(__file__).resolve().parents[3] / "data"
    if not CandidateGate.enabled() or not os.environ.get("YOSAKI_DATA_DIR") or \
            root == project_data.resolve():
        raise ValueError("candidate_isolation_or_switch_invalid")
    gate = CandidateGate(connect, session_lookup, origins, saved_receipt,
        build_messages, plan_payload,
        Path(os.getenv("YOSAKI_V4_CANDIDATE_RECEIPT_DIR", "")),
        os.getenv("YOSAKI_V4_CANDIDATE_RUN_ID", ""))
    router = APIRouter(tags=["voice-v4-dynamic-candidate"])

    @router.get("/api/voice/realtime/chat-candidate/preview")
    def preview(request: Request, character_id: int, conversation_id: int) -> dict:
        if not gate.enabled():
            raise HTTPException(503, "candidate_disabled")
        session = gate.session_lookup(request)
        if session is None:
            raise HTTPException(401, "login_required")
        try:
            prepared, frozen, _ = gate.prepared(request, session["id"],
                                                  character_id, conversation_id)
        except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
            raise HTTPException(409, str(exc)[:100]) from exc
        return {"run_id": gate.run_id, "generation_id": prepared.manifest["generation_id"],
            "manifest_sha256": prepared.manifest_sha256,
            "code_sha256": prepared.code_sha256,
            "receipt_kind": prepared.provider_kind,
            "approved_for_real_execution": prepared.provider_kind == "real_approved",
            "model_request": frozen.public(), "limits": prepared.manifest["limits"]}

    @router.websocket("/api/voice/realtime/chat-candidate")
    async def socket(ws: WebSocket) -> None:
        if not gate.enabled() or ws.headers.get("origin") not in gate.origins:
            await ws.close(code=1008)
            return
        session = gate.session_lookup(ws)
        if session is None:
            await ws.close(code=1008)
            return
        await ws.accept()
        owner_id = session["id"]
        request_id = ""
        bridge: ChatSpeechBridge | None = None
        model_task: asyncio.Task | None = None
        reserved = False
        playback_ended = False
        model_usage: dict[str, int] | None = None
        observed_model_usage: dict[str, dict[str, int]] = {}
        cancel = asyncio.Event()
        cancel_source: dict[str, str] = {}
        diagnostic: RunDiagnostics | None = None
        budget: receipts.TurnBudget | None = None
        diagnostic_closed = False
        summary_recorded = False
        model_task_handled = False
        claimed = False
        try:
            async def reject(reason: str) -> None:
                await ws.send_json({"type": "input_rejected", "request_id": request_id,
                                    "reason": reason})
            try:
                start = await asyncio.wait_for(ws.receive_json(), timeout=5)
            except (TimeoutError, ValueError):
                await reject("start_required")
                return
            if not isinstance(start, dict) or set(start) != {
                    "type", "request_id", "run_id", "character_id", "conversation_id"} or \
                    start.get("type") != "start_chat" or start.get("run_id") != gate.run_id or \
                    any(type(start.get(key)) is not int or start[key] < 1 for key in
                        ("character_id", "conversation_id")):
                await reject("invalid_start")
                return
            try:
                request_id = str(uuid.UUID(start["request_id"]))
            except (KeyError, TypeError, ValueError):
                await reject("invalid_request_id")
                return
            if request_id != start["request_id"]:
                await reject("invalid_request_id")
                return
            try:
                prepared, frozen, profile = gate.prepared(ws, owner_id,
                    start["character_id"], start["conversation_id"])
            except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
                await reject(str(exc)[:100])
                return
            if prepared.provider_kind == "loopback_fake" and \
                    (gate.model_events_factory is None or gate.ttd_connector_factory is None):
                await reject("fake_connectors_missing")
                return
            if prepared.provider_kind == "real_approved" and \
                    (gate.model_events_factory is not None or gate.ttd_connector_factory is not None):
                await reject("real_receipt_with_mock_connector")
                return
            if prepared.provider_kind == "real_approved":
                try:
                    if not os.getenv("ELEVENLABS_API_KEY"):
                        raise ValueError("elevenlabs_key_not_configured")
                    _read_only_plan_token(Path(os.environ["YOSAKI_V4_SOURCE_DATA_DIR"]))
                    RealTTDConnector(os.environ["ELEVENLABS_API_KEY"],
                        prepared.manifest["network_proxy_url"])
                except Exception:
                    await reject("real_credential_or_proxy_preflight_failed")
                    return
            if not await gate.reserve(owner_id):
                await reject("candidate_turn_busy")
                return
            reserved = True
            # Rebuild from the same production functions immediately before
            # occupying the durable receipt; no model or TTD connection yet.
            try:
                latest, current, current_profile = gate.prepared(ws, owner_id,
                    start["character_id"], start["conversation_id"])
                if latest != prepared or current.context_sha256 != frozen.context_sha256 or \
                        current_profile["revision"] != profile["revision"]:
                    raise ValueError("context_or_profile_drift")
                receipts.claim(prepared)
                claimed = True
            except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
                await reject(str(exc)[:100])
                return
            diagnostic = RunDiagnostics(gate.receipt_dir, prepared.manifest["run_id"],
                                        prepared.manifest["generation_id"])
            budget = receipts.TurnBudget(prepared)
            first_tts_at: float | None = None
            def connector():
                nonlocal first_tts_at
                if not gate.still_authorized(ws, m) or not gate.enabled():
                    raise PermissionError("voice_authorization_changed")
                if first_tts_at is None:
                    first_tts_at = time.monotonic()
                if time.monotonic() - first_tts_at > receipts.LIMITS["tts_receive_seconds"]:
                    raise receipts.ReceiptError("tts_receive_time_budget")
                budget.reserve_connection()
                if prepared.provider_kind == "loopback_fake":
                    result = gate.ttd_connector_factory(profile["voice_id"])
                    if getattr(result, "local_fake_provider", False) is not True:
                        raise PermissionError("mock_receipt_requires_fake_ttd")
                    return result
                return RealTTDConnector(os.environ["ELEVENLABS_API_KEY"],
                    m["network_proxy_url"])()

            async def emit(event: dict) -> None:
                if diagnostic:
                    kind = event.get("type")
                    common = {"connection_id": event.get("connection_id"),
                              "connection_epoch": event.get("connection_epoch")}
                    if kind == "connection_attempt":
                        diagnostic.record("connection_attempt", **common,
                            attempt=event.get("attempt"),
                            tts_connections=budget.connections)
                    elif kind == "connection_ready":
                        diagnostic.record("connection_ready", **common,
                            attempt=event.get("attempt"))
                    elif kind == "text_dispatch_attempt":
                        diagnostic.record("text_send_attempt", **common,
                            characters=event.get("characters"),
                            tts_characters=budget.characters)
                    elif kind == "text_dispatch_return":
                        diagnostic.record("text_send_return", **common,
                            characters=event.get("characters"),
                            request_outcome="send_returned_provider_receipt_unconfirmed")
                    elif kind == "audio":
                        # The bridge increments the budget before forwarding.
                        if getattr(emit, "first_audio_seen", False) is False:
                            diagnostic.record("first_audio", **common,
                                total_audio_bytes=budget.audio_bytes)
                            emit.first_audio_seen = True
                        diagnostic.record("audio_count", **common,
                            total_audio_bytes=budget.audio_bytes)
                    elif kind in ("failed", "stopped", "completed", "transport_closed",
                                  "final_turn", "final_protocol_event", "close_socket_sent",
                                  "voice_turn_stopped", "voice_turn_completed"):
                        raw_code = event.get("error_category") or event.get("reason")
                        safe_code = raw_code if raw_code in {
                            "authentication", "quota", "protocol", "unknown",
                            "connection_closed", "authorization_revoked",
                            "transport_or_timeout", "audio_hard_limit", "protocol_unknown",
                            "output_timeout", "user_stop_voice", "user_stop_generation",
                            "generation_incomplete", "saved_message_mismatch",
                            "final_text_mismatch", "tts_receive_time_budget",
                            "browser_playback_time_budget", "browser_disconnected",
                            "speech_sidecar_exception", "voice_provider_failed",
                            "turn_time_budget", "total_audio_budget", "diagnostic_failed",
                        } else ("unknown" if raw_code is not None else None)
                        diagnostic.record("voice_terminal", **common,
                            voice_status=kind, error_code=safe_code,
                            timeout_type=(safe_code if safe_code in {
                                "output_timeout", "tts_receive_time_budget",
                                "browser_playback_time_budget", "turn_time_budget"} else None),
                            request_outcome=event.get("request_outcome"),
                            close_origin=event.get("close_origin"),
                            remote_close_code=event.get("remote_close_code"),
                            total_audio_bytes=budget.audio_bytes)
                if event.get("type") == "audio" and first_tts_at is not None and \
                        time.monotonic() - first_tts_at > receipts.LIMITS["tts_receive_seconds"]:
                    raise receipts.ReceiptError("tts_receive_time_budget")
                try:
                    await ws.send_json({**event, "request_id": request_id,
                        "provider_request_id": event.get("request_id")})
                except (WebSocketDisconnect, RuntimeError, OSError) as exc:
                    raise ConnectionError("browser_disconnected") from exc

            async def observe(event: str, data: dict) -> None:
                nonlocal model_usage
                if event == "text_delta":
                    await emit({"type": "chat_text_delta", "text": data["text"]})
                elif event == "message_start":
                    await emit({"type": "chat_message_start",
                        "generation_id": data["generation_id"],
                        "conversation_id": data["conversation_id"]})
                elif event == "message_complete":
                    model_usage = data.get("model_usage")
                    await emit({"type": "chat_message_complete",
                        "message_id": data.get("message_id"),
                        "generation_status": data.get("generation_status"),
                        "model_status": data.get("model_status"),
                        "display_status": data.get("display_status"),
                        "finish_reason": data.get("finish_reason"),
                        "model_usage": model_usage,
                        "message_version_sha256": data.get("message_version_sha256"),
                        "public_text_sha256": data.get("public_text_sha256")})

            m = prepared.manifest
            def on_segment_pre_send(sequence: int, span) -> None:
                if diagnostic:
                    diagnostic.record("segment_pre_send", sequence=sequence,
                        source_start=span.source_start, source_end=span.source_end,
                        source_sha256=span.source_sha256,
                        spoken_text=span.spoken_text,
                        spoken_text_sha256=hashlib.sha256(
                            span.spoken_text.encode()).hexdigest(),
                        characters=len(span.spoken_text),
                        tts_characters=budget.characters,
                        tts_segments=budget.segments)
            bridge = ChatSpeechBridge(chat_run_id=m["run_id"], owner_id=owner_id,
                character_id=m["character_id"], conversation_id=m["conversation_id"],
                generation_id=m["generation_id"], voice_id=profile["voice_id"],
                connector=connector, emit=emit, clear_playback=lambda _reason: None,
                cancel_chat=lambda _run: cancel.set(),
                verify_saved=lambda data: gate.verify_saved(m, data),
                reserve_segment=budget.reserve_segment,
                on_segment_pre_send=on_segment_pre_send,
                accept_audio=budget.accept_audio, require_lifecycle=True,
                limits=BridgeLimits(max_total_chars=receipts.LIMITS["tts_characters"],
                    max_segment_chars=receipts.LIMITS["tts_characters"],
                    max_segments=receipts.LIMITS["tts_segments"],
                    max_provider_connections=receipts.LIMITS["tts_connections"],
                    max_audio_bytes=receipts.LIMITS["tts_audio_bytes"],
                    max_turn_seconds=receipts.LIMITS["overall_seconds"]),
                output_limits=OutputLimits(total_seconds=receipts.LIMITS["tts_connection_seconds"],
                    reconnect_attempts=0,
                    max_audio_bytes=receipts.LIMITS["tts_audio_bytes"]))
            model_task = asyncio.create_task(consume_chat_sse(
                gate.chat_source(prepared, frozen, budget, cancel, ws,
                    diagnostic=diagnostic, cancel_source=cancel_source,
                    usage_sink=observed_model_usage), bridge, observe))
            deadline = time.monotonic() + receipts.LIMITS["overall_seconds"]
            playback_deadline: float | None = None
            while time.monotonic() < deadline:
                if diagnostic and diagnostic.failed and not bridge.voice_stopped:
                    await bridge.stop_voice("diagnostic_failed")
                if not gate.enabled():
                    cancel_source["source"] = "experiment_policy_gate_closed"
                    if diagnostic:
                        diagnostic.record("cancel", cancel_source=cancel_source["source"])
                    await bridge.stop_generation()
                    break
                if not gate.still_authorized(ws, m):
                    cancel_source["source"] = "authorization_revoked"
                    if diagnostic:
                        diagnostic.record("cancel", cancel_source=cancel_source["source"])
                    await bridge.stop_generation()
                    break
                if first_tts_at is not None and not bridge.voice_stopped and \
                        time.monotonic() - first_tts_at > receipts.LIMITS["tts_receive_seconds"] and \
                        not (bridge.worker and bridge.worker.done()):
                    await bridge.stop_voice("tts_receive_time_budget")
                if model_task.done() and not bridge.chat_complete and not bridge.voice_stopped:
                    await bridge.stop_voice("model_stream_failed")
                    break
                if model_task.done() and bridge.worker and bridge.worker.done() and \
                        playback_deadline is None:
                    playback_deadline = time.monotonic() + receipts.LIMITS["browser_playback_seconds"]
                if playback_deadline is not None and time.monotonic() > playback_deadline and \
                        not playback_ended:
                    await bridge.stop_voice("browser_playback_time_budget")
                    break
                if model_task.done() and (bridge.voice_stopped or playback_ended):
                    break
                try:
                    control = await asyncio.wait_for(ws.receive_json(), timeout=.1)
                except TimeoutError:
                    continue
                if not isinstance(control, dict) or control.get("request_id") != request_id:
                    continue
                kind = control.get("type")
                if kind == "stop_voice":
                    if diagnostic:
                        diagnostic.record("cancel", cancel_source="user_stop_voice")
                    await bridge.stop_voice()
                elif kind == "stop_generation":
                    cancel_source["source"] = "user_stop_generation"
                    if diagnostic:
                        diagnostic.record("cancel", cancel_source=cancel_source["source"])
                    await bridge.stop_generation()
                    if model_task is not None and not model_task.done():
                        model_task.cancel()
                elif kind == "metric" and control.get("name") == "playback_ended":
                    playback_ended = True
                elif kind == "start_chat":
                    await reject("duplicate_start")
            else:
                cancel_source["source"] = "experiment_policy_wall_clock"
                if diagnostic:
                    diagnostic.record("cancel", cancel_source=cancel_source["source"],
                        timeout_type="overall_wall_clock")
                await bridge.stop_generation()
            if model_task:
                if not model_task.done():
                    cancel_source.setdefault("source", "experiment_policy_cleanup")
                    cancel.set()
                    model_task.cancel()
                try:
                    await model_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    if diagnostic:
                        diagnostic.record("run_failure", stage="chat_consumer",
                            exception_type=(type(exc).__name__ if
                                type(exc).__name__ in {"OperationalError", "IntegrityError",
                                "ValueError", "ConnectionError", "RuntimeError"} else "Other"),
                            error_code="consumer_exception")
                model_task_handled = True
            if diagnostic and budget is not None:
                try:
                    diagnostic.record("turn_summary", model_generations=budget.model_attempts,
                        usage=model_usage or observed_model_usage.get("value"),
                        tts_characters=budget.characters, tts_segments=budget.segments,
                        tts_connections=budget.connections,
                        total_audio_bytes=budget.audio_bytes,
                        browser_playback_ended=playback_ended,
                        voice_status="stopped" if bridge.voice_stopped else "completed")
                except (DiagnosticWriteError, DiagnosticCapacityError):
                    pass
                summary_recorded = True
                try:
                    diagnostic.close()
                except DiagnosticWriteError:
                    pass
                diagnostic_closed = True
            await emit({"type": "turn_summary", "generation_id": m["generation_id"],
                "model_generations": budget.model_attempts,
                "model_usage": model_usage or observed_model_usage.get("value"),
                "tts_characters": budget.characters, "tts_segments": budget.segments,
                "tts_connections": budget.connections, "tts_audio_bytes": budget.audio_bytes,
                "public_text_sha256": hashlib.sha256(bridge.public_text.encode()).hexdigest(),
                "speech_segments": [{"sequence": sequence,
                    "source_start": span.source_start,
                    "source_end": span.source_end,
                    "source_sha256": span.source_sha256,
                    "spoken_text": span.spoken_text,
                    "spoken_text_sha256": hashlib.sha256(
                        span.spoken_text.encode()).hexdigest(),
                    "boundary_reason": span.reason}
                    for sequence, span in bridge.sent],
                "voice_stopped": bridge.voice_stopped,
                "browser_playback_ended": playback_ended,
                "receipt_used": claimed,
                "diagnostic_status": diagnostic.status if diagnostic else "unavailable"})
        except WebSocketDisconnect:
            cancel_source["source"] = "browser_disconnected"
            if diagnostic:
                diagnostic.record("cancel", cancel_source=cancel_source["source"])
            cancel.set()
        except Exception as exc:
            if diagnostic:
                diagnostic.record("run_failure", stage="product_route",
                    exception_type=(type(exc).__name__ if type(exc).__name__ in {
                        "ValueError", "RuntimeError", "ConnectionError", "OSError",
                        "OperationalError", "IntegrityError"} else "Other"),
                    error_code="route_exception")
            raise
        finally:
            if bridge is not None and not (bridge.chat_complete and bridge.worker and
                    bridge.worker.done() and not bridge.voice_stopped and playback_ended):
                try:
                    await bridge.stop_voice("browser_disconnected")
                except (ConnectionError, RuntimeError):
                    pass
            if model_task is not None and not model_task_handled:
                if not model_task.done():
                    cancel_source.setdefault("source", "experiment_policy_cleanup")
                    cancel.set()
                    model_task.cancel()
                try:
                    await model_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    if diagnostic:
                        diagnostic.record("run_failure", stage="chat_consumer",
                            exception_type=(type(exc).__name__ if
                                type(exc).__name__ in {"OperationalError", "IntegrityError",
                                "ValueError", "ConnectionError", "RuntimeError"} else "Other"),
                            error_code="consumer_exception")
            if reserved:
                await gate.release(owner_id)
            if diagnostic and not diagnostic_closed:
                if budget is not None and not summary_recorded:
                    try:
                        diagnostic.record("turn_summary", model_generations=budget.model_attempts,
                            usage=model_usage or observed_model_usage.get("value"),
                            tts_characters=budget.characters, tts_segments=budget.segments,
                            tts_connections=budget.connections,
                            total_audio_bytes=budget.audio_bytes,
                            browser_playback_ended=playback_ended,
                            voice_status=("stopped" if bridge and bridge.voice_stopped else "unknown"))
                    except (DiagnosticWriteError, DiagnosticCapacityError):
                        pass
                try:
                    diagnostic.close()
                except DiagnosticWriteError:
                    pass
            try:
                await ws.close(code=1000)
            except (RuntimeError, OSError):
                pass

    app.include_router(router)
    return gate
