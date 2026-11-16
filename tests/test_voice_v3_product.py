"""V3 product-mode contract on a temporary V2 database and Mock HTTP only."""

import asyncio
import json
import os
import threading
import unittest
from unittest.mock import patch

import httpx

from backend.app.voice import audio_cache, expression_product, storage
from backend.app.voice.schemas import ProfileInput


JAPANESE = "今日は静かな夜です。"
MP3 = (b"\xff\xfb\x90\x64" + bytes(413)) * 3


class VoiceV3Product(unittest.TestCase):
    def setUp(self):
        # Reuse the existing isolated FastAPI fixture without importing its
        # test class into this module's unittest discovery namespace.
        from tests.test_voice_v2 import VoiceV2

        self.v2 = VoiceV2("test_public_text_and_plan_are_conservative")
        self.v2.setUp()
        self.addCleanup(self.v2.tearDown)
        self.flag = patch.dict(os.environ, {"YOSAKI_V3_EXPRESSION_ENABLED": "1"})
        self.flag.start()
        self.addCleanup(self.flag.stop)
        with self.v2.connect() as db:
            expression_product.init_db(db)
            db.execute("UPDATE messages SET content=? WHERE id=10", (JAPANESE,))

    def preview(self, mode="none", message_id=10):
        response = self.v2.client.get(
            f"/api/voice/messages/{message_id}/speech-preview?mode={mode}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def speak(self, mode="none", message_id=10, sha=None):
        preview = self.preview(mode, message_id)
        response = self.v2.client.post(f"/api/voice/messages/{message_id}/speak",
            json={"mode": mode, "preview_sha256": sha or preview["preview_sha256"]})
        self.assertEqual(response.status_code, 202, response.text)
        return self.v2.wait_job(response.json()["job_id"])

    def test_default_plain_and_japanese_thoughtful_preview_show_exact_request(self):
        plain = self.preview()
        thoughtful = self.preview("thoughtful")
        self.assertEqual((plain["mode"], plain["segments"], plain["expression_tag"]),
                         ("none", [JAPANESE], None))
        self.assertEqual((thoughtful["mode"], thoughtful["safe_segments"],
                          thoughtful["segments"], thoughtful["expression_tag"]),
                         ("thoughtful", [JAPANESE], ["[thoughtful] " + JAPANESE],
                          "[thoughtful]"))
        self.assertEqual(thoughtful["characters"], len("[thoughtful] " + JAPANESE))
        self.assertEqual((thoughtful["model_id"], thoughtful["output_format"],
                          thoughtful["language_code"], thoughtful["effective_parameters"]),
                         ("eleven_v4", "mp3_44100_128", "ja", {"settings": "omitted"}))
        self.assertNotEqual(plain["preview_sha256"], thoughtful["preview_sha256"])
        self.assertEqual(self.v2.client.get("/api/voice/status").json()["expression_enabled"], True)
        self.assertEqual(self.v2.client.get("/api/voice/messages/10/speech-preview?mode=relaxed").status_code, 422)
        self.assertEqual(self.v2.calls, [])

    def test_source_tag_is_filtered_then_only_planner_tag_is_added(self):
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10",
                       ("[whispers] " + JAPANESE,))
        plain = self.preview()
        thoughtful = self.preview("thoughtful")
        self.assertEqual(plain["segments"], [JAPANESE])
        self.assertEqual(thoughtful["segments"], ["[thoughtful] " + JAPANESE])
        self.assertIn("来源 Audio Tags", thoughtful["skipped_reasons"])
        self.assertEqual(self.v2.calls, [])

    def test_plain_and_expression_both_bind_and_remain_playable_after_switch(self):
        normal = self.speak()
        thoughtful = self.speak("thoughtful")
        self.assertEqual((normal["status"], thoughtful["status"]), ("completed", "completed"))
        self.assertEqual(len(self.v2.calls), 2)
        self.assertEqual([json.loads(x["body"])["inputs"][0]["text"] for x in self.v2.calls],
                         [JAPANESE, "[thoughtful] " + JAPANESE])
        self.assertEqual([x["url"].split("/")[3] for x in (normal["audio"][0],
                                                         thoughtful["audio"][0])],
                         ["audio", "expression-audio"])
        with self.v2.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_message_audio").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_expression_audio").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(DISTINCT cache_key) FROM voice_audio_cache").fetchone()[0], 2)
        for entry in (normal["audio"][0], thoughtful["audio"][0]):
            self.assertEqual(self.v2.client.get(entry["url"]).content, MP3)
        for mode, expected in (("none", normal), ("thoughtful", thoughtful), ("none", normal)):
            value = self.v2.client.get(f"/api/voice/messages/10/audio?mode={mode}").json()
            self.assertEqual(value["audio"], expected["audio"])
        self.assertEqual(self.speak()["status"], "completed")
        self.assertEqual(self.speak("thoughtful")["status"], "completed")
        self.assertEqual(len(self.v2.calls), 2)

    def test_stale_mode_source_and_profile_preview_reject_without_dispatch(self):
        plain = self.preview()
        thoughtful = self.preview("thoughtful")
        wrong_mode = self.v2.client.post("/api/voice/messages/10/speak",
            json={"mode": "thoughtful", "preview_sha256": plain["preview_sha256"]})
        self.assertEqual(wrong_mode.status_code, 409)
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (JAPANESE + "ね。",))
        changed = self.v2.client.post("/api/voice/messages/10/speak",
            json={"mode": "thoughtful", "preview_sha256": thoughtful["preview_sha256"]})
        self.assertEqual(changed.status_code, 409)
        revised = self.preview("thoughtful")
        with self.v2.connect() as db:
            storage.put_profile(db, 1, ProfileInput(expected_revision=1,
                voice_id="synthetic_ivc_voice", active=True))
        stale = self.v2.client.post("/api/voice/messages/10/speak",
            json={"mode": "thoughtful", "preview_sha256": revised["preview_sha256"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.v2.calls, [])

    def test_restricted_language_segment_and_feature_gate_do_not_dispatch(self):
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content='你好。' WHERE id=10")
        self.assertEqual(self.v2.client.get(
            "/api/voice/messages/10/speech-preview?mode=thoughtful").status_code, 422)
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10",
                       ("あ" * 2100,))
        self.assertEqual(self.v2.client.get(
            "/api/voice/messages/10/speech-preview?mode=thoughtful").status_code, 422)
        with patch.dict(os.environ, {"YOSAKI_V3_EXPRESSION_ENABLED": "0"}):
            self.assertFalse(self.v2.client.get("/api/voice/status").json()["expression_enabled"])
            self.assertEqual(self.v2.client.get(
                "/api/voice/messages/10/speech-preview?mode=thoughtful").status_code, 403)
        self.assertEqual(self.v2.calls, [])

    def test_expression_tag_counts_toward_exact_request_limit(self):
        from backend.app.voice.expression_planner import TAGS
        from backend.app.voice.speech_planner import MAX_SEGMENT

        prefix = TAGS["thoughtful"]
        self.assertEqual(len(prefix), 13)
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10",
                       ("あ" * (MAX_SEGMENT - len(prefix)),))
        accepted = self.preview("thoughtful")
        self.assertEqual(accepted["characters"], MAX_SEGMENT)
        self.assertEqual(len(accepted["segments"][0]), MAX_SEGMENT)
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10",
                       ("あ" * (MAX_SEGMENT - len(prefix) + 1),))
        rejected = self.v2.client.get(
            "/api/voice/messages/10/speech-preview?mode=thoughtful")
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("超过单片段上限", rejected.json()["detail"])
        self.assertEqual(self.v2.calls, [])

    def test_missing_schema_is_not_automatically_migrated(self):
        with self.v2.connect() as db:
            db.execute("DROP TABLE voice_expression_audio")
            storage.init_db(db)
            self.assertFalse(expression_product.schema_present(db))
        self.assertFalse(self.v2.client.get("/api/voice/status").json()["expression_enabled"])
        self.assertEqual(self.v2.client.get(
            "/api/voice/messages/10/speech-preview?mode=thoughtful").status_code, 409)
        self.assertEqual(self.v2.calls, [])

    def test_revoke_consent_disables_both_audio_variants(self):
        normal = self.speak()
        thoughtful = self.speak("thoughtful")
        self.assertEqual(self.v2.client.delete("/api/voice/privacy/consent").status_code, 204)
        for entry in (normal["audio"][0], thoughtful["audio"][0]):
            self.assertEqual(self.v2.client.get(entry["url"]).status_code, 403)
        self.assertEqual(self.v2.client.get(
            "/api/voice/messages/10/speech-preview?mode=thoughtful").status_code, 403)
        self.assertEqual(len(self.v2.calls), 2)

    def test_provider_failure_and_unknown_are_not_retried(self):
        self.v2.response = lambda request: httpx.Response(401)
        failed = self.speak("thoughtful")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.speak("thoughtful")["status"], "unknown")
        self.assertEqual(len(self.v2.calls), 1)
        with self.v2.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_expression_audio").fetchone()[0], 0)

    def test_cancelled_late_adapter_result_cannot_publish_expression_audio(self):
        started = threading.Event()

        class LateAdapter:
            async def synthesize_http(self, text, voice_id, language_code):
                started.set()
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return MP3

        self.v2.jobs.adapter_factory = lambda key: LateAdapter()
        preview = self.preview("thoughtful")
        response = self.v2.client.post("/api/voice/messages/10/speak",
            json={"mode": "thoughtful", "preview_sha256": preview["preview_sha256"]})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(started.wait(2))
        self.assertEqual(self.v2.client.delete(
            f"/api/voice/jobs/{response.json()['job_id']}").status_code, 204)
        self.assertEqual(self.v2.wait_job(response.json()["job_id"])["status"], "cancelled")
        with self.v2.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_expression_audio").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM voice_audio_cache").fetchone()[0], "unknown")
        self.assertEqual(list(audio_cache.root().rglob("*.mp3")), [])

    def test_revoke_during_generation_discards_late_expression_result(self):
        started = threading.Event()

        class LateAdapter:
            async def synthesize_http(self, text, voice_id, language_code):
                started.set()
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    return MP3

        self.v2.jobs.adapter_factory = lambda key: LateAdapter()
        preview = self.preview("thoughtful")
        response = self.v2.client.post("/api/voice/messages/10/speak",
            json={"mode": "thoughtful", "preview_sha256": preview["preview_sha256"]})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(started.wait(2))
        self.assertEqual(self.v2.client.delete("/api/voice/privacy/consent").status_code, 204)
        self.assertEqual(self.v2.wait_job(response.json()["job_id"])["status"], "cancelled")
        with self.v2.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_expression_audio").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM voice_audio_cache").fetchone()[0], "unknown")
        self.assertEqual(list(audio_cache.root().rglob("*.mp3")), [])
        self.assertEqual(self.v2.calls, [])

    def test_expression_binding_protects_cache_capacity_and_backup_excludes_it(self):
        thoughtful = self.speak("thoughtful")
        with self.v2.connect() as db:
            db.execute("UPDATE voice_audio_cache SET audio_bytes=?", (audio_cache.MAX_CACHE_BYTES,))
            db.execute("INSERT INTO messages VALUES(14,1,'assistant','今日は晴れです。','completed',NULL,NULL)")
        rejected = self.speak("none", 14)
        self.assertEqual((rejected["status"], rejected["error_code"]), ("failed", "cache_capacity"))
        self.assertEqual(len(self.v2.calls), 1)
        self.assertEqual(self.v2.client.get(thoughtful["audio"][0]["url"]).status_code, 200)
        from backend.app.voice.backup_policy import sanitize_voice_snapshot
        with self.v2.connect() as db:
            snapshot = __import__("sqlite3").connect(":memory:")
            try:
                db.backup(snapshot)
                sanitize_voice_snapshot(snapshot)
                self.assertEqual(snapshot.execute(
                    "SELECT COUNT(*) FROM voice_expression_audio").fetchone()[0], 0)
                self.assertEqual(snapshot.execute(
                    "SELECT COUNT(*) FROM voice_audio_cache").fetchone()[0], 0)
                self.assertEqual(snapshot.execute(
                    "SELECT COUNT(*) FROM voice_profiles").fetchone()[0], 1)
            finally:
                snapshot.close()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_expression_audio").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
