"""P1-A acceptance using synthetic messages and isolated SQLite only."""

import concurrent.futures
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_derivation import M3Base, app_module, derived, schema
from backend.app import memory_v2_entries as entries
from backend.app import memory_v2_entry_api as api


class EntryBase(M3Base):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,created_at) VALUES(1,'p1a-owner','unused','2026-01-01')")

    def narrative(self, db, refs=None, body="她说今天累了。我说那就休息。"):
        refs = self.refs() if refs is None else refs
        item = derived.create_derivation(db, self.cid, "NARRATIVE", refs)
        version = derived.add_version(db, item["id"], refs, body)
        self.approve(db, version["id"])
        return version

    def new_entry(self, db, *, refs=None, title="2026-01-01 休息", synopsis="她说今天累了，我建议休息。",
                  narrative=None):
        refs = self.refs() if refs is None else refs
        narrative = self.narrative(db, refs) if narrative is None else narrative
        item = entries.create_entry(db, self.cid)
        version = entries.add_version(db, item["id"], refs, title, ["休息", "阿夜"], ["对话"], synopsis,
                                      narrative["id"], 1)
        return item, version, narrative

    def approve_entry(self, db, version):
        entries.review_version(db, version["id"], self.cid, 1, version["metadata_hash"],
                               version["source_hash"])
        return entries.activate_version(db, version["id"], self.cid)


