import unittest
import test_memory_v2_metadata as fixture
from backend.app import memory_v2_jobs as jobs, memory_v2_metadata_jobs as work
from backend.app import memory_v2_processing_schema as schema


class Continuation(unittest.TestCase):
    def setUp(self):
        self.case=fixture.ProgressiveMetadata('test_mock_output_needs_review_and_unknown_never_retries')
        self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def test_existing_unknown_error_contract(self):
        self.case.test_mock_output_needs_review_and_unknown_never_retries()

    def test_migrated_cancel_during_adapter_preserves_intent(self):
        with fixture.app_module.connect() as db:
            work.init_db(db)
            draft=self.case.draft(db)
        with fixture.app_module.connect() as db:
            schema.migrate(db)
        with fixture.app_module.connect() as db:
            job=work.enqueue(db,draft['entryId'],self.case.cid,1,'SYNOPSIS',mock_model=True)
        adapter=work.MockMetadataAdapter({'synopsis':'合成梗概'})
        def response():
            with fixture.app_module.connect() as db:
                self.assertEqual(jobs.control(db,job['id'],self.case.cid,'cancel'),'unknown')
            return adapter.changes,20,20
        adapter.generate=response
        work.dispatch_mock(fixture.app_module.connect,job['id'],self.case.cid,adapter)
        with fixture.app_module.connect() as db:
            parent=db.execute('SELECT status,cancel_intent FROM mv3_jobs WHERE id=?',(job['id'],)).fetchone()
            self.assertEqual(tuple(parent),('cancelled',1))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',(job['id'],)).fetchone()[0],0)

    def test_migrated_cancel_missing_usage_retains_reservation(self):
        with fixture.app_module.connect() as db:
            work.init_db(db); draft=self.case.draft(db)
        with fixture.app_module.connect() as db: schema.migrate(db)
        with fixture.app_module.connect() as db:
            job=work.enqueue(db,draft['entryId'],self.case.cid,1,'SYNOPSIS',mock_model=True)
        adapter=work.MockMetadataAdapter({})
        calls=[]
        def response():
            calls.append(1)
            with fixture.app_module.connect() as db:
                jobs.control(db,job['id'],self.case.cid,'cancel')
            return {},None,None
        adapter.generate=response
        work.dispatch_mock(fixture.app_module.connect,job['id'],self.case.cid,adapter)
        with fixture.app_module.connect() as db:
            before=tuple(db.execute('SELECT request_id,dispatch_attempt_id,state,reserved_tokens,used_input_tokens FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone())
            self.assertEqual(before[2:5],('unknown',4096,None))
            with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                jobs.control(db,job['id'],self.case.cid,'resume')
            after=tuple(db.execute('SELECT request_id,dispatch_attempt_id,state,reserved_tokens,used_input_tokens FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone())
            self.assertEqual(before,after)
            self.assertEqual(tuple(db.execute('SELECT status,cancel_intent FROM mv3_jobs WHERE id=?',(job['id'],)).fetchone()),('unknown',1))
            self.assertEqual(calls,[1])


class OtherPaths(unittest.TestCase):
    def test_migrated_m35_cancel_late_usage(self):
        import test_memory_v2_model_tasks as f
        from backend.app import memory_v2_model_adapter as model
        case=f.ModelTasks('test_multi_batch_persistent_identity_and_resume_without_repeat')
        case.setUp()
        try:
            with f.app_module.connect() as db: schema.migrate(db)
            with f.app_module.connect() as db:
                job=jobs.enqueue_model(db,case.cid,'NARRATIVE',case.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
            calls=[]
            def response(request):
                calls.append(1)
                with f.app_module.connect() as db:
                    jobs.control(db,job['id'],case.cid,'cancel')
                    db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?',(job['id'],))
                return case.model(request)
            jobs.dispatch_model_chunk(f.app_module.connect,job['id'],case.cid,case.tok,model.LocalMockAdapter(response))
            with f.app_module.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(job['id'],)).fetchone()[0],'cancelled')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
                self.assertGreater(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone()[0],0)
            self.assertEqual(calls,[1])
        finally: case.tearDown()

    def test_migrated_m36_cancel_settles_dollars_only(self):
        import test_memory_v2_m36 as f
        case=f.M36Trial('test_trial_schema_preflight_and_two_variants')
        case.setUp()
        try:
            with f.app_module.connect() as db: schema.migrate(db)
            tid,variants,fake,_=case.prepare()
            jid=variants[f.responses.PLUGIN_VARIANT]
            with f.app_module.connect() as db:
                before=f.trial.trial_status(db,tid,case.cid)['reserved_microusd']
            def cancel():
                with f.app_module.connect() as db: jobs.control(db,jid,case.cid,'cancel')
            fake.on_generate=cancel
            f.trial.dispatch_trial_chunk(f.app_module.connect,tid,jid,case.cid,f.adapter(fake))
            with f.app_module.connect() as db:
                ledger=f.trial.trial_status(db,tid,case.cid)
                self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0],'cancelled')
                self.assertLess(ledger['reserved_microusd'],before)
                self.assertGreater(ledger['spent_estimate_microusd'],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
                self.assertIsNone(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0])
        finally: case.tearDown()
