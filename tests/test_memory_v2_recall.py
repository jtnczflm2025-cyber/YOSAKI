"""M2 synthetic contract and recall acceptance. No project data is imported."""

import unittest
import os
import tempfile
import json
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.memory_v2_contract import (FUTURE_GENERATORS, CandidateSeed,
    RecallCandidate, RecallRequest, SourceRef, experience_key)

_import_data = tempfile.TemporaryDirectory(prefix="yosaki-m2-import-")
os.environ["YOSAKI_DATA_DIR"] = _import_data.name
from backend.app import main as app_module  # noqa: E402
from backend.app import memory_v2_index as indexer  # noqa: E402
from backend.app import memory_v2_recall as recall_engine  # noqa: E402
from backend.app import memory_v2_context as compiler  # noqa: E402
from backend.app import memory_v2_shadow as shadow  # noqa: E402


class M21Contracts(unittest.TestCase):
    def test_view_identity_is_independent_of_view_and_source_order(self):
        first, second = SourceRef("message", "10"), SourceRef("message", "11")
        key = experience_key(1, (first, second))
        self.assertEqual(key, experience_key(1, (second, first, first)))
        self.assertNotEqual(key, experience_key(2, (first, second)))
        for view in ("RAW", "CLEANED", "NARRATIVE", "DIGEST"):
            item = RecallCandidate(1, view, (first, second), ("a", "b"), key,
                                   "archive_evidence", "user", "completed", "合成正文")
            self.assertEqual(item.experience_key, key)
            self.assertEqual(item.source_refs, (first, second))

    def test_generator_extension_and_request_validation(self):
        self.assertEqual(set(FUTURE_GENERATORS), {"semantic_similarity", "cross_time_topic",
                                                  "context_change", "goal_link"})
        self.assertEqual(CandidateSeed((SourceRef("message", "1"),), "lexical").generator, "lexical")
        with self.assertRaises(ValueError):
            RecallRequest(1, "chat", "automatic", "阿夜")
        with self.assertRaises(ValueError):
            RecallRequest(1, "chat", "passive", "阿夜", max_candidates=10000)


