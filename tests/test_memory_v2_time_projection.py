"""P2 isolated Chronicle tree, digest binding and context projection tests."""

import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_entries import EntryBase, app_module, derived, entries
from backend.app import memory_v2_time_projection as p2
from backend.app import memory_v2_time_api as api
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_index as indexer


class P2Base(EntryBase):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
        self.ids = {}
        with app_module.connect() as db:
            p2.init_db(db)
            p2.init_db(db)
            refs = {}
            for age in (0, 7, 30, 90, 180, 365):
                ts = (self.now - timedelta(days=age)).isoformat()
                source_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                    VALUES(?,'user',?,?)""", (self.conv, f"合成的第{age}天记录。", ts)).lastrowid
                refs[age] = [{"kind": "message", "id": str(source_id)}]
            self._index(db)
            for age, source_refs in refs.items():
                narrative = self.narrative(db, source_refs, f"她说第{age}天的合成记录。")
                item = entries.create_entry(db, self.cid)
                version = entries.add_version(db, item["id"], source_refs, f"第{age}天",
                    [f"第{age}天"], ["合成"], f"第{age}天的单经历梗概。", narrative["id"], 1)
                self.approve_entry(db, version)
                self.ids[age] = item["id"]

    def root(self, db, level, now=None, offset=480):
        nodes = p2.browse(db, self.cid, now=now or self.now, offset_minutes=offset)["nodes"]
        return next(node for node in nodes if node["level"] == level)

    def approved_digest(self, db, node_id, now=None):
        version = p2.create_extractive_digest(db, self.cid, node_id, now=now or self.now)
        self.approve(db, version["id"])
        return version


class P2Projection(P2Base):
    def test_old_backup_without_chronicle_schema_requires_rebuild_without_writes(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            before = db.total_changes
            view = p2.browse(db, self.cid, now=self.now)
            self.assertEqual(view["state"], "needs_schema")
            self.assertEqual(view["nodes"], [])
            self.assertEqual(db.total_changes, before)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_time_digest_bindings'").fetchone())
        finally:
            db.close()

    def test_six_boundaries_dynamic_age_and_full_raw_at_l5(self):
        with app_module.connect() as db:
            nodes = p2.browse(db, self.cid, now=self.now, limit=50)["nodes"]
            self.assertEqual({node["level"] for node in nodes}, {f"L{i}" for i in range(6)})
            self.assertEqual(self.root(db, "L5")["entryIds"], [self.ids[365]])
            old_node = self.root(db, "L5")["nodeId"]
            for level in ("L5", "L4", "L3", "L2", "L1"):
                opened = p2.expand_node(db, self.cid, old_node, now=self.now)
                self.assertEqual(opened["level"], level)
                if opened["childNodeIds"]:
                    old_node = opened["childNodeIds"][0]
            raw = p2.unfold_raw(db, self.cid, self.ids[365], 0, limit=5)
            self.assertEqual(raw["text"], "合成的第3")
            rest = p2.unfold_raw(db, self.cid, self.ids[365], 0, offset=raw["nextOffset"],
                                 expected_content_hash=raw["contentHash"])
            self.assertEqual(raw["text"] + rest["text"], "合成的第365天记录。")
            after_week = p2.browse(db, self.cid, now=self.now + timedelta(days=7), limit=50)["nodes"]
            self.assertTrue(any(self.ids[0] in node["entryIds"] and node["level"] == "L1"
                                for node in after_week))

    def test_same_experience_projects_across_all_six_ages(self):
        with app_module.connect() as db:
            source_id = self.ids[0]
            # Keep only this selected experience so period digests do not pull
            # unrelated, unselected entries into a context projection.
            db.executemany("UPDATE mv3_entries SET status='archived' WHERE id=?",
                           [(entry_id,) for age, entry_id in self.ids.items() if age != 0])
            outcomes = []
            for age in (0, 7, 30, 90, 180, 365):
                view_time = self.now + timedelta(days=age)
                root = next(n for n in p2._tree(db, self.cid, view_time, 480)
                            if source_id in [m.entry_id for m in n.members])
                def prepare(node):
                    for child in node.children:
                        prepare(child)
                    existing, _ = p2._digest(db, self.cid, node)
                    if node.level in ("L1", "L2", "L3", "L4") and existing is None:
                        version = p2.create_extractive_digest(db, self.cid, node.id, now=view_time)
                        self.approve(db, version["id"])
                prepare(root)
                block = p2.context_projection(db, self.cid, [source_id], now=view_time)["blocks"][0]
                outcomes.append((block["level"], block["projectionKind"], block["sourceRefs"]))
                self.assertEqual(p2.unfold_raw(db, self.cid, source_id, 0)["text"], "合成的第0天记录。")
            self.assertEqual([level for level, _, _ in outcomes], [f"L{i}" for i in range(6)])
            self.assertEqual([kind for _, kind, _ in outcomes], ["detailed_narrative"] +
                             ["period_digest"] * 4 + ["title_index"])
            self.assertTrue(all(refs == outcomes[0][2] for _, _, refs in outcomes))

    def test_summary_missing_draft_approved_and_context_same_sources(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            self.assertEqual(weekly["summaryStatus"], "missing")
            before = p2.context_projection(db, self.cid, [self.ids[7]], now=self.now)
            self.assertEqual(before["blocks"][0]["projectionKind"], "entry_synopsis")
            version = p2.create_extractive_digest(db, self.cid, weekly["nodeId"], now=self.now)
            draft = self.root(db, "L1")
            self.assertEqual(draft["summaryStatus"], "stale_or_unreviewed")
            self.assertNotIn("summaryPreview", draft)
            self.approve(db, version["id"])
            approved = self.root(db, "L1")
            self.assertEqual(approved["summaryStatus"], "available")
            context = p2.context_projection(db, self.cid, [self.ids[7]], now=self.now)
            self.assertEqual(context["blocks"][0]["projectionKind"], "period_digest")
            self.assertEqual(approved["sourceRefs"], context["blocks"][0]["sourceRefs"])
            self.assertEqual(context["blocks"][0]["digestVersionId"], version["id"])
            page = p2.expand_node(db, self.cid, weekly["nodeId"], now=self.now, limit=12)
            self.assertEqual(page["summaryPage"], version["body"][:12])
            if page["nextOffset"] is not None:
                with self.assertRaisesRegex(ValueError, "digest_page_changed"):
                    p2.expand_node(db, self.cid, weekly["nodeId"], now=self.now,
                                   offset=page["nextOffset"], limit=12)
                following = p2.expand_node(db, self.cid, weekly["nodeId"], now=self.now,
                    offset=page["nextOffset"], limit=12, expected_digest_version_id=version["id"])
                self.assertEqual(following["summaryPage"], version["body"][12:24])

    def test_existing_reviewed_model_digest_can_bind_without_generation(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            refs = weekly["sourceRefs"]
            derivation = derived.create_derivation(db, self.cid, "DIGEST", refs)
            model = derived.add_version(db, derivation["id"], refs, "合成模型摘要，仅作待审草稿。",
                author_kind="model", rule_version=f"model-digest-m35-v1:L1:{weekly['period']}")
            with self.assertRaisesRegex(ValueError, "digest_not_reviewed_or_current"):
                p2.bind_reviewed_digest(db, self.cid, weekly["nodeId"], model["id"], now=self.now)
            self.assertEqual(self.root(db, "L1")["summaryStatus"], "missing")
            self.approve(db, model["id"])
            p2.bind_reviewed_digest(db, self.cid, weekly["nodeId"], model["id"], now=self.now)
            self.assertEqual(self.root(db, "L1")["digestVersionId"], model["id"])
            with self.assertRaises(ValueError):
                p2.bind_reviewed_digest(db, self.cid + 1, weekly["nodeId"], model["id"], now=self.now)

    def test_hierarchy_requires_child_and_unfolds_exact_scope(self):
        with app_module.connect() as db:
            monthly = self.root(db, "L2")
            child_id = monthly["childNodeIds"][0]
            with self.assertRaisesRegex(ValueError, "child_digest_missing"):
                p2.create_extractive_digest(db, self.cid, monthly["nodeId"], now=self.now)
            child = self.approved_digest(db, child_id)
            parent = self.approved_digest(db, monthly["nodeId"])
            self.assertEqual([dep["child_version_id"] for dep in derived._deps(db, parent["id"])], [child["id"]])
            expanded = p2.expand_node(db, self.cid, monthly["nodeId"], now=self.now)
            self.assertEqual(expanded["summaryStatus"], "available")
            self.assertEqual(expanded["children"][0]["sourceRefs"], monthly["sourceRefs"])
            self.assertEqual(p2.unfold_raw(db, self.cid, self.ids[30], 0)["text"], "合成的第30天记录。")

    def test_title_and_synopsis_revision_do_not_invalidate_raw_digest(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            version = self.approved_digest(db, weekly["nodeId"])
            item_id = self.ids[7]
            old = entries.read_entry(db, item_id, self.cid)
            new = entries.add_version(db, item_id, old["sourceRefs"], "人工新标题", ["新词"],
                ["合成"], "人工修订的单经历梗概。", old["narrativeVersionId"], 1)
            self.approve_entry(db, new)
            again = self.root(db, "L1")
            self.assertEqual(again["summaryStatus"], "available")
            self.assertEqual(again["digestVersionId"], version["id"])
            self.assertEqual(entries.read_entry(db, item_id, self.cid)["title"], "人工新标题")

    def test_reapproved_source_change_stales_old_digest_but_keeps_new_entry(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            self.approved_digest(db, weekly["nodeId"])
            item_id = self.ids[7]
            old = entries.read_entry(db, item_id, self.cid)
            ref = old["sourceRefs"][0]
            db.execute("UPDATE messages SET content='第7天修订后的合成记录。' WHERE id=?", (ref["id"],))
            self._index(db)
            narrative = derived.create_derivation(db, self.cid, "NARRATIVE", old["sourceRefs"])
            new_narrative = derived.add_version(db, narrative["id"], old["sourceRefs"], "她说第7天修订后的记录。")
            self.approve(db, new_narrative["id"])
            new_entry = entries.add_version(db, item_id, old["sourceRefs"], old["title"],
                old["keywords"], old["tags"], "新来源对应的梗概。", new_narrative["id"], 1)
            self.approve_entry(db, new_entry)
            current = self.root(db, "L1")
            self.assertEqual(current["nodeId"], weekly["nodeId"])
            self.assertEqual(current["summaryStatus"], "stale_or_unreviewed")
            self.assertNotIn("summaryPreview", current)
            projected = p2.context_projection(db, self.cid, [item_id], now=self.now)
            self.assertEqual(projected["blocks"][0]["projectionKind"], "entry_synopsis")
            self.assertEqual(projected["blocks"][0]["text"], "新来源对应的梗概。")

    def test_child_digest_invalidation_blocks_parent_summary(self):
        with app_module.connect() as db:
            monthly = self.root(db, "L2")
            child = self.approved_digest(db, monthly["childNodeIds"][0])
            self.approved_digest(db, monthly["nodeId"])
            db.execute("UPDATE mv3_versions SET status='stale' WHERE id=?", (child["id"],))
            current = self.root(db, "L2")
            self.assertEqual(current["summaryStatus"], "child_digest_missing")
            self.assertNotIn("summaryPreview", current)
            self.assertEqual(p2.context_projection(db, self.cid, [self.ids[30]], now=self.now)["blocks"][0]["projectionKind"],
                             "entry_synopsis")

    def test_task_state_is_read_only_and_never_auto_runs(self):
        with app_module.connect() as db:
            jobs.init_db(db)
            weekly = self.root(db, "L1")
            job = jobs.enqueue(db, self.cid, "DIGEST", weekly["sourceRefs"],
                               level="L1", period=weekly["period"])
            before = db.total_changes
            view = self.root(db, "L1")
            self.assertEqual(view["taskStatus"], "queued")
            self.assertEqual(view["summaryStatus"], "missing")
            self.assertEqual(db.total_changes, before)
            db.execute("UPDATE mv3_jobs SET status='failed' WHERE id=?", (job["id"],))
            self.assertEqual(self.root(db, "L1")["taskStatus"], "failed")

    def test_source_edit_revoke_and_replaced_narrative_disappear(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            approved = self.approved_digest(db, weekly["nodeId"])
            page = p2.expand_node(db, self.cid, weekly["nodeId"], now=self.now, limit=4)
            source_id = entries.read_entry(db, self.ids[7], self.cid)["sourceRefs"][0]["id"]
            db.execute("UPDATE messages SET content='变更后的来源' WHERE id=?", (source_id,))
            with self.assertRaisesRegex(ValueError, "node_missing_or_stale|digest_page_changed"):
                p2.expand_node(db, self.cid, weekly["nodeId"], now=self.now,
                    offset=page["nextOffset"], limit=4, expected_digest_version_id=approved["id"])
            view = p2.browse(db, self.cid, now=self.now, limit=50)
            self.assertFalse(any(self.ids[7] in node["entryIds"] for node in view["nodes"]))
            self.assertEqual(p2.context_projection(db, self.cid, [self.ids[7]], now=self.now)["blocks"], [])
            with self.assertRaises(ValueError):
                p2.unfold_raw(db, self.cid, self.ids[7], 0)
            db.execute("UPDATE characters SET enabled=0 WHERE id=?", (self.cid,))
            self.assertEqual(p2.browse(db, self.cid, now=self.now)["nodes"], [])

    def test_narrative_replacement_blocks_old_entry_without_stale_text(self):
        with app_module.connect() as db:
            item_id = self.ids[0]
            old = entries.read_entry(db, item_id, self.cid)
            narrative = derived._row(db, "mv3_versions", old["narrativeVersionId"])
            replacement = derived.add_version(db, narrative["derivation_id"], old["sourceRefs"], "我说新的合成正文。")
            self.approve(db, replacement["id"])
            self.assertEqual(p2.context_projection(db, self.cid, [item_id], now=self.now)["blocks"], [])
            self.assertFalse(any(item_id in node["entryIds"] for node in p2.browse(db, self.cid, now=self.now)["nodes"]))

    def test_pagination_binds_view_timezone_filter_generation_and_snapshot(self):
        with app_module.connect() as db:
            first = p2.browse(db, self.cid, now=self.now, limit=2)
            self.assertIsNotNone(first["nextCursor"])
            second = p2.browse(db, self.cid, now=self.now, limit=2, cursor=first["nextCursor"])
            self.assertEqual(len(second["nodes"]), 2)
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.browse(db, self.cid, now=self.now + timedelta(seconds=1), limit=2,
                          cursor=first["nextCursor"])
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.browse(db, self.cid, now=self.now, offset_minutes=0, limit=2,
                          cursor=first["nextCursor"])
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.browse(db, self.cid, now=self.now, levels=["L0"], limit=2,
                          cursor=first["nextCursor"])
            source_id = entries.read_entry(db, self.ids[0], self.cid)["sourceRefs"][0]["id"]
            db.execute("UPDATE messages SET content='changed' WHERE id=?", (source_id,))
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.browse(db, self.cid, now=self.now, limit=2, cursor=first["nextCursor"])

    def test_pagination_rejects_generation_switch_and_metadata_revision(self):
        with app_module.connect() as db:
            first = p2.browse(db, self.cid, now=self.now, limit=2)
            item_id = self.ids[0]
            old = entries.read_entry(db, item_id, self.cid)
            revision = entries.add_version(db, item_id, old["sourceRefs"], "新标题", old["keywords"],
                old["tags"], old["synopsis"], old["narrativeVersionId"], 1)
            self.approve_entry(db, revision)
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.browse(db, self.cid, now=self.now, limit=2, cursor=first["nextCursor"])
            fresh = p2.browse(db, self.cid, now=self.now, limit=2)
            job = indexer.start_job(db, self.cid, "rebuild")
            for _ in range(20):
                if indexer.step_job(db, job["job_id"])["status"] == "completed":
                    break
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.browse(db, self.cid, now=self.now, limit=2, cursor=fresh["nextCursor"])

    def test_merge_finalization_removes_replaced_roots(self):
        with app_module.connect() as db:
            parents = [self.ids[0], self.ids[7]]
            refs = [entries.read_entry(db, item_id, self.cid)["sourceRefs"][0] for item_id in parents]
            narrative = self.narrative(db, refs, "她先说第0天，又说第7天。")
            children = entries.propose_restructure(db, parents, [{"sourceRefs": refs,
                "title": "合并经历", "keywords": [], "tags": [], "synopsis": "两条合成记录。",
                "narrativeVersionId": narrative["id"], "editedBy": 1}], "merge", self.cid)
            before = p2.browse(db, self.cid, now=self.now, limit=50)["nodes"]
            self.assertTrue(all(any(parent in node["entryIds"] for node in before) for parent in parents))
            draft = db.execute("SELECT * FROM mv3_entry_versions WHERE entry_id=?", (children[0],)).fetchone()
            self.approve_entry(db, dict(draft))
            entries.finalize_restructure(db, parents, children, self.cid)
            after = p2.browse(db, self.cid, now=self.now, limit=50)["nodes"]
            self.assertFalse(any(parent in node["entryIds"] for node in after for parent in parents))
            self.assertTrue(any(children[0] in node["entryIds"] for node in after))

    def test_timezone_iso_week_midnight_and_unknown_time(self):
        stamp = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
        self.assertNotEqual(p2._period("L1", stamp, 0), p2._period("L1", stamp, 480))
        self.assertEqual(p2._period("L1", stamp, 480), "2026-W45")
        with app_module.connect() as db:
            source_id = entries.read_entry(db, self.ids[0], self.cid)["sourceRefs"][0]["id"]
            db.execute("UPDATE messages SET generation_status='imported' WHERE id=?", (source_id,))
            view = p2.browse(db, self.cid, now=self.now)
            self.assertFalse(any(self.ids[0] in node["entryIds"] for node in view["nodes"]))
            self.assertIsNone(p2._timestamp("not-a-date", 480))

    def test_iso_week_crossing_month_stays_with_parent_scope(self):
        later = datetime(2026, 12, 15, 12, tzinfo=timezone.utc)
        with app_module.connect() as db:
            source_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','十一月一日的合成记录。','2026-11-01T12:00:00+00:00')""",
                (self.conv,)).lastrowid
            self._index(db)
            refs = [{"kind": "message", "id": str(source_id)}]
            narrative = self.narrative(db, refs, "她说十一月一日的合成记录。")
            item = entries.create_entry(db, self.cid)
            version = entries.add_version(db, item["id"], refs, "十一月记录", [], [],
                                          "十一月单经历梗概。", narrative["id"], 1)
            self.approve_entry(db, version)
            roots = p2._tree(db, self.cid, later, 480)
            october = next(n for n in roots if n.level == "L2" and n.period == "2026-10"
                           and self.ids[0] in [m.entry_id for m in n.members])
            november = next(n for n in roots if n.level == "L2" and n.period == "2026-11"
                            and item["id"] in [m.entry_id for m in n.members])
            oc_child = next(c for c in october.children if self.ids[0] in [m.entry_id for m in c.members])
            nv_child = next(c for c in november.children if item["id"] in [m.entry_id for m in c.members])
            self.assertEqual(oc_child.period, nv_child.period)
            self.assertNotEqual(oc_child.id, nv_child.id)
            self.assertNotIn(item["id"], [m.entry_id for m in oc_child.members])
            self.assertNotIn(self.ids[0], [m.entry_id for m in nv_child.members])

    def test_context_budget_only_selected_and_l5_title(self):
        with app_module.connect() as db:
            self.approved_digest(db, self.root(db, "L1")["nodeId"])
            result = p2.context_projection(db, self.cid, [self.ids[0], self.ids[7], self.ids[365]],
                                           now=self.now, budget_tokens=160, max_blocks=3)
            self.assertEqual([b["projectionKind"] for b in result["blocks"]],
                             ["detailed_narrative", "period_digest", "title_index"])
            self.assertLessEqual(result["estimatedTokens"], 160)
            tiny = p2.context_projection(db, self.cid, [self.ids[0]], now=self.now, budget_tokens=12)
            self.assertEqual(tiny["blocks"], [])
            self.assertEqual(tiny["rejected"][0]["reason"], "budget_exhausted")
            self.assertTrue(all(not block["eligibleForFormalRecall"] for block in result["blocks"]))

    def test_group_digest_never_adds_unselected_experience(self):
        with app_module.connect() as db:
            stamp = (self.now - timedelta(days=7, hours=1)).isoformat()
            source_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','同周另一条合成事实。',?)""", (self.conv, stamp)).lastrowid
            self._index(db)
            refs = [{"kind": "message", "id": str(source_id)}]
            narrative = self.narrative(db, refs, "她说同周另一条合成事实。")
            item = entries.create_entry(db, self.cid)
            meta = entries.add_version(db, item["id"], refs, "同周第二条", [], [],
                                       "同周第二条的单经历梗概。", narrative["id"], 1)
            self.approve_entry(db, meta)
            node = self.root(db, "L1")
            self.assertEqual(set(node["entryIds"]), {self.ids[7], item["id"]})
            self.approved_digest(db, node["nodeId"])
            one = p2.context_projection(db, self.cid, [self.ids[7]], now=self.now)
            self.assertEqual(one["blocks"][0]["projectionKind"], "entry_synopsis")
            self.assertNotIn("同周另一条合成事实", one["blocks"][0]["text"])
            both = p2.context_projection(db, self.cid, [self.ids[7], item["id"]], now=self.now)
            self.assertEqual(len(both["blocks"]), 1)
            self.assertEqual(both["blocks"][0]["projectionKind"], "period_digest")
            self.assertEqual(set(both["blocks"][0]["entryIds"]), {self.ids[7], item["id"]})
            first_page = p2.expand_node(db, self.cid, node["nodeId"], now=self.now, member_limit=1)
            self.assertEqual(len(first_page["entryIds"]), 1)
            self.assertTrue(first_page["sourceRefsIncomplete"])
            second_page = p2.expand_node(db, self.cid, node["nodeId"], now=self.now, member_limit=1,
                                         member_cursor=first_page["nextMemberCursor"])
            self.assertEqual(set(first_page["entryIds"] + second_page["entryIds"]),
                             {self.ids[7], item["id"]})
            old = entries.read_entry(db, item["id"], self.cid)
            changed = entries.add_version(db, item["id"], old["sourceRefs"], "第二条的新标题",
                old["keywords"], old["tags"], old["synopsis"], old["narrativeVersionId"], 1)
            self.approve_entry(db, changed)
            with self.assertRaisesRegex(ValueError, "cursor_view_changed"):
                p2.expand_node(db, self.cid, node["nodeId"], now=self.now, member_limit=1,
                               member_cursor=first_page["nextMemberCursor"])

    def test_undated_import_stays_undated_and_is_not_l0(self):
        with app_module.connect() as db:
            source_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at,
                generation_status) VALUES(?,'user','无发生时间的合成导入','2026-01-01','imported')""",
                (self.conv,)).lastrowid
            self._index(db)
            refs = [{"kind": "message", "id": str(source_id)}]
            narrative = self.narrative(db, refs, "她说这是一条合成导入。")
            item = entries.create_entry(db, self.cid)
            meta = entries.add_version(db, item["id"], refs, "时间未核实", [], [],
                                       "单经历梗概。", narrative["id"], 1)
            self.approve_entry(db, meta)
            node = next(n for n in p2.browse(db, self.cid, now=self.now, limit=50)["nodes"]
                        if item["id"] in n["entryIds"])
            self.assertIsNone(node["level"])
            self.assertEqual(node["timeBasis"], "unknown")
            self.assertEqual(p2.context_projection(db, self.cid, [item["id"]], now=self.now)["blocks"][0]["projectionKind"],
                             "entry_synopsis")

    def test_raw_page_hash_and_title_fallback(self):
        with app_module.connect() as db:
            item_id = self.ids[365]
            page = p2.unfold_raw(db, self.cid, item_id, 0, limit=4)
            with self.assertRaisesRegex(ValueError, "source_page_changed"):
                p2.unfold_raw(db, self.cid, item_id, 0, offset=4, expected_content_hash="0" * 64)
            block = p2.context_projection(db, self.cid, [item_id], now=self.now, budget_tokens=20)["blocks"][0]
            self.assertEqual(block["projectionKind"], "title_index")

    def test_read_paths_do_not_write_and_role_isolation(self):
        with app_module.connect() as db:
            before = db.total_changes
            p2.browse(db, self.cid, now=self.now)
            p2.context_projection(db, self.cid, [self.ids[0]], now=self.now)
            p2.unfold_raw(db, self.cid, self.ids[365], 0)
            self.assertEqual(db.total_changes, before)
            other = db.execute("""INSERT INTO characters(name,created_at,updated_at)
                VALUES('P2 other','2026-01-01','2026-01-01')""").lastrowid
            self.assertEqual(p2.browse(db, other, now=self.now)["nodes"], [])
            self.assertEqual(p2.context_projection(db, other, [self.ids[0]], now=self.now)["blocks"], [])
            with self.assertRaises(ValueError):
                p2.unfold_raw(db, other, self.ids[0], 0)


class P2API(P2Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        api.install(app, app_module.connect, app_module.current_session)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with app_module.connect() as db:
            db.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,1,'2026-01-01','2099-01-01')",
                       (app_module.session_digest("p2-token"),))
        self.client.cookies.set(app_module.SESSION_COOKIE, "p2-token")

    def test_browse_expand_raw_auth_and_no_store(self):
        base = "/api/memory-v2/chronicle"
        params = {"characterId": self.cid, "asOf": self.now.isoformat(), "limit": 2}
        response = self.client.get(base + "/tree", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        node = response.json()["nodes"][0]
        self.assertEqual(self.client.get(base + "/nodes/" + node["nodeId"], params=params).status_code, 200)
        raw = self.client.get(base + "/entries/" + self.ids[365] + "/raw/0",
                              params={"characterId": self.cid})
        self.assertEqual(raw.status_code, 200, raw.text)
        with app_module.connect() as db:
            db.execute("UPDATE messages SET content='edited' WHERE id=?", (raw.json()["sourceRef"]["id"],))
        stale = self.client.get(base + "/entries/" + self.ids[365] + "/raw/0",
                                params={"characterId": self.cid})
        self.assertEqual(stale.status_code, 409)
        self.assertNotIn("合成的第365天记录", stale.text)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(base + "/tree", params=params).status_code, 401)


if __name__ == "__main__":
    unittest.main()
