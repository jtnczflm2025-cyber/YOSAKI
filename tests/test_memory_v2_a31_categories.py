"""A3.1 synthetic-only finite category proposals and local lifecycle."""
import json
import time
import unittest
from unittest.mock import patch

from a31_gold import GOLD, VARIANTS
from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_category_rules as rules
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as metadata_jobs
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_vocabulary as vocabulary
from backend.app import memory_v2_import_schema as import_schema
from backend.app import memory_v2_sources as source_adapter


class CategoryRules(unittest.TestCase):
    def test_unseen_directness_and_scope_matrix(self):
        cases = (
            ('supported_variant', [('user','请问这个接口的状态码是什么？'),
                                   ('assistant','409 表示游标已过期。')],
             {('form','技术讨论'),('domain','技术')}),
            ('unrelated_same_words', [('user','这个 API 为什么返回 409？'),
                                      ('assistant','我们先不谈接口。因为来源版本变了是昨天的公告标题。')],set()),
            ('quoted_full_pair', [('user','朋友说：“这个 API 为什么返回 409？”'),
                                  ('assistant','她还说：“因为来源版本变了。”')],set()),
            ('assistant_leads', [('assistant','这个 API 为什么返回 409？'),
                                 ('user','因为来源版本变了。')],set()),
            ('negative_then_positive', [('user','这次不是技术讨论，只是提到 API。'),
                                        ('assistant','知道了。'),
                                        ('user','这个 API 为什么返回 409？'),
                                        ('assistant','因为来源版本变了。')],
             {('form','技术讨论'),('domain','技术')}),
            ('outside_life', [('user','明早要去超市买菜，晚饭做什么？'),
                              ('assistant','买点青菜和豆腐，晚饭煮汤。')],set()),
            ('outside_creative', [('user','这幅画的配色怎么调整？'),
                                  ('assistant','试试降低背景的饱和度。')],set()),
        )
        for label,turns,expected in cases:
            with self.subTest(label=label):
                sources=[{'kind':'message','id':str(i),'role':role,'text':body}
                         for i,(role,body) in enumerate(turns)]
                actual={(h['dimension'],h['name']) for h in rules.evaluate(sources)['hits']}
                self.assertEqual(actual,expected)

    def test_gold_and_unseen_variants_with_raw_offsets(self):
        self.assertEqual(len(GOLD),14)
        for case in GOLD+VARIANTS:
            with self.subTest(case=case['name']):
                sources=[{'kind':'message','id':str(i+1),'role':role,'text':body}
                         for i,(role,body) in enumerate(case['turns'])]
                result=rules.evaluate(sources)
                self.assertEqual({(h['dimension'],h['name']) for h in result['hits']},case['expected'])
                for hit in result['hits']:
                    self.assertIn((hit['dimension'],hit['name']),rules.RULE_DEFINITIONS)
                    self.assertEqual(len(hit['evidence']),2)
                    for point in hit['evidence']:
                        source=next(x for x in sources if x['id']==point['id'])
                        self.assertTrue(source['text'][point['start']:point['end']])

    def test_local_context_not_global_exclusion(self):
        sources=[{'kind':'message','id':'1','role':'user','text':'这次不是技术讨论，只提软件。'},
                 {'kind':'message','id':'2','role':'assistant','text':'知道了。'},
                 {'kind':'message','id':'3','role':'user','text':'这个 API 为什么返回 409？'},
                 {'kind':'message','id':'4','role':'assistant','text':'因为来源版本变了。'}]
        result=rules.evaluate(sources)
        self.assertEqual({(x['dimension'],x['name']) for x in result['hits']},
                         {('form','技术讨论'),('domain','技术')})
        sources[2]['text']='如果以后这个 API 为什么返回 409，就再讨论。'
        self.assertEqual(rules.evaluate(sources)['hits'],[])


