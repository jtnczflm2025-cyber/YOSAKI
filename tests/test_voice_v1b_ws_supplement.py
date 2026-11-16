"""The r2 WS-only follow-up uses synthetic files and loopback sockets only."""

import asyncio
import base64
from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from scripts import voice_provider_probe as base
from scripts import voice_v1b_ws_supplement as supplement


FAKE_KEY = "fake-key-local-only"
FAKE_VOICE = "synthetic_voice_id_for_loopback_only"


class SupplementGateTests(unittest.TestCase):
    def test_dry_run_and_missing_approval_are_offline(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(supplement.main([]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["real_requests"], 0)
        self.assertEqual((result["http_requests_limit"], result["ws_connections_limit"]), (0, 2))
        self.assertEqual(result["tts_characters_limit"], 0)
        with redirect_stderr(errors):
            self.assertEqual(supplement.main(["--execute-real"]), 2)
        self.assertEqual(json.loads(errors.getvalue())["error_code"], "real_arguments_missing")

    def test_manifest_and_approval_bind_script_source_and_limits(self):
        m = supplement.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            mutated = {**m, "limits": {**m["limits"], "http_requests": 1}}
            path = Path(d) / "manifest.json"
            path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaisesRegex(base.ProbeError, "supplement_manifest_scope_mismatch"):
                supplement.load_manifest(path)
        approval = {
            "schema_version": 1, "run_id": "supplement-test-001",
            "manifest_sha256": base.digest(m),
            "supplement_script_sha256": supplement.file_hash(Path(supplement.__file__)),
            "base_probe_sha256": supplement.file_hash(Path(base.__file__)),
            "source_run_id": m["source_run_id"],
            "source_result_sha256": m["source_result_sha256"],
            "source_approval_sha256": m["source_approval_sha256"],
            "source_used_sha256": m["source_used_sha256"],
            "voice_id_sha256": hashlib.sha256(FAKE_VOICE.encode()).hexdigest(),
            "character_id": 1, "profile_revision": 1, "limits": m["limits"],
            "third_party_processing_confirmed_for_this_run": True,
            "billing_uncertainty_acknowledged": True, "network_proxy_url": None,
        }
        supplement.verify_approval(approval, m, "supplement-test-001", FAKE_VOICE, 1, None)
        for field, value in (("base_probe_sha256", "0" * 64),
                             ("source_result_sha256", "0" * 64),
                             ("third_party_processing_confirmed_for_this_run", False),
                             ("limits", {**m["limits"], "http_requests": 1})):
            with self.subTest(field=field), self.assertRaisesRegex(base.ProbeError, "approval_mismatch"):
                supplement.verify_approval({**approval, field: value}, m,
                                           "supplement-test-001", FAKE_VOICE, 1, None)

    def test_source_evidence_checks_synthetic_audio_and_receipt(self):
        m = supplement.load_manifest().copy()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / m["source_run_id"]
            source.mkdir()
            audio = []
            for item, filename in supplement.SOURCE_AUDIO.items():
                blob = (item + "_synthetic").encode()
                (source / filename).write_bytes(blob)
                audio.append({"item": item, "file": filename, "bytes": len(blob),
                              "sha256": hashlib.sha256(blob).hexdigest(), "complete": True})
            result = {
                "run_id": m["source_run_id"], "manifest_sha256": m["source_manifest_sha256"],
                "status": "unknown", "error_code": "ttd_idle_unexpected_event",
                "usage": {"http_requests": 4, "ws_connections": 2,
                          "tts_characters_dispatched": 78, "stt_audio_seconds_dispatched": 0.0},
                "audio": audio,
            }
            result_file = source / "result.json"
            result_file.write_text(json.dumps(result), encoding="utf-8")
            used = root / (m["source_run_id"] + ".approval.json.used")
            used.write_text("used", encoding="utf-8")
            old_approval = root / (m["source_run_id"] + ".approval.json")
            old_approval.write_text(json.dumps({
                "run_id": m["source_run_id"],
                "voice_id_sha256": hashlib.sha256(FAKE_VOICE.encode()).hexdigest(),
                "profile_revision": 1, "network_proxy_url": None,
            }), encoding="utf-8")
            m["source_result_sha256"] = supplement.file_hash(result_file)
            m["source_approval_sha256"] = supplement.file_hash(old_approval)
            m["source_used_sha256"] = supplement.file_hash(used)
            supplement.verify_source(source, m, FAKE_VOICE, 1, None)
            (source / audio[0]["file"]).write_bytes(b"changed")
            with self.assertRaisesRegex(base.ProbeError, "source_audio_mismatch"):
                supplement.verify_source(source, m, FAKE_VOICE, 1, None)

    def test_cli_one_use_receipt_with_synthetic_db_and_no_network(self):
        m = supplement.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db_path = root / "synthetic.sqlite3"
            with closing(sqlite3.connect(db_path)) as db:
                db.execute("CREATE TABLE voice_profiles (character_id INTEGER, provider TEXT, "
                           "voice_id TEXT, revision INTEGER, active INTEGER)")
                db.execute("INSERT INTO voice_profiles VALUES (1,'elevenlabs',?,1,0)", (FAKE_VOICE,))
                db.commit()
            approval = {
                "schema_version": 1, "run_id": "supplement-test-001",
                "manifest_sha256": base.digest(m),
                "supplement_script_sha256": supplement.file_hash(Path(supplement.__file__)),
                "base_probe_sha256": supplement.file_hash(Path(base.__file__)),
                "source_run_id": m["source_run_id"],
                "source_result_sha256": m["source_result_sha256"],
                "source_approval_sha256": m["source_approval_sha256"],
                "source_used_sha256": m["source_used_sha256"],
                "voice_id_sha256": hashlib.sha256(FAKE_VOICE.encode()).hexdigest(),
                "character_id": 1, "profile_revision": 1, "limits": m["limits"],
                "third_party_processing_confirmed_for_this_run": True,
                "billing_uncertainty_acknowledged": True, "network_proxy_url": None,
            }
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps(approval), encoding="utf-8")
            source = root / m["source_run_id"]
            source.mkdir()
            called = []

            class StubRunner:
                def __init__(self, _manifest, record, **kwargs):
                    called.append(kwargs["dispatch_granted"])
                    self.record = record

                async def run_supplement(self, _pcm):
                    self.record.state["status"] = "completed"
                    self.record.save()

            args = ["--execute-real", "--run-id", "supplement-test-001",
                    "--approval-file", str(receipt), "--database", str(db_path),
                    "--source-run-dir", str(source), "--results-dir", str(root / "result")]
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                    patch.object(supplement, "verify_source"), \
                    patch.object(supplement.base, "network_proxy", return_value=None), \
                    patch.object(supplement, "SupplementRunner", StubRunner), \
                    redirect_stdout(output):
                self.assertEqual(supplement.main(args), 0)
            self.assertEqual(called, [True])
            self.assertTrue((root / "receipt.json.used").is_file())
            self.assertNotIn(FAKE_KEY, output.getvalue())
            self.assertNotIn(FAKE_VOICE, (root / "result" / "result.json").read_text())
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                    patch.object(supplement, "verify_source"), \
                    patch.object(supplement.base, "network_proxy", return_value=None), \
                    patch.object(supplement, "SupplementRunner", StubRunner), \
                    redirect_stderr(errors):
                self.assertEqual(supplement.main(args[:-1] + [str(root / "other")]), 2)
            self.assertIn("approval_already_used", errors.getvalue())
            self.assertEqual(called, [True])


class SupplementLoopbackTests(unittest.TestCase):
    def run_case(self, mode="ok"):
        return asyncio.run(self._run_case(mode))

    async def _run_case(self, mode):
        observed = {"http": 0, "connections": [], "scribe_bytes": 0}

        def deny_http(_request):
            observed["http"] += 1
            raise AssertionError("HTTP is outside the supplement scope")

        async def ws_handler(ws):
            observed["connections"].append(ws.request.path)
            if ws.request.path.startswith("/v1/text-to-dialogue/stream-input"):
                await ws.recv()  # Voice registration, no TTS text.
                if mode == "idle_unknown":
                    await ws.send(json.dumps({"message_type": "warning", "text": FAKE_KEY}))
                    try:
                        await ws.recv()
                    except ConnectionClosed:
                        return
                else:
                    await asyncio.sleep(0.005)
                return
            await ws.send(json.dumps({"message_type": "session_started"}))
            while True:
                try:
                    frame = json.loads(await ws.recv())
                except ConnectionClosed:
                    return
                observed["scribe_bytes"] += len(base64.b64decode(frame["audio_base_64"]))
                if mode == "scribe_error":
                    await ws.send(json.dumps({"message_type": "rate_limited", "error": FAKE_KEY}))
                    try:
                        while True:
                            await ws.recv()
                    except ConnectionClosed:
                        return
                if frame.get("commit"):
                    await ws.send(json.dumps({"message_type": "committed_transcript",
                                              "text": "本地模拟转写"}))
                    return

        with tempfile.TemporaryDirectory() as d:
            async with serve(ws_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                rec = base.Recorder(Path(d) / "result", "supplement-test-001",
                                    supplement.load_manifest(), simulated=True)
                runner = supplement.SupplementRunner(
                    base.load_manifest(), rec, key=FAKE_KEY, voice_id=FAKE_VOICE,
                    simulated=True, http_transport=httpx.MockTransport(deny_http),
                    http_origin="http://127.0.0.1", ws_origin=f"ws://127.0.0.1:{port}",
                    time_scale=0.001)
                try:
                    await runner.run_supplement(base.fixture_pcm(base.load_manifest()))
                    error = None
                except base.ProbeError as exc:
                    error = exc.code
                persisted = json.loads((Path(d) / "result" / "result.json").read_text(encoding="utf-8"))
                return observed, persisted, error

    def test_only_idle_and_scribe_run(self):
        observed, result, error = self.run_case()
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(observed["http"], 0)
        self.assertEqual(len(observed["connections"]), 2)
        self.assertEqual(observed["scribe_bytes"], 185760)
        self.assertEqual((result["usage"]["http_requests"], result["usage"]["ws_connections"],
                          result["usage"]["tts_characters_dispatched"],
                          result["usage"]["stt_audio_seconds_dispatched"]), (0, 2, 0, 5.805))
        self.assertEqual(result["audio"], [])
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertNotIn(FAKE_VOICE, json.dumps(result))

    def test_idle_unknown_stops_before_scribe(self):
        observed, result, error = self.run_case("idle_unknown")
        self.assertEqual(error, "ttd_idle_unexpected_event")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual((observed["http"], len(observed["connections"]), observed["scribe_bytes"]),
                         (0, 1, 0))
        self.assertTrue(any(x["item"] == "ttd_idle" and x["event"] == "ws_diagnostic"
                            for x in result["events"]))
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_scribe_error_stops_without_retry(self):
        observed, result, error = self.run_case("scribe_error")
        self.assertEqual(error, "scribe_rate_limited")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual((observed["http"], len(observed["connections"])), (0, 2))
        self.assertLess(observed["scribe_bytes"], 185760)

    def test_http_tts_and_excess_ws_are_blocked(self):
        m = base.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            rec = base.Recorder(Path(d) / "result", "supplement-test-001",
                                supplement.load_manifest(), simulated=True)
            called = []

            def deny(request):
                called.append(request)
                return httpx.Response(200)

            runner = supplement.SupplementRunner(
                m, rec, key=FAKE_KEY, voice_id=FAKE_VOICE, simulated=True,
                http_transport=httpx.MockTransport(deny),
                http_origin="http://127.0.0.1", ws_origin="ws://127.0.0.1:1")

            async def check():
                with self.assertRaisesRegex(base.ProbeError, "supplement_http_forbidden"):
                    await runner.http("GET", "/v1/models", 15, "models")
                with self.assertRaisesRegex(base.ProbeError, "supplement_tts_forbidden"):
                    await runner.ttd_ws()
                runner._ws_budget(30)
                runner._ws_budget(20)
                with self.assertRaisesRegex(base.ProbeError, "dispatch_budget_exceeded"):
                    runner._ws_budget(1)

            asyncio.run(check())
            self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
