"""Standalone V1-B probe. Default invocation is offline dry-run.

This file never imports backend.app.main or writes SQLite. Real execution needs
an exact manifest, one-use run receipt, and explicit third-party confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote, urlencode, urlsplit
import urllib.request
import wave

import httpx
from websockets.asyncio.client import connect as default_ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("voice_v1b_manifest.json")
HTTP_ORIGIN = "https://api.elevenlabs.io"
WS_ORIGIN = "wss://api.elevenlabs.io"
TEXTS = (
    "你好，阿夜。现在请用自然的语气读出这句话。",
    "こんにちは、阿夜。自然な声でこの文を読んでください。",
    "你好，阿夜。我们开始测试实时语音。",
    "今夜もよろしくお願いします。",
)
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{5,63}\Z")
MAX_BYTES = 5 * 1024 * 1024
WS_EVENT_TYPES = frozenset({
    "audio", "final", "final_audio_for_turn", "error", "warning", "keep_alive",
    "session_started", "partial_transcript", "committed_transcript",
    "rate_limited", "auth_error", "unaccepted_terms",
})
WS_ERROR_CODES = frozenset({
    "invalid_request", "unauthorized", "forbidden", "rate_limited",
    "quota_exceeded", "voice_not_found", "model_not_found",
    "authentication_failed", "internal_error", "server_error", "timeout",
    "too_many_concurrent_requests",
    "invalid_parameters", "malformed_json", "invalid_api_key", "missing_api_key",
    "rate_limit_exceeded", "concurrent_limit_exceeded", "insufficient_credits",
    "unsupported_model", "invalid_voice_id", "service_unavailable",
})
WS_HTTP_ERROR_CODES = frozenset({400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504})
WS_ERROR_TYPES = frozenset({
    "validation_error", "invalid_request", "authentication_error", "payment_required",
    "authorization_error", "rate_limit_error", "internal_error", "service_unavailable",
})
WS_TEXT_DIAGNOSTIC_PATTERNS = (
    ("idle_timeout_candidate", re.compile(
        r"(?:inactivity|idle) timeout|(?:connection|websocket|session) "
        r"(?:closed|timed out|expired) (?:due to|after) (?:20 seconds of )?inactivity|"
        r"no (?:client )?messages? received (?:for|within) 20 seconds", re.I)),
    ("authentication_error", re.compile(
        r"unauthorized|authentication failed|invalid api key|missing api key|invalid token|forbidden", re.I)),
    ("quota_or_rate_limit", re.compile(
        r"rate limit exceeded|quota exceeded|insufficient credits|too many requests", re.I)),
    ("protocol_error", re.compile(
        r"invalid request|malformed json|invalid parameters|unsupported model|invalid voice id", re.I)),
)


class ProbeError(Exception):
    def __init__(self, code: str, *, unknown: bool = False):
        super().__init__(code)
        self.code, self.unknown = code, unknown


def ws_error_text_diagnostic(frame: dict) -> tuple[str, str]:
    """Inspect provider prose only in memory; emit a fixed category and field name."""
    for source, value in (("error", frame.get("error")),
                          ("error.message", frame.get("error", {}).get("message")
                           if isinstance(frame.get("error"), dict) else None),
                          ("detail.message", frame.get("detail", {}).get("message")
                           if isinstance(frame.get("detail"), dict) else None),
                          ("message", frame.get("message"))):
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.strip().split()).rstrip(".!?").casefold()
        if len(normalized) <= 160 and normalized.isascii():
            for category, pattern in WS_TEXT_DIAGNOSTIC_PATTERNS:
                if pattern.fullmatch(normalized):
                    return category, source
        return "unknown", source
    return "absent", "none"


def ws_event_diagnostic(frame: dict, started: float) -> dict:
    """Record only bounded protocol labels; never persist the provider frame."""
    source = "none"
    kind = None
    for field in ("message_type", "type", "event"):
        if field in frame:
            source, kind = field, frame[field]
            break
    if source == "none":
        for field, label in (("is_final_audio_for_turn", "final_audio_for_turn"),
                             ("is_final", "final")):
            if frame.get(field) is True:
                source, kind = field, label
                break
        if source == "none":
            for field in ("error", "audio"):
                if field in frame:
                    source, kind = field, field
                    break
        if source == "none" and isinstance(frame.get("detail"), dict):
            source, kind = "detail", "error"
    code_source = "none"
    code = None
    for field in ("error_code", "code"):
        if field in frame:
            code_source, code = field, frame[field]
            break
    if code is None:
        for field in ("error", "detail"):
            nested = frame.get(field)
            if isinstance(nested, dict):
                for key in ("code", "status"):
                    if key in nested:
                        code_source, code = field + "." + key, nested[key]
                        break
            if code is not None:
                break
    if type(code) is int and code in WS_HTTP_ERROR_CODES:
        safe_code = str(code)
    elif isinstance(code, str) and code in WS_ERROR_CODES:
        safe_code = code
    elif (isinstance(code, str) and len(code) <= 48 and
          re.fullmatch(r"[a-z]{2,20}(?:_[a-z]{2,20}){1,2}", code) and
          not code.startswith(("sk_", "xi_", "el_", "api_")) and
          not any(word in code for word in ("key", "token", "secret", "credential", "authorization"))):
        safe_code = code
    elif code is not None:
        safe_code = "unrecognized"
    else:
        safe_code = None
    code_type = ("integer" if type(code) is int else "string" if isinstance(code, str)
                 else "absent" if code is None else "other")
    code_fingerprint = None
    if safe_code == "unrecognized" and isinstance(code, (str, int)):
        token = str(code)
        if len(token) <= 64 and re.fullmatch(r"[A-Za-z0-9_.:-]+", token):
            code_fingerprint = hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
    error_type = None
    error_type_source = "none"
    for field in ("error", "detail"):
        nested = frame.get(field)
        if isinstance(nested, dict) and "type" in nested:
            error_type, error_type_source = nested["type"], field + ".type"
            break
    if (error_type is None and isinstance(frame.get("type"), str) and
            frame["type"] not in WS_EVENT_TYPES):
        error_type, error_type_source = frame["type"], "type"
    text_category, text_source = ws_error_text_diagnostic(frame)
    return {
        "event_type": (kind if isinstance(kind, str) and kind in WS_EVENT_TYPES else
                       "error" if "error" in frame or isinstance(frame.get("detail"), dict)
                       else "unrecognized"),
        "type_source": source,
        "error_payload_type": ("object" if isinstance(frame.get("error"), dict) else
                               "string" if isinstance(frame.get("error"), str) else
                               "other" if "error" in frame else "absent"),
        "error_code": safe_code,
        "error_code_source": code_source,
        "error_code_type": code_type,
        "error_code_recognition": ("absent" if code is None else "known" if
                                   (type(code) is int and code in WS_HTTP_ERROR_CODES) or
                                   (isinstance(code, str) and code in WS_ERROR_CODES) else
                                   "bounded_identifier_unverified" if safe_code != "unrecognized" else "unrecognized"),
        "structured_error_type": (error_type if isinstance(error_type, str) and
                                  error_type in WS_ERROR_TYPES else
                                  "unrecognized" if error_type is not None else None),
        "structured_error_type_source": error_type_source,
        "error_text_category": text_category,
        "error_text_source": text_source,
        "unrecognized_code_fingerprint": code_fingerprint,
        "elapsed_seconds": round(max(0.0, time.monotonic() - started), 3),
    }


def ws_close_diagnostic(ws, started: float, *, origin: str = "not_classified",
                        closed: ConnectionClosed | None = None) -> dict:
    """Separate a received close frame from local post-context cleanup state."""
    code = getattr(ws, "close_code", None)
    if type(code) is not int or not 1000 <= code <= 4999:
        code = None
    received = closed.rcvd if closed is not None else None
    remote_code = received.code if received is not None else None
    if type(remote_code) is not int or not 1000 <= remote_code <= 4999:
        remote_code = None
    return {
        "elapsed_seconds": round(max(0.0, time.monotonic() - started), 3),
        "close_origin": origin,
        "remote_close_code": remote_code,
        "remote_reason_present": bool(received.reason) if received is not None else False,
        "post_cleanup_close_code": code,
        "post_cleanup_reason_present": bool(getattr(ws, "close_reason", None)),
    }


def ws_failure_origin(exc: BaseException) -> tuple[str, ConnectionClosed | None]:
    """Classify only evidence observed before context-manager cleanup."""
    if isinstance(exc, ConnectionClosed):
        return ("remote_frame" if exc.rcvd is not None else "transport_interruption", exc)
    if isinstance(exc, asyncio.CancelledError):
        return "local_cancel_cleanup", None
    if isinstance(exc, TimeoutError):
        return "local_timeout_cleanup", None
    if isinstance(exc, ProbeError):
        return "local_cleanup_after_probe_error", None
    return "transport_or_local_unknown", None


def digest(data: dict) -> str:
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_manifest(m: dict) -> None:
    """Reject scope edits even when somebody hashes a changed manifest."""
    if not __debug__:
        raise ProbeError("manifest_validation_unavailable")
    try:
        assert set(m) == {"schema_version", "manifest_id", "character_id", "provider",
                          "model_standard", "model_realtime", "model_stt", "http_ttd",
                          "ttd_ws", "ttd_idle_ws", "scribe_ws", "limits"}
        assert (m["schema_version"], m["manifest_id"], m["character_id"], m["provider"]) == (
            1, "yosaki-v1b-fixed-2026-11-10", 1, "elevenlabs")
        assert (m["model_standard"], m["model_realtime"], m["model_stt"]) == (
            "eleven_v4", "eleven_v4_turbo", "scribe_v2_realtime")
        a, b = m["http_ttd"]
        x, y = m["ttd_ws"]["segments"]
        assert [z["text"] for z in (a, b, x, y)] == list(TEXTS)
        assert [z["characters"] for z in (a, b, x, y)] == [21, 26, 17, 14]
        assert [len(t) for t in TEXTS] == [21, 26, 17, 14]
        assert [(z["id"], z["language"], z["output_format"], z["timeout_seconds"], z["max_audio_seconds"])
                for z in (a, b)] == [
                    ("v4_zh", "zh", "pcm_16000", 30, 10),
                    ("v4_ja", "ja", "mp3_44100_128", 30, 10)]
        assert [(z["id"], z["language"]) for z in (x, y)] == [("turbo_zh", "zh"), ("turbo_ja", "ja")]
        assert {k: m["ttd_ws"][k] for k in ("id", "output_format", "timeout_seconds", "max_audio_seconds",
                                             "idle_between_segments_seconds", "keepalive_interval_seconds")} == {
            "id": "turbo", "output_format": "pcm_24000", "timeout_seconds": 55,
            "max_audio_seconds": 12, "idle_between_segments_seconds": 21,
            "keepalive_interval_seconds": 10}
        assert m["ttd_idle_ws"] == {"timeout_seconds": 30, "expected_idle_close_seconds": 20}
        assert m["scribe_ws"] == {
            "timeout_seconds": 20, "audio_format": "pcm_16000",
            "fixture": "tests/fixtures/voice_v1b_scribe_zh.wav",
            "fixture_sha256": "7f4323a61dbf8ceb939837a634f76dee8378fbc432d97f7aeafa59a95fd39952",
            "sample_rate": 16000, "sample_width_bytes": 2, "channels": 1,
            "frames": 92880, "audio_seconds": 5.805}
        assert m["limits"] == {
            "http_requests": 4, "ws_connections": 3, "tts_characters": 78,
            "stt_audio_seconds": 5.805, "http_deadline_seconds": 90,
            "ws_deadline_seconds": 105, "wall_seconds": 195,
            "max_response_bytes_each": MAX_BYTES}
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise ProbeError("manifest_scope_mismatch") from exc


def load_manifest(path: Path = MANIFEST) -> dict:
    m = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(m)
    return m


def fixture_pcm(m: dict, root: Path = ROOT) -> bytes:
    spec = m["scribe_ws"]
    p = root / spec["fixture"]
    if hashlib.sha256(p.read_bytes()).hexdigest() != spec["fixture_sha256"]:
        raise ProbeError("fixture_hash_mismatch")
    with wave.open(str(p), "rb") as wav:
        if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels(), wav.getnframes()) != (
                16000, 2, 1, 92880):
            raise ProbeError("fixture_format_mismatch")
        pcm = wav.readframes(wav.getnframes())
    if len(pcm) != 185760:
        raise ProbeError("fixture_size_mismatch")
    return pcm


def read_profile(database: Path) -> tuple[str, int]:
    if not database.is_absolute() or not database.is_file():
        raise ProbeError("database_path_invalid")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT provider, voice_id, revision, active FROM voice_profiles "
                             "WHERE character_id=1").fetchone()
    except sqlite3.Error as exc:
        raise ProbeError("profile_read_failed") from exc
    if not row or row[0] != "elevenlabs" or not row[1] or row[3] != 0:
        raise ProbeError("profile_not_inactive")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", row[1]):
        raise ProbeError("voice_id_invalid")
    return row[1], row[2]


def network_proxy() -> str | None:
    """Resolve the local Windows/user proxy once; HTTP and WS must share it."""
    proxies = urllib.request.getproxies()
    http, https = proxies.get("http"), proxies.get("https")
    if http and https and http != https:
        raise ProbeError("proxy_routes_disagree")
    target = https or http
    if not target:
        return None
    url = urlsplit(target if "://" in target else "http://" + target)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or
            url.username or url.password or url.path or url.query or url.fragment):
        raise ProbeError("proxy_configuration_unsupported")
    return f"http://127.0.0.1:{url.port}"


def verify_approval(a: dict, m: dict, run_id: str, voice_id: str, revision: int,
                    proxy_url: str | None) -> None:
    if not RUN_ID.fullmatch(run_id):
        raise ProbeError("run_id_invalid")
    required = {
        "schema_version": 1, "run_id": run_id,
        "manifest_sha256": digest(m),
        "voice_id_sha256": hashlib.sha256(voice_id.encode("utf-8")).hexdigest(),
        "character_id": 1, "profile_revision": revision, "limits": m["limits"],
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
        "reviewed_linear_reference_usd": 0.005628875,
        "network_proxy_url": proxy_url,
    }
    if not isinstance(a, dict) or any(a.get(k) != v for k, v in required.items()):
        raise ProbeError("approval_mismatch")
    if set(a) != set(required):
        raise ProbeError("approval_unknown_fields")


def mp3_seconds(data: bytes) -> float:
    """Read MPEG Layer III frame durations; unknown MP3 layout fails closed."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        i = 10 + ((data[6] & 127) << 21) + ((data[7] & 127) << 14) + ((data[8] & 127) << 7) + (data[9] & 127)
    seconds = 0.0
    frames = 0
    while i + 4 <= len(data):
        if data[i:i + 3] == b"TAG" and len(data) - i == 128:
            i = len(data)
            break
        h = int.from_bytes(data[i:i + 4], "big")
        version, layer = (h >> 19) & 3, (h >> 17) & 3
        bitrate_index, rate_index = (h >> 12) & 15, (h >> 10) & 3
        if h >> 21 != 0x7ff or version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
            raise ProbeError("mp3_header_invalid", unknown=True)
        table = ([0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
                 if version == 3 else [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])
        rate = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000),
                0: (11025, 12000, 8000)}[version][rate_index]
        samples = 1152 if version == 3 else 576
        size = (144 if version == 3 else 72) * table[bitrate_index] * 1000 // rate + ((h >> 9) & 1)
        if size < 4 or i + size > len(data):
            raise ProbeError("mp3_frame_incomplete", unknown=True)
        i += size
        frames += 1
        seconds += samples / rate
    if not frames or i != len(data):
        raise ProbeError("mp3_duration_unknown", unknown=True)
    return seconds


