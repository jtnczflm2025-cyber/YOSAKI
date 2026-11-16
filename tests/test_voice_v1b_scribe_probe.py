"""Scribe-only probe acceptance uses fake credentials and loopback WebSockets."""

import asyncio
import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from scripts import voice_provider_probe as base
from scripts import voice_v1b_scribe_probe as scribe


FAKE_KEY = "fake-key-local-only"


class ScribeGateTests(unittest.TestCase):
    def test_default_dry_run_and_missing_approval_are_offline(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(scribe.main([]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result["real_requests"], result["http_requests_limit"],
                          result["ws_connections_limit"], result["tts_characters_limit"]),
                         (0, 0, 1, 0))
        with redirect_stderr(errors):
            self.assertEqual(scribe.main(["--execute-real"]), 2)
        self.assertEqual(json.loads(errors.getvalue())["error_code"], "real_arguments_missing")

    def test_manifest_and_receipt_bind_scope_and_hashes(self):
        m = scribe.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            mutated = {**m, "limits": {**m["limits"], "ws_connections": 2}}
            path = Path(d) / "mutated.json"
            path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaisesRegex(base.ProbeError, "scribe_manifest_scope_mismatch"):
                scribe.load_manifest(path)
        approval = {
            "schema_version": 1, "run_id": m["proposed_run_id"],
            "manifest_sha256": base.digest(m),
            "scribe_script_sha256": scribe.file_hash(Path(scribe.__file__)),
            "base_probe_sha256": scribe.file_hash(Path(base.__file__)),
            "source_run_id": m["source_run_id"],
            "source_result_sha256": m["source_result_sha256"],
            "source_approval_sha256": m["source_approval_sha256"],
            "source_used_sha256": m["source_used_sha256"],
            "fixture_sha256": m["scribe_ws"]["fixture_sha256"],
            "limits": m["limits"],
            "third_party_processing_confirmed_for_this_run": True,
            "billing_uncertainty_acknowledged": True,
            "network_proxy_url": None,
        }
        scribe.verify_approval(approval, m, m["proposed_run_id"], None)
        for field, value in (("source_result_sha256", "0" * 64),
                             ("scribe_script_sha256", "0" * 64),
                             ("fixture_sha256", "0" * 64),
                             ("third_party_processing_confirmed_for_this_run", False)):
            with self.subTest(field=field), self.assertRaisesRegex(base.ProbeError, "approval_mismatch"):
                scribe.verify_approval({**approval, field: value}, m,
                                       m["proposed_run_id"], None)

    def test_source_receipt_and_no_prior_scribe_are_checked(self):
        m = scribe.load_manifest().copy()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / m["source_run_id"]
            source.mkdir()
            result = {"run_id": m["source_run_id"], "status": "unknown",
                      "error_code": "ttd_idle_unexpected_event",
                      "usage": {"http_requests": 0, "ws_connections": 1,
                                "tts_characters_dispatched": 0,
                                "stt_audio_seconds_dispatched": 0.0}}
            result_file = source / "result.json"
            result_file.write_text(json.dumps(result), encoding="utf-8")
            approval = root / (m["source_run_id"] + ".approval.json")
            approval.write_text("synthetic", encoding="utf-8")
            used = root / (m["source_run_id"] + ".approval.json.used")
            used.write_text("used", encoding="utf-8")
            m["source_result_sha256"] = scribe.file_hash(result_file)
            m["source_approval_sha256"] = scribe.file_hash(approval)
            m["source_used_sha256"] = scribe.file_hash(used)
            scribe.verify_source(source, m)
            result["usage"]["stt_audio_seconds_dispatched"] = 5.805
            result_file.write_text(json.dumps(result), encoding="utf-8")
            m["source_result_sha256"] = scribe.file_hash(result_file)
            with self.assertRaisesRegex(base.ProbeError, "source_result_mismatch"):
                scribe.verify_source(source, m)

    def test_cli_receipt_is_one_use_without_network(self):
        m = scribe.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            approval = {
                "schema_version": 1, "run_id": m["proposed_run_id"],
                "manifest_sha256": base.digest(m),
                "scribe_script_sha256": scribe.file_hash(Path(scribe.__file__)),
                "base_probe_sha256": scribe.file_hash(Path(base.__file__)),
                "source_run_id": m["source_run_id"],
                "source_result_sha256": m["source_result_sha256"],
                "source_approval_sha256": m["source_approval_sha256"],
                "source_used_sha256": m["source_used_sha256"],
                "fixture_sha256": m["scribe_ws"]["fixture_sha256"],
                "limits": m["limits"],
                "third_party_processing_confirmed_for_this_run": True,
                "billing_uncertainty_acknowledged": True,
                "network_proxy_url": None,
            }
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps(approval), encoding="utf-8")
            source = root / m["source_run_id"]
            source.mkdir()
            invoked = []

            class StubRunner:
                def __init__(self, _manifest, record, **kwargs):
                    invoked.append(kwargs["dispatch_granted"])
                    self.record = record

                async def run_scribe(self, _pcm):
                    self.record.state["status"] = "completed"
                    self.record.save()

            args = ["--execute-real", "--run-id", m["proposed_run_id"],
                    "--approval-file", str(receipt), "--source-run-dir", str(source),
                    "--results-dir", str(root / "result")]
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                    patch.object(scribe, "verify_source"), \
                    patch.object(scribe.base, "network_proxy", return_value=None), \
                    patch.object(scribe, "ScribeRunner", StubRunner), redirect_stdout(output):
                self.assertEqual(scribe.main(args), 0)
            self.assertEqual(invoked, [True])
            self.assertTrue((root / "receipt.json.used").is_file())
            self.assertNotIn(FAKE_KEY, output.getvalue())
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                    patch.object(scribe, "verify_source"), \
                    patch.object(scribe.base, "network_proxy", return_value=None), \
                    patch.object(scribe, "ScribeRunner", StubRunner), redirect_stderr(errors):
                self.assertEqual(scribe.main(args[:-1] + [str(root / "other")]), 2)
            self.assertIn("approval_already_used", errors.getvalue())
            self.assertEqual(invoked, [True])


