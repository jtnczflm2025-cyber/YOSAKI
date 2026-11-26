"""V1-B probe tests: fake key, MockTransport, loopback WebSockets only."""

import asyncio
import base64
from contextlib import closing, redirect_stdout, redirect_stderr
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

from scripts import voice_provider_probe as probe


FAKE_KEY = "fake-key-never-send-to-provider"
FAKE_VOICE = "synthetic_voice_id_for_loopback_only"
MP3_FRAME = bytes.fromhex("fffb9064") + bytes(413)


class ProbeGateTests(unittest.TestCase):
    def test_proxy_preflight_uses_one_local_route(self):
        with patch.object(probe.urllib.request, "getproxies", return_value={
                "http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}):
            self.assertEqual(probe.network_proxy(), "http://127.0.0.1:7897")
        with patch.object(probe.urllib.request, "getproxies", return_value={}):
            self.assertIsNone(probe.network_proxy())
        with patch.object(probe.urllib.request, "getproxies", return_value={
                "http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:8080"}):
            with self.assertRaisesRegex(probe.ProbeError, "proxy_routes_disagree"):
                probe.network_proxy()
        with patch.object(probe.urllib.request, "getproxies", return_value={
                "https": "http://user:secret@127.0.0.1:7897"}):
            with self.assertRaisesRegex(probe.ProbeError, "proxy_configuration_unsupported"):
                probe.network_proxy()

    def test_default_dry_run_and_unapproved_real_are_offline(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), redirect_stdout(out):
            self.assertEqual(probe.main([]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["real_requests"], 0)
        self.assertNotIn(FAKE_KEY, out.getvalue())
        with redirect_stderr(err):
            self.assertEqual(probe.main(["--execute-real"]), 2)
        self.assertEqual(json.loads(err.getvalue())["error_code"], "real_arguments_missing")

    def test_manifest_and_approval_scope_reject_mutation(self):
        m = probe.load_manifest()
        changed = json.loads(json.dumps(m))
        changed["http_ttd"][0]["text"] += "额外"
        with self.assertRaisesRegex(probe.ProbeError, "manifest_scope_mismatch"):
            probe.validate_manifest(changed)
        approval = {
            "schema_version": 1, "run_id": "test-run-001",
            "manifest_sha256": probe.digest(m),
            "voice_id_sha256": hashlib.sha256(FAKE_VOICE.encode()).hexdigest(),
            "character_id": 1, "profile_revision": 1, "limits": m["limits"],
            "third_party_processing_confirmed_for_this_run": True,
            "billing_uncertainty_acknowledged": True,
            "reviewed_linear_reference_usd": 0.005628875,
            "network_proxy_url": None,
        }
        probe.verify_approval(approval, m, "test-run-001", FAKE_VOICE, 1, None)
        for field, value in (("run_id", "other-run-001"),
                             ("third_party_processing_confirmed_for_this_run", False),
                             ("limits", {**m["limits"], "tts_characters": 79}),
                             ("voice_id_sha256", "0" * 64)):
            bad = {**approval, field: value}
            with self.assertRaises(probe.ProbeError):
                probe.verify_approval(bad, m, "test-run-001", FAKE_VOICE, 1, None)

    def test_local_budget_refusal_precedes_dispatch(self):
        m = probe.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            rec = probe.Recorder(Path(d) / "result", "test-run-001", m, simulated=True)
            called = []

            def handler(request):
                called.append(request)
                return httpx.Response(200, json=[])

            runner = probe.Runner(m, rec, key=FAKE_KEY, voice_id=FAKE_VOICE,
                                  simulated=True, http_transport=httpx.MockTransport(handler),
                                  http_origin="http://127.0.0.1", ws_origin="ws://127.0.0.1:1")
            rec.state["usage"]["http_requests"] = 4
            with self.assertRaisesRegex(probe.ProbeError, "dispatch_budget_exceeded"):
                asyncio.run(runner.http("GET", "/v1/models", 15, "models"))
            self.assertEqual(called, [])

    def test_real_runner_needs_gate_and_simulation_is_loopback_only(self):
        m = probe.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            rec = probe.Recorder(Path(d) / "result", "test-run-001", m, simulated=True)
            with self.assertRaisesRegex(probe.ProbeError, "real_target_invalid"):
                probe.Runner(m, rec, key=FAKE_KEY, voice_id=FAKE_VOICE)
            with self.assertRaisesRegex(probe.ProbeError, "simulation_transport_invalid"):
                probe.Runner(m, rec, key=FAKE_KEY, voice_id=FAKE_VOICE, simulated=True,
                             http_transport=httpx.MockTransport(lambda _: httpx.Response(200)),
                             http_origin="http://127.0.0.1.evil.example", ws_origin="ws://127.0.0.1:1")

    def test_cli_one_use_gate_with_synthetic_db_and_stubbed_runner(self):
        m = probe.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            db_path = root / "synthetic.sqlite3"
            with closing(sqlite3.connect(db_path)) as db:
                db.execute("CREATE TABLE voice_profiles (character_id INTEGER, provider TEXT, "
                           "voice_id TEXT, revision INTEGER, active INTEGER)")
                db.execute("INSERT INTO voice_profiles VALUES (1,'elevenlabs',?,1,0)", (FAKE_VOICE,))
                db.commit()
            approval = {
                "schema_version": 1, "run_id": "test-run-001",
                "manifest_sha256": probe.digest(m),
                "voice_id_sha256": hashlib.sha256(FAKE_VOICE.encode()).hexdigest(),
                "character_id": 1, "profile_revision": 1, "limits": m["limits"],
                "third_party_processing_confirmed_for_this_run": True,
                "billing_uncertainty_acknowledged": True,
                "reviewed_linear_reference_usd": 0.005628875,
                "network_proxy_url": None,
            }
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps(approval), encoding="utf-8")

            class StubRunner:
                def __init__(self, _, recorder, **kwargs):
                    self.recorder = recorder
                    self.assert_granted = kwargs["dispatch_granted"]

                async def run(self, _):
                    assert self.assert_granted
                    self.recorder.state["status"] = "completed"
                    self.recorder.save()

            args = ["--execute-real", "--run-id", "test-run-001",
                    "--approval-file", str(approval_path), "--database", str(db_path),
                    "--results-dir", str(root / "result")]
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                    patch.object(probe, "Runner", StubRunner), \
                    patch.object(probe, "network_proxy", return_value=None), redirect_stdout(out):
                self.assertEqual(probe.main(args), 0)
            self.assertTrue((root / "approval.json.used").exists())
            self.assertNotIn(FAKE_KEY, out.getvalue())
            self.assertNotIn(FAKE_VOICE, (root / "result" / "result.json").read_text())
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                    patch.object(probe, "Runner", StubRunner), \
                    patch.object(probe, "network_proxy", return_value=None), redirect_stderr(err):
                second = args[:-1] + [str(root / "other-result")]
                self.assertEqual(probe.main(second), 2)
            self.assertIn("approval_already_used", err.getvalue())
            with closing(sqlite3.connect(db_path)) as db:
                self.assertEqual(db.execute("SELECT revision, active FROM voice_profiles").fetchone(), (1, 0))


class ProbeLoopbackTests(unittest.TestCase):
    def run_case(self, mode="ok"):
        return asyncio.run(self._run_case(mode))

    async def _run_case(self, mode):
        m = probe.load_manifest()
        observed = {"http": [], "ws": [], "ttd_inputs": [], "scribe_bytes": 0,
                    "keepalive": 0, "fake_key_seen": 0}
        ttd_count = 0

        def http_handler(request):
            observed["http"].append((request.method, request.url.path))
            if request.headers.get("xi-api-key") == FAKE_KEY:
                observed["fake_key_seen"] += 1
            if mode == "http_timeout":
                raise httpx.ReadTimeout("simulated")
            if request.url.path == "/v1/models":
                if mode == "auth_401":
                    return httpx.Response(401)
                models = ("eleven_v4",) if mode == "tts_model_missing" else (
                    "eleven_v4", "eleven_v4_turbo")
                return httpx.Response(200, json=[{"model_id": x} for x in models])
            if request.url.path.startswith("/v1/voices/"):
                if mode == "rate_429":
                    return httpx.Response(429)
                return httpx.Response(200, json={"voice_id": FAKE_VOICE, "category": "cloned"})
            body = json.loads(request.content)
            observed["ttd_inputs"].append((body["model_id"], body["language_code"],
                                            body["inputs"][0]["text"],
                                            request.url.params["output_format"]))
            if request.url.params["output_format"] == "pcm_16000":
                return httpx.Response(200, content=b"\0\0" * 1600)
            return httpx.Response(200, content=MP3_FRAME)

        async def ws_handler(ws):
            nonlocal ttd_count
            observed["ws"].append(ws.request.path)
            if ws.request.headers.get("xi-api-key") == FAKE_KEY:
                observed["fake_key_seen"] += 1
            if ws.request.path.startswith("/v1/speech-to-text/realtime"):
                await ws.send(json.dumps({"message_type": "session_started"}))
                if mode == "scribe_remote_close":
                    await ws.close(code=4003, reason=FAKE_KEY)
                    return
                if mode == "scribe_transport_abort":
                    ws.transport.abort()
                    return
                if mode == "scribe_unknown_event":
                    await ws.send(json.dumps({"message_type": FAKE_VOICE,
                                              "error_code": "rate_limited", "text": FAKE_KEY}))
                while True:
                    try:
                        msg = json.loads(await ws.recv())
                    except ConnectionClosed:
                        return
                    observed["scribe_bytes"] += len(base64.b64decode(msg["audio_base_64"]))
                    if mode == "scribe_rate_limited":
                        await ws.send(json.dumps({"message_type": "rate_limited", "error": "synthetic"}))
                        await asyncio.sleep(0.05)
                        return
                    if msg.get("commit"):
                        await ws.send(json.dumps({"message_type": "partial_transcript", "text": "你好"}))
                        await ws.send(json.dumps({"message_type": "committed_transcript", "text": "你好，我是阿夜。"}))
                        return
            ttd_count += 1
            await ws.recv()  # voice registration
            if ttd_count == 2:
                if mode == "idle_transport_abort":
                    ws.transport.abort()
                    return
                if mode == "idle_remote_error_close":
                    await ws.close(code=4001, reason=FAKE_KEY)
                    return
                if mode == "idle_warning":
                    await ws.send(json.dumps({"message_type": "warning",
                                              "error": {"code": "rate_limited", "message": FAKE_KEY},
                                              "voice_id": FAKE_VOICE, "authorization": FAKE_KEY}))
                    await asyncio.sleep(0.05)
                    return
                if mode == "idle_mystery":
                    await ws.send(json.dumps({"type": FAKE_VOICE, "error_code": FAKE_KEY}))
                    await asyncio.sleep(0.05)
                    return
                if mode == "idle_invalid_json":
                    await ws.send(FAKE_KEY)
                    await asyncio.sleep(0.05)
                    return
                await asyncio.sleep(0.005)
                return  # provider inactivity close, accelerated for this local simulation
            while True:
                try:
                    msg = json.loads(await ws.recv())
                except Exception:
                    return
                if "inputs" in msg:
                    observed["ttd_inputs"].append(msg["inputs"][0]["text"])
                    if mode == "disconnect_ws":
                        return
                    if mode == "abort_ws":
                        ws.transport.abort()
                        return
                    if mode == "ttd_unknown_event":
                        await ws.send(json.dumps({"message_type": "warning", "text": FAKE_KEY}))
                        await asyncio.sleep(0.05)
                        return
                    pcm = b"\0\0" * 2400
                    await ws.send(json.dumps({"audio": base64.b64encode(pcm).decode()}))
                    await ws.send(json.dumps({"is_final_audio_for_turn": True}))
                    if mode == "late_event":
                        await ws.send(json.dumps({"audio": base64.b64encode(pcm).decode()}))
                elif msg.get("keep_alive"):
                    observed["keepalive"] += 1
                    if mode == "keepalive_error":
                        await ws.send(json.dumps({"error": {"code": "invalid_request",
                                                            "message": FAKE_KEY}}))
                        await asyncio.sleep(0.05)
                        return
                    if mode == "keepalive_remote_close":
                        await ws.close(code=4001, reason=FAKE_KEY)
                        return
                elif msg.get("close_socket"):
                    await ws.send(json.dumps({"is_final": True}))
                    return

        with tempfile.TemporaryDirectory() as d:
            async with serve(ws_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                rec = probe.Recorder(Path(d) / "result", "test-run-001", m, simulated=True)
                runner = probe.Runner(m, rec, key=FAKE_KEY, voice_id=FAKE_VOICE,
                                      simulated=True, http_transport=httpx.MockTransport(http_handler),
                                      http_origin="http://127.0.0.1", ws_origin=f"ws://127.0.0.1:{port}",
                                       time_scale=0.01 if mode.startswith("keepalive_") else 0.001)
                try:
                    await runner.run(probe.fixture_pcm(m))
                    error = None
                except probe.ProbeError as exc:
                    error = exc.code
                persisted = json.loads((Path(d) / "result" / "result.json").read_text(encoding="utf-8"))
                filenames = [x.name for x in (Path(d) / "result").iterdir()]
                return observed, persisted, error, filenames, port

    def test_normal_full_manifest_with_local_ws(self):
        seen, result, error, filenames, _ = self.run_case()
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["usage"]["http_requests"], 4)
        self.assertEqual(result["usage"]["ws_connections"], 3)
        self.assertEqual(result["usage"]["tts_characters_dispatched"], 78)
        self.assertEqual(result["usage"]["stt_audio_seconds_dispatched"], 5.805)
        self.assertEqual(seen["scribe_bytes"], 185760)
        self.assertEqual(seen["fake_key_seen"], 7)
        self.assertGreaterEqual(seen["keepalive"], 1)
        self.assertTrue(any(x["item"] == "turbo" and x["event"] == "keep_alive_sent"
                            and x["sequence"] == 1 for x in result["events"]))
        self.assertTrue(any(x["item"] == "turbo" and x["event"] == "connection_end"
                            and x["close_origin"] == "local_cleanup_after_final"
                            for x in result["events"]))
        self.assertTrue(any(x["item"] == "scribe" and x["event"] == "connection_end"
                            and x["close_origin"] == "local_cleanup_after_commit"
                            for x in result["events"]))
        self.assertEqual(len(result["audio"]), 4)
        self.assertIn("v4_ja.mp3", filenames)
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertNotIn(FAKE_VOICE, json.dumps(result))
        self.assertEqual([x[2] for x in seen["ttd_inputs"][:2]], list(probe.TEXTS[:2]))
        self.assertEqual(seen["ttd_inputs"][2:], list(probe.TEXTS[2:]))
        catalog = [x for x in result["events"] if x["item"] == "models" and
                   x["event"] == "catalog_checked"]
        self.assertEqual(catalog[0]["tts_present"], ["eleven_v4", "eleven_v4_turbo"])
        self.assertEqual(catalog[0]["scribe_status"], "not_listed_unverified")
        self.assertTrue(any(x["item"] == "voice" and x["event"] == "validated"
                            for x in result["events"]))
        self.assertTrue(any(x["item"] == "scribe" and x["event"] == "committed_transcript"
                            for x in result["events"]))
        self.assertTrue(any(x["item"] == "ttd_idle" and x["event"] == "connection_end"
                            and x["close_origin"] == "remote_frame"
                            and x["remote_close_code"] == 1000
                            for x in result["events"]))

    def test_idle_warning_is_redacted_and_stops_before_scribe(self):
        seen, result, error, _, _ = self.run_case("idle_warning")
        self.assertEqual(error, "ttd_idle_unexpected_event")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["http"]), 4)
        self.assertEqual(len(seen["ws"]), 2)
        self.assertEqual(result["usage"]["tts_characters_dispatched"], 78)
        self.assertEqual(result["usage"]["stt_audio_seconds_dispatched"], 0)
        diagnostic = [x for x in result["events"] if x["item"] == "ttd_idle"
                      and x["event"] == "ws_diagnostic"]
        self.assertEqual(len(diagnostic), 1)
        self.assertEqual(diagnostic[0]["event_type"], "warning")
        self.assertEqual(diagnostic[0]["type_source"], "message_type")
        self.assertEqual(diagnostic[0]["error_code"], "rate_limited")
        self.assertGreaterEqual(diagnostic[0]["elapsed_seconds"], 0)
        ended = [x for x in result["events"] if x["item"] == "ttd_idle"
                 and x["event"] == "connection_end"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["close_origin"], "local_cleanup_after_event")
        self.assertIsNone(ended[0]["remote_close_code"])
        self.assertIsInstance(ended[0]["post_cleanup_close_code"], int)
        self.assertIsInstance(ended[0]["post_cleanup_reason_present"], bool)
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertNotIn(FAKE_VOICE, json.dumps(result))

    def test_remote_close_frame_and_transport_abort_are_distinct(self):
        seen, result, error, _, _ = self.run_case("idle_remote_error_close")
        self.assertEqual(error, "ttd_idle_remote_error_close")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["ws"]), 2)
        ended = [x for x in result["events"] if x["item"] == "ttd_idle"
                 and x["event"] == "connection_end"][0]
        self.assertEqual(ended["close_origin"], "remote_frame")
        self.assertEqual(ended["remote_close_code"], 4001)
        self.assertTrue(ended["remote_reason_present"])
        self.assertNotIn(FAKE_KEY, json.dumps(result))

        seen, result, error, _, _ = self.run_case("idle_transport_abort")
        self.assertEqual(error, "ttd_idle_transport_interrupted")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["ws"]), 2)
        ended = [x for x in result["events"] if x["item"] == "ttd_idle"
                 and x["event"] == "connection_end"][0]
        self.assertEqual(ended["close_origin"], "transport_interruption")
        self.assertIsNone(ended["remote_close_code"])

    def test_bounded_structured_code_and_free_text_are_separate(self):
        started = 0.0
        frame = {"error": {"code": "provider_private_code", "message": FAKE_KEY},
                 "authorization": FAKE_KEY}
        diagnostic = probe.ws_event_diagnostic(frame, started)
        self.assertEqual(diagnostic["event_type"], "error")
        self.assertEqual(diagnostic["error_code"], "provider_private_code")
        self.assertEqual(diagnostic["error_code_recognition"], "bounded_identifier_unverified")
        self.assertEqual(diagnostic["error_code_source"], "error.code")
        self.assertEqual(diagnostic["error_code_type"], "string")
        self.assertEqual(diagnostic["error_payload_type"], "object")
        self.assertEqual(diagnostic["error_text_category"], "unknown")
        self.assertEqual(diagnostic["error_text_source"], "error.message")
        self.assertNotIn(FAKE_KEY, json.dumps(diagnostic))
        string_error = probe.ws_event_diagnostic({"error": FAKE_KEY}, started)
        self.assertEqual(string_error["event_type"], "error")
        self.assertEqual(string_error["error_payload_type"], "string")
        self.assertIsNone(string_error["error_code"])
        self.assertNotIn(FAKE_KEY, json.dumps(string_error))
        secret_shaped = probe.ws_event_diagnostic({"error": {"code": "sk_abcdef"}}, started)
        self.assertEqual(secret_shaped["error_code"], "unrecognized")
        self.assertNotIn("sk_abcdef", json.dumps(secret_shaped))

    def test_error_diagnostic_categories_do_not_copy_free_text(self):
        cases = (
            ({"error": "Connection closed after 20 seconds of inactivity"},
             "idle_timeout_candidate", "error", None),
            ({"error": {"code": "invalid_api_key", "message": "Invalid API key"}},
             "authentication_error", "error.message", "invalid_api_key"),
            ({"detail": {"type": "payment_required", "code": "insufficient_credits",
                         "message": "Insufficient credits"}},
             "quota_or_rate_limit", "detail.message", "insufficient_credits"),
            ({"error": {"code": "malformed_json", "message": "Malformed JSON"}},
             "protocol_error", "error.message", "malformed_json"),
            ({"error": "unexpected provider detail " + FAKE_KEY},
             "unknown", "error", None),
        )
        for frame, category, source, code in cases:
            with self.subTest(category=category):
                result = probe.ws_event_diagnostic(frame, 0.0)
                self.assertEqual(result["event_type"], "error")
                self.assertEqual(result["error_text_category"], category)
                self.assertEqual(result["error_text_source"], source)
                self.assertEqual(result["error_code"], code)
                self.assertNotIn(FAKE_KEY, json.dumps(result))
                self.assertNotIn("20 seconds of inactivity", json.dumps(result))

    def test_unrecognized_labels_and_invalid_json_are_redacted_and_stop(self):
        for mode, error, event_type in (("idle_mystery", "ttd_idle_unexpected_event", "unrecognized"),
                                        ("idle_invalid_json", "ttd_idle_invalid_event", "invalid_json")):
            with self.subTest(mode=mode):
                seen, result, actual_error, _, _ = self.run_case(mode)
                self.assertEqual(actual_error, error)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(len(seen["ws"]), 2)
                self.assertEqual(result["usage"]["stt_audio_seconds_dispatched"], 0)
                diagnostic = [x for x in result["events"] if x["item"] == "ttd_idle"
                              and x["event"] == "ws_diagnostic"]
                self.assertEqual(diagnostic[0]["event_type"], event_type)
                self.assertNotIn(FAKE_KEY, json.dumps(result))
                self.assertNotIn(FAKE_VOICE, json.dumps(result))

    def test_unknown_ttd_turn_event_stops_without_next_turn(self):
        seen, result, error, _, _ = self.run_case("ttd_unknown_event")
        self.assertEqual(error, "ttd_unexpected_event")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["ws"]), 1)
        self.assertEqual(seen["ttd_inputs"][2:], [probe.TEXTS[2]])
        self.assertTrue(any(x["item"] == "turbo_zh" and x["event"] == "ws_diagnostic"
                            and x["event_type"] == "warning" for x in result["events"]))
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_missing_tts_target_stops_before_voice_and_generation(self):
        seen, result, error, _, _ = self.run_case("tts_model_missing")
        self.assertEqual(error, "tts_models_missing")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(seen["http"]), 1)
        self.assertEqual(len(seen["ws"]), 0)
        self.assertEqual(result["usage"]["tts_characters_dispatched"], 0)

    def test_auth_failure_stops_without_retry(self):
        seen, result, error, _, _ = self.run_case("auth_401")
        self.assertEqual(error, "http_401")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(seen["http"]), 1)
        self.assertEqual(len(seen["ws"]), 0)

    def test_rate_limit_stops_without_retry(self):
        seen, result, error, _, _ = self.run_case("rate_429")
        self.assertEqual(error, "http_429")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(seen["http"]), 2)
        self.assertEqual(len(seen["ws"]), 0)

    def test_timeout_is_unknown_and_not_retried(self):
        seen, result, error, _, _ = self.run_case("http_timeout")
        self.assertEqual(error, "http_transport_unknown")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["http"]), 1)

    def test_ws_disconnect_is_unknown_and_not_reconnected(self):
        seen, result, error, _, _ = self.run_case("disconnect_ws")
        self.assertEqual(error, "transport_or_protocol_unknown")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["http"]), 4)
        self.assertEqual(len(seen["ws"]), 1)
        ended = [x for x in result["events"] if x["item"] == "turbo"
                 and x["event"] == "connection_end"][0]
        self.assertEqual(ended["close_origin"], "remote_frame")
        self.assertEqual(ended["remote_close_code"], 1000)

    def test_ttd_transport_abort_is_not_remote_close(self):
        seen, result, error, _, _ = self.run_case("abort_ws")
        self.assertEqual(error, "transport_or_protocol_unknown")
        self.assertEqual(len(seen["ws"]), 1)
        ended = [x for x in result["events"] if x["item"] == "turbo"
                 and x["event"] == "connection_end"][0]
        self.assertEqual(ended["close_origin"], "transport_interruption")
        self.assertIsNone(ended["remote_close_code"])

    def test_keepalive_error_and_remote_close_have_separate_evidence(self):
        for mode, expected_origin, expected_code in (
                ("keepalive_error", "local_cleanup_after_probe_error", None),
                ("keepalive_remote_close", "remote_frame", 4001)):
            with self.subTest(mode=mode):
                seen, result, error, _, _ = self.run_case(mode)
                self.assertEqual(len(seen["ws"]), 1)
                self.assertGreaterEqual(seen["keepalive"], 1)
                self.assertEqual(error, "transport_or_protocol_unknown" if expected_code else "ttd_late_event")
                self.assertTrue(any(x["item"] == "turbo" and x["event"] == "keep_alive_sent"
                                    for x in result["events"]))
                ended = [x for x in result["events"] if x["item"] == "turbo"
                         and x["event"] == "connection_end"][0]
                self.assertEqual(ended["close_origin"], expected_origin)
                self.assertEqual(ended["remote_close_code"], expected_code)
                self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_idle_task_cancellation_records_local_cleanup(self):
        async def scenario():
            m = probe.load_manifest()
            registered = asyncio.Event()

            async def handler(ws):
                await ws.recv()
                registered.set()
                try:
                    await ws.recv()
                except ConnectionClosed:
                    pass

            with tempfile.TemporaryDirectory() as d:
                async with serve(handler, "127.0.0.1", 0) as server:
                    port = server.sockets[0].getsockname()[1]
                    rec = probe.Recorder(Path(d) / "result", "test-cancel-001", m, simulated=True)
                    runner = probe.Runner(m, rec, key=FAKE_KEY, voice_id=FAKE_VOICE,
                                          simulated=True, http_transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                                          http_origin="http://127.0.0.1", ws_origin=f"ws://127.0.0.1:{port}",
                                          time_scale=0.001)
                    task = asyncio.create_task(runner.idle_ws())
                    await asyncio.wait_for(registered.wait(), 1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    return rec.state

        result = asyncio.run(scenario())
        ended = [x for x in result["events"] if x["item"] == "ttd_idle"
                 and x["event"] == "connection_end"][0]
        self.assertEqual(ended["close_origin"], "local_cancel_cleanup")
        self.assertIsNone(ended["remote_close_code"])
        self.assertEqual(result["usage"]["http_requests"], 0)
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_late_audio_is_rejected_before_next_segment(self):
        seen, result, error, filenames, _ = self.run_case("late_event")
        self.assertEqual(error, "ttd_late_event")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["ws"]), 1)
        self.assertEqual(seen["ttd_inputs"][2:], [probe.TEXTS[2]])
        self.assertIn("turbo_zh.pcm.part", filenames)

    def test_scribe_rate_limit_stops_before_full_audio(self):
        seen, result, error, _, _ = self.run_case("scribe_rate_limited")
        self.assertEqual(error, "scribe_rate_limited")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["ws"]), 3)
        self.assertLess(seen["scribe_bytes"], 185760)

    def test_unknown_scribe_event_is_redacted_and_stops(self):
        seen, result, error, _, _ = self.run_case("scribe_unknown_event")
        self.assertEqual(error, "scribe_unexpected_event")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(seen["ws"]), 3)
        self.assertLess(seen["scribe_bytes"], 185760)
        diagnostic = [x for x in result["events"] if x["item"] == "scribe"
                      and x["event"] == "ws_diagnostic"]
        self.assertEqual(len(diagnostic), 1)
        self.assertEqual(diagnostic[0]["event_type"], "unrecognized")
        self.assertEqual(diagnostic[0]["error_code"], "rate_limited")
        self.assertTrue(any(x["item"] == "scribe" and x["event"] == "connection_end"
                            for x in result["events"]))
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertNotIn(FAKE_VOICE, json.dumps(result))

    def test_scribe_remote_close_and_transport_abort_are_distinct(self):
        for mode, origin, code in (("scribe_remote_close", "remote_frame", 4003),
                                   ("scribe_transport_abort", "transport_interruption", None)):
            with self.subTest(mode=mode):
                seen, result, error, _, _ = self.run_case(mode)
                self.assertEqual(error, "transport_or_protocol_unknown")
                self.assertEqual(len(seen["ws"]), 3)
                ended = [x for x in result["events"] if x["item"] == "scribe"
                         and x["event"] == "connection_end"][0]
                self.assertEqual(ended["close_origin"], origin)
                self.assertEqual(ended["remote_close_code"], code)
                self.assertNotIn(FAKE_KEY, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
