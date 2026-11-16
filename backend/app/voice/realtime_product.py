"""Gated product WS for one saved, complete assistant message.

No schema is created here. The real connector is disabled independently of the
product output flag; isolated tests inject a loopback connector into the same
installed route. In-memory limits are intentionally single-worker only.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from . import config, manager, realtime_receipt, storage
from .realtime_output import OutputLimits, OutputRelay
from .realtime_ttd_connector import RealTTDConnector


@dataclass(frozen=True)
class BoundOutput:
    owner_id: int
    character_id: int
    conversation_id: int
    message_id: int
    profile_revision: int
    digest: str
    voice_id: str
    text: str


class ProductOutputGate:
    def __init__(self, connect, session_lookup: Callable,
                 allowed_origins: list[str]) -> None:
        self.connect = connect
        self.session_lookup = session_lookup
        self.allowed_origins = frozenset(allowed_origins)
        self.connector_factory: Callable[[BoundOutput], Any] | None = None
        self.active_owners: set[int] = set()
        self.attempts: dict[int, int] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def enabled() -> bool:
        return config.enabled() and config.v4_output_enabled()

    @staticmethod
    def max_attempts() -> int:
        raw = os.getenv("YOSAKI_V4_OUTPUT_MAX_ATTEMPTS_PER_OWNER", "0")
        try:
            value = int(raw)
        except ValueError:
            return 0
        return value if 0 <= value <= 10 else 0

    def bind(self, ws: WebSocket, owner_id: int, ids: dict[str, int]) -> BoundOutput:
        session = self.session_lookup(ws)
        if session is None or session["id"] != owner_id:
            raise PermissionError("session_revoked")
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone() is None:
                raise PermissionError("owner_missing")
            row = db.execute("""SELECT m.conversation_id,c.character_id,m.generation_status
                FROM messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE m.id=?""", (ids["message_id"],)).fetchone()
            if row is None or row["conversation_id"] != ids["conversation_id"] or \
                    row["character_id"] != ids["character_id"] or \
                    row["generation_status"] != "completed":
                raise ValueError("message_binding_invalid")
            profile = storage.profile(db, ids["character_id"])
            if not profile or profile["configuration"]["model_realtime"] != "eleven_v4_turbo":
                raise ValueError("realtime_model_unsupported")
        context = manager.speech_context(self.connect, ids["message_id"], owner_id)
        if context["character_id"] != ids["character_id"] or \
                context["language"] != "ja" or len(context["segments"]) != 1 or \
                len(context["segments"][0]) > 1000:
            raise ValueError("realtime_text_unsupported")
        return BoundOutput(owner_id, ids["character_id"], ids["conversation_id"],
                           ids["message_id"], context["revision"], context["digest"],
                           context["voice_id"], context["segments"][0])

    def still_authorized(self, ws: WebSocket, bound: BoundOutput) -> bool:
        if not self.enabled():
            return False
        try:
            refreshed = self.bind(ws, bound.owner_id, {
                "character_id": bound.character_id,
                "conversation_id": bound.conversation_id,
                "message_id": bound.message_id,
            })
            return refreshed == bound
        except (HTTPException, PermissionError, ValueError, KeyError, sqlite3.Error):
            return False

    async def reserve(self, owner_id: int) -> str | None:
        async with self.lock:
            if owner_id in self.active_owners:
                return "output_busy"
            if self.attempts.get(owner_id, 0) >= self.max_attempts():
                return "output_budget_exhausted"
            self.active_owners.add(owner_id)
            self.attempts[owner_id] = self.attempts.get(owner_id, 0) + 1
            return None

    async def release(self, owner_id: int) -> None:
        async with self.lock:
            self.active_owners.discard(owner_id)

    def connector(self, bound: BoundOutput):
        if self.connector_factory is not None:
            return self.connector_factory(bound)
        if not config.v4_real_provider_enabled():
            raise ValueError("real_provider_disabled")
        return RealTTDConnector(os.getenv("ELEVENLABS_API_KEY", ""),
                                os.getenv("YOSAKI_V4_TTD_PROXY") or None)


def install(app, connect, session_lookup: Callable,
            allowed_origins: list[str]) -> ProductOutputGate:
    gate = ProductOutputGate(connect, session_lookup, allowed_origins)
    router = APIRouter(tags=["voice-v4"])

    @router.websocket("/api/voice/realtime/output")
    async def output_socket(ws: WebSocket) -> None:
        # HTTP middleware does not protect WebSocket handshakes.
        if not gate.enabled() or ws.headers.get("origin") not in gate.allowed_origins:
            await ws.close(code=1008)
            return
        session = gate.session_lookup(ws)
        if session is None:
            await ws.close(code=1008)
            return
        owner_id = session["id"]
        await ws.accept()
        relay: OutputRelay | None = None
        run = None
        approval: realtime_receipt.PreparedApproval | None = None
        claimed = False
        reserved = False
        browser_playback_ended = False
        try:
            async def reject(reason: str, request_id: str | None = None) -> None:
                await ws.send_json({"type": "input_rejected", "reason": reason,
                                    "request_id": request_id})

            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=5)
            except (TimeoutError, ValueError):
                await reject("start_required")
                return
            if (not isinstance(message, dict) or message.get("type") != "start" or
                    set(message) != {"type", "request_id", "message_id",
                                     "conversation_id", "character_id"} or
                    any(type(message.get(key)) is not int or message[key] < 1 for key in
                        ("message_id", "conversation_id", "character_id"))):
                await reject("invalid_start", message.get("request_id") if isinstance(message, dict) else None)
                return
            try:
                request_id = str(uuid.UUID(message["request_id"]))
            except (TypeError, ValueError, AttributeError):
                await reject("invalid_request_id")
                return
            if request_id != message["request_id"]:
                await reject("invalid_request_id")
                return
            ids = {key: message[key] for key in ("message_id", "conversation_id", "character_id")}
            try:
                bound = gate.bind(ws, owner_id, ids)
            except (HTTPException, PermissionError, ValueError, KeyError, sqlite3.Error):
                await reject("binding_or_permission_invalid", request_id)
                return
            try:
                approval = realtime_receipt.prepare(gate.connect, bound)
            except (realtime_receipt.ApprovalError, HTTPException, OSError,
                    sqlite3.Error, ValueError, KeyError) as exc:
                reason = str(exc) if isinstance(exc, realtime_receipt.ApprovalError) else "approval_preflight_failed"
                await reject(reason, request_id)
                return
            denial = await gate.reserve(owner_id)
            if denial:
                await reject(denial, request_id)
                return
            reserved = True
            try:
                connector = gate.connector(bound)
            except ValueError:
                await reject("provider_disabled", request_id)
                return
            if not gate.still_authorized(ws, bound):
                await reject("authorization_changed", request_id)
                return
            try:
                if gate.bind(ws, owner_id, ids) != bound:
                    raise realtime_receipt.ApprovalError("binding_changed")
                refreshed = realtime_receipt.prepare(gate.connect, bound)
                if refreshed != approval:
                    raise realtime_receipt.ApprovalError("approval_changed")
                realtime_receipt.claim(refreshed)
                claimed = True
            except (realtime_receipt.ApprovalError, HTTPException, PermissionError,
                    OSError, sqlite3.Error, ValueError, KeyError) as exc:
                reason = str(exc) if isinstance(exc, realtime_receipt.ApprovalError) else "approval_preflight_failed"
                await reject(reason, request_id)
                return

            def guarded_connector():
                if not gate.still_authorized(ws, bound) or not realtime_receipt.still_matches(approval):
                    raise PermissionError("authorization_changed")
                return connector()

            async def emit(event: dict[str, Any]) -> None:
                if event["type"] in ("connection_ready", "text_dispatch_attempt", "audio") and \
                        (not gate.still_authorized(ws, bound) or
                         not realtime_receipt.still_matches(approval)):
                    raise PermissionError("authorization_revoked")
                try:
                    await ws.send_json(event)
                except (WebSocketDisconnect, RuntimeError, OSError):
                    if event["type"] == "stopped":
                        return  # The browser has already gone; cleanup must release its lease.
                    raise ConnectionError("browser_disconnected")

            relay = OutputRelay(voice_id=bound.voice_id, connector=guarded_connector,
                                emit=emit, limits=OutputLimits(
                                    total_seconds=approval.manifest["limits"]["connection_seconds"],
                                    reconnect_attempts=0,
                                    max_audio_bytes=approval.manifest["limits"]["max_audio_bytes"]))
            run = await relay.start(request_id=request_id, text=bound.text)
            deadline = time.monotonic() + approval.manifest["limits"]["dispatch_wall_seconds"]
            while time.monotonic() < deadline:
                if not gate.still_authorized(ws, bound) or not realtime_receipt.still_matches(approval):
                    await relay.stop()
                    break
                if run.task and run.task.done() and run.final_type == "failed":
                    break
                try:
                    control = await asyncio.wait_for(ws.receive_json(), timeout=1)
                except TimeoutError:
                    continue
                if not isinstance(control, dict):
                    continue
                if control.get("type") == "stop" and control.get("request_id") == request_id:
                    await relay.stop()
                    break
                if control.get("type") == "start":
                    await reject("duplicate_start", control.get("request_id"))
                    continue
                if (control.get("type") == "metric" and
                        control.get("request_id") == request_id and
                        control.get("name") == "playback_ended" and
                        run.final_type == "completed"):
                    browser_playback_ended = True
                    break
            else:
                await relay.stop()
        except WebSocketDisconnect:
            pass
        finally:
            try:
                if relay is not None:
                    await relay.stop()
            finally:
                try:
                    if claimed and approval is not None:
                        realtime_receipt.observe(approval,
                            provider_status=run.final_type if run and run.final_type else "stopped_or_unknown",
                            text_dispatched=bool(run and run.dispatched),
                            audio_bytes=run.audio_bytes if run else 0,
                            browser_playback_ended=browser_playback_ended)
                except OSError:
                    pass  # The occupied marker remains authoritative.
                finally:
                    if reserved:
                        await gate.release(owner_id)
            try:
                await ws.close(code=1000)
            except (RuntimeError, OSError):
                pass

    app.include_router(router)
    return gate
