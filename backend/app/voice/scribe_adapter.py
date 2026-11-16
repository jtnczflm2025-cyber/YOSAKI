"""Bounded server-side Scribe v2 realtime transport for the isolated draft route.

Constructing this object does not connect. Only an explicitly enabled draft
route calls ``open``. No transcript, key, or raw protocol frame is logged.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from urllib.parse import urlencode
from urllib.request import getproxies

import websockets


SCRIBE_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
MAX_EVENT_BYTES = 16_384
FINAL_WAIT_SECONDS = 10
ERROR_EVENTS = frozenset({"auth_error", "quota_exceeded", "transcriber_error",
    "input_error", "invalid_request", "error", "commit_throttled",
    "unaccepted_terms", "rate_limited", "queue_overflow", "resource_exhausted",
    "session_time_limit_exceeded", "chunk_size_exceeded", "insufficient_audio_activity"})


class ScribeProtocolError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def local_proxy() -> str | None:
    routes = getproxies()
    http, https = routes.get("http"), routes.get("https")
    if http and https and http != https:
        raise ScribeProtocolError("proxy_routes_disagree")
    proxy = https or http
    if proxy and not proxy.startswith("http://127.0.0.1:"):
        raise ScribeProtocolError("proxy_configuration_unsupported")
    return proxy


class ScribeRealtimeAdapter:
    def __init__(self, language: str, *, endpoint: str = SCRIBE_URL,
                 key: str | None = None, proxy: str | None = None):
        if language not in ("auto", "zh", "ja"):
            raise ValueError("language_invalid")
        if endpoint != SCRIBE_URL and not endpoint.startswith("ws://127.0.0.1:"):
            raise ValueError("endpoint_invalid")
        self.language = language
        self.endpoint = endpoint
        self.key = key
        self.proxy = proxy
        self.socket = None
        self.pending = b""
        self.closed = False
        self.observer = None

    def _note(self, kind: str, **data) -> None:
        if self.observer is not None:
            self.observer(kind, **data)

    async def open(self) -> None:
        key = self.key or os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not key:
            raise ScribeProtocolError("key_missing")
        params = {"model_id": "scribe_v2_realtime", "audio_format": "pcm_16000",
                  "commit_strategy": "manual"}
        if self.language != "auto":
            params["language_code"] = self.language
        url = self.endpoint + "?" + urlencode(params)
        proxy = self.proxy if self.endpoint != SCRIBE_URL else local_proxy()
        try:
            self._note("upstream_attempt")
            self.socket = await asyncio.wait_for(websockets.connect(
                url, additional_headers={"xi-api-key": key}, proxy=proxy,
                open_timeout=5, close_timeout=2, max_size=MAX_EVENT_BYTES,
                ping_interval=None), timeout=6)
            self._note("upstream_connected")
            event = await asyncio.wait_for(self._receive(), timeout=5)
            if event.get("message_type") != "session_started":
                raise ScribeProtocolError("session_start_invalid")
        except BaseException:
            await self.aclose()
            raise

    async def _receive(self) -> dict:
        if self.socket is None or self.closed:
            raise ScribeProtocolError("connection_closed")
        try:
            raw = await self.socket.recv()
        except websockets.exceptions.ConnectionClosed as exc:
            self._note("upstream_closed", initiator="remote" if exc.rcvd else "transport",
                       close_code=exc.rcvd.code if exc.rcvd else 0)
            raise ScribeProtocolError("provider_disconnected") from None
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
            raise ScribeProtocolError("event_size_invalid")
        try:
            event = json.loads(raw)
        except ValueError as exc:
            raise ScribeProtocolError("event_json_invalid") from exc
        if not isinstance(event, dict) or not isinstance(event.get("message_type"), str):
            raise ScribeProtocolError("event_shape_invalid")
        kind = event["message_type"]
        self._note("provider_event", event=kind if kind in ERROR_EVENTS or kind in
                   {"session_started", "partial_transcript", "committed_transcript"}
                   else "unrecognized")
        return event

    async def _drain(self, *, final: bool) -> list[dict]:
        result = []
        while True:
            try:
                event = await asyncio.wait_for(self._receive(), timeout=FINAL_WAIT_SECONDS if final else 0.02)
            except asyncio.TimeoutError:
                if final:
                    raise ScribeProtocolError("commit_timeout") from None
                return result
            kind = event["message_type"]
            if kind in ERROR_EVENTS:
                raise ScribeProtocolError(kind)
            if kind not in ("partial_transcript", "committed_transcript"):
                raise ScribeProtocolError("event_unrecognized")
            value = event.get("text")
            if not isinstance(value, str) or len(value) > 5000:
                raise ScribeProtocolError("transcript_invalid")
            result.append({"type": "committed" if kind == "committed_transcript" else "partial",
                           "text": value})
            if kind == "committed_transcript":
                return result

    async def feed(self, pcm: bytes, *, commit: bool) -> list[dict]:
        if self.socket is None or self.closed:
            raise ScribeProtocolError("connection_closed")
        if not commit:
            previous, self.pending = self.pending, pcm
            if not previous:
                return []
            chunk = previous
        else:
            chunk, self.pending = self.pending, b""
            if not chunk:
                raise ScribeProtocolError("empty_commit")
        self._note("commit_upstream_attempt" if commit else "audio_upstream_attempt",
                   bytes=len(chunk))
        await self.socket.send(json.dumps({"message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(chunk).decode("ascii"), "commit": commit}))
        self._note("commit_upstream_sent" if commit else "audio_upstream_sent",
                   bytes=len(chunk))
        return await self._drain(final=commit)

    async def aclose(self) -> None:
        self.closed = True
        self.pending = b""
        socket, self.socket = self.socket, None
        if socket is not None:
            try:
                await asyncio.wait_for(socket.close(), timeout=3)
                self._note("upstream_closed", initiator="local")
            except (OSError, TimeoutError):
                pass