class ScribeLoopbackTests(unittest.TestCase):
    def run_case(self, mode="ok"):
        return asyncio.run(self._run_case(mode))

    async def _run_case(self, mode):
        observed = {"http": 0, "connections": 0, "bytes": 0, "voice_id_sent": False}

        def deny_http(_request):
            observed["http"] += 1
            raise AssertionError("Scribe-only probe made an HTTP request")

        async def ws_handler(ws):
            observed["connections"] += 1
            observed["voice_id_sent"] |= "voice_id" in ws.request.path
            await ws.send(json.dumps({"message_type": "session_started"}))
            if mode == "rate_limited":
                await ws.send(json.dumps({"message_type": "rate_limited", "error": FAKE_KEY}))
            if mode == "disconnect":
                ws.transport.abort()
                return
            while True:
                try:
                    raw = await ws.recv()
                except ConnectionClosed:
                    return
                observed["voice_id_sent"] |= "voice_id" in raw
                frame = json.loads(raw)
                observed["bytes"] += len(base64.b64decode(frame["audio_base_64"]))
                if frame.get("commit"):
                    await ws.send(json.dumps({"message_type": "committed_transcript",
                                              "text": "本地模拟转写"}))
                    return

        with tempfile.TemporaryDirectory() as d:
            async with serve(ws_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                record = base.Recorder(Path(d) / "result", "scribe-test-001",
                                       scribe.load_manifest(), simulated=True)
                runner = scribe.ScribeRunner(
                    base.load_manifest(), record, key=FAKE_KEY,
                    voice_id=scribe.UNUSED_VOICE_ID, simulated=True,
                    http_transport=httpx.MockTransport(deny_http),
                    http_origin="http://127.0.0.1", ws_origin=f"ws://127.0.0.1:{port}",
                    time_scale=0.001)
                try:
                    await runner.run_scribe(base.fixture_pcm(base.load_manifest()))
                    error = None
                except base.ProbeError as exc:
                    error = exc.code
                result = json.loads((Path(d) / "result" / "result.json").read_text(encoding="utf-8"))
                return observed, result, error

    def test_only_one_scribe_ws_and_fixed_audio(self):
        observed, result, error = self.run_case()
        self.assertIsNone(error)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((observed["http"], observed["connections"], observed["bytes"]),
                         (0, 1, 185760))
        self.assertFalse(observed["voice_id_sent"])
        self.assertEqual((result["usage"]["http_requests"], result["usage"]["ws_connections"],
                          result["usage"]["tts_characters_dispatched"],
                          result["usage"]["stt_audio_seconds_dispatched"]), (0, 1, 0, 5.805))
        self.assertEqual(result["audio"], [])
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_rate_limit_and_disconnect_stop_without_retry(self):
        for mode in ("rate_limited", "disconnect"):
            with self.subTest(mode=mode):
                observed, result, error = self.run_case(mode)
                self.assertIsNotNone(error)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual((observed["http"], observed["connections"]), (0, 1))
                self.assertLess(observed["bytes"], 185760)

    def test_extra_ws_and_http_are_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            record = base.Recorder(Path(d) / "result", "scribe-test-001",
                                   scribe.load_manifest(), simulated=True)
            runner = scribe.ScribeRunner(
                base.load_manifest(), record, key=FAKE_KEY,
                voice_id=scribe.UNUSED_VOICE_ID, simulated=True,
                http_transport=httpx.MockTransport(lambda _: httpx.Response(200)),
                http_origin="http://127.0.0.1", ws_origin="ws://127.0.0.1:1")

            async def check():
                with self.assertRaisesRegex(base.ProbeError, "scribe_http_forbidden"):
                    await runner.http("GET", "/v1/models", 15, "models")
                with self.assertRaisesRegex(base.ProbeError, "scribe_ttd_forbidden"):
                    await runner.ttd_ws()
                runner._ws_budget(20)
                with self.assertRaisesRegex(base.ProbeError, "dispatch_budget_exceeded"):
                    runner._ws_budget(1)

            asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