class P1AEntries(EntryBase):
    def test_complete_entry_and_review_gate(self):
        with app_module.connect() as db:
            item, version, narrative = self.new_entry(db)
            with self.assertRaisesRegex(ValueError, "valid_review_required"):
                entries.activate_version(db, version["id"], self.cid)
            self.approve_entry(db, version)
            detail = entries.read_entry(db, item["id"], self.cid)
            self.assertEqual(detail["entryId"], item["id"])
            self.assertEqual(detail["sourceRefs"], self.refs())
            self.assertEqual(detail["narrativeVersionId"], narrative["id"])
            self.assertEqual(detail["timeBasis"], "occurred")
            self.assertEqual(detail["occurredAt"], ["2026-01-01", "2026-01-01"])
            self.assertEqual(detail["title"], "2026-01-01 休息")
            self.assertEqual(detail["synopsis"], "她说今天累了，我建议休息。")
            self.assertNotIn("body", detail)
            self.assertEqual(derived.read_version(db, narrative["id"], self.cid)["body"],
                             "她说今天累了。我说那就休息。")

    def test_title_revision_is_selective_and_new_review_required(self):
        with app_module.connect() as db:
            item, first, narrative = self.new_entry(db)
            self.approve_entry(db, first)
            before = entries.dependency_tokens(db, first["id"], self.cid)
            second = entries.add_version(db, item["id"], self.refs(), "2026-01-01 小憩",
                ["休息", "阿夜"], ["对话"], "她说今天累了，我建议休息。", narrative["id"], 1)
            with self.assertRaisesRegex(ValueError, "valid_review_required"):
                entries.activate_version(db, second["id"], self.cid)
            self.approve_entry(db, second)
            after = entries.dependency_tokens(db, second["id"], self.cid)
            self.assertNotEqual(before["title"], after["title"])
            for aspect in ("source", "keywords", "tags", "synopsis", "narrative", "time"):
                self.assertEqual(before[aspect], after[aspect], aspect)
            self.assertEqual(derived.validity(db, narrative["id"], self.cid), None)
            self.assertEqual(entries._version(db, first["id"])["status"], "stale")
            with self.assertRaises(ValueError):
                entries.read_entry(db, first["id"], self.cid)
            self.assertEqual(entries.read_entry(db, item["id"], self.cid)["title"], "2026-01-01 小憩")

    def test_synopsis_and_keywords_have_distinct_dependency_tokens(self):
        with app_module.connect() as db:
            item, first, narrative = self.new_entry(db)
            self.approve_entry(db, first)
            baseline = entries.dependency_tokens(db, first["id"], self.cid)
            revised = entries.add_version(db, item["id"], self.refs(), first["title"],
                ["休息", "阿夜", "小憩"], ["对话"], "她说累了，我建议先休息。", narrative["id"], 1)
            self.approve_entry(db, revised)
            updated = entries.dependency_tokens(db, revised["id"], self.cid)
            self.assertNotEqual(baseline["keywords"], updated["keywords"])
            self.assertNotEqual(baseline["synopsis"], updated["synopsis"])
            for aspect in ("source", "title", "tags", "narrative", "time"):
                self.assertEqual(baseline[aspect], updated[aspect], aspect)

    def test_source_edit_delete_revoke_and_reindex(self):
        with app_module.connect() as db:
            item, version, _ = self.new_entry(db)
            self.approve_entry(db, version)
            db.execute("UPDATE messages SET content='已修改的合成发言' WHERE id=?", (self.user,))
            with self.assertRaisesRegex(ValueError, "source_missing_or_stale"):
                entries.read_entry(db, item["id"], self.cid)
            self.assertEqual(entries.validity(db, version["id"], self.cid, preview=True), "source_missing_or_stale")
            self._index(db)
            self.assertEqual(entries.validity(db, version["id"], self.cid), "source_changed")
            db.execute("DELETE FROM messages WHERE id=?", (self.user,))
            with self.assertRaises(ValueError):
                entries.read_entry(db, item["id"], self.cid)
            db.execute("UPDATE characters SET enabled=0 WHERE id=?", (self.cid,))
            self.assertEqual(entries.validity(db, version["id"], self.cid), "role_unavailable")

    def test_imported_message_does_not_invent_occurred_at(self):
        with app_module.connect() as db:
            db.execute("UPDATE messages SET generation_status='imported' WHERE id IN (?,?)",
                       (self.user, self.assistant))
            self._index(db)
            with self.assertRaisesRegex(ValueError, "title_date_unverified"):
                self.new_entry(db)
            item, version, _ = self.new_entry(db, title="休息")
            self.approve_entry(db, version)
            detail = entries.read_entry(db, item["id"], self.cid)
            self.assertEqual(detail["timeBasis"], "unknown")
            self.assertEqual(detail["occurredAt"], [None, None])
            self.assertEqual(detail["recordedAt"], [None, None])

    def test_narrative_replacement_and_forged_source(self):
        with app_module.connect() as db:
            item, version, old = self.new_entry(db)
            self.approve_entry(db, version)
            other = derived.add_version(db, old["derivation_id"], self.refs(), "合成新纪事")
            self.approve(db, other["id"])
            self.assertEqual(entries.validity(db, version["id"], self.cid), "narrative_not_current")
            new = entries.add_version(db, item["id"], self.refs(), "2026-01-01 休息",
                ["休息"], ["对话"], "合成梗概", other["id"], 1)
            self.approve_entry(db, new)
            self.assertEqual(entries.read_entry(db, item["id"], self.cid)["narrativeVersionId"], other["id"])
            other_role = db.execute("""INSERT INTO characters(name,created_at,updated_at)
                VALUES('other-p1a','2026-01-01','2026-01-01')""").lastrowid
            with self.assertRaises(ValueError):
                entries.read_entry(db, item["id"], other_role)
            with self.assertRaises(ValueError):
                entries.add_version(db, item["id"], [{"kind": "message", "id": "999999"}],
                                    "伪造", [], [], "伪造", None, 1)

    def test_digest_cannot_stand_in_for_detailed_narrative(self):
        with app_module.connect() as db:
            digest = derived.create_derivation(db, self.cid, "DIGEST", self.refs())
            digest_version = derived.add_version(db, digest["id"], self.refs(), "一周摘录")
            self.approve(db, digest_version["id"])
            entry = entries.create_entry(db, self.cid)
            with self.assertRaisesRegex(ValueError, "narrative_not_current"):
                entries.add_version(db, entry["id"], self.refs(), "2026-01-01 休息", [], [],
                                    "单经历梗概。", digest_version["id"], 1)

    def test_activity_unknown_keeps_safe_evidence_and_excludes_payload(self):
        with app_module.connect() as db:
            db.execute("""INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,
                started_at,deadline_at,max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json,
                environment_id) VALUES('p1a-session',?,0,'completed','2026-01-01','2026-01-02',
                0,0,'[]','{}','local-studio-v1')""", (self.cid,))
            db.execute("""INSERT INTO activity_operations(id,character_id,session_id,kind,action_id,
                arguments_json,status,result_json,created_at,updated_at) VALUES(
                'p1a-op',?,'p1a-session','environment','inspect:map','{"secret":"PRIVATE-ARG"}',
                'unknown','{"secret":"PRIVATE-RESULT"}','2026-01-01','2026-01-02')""", (self.cid,))
            self._index(db)
            refs = self.refs() + [{"kind": "activity_operation", "id": "p1a-op"}]
            narrative = self.narrative(db, refs, "她说今天累了。我说休息。另有一次查看地图，结果未知。")
            item, version, _ = self.new_entry(db, refs=refs, title="休息与地图", narrative=narrative)
            self.approve_entry(db, version)
            result = entries.read_entry(db, item["id"], self.cid)
            self.assertEqual(result["timeBasis"], "mixed")
            self.assertEqual(result["sourceRefs"][-1], refs[-1])
            safe = derived.live_source(db, self.cid, "activity_operation", "p1a-op")
            self.assertIn("结果未知", safe.text)
            self.assertNotIn("PRIVATE-ARG", safe.text)
            self.assertNotIn("PRIVATE-RESULT", safe.text)

    def test_corrupt_active_version_pointer_cannot_cross_entry(self):
        with app_module.connect() as db:
            first, fv, narrative = self.new_entry(db)
            self.approve_entry(db, fv)
            second, sv, _ = self.new_entry(db, title="2026-01-01 另一个条目", narrative=narrative)
            self.approve_entry(db, sv)
            db.execute("UPDATE mv3_entries SET active_version_id=? WHERE id=?", (sv["id"], first["id"]))
            with self.assertRaisesRegex(ValueError, "entry_version_owner_mismatch"):
                entries.read_entry(db, first["id"], self.cid)

    def test_review_marker_tamper_blocks_formal_read(self):
        with app_module.connect() as db:
            item, version, _ = self.new_entry(db)
            self.approve_entry(db, version)
            db.execute("UPDATE mv3_entry_versions SET review_status='unreviewed' WHERE id=?", (version["id"],))
            with self.assertRaisesRegex(ValueError, "review_binding_invalid"):
                entries.read_entry(db, item["id"], self.cid)

    def test_split_merge_lineage_and_source_set_gate(self):
        with app_module.connect() as db:
            parent, pv, narrative = self.new_entry(db)
            self.approve_entry(db, pv)
            left, right = [self.refs()[0]], [self.refs()[1]]
            with self.assertRaisesRegex(ValueError, "source_set_requires_restructure"):
                entries.add_version(db, parent["id"], left, "错误", [], [], "错误", None, 1)
            nl = self.narrative(db, left, "她说今天累了。")
            nr = self.narrative(db, right, "我说那就休息。")
            specs = [{"sourceRefs": left, "title": "用户", "keywords": [], "tags": [],
                      "synopsis": "她说累了。", "narrativeVersionId": nl["id"], "editedBy": 1},
                     {"sourceRefs": right, "title": "阿夜", "keywords": [], "tags": [],
                      "synopsis": "我说休息。", "narrativeVersionId": nr["id"], "editedBy": 1}]
            children = entries.propose_restructure(db, [parent["id"]], specs, "split", self.cid)
            self.assertEqual(entries.read_entry(db, parent["id"], self.cid)["entryId"], parent["id"])
            with self.assertRaises(ValueError):
                entries.finalize_restructure(db, [parent["id"]], children, self.cid)
            for child in children:
                v = db.execute("SELECT * FROM mv3_entry_versions WHERE entry_id=?", (child,)).fetchone()
                self.approve_entry(db, dict(v))
            entries.finalize_restructure(db, [parent["id"]], children, self.cid)
            with self.assertRaisesRegex(ValueError, "entry_not_active"):
                entries.read_entry(db, parent["id"], self.cid)
            merged = entries.propose_restructure(db, children, [{"sourceRefs": self.refs(),
                "title": "合并", "keywords": ["休息"], "tags": [], "synopsis": "合成合并梗概",
                "narrativeVersionId": narrative["id"], "editedBy": 1}], "merge", self.cid)
            mv = db.execute("SELECT * FROM mv3_entry_versions WHERE entry_id=?", (merged[0],)).fetchone()
            self.approve_entry(db, dict(mv))
            entries.finalize_restructure(db, children, merged, self.cid)
            self.assertEqual(entries.read_entry(db, merged[0], self.cid)["sourceRefs"], self.refs())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_lineage").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_lineage WHERE status='finalized'").fetchone()[0], 4)

    def test_cancelled_split_preserves_parent_and_audit(self):
        with app_module.connect() as db:
            parent, pv, _ = self.new_entry(db)
            self.approve_entry(db, pv)
            specs = [{"sourceRefs": [ref], "title": "草稿", "keywords": [], "tags": [],
                      "synopsis": "待人工整理。", "editedBy": 1} for ref in self.refs()]
            children = entries.propose_restructure(db, [parent["id"]], specs, "split", self.cid)
            result = entries.cancel_restructure(db, [parent["id"]], children, self.cid)
            self.assertEqual(result["retainedParents"], [parent["id"]])
            self.assertEqual(entries.read_entry(db, parent["id"], self.cid)["entryId"], parent["id"])
            self.assertTrue(all(entries._entry(db, child)["status"] == "archived" for child in children))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_lineage WHERE status='cancelled'").fetchone()[0], 2)

    def test_old_db_migration_idempotence_cascade_and_concurrency(self):
        with app_module.connect() as db:
            db.execute("DROP TABLE mv3_entry_lineage")
            db.execute("DROP TABLE mv3_entry_reviews")
            db.execute("DROP TABLE mv3_entry_sources")
            db.execute("DROP TABLE mv3_entry_versions")
            db.execute("DROP TABLE mv3_entries")
            schema.init_db(db)
            schema.init_db(db)
            item, version, _ = self.new_entry(db)
            entries.review_version(db, version["id"], self.cid, 1, version["metadata_hash"], version["source_hash"])
        def activate():
            try:
                with app_module.connect() as db:
                    entries.activate_version(db, version["id"], self.cid)
                return "ok"
            except ValueError:
                return "rejected"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: activate(), range(2))), ["ok", "rejected"])
        with app_module.connect() as db:
            db.execute("DELETE FROM characters WHERE id=?", (self.cid,))
            self.assertIsNone(entries._entry(db, item["id"]))


