"""Default-off Scribe draft relay for isolated microphone integration.

The real adapter has a separate explicit gate and is off by default. It never
accepts arbitrary client text and only a committed transcript may fill a draft.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import config, scribe_budget, storage
from .scribe_adapter import ScribeProtocolError
from .scribe_diagnostics import ATTEMPT, ScribeTrace, trace_directory


MAX_AUDIO_BYTES = 480_000  # 15 seconds of mono pcm_16000
MAX_CHUNK_BYTES = 32_000  # one second of mono pcm_16000
MAX_CONNECTION_SECONDS = 20
LANGUAGES = {"auto", "zh", "ja"}


class ScribeDraftGate:
    def __init__(self, provider_factory: Callable | None) -> None:
        self.provider_factory = provider_factory


def install(app, connect, session_lookup: Callable,
            allowed_origins: list[str], provider_factory: Callable | None = None,
            call_manager=None):
    router = APIRouter(tags=["voice-scribe-draft"])
    gate = ScribeDraftGate(provider_factory)
    active_owners: set[int] = set()
    lock = asyncio.Lock()
    daily_mode = scribe_budget.enabled()
    if daily_mode and os.getenv("YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED", "0") == "1":
        raise RuntimeError("Daily and one-shot Scribe gates cannot be combined")
    real_provider = os.getenv("YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED", "0") == "1" and not daily_mode
    receipt_value = os.getenv("YOSAKI_SCRIBE_DRAFT_RECEIPT_PATH", "").strip()
    receipt_path = Path(receipt_value) if receipt_value else None
    diagnostics_dir = os.getenv("YOSAKI_SCRIBE_DRAFT_DIAGNOSTICS_DIR", "").strip()
    trace_directory(diagnostics_dir)
    if real_provider and (receipt_path is None or not receipt_path.is_absolute()):
        raise RuntimeError("Real Scribe draft requires an absolute one-shot receipt path")

    def authorized(owner_id: int, character_id: int) -> bool:
        with connect() as db:
            character = db.execute("SELECT enabled FROM characters WHERE id=?",
                                   (character_id,)).fetchone()
            profile = storage.profile(db, character_id)
            consent = storage.consent(db, owner_id)
        return bool(character and character["enabled"] and profile and
                    profile["active"] and consent and
                    consent["notice_version"] == config.NOTICE_VERSION)

    @router.websocket("/api/voice/scribe-draft/ws")
    async def scribe_draft(ws: WebSocket) -> None:
        if (not daily_mode and os.getenv("YOSAKI_SCRIBE_DRAFT_ISOLATED", "0") != "1" or
                os.getenv("YOSAKI_BACKGROUND_TASKS_ENABLED", "1") != "0" or
                not config.enabled() or gate.provider_factory is None or
                ws.headers.get("origin") not in allowed_origins):
            await ws.close(code=1008)
            return
        session = session_lookup(ws)
        if session is None:
            await ws.close(code=1008)
            return
        owner_id = session["id"]
        await ws.accept()
        reserved = False
        trace = None
        browser_bytes = browser_chunks = active_samples = 0
        async def reject(code: str) -> None:
            if trace:
                trace.emit("error", code=code)
            await ws.send_json({"type": "error", "code": code})
        try:
            start = await asyncio.wait_for(ws.receive_json(), timeout=5)
            call_id = start.get("call_id") if isinstance(start, dict) else None
            expected_keys = {"type", "character_id", "language_code", "attempt_id"}
            if call_id is not None:
                expected_keys.add("call_id")
            if (not isinstance(start, dict) or set(start) != expected_keys or
                    start["type"] != "start" or
                    type(start["character_id"]) is not int or
                    start["language_code"] not in LANGUAGES or
                    not isinstance(start["attempt_id"], str) or
                    not ATTEMPT.fullmatch(start["attempt_id"]) or
                    not authorized(owner_id, start["character_id"])):
                await reject("start_or_authorization_invalid")
                return
            if (call_manager is not None and call_manager.enabled() and
                    (call_manager.isolated() or call_manager.single_button_conversation() is not None)
                    and call_id is None):
                await reject("voice_call_required")
                return
            if call_id is not None and (not isinstance(call_id, str) or
                    call_manager is None or not call_manager.valid(call_id, owner_id)):
                await reject("voice_call_invalid")
                return
            trace = ScribeTrace(start["attempt_id"], diagnostics_dir)
            trace.emit("browser_start")
            async with lock:
                if owner_id in active_owners:
                    await reject("scribe_busy")
                    return
                if call_id is not None:
                    try:
                        batch_id = call_manager.reserve_stt(call_id, owner_id,
                                                             start["character_id"],
                                                             start["attempt_id"])
                    except ValueError as exc:
                        await reject(str(exc))
                        return
                if daily_mode:
                    try:
                        scribe_budget.reserve(connect, owner_id, start["attempt_id"],
                                              batch_id=(batch_id if call_id is not None and
                                                        not call_manager.daily() else None),
                                              daily_call_id=(call_id if call_id is not None and
                                                             call_manager.daily() else None),
                                              call_id=call_id)
                    except (scribe_budget.BudgetUnavailable, scribe_budget.BudgetExhausted,
                            ValueError) as exc:
                        if call_id is not None:
                            call_manager.abort_stt(call_id, owner_id,
                                                   start["attempt_id"], unreserved=True)
                        await reject(str(exc))
                        return
                elif real_provider:
                    try:
                        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        await reject("scribe_attempt_budget_exceeded")
                        return
                    except OSError:
                        await reject("scribe_receipt_unavailable")
                        return
                    else:
                        with os.fdopen(fd, "wb") as marker:
                            marker.write(b"claimed\n")
                active_owners.add(owner_id)
                reserved = True
                trace.emit("reserved")
            deadline = time.monotonic() + MAX_CONNECTION_SECONDS
            provider = gate.provider_factory(owner_id, start["character_id"],
                                             start["language_code"])
            if hasattr(provider, "observer"):
                provider.observer = trace.emit
            if hasattr(provider, "open"):
                await asyncio.wait_for(provider.open(), timeout=max(0.1, deadline-time.monotonic()))
            await ws.send_json({"type": "ready", "audio_format": "pcm_16000",
                                "attempt_id": start["attempt_id"]})
            total = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await reject("scribe_timeout")
                    return
                message = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
                if not authorized(owner_id, start["character_id"]):
                    await reject("authorization_revoked")
                    return
                if call_id is not None and not call_manager.valid(call_id, owner_id):
                    await reject("voice_call_ended")
                    return
                if daily_mode and not scribe_budget.settings(connect, owner_id)["enabled"]:
                    await reject("scribe_user_disabled")
                    return
                if not isinstance(message, dict):
                    await reject("frame_invalid")
                    return
                if message.get("type") == "stop" and set(message) == {"type"}:
                    await ws.send_json({"type": "stopped"})
                    trace.emit("browser_result", code="stopped")
                    return
                if message.get("type") == "capture" and set(message) == \
                        {"type", "sample_rate", "channels"}:
                    rate, channels = message["sample_rate"], message["channels"]
                    if type(rate) is not int or not 16000 <= rate <= 192000 or channels != 1:
                        await reject("capture_invalid")
                        return
                    trace.emit("capture_ready", sample_rate=rate, channels=1)
                    continue
                if message.get("type") == "audio" and set(message) == {"type", "pcm_base64"}:
                    encoded = message["pcm_base64"]
                    if not isinstance(encoded, str) or len(encoded) > 43_000:
                        await reject("audio_invalid")
                        return
                    try:
                        pcm = base64.b64decode(encoded, validate=True)
                    except binascii.Error:
                        await reject("audio_invalid")
                        return
                    if not pcm or len(pcm) % 2 or len(pcm) > MAX_CHUNK_BYTES or \
                            total + len(pcm) > MAX_AUDIO_BYTES:
                        await reject("audio_budget_exceeded")
                        return
                    total += len(pcm)
                    browser_bytes += len(pcm)
                    browser_chunks += 1
                    active_samples += sum(pcm[i] != 0 or pcm[i+1] != 0
                                          for i in range(0, len(pcm), 2))
                    trace.emit("audio_browser", bytes=browser_bytes, chunks=browser_chunks,
                               active_samples=active_samples)
                    if daily_mode:
                        try:
                            scribe_budget.record_audio(connect, owner_id,
                                                       start["attempt_id"], len(pcm))
                        except (scribe_budget.BudgetUnavailable,
                                scribe_budget.BudgetExhausted) as exc:
                            await reject(str(exc))
                            return
                    events = await asyncio.wait_for(provider.feed(pcm, commit=False),
                        timeout=max(0.1, deadline-time.monotonic()))
                elif message.get("type") == "commit" and set(message) == {"type"}:
                    trace.emit("commit_browser", bytes=browser_bytes, chunks=browser_chunks,
                               active_samples=active_samples)
                    if total == 0:
                        await reject("empty_audio")
                        return
                    if active_samples == 0:
                        await reject("audio_silent")
                        return
                    events = await asyncio.wait_for(provider.feed(b"", commit=True),
                        timeout=max(0.1, deadline-time.monotonic()))
                else:
                    await reject("frame_invalid")
                    return
                for event in events:
                    if (not isinstance(event, dict) or event.get("type") not in
                            {"partial", "committed"} or not isinstance(event.get("text"), str)
                            or len(event["text"]) > 5000):
                        await reject("provider_event_invalid")
                        return
                    result = {"type": event["type"], "text": event["text"]}
                    if event["type"] == "committed":
                        if call_id is not None and event["text"].strip():
                            try:
                                result["call_turn_id"] = call_manager.transcript(
                                    call_id, owner_id, start["attempt_id"],
                                    event["text"].strip())
                            except ValueError:
                                await reject("voice_call_transcript_stale")
                                return
                    await ws.send_json(result)
                    if event["type"] == "committed":
                        trace.emit("browser_result", code="committed" if
                                   event["text"].strip() else "provider_empty_transcript")
                        return
        except WebSocketDisconnect as exc:
            if trace:
                trace.emit("browser_closed", initiator="remote", close_code=int(exc.code))
            return
        except asyncio.CancelledError:
            if trace:
                trace.emit("error", code="server_cancelled")
            return
        except TimeoutError:
            if trace:
                trace.emit("error", code="scribe_timeout")
            try:
                await ws.send_json({"type": "error", "code": "scribe_timeout"})
            except RuntimeError:
                pass
        except ScribeProtocolError as exc:
            if trace:
                trace.emit("error", code=exc.code)
            try:
                await ws.send_json({"type": "error", "code": exc.code})
            except RuntimeError:
                pass
        except Exception:
            if trace:
                trace.emit("error", code="scribe_local_error")
            try:
                await ws.send_json({"type": "error", "code": "scribe_local_error"})
            except RuntimeError:
                pass
        finally:
            if "call_id" in locals() and call_id is not None and trace:
                call_manager.abort_stt(call_id, owner_id, start["attempt_id"])
            if reserved:
                # Same event loop as the reservation lock: release synchronously
                # before a provider close handshake can block or be cancelled.
                active_owners.discard(owner_id)
            if trace:
                try:
                    trace.emit("cleanup", bytes=browser_bytes, chunks=browser_chunks,
                               active_samples=active_samples)
                except (OSError, ValueError):
                    pass  # Dispatch has already stopped; still release transport.
            if "provider" in locals() and hasattr(provider, "aclose"):
                try:
                    await asyncio.shield(provider.aclose())
                except BaseException:
                    pass
            try:
                await ws.close()
            except RuntimeError:
                pass

    app.include_router(router)
    return gate