class CategoryTasks(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            metadata.init_db(db)
            metadata_jobs.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)
        with app_module.connect() as db:
            vocabulary.seed(db,self.cid,1)

    def make_entry(self, turns=None):
        turns=turns or GOLD[0]['turns']
        with app_module.connect() as db:
            refs=[]
            for role, body in turns:
                mid=db.execute('''INSERT INTO messages(conversation_id,role,content,created_at)
                    VALUES(?,?,?,'2026-01-01')''',(self.conv,role,body)).lastrowid
                refs.append({'kind':'message','id':str(mid)})
            self._index(db)
            detail=metadata.create_draft(db,self.cid,refs,'合成标题',1,
                                         'a31-'+str(refs[0]['id']))
            return detail

    def enqueue(self, detail):
        with app_module.connect() as db:
            return processing.create_category_job(db,detail['entryId'],self.cid,1)

    def test_candidate_evidence_review_gate_and_idempotence(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        first=processing.run_category_job(app_module.connect,job,self.cid,1)
        self.assertEqual(first['candidateCount'],2)
        self.assertFalse(first['reused'])
        with app_module.connect() as db:
            after=metadata.read_draft(db,detail['entryId'],self.cid)
            cats=after['fields']['categories']
            self.assertEqual(cats['completionStatus'],'candidate')
            self.assertEqual(cats['reviewStatus'],'needs_review')
            self.assertEqual(len(cats['value']),2)
            self.assertEqual(after['fields']['title']['fieldId'],detail['fields']['title']['fieldId'])
            self.assertFalse(after['qualifications']['candidateRecall'])
            self.assertFalse(after['qualifications']['formalContext'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(job,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks WHERE field_id=?',
                                        (cats['fieldId'],)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_a3_audit WHERE job_id=?',(job,)).fetchone()[0],1)
            for item in cats['value']:
                for point in item['evidence']:
                    raw=db.execute('SELECT content FROM messages WHERE id=?',(point['id'],)).fetchone()[0]
                    self.assertTrue(raw[point['start']:point['end']])
            fields_before=db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0]
        self.assertEqual(processing.run_category_job(app_module.connect,job,self.cid,1)['fieldSetId'],first['fieldSetId'])
        second_job=self.enqueue(after)
        second=processing.run_category_job(app_module.connect,second_job,self.cid,1)
        self.assertEqual(second['fieldSetId'],first['fieldSetId'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0],fields_before)

    def test_no_hit_does_not_write_empty_categories(self):
        detail=self.make_entry(GOLD[-1]['turns'])
        job=self.enqueue(detail)
        result=processing.run_category_job(app_module.connect,job,self.cid,1)
        self.assertEqual(result['candidateCount'],0)
        self.assertEqual(result['reasonCodes'],['no_supported_pair'])
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['completionStatus'],'missing')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT event FROM mv3_a3_audit WHERE job_id=?',(job,)).fetchone()[0],
                             'category_no_supported_pair')

    def test_manual_and_reviewed_unlocked_are_protected(self):
        detail=self.make_entry()
        with app_module.connect() as db:
            term=vocabulary.seed(db,self.cid,1)[0]
            source=detail['sourceRefs'][0]
            value=[{'id':term['vocabulary_id'],'evidence':[{'kind':source['source_kind'],
                'id':source['source_id'],'start':0,'end':2}]}]
            protected=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],
                                      {'categories':value},1)
        job=self.enqueue(protected)
        self.assertEqual(processing.run_category_job(app_module.connect,job,self.cid,1)['candidateCount'],0)
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['token'],
                             protected['fields']['categories']['token'])
            # Owner's explicit edit still uses the existing revise/review path.
            manual=metadata.revise(db,detail['entryId'],self.cid,protected['versionId'],
                                   {'categories':value},1)
            self.assertNotEqual(manual['versionId'],protected['versionId'])
        # A rule field can be reviewed without a manual lock. A later
        # automatic run still may not replace that reviewed result.
        another=self.make_entry()
        first_job=self.enqueue(another)
        processing.run_category_job(app_module.connect,first_job,self.cid,1)
        with app_module.connect() as db:
            candidate=metadata.read_draft(db,another['entryId'],self.cid)
            token=candidate['fields']['categories']['token']
            metadata.review_field(db,another['entryId'],self.cid,'categories',token,1)
        next_job=self.enqueue(candidate)
        result=processing.run_category_job(app_module.connect,next_job,self.cid,1)
        self.assertEqual(result['candidateCount'],0)
        with app_module.connect() as db:
            current=metadata.read_draft(db,another['entryId'],self.cid)
            self.assertEqual(current['fields']['categories']['token'],token)
            self.assertEqual(current['fields']['categories']['reviewStatus'],'human_checked')

    def test_cancel_during_rule_compute_and_source_change(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        original=rules.evaluate
        def cancel_during(sources):
            with app_module.connect() as db:
                self.assertEqual(processing.cancel_category_job(db,job,self.cid,1),'running')
            return original(sources)
        with patch.object(rules,'evaluate',side_effect=cancel_during):
            self.assertEqual(processing.run_category_job(app_module.connect,job,self.cid,1)['status'],'cancelled')
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['completionStatus'],'missing')
        other=self.make_entry()
        job2=self.enqueue(other)
        def change_during(sources):
            with app_module.connect() as db:
                db.execute('UPDATE messages SET content=? WHERE id=?',('changed synthetic',other['sourceRefs'][0]['source_id']))
            return original(sources)
        with patch.object(rules,'evaluate',side_effect=change_during):
            self.assertEqual(processing.run_category_job(app_module.connect,job2,self.cid,1)['status'],'failed')

    def test_crash_recovery_and_unknown_scope_hold(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        with patch.object(rules,'evaluate',side_effect=RuntimeError('synthetic crash')):
            with self.assertRaisesRegex(RuntimeError,'synthetic crash'):
                processing.run_category_job(app_module.connect,job,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(job,)).fetchone()[0],'running')
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['completionStatus'],'missing')
            db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?',(job,))
            self.assertEqual(processing.recover_category_job(db,job,self.cid,1),'paused')
            self.assertEqual(processing.resume(db,job,self.cid,json.loads(db.execute(
                'SELECT read_set_json FROM mv3_jobs WHERE id=?',(job,)).fetchone()[0])),'queued')
            # A legacy unknown hold blocks the same role without guessing a scope.
            db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES(?,?,?,?,?,'unknown',datetime('now'))",
                       ('legacy-hold',self.cid,'NARRATIVE','h','{}'))
        with self.assertRaisesRegex(ValueError,'legacy_unknown_scope'):
            processing.run_category_job(app_module.connect,job,self.cid,1)

    def test_vocabulary_status_and_rule_revision_invalidate(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        processing.run_category_job(app_module.connect,job,self.cid,1)
        with app_module.connect() as db:
            before=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertEqual(before['fields']['categories']['lifecycleStatus'],'draft')
        with patch.object(rules,'RULE_VERSION','category-direct-evidence-v3'):
            with app_module.connect() as db:
                self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['lifecycleStatus'],'stale')
        with app_module.connect() as db:
            vid=before['fields']['categories']['value'][0]['id']
            term=vocabulary.get(db,self.cid,vid)
            vocabulary.write(db,self.cid,'category',term['name'],term['language'],1,
                vocabulary_id=vid,expected_version=term['version'],dimension=term['dimension'],
                definition=term['definition'],status='deprecated')
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['lifecycleStatus'],'stale')

    def test_explicit_rebuild_after_alias_change_creates_current_candidate(self):
        detail=self.make_entry()
        processing.run_category_job(app_module.connect,self.enqueue(detail),self.cid,1)
        with app_module.connect() as db:
            first=metadata.read_draft(db,detail['entryId'],self.cid)
            old_token=first['fields']['categories']['token']
            vid=first['fields']['categories']['value'][0]['id']
            term=vocabulary.get(db,self.cid,vid)
            vocabulary.write(db,self.cid,'category',term['name'],term['language'],1,
                vocabulary_id=vid,expected_version=term['version'],dimension=term['dimension'],
                definition=term['definition'],aliases=['合成别名'])
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['lifecycleStatus'],'stale')
        fresh_job=self.enqueue(first)
        result=processing.run_category_job(app_module.connect,fresh_job,self.cid,1)
        self.assertEqual(result['candidateCount'],2)
        with app_module.connect() as db:
            rebuilt=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertNotEqual(rebuilt['fields']['categories']['token'],old_token)
            self.assertEqual(rebuilt['fields']['categories']['lifecycleStatus'],'draft')

    def test_cross_role_and_invalid_id_rejected(self):
        detail=self.make_entry()
        with app_module.connect() as db:
            with self.assertRaises(ValueError):
                processing.create_category_job(db,detail['entryId'],self.cid+100,1)
        job=self.enqueue(detail)
        with app_module.connect() as db:
            seed=vocabulary.seed(db,self.cid,1)
            for term in seed:
                if term['dimension']=='form' and term['name']=='技术讨论':
                    vocabulary.write(db,self.cid,'category',term['name'],term['language'],1,
                        vocabulary_id=term['vocabulary_id'],expected_version=term['version'],
                        dimension=term['dimension'],definition=term['definition'],status='deprecated')
                    break
        with self.assertRaises(ValueError):
            processing.run_category_job(app_module.connect,job,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],0)

    def test_final_transaction_rolls_back_field_and_completion(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        original=metadata.revise
        def crash_after_write(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('synthetic final transaction crash')
        with patch.object(metadata,'revise',side_effect=crash_after_write):
            with self.assertRaisesRegex(RuntimeError,'synthetic final transaction crash'):
                processing.run_category_job(app_module.connect,job,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(job,)).fetchone()[0],'running')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',(job,)).fetchone()[0],0)
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['completionStatus'],'missing')

    def test_completed_import_requires_confirmed_ordered_boundary(self):
        with app_module.connect() as db:
            import_schema.init_db(db)
            db.execute('''INSERT INTO mv2_import_batches(id,character_id,status,created_at,updated_at,processed,total)
                VALUES('b',?,'raw_complete','2026-01-01','2026-01-01',2,2)''',(self.cid,))
            conv=db.execute('''INSERT INTO conversations(character_id,title,created_at,updated_at,archive_only)
                VALUES(?,'synthetic imported','2026-01-01','2026-01-01',1)''',(self.cid,)).lastrowid
            db.execute('''INSERT INTO mv2_import_files(id,batch_id,character_id,name,file_sha256,
                parser_version,title,conversation_id,cursor,total,status,conversion_note)
                VALUES('f','b',?,'synthetic','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'synthetic-parser-v1','synthetic',?,2,2,'raw_complete','none')''',(self.cid,conv))
            refs=[]
            for ordinal,(role,body) in enumerate(GOLD[0]['turns'],1):
                mid=db.execute('''INSERT INTO messages(conversation_id,role,content,created_at,generation_status)
                    VALUES(?,?,?,'2026-01-01','imported')''',(conv,role,body)).lastrowid
                db.execute('''INSERT INTO mv2_import_receipts(message_id,file_id,character_id,ordinal,
                    time_precision,timezone_basis,recorded_at,file_sha256,parser_version,source_path,
                    conversion_note,content_sha256)
                    VALUES(?,'f',?,?,'unknown','unverified','2026-01-01',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'synthetic-parser-v1','synthetic','none',?)''',
                    (mid,self.cid,ordinal,source_adapter.digest(body)))
                refs.append({'kind':'message','id':str(mid)})
            self._index(db)
            detail=metadata.create_draft(db,self.cid,refs,'合成历史标题',1,'synthetic-boundary')
            db.execute('''INSERT INTO mv2_import_boundary_confirmations
                (file_id,batch_id,character_id,plan_version,plan_hash,source_snapshot_hash,
                h31_snapshot_hash,plan_json,confirmation_origin,confirmed_by_user_id,confirmed_at)
                VALUES('f','b',?,1,'plan','snapshot','h31','[]','owner',1,'2026-01-01')''',(self.cid,))
            db.execute('''INSERT INTO mv2_import_segments
                (id,batch_id,file_id,ordinal,start_ordinal,end_ordinal,title,entry_id)
                VALUES('s','b','f',1,1,2,'synthetic',?)''',(detail['entryId'],))
            jid=processing.create_category_job(db,detail['entryId'],self.cid,1)
            self.assertTrue(jid)
            db.execute('DELETE FROM mv2_import_boundary_confirmations WHERE file_id=?',('f',))
            with self.assertRaisesRegex(ValueError,'boundary_not_confirmed'):
                processing.create_category_job(db,detail['entryId'],self.cid,1)

    def test_incompatible_definition_stales_candidate_and_blocks_recompute(self):
        detail=self.make_entry()
        processing.run_category_job(app_module.connect,self.enqueue(detail),self.cid,1)
        with app_module.connect() as db:
            before=metadata.read_draft(db,detail['entryId'],self.cid)
            old_id=before['fields']['categories']['fieldId']
            vid=before['fields']['categories']['value'][0]['id']
            term=vocabulary.get(db,self.cid,vid)
            vocabulary.write(db,self.cid,'category',term['name'],term['language'],1,
                vocabulary_id=vid,expected_version=term['version'],dimension=term['dimension'],
                definition='与技术讨论不兼容的新定义')
            after=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertEqual(after['fields']['categories']['lifecycleStatus'],'stale')
            self.assertEqual(after['fields']['categories']['fieldId'],old_id)
            with self.assertRaisesRegex(ValueError,'category_vocabulary_unavailable'):
                processing.create_category_job(db,detail['entryId'],self.cid,1)
            # Owner's existing manual editing path remains independent of the
            # automatic whitelist and retains its historical field revisions.
            manual=metadata.revise(db,detail['entryId'],self.cid,after['versionId'],
                {'categories':[{'id':vid,'evidence':[{'kind':'message',
                    'id':detail['sourceRefs'][0]['source_id'],'start':0,'end':2}]}]},1)
            self.assertEqual(db.execute('SELECT provenance FROM mv3_entry_fields WHERE id=?',
                (manual['fields']['categories']['fieldId'],)).fetchone()[0],'manual')
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['lifecycleStatus'],'draft')

    def test_display_rename_preserves_rule_meaning_and_manual_prefix_untouched(self):
        detail=self.make_entry()
        with app_module.connect() as db:
            seed=vocabulary.seed(db,self.cid,1)
            term=next(x for x in seed if x['dimension']=='form' and x['name']=='技术讨论')
            vocabulary.write(db,self.cid,'category','技术话题',term['language'],1,
                vocabulary_id=term['vocabulary_id'],expected_version=term['version'],
                dimension=term['dimension'],definition=term['definition'])
            mid=detail['sourceRefs'][0]['source_id']
            manual=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],
                {'categories':[{'id':term['vocabulary_id'],'evidence':[
                    {'kind':'message','id':mid,'start':0,'end':2}]}]},1,
                provenance='manual',rule='category-direct-evidence-fake')
            token=manual['fields']['categories']['token']
        with patch.object(rules,'RULE_VERSION','category-direct-evidence-v3'):
            with app_module.connect() as db:
                current=metadata.read_draft(db,detail['entryId'],self.cid)
                self.assertEqual(current['fields']['categories']['token'],token)
                self.assertEqual(current['fields']['categories']['lifecycleStatus'],'draft')
        with app_module.connect() as db:
            self.assertTrue(processing.create_category_job(db,detail['entryId'],self.cid,1))

    def test_expired_local_worker_cannot_commit_after_reclaim(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        original=rules.evaluate
        calls=[]
        def reclaim_during(sources):
            calls.append(1)
            if len(calls)==1:
                with app_module.connect() as db:
                    self.assertEqual(processing.recover_category_job(
                        db,job,self.cid,1,now=time.time()+120),'paused')
                    read_set=json.loads(db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?',
                        (job,)).fetchone()[0])
                    self.assertEqual(processing.resume(db,job,self.cid,read_set),'queued')
                second=processing.run_category_job(app_module.connect,job,self.cid,1)
                self.assertEqual(second['status'],'completed')
            return original(sources)
        with patch.object(rules,'evaluate',side_effect=reclaim_during):
            with self.assertRaisesRegex(ValueError,'category_lease_changed'):
                processing.run_category_job(app_module.connect,job,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',(job,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(job,)).fetchone()[0],'completed')

    def test_local_recovery_rejects_remote_or_unknown_job(self):
        detail=self.make_entry()
        job=self.enqueue(detail)
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='unknown',lease_until=0 WHERE id=?",(job,))
            with self.assertRaisesRegex(ValueError,'local_recovery_not_proven'):
                processing.recover_category_job(db,job,self.cid,1,now=time.time()+120)
            db.execute("UPDATE mv3_jobs SET status='running',payload_json='{}' WHERE id=?",(job,))
            with self.assertRaisesRegex(ValueError,'not_category_job'):
                processing.recover_category_job(db,job,self.cid,1,now=time.time()+120)

    def test_v1_false_positive_stales_and_v2_no_hit_preserves_history(self):
        turns=[('user','这个 API 为什么返回 409？'),
               ('assistant','我们先不谈接口。因为来源版本变了是昨天的公告标题。')]
        detail=self.make_entry(turns)
        with patch.object(rules,'RULE_VERSION','category-direct-evidence-v1'):
            old_job=self.enqueue(detail)
            def former_rule(sources):
                question=sources[0]['text']
                answer=sources[1]['text']
                evidence=[{'kind':'message','id':sources[0]['id'],
                           'start':question.index('这个 API'), 'end':question.index('？')},
                          {'kind':'message','id':sources[1]['id'],
                           'start':answer.index('因为来源版本变了'),
                           'end':answer.index('因为来源版本变了')+len('因为来源版本变了')}]
                return {'hits':[{'dimension':dimension,'name':name,'variant':'technical_zh',
                                 'evidence':evidence}
                                for dimension,name in (('form','技术讨论'),('domain','技术'))],
                        'reasonCodes':[]}
            with patch.object(rules,'evaluate',side_effect=former_rule):
                old=processing.run_category_job(app_module.connect,old_job,self.cid,1)
                self.assertEqual(old['candidateCount'],2)
        with app_module.connect() as db:
            stale=metadata.read_draft(db,detail['entryId'],self.cid)
            old_field=stale['fields']['categories']['fieldId']
            self.assertEqual(stale['fields']['categories']['lifecycleStatus'],'stale')
            self.assertEqual(db.execute('SELECT rule_version FROM mv3_entry_fields WHERE id=?',
                                        (old_field,)).fetchone()[0],'category-direct-evidence-v1')
            old_count=db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0]
        new_job=self.enqueue(stale)
        result=processing.run_category_job(app_module.connect,new_job,self.cid,1)
        self.assertEqual(result['candidateCount'],0)
        self.assertEqual(result['reasonCodes'],['reply_not_direct'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],old_count)
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['fieldId'],old_field)

    def test_v1_in_flight_cannot_submit_after_rule_upgrade(self):
        detail=self.make_entry()
        with patch.object(rules,'RULE_VERSION','category-direct-evidence-v1'):
            old_job=self.enqueue(detail)
        with self.assertRaisesRegex(ValueError,'category_input_changed'):
            processing.run_category_job(app_module.connect,old_job,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(old_job,)).fetchone()[0],'queued')
        with patch.object(rules,'RULE_VERSION','category-direct-evidence-v1'):
            original=rules.evaluate
            def upgrade_during(sources):
                rules.RULE_VERSION='category-direct-evidence-v2'
                return original(sources)
            with patch.object(rules,'evaluate',side_effect=upgrade_during):
                result=processing.run_category_job(app_module.connect,old_job,self.cid,1)
                self.assertEqual(result,{'status':'failed','reason':'category_input_changed'})
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],0)

    def test_shared_positive_rebuilds_once_under_v2(self):
        detail=self.make_entry()
        with patch.object(rules,'RULE_VERSION','category-direct-evidence-v1'):
            first=processing.run_category_job(app_module.connect,self.enqueue(detail),self.cid,1)
        with app_module.connect() as db:
            old=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertEqual(old['fields']['categories']['lifecycleStatus'],'stale')
            old_field=old['fields']['categories']['fieldId']
        fresh=processing.run_category_job(app_module.connect,self.enqueue(old),self.cid,1)
        self.assertEqual(fresh['candidateCount'],2)
        with app_module.connect() as db:
            current=metadata.read_draft(db,detail['entryId'],self.cid)
            new_field=current['fields']['categories']['fieldId']
            self.assertNotEqual(old_field,new_field)
            self.assertEqual(db.execute('SELECT rule_version FROM mv3_entry_fields WHERE id=?',
                                        (new_field,)).fetchone()[0],'category-direct-evidence-v2')
            before=db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0]
        repeat=processing.run_category_job(app_module.connect,self.enqueue(current),self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name="categories"').fetchone()[0],before)
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['categories']['fieldId'],new_field)


if __name__ == '__main__': unittest.main()
