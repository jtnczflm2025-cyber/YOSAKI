"""No external connections: bounded lifecycle probe with local fake WebSocket."""

import asyncio
from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from scripts import voice_provider_probe as base
from scripts import voice_ws_lifecycle_probe as lifecycle


FAKE_KEY = "synthetic-secret-never-log"
FAKE_VOICE = "synthetic_voice_id"


class LifecycleProbeTests(unittest.TestCase):
    def test_manifest_and_default_dry_run_are_offline(self):
        manifest = lifecycle.load_manifest()
        self.assertEqual(manifest["run_id"], "voice-ws-keepalive-20261111-r1")
        self.assertEqual(len(manifest["connections"]), 1)
        self.assertEqual(manifest["limits"]["ws_connections"], 1)
        self.assertEqual(manifest["limits"]["http_requests"], 0)
        self.assertEqual(manifest["limits"]["tts_characters"], 0)
        self.assertEqual(manifest["limits"]["stt_audio_seconds"], 0)
        self.assertEqual(manifest["limits"]["wall_seconds"], 30)
        with patch.object(lifecycle, "read_authorized_profile", side_effect=AssertionError("database read")), \
             patch.object(lifecycle.base, "network_proxy", side_effect=AssertionError("network check")), \
             patch.dict("os.environ", {"ELEVENLABS_API_KEY": FAKE_KEY}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(lifecycle.main([]), 0)
        dry = json.loads(out.getvalue())
        self.assertEqual(dry["real_requests"], 0)
        self.assertEqual(dry["status"], "no_network")
        self.assertNotIn(FAKE_KEY, out.getvalue())
        with tempfile.TemporaryDirectory() as d:
            changed = dict(manifest, model_id="other")
            path = Path(d) / "manifest.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(base.ProbeError):
                lifecycle.load_manifest(path)

    def test_approval_binds_all_fingerprints_and_scope(self):
        m = lifecycle.load_manifest()
        required = {
            "schema_version": 1, "run_id": lifecycle.RUN_ID,
            "manifest_sha256": base.digest(m),
            "lifecycle_script_sha256": lifecycle.file_hash(Path(lifecycle.__file__)),
            "base_probe_sha256": lifecycle.file_hash(Path(base.__file__)),
            "character_id": 1, "profile_revision": 3,
            "voice_id_sha256": __import__("hashlib").sha256(FAKE_VOICE.encode()).hexdigest(),
            "notice_version": lifecycle.NOTICE_VERSION, "limits": m["limits"],
            "third_party_processing_confirmed_for_this_run": True,
            "billing_uncertainty_acknowledged": True, "network_proxy_url": None,
        }
        lifecycle.verify_approval(required, m, FAKE_VOICE, None)
        for key, value in (("third_party_processing_confirmed_for_this_run", False),
                           ("profile_revision", 4), ("manifest_sha256", "other"),
                           ("run_id", "voice-ws-lifecycle-20261111-r1")):
            with self.subTest(key=key), self.assertRaises(base.ProbeError):
                lifecycle.verify_approval(dict(required, **{key: value}), m, FAKE_VOICE, None)

    def test_read_only_profile_and_consent_gate(self):
        m = lifecycle.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "synthetic.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE voice_profiles(character_id INTEGER,provider TEXT,"
                           "voice_id TEXT,revision INTEGER,active INTEGER)")
                db.execute("CREATE TABLE voice_privacy_consents(provider TEXT,notice_version TEXT)")
                db.execute("INSERT INTO voice_profiles VALUES(1,'elevenlabs',?,3,1)", (FAKE_VOICE,))
                db.execute("INSERT INTO voice_privacy_consents VALUES('elevenlabs',?)",
                           (lifecycle.NOTICE_VERSION,))
            self.assertEqual(lifecycle.read_authorized_profile(path, m), FAKE_VOICE)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE voice_profiles SET active=0")
            with self.assertRaises(base.ProbeError):
                lifecycle.read_authorized_profile(path, m)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE voice_profiles SET active=1")
                db.execute("DELETE FROM voice_privacy_consents")
            with self.assertRaises(base.ProbeError):
                lifecycle.read_authorized_profile(path, m)

    def test_execute_without_receipt_stops_before_key_or_network(self):
        with patch.object(lifecycle, "read_authorized_profile", side_effect=AssertionError("database read")), \
             patch.object(lifecycle.base, "network_proxy", side_effect=AssertionError("proxy lookup")), \
             redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.main(["--execute-real"]), 2)

    def test_single_keepalive_connection_and_failure_stop(self):
        async def scenario(mode):
            observed = {"connections": 0, "keepalive": 0, "texts": 0}

            async def handler(ws):
                observed["connections"] += 1
                await ws.recv()  # fixed voice registration
                while True:
                    try:
                        message = json.loads(await ws.recv())
                    except ConnectionClosed:
                        return
                    if "inputs" in message:
                        observed["texts"] += 1
                    elif message.get("keep_alive"):
                        observed["keepalive"] += 1
                        if observed["keepalive"] == 1:
                            if mode == "error":
                                await ws.send(json.dumps({"error": {"code": "invalid_api_key",
                                                                   "message": "Invalid API key"}}))
                            elif mode == "unknown":
                                await ws.send(json.dumps({"mystery": FAKE_KEY}))
                            elif mode == "remote_close":
                                await ws.close(code=4001, reason=FAKE_KEY)
                                return
                            elif mode == "transport_abort":
                                ws.transport.abort()
                                return

            with tempfile.TemporaryDirectory() as d:
                async with serve(handler, "127.0.0.1", 0) as server:
                    port = server.sockets[0].getsockname()[1]
                    rec = base.Recorder(Path(d) / "result", "synthetic-keepalive", lifecycle.load_manifest(),
                                        simulated=True)
                    runner = lifecycle.LifecycleRunner(
                        base.load_manifest(), rec, key=FAKE_KEY, voice_id=FAKE_VOICE,
                        simulated=True, http_transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                        http_origin="http://127.0.0.1", ws_origin=f"ws://127.0.0.1:{port}",
                        time_scale=0.02)
                    if mode in ("timeout", "wall_timeout"):
                        async def timed_out(*args, **kwargs):
                            if mode == "wall_timeout":
                                await asyncio.sleep(0.1)
                            raise TimeoutError
                        runner.ttd_keepalive_gap = timed_out
                    try:
                        await runner.run_lifecycle(wall_seconds=0.05 if mode == "wall_timeout" else 30)
                        error = None
                    except base.ProbeError as exc:
                        error = exc.code
                    with self.assertRaises(base.ProbeError) as budget:
                        await runner.keepalive_only_ws()
                    self.assertEqual(budget.exception.code, "dispatch_budget_exceeded")
                    return observed, rec.state, error

        observed, result, error = asyncio.run(scenario("ok"))
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(observed, {"connections": 1, "keepalive": 2, "texts": 0})
        self.assertEqual(result["usage"]["http_requests"], 0)
        self.assertEqual(result["usage"]["ws_connections"], 1)
        self.assertEqual(result["usage"]["tts_characters_dispatched"], 0)
        self.assertEqual(result["usage"]["stt_audio_seconds_dispatched"], 0)
        self.assertEqual(result["usage"]["output_audio_bytes"], 0)
        self.assertEqual(len([x for x in result["events"] if x["event"] == "keep_alive_attempt"]), 2)
        self.assertEqual(len([x for x in result["events"] if x["event"] == "keep_alive_sent"]), 2)
        self.assertTrue(any(x["event"] == "post_window_pong_observed" for x in result["events"]))
        self.assertTrue(any(x["event"] == "local_close_attempt" for x in result["events"]))
        self.assertTrue(any(x["event"] == "local_close_completed" for x in result["events"]))
        self.assertTrue(any(x["event"] == "connection_end" and
                            x["close_origin"] == "local_requested_close" for x in result["events"]))
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertNotIn(FAKE_VOICE, json.dumps(result))

        for mode, expected in (("error", "ttd_late_event"),
                               ("unknown", "ttd_late_event"),
                               ("remote_close", "transport_or_protocol_unknown"),
                               ("transport_abort", "transport_or_protocol_unknown"),
                               ("timeout", "ttd_keepalive_timeout"),
                               ("wall_timeout", "wall_timeout")):
            with self.subTest(mode=mode):
                observed, result, error = asyncio.run(scenario(mode))
                self.assertEqual(error, expected)
                self.assertEqual(observed["connections"], 1)
                self.assertEqual(observed["texts"], 0)
                self.assertEqual(result["usage"]["ws_connections"], 1)
                self.assertEqual(result["usage"]["output_audio_bytes"], 0)
                self.assertFalse(any(x["event"] == "local_close_completed" for x in result["events"]))
                if mode == "error":
                    self.assertTrue(any(x["event"] == "ws_diagnostic" and
                                        x["error_code"] == "invalid_api_key" and
                                        x["error_code_source"] == "error.code"
                                        for x in result["events"]))
                if mode == "unknown":
                    self.assertTrue(any(x["event"] == "ws_diagnostic" and
                                        x["event_type"] == "unrecognized"
                                        for x in result["events"]))
                if mode in ("remote_close", "transport_abort"):
                    ended = [x for x in result["events"] if x["event"] == "connection_end"][0]
                    self.assertEqual(ended["close_origin"],
                                     "remote_frame" if mode == "remote_close" else "transport_interruption")
                    self.assertEqual(ended["remote_close_code"], 4001 if mode == "remote_close" else None)
                self.assertNotIn(FAKE_KEY, json.dumps(result))
                self.assertNotIn(FAKE_VOICE, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