class P1AApi(EntryBase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        api.install(app, app_module.connect, app_module.current_session)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with app_module.connect() as db:
            db.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,1,'2026-01-01','2099-01-01')",
                       (app_module.session_digest("p1a-token"),))
        self.client.cookies.set(app_module.SESSION_COOKIE, "p1a-token")

    def test_api_create_review_revise_source_expiry(self):
        with app_module.connect() as db:
            narrative = self.narrative(db)
        base = "/api/memory-v2/entries"
        payload = {"characterId": self.cid, "sourceRefs": self.refs(), "title": "2026-01-01 休息",
                   "keywords": ["休息"], "tags": ["对话"], "synopsis": "她说累了，我建议休息。",
                   "narrativeVersionId": narrative["id"]}
        created = self.client.post(base, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        entry, v = created.json()["entryId"], created.json()["versionId"]
        root = base + "/" + entry
        vp = root + "/versions/" + v
        self.assertEqual(self.client.get(root, params={"characterId": self.cid}).status_code, 409)
        self.assertEqual(self.client.get(vp + "/preview", params={"characterId": self.cid}).json()["eligibleForRecall"], False)
        self.assertEqual(self.client.post(vp + "/activate", params={"characterId": self.cid}).status_code, 409)
        forged = {"characterId": self.cid, "metadataHash": created.json()["metadataHash"],
                  "sourceHash": created.json()["sourceHash"], "reviewerId": 1, "status": "active"}
        self.assertEqual(self.client.post(vp + "/review", json=forged).status_code, 422)
        forged.pop("reviewerId")
        forged.pop("status")
        self.assertEqual(self.client.post(vp + "/review", json=forged).status_code, 200)
        self.assertEqual(self.client.post(vp + "/activate", params={"characterId": self.cid}).status_code, 200)
        self.assertEqual(self.client.get(root, params={"characterId": self.cid}).json()["title"], payload["title"])
        self.assertEqual(self.client.get(vp + "/sources", params={"characterId": self.cid}).status_code, 200)
        revised = self.client.post(root + "/versions", json={**payload, "title": "修订标题"})
        self.assertEqual(revised.status_code, 201, revised.text)
        self.assertEqual(self.client.post(root + "/versions/" + revised.json()["versionId"] + "/activate",
            params={"characterId": self.cid}).status_code, 409)
        with app_module.connect() as db:
            db.execute("UPDATE messages SET content='edited' WHERE id=?", (self.user,))
        for path in (root, vp + "/preview", vp + "/sources",
                     root + "/versions/" + revised.json()["versionId"] + "/preview"):
            response = self.client.get(path, params={"characterId": self.cid})
            self.assertEqual(response.status_code, 409, (path, response.text))
            self.assertNotIn("她说累了", response.text)
        hist = self.client.get(root + "/versions", params={"characterId": self.cid})
        self.assertEqual(hist.status_code, 200)
        self.assertTrue(all("title" not in row and "synopsis" not in row for row in hist.json()))
        self.client.cookies.clear()
        self.assertEqual(self.client.get(root + "/versions", params={"characterId": self.cid}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
