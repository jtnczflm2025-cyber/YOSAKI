import unittest
import uuid
from test_memory_v2_a3_recovery import database
from backend.app import memory_v2_processing_schema as schema, memory_v2_jobs as jobs


class TerminalCoordinator(unittest.TestCase):
    def setUp(self):
        self.db=database(); schema.migrate(self.db)
        self.db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at,cancel_intent) VALUES('job',1,'NARRATIVE','hash','{}','unknown','synthetic',1)")
        self.requests={i:str(uuid.uuid4()) for i in range(3)}
        for i,state in enumerate(('completed','prepared','prepared')):
            self.db.execute('''INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,
                reserved_tokens,reserved_cost_units,dispatch_attempt_id) VALUES('job',?,?,?,?,?,100,100,?)''',
                (i,self.requests[i],'hash','{}',state,None))
        self.proofs={i:jobs.persist_dispatch_identity(self.db,'job',1,i) for i in (1,2)}
        self.db.commit()

    def tearDown(self): self.db.close()

    def settle(self,i):
        return jobs.settle_dispatch(self.db,'job',1,i,self.requests[i],self.proofs[i],
            state='completed',used_input=10,used_output=20,used_cost=30,result_hash='synthetic-output')

    def test_partial_reconciliation_does_not_override_unknown(self):
        self.db.execute("UPDATE mv3_model_chunks SET state='unknown' WHERE ordinal IN (1,2)")
        self.assertEqual(self.settle(1),'unknown')
        self.assertEqual(jobs.recompute_parent(self.db,'job',1,output_ready=True),'unknown')
        self.assertEqual(self.settle(2),'cancelled')
        self.assertFalse(jobs.business_result_allowed(self.db,'job',1))
        self.assertEqual(jobs.recompute_parent(self.db,'job',1,output_ready=True),'cancelled')

    def test_completed_unknown_prepared_and_replay(self):
        self.db.execute("UPDATE mv3_model_chunks SET state='prepared',dispatch_attempt_id=NULL WHERE ordinal=2")
        self.db.execute("UPDATE mv3_model_chunks SET state='unknown' WHERE ordinal=1")
        self.assertEqual(self.settle(1),'cancelled')
        before=[tuple(r) for r in self.db.execute('SELECT * FROM mv3_model_chunks ORDER BY ordinal')]
        self.assertEqual(self.settle(1),'cancelled')
        self.assertEqual(before,[tuple(r) for r in self.db.execute('SELECT * FROM mv3_model_chunks ORDER BY ordinal')])
        self.assertEqual(before[2][5],'prepared')
        with self.assertRaisesRegex(ValueError,'conflicting_settlement'):
            jobs.settle_dispatch(self.db,'job',1,1,self.requests[1],self.proofs[1],state='completed',
                used_input=11,used_output=20,used_cost=31,result_hash='synthetic-output')
        with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
            jobs.settle_dispatch(self.db,'job',1,1,self.requests[1],'another-worker',state='completed',
                used_input=10,used_output=20,used_cost=30,result_hash='synthetic-output')

    def test_legacy_paused_without_dispatch_evidence_stays_blocked(self):
        db=database()
        try:
            # Historical paused alone is not proof that no request was sent.
            db.execute('''INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at)
                VALUES('legacy',1,'NARRATIVE','old','{"model_mode":true,"refs":[]}', 'paused','synthetic')''')
            db.commit()
            schema.migrate(db)
            self.assertEqual(db.execute("SELECT status FROM mv3_jobs WHERE id='legacy'").fetchone()[0],'unknown')
            with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                jobs.control(db,'legacy',1,'resume')
        finally: db.close()