class Recorder:
    """Write only whitelisted event fields and audio; never provider payloads/headers."""

    def __init__(self, directory: Path, run_id: str, m: dict, *, simulated: bool):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.state = {
            "run_id": run_id, "manifest_sha256": digest(m),
            "mode": "simulation" if simulated else "real", "status": "running",
            "usage": {"http_requests": 0, "ws_connections": 0, "tts_characters_dispatched": 0,
                      "stt_audio_seconds_dispatched": 0.0, "output_audio_bytes": 0,
                      "provider_billed_credits": None, "provider_billed_usd": None},
            "events": [], "audio": [], "error_code": None,
        }
        self.save()

    def event(self, item: str, event: str, **safe) -> None:
        self.state["events"].append({"item": item, "event": event, **safe})
        self.save()

    def audio_file(self, name: str, data: bytes, *, complete: bool, seconds: float | None) -> None:
        suffix = ".mp3" if name == "v4_ja" else ".pcm"
        filename = name + suffix + ("" if complete else ".part")
        (self.directory / filename).write_bytes(data)
        self.state["audio"].append({"item": name, "file": filename, "bytes": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest(),
                                    "seconds": seconds, "complete": complete})
        self.state["usage"]["output_audio_bytes"] += len(data)
        self.save()

    def save(self) -> None:
        tmp = self.directory / "result.json.tmp"
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.directory / "result.json")


