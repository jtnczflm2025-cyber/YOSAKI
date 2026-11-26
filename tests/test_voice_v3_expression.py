"""V3 isolated expression exercise: fake credentials, temporary DB and MockTransport."""

from contextlib import contextmanager
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import httpx

from backend.app.voice import audio_cache, manager, storage
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.expression_eval import ExpressionEvaluator, IsolatedExpressionStore
from backend.app.voice.expression_planner import EXPRESSION_PLANNER_VERSION, build
from backend.app.voice.schemas import ProfileInput


FRAME = b"\xff\xfb\x90\x64" + bytes(413)
MP3 = FRAME * 3
RESTRAINED = "大丈夫。今できることを一つずつ確認しよう。"
RELAXED = "まあ、少し休んでから始めよう。"


class VoiceV3Expression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-v3-isolated-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.data_dir = base / "data"
        self.data_dir.mkdir()
        self.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": str(self.data_dir),
            "YOSAKI_VOICE_ENABLED": "1",
            "ELEVENLABS_API_KEY": "fake-v3-key",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db_path = self.data_dir / "yosaki.sqlite3"

        @contextmanager
        def connect():
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

        self.connect = connect
        with connect() as db:
            db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER NOT NULL,
                    archive_only INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,content TEXT NOT NULL,generation_status TEXT,
                    tool_calls_json TEXT,tool_batch_status TEXT);
                INSERT INTO users VALUES(1);
                INSERT INTO characters VALUES(1,1);
                INSERT INTO conversations VALUES(1,1,0);""")
            db.execute("INSERT INTO messages VALUES(21,1,'assistant',?,'completed',NULL,NULL)",
                       (RESTRAINED,))
            db.execute("INSERT INTO messages VALUES(22,1,'assistant',?,'completed',NULL,NULL)",
                       (RELAXED,))
            db.execute("INSERT INTO messages VALUES(23,1,'assistant',?,'completed',NULL,NULL)",
                       ("[whispers] " + RESTRAINED,))
            db.execute("INSERT INTO messages VALUES(24,1,'assistant',?,'completed',NULL,NULL)",
                       ("```python\nsecret = 1\n```\n\n" + RESTRAINED,))
            storage.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=0,
                voice_id="synthetic_ivc_voice", active=True))

        # Deliberately create a V2 binding first; V3 must never replace it.
        context = manager.speech_context(connect, 21, 1)
        state, cache_id, attempt = audio_cache.claim(connect, 1, context["voice_id"],
            context["revision"], context["language"], context["segments"][0], set())
        self.assertEqual(state, "new")
        audio_cache.publish(connect, cache_id, attempt, MP3)
        with connect() as db:
            db.execute("""INSERT INTO voice_message_audio
                (message_id,cache_id,sequence,source_sha256,profile_revision,created_at)
                VALUES (?,?,?,?,?,?)""", (21, cache_id, 0, context["digest"],
                                           context["revision"], storage.stamp()))
        self.v2_binding = self._v2_binding()
        self.calls = []

        def handler(request):
            self.calls.append((request.method, request.url.path,
                               dict(request.url.params), json.loads(request.content)))
            return httpx.Response(200, content=MP3)

        self.transport = httpx.MockTransport(handler)
        self.store = IsolatedExpressionStore(base / "evaluation")
        self.evaluator = ExpressionEvaluator(connect, self.store,
            adapter_factory=lambda key: ElevenLabsAdapter(key, self.transport),
            simulated_execution_enabled=True)

    def _v2_binding(self):
        with self.connect() as db:
            return [tuple(r) for r in db.execute(
                "SELECT id,message_id,cache_id,sequence,source_sha256,profile_revision,created_at "
                "FROM voice_message_audio ORDER BY id")]

    def run_result(self, message_id, mode, sha=None, evaluator=None):
        evaluator = evaluator or self.evaluator
        sha = sha or evaluator.preview(message_id, 1, mode)["preview_sha256"]
        return asyncio.run(evaluator.execute(message_id, 1, mode, sha))

    def test_preview_is_complete_exact_and_mode_specific(self):
        normal = self.evaluator.preview(21, 1, "none")
        thoughtful = self.evaluator.preview(21, 1, "thoughtful")
        relaxed = self.evaluator.preview(22, 1, "relaxed")
        self.assertEqual((normal["safe_text"], normal["request_text"], normal["characters"]),
                         (RESTRAINED, RESTRAINED, 21))
        self.assertEqual((thoughtful["tag"], thoughtful["request_text"], thoughtful["characters"]),
                         ("[thoughtful]", "[thoughtful] " + RESTRAINED, 34))
        self.assertEqual((relaxed["tag"], relaxed["request_text"], relaxed["characters"]),
                         ("[relaxed]", "[relaxed] " + RELAXED, 25))
        self.assertEqual((len(RELAXED), normal["segments"], thoughtful["requests"]), (15, 1, 1))
        self.assertEqual(normal["effective_parameters"], thoughtful["effective_parameters"])
        self.assertEqual(self.evaluator.preview(22, 1, "none")["effective_parameters"],
                         relaxed["effective_parameters"])
        self.assertEqual(normal["effective_parameters"]["language_code"], "ja")
        self.assertEqual(normal["effective_parameters"]["settings"], "omitted")
        self.assertEqual(normal["expression_planner_version"], EXPRESSION_PLANNER_VERSION)
        self.assertNotEqual(normal["preview_sha256"], thoughtful["preview_sha256"])
        self.assertNotEqual(normal["cache_key"], thoughtful["cache_key"])
        self.assertEqual(normal["cache_status"], "miss")
        self.assertNotIn("fake-v3-key", json.dumps(normal))
        self.assertNotIn("synthetic_ivc_voice", json.dumps(normal))
        self.assertEqual(self.calls, [])

    def test_source_tags_are_filtered_before_controlled_tag_and_no_raw_markup(self):
        tagged = self.evaluator.preview(23, 1, "thoughtful")
        self.assertEqual(tagged["safe_text"], RESTRAINED)
        self.assertEqual(tagged["request_text"], "[thoughtful] " + RESTRAINED)
        self.assertIn("来源 Audio Tags", tagged["skipped_reasons"])
        code = self.evaluator.preview(24, 1, "none")
        self.assertEqual(code["request_text"], RESTRAINED)
        self.assertIn("代码或 HTML", code["skipped_reasons"])
        ordinary = build("[ふつう] " + RESTRAINED, "none")
        self.assertNotIn("[ふつう]", ordinary.request_text)
        self.assertIn("ふつう", ordinary.request_text)
        source_instruction = build("[thoughtful] " + RESTRAINED, "none")
        self.assertIsNone(source_instruction.tag)
        self.assertNotIn("[thoughtful]", source_instruction.request_text)
        with self.assertRaises(HTTPException) as invalid:
            self.evaluator.preview(21, 1, "[shouts]")
        self.assertEqual(invalid.exception.status_code, 422)
        self.assertEqual(self.calls, [])

    def test_default_gate_denies_dispatch_and_access_checks_are_read_only(self):
        denied = ExpressionEvaluator(self.connect, self.store,
            adapter_factory=lambda key: (_ for _ in ()).throw(AssertionError("network")))
        preview = denied.preview(21, 1, "none")
        with self.assertRaises(HTTPException) as gate:
            self.run_result(21, "none", preview["preview_sha256"], denied)
        self.assertEqual(gate.exception.status_code, 403)
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"}):
            with self.assertRaises(HTTPException) as closed:
                self.evaluator.preview(21, 1, "none")
            self.assertEqual(closed.exception.status_code, 503)
        with self.assertRaises(HTTPException) as wrong_owner:
            self.evaluator.preview(21, 2, "none")
        self.assertEqual(wrong_owner.exception.status_code, 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(self._v2_binding(), self.v2_binding)

    def test_consent_profile_and_key_gates_do_not_connect_provider(self):
        with self.connect() as db:
            storage.revoke_consent(db, 1)
        with self.assertRaises(HTTPException) as no_consent:
            self.evaluator.preview(21, 1, "none")
        self.assertEqual(no_consent.exception.status_code, 403)
        with self.connect() as db:
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=1,
                voice_id="synthetic_ivc_voice", active=False))
        with self.assertRaises(HTTPException) as inactive:
            self.evaluator.preview(21, 1, "none")
        self.assertEqual(inactive.exception.status_code, 403)
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
            with self.assertRaises(HTTPException) as key_missing:
                self.evaluator.preview(21, 1, "none")
            self.assertEqual(key_missing.exception.status_code, 503)
        self.assertEqual(self.calls, [])
        self.assertEqual(self._v2_binding(), self.v2_binding)

    def test_old_preview_and_changed_source_or_profile_reject_before_provider(self):
        first = self.evaluator.preview(21, 1, "none")
        with self.assertRaises(HTTPException) as wrong_mode:
            self.run_result(21, "thoughtful", first["preview_sha256"])
        self.assertEqual(wrong_mode.exception.status_code, 409)
        with self.assertRaises(HTTPException) as old_v2:
            self.run_result(21, "none", manager.speech_context(self.connect, 21, 1)["digest"])
        self.assertEqual(old_v2.exception.status_code, 409)
        with patch("backend.app.voice.expression_eval.EXPRESSION_PLANNER_VERSION",
                   "v3-expression-isolated-2"):
            revised = self.evaluator.preview(21, 1, "none")
            self.assertNotEqual(first["preview_sha256"], revised["preview_sha256"])
            self.assertNotEqual(first["cache_key"], revised["cache_key"])
            with self.assertRaises(HTTPException) as old_version:
                self.run_result(21, "none", first["preview_sha256"])
            self.assertEqual(old_version.exception.status_code, 409)
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=21", (RESTRAINED + "ね。",))
        with self.assertRaises(HTTPException) as changed:
            self.run_result(21, "none", first["preview_sha256"])
        self.assertEqual(changed.exception.status_code, 409)
        before = self.evaluator.preview(22, 1, "none")
        with self.connect() as db:
            storage.put_profile(db, 1, ProfileInput(expected_revision=1,
                voice_id="synthetic_ivc_voice", active=True))
        with self.assertRaises(HTTPException) as revision:
            self.run_result(22, "none", before["preview_sha256"])
        self.assertEqual(revision.exception.status_code, 409)
        self.assertEqual(self.calls, [])

    def test_mock_http_four_fixed_variants_separate_cache_and_keep_v2_binding(self):
        normal = self.run_result(21, "none")
        thoughtful = self.run_result(21, "thoughtful")
        normal_relaxed = self.run_result(22, "none")
        relaxed = self.run_result(22, "relaxed")
        outcomes = (normal, thoughtful, normal_relaxed, relaxed)
        self.assertEqual([x["status"] for x in outcomes], ["completed"] * 4)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual([x[3]["inputs"][0]["text"] for x in self.calls],
                         [RESTRAINED, "[thoughtful] " + RESTRAINED,
                          RELAXED,
                          "[relaxed] " + RELAXED])
        self.assertEqual(sum(len(x[3]["inputs"][0]["text"]) for x in self.calls), 95)
        self.assertTrue(all(x[2]["output_format"] == "mp3_44100_128" for x in self.calls))
        self.assertTrue(all(x[3]["model_id"] == "eleven_v4" and
                            x[3]["language_code"] == "ja" and
                            "settings" not in x[3] for x in self.calls))
        self.assertEqual(len({x["cache_key"] for x in outcomes}), 4)
        for result in outcomes:
            self.assertEqual(self.store.audio_path(result["cache_key"]).read_bytes(), MP3)
        repeat = self.run_result(21, "thoughtful")
        self.assertTrue(repeat["cache_hit"])
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self._v2_binding(), self.v2_binding)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM voice_audio_cache").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT revision,active FROM voice_profiles").fetchone()[:], (1, 1))
            self.assertEqual(db.execute("SELECT count(*) FROM voice_privacy_consents").fetchone()[0], 1)

    def test_timeout_marks_unknown_and_never_retries(self):
        calls = []

        class SlowAdapter:
            async def synthesize_http(self, text, voice_id, language_code):
                calls.append(text)
                await asyncio.sleep(0.05)
                return MP3

        slow = ExpressionEvaluator(self.connect, self.store,
            adapter_factory=lambda key: SlowAdapter(),
            simulated_execution_enabled=True, timeout_seconds=0.01)
        first = self.run_result(21, "thoughtful", evaluator=slow)
        self.assertEqual((first["status"], first["error_code"]), ("unknown", "result_unknown"))
        with self.assertRaises(HTTPException) as repeat:
            self.run_result(21, "thoughtful", evaluator=slow)
        self.assertEqual(repeat.exception.status_code, 409)
        self.assertEqual(len(calls), 1)
        key = slow.preview(21, 1, "thoughtful")["cache_key"]
        self.assertEqual(self.store.record(key)["status"], "unknown")
        self.assertFalse(self.store.audio_path(key).exists())
        self.assertEqual(self._v2_binding(), self.v2_binding)

    def test_429_is_failed_and_not_automatically_retried(self):
        self.transport = httpx.MockTransport(lambda request: (
            self.calls.append(request.url.path) or httpx.Response(429)))
        first = self.run_result(21, "relaxed")
        self.assertEqual(first["status"], "failed")
        with self.assertRaises(HTTPException) as repeat:
            self.run_result(21, "relaxed")
        self.assertEqual(repeat.exception.status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_late_result_after_source_change_cannot_publish(self):
        async def exercise():
            entered = asyncio.Event()
            release = asyncio.Event()
            calls = []

            class DelayedAdapter:
                async def synthesize_http(self, text, voice_id, language_code):
                    calls.append(text)
                    entered.set()
                    await release.wait()
                    return MP3

            evaluator = ExpressionEvaluator(self.connect, self.store,
                adapter_factory=lambda key: DelayedAdapter(),
                simulated_execution_enabled=True)
            preview = evaluator.preview(21, 1, "none")
            task = asyncio.create_task(evaluator.execute(21, 1, "none", preview["preview_sha256"]))
            await entered.wait()
            with self.connect() as db:
                db.execute("UPDATE messages SET content=? WHERE id=21", (RESTRAINED + "ね。",))
            release.set()
            outcome = await task
            return outcome, preview, calls

        outcome, preview, calls = asyncio.run(exercise())
        self.assertEqual(outcome["status"], "unknown")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.store.audio_path(preview["cache_key"]).exists())
        self.assertEqual(self._v2_binding(), self.v2_binding)

    def test_store_rejects_formal_data_dir_and_main_schema_is_unchanged(self):
        with self.assertRaises(ValueError):
            IsolatedExpressionStore(self.data_dir / "voice" / "expression")
        with self.connect() as db:
            before = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        self.run_result(21, "none")
        with self.connect() as db:
            after = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        self.assertEqual(before, after)
        self.assertNotIn("samples", after)
        self.assertEqual(self._v2_binding(), self.v2_binding)


if __name__ == "__main__":
    unittest.main()
