import json
from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_metadata as meta


class ProgressiveMetadata(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db: meta.init_db(db)

    def draft(self, db, key='one'):
        return meta.create_draft(db, self.cid, self.refs(), '休息', 1, key)

    def test_title_only_order_and_idempotence(self):
        with app_module.connect() as db:
            first = self.draft(db)
            self.assertEqual(first['entryId'], self.draft(db)['entryId'])
            self.assertEqual([r['source_id'] for r in first['sourceRefs']], [str(self.user), str(self.assistant)])
            self.assertEqual(first['fields']['synopsis']['completionStatus'], 'missing')
            self.assertFalse(first['qualifications']['candidateRecall'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            meta.init_db(db)
            self.assertEqual(self.draft(db)['entryId'], first['entryId'])

    def test_revisions_share_unchanged_fields(self):
        with app_module.connect() as db:
            first = self.draft(db)
            second = meta.revise(db, first['entryId'], self.cid, first['versionId'], {'synopsis': '她说累了。'}, 1)
            self.assertEqual(first['fields']['title']['fieldId'], second['fields']['title']['fieldId'])
            third = meta.revise(db, second['entryId'], self.cid, second['versionId'], {'title': '小憩'}, 1)
            self.assertEqual(second['fields']['synopsis']['token'], third['fields']['synopsis']['token'])
            with self.assertRaisesRegex(ValueError, 'version_conflict'):
                meta.revise(db, first['entryId'], self.cid, first['versionId'], {'title': '旧写入'}, 1)

    def test_source_changed_or_role_revoked_rejects_management_read(self):
        with app_module.connect() as db:
            first = self.draft(db)
            db.execute('UPDATE messages SET content=? WHERE id=?', ('修订', self.user))
            with self.assertRaises(ValueError): meta.read_draft(db, first['entryId'], self.cid)
            with self.assertRaises(ValueError): meta.read_draft(db, first['entryId'], self.cid + 100)

    def test_manual_suppression_cannot_be_overwritten(self):
        with app_module.connect() as db:
            first = self.draft(db)
            second = meta.revise(db, first['entryId'], self.cid, first['versionId'], {'tags': None}, 1)
            with self.assertRaisesRegex(ValueError, 'manual_field_locked'):
                meta.revise(db, first['entryId'], self.cid, second['versionId'], {'tags': []}, 1,
                    provenance='rule', rule='extract-v1')

    def test_review_is_version_bound_and_not_formal_activation(self):
        with app_module.connect() as db:
            first = self.draft(db)
            approved = meta.review_field(db, first['entryId'], self.cid, 'title', first['fields']['title']['token'], 1)
            self.assertEqual(approved['fields']['title']['lifecycleStatus'], 'available')
            self.assertFalse(approved['qualifications']['formalContext'])
            second = meta.revise(db, first['entryId'], self.cid, first['versionId'], {'title': '修订'}, 1)
            self.assertEqual(second['fields']['title']['lifecycleStatus'], 'draft')
            with self.assertRaises(ValueError):
                meta.review_field(db, first['entryId'], self.cid, 'title', first['fields']['title']['token'], 1)

    def test_field_dependencies_invalidate_only_consumers(self):
        with app_module.connect() as db:
            first = self.draft(db)
            meta.review_field(db, first['entryId'], self.cid, 'title', first['fields']['title']['token'], 1)
            second = meta.revise(db, first['entryId'], self.cid, first['versionId'], {'keywords': []}, 1,
                dependencies={'title': first['fields']['title']['token']})
            third = meta.revise(db, first['entryId'], self.cid, second['versionId'], {'title': '小憩'}, 1)
            self.assertEqual(third['fields']['keywords']['lifecycleStatus'], 'stale')
            self.assertIsNone(third['fields']['keywords']['value'])

    def test_existing_legacy_review_remains_valid(self):
        with app_module.connect() as db:
            entry, version, narrative = self.new_entry(db)
            self.approve_entry(db, version)
            meta.init_db(db)
            from backend.app import memory_v2_entries as legacy
            self.assertIsNone(legacy.validity(db, version['id'], self.cid))

    def test_vocabulary_rename_semantic_change_and_alias_collision(self):
        from backend.app import memory_v2_vocabulary as vocab
        with app_module.connect() as db:
            seed=vocab.seed(db,self.cid,1)
            self.assertEqual([x['vocabulary_id'] for x in seed],
                [x['vocabulary_id'] for x in vocab.seed(db,self.cid,1)])
            first=vocab.write(db,self.cid,'tag','YOSAKI','en',1,definition='项目')
            self.assertEqual(vocab.match(db,self.cid,'tag','ｙｏｓａｋｉ','en')['vocabulary_id'],first['vocabulary_id'])
            renamed=vocab.write(db,self.cid,'tag','Yosaki 项目','en',1,
                vocabulary_id=first['vocabulary_id'],expected_version=1,definition='项目')
            self.assertEqual(first['semantic_token'],renamed['semantic_token'])
            changed=vocab.write(db,self.cid,'tag','Yosaki 项目','en',1,
                vocabulary_id=first['vocabulary_id'],expected_version=2,definition='新的含义')
            self.assertNotEqual(first['semantic_token'],changed['semantic_token'])
            with self.assertRaises(ValueError): vocab.get(db,self.cid+1,first['vocabulary_id'])
            with self.assertRaisesRegex(ValueError,'collision'):
                vocab.write(db,self.cid,'tag','YOSAKI 项目','en',1)

    def test_api_auth_strict_payload_and_stale_preview(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_metadata_api as api
        app=FastAPI()
        api.install(app,app_module.connect,lambda request: {'id':1} if request.headers.get('x-owner')=='yes' else None)
        client=TestClient(app)
        payload=dict(characterId=self.cid,sourceRefs=self.refs(),title='休息',requestKey='api')
        self.assertEqual(client.post('/api/memory-v2/entry-drafts',json=payload).status_code,401)
        headers={'x-owner':'yes'}
        self.assertEqual(client.post('/api/memory-v2/entry-drafts',json={**payload,'reviewerId':1},headers=headers).status_code,422)
        response=client.post('/api/memory-v2/entry-drafts',json=payload,headers=headers)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        eid=response.json()['entryId']
        with app_module.connect() as db: db.execute('DELETE FROM messages WHERE id=?',(self.user,))
        response=client.get(f'/api/memory-v2/entry-drafts/{eid}',params={'characterId':self.cid},headers=headers)
        self.assertEqual(response.status_code,409)
        self.assertNotIn('休息',response.text)

    def test_jobs_pause_resume_verified_extract_and_repeated_dispatch(self):
        from backend.app import memory_v2_metadata_jobs as work, memory_v2_jobs as jobs
        with app_module.connect() as db:
            work.init_db(db)
            first=self.draft(db)
            job=work.enqueue(db,first['entryId'],self.cid,1,'SYNOPSIS')
            self.assertEqual(job['id'],work.enqueue(db,first['entryId'],self.cid,1,'SYNOPSIS')['id'])
            jobs.control(db,job['id'],self.cid,'pause')
            with self.assertRaises(ValueError): jobs.claim(db,job['id'],self.cid)
            jobs.control(db,job['id'],self.cid,'resume')
            lease=jobs.claim(db,job['id'],self.cid)
            result=jobs.execute_claimed(db,job['id'],self.cid,lease)
            self.assertEqual(result['status'],'completed',result)
            with self.assertRaises(ValueError): jobs.execute_claimed(db,job['id'],self.cid,lease)
            checked=meta.verify_rule(db,first['entryId'],self.cid,'synopsis')
            self.assertEqual(checked['fields']['synopsis']['lifecycleStatus'],'available')
            self.assertEqual(checked['fields']['synopsis']['reviewStatus'],'not_applicable')
            self.assertFalse(checked['qualifications']['candidateRecall'])
            meta.review_field(db,first['entryId'],self.cid,'title',checked['fields']['title']['token'],1)
            meta.publish(db,first['entryId'],self.cid,checked['versionId'],1)
            self.assertFalse(meta.read_active(db,first['entryId'],self.cid)['qualifications']['formalContext'])

    def test_forged_rule_does_not_publish_free_rewrite(self):
        with app_module.connect() as db:
            first=self.draft(db)
            result=meta.revise(db,first['entryId'],self.cid,first['versionId'],
                {'synopsis':'她决定永远不再休息。'},1,provenance='rule',rule='metadata-extractive-v1')
            with self.assertRaisesRegex(ValueError,'rule_output_mismatch'):
                meta.verify_rule(db,first['entryId'],self.cid,'synopsis')
            self.assertEqual(result['fields']['synopsis']['lifecycleStatus'],'draft')

    def test_metadata_search_and_shared_m2_coordinator(self):
        from backend.app import memory_v2_metadata_search as search, memory_v2_recall as recall
        from backend.app.memory_v2_contract import RecallRequest
        from backend.app.memory_v2_context import compile_context
        with app_module.connect() as db:
            search.init_db(db)
            first=meta.create_draft(db,self.cid,self.refs(),'美咲与阿夜',1,'search')
            second=meta.revise(db,first['entryId'],self.cid,first['versionId'],{'synopsis':'她说累了，我建议休息。'},1)
            for name in ('title','synopsis'):
                meta.review_field(db,first['entryId'],self.cid,name,second['fields'][name]['token'],1)
            search.rebuild(db,first['entryId'],self.cid)
            self.assertTrue(search.search(db,self.cid,'美咲'))
            self.assertFalse(search.search(db,self.cid,'美咲',purpose='candidate'))
            meta.publish(db,first['entryId'],self.cid,second['versionId'],1)
            request=RecallRequest(self.cid,'explicit','deliberate','美咲')
            result=recall.recall(db,request,generators=[search.MetadataGenerator(),search.MetadataGenerator()])
            self.assertEqual(len(result.selected),1)
            self.assertEqual(len(result.selected[0].source_refs),2)
            bundle=compile_context(result,budget_tokens=512)
            self.assertEqual(len(bundle.blocks),2)
            self.assertEqual(bundle.blocks[0].text,'今天累了。')
            db.execute('UPDATE messages SET content=? WHERE id=?',('变化',self.user))
            self.assertFalse(search.search(db,self.cid,'美咲'))

    def test_130_synthetic_sources_13_idempotent_boundaries(self):
        ranges=[(1,4),(5,10),(11,16),(17,26),(27,42),(43,50),(51,74),
                (75,82),(83,90),(91,98),(99,110),(111,120),(121,130)]
        with app_module.connect() as db:
            ids=[]
            for ordinal in range(1,131):
                ids.append(str(db.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',
                    (self.conv,'user' if ordinal%2 else 'assistant',f'合成消息 {ordinal}','2026-01-01')).lastrowid))
            self._index(db)
            covered=[]; uuids=[]
            for first,last in ranges:
                refs=[{'kind':'message','id':x} for x in ids[first-1:last]]
                item=meta.create_draft(db,self.cid,refs,f'合成片段 {first}',1,f'boundary-{first}')
                repeat=meta.create_draft(db,self.cid,refs,f'合成片段 {first}',1,f'boundary-{first}')
                self.assertEqual(item['entryId'],repeat['entryId'])
                self.assertEqual(item['fields']['synopsis']['completionStatus'],'missing')
                covered.extend(r['source_id'] for r in item['sourceRefs']); uuids.append(item['entryId'])
            self.assertEqual(covered,ids); self.assertEqual(len(set(uuids)),13)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_mock_output_needs_review_and_unknown_never_retries(self):
        from backend.app import memory_v2_metadata_jobs as work, memory_v2_jobs as jobs
        with app_module.connect() as db:
            work.init_db(db); first=self.draft(db)
            job=work.enqueue(db,first['entryId'],self.cid,1,'SYNOPSIS',mock_model=True)
        result=work.dispatch_mock(app_module.connect,job['id'],self.cid,work.MockMetadataAdapter({'synopsis':'她说累了。'}))
        self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            detail=meta.read_draft(db,first['entryId'],self.cid)
            self.assertEqual(detail['fields']['synopsis']['lifecycleStatus'],'draft')
            with self.assertRaises(ValueError): meta.verify_rule(db,first['entryId'],self.cid,'synopsis')
            second=self.draft(db,'timeout')
            unknown=work.enqueue(db,second['entryId'],self.cid,1,'SYNOPSIS',mock_model=True)
        result=work.dispatch_mock(app_module.connect,unknown['id'],self.cid,work.MockMetadataAdapter({},timeout=True))
        self.assertEqual(result['status'],'unknown')
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError,'model_result_unknown'):
                jobs.control(db,unknown['id'],self.cid,'resume')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(unknown['id'],)).fetchone()[0],1)

    def test_time_evidence_change_is_not_hidden_by_recorded_time(self):
        # Contract test uses no private data; the real H5-B receipt is never opened.
        from backend.app.memory_v2_metadata_contract import time_tokens
        evidence=[dict(kind='message',id='1',sourceOrdinal=1,rawTime='2026-07-20 19:38',
            occurredAt=None,occurredDate=None,precision='local_datetime_unverified',timezoneBasis='none')]
        self.assertFalse(time_tokens(evidence)['ageEligible'])
        self.assertNotEqual(time_tokens(evidence)['timeEvidenceToken'],
            time_tokens([{**evidence[0],'rawTime':'2026-07-19 17:37'}])['timeEvidenceToken'])

    def test_source_changes_during_mock_request_reject_result(self):
        from unittest.mock import patch
        from backend.app import memory_v2_metadata_jobs as work
        with app_module.connect() as db:
            work.init_db(db); first=self.draft(db)
            job=work.enqueue(db,first['entryId'],self.cid,1,'SYNOPSIS',mock_model=True)
        def response(adapter):
            with app_module.connect() as db: db.execute('UPDATE messages SET content=? WHERE id=?',('变更',self.user))
            return {'synopsis':'过期输出'},20,20
        with patch.object(work.MockMetadataAdapter,'generate',response):
            result=work.dispatch_mock(app_module.connect,job['id'],self.cid,work.MockMetadataAdapter({}))
        self.assertEqual(result['status'],'rejected')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name=?',('synopsis',)).fetchone()[0],0)

    def test_p2_reads_published_partial_entry_without_import_age(self):
        from backend.app import memory_v2_time_projection as projection
        from datetime import datetime, timezone
        with app_module.connect() as db:
            projection.init_db(db)
            first=self.draft(db)
            second=meta.revise(db,first['entryId'],self.cid,first['versionId'],{'synopsis':'她说累了。'},1)
            for name in ('title','synopsis'):
                meta.review_field(db,first['entryId'],self.cid,name,second['fields'][name]['token'],1)
            meta.publish(db,first['entryId'],self.cid,second['versionId'],1)
            members=projection._members(db,self.cid,datetime(2026,11,8,tzinfo=timezone.utc),480)
            self.assertEqual(len(members),1)
            self.assertEqual(members[0].entry_id,first['entryId'])

    def test_receipt_original_time_and_unknown_age(self):
        from backend.app import memory_v2_import as importer, memory_v2_import_schema as schema
        from backend.app import memory_v2_time_projection as projection
        from datetime import datetime,timezone
        with app_module.connect() as db:
            schema.init_db(db); projection.init_db(db)
            raw=json.dumps({'format':'yosaki-conversation','version':2,'conversation':{'id':'synthetic-time'},
                'messages':[{'role':'user','content':'合成未知时区','created_at':'2026-07-20 19:38'}]}).encode()
            batch=importer.prepare(db,self.cid,[('synthetic.json',raw)])['batchId']
            importer.confirm(db,batch,self.cid); importer.step(db,batch,self.cid)
            row=db.execute('SELECT * FROM mv2_import_receipts').fetchone()
            self._index(db)
            item=meta.create_draft(db,self.cid,[{'kind':'message','id':str(row['message_id'])}],'未定时间',1,'receipt')
            self.assertEqual(item['timeEvidence'][0]['rawTime'],'2026-07-20 19:38')
            self.assertEqual(item['timeEvidence'][0]['sourceOrdinal'],row['ordinal'])
            self.assertFalse(item['ageEligible'])
            db.execute("UPDATE mv2_import_receipts SET original_time_text='2026-07-19 17:37'")
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=?',
                (row['message_id'],)).fetchone()[0], '合成未知时区')
            from backend.app import memory_v2_read
            indexed=db.execute("SELECT * FROM mv2_sources WHERE source_kind='message' AND source_id=?",
                (str(row['message_id']),)).fetchone()
            self.assertIsNotNone(indexed)
            self.assertIsNone(memory_v2_read.valid_source(db,indexed,self.cid))
            with self.assertRaisesRegex(ValueError,'source_missing_or_stale'):
                meta.read_draft(db,item['entryId'],self.cid)

    def test_revoked_role_list_rejects_without_pagination_metadata(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_metadata_api as api
        with app_module.connect() as db:
            self.draft(db,'revoked-one'); self.draft(db,'revoked-two')
            db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
        app=FastAPI()
        api.install(app,app_module.connect,lambda request: {'id':1})
        response=TestClient(app).get('/api/memory-v2/entry-drafts',params={'characterId':self.cid,'limit':1})
        self.assertIn(response.status_code,(403,404,409),response.text)
        self.assertNotIn('nextOffset',response.json())

    def test_list_role_gate_empty_missing_owner_and_cross_role(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_metadata_api as api
        app=FastAPI()
        api.install(app,app_module.connect,lambda request: {'id':1} if request.headers.get('x-owner')=='yes' else {'id':999})
        client=TestClient(app)
        headers={'x-owner':'yes'}
        base='/api/memory-v2/entry-drafts'
        empty=client.get(base,params={'characterId':self.cid},headers=headers)
        self.assertEqual(empty.status_code,200)
        self.assertEqual(empty.json()['items'],[])
        self.assertIsNone(empty.json()['nextOffset'])
        self.assertEqual(client.get(base,params={'characterId':self.cid}).status_code,404)
        for role in (999999,self.cid):
            with app_module.connect() as db:
                if role==self.cid: db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
            response=client.get(base,params={'characterId':role},headers=headers)
            self.assertEqual(response.status_code,404)
            self.assertNotIn('nextOffset',response.json())
            self.assertNotIn('items',response.json())

    def test_list_normal_pagination_and_revoke_between_pages(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_metadata_api as api
        app=FastAPI(); api.install(app,app_module.connect,lambda request:{'id':1})
        client=TestClient(app)
        with app_module.connect() as db:
            a=self.draft(db,'page-a'); b=self.draft(db,'page-b')
            other=db.execute("INSERT INTO characters(name,created_at,updated_at) VALUES('另一角色','2026-01-01','2026-01-01')").lastrowid
        first=client.get('/api/memory-v2/entry-drafts',params={'characterId':self.cid,'limit':1})
        self.assertEqual(first.status_code,200)
        self.assertEqual(len(first.json()['items']),1)
        self.assertEqual(first.json()['nextOffset'],1)
        second=client.get('/api/memory-v2/entry-drafts',params={'characterId':self.cid,'limit':1,'offset':1})
        self.assertEqual(second.status_code,200)
        self.assertEqual(len(second.json()['items']),1)
        self.assertIsNone(second.json()['nextOffset'])
        denied=client.get(f'/api/memory-v2/entry-drafts/{a["entryId"]}',params={'characterId':other})
        self.assertEqual(denied.status_code,409)
        self.assertNotIn('page-a',denied.text)
        self.assertEqual(client.get('/api/memory-v2/entry-drafts',params={'characterId':other}).json()['items'],[])
        with app_module.connect() as db: db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
        revoked=client.get('/api/memory-v2/entry-drafts',params={'characterId':self.cid,'limit':1,'offset':1})
        self.assertEqual(revoked.status_code,404)
        self.assertNotIn('nextOffset',revoked.json())
        self.assertEqual(client.get(f'/api/memory-v2/entry-drafts/{b["entryId"]}',params={'characterId':self.cid}).status_code,404)

    def test_published_title_only_has_separate_qualifications(self):
        from backend.app import memory_v2_metadata_search as search
        with app_module.connect() as db:
            search.init_db(db)
            item=meta.create_draft(db,self.cid,self.refs(),'美咲与阿夜',1,'title-only')
            meta.review_field(db,item['entryId'],self.cid,'title',item['fields']['title']['token'],1)
            published=meta.publish(db,item['entryId'],self.cid,item['versionId'],1)
            self.assertTrue(published['qualifications']['browse'])
            self.assertTrue(published['qualifications']['search'])
            self.assertTrue(published['qualifications']['p2Projection'])
            self.assertFalse(published['qualifications']['candidateRecall'])
            self.assertFalse(published['qualifications']['formalContext'])
            search.rebuild(db,item['entryId'],self.cid)
            self.assertEqual(len(search.search(db,self.cid,'美咲',purpose='management')),1)
            self.assertEqual(search.search(db,self.cid,'美咲',purpose='candidate'),[])
            self.assertIsNone(meta.read_active(db,item['entryId'],self.cid)['synopsis'])
