import sqlite3
import unittest
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_processing as p
from backend.app import memory_v2_processing_schema as schema


def database():
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL)')
    db.execute('CREATE TABLE mv3_versions(id TEXT PRIMARY KEY)')
    db.execute('INSERT INTO characters VALUES(1,1)')
    jobs.init_db(db)
    db.commit()
    return db


class A3Recovery(unittest.TestCase):
    def setUp(self):
        self.db=database()
        schema.migrate(self.db)

    def tearDown(self):
        self.db.close()

    def make(self,fields=('title',),version='v1'):
        jid=p.create(self.db,1,'entry','synthetic-entry',fields,{'sources':['s1']},
                     rule_version=version,input_version='i1')
        self.db.commit()
        return jid

    def test_three_batches_unknown_cancel_and_reconcile(self):
        jid=self.make()
        p.prepare_chunks(self.db,jid,1,[{'reservedTokens':10,'reservedCostUnits':5} for _ in range(3)])
        request=p.dispatch(self.db,jid,1,0)
        p.mark_unknown(self.db,jid,1,0,request['requestId'],dispatch_proof=request['dispatchProof'])
        self.db.execute("UPDATE mv3_model_chunks SET state='completed' WHERE job_id=? AND ordinal=0",(jid,))
        # Two unsettled children can result from recovered legacy or concurrent in-flight work.
        proofs={i:jobs.persist_dispatch_identity(self.db,jid,1,i) for i in (1,2)}
        self.db.execute("UPDATE mv3_model_chunks SET state='unknown' WHERE job_id=? AND ordinal=1",(jid,))
        self.db.execute("UPDATE mv3_model_chunks SET state='unknown' WHERE job_id=? AND ordinal=2",(jid,))
        self.assertEqual(p.cancel(self.db,jid,1),'unknown')
        self.assertEqual(self.db.execute('SELECT status,cancel_intent FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[:],('unknown',1))
        for ordinal in (1,2):
            row=self.db.execute('SELECT request_id FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',(jid,ordinal)).fetchone()
            state=p.reconcile(self.db,jid,1,ordinal,row['request_id'],dispatch_proof=proofs[ordinal],
                evidence={'kind':'mock_completed','requestId':row['request_id']},
                used_input_tokens=2,used_output_tokens=3,current_read_set={'sources':['s1']})
            self.assertEqual(state,'unknown' if ordinal==1 else 'cancelled')
        self.assertEqual(self.db.execute('SELECT reserved_cost_units FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(jid,)).fetchone()[0],5)

    def test_scope_overlap_versions_and_scheduler(self):
        jid=self.make(('title','synopsis'))
        p.prepare_chunks(self.db,jid,1,[{'reservedTokens':10}])
        p.dispatch(self.db,jid,1,0)
        for origin in ('manual','scheduler'):
            with self.assertRaisesRegex(ValueError,'semantic_scope_unknown'):
                p.create(self.db,1,'entry','synthetic-entry',['title'],{'sources':['s2']},
                         rule_version='v2',input_version='i2',origin=origin)
        # Approved recovery contract: unresolved remote work has a specific reason.
        with self.assertRaisesRegex(ValueError,'model_result_unknown'):
            p.resume(self.db,jid,1,{'sources':['s1']})
        self.assertEqual(self.db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0],'unknown')

    def test_late_result_cannot_publish_changed_input(self):
        jid=self.make()
        p.prepare_chunks(self.db,jid,1,[{'reservedTokens':10}])
        r=p.dispatch(self.db,jid,1,0)
        self.assertEqual(p.reconcile(self.db,jid,1,0,r['requestId'],dispatch_proof=r['dispatchProof'],
            evidence={'kind':'mock_completed','requestId':r['requestId']},
            used_input_tokens=2,used_output_tokens=2,current_read_set={'sources':['changed']}),'failed')
        self.assertIsNone(self.db.execute('SELECT result_version_id FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0])

    def test_legacy_running_migrates_unknown_and_blocks(self):
        db=database()
        try:
            db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES('old',1,'NARRATIVE','h','{}','running','now')")
            db.commit()
            self.assertTrue(schema.migrate(db))
            self.assertEqual(db.execute("SELECT status FROM mv3_jobs WHERE id='old'").fetchone()[0],'unknown')
            with self.assertRaisesRegex(ValueError,'legacy_unknown_scope'):
                p.create(db,1,'entry','new',['title'],{'sources':['s']},rule_version='v1',input_version='i1')
            db.rollback()
            self.assertFalse(schema.migrate(db))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:
            db.close()
