"""V4 TTD output relay, installed only by explicitly gated callers.

The caller supplies trusted text, voice and a WebSocket connector. This module
does not read credentials, import main.py, touch SQLite, or open a connection
on its own. In this phase only loopback fake connectors exercise the relay.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from websockets.exceptions import ConnectionClosed, InvalidStatus


Emit = Callable[[dict[str, Any]], Awaitable[None]]
Connect = Callable[[], Any]
KNOWN_ERRORS = {
    "invalid_api_key": "authentication",
    "authentication_error": "authentication",
    "quota_exceeded": "quota",
    "rate_limit_exceeded": "quota",
    "invalid_request": "protocol",
    "validation_error": "protocol",
}


@dataclass
class OutputLimits:
    keepalive_seconds: float = 10.0
    total_seconds: float = 45.0
    close_wait_seconds: float = 5.0
    connect_budget_seconds: float = 5.0
    reconnect_attempts: int = 1
    max_audio_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if not (0 < self.keepalive_seconds <= 10 and
                0 < self.total_seconds <= 60 and
                0 < self.close_wait_seconds <= 5 and
                0 < self.connect_budget_seconds <= 5 and
                self.reconnect_attempts in (0, 1) and
                0 < self.max_audio_bytes <= 8 * 1024 * 1024):
            raise ValueError("invalid_output_limits")


@dataclass
class OutputRun:
    request_id: str
    generation_id: str
    segment_id: str
    epoch: int
    connection_id: str = ""
    connection_attempt: int = 0
    dispatched: bool = False
    cancelled: bool = False
    terminal: bool = False
    final_type: str | None = None
    audio_bytes: int = 0
    sequence: int = 0
    text_dispatch_at: float | None = None
    task: asyncio.Task[None] | None = None


class OutputRelay:
    def __init__(self, *, voice_id: str, connector: Connect, emit: Emit,
                 limits: OutputLimits | None = None) -> None:
        if not voice_id or not callable(connector) or not callable(emit):
            raise ValueError("invalid_output_configuration")
        self.voice_id = voice_id
        self.connector = connector
        self.emit = emit
        self.limits = limits or OutputLimits()
        self.session_id = uuid.uuid4().hex
        self.epoch = 0
        self.current: OutputRun | None = None

    async def start(self, *, request_id: str, text: str) -> OutputRun:
        if not request_id or not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError("invalid_trusted_text")
        await self.stop()
        self.epoch += 1
        run = OutputRun(request_id=request_id, generation_id=uuid.uuid4().hex,
                        segment_id=uuid.uuid4().hex, epoch=self.epoch)
        self.current = run
        await self._emit(run, "started")
        run.task = asyncio.create_task(self._run(run, text))
        return run

    async def stop(self) -> None:
        run = self.current
        self.current = None
        self.epoch += 1
        if run is None:
            return
        run.cancelled = True
        if run.task:
            run.task.cancel()
            try:
                await asyncio.wait_for(run.task, timeout=1)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                # The stale task cannot emit into the next run even if a
                # broken connector ignores cancellation during cleanup.
                pass
        if run.terminal:
            return
        await self.emit({"type": "stopped", "session_id": self.session_id,
                         "generation_id": run.generation_id,
                         "segment_id": run.segment_id,
                         "connection_id": run.connection_id,
                         "connection_epoch": run.epoch,
                         "request_id": run.request_id,
                         "text_dispatched": run.dispatched,
                         "close_origin": "local_cancel_requested",
                         "request_outcome": "unknown" if run.dispatched else "not_dispatched"})

    async def _emit(self, run: OutputRun, kind: str, **fields: Any) -> None:
        if self.current is run and not run.cancelled:
            if kind in ("completed", "failed"):
                run.terminal = True
                run.final_type = kind
            await self.emit({"type": kind, "session_id": self.session_id,
                             "request_id": run.request_id,
                             "generation_id": run.generation_id,
                             "segment_id": run.segment_id,
                             "connection_id": run.connection_id,
                             "connection_epoch": run.epoch,
                             **fields})

    @staticmethod
    def _close_evidence(exc: ConnectionClosed) -> dict[str, Any]:
        remote = getattr(exc, "rcvd", None)
        return {"close_origin": "remote_close_frame" if remote is not None else "transport_lost",
                "remote_close_code": getattr(remote, "code", None)}

    async def _run(self, run: OutputRun, text: str) -> None:
        try:
            async with asyncio.timeout(self.limits.total_seconds):
                await self._run_attempts(run, text)
        except TimeoutError:
            await self._emit(run, "failed", error_category="output_timeout",
                             request_outcome="unknown" if run.dispatched else "not_dispatched")

    async def _run_attempts(self, run: OutputRun, text: str) -> None:
        started = time.monotonic()
        attempts = 1 + self.limits.reconnect_attempts
        for attempt in range(1, attempts + 1):
            if self.current is not run:
                return
            run.connection_attempt = attempt
            run.connection_id = uuid.uuid4().hex
            run.epoch = self.epoch = self.epoch + 1
            await self._emit(run, "connection_attempt", attempt=attempt)
            try:
                remaining = self.limits.connect_budget_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("connect_budget_exhausted")
                connection = self.connector()
                async with asyncio.timeout(remaining):
                    ws = await connection.__aenter__()
                pending_error: BaseException | None = None
                try:
                    await self._emit(run, "connection_ready", attempt=attempt)
                    await ws.send(json.dumps({"voices": [self.voice_id]}))
                    # A failed send has an unknown dispatch outcome. Never replay it.
                    run.dispatched = True
                    run.text_dispatch_at = time.monotonic()
                    await self._emit(run, "text_dispatch_attempt", characters=len(text))
                    await ws.send(json.dumps({"inputs": [{"text": text,
                                                          "voice_id": self.voice_id}],
                                              "flush": True}, ensure_ascii=False))
                    await self._emit(run, "text_dispatch_return", characters=len(text))
                    await self._receive(run, ws, started)
                except BaseException as exc:
                    pending_error = exc
                    raise
                finally:
                    try:
                        await connection.__aexit__(None, None, None)
                        if pending_error is None:
                            await self._emit(run, "transport_closed",
                                             close_origin="local_cleanup_completed")
                    except Exception:
                        if pending_error is None and not run.terminal:
                            raise
                if run.terminal:
                    return
                if not run.sequence:
                    raise ValueError("empty_audio")
                await self._emit(run, "completed", audio_chunks=run.sequence,
                                 audio_bytes=run.audio_bytes)
                return
            except asyncio.CancelledError:
                raise
            except InvalidStatus as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                category = ("authentication" if status in (401, 403) else
                            "quota" if status == 429 else
                            "protocol" if status == 400 else "unknown")
                await self._emit(run, "failed", error_category=category,
                                 http_status=status if status in (400, 401, 403, 429) else None,
                                 request_outcome="unknown" if run.dispatched else "not_dispatched")
                return
            except ConnectionClosed as exc:
                evidence = self._close_evidence(exc)
                await self._emit(run, "transport_closed", **evidence)
                await self._emit(run, "failed", error_category="connection_closed",
                                 request_outcome="unknown" if run.dispatched else "not_dispatched",
                                 **evidence)
                return
            except PermissionError:
                await self._emit(run, "failed", error_category="authorization_revoked",
                                 request_outcome="unknown" if run.dispatched else "not_dispatched")
                return
            except (OSError, TimeoutError) as exc:
                # Only a definitely pre-text transport failure may retry once.
                if not run.dispatched and attempt < attempts and \
                        time.monotonic() - started < self.limits.connect_budget_seconds:
                    await self._emit(run, "connect_retry", attempt=attempt + 1)
                    continue
                await self._emit(run, "failed", error_category="transport_or_timeout",
                                 request_outcome="unknown" if run.dispatched else "not_dispatched",
                                 close_origin="transport_lost")
                return
            except Exception as exc:
                category = ("audio_hard_limit" if str(exc) in
                            ("audio_limit", "audio_budget_exceeded") else "protocol_unknown")
                await self._emit(run, "failed", error_category=category,
                                 request_outcome="unknown" if run.dispatched else "not_dispatched")
                return

    async def _receive(self, run: OutputRun, ws: Any, started: float) -> None:
        final_turn = False
        close_sent = False
        while self.current is run and not run.cancelled:
            remaining = self.limits.total_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("output_deadline")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(
                    remaining, self.limits.close_wait_seconds if close_sent
                    else self.limits.keepalive_seconds))
            except asyncio.TimeoutError:
                if close_sent:
                    raise TimeoutError("final_deadline")
                await ws.send('{"keep_alive":true}')
                await self._emit(run, "keepalive_sent")
                continue
            if not isinstance(raw, str) or len(raw) > 12 * 1024 * 1024:
                raise ValueError("invalid_frame")
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("invalid_event")
            if "error" in event:
                code = event.get("error")
                if isinstance(code, dict):
                    code = code.get("code")
                category = KNOWN_ERRORS.get(code, "unknown") if isinstance(code, str) else "unknown"
                await self._emit(run, "failed", error_category=category,
                                 request_outcome="unknown")
                return
            recognized = False
            if "audio" in event:
                if final_turn or close_sent or not isinstance(event["audio"], str):
                    raise ValueError("unexpected_audio")
                try:
                    pcm = base64.b64decode(event["audio"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("invalid_audio") from exc
                if not pcm or len(pcm) % 2 or run.audio_bytes + len(pcm) > self.limits.max_audio_bytes:
                    raise ValueError("audio_limit")
                run.audio_bytes += len(pcm)
                run.sequence += 1
                timing = ({"text_dispatch_to_first_audio_ms": round(
                    (time.monotonic() - run.text_dispatch_at) * 1000, 3)}
                    if run.sequence == 1 and run.text_dispatch_at is not None else {})
                await self._emit(run, "audio", sequence=run.sequence,
                                 pcm_base64=event["audio"], sample_rate=24000,
                                 **timing)
                recognized = True
            if event.get("is_final_audio_for_turn") is True and not final_turn:
                final_turn = True
                await self._emit(run, "final_turn")
                await ws.send('{"close_socket":true}')
                close_sent = True
                await self._emit(run, "close_socket_sent")
                recognized = True
            if event.get("is_final") is True and close_sent:
                await self._emit(run, "final_protocol_event")
                return
            if not recognized:
                raise ValueError("unknown_event")
