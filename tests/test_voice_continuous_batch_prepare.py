"""Explicit isolated preparation tests; never opens the daily database."""

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_voice_continuous_batch import prepare
from scripts.yosaki_runtime import _continuous_batch_ready


class PrepareContinuousBatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-batch-prep-")
        self.path = Path(self.temp.name) / "yosaki.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER,
                    default_model_id INTEGER);
                CREATE TABLE model_profiles(id INTEGER PRIMARY KEY,enabled INTEGER,
                    protocol TEXT);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER,
                    title TEXT,model_profile_id INTEGER,created_at TEXT,updated_at TEXT);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER,
                    role TEXT);
                INSERT INTO users VALUES(1);
                INSERT INTO characters VALUES(1,1,5);
                INSERT INTO model_profiles VALUES(5,1,'chatgpt_plan');
                INSERT INTO conversations VALUES(7,1,'old',5,'x','x');
                INSERT INTO messages VALUES(1,7,'user'),(2,7,'assistant');""")

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_transaction_creates_only_receipt_and_empty_target(self):
        batch = "a" * 32
        self.assertFalse(prepare(self.path, "isolated", batch, apply=False)
                         ["batch_prepared"])
        result = prepare(self.path, "isolated", batch, apply=True)
        self.assertTrue(result["schema_created"])
        with closing(sqlite3.connect(self.path)) as preflight_db:
            with patch.dict("os.environ", {"YOSAKI_VOICE_CONTINUOUS_BATCH_ID": batch}):
                self.assertTrue(_continuous_batch_ready(preflight_db))
        with closing(sqlite3.connect(self.path)) as db, db:
            target = result["conversation_id"]
            self.assertNotEqual(target, 7)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=7")
                             .fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                                        (target,)).fetchone()[0], 0)
            self.assertEqual(db.execute("""SELECT conversation_id
                FROM voice_continuous_batch_receipts WHERE batch_id=?""",
                (batch,)).fetchone()[0], target)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaisesRegex(ValueError, "already_prepared"):
            prepare(self.path, "isolated", batch, apply=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 2)

    def test_bad_identity_refuses_before_any_table_creation(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE model_profiles SET enabled=0 WHERE id=5")
        with self.assertRaisesRegex(ValueError, "binding_invalid"):
            prepare(self.path, "isolated", "b" * 32, apply=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertFalse(db.execute("""SELECT 1 FROM sqlite_master
                WHERE name='voice_continuous_batch_receipts'""").fetchone())


if __name__ == "__main__":
    unittest.main()
