"""Run original business suites against explicitly migrated synthetic databases."""
import test_memory_v2_model_tasks as m35
import test_memory_v2_m36 as m36
import test_memory_v2_metadata as metadata
from backend.app import memory_v2_processing_schema as schema, memory_v2_jobs as jobs
from backend.app import memory_v2_metadata_jobs as work


class MigratedM35(m35.ModelTasks):
    def setUp(self):
        super().setUp()
        with m35.app_module.connect() as db: schema.migrate(db)

    def test_crash_after_possible_dispatch_cannot_be_retried(self):
        # Old fixture writes child-only state then recovers to paused. The migrated
        # contract forbids that state; create the same crash via the real dispatcher.
        with m35.app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
            jobs.prepare_model(db,job['id'],self.cid,self.tok)
            jobs.claim(db,job['id'],self.cid)
            row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(job['id'],)).fetchone()
            proof=jobs.begin_dispatch(db,job['id'],self.cid,0,expected_request_id=row['request_id'],expected_input_hash=row['input_hash'])
        del proof
        with m35.app_module.connect() as db:
            before=jobs.model_status(db,job['id'],self.cid)
            self.assertEqual(before['status'],'unknown')
            with self.assertRaises(ValueError): jobs.control(db,job['id'],self.cid,'recover',now=10**12)
            with self.assertRaisesRegex(ValueError,'model_result_unknown'): jobs.control(db,job['id'],self.cid,'resume')
            self.assertEqual(before,jobs.model_status(db,job['id'],self.cid))
        mock=m35.adapter.LocalMockAdapter(self.model)
        with self.assertRaisesRegex(ValueError,'model_result_unknown'):
            jobs.dispatch_model_chunk(m35.app_module.connect,job['id'],self.cid,self.tok,mock)
        self.assertEqual(mock.calls,[])

    def test_source_mutates_during_call_never_commits_version(self):
        # Explicit v2 contract: reliable response is accounted, stale artifact rejected.
        # Original unmigrated test remains unchanged and is run separately.
        with m35.app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
        def changed(request):
            with m35.app_module.connect() as db:
                db.execute("UPDATE messages SET content='synthetic changed source' WHERE id=?",(self.user,))
            return self.model(request)
        adapter=m35.adapter.LocalMockAdapter(changed)
        result=jobs.dispatch_model_chunk(m35.app_module.connect,job['id'],self.cid,self.tok,adapter)
        self.assertEqual(result['error'],'source_changed_during_model')
        with m35.app_module.connect() as db:
            report=jobs.model_status(db,job['id'],self.cid)
            self.assertEqual(report['status'],'failed')
            self.assertEqual(report['chunks'][0]['state'],'rejected')
            self.assertEqual(report['reservedCostUnits'],0)
            self.assertGreater(report['usedCostUnits'],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
        self.assertEqual(len(adapter.calls),1)


class MigratedM36(m36.M36Trial):
    def setUp(self):
        super().setUp()
        with m36.app_module.connect() as db: schema.migrate(db)

    def _uncertain_provider(self,kwargs):
        fake=m36.FakeProvider(**kwargs)
        tid,variants,_,_=self.prepare(fake)
        jid=variants[m36.responses.PLUGIN_VARIANT]
        with self.assertRaises(ValueError):
            m36.trial.dispatch_trial_chunk(m36.app_module.connect,tid,jid,self.cid,m36.adapter(fake))
        with m36.app_module.connect() as db:
            self.assertEqual(m36.trial.trial_status(db,tid,self.cid)['status'],'stopped')
            row=db.execute('SELECT state,provider_response_id FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()
            self.assertEqual(row['state'],'unknown')
            self.assertEqual(row['provider_response_id'],'resp_synthetic' if kwargs.get('usage') is False else None)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0],'unknown')
        with self.assertRaises(ValueError):
            m36.trial.dispatch_trial_chunk(m36.app_module.connect,tid,jid,self.cid,m36.adapter(fake))
        self.assertEqual(sum(path=='/v1/responses' for path,_ in fake.calls),1)

    def test_timeout_and_usage_missing_stop_without_retry(self):
        # Separate databases: an unresolved first trial must block a second trial.
        self._uncertain_provider({'status':'timeout'})

    def test_usage_missing_in_independent_fixture_stops_without_retry(self):
        self._uncertain_provider({'usage':False})

    def test_new_trial_cannot_bypass_old_unknown(self):
        self._uncertain_provider({'status':'timeout'})
        tid,variants,fake,_=self.prepare(m36.FakeProvider(usage=False))
        jid=variants[m36.responses.PLUGIN_VARIANT]
        with m36.app_module.connect() as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM mv3_model_chunks ORDER BY job_id,ordinal')]
            ledger=tuple(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(tid,)).fetchone())
        with self.assertRaisesRegex(ValueError,'semantic_scope_unknown'):
            m36.trial.dispatch_trial_chunk(m36.app_module.connect,tid,jid,self.cid,m36.adapter(fake))
        with m36.app_module.connect() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM mv3_model_chunks ORDER BY job_id,ordinal')])
            self.assertEqual(ledger,tuple(db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(tid,)).fetchone()))
        self.assertEqual(sum(path=='/v1/responses' for path,_ in fake.calls),0)


class MigratedMetadata(metadata.ProgressiveMetadata):
    def setUp(self):
        super().setUp()
        with metadata.app_module.connect() as db: work.init_db(db)
        with metadata.app_module.connect() as db: schema.migrate(db)