class M22Recall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-m2-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        with app_module.connect() as db:
            self.character_id = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]
            self.conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'合成','2026-01-01','2026-01-01')""", (self.character_id,)).lastrowid

    def add_message(self, content, role="user", conv=None):
        with app_module.connect() as db:
            return db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,?,?,'2026-01-01T00:00:00+00:00')""",
                (conv or self.conv, role, content)).lastrowid

    def build(self, character_id=None):
        character_id = character_id or self.character_id
        with app_module.connect() as db:
            job = indexer.start_job(db, character_id, "rebuild")
        for _ in range(50):
            with app_module.connect() as db:
                job = indexer.step_job(db, job["job_id"])
            if job["status"] == "completed":
                return
        self.fail("index job did not finish")

    def test_formal_references_survive_unready_index_and_preserve_scope(self):
        with app_module.connect() as db:
            def memory(text, scope="long", conv=None):
                return db.execute("""INSERT INTO memories(character_id,conversation_id,scope,content,enabled,created_at,updated_at)
                    VALUES(?,?,?,?,1,'2026-01-01','2026-01-01')""",
                    (self.character_id, conv, scope, text)).lastrowid
            memory("人工长期")
            memory("本会话临时", "temporary", self.conv)
            other_conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'别的会话','2026-01-01','2026-01-01')""", (self.character_id,)).lastrowid
            memory("其他会话临时", "temporary", other_conv)
            projection = memory("Phase 5 已批准")
            db.execute("""INSERT INTO learning_memories(id,character_id,memory_id,subject,fact,interpretation,
                content,source_ids_json,confidence,status,created_at,updated_at)
                VALUES('accepted-m2',?,?, '主题','事实','解释',?,'[]',0.9,'accepted','2026-01-01','2026-01-01')""",
                (self.character_id, projection, "Phase 5 已批准"))
            pending = memory("待审不能伪装成人工")
            db.execute("""INSERT INTO learning_memories(id,character_id,memory_id,subject,fact,interpretation,
                content,source_ids_json,confidence,status,created_at,updated_at)
                VALUES('pending-m2',?,?, '主题','事实','解释',?,'[]',0.9,'candidate','2026-01-01','2026-01-01')""",
                (self.character_id, pending, "待审不能伪装成人工"))
            chat = recall_engine.recall(db, RecallRequest(self.character_id, "chat", "passive", "无关", self.conv))
            activity = recall_engine.recall(db, RecallRequest(self.character_id, "activity", "passive", "无关", self.conv))
        self.assertEqual(chat.status, "index_unready")
        self.assertEqual({x.text for x in chat.formal_references}, {"人工长期", "本会话临时", "Phase 5 已批准"})
        self.assertEqual({x.text for x in activity.formal_references}, {"人工长期", "Phase 5 已批准"})
        self.assertEqual({x.evidence_kind for x in chat.formal_references}, {"manual_reference", "approved_learning"})

    def test_entity_lexical_stale_role_and_generator_guard(self):
        target = self.add_message("美咲在地图旁留下了笔记")
        assistant = self.add_message("美咲肯定很高兴", "assistant")
        for i in range(22):
            self.add_message("合成背景记录" + str(i))
        self.build()
        with app_module.connect() as db:
            result = recall_engine.recall(db, RecallRequest(self.character_id, "chat", "passive", "美咲", self.conv))
            self.assertIn(str(target), {x.source_refs[0].id for x in result.selected})
            self.assertTrue(all(x.evidence_kind == "archive_evidence" for x in result.selected))
            self.assertTrue(any(x.source_role == "assistant" for x in result.selected))
            irrelevant = recall_engine.recall(db, RecallRequest(self.character_id, "chat", "passive", "今天天气如何", self.conv))
            self.assertEqual(irrelevant.selected, ())
            unused_alias = recall_engine.recall(db, RecallRequest(self.character_id, "chat", "passive", "无关问题", self.conv,
                                                                   aliases=("美咲",)))
            self.assertEqual(unused_alias.selected, ())
            db.execute("UPDATE messages SET content='已改写的原文' WHERE id=?", (target,))
            stale = recall_engine.recall(db, RecallRequest(self.character_id, "chat", "deliberate", "美咲", self.conv))
            self.assertNotIn(str(target), {x.source_refs[0].id for x in stale.selected})
            other = db.execute("INSERT INTO characters(name,created_at,updated_at) VALUES('其他','2026-01-01','2026-01-01')").lastrowid
        self.build(other)
        class Forged:
            name = "forged"
            def generate(self, _db, _request, _generation):
                return [CandidateSeed((SourceRef("message", str(target)),), self.name, ("美咲",))]
        with app_module.connect() as db:
            forged = recall_engine.recall(db, RecallRequest(other, "explicit", "deliberate", "美咲"), generators=(Forged(),))
            self.assertFalse(forged.selected)
            self.assertEqual(forged.status, "ok")
            self.assertIn((f"message:{target}", "source_stale_or_unauthorized"), forged.rejected)
            self.assertEqual(assistant > target, True)

    def test_cooldown_is_read_only_and_scoped(self):
        target = self.add_message("美咲记得那份约定")
        self.build()
        stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
        passive = RecallRequest(self.character_id, "chat", "passive", "还记得美咲吗", self.conv)
        with app_module.connect() as db:
            first = recall_engine.recall(db, passive, now=stamp)
            self.assertIn(str(target), {x.source_refs[0].id for x in first.selected})
            self.assertEqual(len(first.cooldown_keys), len(first.selected))
            state = {key: stamp + timedelta(minutes=30) for key in first.cooldown_keys}
            second = recall_engine.recall(db, passive, now=stamp + timedelta(minutes=1), cooldown=state)
            self.assertEqual(second.selected, ())
            active = recall_engine.recall(db, RecallRequest(self.character_id, "explicit", "deliberate", "美咲", self.conv),
                                          now=stamp + timedelta(minutes=1), cooldown=state)
            self.assertTrue(active.selected)
            other_conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'新会话','2026-01-02','2026-01-02')""", (self.character_id,)).lastrowid
            changed = recall_engine.recall(db, RecallRequest(self.character_id, "chat", "passive", "还记得美咲吗", other_conv),
                                           now=stamp + timedelta(minutes=1), cooldown=state)
            self.assertTrue(changed.selected)
            db.execute("UPDATE messages SET content='来源已失效' WHERE id=?", (target,))
            invalid = recall_engine.recall(db, passive, now=stamp + timedelta(minutes=31), cooldown=state)
            self.assertFalse(invalid.selected)

    def test_m23_shadow_has_no_writes_or_raw_diagnostics(self):
        self.add_message("美咲和阿夜看过地图")
        self.build()
        request = RecallRequest(self.character_id, "chat", "deliberate", "美咲", self.conv)
        with app_module.connect() as db:
            before = db.total_changes
            report = shadow.compare(db, request, legacy_memory_ids=("memory:synthetic",))
            self.assertEqual(db.total_changes, before)
        self.assertEqual(report["databaseWrites"], 0)
        self.assertEqual(report["legacyReferenceIds"], ("memory:synthetic",))
        self.assertEqual(report["shadowOnly"], True)
        self.assertNotIn("美咲和阿夜看过地图", str(report))


