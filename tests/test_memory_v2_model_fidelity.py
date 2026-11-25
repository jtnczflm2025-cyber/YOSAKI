import json
from test_memory_v2_model_tasks import ModelTasks, Utf8ByteTokenizer
from test_memory_v2_derivation import app_module, derived
from backend.app import memory_v2_jobs as jobs, memory_v2_model_adapter as adapter


class ModelFidelity(ModelTasks):
    def test_narrative_draft_needs_human_review(self):
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?',
                ('我今天累了，'*6+'如果下雨，我就不出去。',self.user))
            db.execute('UPDATE messages SET content=? WHERE id=?',
                ('俺说今天先休息。'*6,self.assistant))
            self._index(db)
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=1000,total_token_cap=3000)
        def compressed(request):
            texts=[]
            for m in request['messages']:
                out=('她说今天累了；如果下雨，她就不出去。' if m['speaker']=='user'
                     else '我说今天先休息。')
                texts.append({'sourceKind':m['sourceKind'],'sourceId':m['sourceId'],'speaker':m['speaker'],'text':out})
            return {'finishReason':'stop','output':{'items':texts}}
        result=jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,adapter.LocalMockAdapter(compressed))
        self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            v=derived._row(db,'mv3_versions',result['versionId'])
            self.assertEqual((v['status'],v['validation_status'],v['review_status']),('needs_review','pending','unreviewed'))
            with self.assertRaisesRegex(ValueError,'needs_review'):
                derived.read_version(db,v['id'],self.cid)
            with self.assertRaisesRegex(ValueError,'valid_review_required'):
                derived.activate_version(db,v['id'],self.cid)
            mapping=json.loads(v['mapping_json'])
            self.assertEqual([x['sourceId'] for x in mapping],[str(self.user),str(self.assistant)])
            self.assertTrue(all(x['rawStart'] is None for x in mapping))
            self.assertEqual(len(mapping),2)

    def test_wrong_speaker_unsupported_feelings_and_missing_condition_rejected(self):
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?',('如果下雨，我就不去。',self.user))
            self._index(db)
            job=jobs.enqueue_model(db,self.cid,'NARRATIVE',self.refs(),input_cap=2000,output_cap=1000,total_token_cap=3000)
        def wrong(request):
            return {'finishReason':'stop','output':{'items':[
                {'sourceKind':'message','sourceId':str(self.user),'speaker':'user','text':'俺觉得很难过，所以不去。'},
                {'sourceKind':'message','sourceId':str(self.assistant),'speaker':'assistant','text':'我说今天先休息。'}]}}
        with self.assertRaisesRegex(ValueError,'user_utterance_misattributed'):
            jobs.dispatch_model_chunk(app_module.connect,job['id'],self.cid,self.tok,adapter.LocalMockAdapter(wrong))
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0)
            self.assertEqual(jobs.model_status(db,job['id'],self.cid)['chunks'][0]['state'],'rejected')

    def test_multi_batch_source_order_and_repeat_guard(self):
        from backend.app.memory_v2_fidelity import join_checked, validate_chunk
        with self.assertRaisesRegex(ValueError,'duplicate_source_across_batches'):
            join_checked([{'items':[{'sourceKind':'message','sourceId':'1','speaker':'user','text':'A'}]},
                          {'items':[{'sourceKind':'message','sourceId':'1','speaker':'user','text':'B'}]}])
        inputs=[{'sourceKind':'message','sourceId':'1','speaker':'user','sourceStatus':'completed','content':'先说甲，后来更正为乙。'}]
        response={'finishReason':'stop','output':{'items':[{'sourceKind':'message','sourceId':'1','speaker':'user','text':'她说甲，后来是乙。'}]}}
        with self.assertRaisesRegex(ValueError,'correction_dropped'):
            validate_chunk(inputs,response,kind='NARRATIVE')
        self.assertIn('possible_repeated_narration',join_checked([{'items':[{'sourceKind':'message','sourceId':'1','speaker':'user','text':'重复'}]},
            {'items':[{'sourceKind':'message','sourceId':'2','speaker':'user','text':'重复'}]}])[1])
