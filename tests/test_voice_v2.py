"""V2-A isolated API tests. Fake credentials, SQLite and HTTP only."""

from contextlib import contextmanager, nullcontext
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx

from backend.app.voice import audio_cache, auto_budget, manager, storage
from backend.app.voice.audio_jobs import VoiceJobs
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.readable_text import public_paragraphs
from backend.app.voice.router import make_router
from backend.app.voice.schemas import ProfileInput
from backend.app.voice.speech_planner import digest, plan, request_language


FRAME = b"\xff\xfb\x90\x64" + bytes(413)
MP3 = FRAME + FRAME + FRAME


class VoiceV2(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-v2a-")
        self.env = patch.dict(os.environ, {"YOSAKI_DATA_DIR": self.temp.name,
                                        "YOSAKI_VOICE_ENABLED": "1",
                                        "ELEVENLABS_API_KEY": "fake-v2a-key"})
        self.env.start()
        self.path = Path(self.temp.name) / "yosaki.sqlite3"
        self.calls = []
        self.response = lambda request: httpx.Response(200, content=MP3)

        @contextmanager
        def connect():
            db = sqlite3.connect(self.path)
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
                CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE);
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER NOT NULL
                    REFERENCES characters(id) ON DELETE CASCADE,archive_only INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,role TEXT NOT NULL,content TEXT NOT NULL,
                    generation_status TEXT,tool_calls_json TEXT,tool_batch_status TEXT);
                INSERT INTO users VALUES(1); INSERT INTO users VALUES(2);
                INSERT INTO auth_sessions VALUES('test-session',1);
                INSERT INTO characters VALUES(1,1); INSERT INTO characters VALUES(2,1);
                INSERT INTO conversations VALUES(1,1,0); INSERT INTO conversations VALUES(2,2,0);
                INSERT INTO messages VALUES(10,1,'assistant','你好，阿夜。','completed',NULL,NULL);
                INSERT INTO messages VALUES(11,1,'assistant','部分回复','stopped',NULL,NULL);
                INSERT INTO messages VALUES(12,1,'tool','工具结果','completed',NULL,NULL);
                INSERT INTO messages VALUES(13,2,'assistant','别的角色','completed',NULL,NULL);""")
            storage.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=0,
                voice_id="synthetic_ivc_voice", active=True))

        def handler(request):
            self.calls.append({"method": request.method, "path": request.url.path,
                               "body": request.content})
            return self.response(request)

        transport = httpx.MockTransport(handler)
        self.jobs = VoiceJobs(connect, adapter_factory=lambda key: ElevenLabsAdapter(key, transport))
        app = FastAPI()

        @app.middleware("http")
        async def owner(request, call_next):
            if request.headers.get("x-test-owner") == "none":
                return JSONResponse({"detail": "需要登录"}, status_code=401)
            request.state.user = {"id": int(request.headers.get("x-test-owner", "1")),
                                  "token_hash": "test-session"}
            return await call_next(request)

        app.include_router(make_router(connect, self.jobs))
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def wait_job(self, job_id):
        for _ in range(100):
            result = self.client.get(f"/api/voice/jobs/{job_id}").json()
            if result["status"] not in ("queued", "generating"):
                return result
            time.sleep(0.02)
        self.fail("job did not stop")

    def speak(self, message_id=10):
        preview = self.client.get(f"/api/voice/messages/{message_id}/speech-preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        response = self.client.post(f"/api/voice/messages/{message_id}/speak",
                                    json={"preview_sha256": preview.json()["preview_sha256"]})
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait_job(response.json()["job_id"])

    def test_frozen_long_fixture_previews_two_requests_without_dispatch(self):
        fixture = (Path(__file__).parent / "fixtures" /
                   "voice_long_two_request_zh.txt").read_text(encoding="utf-8")
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (fixture,))
        response = self.client.get("/api/voice/messages/10/speech-preview")
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(preview["characters"], 2438)
        self.assertEqual(preview["requests"], 2)
        self.assertEqual([len(part) for part in preview["segments"]], [1202, 1236])
        self.assertEqual(preview["language_code"], "zh")
        self.assertEqual(self.calls, [])

    def test_dispatch_guard_stops_before_http_even_after_job_is_queued(self):
        context = manager.speech_context(self.connect, 10, 1, "none")

        def reject(_characters):
            raise ValueError("voice_call_tts_dispatch_not_reserved")

        async def run():
            job_id = self.jobs.submit(context, 1, dispatch_guard=reject)["job_id"]
            await self.jobs.jobs[job_id].task
            return self.jobs.get(job_id, 1)

        result = asyncio.run(run())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.calls), 0)

    def test_missing_local_key_does_not_preclaim_auto_budget(self):
        context = manager.speech_context(self.connect, 10, 1, "none")
        with self.connect() as db:
            auto_budget.init_db(db)
            auto_budget.init_daily_db(db)
        async def run():
            job_id = self.jobs.submit(context, 1,
                                      auto_session_hash="test-session")["job_id"]
            await self.jobs.jobs[job_id].task
            return self.jobs.get(job_id, 1)
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
                                  "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
                                  "ELEVENLABS_API_KEY": ""}):
            result = asyncio.run(run())
        self.assertEqual((result["status"], result["error_code"]),
                         ("failed", "key_missing"))
        self.assertEqual(len(self.calls), 0)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_auto_owner_day_budget")
                             .fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_auto_session_budget")
                             .fetchone()[0], 0)

    def test_public_text_and_plan_are_conservative(self):
        body = ("公开 **正文** [链接文字](https://secret.example/target)。\n\n"
                "`private code` 和正文。\n\n```json\n{\"api_key\":\"secret\"}\n```\n\n"
                "<div>内部 HTML</div>\n\n![secret alt](https://secret.example/image)\n\n"
                "安全 <span>隐藏</span>。\n\n<inner>推理</inner>\n\n"
                "arguments: {\"token\": \"secret\"}\n\n结尾。")
        paragraphs = public_paragraphs(body)
        spoken = " ".join(paragraphs)
        for secret in ("secret", "private", "内部", "隐藏", "推理", "http"):
            self.assertNotIn(secret, spoken)
        self.assertIn("链接文字", spoken)
        self.assertIn("结尾", spoken)
        self.assertEqual(plan("你好。世界！"), ["你好。世界！"])
        self.assertEqual(len(plan("长" * 2001)), 2)
        long_paragraph = "这" * 1200 + "。" + "这" * 1200 + "。"
        long_segments = plan(long_paragraph)
        self.assertEqual(len(long_segments), 2)
        self.assertTrue(long_segments[0].endswith("。"))
        self.assertEqual("".join(long_segments), long_paragraph)
        unicode_parts = plan("字" * 1999 + "e\u0301" + "尾" * 20)
        self.assertTrue(unicode_parts[0].endswith("字"))
        self.assertTrue(unicode_parts[1].startswith("e\u0301"))
        with self.assertRaises(ValueError):
            plan("长" * 8001)

    def test_source_audio_tags_removed_and_bracket_text_retained(self):
        reasons = set()
        segments = plan("[whispers] 请听[版本说明]。[laughs] 继续[注释]。", reasons)
        self.assertEqual(segments, ["请听版本说明。 继续注释。"])
        self.assertIn("来源 Audio Tags", reasons)
        self.assertIn("方括号标记", reasons)
        self.assertNotIn("whispers", " ".join(segments))

    def test_preview_and_dispatched_text_share_bracket_filter(self):
        source = "[whispers] 请听[版本说明]。"
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (source,))
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        self.assertEqual(self.client.get("/api/voice/status").json()["planner_version"], "v2a-plain-2")
        self.assertEqual(preview["segments"], ["请听版本说明。"])
        self.assertEqual(self.calls, [])
        result = self.speak()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(json.loads(self.calls[0]["body"])["inputs"][0]["text"], preview["segments"][0])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT content FROM messages WHERE id=10").fetchone()[0], source)

    def test_short_paragraphs_merge_and_chinese_overrides_reference_language(self):
        body = "\n\n".join(["这" + "中" * 23 + "。"] * 3)
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (body,))
            settings = json.loads(db.execute(
                "SELECT configuration_json FROM voice_profiles WHERE character_id=1").fetchone()[0])
            settings["language"] = "ja"  # IVC source language, not request language.
            db.execute("UPDATE voice_profiles SET configuration_json=? WHERE character_id=1",
                       (json.dumps(settings),))
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        self.assertEqual(preview["segments"], [body])
        self.assertEqual((preview["requests"], preview["characters"]), (1, 79))
        self.assertEqual(preview["non_whitespace_characters"], 75)
        self.assertEqual(sum(not char.isspace() for char in body), 75)
        self.assertEqual((preview["reference_language"], preview["language_code"]), ("ja", "zh"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        sent = json.loads(self.calls[0]["body"])
        self.assertEqual(sent["inputs"][0]["text"], body)
        self.assertEqual(sent["language_code"], "zh")
        with self.connect() as db:
            row = db.execute("SELECT language_code,planner_version FROM voice_audio_cache").fetchone()
            self.assertEqual((row[0], row[1]), ("zh", "v2a-plain-2"))

    def test_japanese_and_mixed_requests_use_distinct_language_parameters(self):
        for source, expected in (("今日は静かな夜です。", "ja"),
                                 ("你好。今日は静かな夜です。", None)):
            with self.subTest(source=source):
                with self.connect() as db:
                    db.execute("UPDATE messages SET content=? WHERE id=10", (source,))
                preview = self.client.get("/api/voice/messages/10/speech-preview").json()
                self.assertEqual(preview["language_code"], expected)
                self.assertEqual(self.speak()["status"], "completed")
                sent = json.loads(self.calls[-1]["body"])
                self.assertEqual(sent.get("language_code"), expected)
                if expected is None:
                    self.assertNotIn("language_code", sent)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(request_language(plan("東京。"))[0], "auto")
        self.assertEqual(request_language(plan("今日は東京です。"))[0], "ja")
        self.assertEqual(request_language(plan("你好，hello。"))[0], "auto")

    def test_remaining_mixed_fixture_uses_one_auto_language_request_and_cache(self):
        body = "おかえり。今天辛苦了，先喝口水。少し休んでから、我们再慢慢聊。"
        self.assertEqual(len(body), 31)
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (body,))
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        self.assertEqual(preview["segments"], [body])
        self.assertEqual((preview["characters"], preview["requests"],
                          preview["language_code"]), (31, 1, None))
        self.assertEqual(self.calls, [])
        first = self.speak()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(len(first["audio"]), 1)
        sent = json.loads(self.calls[0]["body"])
        self.assertEqual(sent["inputs"], [{"text": body, "voice_id": "synthetic_ivc_voice"}])
        self.assertNotIn("language_code", sent)
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(len(self.calls), 1)

    def test_remaining_long_fixture_preserves_three_paragraphs_in_one_request(self):
        body = "\n\n".join(("这" * 490 + "。", "请" * 500 + "。", "你" * 510 + "。"))
        self.assertEqual(len(body), 1507)
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (body,))
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        self.assertEqual(preview["segments"], [body])
        self.assertEqual((preview["characters"], preview["requests"],
                          preview["language_code"]), (len(body), 1, "zh"))
        self.assertEqual(self.calls, [])
        first = self.speak()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(len(first["audio"]), 1)
        self.assertEqual(json.loads(self.calls[0]["body"])["inputs"][0]["text"], body)
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(len(self.calls), 1)

    def test_remaining_multisegment_fixture_orders_binds_and_reuses_two_requests(self):
        paragraphs = ("这" * 1199 + "。", "请" * 1199 + "。")
        body = "\n\n".join(paragraphs)
        self.assertTrue(2001 <= len(body) <= 3500)
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (body,))
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        self.assertEqual(preview["segments"], list(paragraphs))
        self.assertEqual((preview["characters"], preview["requests"],
                          preview["language_code"]), (2400, 2, "zh"))
        self.assertEqual(self.calls, [])
        first = self.speak()
        self.assertEqual(first["status"], "completed")
        self.assertEqual([item["sequence"] for item in first["audio"]], [0, 1])
        self.assertEqual([json.loads(call["body"])["inputs"][0]["text"]
                          for call in self.calls], list(paragraphs))
        self.assertTrue(all(len(paragraph) <= 2000 for paragraph in paragraphs))
        self.assertTrue(all(self.client.get(item["url"]).content == MP3
                            for item in first["audio"]))
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(len(self.calls), 2)
        with self.connect() as db:
            db.execute("UPDATE messages SET content='已更新。' WHERE id=10")
        self.assertEqual(self.client.get(first["audio"][0]["url"]).status_code, 410)
        self.assertEqual(self.client.get(first["audio"][1]["url"]).status_code, 410)

    def test_remaining_multisegment_second_request_unknown_keeps_binding_empty(self):
        body = "\n\n".join(("这" * 1199 + "。", "请" * 1199 + "。"))
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (body,))

        def second_fails(request):
            return httpx.Response(200, content=MP3) if len(self.calls) == 1 else httpx.Response(503)

        self.response = second_fails
        first = self.speak()
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(len(self.calls), 2)
        with self.connect() as db:
            rows = db.execute("SELECT status FROM voice_audio_cache ORDER BY id").fetchall()
            self.assertEqual([row[0] for row in rows], ["ready", "unknown"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 0)
        self.assertEqual(self.client.get("/api/voice/messages/10/audio").json()["audio"], [])
        self.assertEqual(self.speak()["status"], "unknown")
        self.assertEqual(len(self.calls), 2)

    def test_gates_and_preview_have_zero_provider_calls(self):
        self.assertEqual(self.client.get("/api/voice/messages/10/speech-preview",
                         headers={"x-test-owner": "none"}).status_code, 401)
        self.assertEqual(self.client.get("/api/voice/messages/10/speech-preview",
                         headers={"x-test-owner": "2"}).status_code, 403)
        for message_id in (11, 12, 13):
            status = self.client.get(f"/api/voice/messages/{message_id}/speech-preview").status_code
            self.assertIn(status, (403, 409))
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"}):
            self.assertEqual(self.client.get("/api/voice/messages/10/speech-preview").status_code, 503)
        with self.connect() as db:
            storage.revoke_consent(db, 1)
        self.assertEqual(self.client.get("/api/voice/messages/10/speech-preview").status_code, 403)
        self.assertEqual(self.calls, [])

    def test_preview_changed_source_rejected_before_dispatch(self):
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        with self.connect() as db:
            db.execute("UPDATE messages SET content='另一条完整回复。' WHERE id=10")
        response = self.client.post("/api/voice/messages/10/speak",
                                    json={"preview_sha256": preview["preview_sha256"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, [])

    def test_old_planner_preview_and_cache_cannot_dispatch_or_match(self):
        current = self.client.get("/api/voice/messages/10/speech-preview").json()
        with patch("backend.app.voice.speech_planner.PLANNER_VERSION", "v2a-plain-1"):
            old_digest = digest(10, "你好，阿夜。", "completed", 1,
                                ["你好，阿夜。"], voice_id="synthetic_ivc_voice",
                                model_id="eleven_v4", output_format="mp3_44100_128",
                                language_code="zh", reference_language="auto")
        self.assertNotEqual(current["preview_sha256"], old_digest)
        stale = self.client.post("/api/voice/messages/10/speak",
                                 json={"preview_sha256": old_digest})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.calls, [])

        active = set()
        with patch("backend.app.voice.audio_cache.PLANNER_VERSION", "v2a-plain-1"):
            state, old_id, attempt = audio_cache.claim(
                self.connect, 1, "synthetic_ivc_voice", 1, "zh", "你好，阿夜。", active)
        self.assertEqual(state, "new")
        audio_cache.publish(self.connect, old_id, attempt, MP3)
        self.assertEqual(self.client.get("/api/voice/messages/10/audio").json()["audio"], [])
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        with self.connect() as db:
            rows = db.execute("SELECT id,planner_version,status FROM voice_audio_cache ORDER BY id").fetchall()
            self.assertEqual([(row[1], row[2]) for row in rows],
                             [("v2a-plain-1", "ready"), ("v2a-plain-2", "ready")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 1)

    def test_preview_binds_profile_and_effective_request_parameters(self):
        with self.connect() as db:
            original = db.execute("SELECT configuration_json FROM voice_profiles WHERE character_id=1").fetchone()[0]
        cases = ("revision", "voice_id", "language", "model_standard", "audio_format", "planner_version")
        for case in cases:
            with self.subTest(case=case):
                with self.connect() as db:
                    db.execute("UPDATE voice_profiles SET revision=1,voice_id='synthetic_ivc_voice',configuration_json=? WHERE character_id=1", (original,))
                preview = self.client.get("/api/voice/messages/10/speech-preview").json()
                if case == "planner_version":
                    context = patch("backend.app.voice.speech_planner.PLANNER_VERSION", "v2a-plain-new")
                else:
                    context = nullcontext()
                    with self.connect() as db:
                        settings = json.loads(original)
                        if case == "revision":
                            db.execute("UPDATE voice_profiles SET revision=2 WHERE character_id=1")
                        elif case == "voice_id":
                            db.execute("UPDATE voice_profiles SET voice_id='other_synthetic_ivc' WHERE character_id=1")
                        else:
                            settings[case] = {"language": "ja", "model_standard": "other_model",
                                              "audio_format": "pcm_24000"}[case]
                            db.execute("UPDATE voice_profiles SET configuration_json=? WHERE character_id=1",
                                       (json.dumps(settings),))
                with context:
                    response = self.client.post("/api/voice/messages/10/speak",
                                                json={"preview_sha256": preview["preview_sha256"]})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(self.calls, [])
        baseline = digest(10, "你好。", "completed", 1, ["你好。"], voice_id="a",
                          model_id="eleven_v4", output_format="mp3_44100_128", language_code="zh")
        for change in ({"voice_id": "b"}, {"model_id": "other"},
                       {"output_format": "pcm_24000"}, {"language_code": "ja"}):
            args = {"voice_id": "a", "model_id": "eleven_v4",
                    "output_format": "mp3_44100_128", "language_code": "zh"}
            args.update(change)
            self.assertNotEqual(digest(10, "你好。", "completed", 1, ["你好。"], **args), baseline)

    def test_preview_reports_skips_without_exposing_hidden_text(self):
        with self.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (
                "公开正文。\n\n```json\n{\"secret\":\"private\"}\n```\n\n"
                "<div>hidden html</div>\n\n"
                "[可见链接](https://secret.example/path)。",))
        response = self.client.get("/api/voice/messages/10/speech-preview")
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertTrue(value["skipped_reasons"])
        self.assertIn("公开正文。", "\n".join(value["segments"]))
        self.assertNotIn("private", response.text)
        self.assertNotIn("hidden html", response.text)
        self.assertNotIn("secret.example", response.text)
        self.assertEqual(self.calls, [])

    def test_http_generation_cache_hit_and_binding_invalidation(self):
        first = self.speak()
        self.assertEqual(first["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["path"], "/v1/text-to-dialogue")
        audio = self.client.get(first["audio"][0]["url"])
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content, MP3)
        second = self.speak()
        self.assertEqual(second["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        cached = self.client.get("/api/voice/messages/10/audio")
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(cached.json()["audio"], second["audio"])
        self.assertEqual(len(self.calls), 1)
        with self.connect() as db:
            db.execute("UPDATE messages SET content='已修改。' WHERE id=10")
        self.assertEqual(self.client.get(first["audio"][0]["url"]).status_code, 410)
        self.assertEqual(self.client.get("/api/voice/messages/10/audio").json()["audio"], [])
        with self.connect() as db:
            db.execute("DELETE FROM messages WHERE id=10")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 0)

    def test_definite_rejection_and_unknown_do_not_retry(self):
        self.response = lambda request: httpx.Response(401)
        first = self.speak()
        self.assertEqual(first["status"], "failed")
        self.assertEqual(len(self.calls), 1)
        second = self.speak()
        self.assertEqual(second["status"], "unknown")
        self.assertEqual(len(self.calls), 1)

    def test_cache_paths_clear_and_mp3_validation(self):
        with self.assertRaises(ValueError):
            audio_cache.safe_path("../../formal.sqlite3")
        with self.assertRaises(ValueError):
            audio_cache.validate_mp3(MP3[:-10])
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(self.client.delete("/api/voice/cache?character_id=1").json()["removed"], 1)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_audio_cache").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 0)

    def test_orphan_cleanup_stays_in_isolated_cache_root(self):
        self.assertEqual(self.speak()["status"], "completed")
        folder = audio_cache.root() / "bb"
        folder.mkdir(parents=True)
        orphan = folder / ("b" * 64 + ".mp3")
        orphan.write_bytes(MP3)
        old = time.time() - 4000
        os.utime(orphan, (old, old))
        active_part = folder / ("c" * 64 + ".mp3." + "d" * 32 + ".part")
        active_part.write_bytes(MP3)
        outside = Path(self.temp.name) / "outside.mp3"
        outside.write_bytes(MP3)
        self.assertEqual(audio_cache.sweep_orphans(self.connect), 1)
        self.assertFalse(orphan.exists())
        self.assertTrue(active_part.exists())
        self.assertTrue(outside.exists())

    def test_expiry_removes_binding_without_regeneration(self):
        self.assertEqual(self.speak()["status"], "completed")
        with self.connect() as db:
            db.execute("UPDATE voice_audio_cache SET expires_at='2000-01-01' ")
        self.assertEqual(self.client.get("/api/voice/messages/10/audio").json()["audio"], [])
        self.assertEqual(audio_cache.prune(self.connect), 1)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 0)
        self.assertEqual(len(self.calls), 1)

    def test_abandoned_generation_is_unknown_without_dispatch(self):
        active_attempts = set()
        state, cache_id, attempt = audio_cache.claim(
            self.connect, 1, "synthetic_ivc_voice", 1, "zh", "你好，阿夜。", active_attempts)
        self.assertEqual(state, "new")
        self.assertIn(attempt, active_attempts)
        self.assertEqual(audio_cache.claim(self.connect, 1, "synthetic_ivc_voice", 1,
                                           "zh", "你好，阿夜。", active_attempts)[0], "busy")
        state, second_id, _ = audio_cache.claim(
            self.connect, 1, "synthetic_ivc_voice", 1, "zh", "你好，阿夜。", set())
        self.assertEqual((state, second_id), ("unknown", cache_id))
        with self.assertRaisesRegex(ValueError, "缓存任务已失效"):
            audio_cache.publish(self.connect, cache_id, attempt, MP3)
        self.assertEqual(list(audio_cache.root().rglob("*.mp3")), [])
        self.assertEqual(self.speak()["status"], "unknown")
        self.assertEqual(self.calls, [])

    def test_cancelled_adapter_late_result_cannot_publish(self):
        import asyncio
        started = threading.Event()

        class StubbornAdapter:
            async def synthesize_http(self, text, voice_id, language_code):
                started.set()
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return MP3

        self.jobs.adapter_factory = lambda key: StubbornAdapter()
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        response = self.client.post("/api/voice/messages/10/speak",
                                    json={"preview_sha256": preview["preview_sha256"]})
        self.assertTrue(started.wait(2))
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT audio_bytes FROM voice_audio_cache").fetchone()[0],
                             audio_cache.MAX_OBJECT_BYTES)
        self.assertEqual(self.client.delete(f"/api/voice/jobs/{response.json()['job_id']}").status_code, 204)
        self.assertEqual(self.wait_job(response.json()["job_id"])["status"], "cancelled")
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM voice_audio_cache").fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 0)
        self.assertEqual(list(audio_cache.root().rglob("*.mp3")), [])
        self.assertEqual(self.calls, [])

    def test_bound_cache_is_not_capacity_evicted_and_full_capacity_rejects_before_http(self):
        self.assertEqual(self.speak()["status"], "completed")
        with self.connect() as db:
            db.execute("UPDATE voice_audio_cache SET audio_bytes=?", (audio_cache.MAX_CACHE_BYTES,))
            db.execute("INSERT INTO messages VALUES(14,1,'assistant','另一句。','completed',NULL,NULL)")
        result = self.speak(14)
        self.assertEqual((result["status"], result["error_code"]), ("failed", "cache_capacity"))
        self.assertEqual(len(self.calls), 1)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_audio_cache").fetchone()[0], 1)

    def test_disk_space_rejects_before_http(self):
        from types import SimpleNamespace
        with patch("backend.app.voice.audio_cache.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            result = self.speak()
        self.assertEqual((result["status"], result["error_code"]), ("failed", "cache_capacity"))
        self.assertEqual(self.calls, [])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_audio_cache").fetchone()[0], 0)

    def test_unbound_cache_is_evicted_to_reserve_inflight_response(self):
        active = set()
        state, old_id, attempt = audio_cache.claim(
            self.connect, 1, "synthetic_ivc_voice", 1, "auto", "旧句。", active)
        self.assertEqual(state, "new")
        audio_cache.publish(self.connect, old_id, attempt, MP3)
        with self.connect() as db:
            relative = db.execute("SELECT relative_path FROM voice_audio_cache WHERE id=?", (old_id,)).fetchone()[0]
            db.execute("UPDATE voice_audio_cache SET audio_bytes=? WHERE id=?",
                       (audio_cache.MAX_CACHE_BYTES, old_id))
        old_path = audio_cache.safe_path(relative)
        self.assertTrue(old_path.exists())
        state, new_id, _ = audio_cache.claim(
            self.connect, 1, "synthetic_ivc_voice", 1, "auto", "新句。", active)
        self.assertEqual(state, "new")
        self.assertFalse(old_path.exists())
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT cache_key FROM voice_audio_cache WHERE attempt_id=?",
                                         (attempt,)).fetchone())
            self.assertEqual(db.execute("SELECT audio_bytes FROM voice_audio_cache WHERE id=?", (new_id,)).fetchone()[0],
                             audio_cache.MAX_OBJECT_BYTES)
        self.assertEqual(self.calls, [])

    def test_cancel_inflight_request_stops_job_without_retry(self):
        import asyncio
        started = threading.Event()

        class SlowAdapter:
            async def synthesize_http(self, text, voice_id, language_code):
                started.set()
                await asyncio.sleep(100)
                return MP3

        self.jobs.adapter_factory = lambda key: SlowAdapter()
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        response = self.client.post("/api/voice/messages/10/speak",
                                    json={"preview_sha256": preview["preview_sha256"]})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(started.wait(2))
        job_id = response.json()["job_id"]
        self.assertEqual(self.client.delete(f"/api/voice/jobs/{job_id}").status_code, 204)
        self.assertEqual(self.wait_job(job_id)["status"], "cancelled")
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM voice_audio_cache").fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 0)
        self.assertEqual(self.calls, [])

    def test_profile_revision_and_consent_revoke_invalidate_audio(self):
        first = self.speak()
        url = first["audio"][0]["url"]
        with self.connect() as db:
            storage.put_profile(db, 1, ProfileInput(expected_revision=1,
                voice_id="synthetic_ivc_voice", active=True))
        self.assertEqual(self.client.get(url).status_code, 410)
        with self.connect() as db:
            storage.revoke_consent(db, 1)
        self.assertEqual(self.client.get("/api/voice/messages/10/audio").status_code, 403)
        self.assertEqual(len(self.calls), 1)

    def test_same_character_different_message_reuses_cache(self):
        with self.connect() as db:
            db.execute("INSERT INTO messages VALUES(14,1,'assistant','你好，阿夜。','completed',NULL,NULL)")
        first = self.speak(10)
        second = self.speak(14)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(len(self.calls), 1)
        self.assertNotEqual(first["audio"][0]["binding_id"], second["audio"][0]["binding_id"])

    def test_timeout_is_unknown_and_never_auto_retried(self):
        self.response = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic timeout"))
        first = self.speak()
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(self.speak()["status"], "unknown")
        self.assertEqual(len(self.calls), 1)

    def test_total_request_deadline_stops_slow_provider_without_retry(self):
        import asyncio

        calls = []

        class SlowAdapter:
            async def synthesize_http(self, text, voice_id, language_code):
                calls.append((text, language_code))
                await asyncio.sleep(0.1)
                return MP3

        self.jobs.adapter_factory = lambda key: SlowAdapter()
        with patch("backend.app.voice.audio_jobs.HTTP_TTD_MAX_SECONDS", 0.01):
            first = self.speak()
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(first["error_code"], "generation_unknown")
        self.assertEqual(self.speak()["status"], "unknown")
        self.assertEqual(len(calls), 1)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM voice_audio_cache").fetchone()[0], "unknown")

    def test_server_error_is_unknown_and_never_auto_retried(self):
        self.response = lambda request: httpx.Response(503)
        self.assertEqual(self.speak()["status"], "unknown")
        self.assertEqual(self.speak()["status"], "unknown")
        self.assertEqual(len(self.calls), 1)

    def test_auto_budget_persists_and_cache_hit_is_free(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
                db.execute("INSERT INTO messages VALUES(14,1,'assistant','你好，阿夜。','completed',NULL,NULL)")
                db.execute("INSERT INTO messages VALUES(15,1,'assistant','再见，阿夜。','completed',NULL,NULL)")
                db.execute("INSERT INTO messages VALUES(16,1,'assistant','稍后见，阿夜。','completed',NULL,NULL)")
            def auto(message_id):
                preview = self.client.get(f"/api/voice/messages/{message_id}/speech-preview").json()
                response = self.client.post(f"/api/voice/messages/{message_id}/auto-speak",
                    json={"preview_sha256": preview["preview_sha256"]})
                self.assertEqual(response.status_code, 202, response.text)
                return self.wait_job(response.json()["job_id"])
            self.assertEqual(auto(10)["status"], "completed")
            self.assertEqual(auto(14)["status"], "completed")  # same cached text
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(auto(15)["status"], "completed")
            self.assertEqual(auto(16)["error_code"], "auto_budget_exhausted")
            self.assertEqual(len(self.calls), 2)
            with self.connect() as db:
                row = db.execute("SELECT * FROM voice_auto_session_budget").fetchone()
                self.assertEqual((row["synth_jobs"], row["request_count"]), (2, 2))
                self.assertIsNone(db.execute("SELECT id FROM voice_audio_cache WHERE status='failed'").fetchone())
            # A budget denial did not poison the manual path for that text.
            self.assertEqual(self.speak(16)["status"], "completed")
            # A new jobs object/process sees the same durable counters.
            self.assertEqual(auto_budget.usage(self.connect, "test-session", 1)["used"]["requests"], 2)

    def test_auto_budget_atomic_concurrency_and_no_refund(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
            outcomes = []
            def reserve():
                try:
                    auto_budget.reserve(self.connect, "test-session", 1, 10,
                                        first_request=True, limits=auto_budget.Limits(1, 1, 10))
                    outcomes.append("reserved")
                except auto_budget.BudgetExhausted:
                    outcomes.append("exhausted")
            workers = [threading.Thread(target=reserve) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            self.assertCountEqual(outcomes, ["reserved", "exhausted"])
            self.assertEqual(auto_budget.usage(self.connect, "test-session", 1)["used"]["requests"], 1)

    def test_daily_migration_carries_active_session_usage_without_changing_old_data(self):
        from scripts.migrate_voice_auto_daily_budget import data_hash, migrate, schema_hash
        with self.connect() as db:
            auto_budget.init_db(db)
            db.execute("INSERT INTO auth_sessions VALUES ('another-session',1)")
            db.execute("""INSERT INTO voice_auto_session_budget
                (session_hash,owner_id,synth_jobs,request_count,character_count,
                 created_at,updated_at) VALUES ('test-session',1,1,1,14,'now','now')""")
            db.execute("""INSERT INTO voice_auto_session_budget
                (session_hash,owner_id,synth_jobs,request_count,character_count,
                 created_at,updated_at) VALUES ('another-session',1,1,1,50,'now','now')""")
            db.commit()
            before_schema, before_data = schema_hash(db), data_hash(db)
        outcome = migrate(self.path, before_schema, True)
        self.assertTrue(outcome["old_data_unchanged"])
        self.assertEqual(outcome["carried_active_owner_count"], 1)
        with self.connect() as db:
            self.assertEqual(data_hash(db), before_data)
            row = db.execute("""SELECT synth_jobs,request_count,character_count
                FROM voice_auto_owner_day_budget WHERE owner_id=1""").fetchone()
            self.assertEqual(tuple(row), (2, 2, 64))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_auto_owner_settings")
                             .fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, "database_or_schema_drift"):
            migrate(self.path, before_schema, True)

    def test_owner_day_budget_survives_new_login_and_old_session_deletion(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
                                     "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
                auto_budget.init_daily_db(db)
            auto_budget.reserve(self.connect, "test-session", 1, 14,
                                first_request=True)
            with self.connect() as db:
                db.execute("DELETE FROM auth_sessions WHERE token_hash='test-session'")
                db.execute("INSERT INTO auth_sessions VALUES('second-login',1)")
            self.assertEqual(auto_budget.usage(self.connect, "second-login", 1)
                             ["daily"]["used"], {"jobs": 1, "requests": 1,
                                                    "characters": 14})
            self.assertEqual(auto_budget.usage(self.connect, "second-login", 1)
                             ["used"]["requests"], 0)
            auto_budget.reserve(self.connect, "second-login", 1, 20,
                                first_request=True)
            with self.connect() as db:
                db.execute("INSERT INTO auth_sessions VALUES('third-login',1)")
            with self.assertRaisesRegex(auto_budget.BudgetExhausted,
                                        "auto_daily_budget_exhausted"):
                auto_budget.reserve(self.connect, "third-login", 1, 10,
                                    first_request=True)
            self.assertEqual(auto_budget.usage(self.connect, "third-login", 1)
                             ["daily"]["remaining"]["requests"], 0)

    def test_daily_budget_resets_without_relogging_or_refunding_old_usage(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
                                     "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
                auto_budget.init_daily_db(db)
            with patch.object(auto_budget, "daily_period",
                              return_value=("2026-11-15", "2026-11-16T00:00:00+08:00")):
                for _ in range(2):
                    auto_budget.reserve(self.connect, "test-session", 1, 10,
                                        first_request=True)
            with patch.object(auto_budget, "daily_period",
                              return_value=("2026-11-16", "2026-11-17T00:00:00+08:00")):
                for _ in range(2):
                    auto_budget.reserve(self.connect, "test-session", 1, 10,
                                        first_request=True)
                with self.assertRaisesRegex(auto_budget.BudgetExhausted,
                                            "auto_daily_budget_exhausted"):
                    auto_budget.reserve(self.connect, "test-session", 1, 10,
                                        first_request=True)
                self.assertEqual(auto_budget.usage(self.connect, "test-session", 1)
                                 ["daily"]["used"], {"jobs": 2, "requests": 2,
                                                      "characters": 20})
            self.assertEqual(auto_budget.usage(self.connect, "test-session", 1)
                             ["used"], {"jobs": 4, "requests": 4,
                                        "characters": 40})

    def test_owner_day_budget_atomic_across_sessions_and_configured_downward(self):
        env = {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
               "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
               "YOSAKI_VOICE_AUTO_DAILY_JOBS": "1",
               "YOSAKI_VOICE_AUTO_DAILY_REQUESTS": "1",
               "YOSAKI_VOICE_AUTO_DAILY_CHARACTERS": "10"}
        with patch.dict(os.environ, env):
            with self.connect() as db:
                auto_budget.init_db(db)
                auto_budget.init_daily_db(db)
                db.execute("INSERT INTO auth_sessions VALUES('other-login',1)")
            outcomes = []
            def reserve(session):
                try:
                    auto_budget.reserve(self.connect, session, 1, 10,
                                        first_request=True)
                    outcomes.append("reserved")
                except auto_budget.BudgetExhausted:
                    outcomes.append("exhausted")
            workers = [threading.Thread(target=reserve, args=(session,))
                       for session in ("test-session", "other-login")]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            self.assertCountEqual(outcomes, ["reserved", "exhausted"])
            day = auto_budget.usage(self.connect, "test-session", 1)["daily"]
            self.assertEqual(day["used"], {"jobs": 1, "requests": 1,
                                           "characters": 10})
            self.assertEqual(day["remaining"], {"jobs": 0, "requests": 0,
                                                "characters": 0})
            self.assertTrue(day["reset_at"].endswith("+08:00"))
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_DAILY_JOBS": "21"}):
            with self.assertRaises(auto_budget.BudgetUnavailable):
                auto_budget.daily_limits()

    def test_daily_settings_require_explicit_increase_and_revision(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
                                     "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
                auto_budget.init_daily_db(db)
            route = "/api/voice/auto-budget/settings"
            initial = self.client.get(route)
            self.assertEqual(initial.status_code, 200)
            self.assertEqual(initial.json()["limits"],
                             {"jobs": 2, "requests": 2, "characters": 160})
            self.assertEqual(initial.json()["revision"], 0)
            payload = {"expected_revision": 0, "jobs": 3,
                       "requests": 3, "characters": 300}
            self.assertEqual(self.client.put(route, json=payload).status_code, 409)
            with self.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_auto_owner_settings")
                                 .fetchone()[0], 0)
            saved = self.client.put(route, json={**payload, "confirm_increase": True})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["revision"], 1)
            self.assertEqual(saved.json()["effective_limits"],
                             {"jobs": 3, "requests": 3, "characters": 300})
            self.assertEqual(self.client.put(route, json={**payload, "confirm_increase": True})
                             .status_code, 409)
            with self.connect() as db:
                db.execute("INSERT INTO auth_sessions VALUES('second-login',1)")
            for session in ("test-session", "second-login", "test-session"):
                auto_budget.reserve(self.connect, session, 1, 50, first_request=True)
            self.assertEqual(auto_budget.usage(self.connect, "second-login", 1)
                             ["daily"]["remaining"]["jobs"], 0)
            with self.assertRaises(auto_budget.BudgetExhausted):
                auto_budget.reserve(self.connect, "second-login", 1, 5,
                                    first_request=True)

    def test_daily_settings_are_owner_scoped_and_can_lower_without_confirmation(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_daily_db(db)
            route = "/api/voice/auto-budget/settings"
            first = self.client.put(route, json={"expected_revision": 0,
                "jobs": 1, "requests": 1, "characters": 100})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["revision"], 1)
            other = self.client.get(route, headers={"x-test-owner": "2"})
            self.assertEqual(other.json()["revision"], 0)
            self.assertEqual(other.json()["limits"]["jobs"], 2)
            self.assertEqual(self.client.put(route, json={"expected_revision": 1,
                "jobs": 21, "requests": 1, "characters": 100,
                "confirm_increase": True}).status_code, 422)

    def test_owner_day_requires_separate_schema_before_reserving(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
                                     "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
            with self.assertRaisesRegex(auto_budget.BudgetUnavailable,
                                        "auto_daily_schema_missing"):
                auto_budget.reserve(self.connect, "test-session", 1, 10,
                                    first_request=True)
            with self.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_auto_session_budget")
                                 .fetchone()[0], 0)

    def test_auto_unknown_keeps_reserved_budget_and_no_retry(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1"}):
            with self.connect() as db:
                auto_budget.init_db(db)
            self.response = lambda _request: httpx.Response(503)
            preview = self.client.get("/api/voice/messages/10/speech-preview").json()
            payload = {"preview_sha256": preview["preview_sha256"]}
            route = "/api/voice/messages/10/auto-speak"
            created = self.client.post(route, json=payload)
            self.assertEqual(created.status_code, 202)
            self.assertEqual(self.wait_job(created.json()["job_id"])["status"], "unknown")
            self.assertEqual(auto_budget.usage(self.connect, "test-session", 1)["used"]["requests"], 1)
            self.assertEqual(len(self.calls), 1)
            again = self.client.post(route, json=payload)
            self.assertEqual(again.status_code, 202)
            self.assertEqual(self.wait_job(again.json()["job_id"])["status"], "unknown")
            self.assertEqual(len(self.calls), 1)

    def test_auto_route_default_off_and_missing_schema_never_dispatch(self):
        preview = self.client.get("/api/voice/messages/10/speech-preview").json()
        payload = {"preview_sha256": preview["preview_sha256"]}
        self.assertEqual(self.client.post("/api/voice/messages/10/auto-speak", json=payload).status_code, 503)
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1"}):
            self.assertEqual(self.client.post("/api/voice/messages/10/auto-speak", json=payload).status_code, 409)
        self.assertEqual(len(self.calls), 0)


if __name__ == "__main__":
    unittest.main()