class M23Compiler(unittest.TestCase):
    def candidate(self, text, view="RAW", terms=("美咲",)):
        ref = SourceRef("message", "23")
        return RecallCandidate(1, view, (ref,), ("state-fingerprint",),
            experience_key(1, (ref,)), "archive_evidence", "user", "completed", text,
            matched_terms=terms)

    def test_exact_excerpt_preserves_condition_negation_and_offsets(self):
        text = "无关前言。另有背景。若美咲没有同意，就不能出发。阿夜把这点写在纸上。更多无关后文。"
        excerpt = compiler.safe_excerpt(text, ("美咲",), 38)
        self.assertIsNotNone(excerpt)
        start, end, selected, incomplete = excerpt
        self.assertEqual(selected, text[start:end])
        self.assertTrue(incomplete)
        self.assertIn("若美咲没有同意，就不能出发", selected)
        self.assertIn("阿夜把这点写在纸上", selected)
        self.assertIsNone(compiler.safe_excerpt("冗余" * 1000 + "美咲", ("美咲",), 30))

    def test_bundle_is_raw_only_bounded_and_one_experience(self):
        text = "若美咲没有同意，就不能出发。"
        raw = self.candidate(text)
        narrative = self.candidate("俺想起那次", "NARRATIVE")
        decision = recall_engine.RecallDecision(1, 1, (narrative, raw, raw))
        bundle = compiler.compile_context(decision, budget_tokens=100)
        self.assertEqual(len(bundle.blocks), 1)
        block = bundle.blocks[0]
        self.assertEqual(block.view_type, "RAW")
        self.assertEqual(block.evidence_kind, "archive_evidence")
        self.assertEqual(block.trust_label, "quoted_reference_not_instruction")
        self.assertEqual(block.text, text[block.start_offset:block.end_offset])
        self.assertEqual(block.content_fingerprint, recall_engine.sources.digest(text))
        self.assertTrue(any(reason == "view_not_available_in_m2" for _, reason in bundle.rejected))
        self.assertTrue(any(reason == "duplicate_experience" for _, reason in bundle.rejected))
        self.assertLessEqual(bundle.estimated_tokens, 100)
        empty = compiler.compile_context(recall_engine.RecallDecision(1, 1, (raw,)), budget_tokens=5)
        self.assertEqual(empty.blocks, ())