class Runner:
    def __init__(self, m: dict, recorder: Recorder, *, key: str, voice_id: str,
                 http_transport: httpx.AsyncBaseTransport | None = None,
                 ws_connect=default_ws_connect, http_origin: str = HTTP_ORIGIN,
                 ws_origin: str = WS_ORIGIN, simulated: bool = False, time_scale: float = 1.0,
                 dispatch_granted: bool = False, proxy_url: str | None = None):
        validate_manifest(m)
        if not key or not voice_id or not 0 < time_scale <= 1:
            raise ProbeError("runner_config_invalid")
        if simulated:
            http_url, ws_url = urlsplit(http_origin), urlsplit(ws_origin)
            if (http_transport is None or http_url.scheme != "http" or http_url.hostname != "127.0.0.1"
                    or ws_url.scheme != "ws" or ws_url.hostname != "127.0.0.1" or proxy_url is not None):
                raise ProbeError("simulation_transport_invalid")
        elif (not dispatch_granted or http_origin != HTTP_ORIGIN or
              ws_origin != WS_ORIGIN or time_scale != 1):
            raise ProbeError("real_target_invalid")
        self.m, self.r = m, recorder
        self.key, self.voice_id = key, voice_id
        self.http_transport, self.ws_connect = http_transport, ws_connect
        self.http_origin, self.ws_origin = http_origin, ws_origin
        self.simulated, self.time_scale = simulated, time_scale
        self.proxy_url = proxy_url
        self.http_deadlines = self.ws_deadlines = 0

    def _http_budget(self, seconds: int, characters: int = 0) -> None:
        u = self.r.state["usage"]
        if (u["http_requests"] >= 4 or self.http_deadlines + seconds > 90 or
                u["tts_characters_dispatched"] + characters > 78):
            raise ProbeError("dispatch_budget_exceeded")
        u["http_requests"] += 1
        u["tts_characters_dispatched"] += characters
        self.http_deadlines += seconds
        self.r.save()

    def _ws_budget(self, seconds: int) -> None:
        u = self.r.state["usage"]
        if u["ws_connections"] >= 3 or self.ws_deadlines + seconds > 105:
            raise ProbeError("dispatch_budget_exceeded")
        u["ws_connections"] += 1
        self.ws_deadlines += seconds
        self.r.save()

    async def http(self, method: str, path: str, timeout: int, item: str,
                   *, body: dict | None = None, characters: int = 0) -> bytes:
        self._http_budget(timeout, characters)
        self.r.event(item, "dispatched", transport="http", characters=characters)
        chunks = []
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(base_url=self.http_origin, transport=self.http_transport,
                                         proxy=self.proxy_url, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(timeout)) as client:
                async with client.stream(method, path, headers={"xi-api-key": self.key}, json=body) as response:
                    self.r.event(item, "http_status", status=response.status_code)
                    if response.status_code != 200:
                        raise ProbeError("http_" + str(response.status_code))
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ProbeError("response_size_limit", unknown=True)
                        chunks.append(chunk)
                    return b"".join(chunks)
        except ProbeError:
            if method == "POST" and chunks:
                self.r.audio_file(item, b"".join(chunks), complete=False, seconds=None)
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if method == "POST" and chunks:
                self.r.audio_file(item, b"".join(chunks), complete=False, seconds=None)
            raise ProbeError("http_transport_unknown", unknown=True) from exc
        finally:
            self.r.event(item, "http_elapsed", seconds=round(time.monotonic() - started, 3))

    def ws(self, path: str):
        return self.ws_connect(self.ws_origin + path, additional_headers={"xi-api-key": self.key},
                               open_timeout=5, close_timeout=1, ping_interval=None,
                               proxy=self.proxy_url)

    @staticmethod
    async def recv_json(ws, item: str) -> dict:
        try:
            obj = json.loads(await ws.recv())
            if not isinstance(obj, dict):
                raise ValueError
            return obj
        except (ValueError, TypeError) as exc:
            raise ProbeError(item + "_invalid_event", unknown=True) from exc

    async def metadata(self) -> None:
        raw = await self.http("GET", "/v1/models", 15, "models")
        try:
            models = json.loads(raw)
            ids = {x["model_id"] for x in models if isinstance(x, dict) and isinstance(x.get("model_id"), str)}
        except (ValueError, TypeError) as exc:
            raise ProbeError("models_invalid", unknown=True) from exc
        tts_needed = {"eleven_v4", "eleven_v4_turbo"}
        self.r.event("models", "catalog_checked", tts_present=sorted(tts_needed & ids),
                     scribe_status=("listed_unverified" if "scribe_v2_realtime" in ids
                                    else "not_listed_unverified"))
        if not tts_needed <= ids:
            raise ProbeError("tts_models_missing")
        raw = await self.http("GET", "/v1/voices/" + quote(self.voice_id, safe=""), 15, "voice")
        try:
            voice = json.loads(raw)
            if not isinstance(voice, dict) or voice.get("voice_id") != self.voice_id:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ProbeError("voice_invalid", unknown=True) from exc
        category = voice.get("category")
        self.r.event("voice", "validated", category=category if category in ("cloned", "generated", "premade") else "unknown")

    async def http_ttd(self, spec: dict) -> None:
        body = {"model_id": "eleven_v4", "language_code": spec["language"],
                "inputs": [{"text": spec["text"], "voice_id": self.voice_id}]}
        path = "/v1/text-to-dialogue?" + urlencode({"output_format": spec["output_format"]})
        raw = await self.http("POST", path, spec["timeout_seconds"], spec["id"],
                              body=body, characters=spec["characters"])
        if not raw:
            raise ProbeError("empty_audio", unknown=True)
        try:
            if spec["output_format"] == "pcm_16000":
                if len(raw) % 2:
                    raise ProbeError("pcm_odd_size", unknown=True)
                seconds = len(raw) / 32000
            else:
                seconds = mp3_seconds(raw)
        except ProbeError:
            self.r.audio_file(spec["id"], raw, complete=False, seconds=None)
            raise
        if seconds > spec["max_audio_seconds"]:
            self.r.audio_file(spec["id"], raw, complete=False, seconds=seconds)
            raise ProbeError("audio_duration_limit", unknown=True)
        self.r.audio_file(spec["id"], raw, complete=True, seconds=seconds)
        self.r.event(spec["id"], "completed", audio_seconds=seconds)

    async def ttd_turn(self, ws, spec: dict, audio: bytearray, *, new_turn: bool) -> None:
        usage = self.r.state["usage"]
        if usage["tts_characters_dispatched"] + spec["characters"] > 78:
            raise ProbeError("dispatch_budget_exceeded")
        usage["tts_characters_dispatched"] += spec["characters"]
        self.r.event(spec["id"], "dispatched", transport="ws", characters=spec["characters"])
        line = {"text": spec["text"], "voice_id": self.voice_id}
        if new_turn:
            line["new_turn"] = True
        await ws.send(json.dumps({"inputs": [line], "flush": True}, ensure_ascii=False))
        started = time.monotonic()
        while True:
            obj = await self.recv_json(ws, spec["id"])
            if "error" in obj:
                self.r.event(spec["id"], "ws_diagnostic", **ws_event_diagnostic(obj, started))
                raise ProbeError("ttd_provider_error", unknown=True)
            recognized = "audio" in obj
            if "audio" in obj:
                try:
                    chunk = base64.b64decode(obj["audio"], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ProbeError("ttd_audio_invalid", unknown=True) from exc
                audio.extend(chunk)
                if len(audio) > MAX_BYTES:
                    raise ProbeError("response_size_limit", unknown=True)
                self.r.event(spec["id"], "audio_chunk", bytes=len(chunk))
            if obj.get("is_final_audio_for_turn") is True:
                self.r.event(spec["id"], "final_turn")
                return
            if not recognized:
                self.r.event(spec["id"], "ws_diagnostic", **ws_event_diagnostic(obj, started))
                raise ProbeError("ttd_unexpected_event", unknown=True)

    async def ttd_keepalive_gap(self, ws, *, item: str = "turbo") -> int:
        spec = self.m["ttd_ws"]
        idle = spec["idle_between_segments_seconds"] * self.time_scale
        interval = spec["keepalive_interval_seconds"] * self.time_scale
        clock = asyncio.get_running_loop()
        end, next_keepalive = clock.time() + idle, clock.time() + interval
        gap_started = time.monotonic()
        sequence = 0
        while clock.time() < end:
            timeout = max(0.0001, min(end, next_keepalive) - clock.time())
            try:
                obj = await asyncio.wait_for(self.recv_json(ws, item), timeout=timeout)
                self.r.event(item, "ws_diagnostic", **ws_event_diagnostic(obj, gap_started))
                raise ProbeError("ttd_late_event", unknown=True)
            except asyncio.TimeoutError:
                pass
            if clock.time() >= next_keepalive and next_keepalive < end:
                sequence += 1
                self.r.event(item, "keep_alive_attempt", sequence=sequence,
                             elapsed_seconds=round(time.monotonic() - gap_started, 3))
                await ws.send('{"keep_alive":true}')
                self.r.event(item, "keep_alive_sent", sequence=sequence,
                             elapsed_seconds=round(time.monotonic() - gap_started, 3))
                next_keepalive += interval
        return sequence

    async def ttd_ws(self) -> None:
        spec = self.m["ttd_ws"]
        self._ws_budget(spec["timeout_seconds"])
        path = "/v1/text-to-dialogue/stream-input?" + urlencode({
            "model_id": "eleven_v4_turbo", "output_format": "pcm_24000"})
        self.r.event("turbo", "dispatched", transport="ws_connection")
        audio = [bytearray(), bytearray()]
        started = time.monotonic()
        complete = False
        ws_ref = None
        close_origin = "not_classified"
        closed_exc = None
        try:
            async with asyncio.timeout(spec["timeout_seconds"]):
                async with self.ws(path) as ws:
                    ws_ref = ws
                    await ws.send(json.dumps({"voices": [self.voice_id]}))
                    await self.ttd_turn(ws, spec["segments"][0], audio[0], new_turn=False)
                    await self.ttd_keepalive_gap(ws)
                    await self.ttd_turn(ws, spec["segments"][1], audio[1], new_turn=True)
                    self.r.event("turbo", "close_socket_attempt",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    await ws.send('{"close_socket":true}')
                    self.r.event("turbo", "close_socket_sent",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    final = await self.recv_json(ws, "turbo")
                    if final.get("is_final") is not True:
                        self.r.event("turbo", "ws_diagnostic", **ws_event_diagnostic(final, started))
                        raise ProbeError("ttd_close_final_missing", unknown=True)
                    self.r.event("turbo", "final_observed",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    close_origin = "local_cleanup_after_final"
            if any(not part or len(part) % 2 for part in audio):
                raise ProbeError("ttd_pcm_invalid", unknown=True)
            seconds = sum(map(len, audio)) / 48000
            if seconds > spec["max_audio_seconds"]:
                raise ProbeError("audio_duration_limit", unknown=True)
            for item, part in zip(spec["segments"], audio):
                self.r.audio_file(item["id"], bytes(part), complete=True, seconds=len(part) / 48000)
            complete = True
            self.r.event("turbo", "completed", audio_seconds=seconds)
        except BaseException as exc:
            close_origin, closed_exc = ws_failure_origin(exc)
            if not complete:
                for item, part in zip(spec["segments"], audio):
                    if part:
                        self.r.audio_file(item["id"], bytes(part), complete=False, seconds=None)
            raise
        finally:
            if ws_ref is not None:
                self.r.event("turbo", "connection_end",
                             **ws_close_diagnostic(ws_ref, started, origin=close_origin,
                                                   closed=closed_exc))
            self.r.event("turbo", "connection_elapsed", seconds=round(time.monotonic() - started, 3))

    async def idle_ws(self) -> None:
        spec = self.m["ttd_idle_ws"]
        self._ws_budget(spec["timeout_seconds"])
        path = "/v1/text-to-dialogue/stream-input?" + urlencode({
            "model_id": "eleven_v4_turbo", "output_format": "pcm_24000"})
        self.r.event("ttd_idle", "dispatched", transport="ws_connection")
        start = time.monotonic()
        ws_ref = None
        close_origin = "not_classified"
        closed_exc = None
        try:
            async with asyncio.timeout(spec["timeout_seconds"]):
                async with self.ws(path) as ws:
                    ws_ref = ws
                    await ws.send(json.dumps({"voices": [self.voice_id]}))
                    try:
                        obj = await self.recv_json(ws, "ttd_idle")
                    except ProbeError as exc:
                        if exc.code == "ttd_idle_invalid_event":
                            self.r.event("ttd_idle", "ws_diagnostic",
                                         **{**ws_event_diagnostic({}, start),
                                            "event_type": "invalid_json"})
                        close_origin = "local_cleanup_after_invalid_event"
                        raise
                    except ConnectionClosed as exc:
                        closed_exc = exc
                        if exc.rcvd is None:
                            close_origin = "transport_interruption"
                            raise ProbeError("ttd_idle_transport_interrupted", unknown=True) from exc
                        close_origin = "remote_frame"
                        if exc.rcvd.code not in (1000, 1001):
                            raise ProbeError("ttd_idle_remote_error_close", unknown=True) from exc
                    else:
                        self.r.event("ttd_idle", "ws_diagnostic", **ws_event_diagnostic(obj, start))
                        close_origin = "local_cleanup_after_event"
                        raise ProbeError("ttd_idle_unexpected_event", unknown=True)
        except TimeoutError as exc:
            close_origin = "local_timeout_cleanup"
            raise ProbeError("ttd_idle_timeout", unknown=True) from exc
        except asyncio.CancelledError:
            close_origin = "local_cancel_cleanup"
            raise
        except BaseException:
            if close_origin == "not_classified":
                close_origin = "transport_or_local_unknown"
            raise
        finally:
            if ws_ref is not None:
                self.r.event("ttd_idle", "connection_end",
                             **ws_close_diagnostic(ws_ref, start, origin=close_origin,
                                                   closed=closed_exc))
        elapsed = time.monotonic() - start
        if not self.simulated and not 15 <= elapsed <= 30:
            raise ProbeError("ttd_idle_timing_mismatch", unknown=True)
        self.r.event("ttd_idle", "closed", elapsed_seconds=round(elapsed, 3))

    async def scribe_ws(self, pcm: bytes) -> None:
        spec = self.m["scribe_ws"]
        self._ws_budget(spec["timeout_seconds"])
        path = "/v1/speech-to-text/realtime?" + urlencode({
            "model_id": "scribe_v2_realtime", "audio_format": "pcm_16000",
            "commit_strategy": "manual", "language_code": "zh"})
        self.r.event("scribe", "dispatched", transport="ws_connection")
        started = time.monotonic()
        ws_ref = None
        close_origin = "not_classified"
        closed_exc = None
        try:
            async with asyncio.timeout(spec["timeout_seconds"]):
                async with self.ws(path) as ws:
                    ws_ref = ws
                    hello = await self.recv_json(ws, "scribe")
                    if hello.get("message_type") != "session_started":
                        self.r.event("scribe", "ws_diagnostic", **ws_event_diagnostic(hello, started))
                        raise ProbeError("scribe_session_missing", unknown=True)
                    self.r.event("scribe", "session_started")
                    chunk_size = 16000  # 0.5 seconds of PCM 16 kHz, s16le, mono
                    chunks = [pcm[i:i + chunk_size] for i in range(0, len(pcm), chunk_size)]
                    for i, chunk in enumerate(chunks):
                        duration = len(chunk) / 32000
                        usage = self.r.state["usage"]
                        if usage["stt_audio_seconds_dispatched"] + duration > 5.8050001:
                            raise ProbeError("dispatch_budget_exceeded")
                        usage["stt_audio_seconds_dispatched"] = round(
                            usage["stt_audio_seconds_dispatched"] + duration, 6)
                        self.r.save()
                        await ws.send(json.dumps({"message_type": "input_audio_chunk",
                                                  "audio_base_64": base64.b64encode(chunk).decode("ascii"),
                                                  "commit": i == len(chunks) - 1}))
                        if not self.simulated:
                            await asyncio.sleep(duration)
                        while True:
                            try:
                                early = await asyncio.wait_for(self.recv_json(ws, "scribe"), timeout=0.001)
                            except asyncio.TimeoutError:
                                break
                            if self.scribe_event(early, started):
                                if i != len(chunks) - 1:
                                    raise ProbeError("scribe_early_commit", unknown=True)
                                self.r.event("scribe", "audio_sent", chunks=len(chunks), seconds=5.805)
                                close_origin = "local_cleanup_after_commit"
                                return
                    self.r.event("scribe", "audio_sent", chunks=len(chunks), seconds=5.805)
                    while True:
                        if self.scribe_event(await self.recv_json(ws, "scribe"), started):
                            close_origin = "local_cleanup_after_commit"
                            return
        except TimeoutError as exc:
            close_origin = "local_timeout_cleanup"
            raise ProbeError("scribe_timeout", unknown=True) from exc
        except BaseException as exc:
            close_origin, closed_exc = ws_failure_origin(exc)
            raise
        finally:
            if ws_ref is not None:
                self.r.event("scribe", "connection_end",
                             **ws_close_diagnostic(ws_ref, started, origin=close_origin,
                                                   closed=closed_exc))
            self.r.event("scribe", "connection_elapsed", seconds=round(time.monotonic() - started, 3))

    def scribe_event(self, obj: dict, started: float) -> bool:
        kind = obj.get("message_type")
        if kind in ("rate_limited", "error", "auth_error", "unaccepted_terms"):
            self.r.event("scribe", "ws_diagnostic", **ws_event_diagnostic(obj, started))
            raise ProbeError("scribe_" + kind, unknown=True)
        if kind == "partial_transcript":
            self.r.event("scribe", "partial_transcript")
            return False
        if kind == "warning":
            self.r.event("scribe", "warning")
            return False
        if kind == "committed_transcript":
            if not isinstance(obj.get("text"), str):
                raise ProbeError("scribe_transcript_invalid", unknown=True)
            self.r.event("scribe", "committed_transcript", text=obj["text"])
            return True
        self.r.event("scribe", "ws_diagnostic", **ws_event_diagnostic(obj, started))
        raise ProbeError("scribe_unexpected_event", unknown=True)

    async def run(self, pcm: bytes) -> dict:
        if len(pcm) != 185760:
            raise ProbeError("fixture_size_mismatch")
        try:
            async with asyncio.timeout(self.m["limits"]["wall_seconds"]):
                await self.metadata()
                for item in self.m["http_ttd"]:
                    await self.http_ttd(item)
                await self.ttd_ws()
                await self.idle_ws()
                await self.scribe_ws(pcm)
            u = self.r.state["usage"]
            if (u["http_requests"], u["ws_connections"], u["tts_characters_dispatched"],
                    u["stt_audio_seconds_dispatched"]) != (4, 3, 78, 5.805):
                raise ProbeError("actual_usage_mismatch", unknown=True)
            self.r.state["status"] = "completed"
        except BaseException as exc:
            if isinstance(exc, ProbeError):
                error = exc
            elif isinstance(exc, InvalidStatus):
                status = getattr(getattr(exc, "response", None), "status_code", None)
                error = ProbeError("ws_http_" + str(status) if status in (401, 403, 429)
                                   else "ws_handshake_unknown", unknown=status not in (401, 403, 429))
            else:
                error = ProbeError("transport_or_protocol_unknown", unknown=True)
            self.r.state["status"] = "unknown" if error.unknown else "failed"
            self.r.state["error_code"] = error.code
            self.r.save()
            raise error from None
        self.r.save()
        return self.r.state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed Voice V1-B probe; dry-run by default")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        m = load_manifest()
        if not args.execute_real:
            print(json.dumps({"mode": "dry-run", "manifest_id": m["manifest_id"],
                              "manifest_sha256": digest(m), "real_requests": 0,
                              "http_requests_limit": 4, "ws_connections_limit": 3,
                              "tts_characters_limit": 78, "stt_audio_seconds_limit": 5.805,
                              "provider_billing": "unverified", "status": "no_network"}, ensure_ascii=False))
            return 0
        if not all((args.run_id, args.approval_file, args.database, args.results_dir)):
            raise ProbeError("real_arguments_missing")
        voice_id, revision = read_profile(args.database.resolve())
        proxy_url = network_proxy()
        approval = json.loads(args.approval_file.read_text(encoding="utf-8"))
        verify_approval(approval, m, args.run_id, voice_id, revision, proxy_url)
        pcm = fixture_pcm(m)
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not key:
            raise ProbeError("api_key_not_configured")
        if args.results_dir.exists():
            raise ProbeError("results_directory_exists")
        used = args.approval_file.with_name(args.approval_file.name + ".used")
        try:
            with used.open("x", encoding="utf-8") as fp:
                fp.write(args.run_id + "\n")
        except FileExistsError as exc:
            raise ProbeError("approval_already_used") from exc
        rec = Recorder(args.results_dir, args.run_id, m, simulated=False)
        rec.state["reviewed_linear_reference_usd"] = approval["reviewed_linear_reference_usd"]
        rec.save()
        asyncio.run(Runner(m, rec, key=key, voice_id=voice_id, dispatch_granted=True,
                           proxy_url=proxy_url).run(pcm))
        print(json.dumps({"mode": "real", "run_id": args.run_id, "status": "completed",
                          "results": str(args.results_dir / "result.json")}, ensure_ascii=False))
        return 0
    except (ProbeError, OSError, ValueError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, ProbeError) else "local_setup_failed"
        print(json.dumps({"mode": "real" if args.execute_real else "dry-run", "status": "stopped",
                          "error_code": code}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
