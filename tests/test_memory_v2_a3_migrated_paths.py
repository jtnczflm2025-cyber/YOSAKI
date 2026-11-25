import unittest
from backend.app import memory_v2_processing_schema as schema, memory_v2_jobs as jobs


class MigratedPaths(unittest.TestCase):
    def fixture(self,cls,method,app):
        case=cls(method); case.setUp(); self.addCleanup(case.doCleanups)
        with app.connect() as db: schema.migrate(db)
        return case

    def test_m35_multibatch_pause_resume(self):
        import test_memory_v2_model_tasks as f
        name='test_multi_batch_persistent_identity_and_resume_without_repeat'
        case=self.fixture(f.ModelTasks,name,f.app_module)
        getattr(case,name)()

    def test_m36_two_variants_no_auto_review(self):
        import test_memory_v2_m36 as f
        name='test_trial_schema_preflight_and_two_variants'
        case=self.fixture(f.M36Trial,name,f.app_module)
        getattr(case,name)()

    def test_metadata_normal_and_unknown_recovery(self):
        import test_memory_v2_metadata as f
        from backend.app import memory_v2_metadata_jobs as work
        case=f.ProgressiveMetadata('test_mock_output_needs_review_and_unknown_never_retries')
        case.setUp(); self.addCleanup(case.doCleanups)
        with f.app_module.connect() as db: work.init_db(db)
        with f.app_module.connect() as db: schema.migrate(db)
        case.test_mock_output_needs_review_and_unknown_never_retries()

    def test_m35_source_change_known_vs_unknown(self):
        import test_memory_v2_model_tasks as f
        from backend.app import memory_v2_model_adapter as model
        for known in (True,False):
            with self.subTest(known=known):
                case=self.fixture(f.ModelTasks,'test_source_mutates_during_call_never_commits_version',f.app_module)
                with f.app_module.connect() as db:
                    job=jobs.enqueue_model(db,case.cid,'NARRATIVE',case.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
                identity=[]
                def response(request):
                    with f.app_module.connect() as db:
                        row=db.execute('SELECT request_id,dispatch_attempt_id,state FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone()
                        self.assertEqual(row['request_id'],request['requestId'])
                        self.assertTrue(row['dispatch_attempt_id'])
                        self.assertEqual(row['state'],'possibly_dispatched')
                        identity.append(tuple(row))
                        db.execute("UPDATE messages SET content='synthetic changed source' WHERE id=?",(case.user,))
                    if not known: raise TimeoutError('synthetic unknown')
                    return case.model(request)
                adapter=model.LocalMockAdapter(response)
                if known:
                    result=jobs.dispatch_model_chunk(f.app_module.connect,job['id'],case.cid,case.tok,adapter)
                    self.assertEqual(result['error'],'source_changed_during_model')
                else:
                    with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                        jobs.dispatch_model_chunk(f.app_module.connect,job['id'],case.cid,case.tok,adapter)
                with f.app_module.connect() as db:
                    report=jobs.model_status(db,job['id'],case.cid)
                    self.assertEqual(report['status'],'failed' if known else 'unknown')
                    self.assertEqual(report['chunks'][0]['state'],'rejected' if known else 'unknown')
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
                    if known:
                        self.assertEqual(report['reservedCostUnits'],0)
                        self.assertGreater(report['usedCostUnits'],0)
                        self.assertEqual(report['usedCostUnits'],report['usedTokens'])
                    else:
                        self.assertGreater(report['reservedCostUnits'],0)
                        self.assertEqual(report['usedTokens'],0)
                        with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                            jobs.control(db,job['id'],case.cid,'resume')
                    row=db.execute('SELECT request_id,dispatch_attempt_id FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone()
                    self.assertEqual(tuple(row),identity[0][:2])
                self.assertEqual(len(adapter.calls),1)