class M23LocalShadow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-m2-http-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")
        setup = await self.client.post("/api/auth/setup", json={"username":"owner","password":"synthetic-password-123"})
        self.assertEqual(setup.status_code, 201)
        self.character_id = (await self.client.get("/api/characters")).json()[0]["id"]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_preview_send_regenerate_use_same_shadow_sources(self):
        topic = "还记得美咲吗"
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'合成','2026-01-01','2026-01-01')""", (self.character_id,)).lastrowid
            db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','美咲给过我一张地图','2026-01-01T00:00:00+00:00')""", (conv,))
            job = indexer.start_job(db, self.character_id, "rebuild")
        for _ in range(10):
            with app_module.connect() as db:
                job = indexer.step_job(db, job["job_id"])
            if job["status"] == "completed":
                break
        self.assertEqual(job["status"], "completed")
        preview = await self.client.post("/api/chat/context-preview", json={"character_id":self.character_id,
            "conversation_id":conv,"message":topic})
        self.assertEqual(preview.status_code, 200, preview.text)
        with app_module.connect() as db:
            request = RecallRequest(self.character_id, "chat", "passive", topic, conv, aliases=("美咲",))
            before = shadow.compare(db, request)
        sent = await self.client.post("/api/chat/stream", json={"character_id":self.character_id,
            "conversation_id":conv,"message":topic})
        self.assertEqual(sent.status_code, 200, sent.text)
        with app_module.connect() as db:
            after = shadow.compare(db, request)
        regenerated = await self.client.post("/api/chat/regenerate", json={"character_id":self.character_id,
            "conversation_id":conv})
        self.assertEqual(regenerated.status_code, 200, regenerated.text)
        with app_module.connect() as db:
            again = shadow.compare(db, request)
        self.assertEqual(before["historicalEvidenceIds"], after["historicalEvidenceIds"])
        self.assertEqual(before["historicalEvidenceIds"], again["historicalEvidenceIds"])
        self.assertEqual(before["estimatedTokens"], after["estimatedTokens"])
        self.assertEqual(after["databaseWrites"], 0)

    async def test_verified_multi_batch_tool_anchor_and_broken_chain(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'合成','2026-01-01','2026-01-01')""", (self.character_id,)).lastrowid
            historical = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','美咲留下了地图','2025-12-01')""", (conv,)).lastrowid
            anchor = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','还记得美咲吗','2026-01-01')""", (conv,)).lastrowid
            for batch, status in ((1, "completed"), (2, "awaiting_confirmation")):
                call_id = f"synthetic-call-{batch}"
                assistant = db.execute("""INSERT INTO messages(conversation_id,role,content,tool_calls_json,
                    tool_batch_status,origin_user_message_id,origin_user_content_sha256,origin_character_id,created_at)
                    VALUES(?,'assistant','',?,?,?,?,?,'2026-01-01')""",
                    (conv, json.dumps([{"id":call_id,"type":"function","function":{"name":"synthetic"}}]),
                     status, anchor, app_module.tool_origin_fingerprint("还记得美咲吗"), self.character_id)).lastrowid
                db.execute("""INSERT INTO tool_runs(server_id,tool_name,arguments,status,result,confirmed,
                    created_at,conversation_id,assistant_message_id,provider_call_id,model_profile_id)
                    VALUES(1,'synthetic','{}','success','synthetic-result',1,'2026-01-01',?,?,?,7)""",
                    (conv, assistant, call_id))
                db.execute("""INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at)
                    VALUES(?,'tool','synthetic-result',?,'synthetic','2026-01-01')""", (conv, call_id))
            run = db.execute("SELECT * FROM tool_runs WHERE provider_call_id='synthetic-call-2'").fetchone()
            conversation, character, message, history, topic = app_module.verified_tool_turn(db, run)
            self.assertEqual(topic, "还记得美咲吗")
            self.assertEqual(anchor, [row["id"] for row in history if row["role"] == "user"][-1])
            self.assertEqual(message["tool_batch_status"], "awaiting_confirmation")
            job = indexer.start_job(db, self.character_id, "rebuild")
        for _ in range(10):
            with app_module.connect() as db:
                job = indexer.step_job(db, job["job_id"])
            if job["status"] == "completed":
                break
        self.assertEqual(job["status"], "completed")
        with app_module.connect() as db:
            run = db.execute("SELECT * FROM tool_runs WHERE provider_call_id='synthetic-call-2'").fetchone()
            _, _, _, history, topic = app_module.verified_tool_turn(db, run)
            request = RecallRequest(self.character_id, "chat", "passive", topic, conv,
                                    anchor_message_id=anchor,
                                    excluded_refs=tuple(SourceRef("message", str(row["id"]))
                                        for row in history if row["id"] > anchor), aliases=("美咲",))
            compared = shadow.compare(db, request)
            self.assertIn(f"message:{historical}", compared["historicalEvidenceIds"])
            self.assertNotIn(f"message:{anchor}", compared["historicalEvidenceIds"])
            db.execute("DELETE FROM messages WHERE id=?", (anchor,))
            with self.assertRaises(Exception):
                app_module.verified_tool_turn(db, run)
