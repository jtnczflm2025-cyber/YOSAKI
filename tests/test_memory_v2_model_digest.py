from test_memory_v2_model_tasks import ModelTasks
from test_memory_v2_derivation import app_module, derived
from backend.app import memory_v2_jobs as jobs, memory_v2_model_adapter as adapter
from backend.app import memory_v2_digest as digest


class ModelDigest(ModelTasks):
    def test_model_digest_dependency_and_invalidated_source(self):
        with app_module.connect() as db:
            child=digest.create_digest(db,self.cid,self.refs(),level='L1',period='2026-W01')
            self.approve(db,child['id'])
            job=jobs.enqueue_model(db,self.cid,'DIGEST',self.refs(),level='L2',period='2026-01',
                children=[child['id']],input_cap=2000,output_cap=800,total_token_cap=2800)
        result=jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,adapter.LocalMockAdapter(self.model))
        self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            v=derived._row(db,'mv3_versions',result['versionId'])
            self.assertEqual(v['status'],'needs_review')
            self.assertEqual(db.execute('SELECT child_version_id FROM mv3_dependencies WHERE version_id=?',(v['id'],)).fetchone()[0],child['id'])
            self.approve(db,v['id'])
            db.execute('DELETE FROM messages WHERE id=?',(self.user,))
            with self.assertRaises(ValueError):
                derived.read_version(db,v['id'],self.cid)

    def test_model_digest_rejects_unknown_claim_of_success(self):
        from backend.app.memory_v2_fidelity import validate_chunk
        inp=[{'sourceKind':'activity_operation','sourceId':'synthetic-op','speaker':'activity','sourceStatus':'unknown','content':'结果未知'}]
        for claim in ('操作成功','操作已完成','事情办妥了','执行完毕'):
            out={'finishReason':'stop','output':{'items':[{'sourceKind':'activity_operation','sourceId':'synthetic-op','speaker':'activity','text':claim}]}}
            with self.assertRaisesRegex(ValueError,'unverified_operation_success'):
                validate_chunk(inp,out,kind='DIGEST')
