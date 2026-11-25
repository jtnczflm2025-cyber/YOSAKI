import unittest
from test_memory_v2_a3_recovery import database
from backend.app import memory_v2_processing as p
from backend.app import memory_v2_processing_schema as s


class A3Isolation(unittest.TestCase):
    def test_migration_is_explicit_and_auth_precedes_listing(self):
        db=database()
        try:
            self.assertFalse(s.installed(db))
            with self.assertRaisesRegex(ValueError,'a3_migration_required'):
                p.create(db,1,'entry','e',['title'],{'s':1},rule_version='v',input_version='i')
            s.migrate(db)
            db.execute('UPDATE characters SET enabled=0 WHERE id=1')
            with self.assertRaisesRegex(ValueError,'character_not_accessible'):
                p.create(db,1,'entry','e',['title'],{'s':1},rule_version='v',input_version='i')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:
            db.close()

    def test_transaction_rollback_keeps_parent_and_child_consistent(self):
        db=database()
        try:
            s.migrate(db)
            jid=p.create(db,1,'entry','e',['title'],{'s':1},rule_version='v',input_version='i')
            p.prepare_chunks(db,jid,1,[{'reservedTokens':10}])
            db.commit()
            p.dispatch(db,jid,1,0)
            db.rollback()
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0],'queued')
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],'prepared')
        finally:
            db.close()
