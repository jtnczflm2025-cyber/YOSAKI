"""Single bounded TTD keepalive diagnostic. Dry-run by default; never imports main.py."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlencode

from websockets.exceptions import InvalidStatus

if __package__:
    from . import voice_provider_probe as base
else:
    import voice_provider_probe as base


MANIFEST = Path(__file__).with_name("voice_ws_lifecycle_manifest.json")
RUN_ID = "voice-ws-keepalive-20261111-r1"
NOTICE_VERSION = "2026-11-08-v1"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path = MANIFEST) -> dict:
    m = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1, "manifest_id": "yosaki-ttd-ws-keepalive-2026-11-11",
        "run_id": RUN_ID, "character_id": 1, "provider": "elevenlabs",
        "profile_revision": 3, "notice_version": NOTICE_VERSION,
        "model_id": "eleven_v4_turbo", "output_format": "pcm_24000",
        "connections": [
            {"id": "keepalive_only", "timeout_seconds": 25, "idle_seconds": 21,
             "keepalive_interval_seconds": 10, "tts_characters": 0},
        ],
        "limits": {"http_requests": 0, "ws_connections": 1, "tts_characters": 0,
                   "stt_audio_seconds": 0, "ws_deadline_seconds": 25, "wall_seconds": 30},
    }
    if m != expected:
        raise base.ProbeError("lifecycle_manifest_scope_mismatch")
    return m


def read_authorized_profile(database: Path, m: dict) -> str:
    if not database.is_absolute() or not database.is_file():
        raise base.ProbeError("database_path_invalid")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            profile = db.execute("SELECT provider,voice_id,revision,active FROM voice_profiles "
                                 "WHERE character_id=?", (m["character_id"],)).fetchone()
            consent = db.execute("SELECT notice_version FROM voice_privacy_consents "
                                 "WHERE provider='elevenlabs'").fetchall()
    except sqlite3.Error as exc:
        raise base.ProbeError("local_authorization_read_failed") from exc
    if (not profile or profile[0] != "elevenlabs" or profile[2:] != (3, 1) or
            not isinstance(profile[1], str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", profile[1]) or
            len(consent) != 1 or consent[0][0] != m["notice_version"]):
        raise base.ProbeError("local_authorization_changed")
    return profile[1]


def verify_approval(a: dict, m: dict, voice_id: str, proxy_url: str | None) -> None:
    required = {
        "schema_version": 1, "run_id": RUN_ID,
        "manifest_sha256": base.digest(m),
        "lifecycle_script_sha256": file_hash(Path(__file__)),
        "base_probe_sha256": file_hash(Path(base.__file__)),
        "character_id": 1, "profile_revision": 3,
        "voice_id_sha256": hashlib.sha256(voice_id.encode("utf-8")).hexdigest(),
        "notice_version": m["notice_version"], "limits": m["limits"],
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
        "network_proxy_url": proxy_url,
    }
    if not isinstance(a, dict) or a != required:
        raise base.ProbeError("approval_mismatch")


class LifecycleRunner(base.Runner):
    def _http_budget(self, seconds: int, characters: int = 0) -> None:
        raise base.ProbeError("lifecycle_http_forbidden")

    def _ws_budget(self, seconds: int) -> None:
        usage = self.r.state["usage"]
        if usage["ws_connections"] >= 1 or self.ws_deadlines + seconds > 25:
            raise base.ProbeError("dispatch_budget_exceeded")
        usage["ws_connections"] += 1
        self.ws_deadlines += seconds
        self.r.save()

    async def keepalive_only_ws(self) -> None:
        self._ws_budget(25)
        item = "ttd_keepalive"
        path = "/v1/text-to-dialogue/stream-input?" + urlencode({
            "model_id": "eleven_v4_turbo", "output_format": "pcm_24000"})
        self.r.event(item, "dispatched", transport="ws_connection", characters=0)
        started = time.monotonic()
        ws_ref = None
        close_origin = "not_classified"
        closed_exc = None
        try:
            async with asyncio.timeout(25):
                async with self.ws(path) as ws:
                    ws_ref = ws
                    await ws.send(json.dumps({"voices": [self.voice_id]}))
                    keepalives_sent = await self.ttd_keepalive_gap(ws, item=item)
                    if (keepalives_sent != 2 or
                            time.monotonic() - started <= 20 * self.time_scale):
                        raise base.ProbeError("keepalive_window_unverified", unknown=True)
                    self.r.event(item, "post_window_ping_attempt",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    pong = await ws.ping()
                    await asyncio.wait_for(pong, timeout=1)
                    self.r.event(item, "post_window_pong_observed",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    self.r.event(item, "local_close_attempt",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    await ws.close(code=1000)
                    self.r.event(item, "local_close_completed",
                                 elapsed_seconds=round(time.monotonic() - started, 3))
                    close_origin = "local_requested_close"
        except TimeoutError as exc:
            close_origin = "local_timeout_cleanup"
            raise base.ProbeError("ttd_keepalive_timeout", unknown=True) from exc
        except BaseException as exc:
            close_origin, closed_exc = base.ws_failure_origin(exc)
            raise
        finally:
            if ws_ref is not None:
                self.r.event(item, "connection_end",
                             **base.ws_close_diagnostic(ws_ref, started, origin=close_origin,
                                                        closed=closed_exc))
        self.r.event(item, "completed", elapsed_seconds=round(time.monotonic() - started, 3))

    async def run_lifecycle(self, *, wall_seconds: float = 30) -> dict:
        if not 0 < wall_seconds <= 30:
            raise base.ProbeError("wall_budget_invalid")
        try:
            async with asyncio.timeout(wall_seconds):
                await self.keepalive_only_ws()
            usage = self.r.state["usage"]
            if tuple(usage[k] for k in ("http_requests", "ws_connections",
                                        "tts_characters_dispatched",
                                        "stt_audio_seconds_dispatched",
                                        "output_audio_bytes")) != (0, 1, 0, 0.0, 0):
                raise base.ProbeError("actual_usage_mismatch", unknown=True)
            self.r.state["status"] = "completed"
        except BaseException as exc:
            if isinstance(exc, base.ProbeError):
                error = exc
            elif isinstance(exc, TimeoutError):
                error = base.ProbeError("wall_timeout", unknown=True)
            elif isinstance(exc, InvalidStatus):
                status = getattr(getattr(exc, "response", None), "status_code", None)
                error = base.ProbeError("ws_http_" + str(status) if status in (401, 403, 429)
                                        else "ws_handshake_unknown", unknown=status not in (401, 403, 429))
            else:
                error = base.ProbeError("transport_or_protocol_unknown", unknown=True)
            self.r.state["status"] = "unknown" if error.unknown else "failed"
            self.r.state["error_code"] = error.code
            self.r.save()
            raise error from None
        self.r.save()
        return self.r.state


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="Single TTD WS keepalive diagnostic; offline by default")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        m = load_manifest()
        if not args.execute_real:
            print(json.dumps({"mode": "dry-run", "run_id": RUN_ID,
                              "manifest_sha256": base.digest(m), "real_requests": 0,
                              "limits": m["limits"], "status": "no_network"}, ensure_ascii=False))
            return 0
        if (args.run_id != RUN_ID or not all((args.approval_file, args.database, args.results_dir))):
            raise base.ProbeError("real_arguments_missing_or_changed")
        voice_id = read_authorized_profile(args.database.resolve(), m)
        proxy_url = base.network_proxy()
        approval = json.loads(args.approval_file.read_text(encoding="utf-8"))
        verify_approval(approval, m, voice_id, proxy_url)
        if args.results_dir.exists():
            raise base.ProbeError("results_directory_exists")
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not key:
            raise base.ProbeError("api_key_not_configured")
        if time.monotonic() - started >= 30:
            raise base.ProbeError("wall_timeout", unknown=True)
        used = args.approval_file.with_name(args.approval_file.name + ".used")
        try:
            with used.open("x", encoding="utf-8") as fp:
                fp.write(RUN_ID + "\n")
        except FileExistsError as exc:
            raise base.ProbeError("approval_already_used") from exc
        rec = base.Recorder(args.results_dir, RUN_ID, m, simulated=False)
        remaining = 30 - (time.monotonic() - started)
        if remaining <= 0:
            rec.state["status"] = "unknown"
            rec.state["error_code"] = "wall_timeout"
            rec.save()
            raise base.ProbeError("wall_timeout", unknown=True)
        asyncio.run(LifecycleRunner(base.load_manifest(), rec, key=key, voice_id=voice_id,
                                    dispatch_granted=True, proxy_url=proxy_url).run_lifecycle(
                                        wall_seconds=remaining))
        print(json.dumps({"mode": "real", "run_id": RUN_ID, "status": "completed",
                          "results": str(args.results_dir / "result.json")}, ensure_ascii=False))
        return 0
    except (base.ProbeError, OSError, ValueError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, base.ProbeError) else "local_setup_failed"
        print(json.dumps({"mode": "real" if args.execute_real else "dry-run",
                          "status": "stopped", "error_code": code}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
