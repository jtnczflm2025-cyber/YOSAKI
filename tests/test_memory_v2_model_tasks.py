"""M3.5 synthetic model and local UTF-8 byte-tokenizer acceptance."""
import json
import concurrent.futures
import threading
import unittest
from test_memory_v2_derivation import M3Base, app_module, derived
from backend.app import memory_v2_jobs as jobs, memory_v2_model_adapter as adapter


class Utf8ByteTokenizer:
    name = 'utf8-byte-v1 (synthetic only)'
    def encode(self,text):
        return list(text.encode('utf-8'))


class ModelTasks(M3Base):
    def setUp(self):
        super().setUp()
        self.tok=Utf8ByteTokenizer()
        with app_module.connect() as db:
            jobs.init_db(db)

    def model(self, request):
        return {'finishReason':'stop','output':{'items':[
            {'sourceKind':m['sourceKind'],'sourceId':m['sourceId'],'speaker':m['speaker'],'text':
             ('我说：' if m['speaker']=='assistant' else '她说：')+m['content']}
            for m in request['messages']]}}

    def test_single_long_message_owns_large_batch_and_hard_limits(self):
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?',('あ'*7000,self.user))
            self._index(db)
            sources=[derived.live_source(db,self.cid,'message',str(i)) for i in (self.user,self.assistant)]
            groups=adapter.batches(sources,self.tok,input_cap=25000)
            self.assertEqual([len(x) for x in groups],[1,1])
            self.assertEqual(len(groups[0][0]['content']),7000)
            db.execute('UPDATE messages SET content=? WHERE id=?',('あ'*12001,self.user))
            self._index(db)
            source=derived.live_source(db,self.cid,'message',str(self.user))
            with self.assertRaisesRegex(ValueError,'character_hard_limit'):
                adapter.batches([source],self.tok,input_cap=50000)
            with self.assertRaisesRegex(ValueError,'token_hard_limit'):
                adapter.batches([sources[0]],self.tok,input_cap=100)

    def test_multi_batch_persistent_identity_and_resume_without_repeat(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600,target_chars=6)
        mock=adapter.LocalMockAdapter(self.model)
        first=jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,mock)
        self.assertEqual(first['status'],'running')
        with app_module.connect() as db:
            rows=db.execute('SELECT state,request_id FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',(job['id'],)).fetchall()
            self.assertEqual([r['state'] for r in rows],['completed','prepared'])
            self.assertNotIn('今天累了',db.execute('SELECT input_json FROM mv3_model_chunks WHERE job_id=? LIMIT 1',(job['id'],)).fetchone()[0])
            self.assertEqual(jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600,target_chars=6)['id'],job['id'])
            expiry=db.execute('SELECT lease_until FROM mv3_jobs WHERE id=?',(job['id'],)).fetchone()[0]
            self.assertEqual(jobs.control(db,job['id'],self.cid,'recover',now=expiry+1),'paused')
            self.assertEqual(jobs.control(db,job['id'],self.cid,'resume'),'queued')
        last=jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,mock)
        self.assertEqual(last['status'],'completed')
        self.assertEqual(len(mock.calls),2)
        with app_module.connect() as db:
            self.assertEqual(derived._row(db,'mv3_versions',last['versionId'])['status'],'needs_review')
            self.assertGreater(jobs.model_status(db,job['id'],self.cid)['usedTokens'],0)
        with self.assertRaises(ValueError):
            jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,mock)
        self.assertEqual(len(mock.calls),2)

    def test_prepared_and_unknown_never_auto_retry(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600,target_chars=6)
            jobs.prepare_model(db,job['id'],self.cid,self.tok)
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=? LIMIT 1',(job['id'],)).fetchone()[0],'prepared')
        failed=adapter.LocalMockAdapter(lambda request: (_ for _ in ()).throw(TimeoutError()))
        with self.assertRaisesRegex(ValueError,'model_result_unknown'):
            jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,failed)
        with app_module.connect() as db:
            self.assertEqual(jobs.model_status(db,job['id'],self.cid)['chunks'][0]['state'],'unknown')
            self.assertGreater(jobs.model_status(db,job['id'],self.cid)['reservedTokens'],0)
            with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                jobs.control(db,job['id'],self.cid,'resume')
        self.assertEqual(len(failed.calls),1)

    def test_source_mutates_during_call_never_commits_version(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
        def changed(request):
            with app_module.connect() as db:
                db.execute("UPDATE messages SET content='changed' WHERE id=?",(self.user,))
            return self.model(request)
        self.assertEqual(jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,
            adapter.LocalMockAdapter(changed))['error'],'source_changed_during_model')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
            self.assertEqual(jobs.model_status(db,job['id'],self.cid)['chunks'][0]['state'],'unknown')

    def test_output_structure_fails_closed(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
        bad=adapter.LocalMockAdapter(lambda request:{'finishReason':'length','output':{'items':[]}})
        with self.assertRaises(ValueError):
            jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,bad)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
            report=jobs.model_status(db,job['id'],self.cid)
            self.assertEqual(report['chunks'][0]['state'],'rejected')
            self.assertEqual(report['reservedTokens'],0)
            self.assertEqual(report['reservedCostUnits'],0)

    def test_concurrent_dispatch_makes_one_model_call_and_cost_reservation(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,
                                   total_token_cap=5600,max_cost_units=5600)
        entered=threading.Event()
        release=threading.Event()
        def held(request):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.model(request)
        mock=adapter.LocalMockAdapter(held)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(jobs.dispatch_model_chunk,app_module.connect,job['id'],self.cid,self.tok,mock)
            self.assertTrue(entered.wait(5))
            second=pool.submit(jobs.dispatch_model_chunk,app_module.connect,job['id'],self.cid,self.tok,mock)
            try:
                with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                    second.result(timeout=5)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)['status'],'completed')
        self.assertEqual(len(mock.calls),1)
        with app_module.connect() as db:
            report=jobs.model_status(db,job['id'],self.cid)
            self.assertEqual(report['reservedCostUnits'],0)
            self.assertLessEqual(report['usedCostUnits'],5600)

    def test_budget_rejected_before_any_dispatch(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,
                                   total_token_cap=5600,max_cost_units=100)
        mock=adapter.LocalMockAdapter(self.model)
        with self.assertRaisesRegex(ValueError,'model_budget_exceeded'):
            jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,mock)
        self.assertEqual(mock.calls,[])

    def test_crash_after_possible_dispatch_cannot_be_retried(self):
        with app_module.connect() as db:
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=800,
                                   total_token_cap=5600)
            jobs.prepare_model(db,job['id'],self.cid,self.tok)
            jobs.claim(db,job['id'],self.cid,now=100)
            db.execute("UPDATE mv3_model_chunks SET state='possibly_dispatched' WHERE job_id=? AND ordinal=0",(job['id'],))
            self.assertEqual(jobs.control(db,job['id'],self.cid,'recover',now=161),'paused')
            with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                jobs.control(db,job['id'],self.cid,'resume')
        mock=adapter.LocalMockAdapter(self.model)
        with self.assertRaises(ValueError):
            jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,mock)
        self.assertEqual(mock.calls,[])
