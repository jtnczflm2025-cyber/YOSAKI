"""Frozen V3 fixture runner: only temporary SQLite, fake key and mock HTTP."""

from contextlib import contextmanager, redirect_stdout
import asyncio
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

from backend.app.voice import storage
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.expression_fixture import (
    FixtureError, FixtureEvaluator, digest, manifest,
)
from backend.app.voice.schemas import ProfileInput
from scripts import voice_v3_fixture_probe


FRAME = b"\xff\xfb\x90\x64" + bytes(413)
MP3 = FRAME * 3
VOICE_ID = "synthetic_ivc_voice"


@contextmanager
def opened_database(path):
    db = sqlite3.connect(path)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class FrozenFixtureTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="yosaki-v3-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.db_path = self.data / "yosaki.sqlite3"
        env = patch.dict(os.environ, {"YOSAKI_DATA_DIR": str(self.data),
                                   "YOSAKI_VOICE_ENABLED": "0",
                                   "ELEVENLABS_API_KEY": "fake-key-only"})
        env.start()
        self.addCleanup(env.stop)
        with opened_database(self.db_path) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""PRAGMA foreign_keys=ON;
                CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE characters(id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY, character_id INTEGER);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id INTEGER);
                INSERT INTO users VALUES(1);
                INSERT INTO characters VALUES(1,1);""")
            storage.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=0,
                voice_id=VOICE_ID, active=True))
        self.spec = manifest()
        self.spec["voice_id_sha256"] = hashlib.sha256(VOICE_ID.encode()).hexdigest()
        self.spec["profile_revision"] = 1
        fixture_manifest = patch("backend.app.voice.expression_fixture.manifest",
                                 return_value=self.spec)
        fixture_manifest.start()
        self.addCleanup(fixture_manifest.stop)
        self.requests = []

        def handler(request):
            self.requests.append((request.url.path, dict(request.url.params),
                                  json.loads(request.content)))
            return httpx.Response(200, content=MP3)

        self.transport = httpx.MockTransport(handler)
        self.evaluator = FixtureEvaluator(self.db_path,
            adapter_factory=lambda key: ElevenLabsAdapter(key, self.transport))

    def approval(self):
        return self.evaluator.expected_approval("v3-20261111-ab-r1")

    def run_batch(self, *, approval=None, target="run"):
        return asyncio.run(self.evaluator.run_batch("v3-20261111-ab-r1",
            approval or self.approval(), self.root / target))

    def test_frozen_previews_have_no_message_id_and_exact_four_inputs(self):
        previews = self.evaluator.previews()
        self.assertEqual([(p["sample_id"], p["mode"], p["characters"])
                          for p in previews], [
            ("V3-JA-RESTRAINED", "none", 21),
            ("V3-JA-RESTRAINED", "thoughtful", 34),
            ("V3-JA-RELAXED", "none", 15),
            ("V3-JA-RELAXED", "relaxed", 25)])
        self.assertEqual([p["request_text"] for p in previews], [
            "大丈夫。今できることを一つずつ確認しよう。",
            "[thoughtful] 大丈夫。今できることを一つずつ確認しよう。",
            "まあ、少し休んでから始めよう。",
            "[relaxed] まあ、少し休んでから始めよう。"])
        self.assertTrue(all("message_id" not in p for p in previews))
        self.assertTrue(all(p["effective_parameters"] == previews[0]["effective_parameters"]
                            for p in previews))
        self.assertEqual(previews[0]["effective_parameters"]["language_code"], "ja")
        self.assertEqual(len({p["preview_sha256"] for p in previews}), 4)
        self.assertNotIn(VOICE_ID, json.dumps(previews))
        self.assertNotIn("fake-key-only", json.dumps(previews))
        self.assertEqual(self.requests, [])

    def test_dry_run_cli_is_read_only_and_never_calls_provider(self):
        output = io.StringIO()
        with patch.object(voice_v3_fixture_probe, "local_proxy", return_value=None):
            with redirect_stdout(output):
                code = voice_v3_fixture_probe.main(["--database", str(self.db_path)])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result["mode"], result["real_requests"]),
                         (0, "dry-run", 0))
        self.assertEqual((result["request_count"], result["request_characters"]), (4, 95))
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.root.glob("run*")), [])

    def test_cli_receipt_is_one_use_with_mock_transport_only(self):
        approval_path = self.root / "approval.json"
        approval_path.write_text(json.dumps(self.approval(), ensure_ascii=False),
                                 encoding="utf-8")
        def fake_adapter(key, proxy_url=None):
            self.assertEqual(key, "fake-key-only")
            self.assertIsNone(proxy_url)
            return ElevenLabsAdapter(key, self.transport)

        with patch.object(voice_v3_fixture_probe, "local_proxy", return_value=None), patch.object(
                voice_v3_fixture_probe, "ElevenLabsAdapter", fake_adapter), patch.object(
                voice_v3_fixture_probe, "ROOT", self.root):
            with redirect_stdout(io.StringIO()) as output:
                first = voice_v3_fixture_probe.main([
                    "--database", str(self.db_path), "--execute-real",
                    "--approval-file", str(approval_path),
                    "--result-dir", str(self.root / "cli-run")])
            self.assertEqual(first, 0)
            self.assertEqual(json.loads(output.getvalue())["dispatched_http"], 4)
            self.assertTrue((self.root / "approval.json.used").is_file())
            with redirect_stdout(io.StringIO()) as output:
                repeat = voice_v3_fixture_probe.main([
                    "--database", str(self.db_path), "--execute-real",
                    "--approval-file", str(approval_path),
                    "--result-dir", str(self.root / "cli-run-2")])
            self.assertEqual(repeat, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "stopped")
        self.assertEqual(len(self.requests), 4)
        self.assertFalse((self.root / "cli-run-2").exists())

    def test_real_cli_rejects_non_daily_database_before_receipt_use(self):
        approval_path = self.root / "unused-approval.json"
        approval_path.write_text(json.dumps(self.approval()), encoding="utf-8")
        with patch.object(voice_v3_fixture_probe, "local_proxy", return_value=None):
            with redirect_stdout(io.StringIO()) as output:
                status = voice_v3_fixture_probe.main([
                    "--database", str(self.db_path), "--execute-real",
                    "--approval-file", str(approval_path),
                    "--result-dir", str(self.root / "should-not-exist")])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())["error_code"],
                         "real_database_path_mismatch")
        self.assertFalse((self.root / "unused-approval.json.used").exists())
        self.assertEqual(self.requests, [])

    def test_four_mock_requests_serial_and_independent_audio(self):
        result = self.run_batch()
        self.assertEqual((result["status"], result["dispatched_http"],
                          result["dispatched_characters"]), ("completed", 4, 95))
        self.assertEqual([r[2]["inputs"][0]["text"] for r in self.requests],
                         [p["request_text"] for p in self.evaluator.previews()])
        self.assertTrue(all(r[1]["output_format"] == "mp3_44100_128"
                            and r[2]["model_id"] == "eleven_v4"
                            and r[2]["language_code"] == "ja"
                            and "settings" not in r[2] for r in self.requests))
        self.assertEqual(len(list((self.root / "run").glob("*.mp3"))), 4)
        with opened_database(self.db_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM voice_audio_cache").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM voice_message_audio").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT revision FROM voice_profiles").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM voice_privacy_consents").fetchone()[0], 1)
        with self.assertRaises(FileExistsError):
            self.run_batch()
        self.assertEqual(len(self.requests), 4)

    def test_bad_approval_and_changed_profile_or_consent_reject_before_dispatch(self):
        approval = self.approval()
        approval["input_sha256"][0] = "0" * 64
        with self.assertRaises(FixtureError) as bad:
            self.run_batch(approval=approval)
        self.assertEqual(bad.exception.code, "approval_mismatch")
        with opened_database(self.db_path) as db:
            db.execute("UPDATE voice_profiles SET revision=2")
        with self.assertRaises(FixtureError) as changed:
            self.evaluator.previews()
        self.assertEqual(changed.exception.code, "profile_changed")
        with opened_database(self.db_path) as db:
            db.execute("UPDATE voice_profiles SET revision=1")
            db.execute("DELETE FROM voice_privacy_consents")
        with self.assertRaises(FixtureError) as revoked:
            self.evaluator.previews()
        self.assertEqual(revoked.exception.code, "third_party_consent_missing")
        self.assertEqual(self.requests, [])

    def test_missing_key_and_closed_default_adapter_gate(self):
        denied = FixtureEvaluator(self.db_path)
        with self.assertRaises(FixtureError) as gate:
            asyncio.run(denied.run_batch("v3-20261111-ab-r1", self.approval(),
                                        self.root / "denied"))
        self.assertEqual(gate.exception.code, "dispatch_gate_closed")
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
            with self.assertRaises(FixtureError) as key:
                self.run_batch()
            self.assertEqual(key.exception.code, "api_key_missing")
        self.assertEqual(self.requests, [])

    def test_timeout_or_429_stops_batch_without_retry(self):
        class Slow:
            async def synthesize_http(self, text, voice_id, language):
                self_calls.append(text)
                await asyncio.sleep(0.05)
                return MP3

        self_calls = []
        self.evaluator.adapter_factory = lambda key: Slow()
        original_timeout = asyncio.timeout

        def short_timeout(seconds):
            return original_timeout(0.01)

        with patch("backend.app.voice.expression_fixture.asyncio.timeout", short_timeout):
            timed = self.run_batch(target="timeout")
        self.assertEqual((timed["status"], timed["dispatched_http"]), ("stopped", 1))
        self.assertEqual(timed["items"][0]["status"], "unknown")
        self.assertEqual(len(self_calls), 1)
        self.assertEqual(len(list((self.root / "timeout").glob("*.mp3"))), 0)

        calls = []
        def limited(request):
            calls.append(request.url.path)
            return httpx.Response(429)
        transport = httpx.MockTransport(limited)
        self.evaluator.adapter_factory = lambda key: ElevenLabsAdapter(key, transport)
        limited_result = self.run_batch(target="limited")
        self.assertEqual(limited_result["items"][0]["status"], "failed")
        self.assertEqual((limited_result["status"], limited_result["dispatched_http"]),
                         ("stopped", 1))
        self.assertEqual(len(calls), 1)

    def test_late_profile_change_drops_audio_and_stops_batch(self):
        class Changed:
            async def synthesize_http(self, text, voice_id, language):
                with opened_database(self_db) as db:
                    db.execute("UPDATE voice_profiles SET revision=2")
                return MP3

        self_db = self.db_path
        self.evaluator.adapter_factory = lambda key: Changed()
        result = self.run_batch(target="changed")
        self.assertEqual(result["items"][0]["status"], "unknown")
        self.assertEqual(result["dispatched_http"], 1)
        self.assertFalse(list((self.root / "changed").glob("*.mp3")))

    def test_manifest_edit_and_result_directory_in_data_are_rejected(self):
        changed = json.loads(json.dumps(self.spec))
        changed["samples"][0]["body"] += "ね"
        altered = self.root / "altered.json"
        altered.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        from backend.app.voice import expression_fixture
        # The imported function is the strict reader, while setUp patches the
        # module attribute used by FixtureEvaluator for synthetic credentials.
        with patch.object(expression_fixture, "MANIFEST_PATH", altered):
            with self.assertRaises(FixtureError) as mismatch:
                manifest()
        self.assertEqual(mismatch.exception.code, "manifest_scope_mismatch")
        with self.assertRaises(FixtureError) as path:
            self.run_batch(target=str(self.data / "unsafe"))
        self.assertEqual(path.exception.code, "result_dir_overlaps_data")


if __name__ == "__main__":
    unittest.main()
