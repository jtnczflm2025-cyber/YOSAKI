"""Default-off product route for an isolated, fake-only dynamic chat bridge.

This route has no real model or ElevenLabs connector. Installation requires an
explicit isolated flag and a separate temporary database; the connector must
be injected by an isolated runtime/test. It never accepts client speech text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from . import config, realtime_dynamic_receipt as receipts, storage
from .realtime_chat_bridge import BridgeLimits, ChatSpeechBridge, consume_chat_sse


FAKE_CHAT_MODEL = "isolated_mock_chat_v1"
FAKE_PUBLIC_CHUNKS = (
    "今日は少しゆっくり話そう。続きは",
    "君のペースで聞かせて。\n\n無理に急がなくていい。",
)


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass(frozen=True)
class BoundTurn:
    owner_id: int
    character_id: int
    conversation_id: int
    profile_revision: int
    voice_id: str
    notice_version: str
    model_id: str
    language_code: str


class DynamicProductGate:
    def __init__(self, connect, session_lookup: Callable, origins: list[str],
                 receipt_dir: Path, run_id: str, saved_receipt: Callable) -> None:
        self.connect = connect
        self.session_lookup = session_lookup
        self.origins = frozenset(origins)
        self.receipt_dir = receipt_dir
        self.run_id = run_id
        self.saved_receipt = saved_receipt
        self.connector_factory: Callable[[BoundTurn], Any] | None = None
        self.chat_source_factory: Callable[..., Any] | None = None
        self.fake_mode = "normal"
        self.active_owners: set[int] = set()
        self.lock = asyncio.Lock()

    @staticmethod
    def enabled() -> bool:
        return (os.getenv("YOSAKI_V4_DYNAMIC_CHAT_ISOLATED", "0") == "1" and
                os.getenv("YOSAKI_V4_DYNAMIC_FAKE_ONLY", "0") == "1" and
                os.getenv("YOSAKI_V4_REAL_PROVIDER_ENABLED", "0") != "1" and
                os.getenv("YOSAKI_BACKGROUND_TASKS_ENABLED", "1") == "0" and
                config.enabled())

    def bind(self, ws: WebSocket, owner_id: int,
             character_id: int, conversation_id: int) -> BoundTurn:
        session = self.session_lookup(ws)
        if session is None or session["id"] != owner_id:
            raise PermissionError("session_revoked")
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
        configuration = profile["configuration"]
        if (configuration["model_realtime"] != "eleven_v4_turbo" or
                configuration["audio_format"] != "pcm_24000" or
                configuration["language"] != "ja"):
            raise ValueError("voice_configuration_unsupported")
        return BoundTurn(owner_id, character_id, conversation_id,
                         profile["revision"], profile["voice_id"],
                         consent["notice_version"],
                         configuration["model_realtime"], "ja")

    def still_authorized(self, ws: WebSocket, bound: BoundTurn) -> bool:
        if not self.enabled():
            return False
        try:
            return self.bind(ws, bound.owner_id, bound.character_id,
                             bound.conversation_id) == bound
        except (PermissionError, ValueError, HTTPException, sqlite3.Error):
            return False

    def approval(self, bound: BoundTurn) -> receipts.Prepared:
        prepared = receipts.prepare(self.receipt_dir, self.run_id)
        manifest = prepared.manifest
        expected = {
            "owner_id": bound.owner_id, "character_id": bound.character_id,
            "conversation_id": bound.conversation_id,
            "profile_revision": bound.profile_revision,
            "voice_id_sha256": hashlib.sha256(bound.voice_id.encode()).hexdigest(),
            "notice_version": bound.notice_version,
            "chat_model_id": FAKE_CHAT_MODEL,
            "tts_model_id": bound.model_id,
            "output_format": "pcm_24000",
            "language_code": bound.language_code,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise receipts.ReceiptError("receipt_binding_changed")
        return prepared

    async def reserve(self, owner_id: int) -> bool:
        async with self.lock:
            if owner_id in self.active_owners:
                return False
            self.active_owners.add(owner_id)
            return True

    async def release(self, owner_id: int) -> None:
        async with self.lock:
            self.active_owners.discard(owner_id)

    def verify_saved(self, bound: BoundTurn, data: dict) -> bool:
        with self.connect() as db:
            row = db.execute("""SELECT m.id,m.conversation_id,m.role,m.content,
                m.generation_status,c.character_id FROM messages m
                JOIN conversations c ON c.id=m.conversation_id WHERE m.id=?""",
                (data["message_id"],)).fetchone()
            if not row or row["role"] != "assistant" or \
                    row["conversation_id"] != bound.conversation_id or \
                    row["character_id"] != bound.character_id or \
                    row["generation_status"] != "completed":
                return False
            receipt = self.saved_receipt(db, row["id"])
        return (receipt["message_version_sha256"] == data["message_version_sha256"] and
                receipt["public_text_sha256"] == data["public_text_sha256"])

    async def fake_chat(self, bound: BoundTurn, generation_id: str,
                        cancel: asyncio.Event):
        """Only an isolated fixture; it writes a real assistant row at the end."""
        yield _frame("message_start", {"owner_id": bound.owner_id,
            "character_id": bound.character_id, "conversation_id": bound.conversation_id,
            "generation_id": generation_id, "model": FAKE_CHAT_MODEL,
            "mode": "isolated_fake"})
        yield _frame("reasoning_delta", {"text": "internal reasoning fixture"})
        yield _frame("monologue_delta", {"text": "internal monologue fixture"})
        yield _frame("tool_waiting_confirmation", {"arguments": "internal tool fixture"})
        visible = ""
        for chunk in FAKE_PUBLIC_CHUNKS:
            if cancel.is_set():
                break
            visible += chunk
            yield _frame("text_delta", {"text": chunk})
            await asyncio.sleep(.25 if self.fake_mode == "normal" else .5)
        status = "stopped" if cancel.is_set() else "completed"
        if self.fake_mode == "save_failure":
            yield _frame("error", {"message": "isolated_save_failed"})
            return
        with self.connect() as db:
            assistant_id = db.execute("""INSERT INTO messages
                (conversation_id,role,content,model,created_at,generation_status)
                VALUES (?,'assistant',?,?,datetime('now'),?)""",
                (bound.conversation_id, visible, FAKE_CHAT_MODEL, status)).lastrowid
            saved = self.saved_receipt(db, assistant_id)
        if self.fake_mode == "final_mismatch":
            saved["public_text_sha256"] = "0" * 64
        yield _frame("message_complete", {"conversation_id": bound.conversation_id,
            "generation_id": generation_id, "generation_status": status,
            "model": FAKE_CHAT_MODEL, **saved})


def install(app, connect, session_lookup: Callable, origins: list[str],
            saved_receipt: Callable, *, receipt_dir: Path | None = None,
            run_id: str | None = None) -> DynamicProductGate:
    root = Path(os.environ.get("YOSAKI_DATA_DIR", "")).resolve()
    project_data = Path(__file__).resolve().parents[3] / "data"
    if not DynamicProductGate.enabled() or root == project_data.resolve() or \
            not os.environ.get("YOSAKI_DATA_DIR"):
        raise ValueError("isolated_dynamic_route_disabled_or_data_path_unsafe")
    gate = DynamicProductGate(connect, session_lookup, origins,
        receipt_dir or Path(os.getenv("YOSAKI_V4_DYNAMIC_RECEIPT_DIR", "")),
        run_id or os.getenv("YOSAKI_V4_DYNAMIC_RUN_ID", ""), saved_receipt)
    router = APIRouter(tags=["voice-v4-isolated"])

    @router.get("/api/voice/realtime/chat-isolated/status")
    def status() -> dict:
        return {"enabled": gate.enabled(), "mock_only": True,
                "run_id": gate.run_id}

    @router.websocket("/api/voice/realtime/chat-isolated")
    async def chat_socket(ws: WebSocket) -> None:
        if not gate.enabled() or ws.headers.get("origin") not in gate.origins:
            await ws.close(code=1008)
            return
        session = gate.session_lookup(ws)
        if session is None:
            await ws.close(code=1008)
            return
        owner_id = session["id"]
        await ws.accept()
        request_id: str | None = None
        reserved = False
        bridge: ChatSpeechBridge | None = None
        chat_task: asyncio.Task | None = None
        cancel = asyncio.Event()
        claimed = False
        browser_playback_ended = False
        budget: receipts.TurnBudget | None = None
        last_segment_end: float | None = None
        try:
            async def reject(reason: str) -> None:
                await ws.send_json({"type": "input_rejected", "request_id": request_id,
                                    "reason": reason})

            try:
                start = await asyncio.wait_for(ws.receive_json(), timeout=5)
            except (TimeoutError, ValueError):
                await reject("start_required")
                return
            if (not isinstance(start, dict) or set(start) !=
                    {"type", "request_id", "run_id", "character_id", "conversation_id"} or
                    start.get("type") != "start_chat" or
                    any(type(start.get(key)) is not int or start[key] < 1 for key in
                        ("character_id", "conversation_id")) or
                    start.get("run_id") != gate.run_id):
                await reject("invalid_start")
                return
            try:
                request_id = str(uuid.UUID(start["request_id"]))
            except (TypeError, ValueError, AttributeError):
                await reject("invalid_request_id")
                return
            if request_id != start["request_id"]:
                await reject("invalid_request_id")
                return
            try:
                bound = gate.bind(ws, owner_id, start["character_id"],
                                  start["conversation_id"])
                approval = gate.approval(bound)
            except (PermissionError, ValueError, sqlite3.Error, OSError, KeyError):
                await reject("binding_or_receipt_invalid")
                return
            if gate.connector_factory is None:
                await reject("fake_supplier_not_installed")
                return
            if not await gate.reserve(owner_id):
                await reject("dynamic_turn_busy")
                return
            reserved = True
            if not gate.still_authorized(ws, bound):
                await reject("authorization_changed")
                return
            budget = receipts.TurnBudget(approval)
            limits = approval.manifest["limits"]

            def connector():
                nonlocal claimed
                if not gate.still_authorized(ws, bound) or \
                        receipts.code_digest() != approval.code_sha256:
                    raise PermissionError("authorization_changed")
                connection = gate.connector_factory(bound)
                if getattr(connection, "local_fake_provider", False) is not True:
                    raise PermissionError("mock_receipt_cannot_use_real_provider")
                if not claimed:
                    receipts.claim(approval)
                    claimed = True
                budget.reserve_connection()
                return connection

            async def emit(event: dict) -> None:
                nonlocal last_segment_end
                if not gate.still_authorized(ws, bound):
                    raise PermissionError("authorization_changed")
                wrapped = {**event, "request_id": request_id,
                           "provider_request_id": event.get("request_id")}
                if event.get("type") == "completed":
                    last_segment_end = time.monotonic()
                if event.get("type") == "audio" and event.get("sequence") == 1 and \
                        last_segment_end is not None:
                    wrapped["inter_segment_receive_gap_ms"] = round(
                        (time.monotonic() - last_segment_end) * 1000, 3)
                    last_segment_end = None
                try:
                    await ws.send_json(wrapped)
                except (WebSocketDisconnect, RuntimeError, OSError) as exc:
                    raise ConnectionError("browser_disconnected") from exc

            async def observe(event: str, data: dict) -> None:
                if event == "text_delta":
                    await emit({"type": "chat_text_delta", "text": data["text"]})
                elif event == "message_start":
                    await emit({"type": "chat_message_start",
                                "generation_id": data["generation_id"],
                                "conversation_id": data["conversation_id"]})
                elif event == "message_complete":
                    await emit({"type": "chat_message_complete",
                                "message_id": data.get("message_id"),
                                "generation_status": data.get("generation_status"),
                                "message_version_sha256": data.get("message_version_sha256"),
                                "public_text_sha256": data.get("public_text_sha256")})
                elif event == "error":
                    await emit({"type": "chat_failed", "reason": "isolated_chat_error"})

            bridge = ChatSpeechBridge(
                chat_run_id=approval.manifest["run_id"],
                owner_id=bound.owner_id, character_id=bound.character_id,
                conversation_id=bound.conversation_id,
                generation_id=approval.manifest["generation_id"],
                voice_id=bound.voice_id, connector=connector, emit=emit,
                clear_playback=lambda reason: None,
                cancel_chat=lambda _run: cancel.set(),
                verify_saved=lambda data: gate.verify_saved(bound, data),
                reserve_segment=budget.reserve_segment,
                accept_audio=budget.accept_audio,
                require_lifecycle=True,
                limits=BridgeLimits(max_total_chars=limits["max_characters"],
                    max_segment_chars=min(400, limits["max_characters"]),
                    max_segments=limits["max_segments"],
                    max_provider_connections=limits["max_connections"],
                    max_audio_bytes=limits["max_audio_bytes"],
                    max_turn_seconds=limits["max_turn_seconds"]),
            )
            source = (gate.chat_source_factory(bound, approval.manifest["generation_id"], cancel)
                      if gate.chat_source_factory else
                      gate.fake_chat(bound, approval.manifest["generation_id"], cancel))
            chat_task = asyncio.create_task(consume_chat_sse(source, bridge, observe))
            deadline = time.monotonic() + limits["max_turn_seconds"]
            while time.monotonic() < deadline:
                if not gate.still_authorized(ws, bound):
                    await bridge.stop_voice("authorization_revoked")
                    break
                if chat_task.done() and not bridge.chat_complete and not bridge.voice_stopped:
                    await bridge.stop_voice("chat_stream_failed")
                    break
                if chat_task.done() and (bridge.voice_stopped or browser_playback_ended):
                    break
                try:
                    control = await asyncio.wait_for(ws.receive_json(), timeout=.1)
                except TimeoutError:
                    continue
                if not isinstance(control, dict) or control.get("request_id") != request_id:
                    continue
                if control.get("type") == "stop_voice":
                    await bridge.stop_voice()
                elif control.get("type") == "stop_generation":
                    await bridge.stop_generation()
                elif control.get("type") == "start_chat":
                    await reject("duplicate_start")
                elif control.get("type") == "metric" and control.get("name") == "playback_ended":
                    browser_playback_ended = True
                if chat_task.done() and bridge.voice_stopped:
                    break
            else:
                await bridge.stop_voice("turn_time_budget")
            if chat_task and not chat_task.done():
                try:
                    await asyncio.wait_for(chat_task, timeout=2)
                except TimeoutError:
                    chat_task.cancel()
            if bridge:
                await ws.send_json({"type": "turn_summary", "request_id": request_id,
                    "turn_id": bridge.turn_id, "generation_id": bridge.generation_id,
                    "segments": len(bridge.sent), "connections": budget.connections,
                    "characters": budget.characters, "audio_bytes": budget.audio_bytes,
                    "public_text_sha256": hashlib.sha256(
                        bridge.public_text.encode("utf-8")).hexdigest(),
                    "speech_segments": [{
                        "sequence": sequence,
                        "source_start": span.source_start,
                        "source_end": span.source_end,
                        "source_sha256": span.source_sha256,
                        "spoken_text": span.spoken_text,
                        "spoken_text_sha256": hashlib.sha256(
                            span.spoken_text.encode("utf-8")).hexdigest(),
                        "boundary_reason": span.reason,
                    } for sequence, span in bridge.sent],
                    "voice_stopped": bridge.voice_stopped,
                    "browser_playback_ended": browser_playback_ended,
                    "receipt_used": claimed})
        except WebSocketDisconnect:
            cancel.set()
        finally:
            if bridge is not None and not (
                    bridge.chat_complete and bridge.worker and bridge.worker.done() and
                    not bridge.voice_stopped and browser_playback_ended):
                await bridge.stop_voice("browser_disconnected")
            if chat_task is not None and not chat_task.done():
                cancel.set()
                chat_task.cancel()
                try:
                    await chat_task
                except (asyncio.CancelledError, ConnectionError):
                    pass
            if reserved:
                await gate.release(owner_id)
            try:
                await ws.close(code=1000)
            except (RuntimeError, OSError):
                pass

    app.include_router(router)
    return gate
