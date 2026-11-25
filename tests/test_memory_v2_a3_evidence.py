import sqlite3
import unittest
from unittest.mock import patch
from test_memory_v2_a3_recovery import database
from backend.app import memory_v2_jobs as jobs, memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as schema


class MigrationEvidence(unittest.TestCase):
    def test_legacy_missing_attempt_cannot_settle_or_release_reservation(self):
        db=database()
        try:
            db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES('old',1,'NARRATIVE','h','{\"model_mode\":true}','paused','synthetic')")
            db.execute("INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens,reserved_cost_units) VALUES('old',0,'legacy-request','h','{}','unknown',100,100)")
            db.commit(); schema.migrate(db)
            before=tuple(db.execute('SELECT state,reserved_cost_units,used_cost_units FROM mv3_model_chunks').fetchone())
            self.assertIsNone(db.execute('SELECT dispatch_attempt_id FROM mv3_model_chunks').fetchone()[0])
            with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                jobs.settle_dispatch(db,'old',1,0,'legacy-request',None,
                    state='completed',used_input=10,used_output=10,used_cost=20,result_hash='unproven')
            self.assertEqual(before,tuple(db.execute('SELECT state,reserved_cost_units,used_cost_units FROM mv3_model_chunks').fetchone()))
            self.assertEqual(jobs.recompute_parent(db,'old',1),'unknown')
        finally: db.close()

    def test_hold_survives_recompute_cancel_and_new_task(self):
        db=database()
        try:
            db.execute('''INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at)
                VALUES('old',1,'NARRATIVE','h','{"model_mode":true}', 'paused','synthetic')''')
            db.commit(); schema.migrate(db)
            self.assertEqual(jobs.recompute_parent(db,'old',1,output_ready=True),'unknown')
            self.assertEqual(jobs.control(db,'old',1,'cancel'),'unknown')
            for origin in ('manual','scheduler'):
                with self.assertRaisesRegex(ValueError,'legacy_unknown_scope'):
                    processing.create(db,1,'entry','e',['title'],{'s':1},rule_version='v2',input_version='v2',origin=origin)
            self.assertEqual(db.execute('SELECT prior_status FROM mv3_a3_migration_holds').fetchone()[0],'paused')
            db.commit()
            before=db.execute('SELECT COUNT(*) FROM mv3_a3_audit').fetchone()[0]
            self.assertFalse(schema.migrate(db))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_a3_audit').fetchone()[0],before)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally: db.close()

    def test_failed_migration_rolls_back_rows_schema_and_budget(self):
        db=database()
        try:
            db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES('old',1,'NARRATIVE','h','{}','running','synthetic')")
            db.execute("INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens,reserved_cost_units) VALUES('old',0,'r','h','{}','unknown',100,100)")
            db.commit()
            before=[tuple(r) for r in db.execute('SELECT * FROM mv3_jobs')]
            db.set_authorizer(lambda action,a,b,c,d: sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_DROP_TABLE else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError): schema.migrate(db)
            db.set_authorizer(None)
            self.assertFalse(schema.installed(db))
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM mv3_jobs')])
            self.assertEqual(db.execute('SELECT reserved_tokens,reserved_cost_units FROM mv3_model_chunks').fetchone()[:],(100,100))
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
        finally: db.close()


class PublicationTransaction(unittest.TestCase):
    def setUp(self):
        import test_memory_v2_metadata as f
        self.f=f
        self.case=f.ProgressiveMetadata('test_title_only_order_and_idempotence'); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        from backend.app import memory_v2_metadata_jobs as work
        self.work=work
        with f.app_module.connect() as db:
            work.init_db(db); self.draft=self.case.draft(db)
        with f.app_module.connect() as db: schema.migrate(db)
        with f.app_module.connect() as db:
            self.job=work.enqueue(db,self.draft['entryId'],self.case.cid,1,'SYNOPSIS',mock_model=True)

    def test_write_lock_spans_cancel_source_check_and_field_write(self):
        original=self.f.meta.revise
        observed=[]
        def checked(db,*args,**kwargs):
            self.assertTrue(db.in_transaction)
            other=sqlite3.connect(str(self.f.app_module.DB),timeout=0)
            try:
                for sql,params in [
                    ('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(self.job['id'],)),
                    ("UPDATE messages SET content='racing edit' WHERE id=?",(self.case.user,)),
                    ("UPDATE mv3_entries SET draft_version_id=NULL WHERE id=?",(self.draft['entryId'],))]:
                    with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                        other.execute(sql,params)
                    other.rollback(); observed.append(sql)
            finally: other.close()
            return original(db,*args,**kwargs)
        with patch.object(self.f.meta,'revise',checked):
            self.work.dispatch_mock(self.f.app_module.connect,self.job['id'],self.case.cid,
                self.work.MockMetadataAdapter({'synopsis':'合成内容'}))
        self.assertEqual(len(observed),3)

    def test_field_error_keeps_settlement_and_no_revision(self):
        with patch.object(self.f.meta,'revise',side_effect=sqlite3.OperationalError('synthetic field write failure')):
            result=self.work.dispatch_mock(self.f.app_module.connect,self.job['id'],self.case.cid,
                self.work.MockMetadataAdapter({'synopsis':'合成内容'}))
        self.assertEqual(result['status'],'rejected')
        with self.f.app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_input_tokens,used_output_tokens FROM mv3_model_chunks WHERE job_id=?',(self.job['id'],)).fetchone()[:],(20,20))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(self.job['id'],)).fetchone()[0],'failed')

    def test_crash_before_field_write_rolls_back_settlement_not_dispatch(self):
        class Crash(BaseException): pass
        with patch.object(jobs,'business_result_allowed',side_effect=Crash):
            with self.assertRaises(Crash):
                self.work.dispatch_mock(self.f.app_module.connect,self.job['id'],self.case.cid,
                    self.work.MockMetadataAdapter({'synopsis':'合成内容'}))
        with self.f.app_module.connect() as db:
            row=db.execute('SELECT state,reserved_tokens,used_input_tokens FROM mv3_model_chunks WHERE job_id=?',(self.job['id'],)).fetchone()
            self.assertEqual(row[:],('possibly_dispatched',4096,None))
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(self.job['id'],)).fetchone()[0],'unknown')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results').fetchone()[0],0)
            with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                jobs.control(db,self.job['id'],self.case.cid,'resume')
