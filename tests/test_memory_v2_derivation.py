"""Synthetic M3 acceptance. Set a temporary data directory before importing main."""

import os
import tempfile
import unittest
from pathlib import Path


_import_data = tempfile.TemporaryDirectory(prefix="yosaki-mv3-import-")
os.environ["YOSAKI_DATA_DIR"] = _import_data.name
from backend.app import main as app_module  # noqa: E402
from backend.app import memory_v2_index as indexer  # noqa: E402
from backend.app import memory_v2_derivation_schema as schema  # noqa: E402
from backend.app import memory_v2_derivation as derived  # noqa: E402


class M3Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-mv3-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        with app_module.connect() as db:
            schema.init_db(db)
            self.cid = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]
            self.conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01','2026-01-01')""", (self.cid,)).lastrowid
            self.user = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','今天累了。','2026-01-01')""", (self.conv,)).lastrowid
            self.assistant = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'assistant','那就休息。','2026-01-01')""", (self.conv,)).lastrowid
            self._index(db)

    def _index(self, db):
        job = indexer.start_job(db, self.cid, "rebuild" if db.execute(
            "SELECT state FROM mv2_meta WHERE character_id=?", (self.cid,)).fetchone()[0] != "ready" else "incremental")
        for _ in range(20):
            result = indexer.step_job(db, job["job_id"])
            if result["status"] == "completed":
                return
        self.fail("M1 index did not complete")

    def approve(self, db, version_id):
        uid = db.execute("INSERT OR IGNORE INTO users(id,username,password_hash,created_at) VALUES(1,'synthetic-owner','not-a-password','2026-01-01')")
        v=derived._row(db,"mv3_versions",version_id)
        derived.review_version(db,version_id,self.cid,1,v["output_hash"],v["input_hash"])
        return derived.activate_version(db,version_id,self.cid)

    def refs(self):
        return [{"kind": "message", "id": str(self.user)},
                {"kind": "message", "id": str(self.assistant)}]


class M31Lifecycle(M3Base):
    def test_schema_idempotence_and_role_cascade(self):
        with app_module.connect() as db:
            schema.init_db(db)
            item = derived.create_derivation(db, self.cid, "CLEANED", self.refs())
            version = derived.add_version(db, item["id"], self.refs(), "她说今天累了。我说那就休息。")
            self.assertEqual(version["status"], "needs_review")
            self.assertIsNone(item["active_version"])
            db.execute("DELETE FROM characters WHERE id=?", (self.cid,))
            self.assertIsNone(derived._row(db, "mv3_derivations", item["id"]))

    def test_order_hash_machine_idempotence_and_manual_history(self):
        with app_module.connect() as db:
            item = derived.create_derivation(db, self.cid, "CLEANED", self.refs())
            first = derived.add_version(db, item["id"], self.refs(), "今天累了。那就休息。",
                                        author_kind="rule", rule_version="clean-v1")
            repeat = derived.add_version(db, item["id"], self.refs(), "今天累了。那就休息。",
                                         author_kind="rule", rule_version="clean-v1")
            self.assertEqual(first["id"], repeat["id"])
            reverse = derived.add_version(db, item["id"], list(reversed(self.refs())),
                                          "那就休息。今天累了。", author_kind="rule", rule_version="clean-v1")
            self.assertNotEqual(first["input_hash"], reverse["input_hash"])
            self.assertEqual(derived.create_derivation(db, self.cid, "CLEANED", list(reversed(self.refs())))["id"], item["id"])
            user_a = derived.add_version(db, item["id"], self.refs(), "人工修订 A")
            user_b = derived.add_version(db, item["id"], self.refs(), "人工修订 B")
            self.assertNotEqual(user_a["id"], user_b["id"])
            self.assertEqual(user_a["author_kind"], "user")

    def test_live_validation_dependency_and_rebuild(self):
        with app_module.connect() as db:
            cleaned = derived.create_derivation(db, self.cid, "CLEANED", self.refs())
            clean_version = derived.add_version(db, cleaned["id"], self.refs(),
                "她说今天累了。我说那就休息。", author_kind="rule")
            self.approve(db, clean_version["id"])
            narrative = derived.create_derivation(db, self.cid, "NARRATIVE", self.refs())
            narrative_version = derived.add_version(db, narrative["id"], self.refs(),
                "她说今天累了。我说那就休息。", author_kind="model",
                child_version_ids=(clean_version["id"],))
            self.approve(db, narrative_version["id"])
            self.assertEqual(derived.read_version(db, narrative_version["id"], self.cid)["body"],
                             "她说今天累了。我说那就休息。")
            self._index(db)  # A fresh M1 scan leaves stable original source IDs valid.
            self.assertIsNone(derived.validity(db, narrative_version["id"], self.cid))
            db.execute("UPDATE messages SET content='修订内容' WHERE id=?", (self.user,))
            self.assertEqual(db.execute("SELECT content FROM messages WHERE id=?", (self.user,)).fetchone()[0], "修订内容")
            with self.assertRaisesRegex(ValueError, "source_missing_or_stale"):
                derived.read_version(db, narrative_version["id"], self.cid)
            self.assertEqual(derived.invalidate_stale(db, self.cid), 2)
            self.assertIsNone(derived._row(db, "mv3_derivations", narrative["id"])["active_version"])

    def test_deleted_source_cross_role_and_dependency_rejection(self):
        with app_module.connect() as db:
            item = derived.create_derivation(db, self.cid, "CLEANED", self.refs())
            v = derived.add_version(db, item["id"], self.refs(), "合成清理", author_kind="rule")
            self.approve(db, v["id"])
            other = db.execute("""INSERT INTO characters(name,created_at,updated_at)
                VALUES('other synthetic','2026-01-01','2026-01-01')""").lastrowid
            with self.assertRaises(ValueError):
                derived.read_version(db, v["id"], other)
            with self.assertRaises(ValueError):
                derived.create_derivation(db, other, "CLEANED", self.refs())
            with self.assertRaisesRegex(ValueError, "invalid_dependency"):
                derived.add_version(db, item["id"], self.refs(), "不可自依赖", child_version_ids=(v["id"],))
            db.execute("DELETE FROM messages WHERE id=?", (self.user,))
            with self.assertRaises(ValueError):
                derived.read_version(db, v["id"], self.cid)


if __name__ == "__main__":
    unittest.main()
