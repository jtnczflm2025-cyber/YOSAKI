"""Explicit A3.6 schema migration on synthetic SQLite only."""
import sqlite3
import unittest

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_time_projection as p2
from backend.app import memory_v2_temporal_digest_schema as temporal_schema


class TemporalMigration(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            jobs.init_db(db)
            p2.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)

    def test_normal_initialization_does_not_install_new_binding(self):
        with app_module.connect() as db:
            self.assertFalse(temporal_schema.installed(db))
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                         (temporal_schema.TABLE,)).fetchone())

    def test_explicit_migration_is_atomic_idempotent_and_keeps_history(self):
        with app_module.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0]
            self.assertTrue(temporal_schema.migrate(db))
            self.assertFalse(temporal_schema.migrate(db))
            self.assertTrue(temporal_schema.installed(db))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],
                             before)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())

    def test_partial_install_refuses_without_creating_marker(self):
        with app_module.connect() as db:
            db.execute('CREATE TABLE mv3_time_digest_bindings_v2 (id TEXT PRIMARY KEY)')
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError, 'a36_schema_invalid'):
                temporal_schema.migrate(db)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                         (temporal_schema.INDEX,)).fetchone())

    def test_transactional_failure_rolls_back_table_and_index(self):
        with app_module.connect() as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("INSERT INTO mv3_source_refs(version_id,ordinal,source_kind,source_id,"
                       "character_id,source_role,evidence_kind,content_hash,state_hash)"
                       " VALUES('synthetic-broken',0,'message','x',?,'user','raw','x','x')",
                       (self.cid,))
            db.execute("PRAGMA foreign_keys=ON")
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError, 'a36_migration_foreign_key_error'):
                temporal_schema.migrate(db)
            self.assertFalse(temporal_schema.installed(db))
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                         (temporal_schema.TABLE,)).fetchone())


if __name__ == '__main__':
    unittest.main()
