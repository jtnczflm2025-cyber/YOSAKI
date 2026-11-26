"""Affected Voice paths, with isolated SQLite and intercepted provider I/O."""
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

import httpx

from backend.app.voice import automatic_expression, storage
from backend.app.voice.schemas import ProfileInput


class VoiceDeliveryLocal(unittest.TestCase):
    def setUp(self):
        from tests.test_voice_v3_product import VoiceV3Product
        self.v3 = VoiceV3Product()
        self.v3.setUp()
        self.addCleanup(self.v3.doCleanups)
        flag = patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_EXPRESSION_ENABLED":"1"})
        flag.start()
        self.addCleanup(flag.stop)
        self.v2 = self.v3.v2
        with self.v2.connect() as db:
            automatic_expression.init_db(db)
            db.execute("CREATE TABLE psychological_snapshots(character_id INTEGER PRIMARY KEY,state_json TEXT)")

    def prepare_expression(self, text, personality, mode="auto", snapshot=True):
        with self.v2.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (text,))
            db.execute("DELETE FROM psychological_snapshots")
            if snapshot:
                db.execute("INSERT INTO psychological_snapshots VALUES(1,?)",
                           (json.dumps({"emotion":{"arousal":.2,"tension":.2,"valence":0}}),))
            db.commit()
            revision = storage.profile(db, 1)["revision"]
            storage.put_profile(db, 1, ProfileInput(expected_revision=revision,
                voice_id="synthetic_ivc_voice", active=True,
                configuration={"personality":personality,"expression_mode":mode}))

    def test_all_rule_tags_go_only_in_http_request_and_cache_freezes_choice(self):
        rows = (
            ("のんびり話そう。", "慵懒", "relaxed", "relaxed"),
            ("言葉を選びたい。", "克制", "restrained", "thoughtful"),
            ("冗談だけどね。", "冷幽默", "dry_humor", "sarcastic"),
            ("仕方がないね。", "无奈", "resigned", "sighs"),
            ("小声で話そう。", "低语", "whisper", "whispers"),
            ("ふふっ、そうか。", "轻笑", "chuckle", "chuckles"),
        )
        candidate_styles = patch.object(automatic_expression, "DAILY_STYLES",
                                        frozenset(automatic_expression.RULES))
        candidate_styles.start()
        self.addCleanup(candidate_styles.stop)
        for i, (text, personality, style, tag) in enumerate(rows, 1):
            with self.subTest(style=style):
                self.prepare_expression("[whispers] " + text, personality)
                before = self.v3.preview("auto")
                self.assertEqual(before["expression_style"], style)
                self.assertEqual(before["segments"], [f"[{tag}] {text}"])
                self.assertEqual(self.v3.preview("none")["segments"], [text])
                with self.v2.connect() as db:
                    db.execute("DELETE FROM psychological_snapshots")
                # The repeated preview and cache path must not read Psychology.
                with patch.object(automatic_expression, "_choose", side_effect=AssertionError):
                    job = self.v3.speak("auto", sha=before["preview_sha256"])
                    self.assertEqual(job["status"], "completed")
                    self.assertEqual(self.v3.speak("auto")["audio"], job["audio"])
                    self.assertEqual(self.v2.client.get(job["audio"][0]["url"]).status_code, 200)
                sent = json.loads(self.v2.calls[-1]["body"])
                self.assertEqual(sent["inputs"][0]["text"], f"[{tag}] {text}")
                self.assertEqual(len(self.v2.calls), i)
                with self.v2.connect() as db:
                    content = db.execute("SELECT content FROM messages WHERE id=10").fetchone()[0]
                self.assertEqual(content, "[whispers] " + text)

    def test_manual_precedence_and_conservative_fallback(self):
        self.prepare_expression("小声で話そう。", "低语", snapshot=False)
        self.assertEqual(self.v3.preview("auto")["effective_mode"], "none")
        self.prepare_expression("よくわからない。", "低语")
        self.assertIsNone(self.v3.preview("auto")["expression_tag"])
        self.prepare_expression("小声で話そう。", "低语", mode="thoughtful")
        self.assertEqual(self.v3.preview("auto")["expression_tag"], "[thoughtful]")
        self.prepare_expression("小声で話そう。", "低语")
        self.assertEqual(self.v3.preview("thoughtful")["expression_tag"], "[thoughtful]")
        self.prepare_expression("轻声说吧。", "低语")
        self.assertEqual(self.v3.preview("auto")["effective_mode"], "none")
        self.assertEqual(self.v2.calls, [])

    def test_daily_auto_allows_restrained_only(self):
        self.prepare_expression("のんびり話そう。", "慵懒")
        candidate = self.v3.preview("auto")
        self.assertEqual((candidate["effective_mode"], candidate["expression_style"],
                          candidate["expression_tag"]), ("none", "candidate_not_enabled", None))
        self.prepare_expression("言葉を選びたい。今は静かに話そう。", "克制")
        allowed = self.v3.preview("auto")
        self.assertEqual((allowed["effective_mode"], allowed["expression_tag"]),
                         ("auto", "[thoughtful]"))
        self.assertEqual(self.v2.calls, [])

    def test_new_two_segment_fixture_uses_product_preview(self):
        fixture = Path(__file__).parent / "fixtures/voice_delivery_two_segment_zh.txt"
        self.prepare_expression(fixture.read_text(encoding="utf-8"), "", mode="none")
        preview = self.v3.preview()
        self.assertEqual((preview["requests"],preview["language_code"]), (2,"zh"))
        self.assertGreater(preview["characters"], 2000)
        job = self.v3.speak()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(job["audio"]), 2)
        self.assertEqual([json.loads(row["body"])["inputs"][0]["text"]
                          for row in self.v2.calls],preview["segments"])

    def test_first_segment_failure_phase_and_no_second_attempt(self):
        class BrokenBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"partial"
                raise httpx.ReadError("synthetic")

        def response(phase):
            if phase == "connecting":
                raise httpx.ConnectError("synthetic")
            if phase == "waiting_response":
                raise httpx.ReadTimeout("synthetic")
            return httpx.Response(200, stream=BrokenBody())

        for index, phase in enumerate(("connecting","waiting_response","receiving_audio")):
            with self.subTest(phase=phase):
                self.prepare_expression(("あ" * 1150 + "。\n\n") * 2 + str(index), "", mode="none")
                self.v2.response = lambda _request: response(phase)
                job = self.v3.speak()
                self.assertEqual((job["status"],job["failed_segment"],job["failure_phase"]),
                                 ("unknown",1,phase))
                self.assertEqual(len(self.v2.calls), index + 1)
