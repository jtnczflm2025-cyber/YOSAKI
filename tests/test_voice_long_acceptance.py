"""The final isolated two-segment entry with only local fake HTTP."""

import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import httpx

from scripts import voice_long_segmented_acceptance as acceptance
from tests.test_voice_v2 import MP3
from backend.app.voice.schemas import VoiceConfiguration


class VoiceLongAcceptance(unittest.TestCase):
    def setUp(self):
        # Exercise the real read-only profile guard against synthetic authority,
        # never against a developer's daily database or system proxy settings.
        folder=tempfile.TemporaryDirectory(prefix='yosaki-long-authority-')
        self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'authority.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE voice_profiles(character_id,voice_id,revision,configuration_json,active);CREATE TABLE voice_privacy_consents(owner_id,provider,notice_version);')
            db.execute('INSERT INTO voice_profiles VALUES(1,?,?,?,1)',('synthetic_ivc_voice',acceptance.EXPECTED_REVISION,VoiceConfiguration().model_dump_json()))
            db.execute('INSERT INTO voice_privacy_consents VALUES(1,?,?)',('elevenlabs',acceptance.EXPECTED_NOTICE))
        for target in (patch.object(acceptance,'LIVE_DB',path),
                       patch.object(acceptance,'EXPECTED_VOICE_SHA256',acceptance.sha256(b'synthetic_ivc_voice')),
                       patch.object(acceptance,'getproxies',return_value={'https':acceptance.EXPECTED_PROXY})):
            target.start();self.addCleanup(target.stop)

    def test_changed_binding_has_zero_dispatch_and_no_receipt(self):
        voice, preview = acceptance.prepare()
        try:
            with tempfile.TemporaryDirectory(prefix="yosaki-long-guard-") as folder:
                root = Path(folder)
                with self.assertRaisesRegex(ValueError, "approved_binding_changed"):
                    acceptance.execute(voice, preview, "synthetic-long-run",
                        preview["preview_sha256"],
                        acceptance.sha256(acceptance.FIXTURE.read_bytes()),
                        "0" * 64, run_root=root)
                self.assertEqual(voice.calls, [])
                self.assertFalse((root / "synthetic-long-run").exists())
        finally:
            voice.tearDown()

    def test_first_request_rejected_stops_before_second(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(429)

        voice, preview = acceptance.prepare()
        try:
            with tempfile.TemporaryDirectory(prefix="yosaki-long-stop-") as folder:
                root = Path(folder)
                report = acceptance.execute(voice, preview, "synthetic-long-stop",
                    preview["preview_sha256"],
                    acceptance.sha256(acceptance.FIXTURE.read_bytes()),
                    acceptance.code_collection_sha256(),
                    transport=httpx.MockTransport(handler), run_root=root)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["http_attempts"], 1)
                self.assertEqual(report["attempted_characters"], 1202)
                self.assertEqual(len(calls), 1)
                self.assertEqual(report["audio"], [])
                self.assertTrue((root / "synthetic-long-stop" / "receipt.json").is_file())
        finally:
            voice.tearDown()

    def test_receipt_two_requests_and_duplicate_run_rejected(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=MP3)

        voice, preview = acceptance.prepare()
        try:
            with tempfile.TemporaryDirectory(prefix="yosaki-long-route-") as folder:
                root = Path(folder)
                args = (voice, preview, "synthetic-long-run",
                        preview["preview_sha256"],
                        acceptance.sha256(acceptance.FIXTURE.read_bytes()),
                        acceptance.code_collection_sha256())
                report = acceptance.execute(*args,
                    transport=httpx.MockTransport(handler), run_root=root)
                self.assertEqual(report["status"], "completed", report)
                self.assertEqual(report["http_attempts"], 2)
                self.assertEqual(report["attempted_characters"], 2438)
                self.assertEqual(len(report["audio"]), 2)
                self.assertEqual(report["player"], "player.html")
                player = (root / "synthetic-long-run" / "player.html").read_text(encoding="utf-8")
                self.assertIn("['segment-1.mp3','segment-2.mp3']", player)
                self.assertIn("audio.onended=null", player)
                self.assertEqual(len(calls), 2)
                for request, expected in zip(calls, preview["segments"]):
                    self.assertEqual(json.loads(request.content)["inputs"][0]["text"], expected)
                for audio in report["audio"]:
                    self.assertTrue((root / "synthetic-long-run" / audio["file"]).is_file())
                with self.assertRaises(FileExistsError):
                    acceptance.execute(*args,
                        transport=httpx.MockTransport(handler), run_root=root)
                self.assertEqual(len(calls), 2)
        finally:
            voice.tearDown()

    def test_explicit_process_key_reaches_adapter_without_persisting_it(self):
        seen_keys = []

        def handler(request):
            seen_keys.append(request.headers.get("xi-api-key"))
            return httpx.Response(200, content=MP3)

        voice, preview = acceptance.prepare()
        try:
            with tempfile.TemporaryDirectory(prefix="yosaki-long-key-") as folder:
                root = Path(folder)
                report = acceptance.execute(voice, preview, "synthetic-long-key",
                    preview["preview_sha256"],
                    acceptance.sha256(acceptance.FIXTURE.read_bytes()),
                    acceptance.code_collection_sha256(),
                    transport=httpx.MockTransport(handler), run_root=root,
                    provider_key="synthetic-explicit-key")
                self.assertEqual(report["status"], "completed")
                self.assertEqual(seen_keys, ["synthetic-explicit-key"] * 2)
                self.assertEqual((root / "synthetic-long-key" / "report.json")
                                 .read_text(encoding="utf-8").find("synthetic-explicit-key"), -1)
        finally:
            voice.tearDown()


if __name__ == "__main__":
    unittest.main()
