"""H3.2 synthetic-only boundary confirmation; never opens the retained H5-B database."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_bootstrap = tempfile.TemporaryDirectory(prefix="yosaki-h32-bootstrap-")
os.environ["YOSAKI_DATA_DIR"] = _bootstrap.name

from backend.app import main as app_module  # noqa: E402
from backend.app import memory_v2_import as importer  # noqa: E402
from backend.app import memory_v2_import_schema as import_schema  # noqa: E402
from backend.app import memory_v2_index as indexer  # noqa: E402
from backend.app import memory_v2_derivation_schema as derivation_schema  # noqa: E402
from backend.app import memory_v2_metadata as metadata  # noqa: E402


RANGES = [(1, 4), (5, 10), (11, 16), (17, 26), (27, 42), (43, 50),
          (51, 74), (75, 82), (83, 90), (91, 98), (99, 110),
          (111, 120), (121, 130)]


class H32Contract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-h32-")
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(path)
        app_module.DATA = path
        app_module.DB = path / "yosaki.sqlite3"
        app_module.SECRET_FILE = path / ".secret_key"
        app_module.init_db()
        with app_module.connect() as db:
            import_schema.init_db(db)
            derivation_schema.init_db(db)
            metadata.init_db(db)
            self.cid = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]
            self.owner = db.execute("SELECT id FROM users LIMIT 1").fetchone()
            if self.owner is None:
                self.owner = db.execute("INSERT INTO users(username,password_hash,created_at) VALUES('owner','synthetic','2026-01-01')").lastrowid
            else:
                self.owner = self.owner[0]
            db.commit()
            body = json.dumps({"format": "yosaki-conversation", "version": 2,
                "conversation": {"id": "synthetic-h32"},
                "messages": [{"role": "user" if i % 2 == 0 else "assistant",
                              "content": f"合成消息{i+1}", "created_at": "2026-07-20 19:38"}
                             for i in range(130)]}, ensure_ascii=False).encode()
            self.batch = importer.prepare(db, self.cid, [("synthetic.json", body)])["batchId"]
            importer.confirm(db, self.batch, self.cid)
            importer.step(db, self.batch, self.cid)
            importer.step(db, self.batch, self.cid)
            self.file = db.execute("SELECT * FROM mv2_import_files WHERE batch_id=?", (self.batch,)).fetchone()
            job = indexer.start_job(db, self.cid, "rebuild")
            for _ in range(1000):
                job = indexer.step_job(db, job["job_id"])
                if job["status"] != "running":
                    break
            self.assertEqual(job["status"], "completed")
            db.commit()

    def arguments(self, db):
        _, _, snapshot = importer.confirmed_file_snapshot(
            db, self.batch, self.cid, self.file["id"],
            self.file["file_sha256"], self.file["parser_version"])
        return dict(expected_sha=self.file["file_sha256"], expected_parser=self.file["parser_version"],
                    expected_snapshot=snapshot, h31_snapshot_hash="a"*64,
                    editor_id=self.owner, confirmation_origin="synthetic_owner_instruction")

    def segments(self):
        return [dict(start=start, end=end, title=f"合成建议标题{i}")
                for i, (start, end) in enumerate(RANGES, 1)]

    def test_confirm_once_and_reuse_exact_uuids_without_review(self):
        with app_module.connect() as db:
            args = self.arguments(db)
            first = importer.confirm_h32_drafts(db, self.batch, self.cid,
                                                self.file["id"], self.segments(), **args)
            second = importer.confirm_h32_drafts(db, self.batch, self.cid,
                                                 self.file["id"], self.segments(), **args)
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual([x["entryId"] for x in first["items"]],
                             [x["entryId"] for x in second["items"]])
            self.assertEqual(len(set(x["entryId"] for x in first["items"])), 13)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entries").fetchone()[0], 13)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_field_checks").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_versions").fetchone()[0], 0)
            seen = []
            for item in first["items"]:
                detail = metadata.read_draft(db, item["entryId"], self.cid)
                self.assertEqual(detail["fields"]["title"]["reviewStatus"], "needs_review")
                self.assertFalse(detail["fields"]["title"]["locked"])
                self.assertEqual(detail["fields"]["title"]["completionStatus"], "candidate")
                self.assertTrue(all(detail["fields"][name]["completionStatus"] == "missing"
                                    for name in ("categories", "tags", "keywords", "synopsis", "narrative")))
                self.assertFalse(detail["ageEligible"])
                self.assertFalse(detail["qualifications"]["candidateRecall"])
                self.assertFalse(detail["qualifications"]["formalContext"])
                seen.extend(int(ref["source_id"]) for ref in detail["sourceRefs"])
            self.assertEqual(len(seen), 130)
            self.assertEqual(len(set(seen)), 130)

    def test_conflicts_and_changed_source_leave_no_partial_entries(self):
        with app_module.connect() as db:
            args = self.arguments(db)
            invalid = self.segments()
            invalid[1]["start"] = 7
            with self.assertRaisesRegex(ValueError, "h32_boundary_invalid"):
                importer.confirm_h32_drafts(db, self.batch, self.cid, self.file["id"], invalid, **args)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entries").fetchone()[0], 0)
            first = importer.confirm_h32_drafts(db, self.batch, self.cid,
                                                self.file["id"], self.segments(), **args)
            changed = self.segments()
            changed[0]["title"] = "另一标题"
            with self.assertRaisesRegex(ValueError, "h32_confirmation_conflict"):
                importer.confirm_h32_drafts(db, self.batch, self.cid, self.file["id"], changed, **args)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entries").fetchone()[0], 13)
            self.assertEqual(len(first["items"]), 13)
            db.execute("UPDATE mv2_import_receipts SET original_time_text='altered' WHERE ordinal=1")
            db.commit()
            with self.assertRaisesRegex(ValueError, "h32_source_missing_or_stale"):
                importer.confirm_h32_drafts(db, self.batch, self.cid, self.file["id"], self.segments(), **args)

    def test_mid_creation_failure_rolls_back_confirmation_and_all_drafts(self):
        with app_module.connect() as db:
            args = self.arguments(db)
            original_segments = [tuple(row) for row in db.execute(
                "SELECT id,ordinal,start_ordinal,end_ordinal,title,entry_id "
                "FROM mv2_import_segments WHERE batch_id=? ORDER BY ordinal", (self.batch,))]
            original_version = db.execute(
                "SELECT plan_version FROM mv2_import_batches WHERE id=?", (self.batch,)).fetchone()[0]
            real_create = metadata.create_draft
            calls = 0

            def fail_on_second(*call_args, **call_kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("synthetic_mid_transaction")
                return real_create(*call_args, **call_kwargs)

            with patch.object(metadata, "create_draft", side_effect=fail_on_second):
                with self.assertRaisesRegex(RuntimeError, "synthetic_mid_transaction"):
                    importer.confirm_h32_drafts(
                        db, self.batch, self.cid, self.file["id"], self.segments(), **args)

            self.assertEqual(calls, 2)
            for table in ("mv3_entries", "mv3_entry_field_sets", "mv3_metadata_idempotency",
                          "mv2_import_boundary_confirmations"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual([tuple(row) for row in db.execute(
                "SELECT id,ordinal,start_ordinal,end_ordinal,title,entry_id "
                "FROM mv2_import_segments WHERE batch_id=? ORDER BY ordinal", (self.batch,))],
                original_segments)
            self.assertEqual(db.execute(
                "SELECT plan_version FROM mv2_import_batches WHERE id=?", (self.batch,)).fetchone()[0],
                original_version)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
