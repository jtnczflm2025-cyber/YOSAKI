"""One-use V4 TTD stream gate. Dry-run by default; never imports main.py.

Real mode requires a separately created approval receipt. The CLI has no
option to override the supplier URL, database, limits, or frozen input.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import wave

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

from backend.app.voice.realtime_output import OutputLimits, OutputRelay
from backend.app.voice.realtime_ttd_connector import RealTTDConnector
from scripts.voice_provider_probe import ProbeError, network_proxy


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "VOICE_V4_STREAM_R2_MANIFEST.json"
DATABASE = ROOT / "data" / "yosaki.sqlite3"
RUN_ID = "voice-v4-stream-20261111-r2"
NOTICE_VERSION = "2026-11-08-v1"
ORIGIN = "http://127.0.0.1:15174"
PORT = 18769
FROZEN_TEXT = "これは音声の接続と再生を確かめるための短い文章です。落ち着いた声で、自然な間を保って読み上げてください。"
CODE_PATHS = (
    "backend/app/voice/realtime_output.py",
    "backend/app/voice/realtime_ttd_connector.py",
    "frontend/src/voice/realtimeOutput.ts",
    "frontend/src/voice/voiceV4Real.ts",
    "frontend/voice-v4-real.html",
    "scripts/voice_provider_probe.py",
    "scripts/voice_v4_stream_probe.py",
    "backend/requirements.txt",
    "package-lock.json",
)
METRICS = frozenset(("first_audio_received", "first_playback_started",
                     "receive_completed", "playback_ended", "stopped"))
PLAYBACK_ERRORS = frozenset(("playback_queue_full", "audio_hard_limit",
                             "audio_frame_limit", "invalid_pcm",
                             "invalid_audio_event", "unknown_playback_error"))


class GateError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def code_set_sha256(root: Path = ROOT) -> str:
    lines = []
    for name in CODE_PATHS:
        lines.append(f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1, "status": "awaiting_single_real_approval",
        "run_id": RUN_ID,
        "purpose": "one_short_japanese_ttd_ws_output_and_local_playback",
        "character_id": 1, "profile_revision": 3,
        "voice_id_sha256": "e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767",
        "notice_version": NOTICE_VERSION, "provider": "elevenlabs",
        "model_id": "eleven_v4_turbo", "output_format": "pcm_24000",
        "pcm_sample_rate_hz": 24000, "pcm_encoding": "s16le",
        "pcm_channels_expected": 1,
        "language_code": "ja", "voice_settings": "omitted",
        "text": FROZEN_TEXT, "characters": 52,
        "limits": {"provider_http_requests": 0, "provider_ws_connections": 1,
                   "provider_text_dispatches": 1, "tts_characters": 52,
                   "connection_seconds": 45, "process_seconds": 50,
                   "max_audio_bytes": 8388608, "automatic_text_retries": 0,
                   "safe_pre_text_connect_retries": 0,
                   "connect_retry_budget_seconds": 5},
        "failure_policy": "stop_on_auth_quota_protocol_unknown_timeout_or_disconnect; never_replay_dispatched_text",
        "billing": "unknown_until_provider_account_observation",
    }
    if manifest != expected or len(manifest["text"]) != 52:
        raise GateError("manifest_scope_changed")
    return manifest


def read_profile(database: Path, manifest: dict) -> str:
    if database.resolve() != DATABASE.resolve() or not database.is_file():
        raise GateError("daily_database_path_mismatch")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            actual = db.execute("PRAGMA database_list").fetchone()[2]
            if Path(actual).resolve() != database.resolve():
                raise GateError("sqlite_connection_path_mismatch")
            row = db.execute("SELECT provider,voice_id,revision,active FROM voice_profiles "
                             "WHERE character_id=1").fetchone()
            consents = db.execute("SELECT notice_version,accepted_at FROM voice_privacy_consents "
                                  "WHERE provider='elevenlabs'").fetchall()
    except sqlite3.Error as exc:
        raise GateError("local_authorization_read_failed") from exc
    if (not row or row[0] != "elevenlabs" or row[2:] != (3, 1) or
            not isinstance(row[1], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", row[1]) or
            hashlib.sha256(row[1].encode()).hexdigest() != manifest["voice_id_sha256"] or
            len(consents) != 1 or consents[0][0] != NOTICE_VERSION or not consents[0][1]):
        raise GateError("profile_or_consent_changed")
    return row[1]


def verify_receipt(receipt: dict, manifest: dict, code_hash: str,
                   proxy_url: str | None) -> None:
    expected = {
        "schema_version": 1, "run_id": RUN_ID,
        "manifest_sha256": digest(manifest), "code_set_sha256": code_hash,
        "voice_id_sha256": manifest["voice_id_sha256"],
        "profile_revision": 3, "notice_version": NOTICE_VERSION,
        "limits": manifest["limits"], "network_proxy_url": proxy_url,
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
    }
    if not isinstance(receipt, dict) or receipt != expected:
        raise GateError("approval_mismatch")


def consume_receipt(path: Path, root: Path, manifest: dict,
                    code_hash: str, proxy_url: str | None) -> Path:
    root = root.resolve()
    if path.resolve() != (root / f"{RUN_ID}.approval.json").resolve():
        raise GateError("approval_path_invalid")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    verify_receipt(receipt, manifest, code_hash, proxy_url)
    results = root / RUN_ID
    if results.exists():
        raise GateError("result_directory_exists")
    used = path.with_name(path.name + ".used")
    try:
        with used.open("x", encoding="utf-8") as output:
            output.write(RUN_ID + "\n")
    except FileExistsError as exc:
        raise GateError("approval_already_used") from exc
    results.mkdir(parents=False, exist_ok=False)
    return results


class ProbeRuntime:
    def __init__(self, manifest: dict, result_dir: Path, voice_id: str,
                 connector, *, connection_seconds: float = 45) -> None:
        self.manifest = manifest
        self.result_dir = result_dir
        self.voice_id = voice_id
        self.connector = connector
        self.connection_seconds = connection_seconds
        self.started_at = time.monotonic()
        self.done = asyncio.Event()
        self.started = False
        self.browser_nonce = secrets.token_urlsafe(24)
        self.browser: WebSocket | None = None
        self.relay: OutputRelay | None = None
        self.audio = bytearray()
        self.state = {
            "run_id": RUN_ID, "manifest_sha256": digest(manifest),
            "status": "armed", "request_outcome": "not_dispatched",
            "billing_observation": "unknown", "usage": {
                "provider_http_requests": 0, "provider_ws_connections": 0,
                "provider_text_dispatches": 0, "tts_characters_dispatched": 0,
                "audio_bytes_received": 0},
            "events": [], "browser_metrics": {}, "browser_diagnostics": [],
            "phase_markers": {},
            "streaming_observation": "unverified",
        }
        self.save()

    def save(self) -> None:
        path = self.result_dir / "result.json"
        temporary = self.result_dir / "result.json.tmp"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def record(self, kind: str, **fields) -> None:
        self.state["events"].append({"event": kind,
                                     "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
                                     **fields})
        self.save()

    def open_provider(self):
        usage = self.state["usage"]
        if usage["provider_ws_connections"] >= 1:
            raise GateError("ws_budget_exceeded")
        usage["provider_ws_connections"] += 1
        self.record("provider_ws_attempt")
        return self.connector()

    async def emit(self, event: dict) -> None:
        kind = event["type"]
        if kind == "text_dispatch_attempt":
            usage = self.state["usage"]
            if usage["provider_text_dispatches"] or event.get("characters") != 52:
                raise GateError("text_budget_exceeded")
            usage["provider_text_dispatches"] = 1
            usage["tts_characters_dispatched"] = 52
            self.state["request_outcome"] = "unknown"
        if kind == "audio":
            pcm = base64.b64decode(event["pcm_base64"], validate=True)
            if len(self.audio) + len(pcm) > self.manifest["limits"]["max_audio_bytes"]:
                raise GateError("audio_budget_exceeded")
            self.audio.extend(pcm)
            self.state["usage"]["audio_bytes_received"] = len(self.audio)
            with (self.result_dir / "audio.pcm.part").open("ab") as output:
                output.write(pcm)
            if not any(e["event"] == "first_audio_received" for e in self.state["events"]):
                self.record("first_audio_received")
        safe = {key: event[key] for key in (
            "request_id", "session_id", "generation_id", "segment_id",
            "connection_id", "connection_epoch", "sequence", "characters",
            "attempt", "audio_bytes", "audio_chunks", "error_category",
            "http_status", "request_outcome", "close_origin", "remote_close_code")
            if key in event}
        self.record(kind, **safe)
        if kind in ("final_turn", "close_socket_sent", "final_protocol_event",
                    "transport_closed", "completed", "stopped", "failed"):
            self.state["phase_markers"][kind] = round(time.monotonic() - self.started_at, 3)
        if kind == "completed":
            self.state["request_outcome"] = "completed"
            self.record("receive_completed")
            self.state["phase_markers"]["receive_completed"] = round(
                time.monotonic() - self.started_at, 3)
            self._save_wav()
        elif kind in ("failed", "stopped"):
            if self.state["status"] != "playback_failed":
                self.state["status"] = kind
            self.state["request_outcome"] = event.get("request_outcome", "unknown")
            self.done.set()
        if self.browser is not None:
            await self.browser.send_json(event)
        self._finish_if_played()

    def _save_wav(self) -> None:
        if not self.audio:
            return
        path = self.result_dir / "audio.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(bytes(self.audio))
        self.state["audio_file"] = path.name
        self.save()

    def accept_metric(self, message: dict) -> None:
        name, client_ms = message.get("name"), message.get("client_ms")
        if (name not in METRICS or not isinstance(client_ms, (int, float)) or
                not math.isfinite(client_ms) or client_ms < 0 or
                self.relay is None or self.relay.current is None or
                message.get("request_id") != self.relay.current.request_id):
            return
        metrics = self.state["browser_metrics"]
        if name not in metrics:
            metrics[name] = round(client_ms, 3)
            if name == "playback_ended":
                self.state["phase_markers"]["playback_queue_drained"] = round(
                    time.monotonic() - self.started_at, 3)
            self.save()
        self._finish_if_played()

    def accept_playback_error(self, message: dict) -> None:
        run = self.relay.current if self.relay else None
        if (run is None or len(self.state["browser_diagnostics"]) >= 4 or
                message.get("code") not in PLAYBACK_ERRORS or
                message.get("request_id") != run.request_id or
                message.get("session_id") != self.relay.session_id or
                message.get("generation_id") != run.generation_id or
                message.get("segment_id") != run.segment_id or
                message.get("connection_id") != run.connection_id or
                message.get("connection_epoch") != run.epoch):
            return
        bounds = {"pending_audio_bytes": 8 * 1024 * 1024,
                  "incoming_audio_bytes": 8 * 1024 * 1024,
                  "scheduled_ahead_seconds": 60,
                  "scheduled_sources": 1000}
        values = {name: message.get(name) for name in bounds}
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or
               not math.isfinite(value) or value < 0 or value > bounds[name]
               for name, value in values.items()):
            return
        safe = {"code": message["code"], "request_id": run.request_id,
                "session_id": self.relay.session_id,
                "generation_id": run.generation_id,
                "segment_id": run.segment_id,
                "connection_id": run.connection_id,
                "connection_epoch": run.epoch, **values}
        self.state["browser_diagnostics"].append(safe)
        self.record("browser_playback_error", **safe)
        self.state["status"] = "playback_failed"
        self.done.set()
        self.save()

    def _finish_if_played(self) -> None:
        metrics = self.state["browser_metrics"]
        first, complete = metrics.get("first_playback_started"), metrics.get("receive_completed")
        if first is not None and complete is not None:
            self.state["streaming_observation"] = (
                "playback_started_before_receive_complete" if first < complete
                else "playback_started_after_receive_complete")
        if self.state["request_outcome"] == "completed" and "playback_ended" in metrics:
            if not all(name in metrics for name in (
                    "first_audio_received", "first_playback_started",
                    "receive_completed", "playback_ended")):
                self.state["status"] = "playback_timing_unverified"
            elif first < complete:
                self.state["status"] = "streaming_observed"
            else:
                self.state["status"] = "playback_started_after_receive_complete"
            self.done.set()
        self.save()


def make_app(runtime: ProbeRuntime) -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws/output")
    async def output(browser: WebSocket) -> None:
        if browser.headers.get("origin") != ORIGIN or runtime.browser is not None:
            await browser.close(code=1008)
            return
        await browser.accept()
        runtime.browser = browser
        try:
            while not runtime.done.is_set():
                raw = await browser.receive_text()
                if len(raw) > 1024:
                    await browser.close(code=1009)
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    break
                if not isinstance(message, dict):
                    break
                if message.get("type") == "start" and not runtime.started:
                    if (message.get("sample_id") != "frozen" or
                            message.get("browser_ticket") != runtime.browser_nonce or
                            not isinstance(message.get("request_id"), str) or
                            not re.fullmatch(r"[0-9a-f-]{32,40}", message["request_id"])):
                        break
                    runtime.started = True
                    runtime.state["status"] = "running"
                    runtime.save()
                    runtime.relay = OutputRelay(
                        voice_id=runtime.voice_id, connector=runtime.open_provider,
                        emit=runtime.emit,
                        limits=OutputLimits(total_seconds=runtime.connection_seconds,
                                            connect_budget_seconds=5, reconnect_attempts=0,
                                            max_audio_bytes=runtime.manifest["limits"]["max_audio_bytes"]))
                    await runtime.relay.start(request_id=message["request_id"],
                                              text=runtime.manifest["text"])
                elif message.get("type") == "metric":
                    runtime.accept_metric(message)
                elif message.get("type") == "playback_error":
                    runtime.accept_playback_error(message)
                elif message.get("type") == "stop" and runtime.relay:
                    if runtime.relay.current and message.get("request_id") == runtime.relay.current.request_id:
                        await runtime.relay.stop()
                else:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if runtime.relay and runtime.relay.current and not runtime.relay.current.terminal:
                await runtime.relay.stop()
            runtime.browser = None
            if not runtime.done.is_set():
                runtime.state["status"] = ("browser_disconnected" if runtime.started
                                           else "browser_entry_rejected")
                runtime.done.set()
                runtime.save()

    return app


async def run_server(runtime: ProbeRuntime, *, wall_seconds: float) -> dict:
    server = uvicorn.Server(uvicorn.Config(make_app(runtime), host="127.0.0.1", port=PORT,
                                          log_level="critical", access_log=False,
                                          ws_max_size=2 * 1024 * 1024))
    server_task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(wall_seconds):
            while not server.started and not server_task.done():
                await asyncio.sleep(.02)
            if server_task.done() or not server.started:
                raise GateError("local_listener_failed")
            runtime.record("local_listener_ready", port=PORT)
            (runtime.result_dir / "entry.url").write_text(
                f"{ORIGIN}/voice-v4-real.html#ticket={runtime.browser_nonce}", encoding="utf-8")
            await runtime.done.wait()
    except TimeoutError:
        if runtime.relay and runtime.relay.current and not runtime.relay.current.terminal:
            await runtime.relay.stop()
        if runtime.state["status"] not in ("streaming_observed", "failed", "stopped"):
            runtime.state["status"] = "process_timeout"
            runtime.save()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=1)
        except (TimeoutError, asyncio.CancelledError):
            server_task.cancel()
    return runtime.state


def result_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "YOSAKI" / "voice-v4-stream-runs"


def worker(approval_file: Path) -> int:
    try:
        manifest = load_manifest()
        code_hash = code_set_sha256()
        voice_id = read_profile(DATABASE, manifest)
        proxy = network_proxy()
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not key:
            raise GateError("api_key_not_configured")
        root = result_root()
        results = consume_receipt(approval_file, root, manifest, code_hash, proxy)
        runtime = ProbeRuntime(manifest, results, voice_id,
                               RealTTDConnector(key, proxy))
        asyncio.run(run_server(runtime, wall_seconds=49))
        print(json.dumps({"status": runtime.state["status"],
                          "result": str(results / "result.json")}, ensure_ascii=False))
        return 0 if runtime.state["status"] == "streaming_observed" else 2
    except (GateError, ProbeError, OSError, ValueError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, (GateError, ProbeError)) else "local_preflight_failed"
        print(json.dumps({"status": "stopped", "error_code": code}), file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-use V4 TTD stream; dry-run by default")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--approval-file", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if not args.execute_real and not args.worker:
            print(json.dumps({"mode": "dry-run", "run_id": RUN_ID,
                              "manifest_sha256": digest(manifest),
                              "code_set_sha256": code_set_sha256(),
                              "real_requests": 0, "limits": manifest["limits"]}, ensure_ascii=False))
            return 0
        if args.worker:
            if not args.approval_file:
                raise GateError("approval_required")
            return worker(args.approval_file)
        if not args.approval_file:
            raise GateError("approval_required")
        command = [sys.executable, "-m", "scripts.voice_v4_stream_probe", "--worker",
                   "--approval-file", str(args.approval_file)]
        try:
            finished = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                      timeout=50, check=False)
        except subprocess.TimeoutExpired:
            result_dir = result_root() / RUN_ID
            if result_dir.is_dir():
                (result_dir / "hard_timeout.json").write_text(
                    json.dumps({"run_id": RUN_ID, "status": "unknown",
                                "error_code": "process_hard_timeout"}), encoding="utf-8")
            print(json.dumps({"status": "unknown", "error_code": "process_hard_timeout",
                              "run_id": RUN_ID}), file=sys.stderr)
            return 2
        # The child prints only a bounded status/result path or a local error code.
        print((finished.stdout or finished.stderr).strip()[:1000])
        return finished.returncode
    except (GateError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, GateError) else "local_preflight_failed"
        print(json.dumps({"status": "stopped", "error_code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
