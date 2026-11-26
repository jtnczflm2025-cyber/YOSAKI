"""Exact Scribe settings capacity upgrade without resetting daily usage."""

from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.app.voice import scribe_budget
from scripts import migrate_scribe_daily_capacity, migrate_scribe_daily_capacity_19
from scripts.yosaki_runtime import _scribe_daily_schema_matches


class ScribeCapacity19Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-scribe-19-")
        self.path = Path(self.temp.name) / "isolated.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, old: tuple[int, int, int]):
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO users VALUES(1)")
            scribe_budget.init_db(db)
            current = db.execute("""SELECT sql FROM sqlite_master
                WHERE name='voice_scribe_owner_settings'""").fetchone()[0]
            db.execute("DROP TABLE voice_scribe_owner_settings")
            for name, before, after in (
                ("attempts", 19, old[0]),
                ("connections", 19, old[1]),
                ("audio_seconds", 285, old[2]),
            ):
                current = current.replace(f"{name} BETWEEN 0 AND {before}",
                                          f"{name} BETWEEN 0 AND {after}")
            db.execute(current)
            db.execute("""INSERT INTO voice_scribe_owner_settings
                VALUES (1,1,18,18,270,4,'old','old')""" if old[0] == 18 else
                """INSERT INTO voice_scribe_owner_settings
                VALUES (1,1,10,10,150,3,'old','old')""")
            db.execute("""INSERT INTO voice_scribe_owner_day_budget
                VALUES (1,'2026-11-16',17,17,8160000,3376372,'old','old')""")
            db.commit()
        finally:
            db.close()

    def test_upgrade_preserves_usage_and_uses_settings_confirmation(self):
        self.prepare((18, 18, 270))
        result = migrate_scribe_daily_capacity_19.migrate(
            self.path, "isolated", apply=True)
        self.assertTrue(result["applied"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertTrue(_scribe_daily_schema_matches(db))
            self.assertEqual(db.execute("""SELECT attempts,connections,
                reserved_audio_bytes,actual_audio_bytes
                FROM voice_scribe_owner_day_budget""").fetchone(),
                (17, 17, 8160000, 3376372))
            self.assertEqual(db.execute("SELECT attempts,connections,audio_seconds,revision "
                                        "FROM voice_scribe_owner_settings").fetchone(),
                             (18, 18, 270, 4))
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

        @contextmanager
        def connect():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        with self.assertRaisesRegex(ValueError, "increase_confirmation_required"):
            scribe_budget.put_settings(connect, 1, True,
                scribe_budget.Limits(19, 19, 285), 4, False, False)
        saved = scribe_budget.put_settings(connect, 1, True,
            scribe_budget.Limits(19, 19, 285), 4, True, False)
        self.assertEqual(saved["limits"], {"attempts": 19, "connections": 19,
                                           "audio_seconds": 285})
        self.assertEqual(saved["revision"], 5)
        with connect() as db:
            self.assertEqual(tuple(db.execute("SELECT attempts,connections FROM "
                "voice_scribe_owner_day_budget").fetchone()), (17, 17))

    def test_historical_upgrade_still_stops_at_18(self):
        self.prepare((10, 10, 150))
        result = migrate_scribe_daily_capacity.migrate(self.path, "isolated", apply=True)
        self.assertTrue(result["applied"])
        with closing(sqlite3.connect(self.path)) as db:
            sql = db.execute("""SELECT sql FROM sqlite_master
                WHERE name='voice_scribe_owner_settings'""").fetchone()[0]
            self.assertIn("attempts BETWEEN 0 AND 18", sql)
            self.assertNotIn("attempts BETWEEN 0 AND 19", sql)
