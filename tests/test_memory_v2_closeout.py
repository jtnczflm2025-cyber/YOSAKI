"""Non-private fixtures exercising real M3, metadata and model-context code."""
import os
import json
from unittest.mock import patch
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_memory_v2_entries import EntryBase, app_module, derived, entries
from backend.app import memory_v2_metadata as meta
from backend.app import memory_v2_dialogue_refinement as finite
from backend.app import memory_v2_chronicle_channel as channel
from backend.app import memory_v2_chronicle_workspace as workspace
from backend.app import memory_v2_chronicle_curation as curation
from backend.app import memory_v2_time_projection as p2


class Closeout(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            meta.init_db(db); p2.init_db(db); curation.init_db(db)
            self.draft = meta.create_draft(db, self.cid, self.refs(), '2026-01-01 休息', 1, 'closeout')

    def test_long_finite_dialogue_preserves_mapping_across_batches(self):
        long_turn = '「俺は待つ。」\n' * 28_000 + '最後：お前が決める。'
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?', (long_turn, self.assistant))
            self._index(db)
            draft = meta.create_draft(db, self.cid, self.refs(), '长文分批', 1, 'long-closeout')
            v = finite.create(db, self.cid, 1, draft['entryId'], draft['versionId'])
            self.assertGreater(len(v['body']), 200_000)
            rows = json.loads(v['mapping_json'])
            self.assertEqual([r['speaker'] for r in rows], ['user', 'assistant'])
            self.assertEqual(v['body'][rows[1]['start']:rows[1]['end']], '我说：' + long_turn)
            self.assertEqual(v['body'][rows[0]['end']:rows[1]['start']], '\n\n')
            self.assertIsNone(derived._validity(db, v['id'], self.cid, set(), draft=True))

    def ready(self, db, synopsis='イキヤ说累了，阿夜建议休息。'):
        d = meta.read_draft(db, self.draft['entryId'], self.cid)
        v = finite.create(db, self.cid, 1, d['entryId'], d['versionId'])
        workspace.confirm_narrative(db, d['entryId'], workspace.Confirm(characterId=self.cid,
            expectedVersion=d['versionId'], versionId=v['id'], inputHash=v['input_hash'], outputHash=v['output_hash']), 1)
        d = meta.read_draft(db, d['entryId'], self.cid)
        d = meta.revise(db, d['entryId'], self.cid, d['versionId'], {'synopsis': synopsis}, 1)
        for name in ('title', 'synopsis'):
            d = meta.review_field(db, d['entryId'], self.cid, name, d['fields'][name]['token'], 1)
        meta.publish(db, d['entryId'], self.cid, d['versionId'], 1)
        return d, v

    def test_finite_preserves_order_japanese_quotes_parentheses_and_repetition(self):
        raw = '……「俺とお前」、まだ約束だ。\n……試験は未完了。（予備部品は二つ）\n同じ話。\n同じ話。'
        text, edits = finite.render_message(raw, 'assistant')
        self.assertEqual(text, '我说：……「俺とお前」、まだ約束だ。\n試験は未完了。（予備部品は二つ）\n同じ話。\n同じ話。')
        self.assertEqual(len(edits), 1)
        user, _ = finite.render_message('我考虑休息。自我介绍里她说「我不会」。', 'user', rule=finite.PREVIOUS_RULE)
        self.assertEqual(user, 'イキヤ说：イキヤ考虑休息。自我介绍里她说「我不会」。')
        simple,_=finite.render_message('我考虑休息，但还没有决定。','user',rule=finite.PREVIOUS_RULE)
        self.assertEqual(simple,'イキヤ说：她考虑休息，但还没有决定。')
        old,_=finite.render_message('我考虑休息。','user',rule=finite.LEGACY_RULE)
        self.assertEqual(old,'イキヤ说：イキヤ考虑休息。')

    def test_perspective_v3_preserves_care_roles_questions_and_laughter(self):
        for raw in ('你照顾我，不是我照顾你。', '如果她同意，我才照顾她；后来改为先等。',
                    '我会照顾你。你说是不是？', '我问她是否改变主意，她还没有答复。',
                    '我说「俺とお前」，这里的我是谁？', '我妈妈说：你先休息。'):
            text, edits = finite.render_message(raw, 'user')
            self.assertEqual(text, 'イキヤ对我说：\n' + raw)
            self.assertEqual(edits, [])
        text, edits = finite.render_message('我考虑休息，但还没有决定。', 'user')
        self.assertEqual(text, 'イキヤ说，她考虑休息，但还没有决定。')
        self.assertEqual(finite.apply_edits('我考虑休息，但还没有决定。', edits),
                         '她考虑休息，但还没有决定。')
        self.assertEqual(finite.render_message('哈哈哈！', 'user')[0], 'イキヤ笑了笑。')
        text, edits = finite.render_message('哈哈哈，你照顾我，对不对？', 'user')
        self.assertEqual(text, 'イキヤ笑着说：\n你照顾我，对不对？')
        self.assertEqual(edits[0]['reason'], 'explicit_laughter_attribution')
        self.assertEqual(finite.render_message('まだだ。「俺」「お前」は変えない。', 'assistant')[0],
                         '我说：まだだ。「俺」「お前」は変えない。')

    def test_perspective_v3_old_versions_remain_readable_and_prefix_tamper_rejected(self):
        with app_module.connect() as db:
            d = self.draft
            parts, mapping = [], []
            for ref in self.refs():
                raw = derived.live_source(db, self.cid, ref['kind'], ref['id'])
                text, edits = finite.render_message(raw.text, raw.role, rule=finite.PREVIOUS_RULE)
                start = sum(map(len, parts)) + 2 * len(parts)
                parts.append(text)
                mapping.append(dict(sourceKind=ref['kind'], sourceId=ref['id'],
                    speaker=raw.role, sourceHash=raw.content_hash, start=start, end=start+len(text),
                    edits=edits, ruleVersion=finite.PREVIOUS_RULE, entryId=d['entryId'],
                    sourceToken=d['sourceToken']))
            derivation = derived.create_derivation(db, self.cid, 'NARRATIVE', self.refs())
            old = derived.add_version(db, derivation['id'], self.refs(), '\n\n'.join(parts),
                author_kind='rule', rule_version=finite.PREVIOUS_RULE, mapping=mapping)
            self.assertEqual(old['rule_version'], finite.PREVIOUS_RULE)
            self.assertIsNone(derived._validity(db, old['id'], self.cid, set(), draft=True))
            current = finite.create(db, self.cid, 1, d['entryId'], d['versionId'])
            self.assertEqual(current['rule_version'], finite.RULE)
            self.assertIsNone(derived._validity(db, current['id'], self.cid, set(), draft=True))
            rows = json.loads(current['mapping_json'])
            rows[0]['prefix'] = '我说：'
            db.execute('UPDATE mv3_versions SET mapping_json=? WHERE id=?',
                       (json.dumps(rows), current['id']))
            self.assertEqual(derived._validity(db, current['id'], self.cid, set(), draft=True),
                             'dialogue_attribution_changed')

    def test_only_explicit_translation_span_removed(self):
        raw = 'まだだ。（还没呢。） （明天可能测试）'
        text, _ = finite.render_message(raw, 'assistant', [{'start':4,'end':10,'quote':'（还没呢。）'}])
        self.assertEqual(text, '我说：まだだ。 （明天可能测试）')
        with self.assertRaises(ValueError):
            finite.render_message(raw, 'user', [{'start':4,'end':10,'quote':'（还没呢。）'}])

    def test_draft_no_approval_no_field_write_idempotent_and_source_invalidation(self):
        with app_module.connect() as db:
            d = self.draft
            a = finite.create(db, self.cid, 1, d['entryId'], d['versionId'])
            b = finite.create(db, self.cid, 1, d['entryId'], d['versionId'])
            self.assertEqual(a['id'], b['id'])
            self.assertEqual(a['status'], 'needs_review')
            self.assertEqual(a['body'], 'イキヤ说：今天累了。\n\n我说：那就休息。')
            self.assertIsNone(meta.read_draft(db, d['entryId'], self.cid)['fields']['narrative']['value'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0], 0)
            db.execute('UPDATE messages SET content=? WHERE id=?', ('后来更正', self.user))
            self.assertIsNotNone(derived._validity(db, a['id'], self.cid, set(), draft=True))

    def test_explicit_confirm_link_publish_read_and_no_review_inheritance(self):
        with app_module.connect() as db:
            d, v = self.ready(db)
            page = channel.expand(db, self.cid, 1, d['entryId'], limit=7)
            rest = channel.expand(db, self.cid, 1, d['entryId'], offset=page['nextOffset'], expected_version=page['versionId'])
            self.assertEqual(page['text'] + rest['text'], v['body'])
            revised = meta.revise(db, d['entryId'], self.cid, d['versionId'], {'synopsis': '人工改了梗概。'}, 1)
            self.assertEqual(revised['fields']['synopsis']['lifecycleStatus'], 'draft')
            self.assertEqual(revised['fields']['narrative']['lifecycleStatus'], 'available')

    def test_wrong_owner_role_source_and_page_version_cannot_leak(self):
        with app_module.connect() as db:
            d, v = self.ready(db)
            for cid, uid in [(self.cid, 999), (self.cid+100, 1)]:
                with self.assertRaises(ValueError): channel.expand(db, cid, uid, d['entryId'])
            with self.assertRaises(ValueError):
                channel.expand(db, self.cid, 1, d['entryId'], offset=2, expected_version='wrong')
            db.execute('UPDATE messages SET content=? WHERE id=?', ('SECRET REVOKED', self.user))
            with self.assertRaises(ValueError): channel.expand(db, self.cid, 1, d['entryId'])
            self.assertEqual(channel.search(db, self.cid, 1, '休息')['blocks'], [])

    def test_search_and_actual_chat_builder_only_processed_view(self):
        with app_module.connect() as db:
            d, v = self.ready(db)
            self.assertEqual(channel.search(db, self.cid, 1, '无关火山')['blocks'], [])
            with patch.dict(os.environ, {'YOSAKI_CHRONICLE_CONTEXT': '1'}):
                memories, summary, settings = app_module.memory_context(db, self.cid, self.conv, '休息', chronicle_owner_id=1)
                self.assertTrue(any(x.get('channel') == 'chronicle' for x in memories))
                character = db.execute('SELECT * FROM characters WHERE id=?', (self.cid,)).fetchone()
                messages = app_module.build_messages(character, [], [], '休息', memories, summary, settings=settings)
                self.assertIn('Chronicle 已确认经历', str(messages))
                self.assertNotIn('sourceRefs', str(messages))
                anonymous, _, _ = app_module.memory_context(db, self.cid, self.conv, '休息')
                self.assertFalse(any(x.get('channel') == 'chronicle' for x in anonymous))

    def test_curation_choices_independent_cas_cancel_and_history(self):
        with app_module.connect() as db:
            d, v = self.ready(db)
            flags = dict.fromkeys(curation.FLAGS, False); flags['important'] = True
            a = curation.save(db, self.cid, 1, d['entryId'], 0, flags, '重要但不常驻')
            self.assertEqual(curation.fixed_entries(db, self.cid, 1), [])
            flags['fixed'] = True
            b = curation.save(db, self.cid, 1, d['entryId'], a['revision'], flags, '明确固定')
            with self.assertRaises(ValueError): curation.save(db, self.cid, 1, d['entryId'], 0, flags, '')
            self.assertEqual(curation.fixed_entries(db, self.cid, 1), [d['entryId']])
            flags['fixed'] = False
            curation.save(db, self.cid, 1, d['entryId'], b['revision'], flags, '取消固定')
            self.assertEqual(curation.fixed_entries(db, self.cid, 1), [])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_curation').fetchone()[0], 3)

    def test_manager_http_real_routes_auth_read_save_no_implicit_review(self):
        app = FastAPI()
        workspace.install(app, app_module.connect, lambda req: {'id': 1} if req.headers.get('x-test-owner') == 'yes' else None)
        client = TestClient(app); path = '/api/memory-v2/workspace/entries/' + self.draft['entryId']
        self.assertEqual(client.get(path, params={'characterId':self.cid}).status_code, 401)
        headers = {'x-test-owner':'yes'}
        r = client.post(path+'/refine', headers=headers, json={'characterId':self.cid,'expectedVersion':self.draft['versionId']})
        self.assertEqual(r.status_code, 200, r.text)
        view = client.get(path, headers=headers, params={'characterId':self.cid}).json()
        self.assertEqual(view['narrative']['body'], 'イキヤ说：今天累了。\n\n我说：那就休息。')
        self.assertEqual(view['narrative']['review_status'], 'unreviewed')
        self.assertEqual(len(view['raw']), 2)

    def test_general_import_receipts_confirmation_not_h32_specimen(self):
        from backend.app import memory_v2_import as imp, memory_v2_import_schema as schema
        from backend.app import memory_v2_synopsis as synopsis
        from fastapi import HTTPException
        archive = json.dumps({'format':'yosaki-conversation','version':2,'conversation':{'id':'closeout'},
            'messages':[{'id':str(i),'role':'user' if i%2==0 else 'assistant',
                         'content':f'完整消息 {i}。重复也保留。','created_at':'2026-09-20T14:22:00+08:00'} for i in range(10)]}).encode()
        with app_module.connect() as db:
            schema.init_db(db); db.commit()
            batch = imp.prepare(db,self.cid,[('nonprivate.json',archive)])['batchId']
            imp.confirm(db,batch,self.cid)
            done=imp.step(db,batch,self.cid)
            file=dict(db.execute('SELECT * FROM mv2_import_files WHERE id=?',(done['files'][0]['id'],)).fetchone())
            self._index(db);db.commit()
            _,records,snapshot=imp.confirmed_file_snapshot(db,batch,self.cid,file['id'],file['file_sha256'],file['parser_version'],general=True)
            self.assertEqual(len(records),10)
            args=dict(expected_sha=file['file_sha256'],expected_parser=file['parser_version'],expected_snapshot=snapshot,editor_id=1)
            choice=[{'start':1,'end':10,'title':'2026-09-20 完整对话'}]
        app = FastAPI()
        workspace.install(app, app_module.connect, lambda req: {'id': 1} if req.headers.get('x-test-owner') == 'yes' else None)
        client = TestClient(app)
        path = f"/api/memory-v2/workspace/imports/{batch}/{file['id']}/boundaries"
        payload = {'characterId': self.cid, 'sourceSnapshot': snapshot, 'segments': choice}
        self.assertEqual(client.post(path, json=payload).status_code, 401)
        result = client.post(path, json=payload, headers={'x-test-owner': 'yes'})
        self.assertEqual(result.status_code, 200, result.text)
        a = result.json()
        self.assertEqual(a['preparation'][0]['status'], 'prepared')
        eid = a['items'][0]['entryId']
        editor = client.get(f'/api/memory-v2/workspace/entries/{eid}',
                            params={'characterId': self.cid}, headers={'x-test-owner': 'yes'}).json()
        self.assertIsNotNone(editor['narrative'])
        saved = client.post(f'/api/memory-v2/entry-drafts/{eid}/revisions',
                            headers={'x-test-owner': 'yes'}, json={'characterId': self.cid,
                            'expectedVersion': editor['entry']['versionId'],
                            'changes': {'title': '首次整理保存'}})
        self.assertEqual(saved.status_code, 200, saved.text)
        with app_module.connect() as db:
            b=imp.confirm_dialogue_drafts(db,batch,self.cid,file['id'],choice,**args)
            self.assertEqual([item['entryId'] for item in a['items']], [item['entryId'] for item in b['items']])
            self.assertEqual(b['items'][0]['title'], '首次整理保存')
            self.assertTrue(b['reused'])
            detail=meta.read_draft(db,a['items'][0]['entryId'],self.cid)
            self.assertEqual(len(synopsis.confirmed_snapshot(db,detail['entryId'],self.cid)['sourceRefs']),10)
            v=finite.create(db,self.cid,1,detail['entryId'],detail['versionId']);db.commit()
            self.assertEqual(v['body'].count('重复也保留。'),10)
            with self.assertRaises(HTTPException): app_module.require_live_conversation(db,file['conversation_id'])
            with self.assertRaises(HTTPException): imp.prepare(db,self.cid,[('nonprivate.json',archive)])
            with self.assertRaises(ValueError):
                imp.confirmed_file_snapshot(db,batch,self.cid,file['id'],file['file_sha256'],file['parser_version'])

    def test_import_entry_prepares_perspective_v3_with_exact_turn_mapping(self):
        from backend.app import memory_v2_import as imp, memory_v2_import_schema as schema
        bodies = ['你照顾我，不是我照顾你，对不对？', '「俺がお前を支える」と言った。',
                  '哈哈哈，我还没决定；如果获准，再讨论。', 'まだ約束で、実行していない。',
                  '你说是不是？我是在问许可是否已经批准。', 'まだ答えはない。（予備は二つ）']
        archive = json.dumps({'format': 'yosaki-conversation', 'version': 2,
            'conversation': {'id': 'perspective-import'},
            'messages': [{'id': str(i), 'role': 'assistant' if i % 2 else 'user',
                          'content': body, 'created_at': '2026-09-20 14:22:00'}
                         for i, body in enumerate(bodies)]}).encode()
        with app_module.connect() as db:
            schema.init_db(db); db.commit()
            batch = imp.prepare(db, self.cid, [('nonprivate.json', archive)])['batchId']
            imp.confirm(db, batch, self.cid)
            done = imp.step(db, batch, self.cid)
            f = dict(db.execute('SELECT * FROM mv2_import_files WHERE id=?',
                               (done['files'][0]['id'],)).fetchone())
            self._index(db); db.commit()
            _, _, snapshot = imp.confirmed_file_snapshot(db, batch, self.cid, f['id'],
                f['file_sha256'], f['parser_version'], general=True)
        app = FastAPI()
        workspace.install(app, app_module.connect, lambda req: {'id': 1})
        client = TestClient(app)
        path = f"/api/memory-v2/workspace/imports/{batch}/{f['id']}/boundaries"
        response = client.post(path, json={'characterId': self.cid, 'sourceSnapshot': snapshot,
            'segments': [{'start': 1, 'end': 6, 'title': '发生时间未核实的合成对话'}]})
        self.assertEqual(response.status_code, 200, response.text)
        eid = response.json()['items'][0]['entryId']
        view_path = f'/api/memory-v2/workspace/entries/{eid}'
        view = client.get(view_path, params={'characterId': self.cid}).json()
        self.assertEqual([r['text'] for r in view['raw']], bodies)
        self.assertIn('イキヤ对我说：\n' + bodies[0], view['narrative']['body'])
        self.assertIn('我说：' + bodies[1], view['narrative']['body'])
        self.assertIn('イキヤ笑着说：\n我还没决定；如果获准，再讨论。', view['narrative']['body'])
        self.assertIn(bodies[4], view['narrative']['body'])
        self.assertEqual(view['narrative']['status'], 'needs_review')
        self.assertEqual(view['entry']['fields']['synopsis']['completionStatus'], 'missing')
        with app_module.connect() as db:
            v = derived._row(db, 'mv3_versions', view['narrative']['id'])
            self.assertEqual(v['rule_version'], finite.RULE)
            rows = json.loads(v['mapping_json'])
            self.assertEqual([r['sourceId'] for r in rows], [r['id'] for r in view['raw']])
            self.assertIsNone(derived._validity(db, v['id'], self.cid, set(), draft=True))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0], 0)
        repeated = client.post(view_path + '/refine', json={'characterId': self.cid,
            'expectedVersion': view['entry']['versionId']})
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()['id'], view['narrative']['id'])

    def test_calendar_search_published_title_without_synopsis(self):
        with app_module.connect() as db:
            entry = self.draft
            meta.review_field(db, entry['entryId'], self.cid, 'title', entry['fields']['title']['token'], 1)
            meta.publish(db, entry['entryId'], self.cid, entry['versionId'], 1)
        app = FastAPI()
        workspace.install(app, app_module.connect, lambda _: {'id': 1})
        response = TestClient(app).get('/api/memory-v2/calendar/entries', params={
            'characterId': self.cid, 'asOf': '2026-11-17T00:00:00+08:00', 'q': '休息'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r['entryId'] for r in response.json()['items']], [entry['entryId']])
        self.assertIsNone(response.json()['items'][0]['synopsis'])

    def test_finite_unknown_version_and_missing_mapping_fail_closed(self):
        with app_module.connect() as db:
            v=finite.create(db,self.cid,1,self.draft['entryId'],self.draft['versionId'])
            db.execute("UPDATE mv3_versions SET mapping_json='[]' WHERE id=?",(v['id'],))
            self.assertEqual(derived._validity(db,v['id'],self.cid,set(),draft=True),'dialogue_mapping_missing')
            db.execute("UPDATE mv3_versions SET rule_version='dialogue-finite-unknown' WHERE id=?",(v['id'],))
            self.assertEqual(derived._validity(db,v['id'],self.cid,set(),draft=True),'dialogue_rule_unknown')

    def test_vector_arithmetic_is_not_semantic_quality_and_stale_does_not_return(self):
        from backend.app import memory_v2_chronicle_vectors as vectors
        with app_module.connect() as db:
            d,v=self.ready(db);vectors.init_db(db)
            _,fingerprint=vectors._input(db,self.cid,d['entryId'])
            vectors.store(db,self.cid,1,d['entryId'],'synthetic-arithmetic-only',fingerprint,[1.0,0.0])
            self.assertEqual(vectors.candidates(db,self.cid,1,[1.0,0.0],'synthetic-arithmetic-only'),[d['entryId']])
            self.assertEqual(vectors.hybrid(db,self.cid,1,'休息')['vectorStatus'],'not_configured')
            for bad in ([True,0],[float('nan'),0],[0,0]):
                with self.assertRaises(ValueError): vectors.unit(bad)
            db.execute('UPDATE messages SET content=? WHERE id=?',('已更正',self.user))
            self.assertEqual(vectors.candidates(db,self.cid,1,[1.0,0.0],'synthetic-arithmetic-only'),[])

    def test_silverleaf_hybrid_dedup_revision_and_provider_unavailable(self):
        import httpx
        from backend.app import memory_v2_chronicle_vectors as vectors, memory_v2_chronicle_embedding as embed
        from backend.app import memory_v2_chronicle_schema as schema
        model = embed.MODEL
        vector = [1.0] + [0.0] * 1535
        with app_module.connect() as db:
            schema.install(db)
            for rid, text in (
                (self.user, '银叶馆的蓝灯指示牌还没安装。如果馆方批准且夜间测试通过，我们再讨论是否安装。'),
                (self.assistant, '了解。承認と夜間テストの両方が済むまで、設置は保留しよう。'),
            ):
                db.execute('UPDATE messages SET content=? WHERE id=?', (text, rid))
            self._index(db)
            self.draft = meta.create_draft(db, self.cid, self.refs(), '银叶馆蓝灯指示牌', 1, 'silverleaf-search')
            draft,_ = self.ready(db, '馆方批准且夜间测试通过后再讨论是否安装，阿夜建议暂缓。')
            eid = draft['entryId']
            _, fingerprint = vectors._input(db, self.cid, eid)
            vectors.store(db, self.cid, 1, eid, model, fingerprint, vector)
            # Synthetic cosine controls exercise routing, not provider quality.
            paraphrase = '批准之前是不是该先搁置安装？'
            self.assertEqual(channel.lexical_candidates(db,self.cid,1,paraphrase),[])
            self.assertEqual(vectors.candidates(db,self.cid,1,[0.8,0.6]+[0.0]*1534,model),[eid])
            unrelated = '银叶馆 展厅票价'
            self.assertEqual(channel.lexical_candidates(db,self.cid,1,unrelated),[eid])
            distractor = [0.55,(1-0.55**2)**0.5]+[0.0]*1534
            self.assertEqual(vectors.candidates(db,self.cid,1,distractor,model),[])
            self.assertEqual(vectors.candidates(db,self.cid,1,distractor,model,min_cosine=0.446),[eid])
            self.assertEqual(vectors.hybrid(db,self.cid,1,unrelated,
                                            query_vector=distractor,model_version=model)['blocks'],[])
            self.assertEqual([x for block in vectors.hybrid(db,self.cid,1,unrelated)['blocks']
                              for x in block['entryIds']],[eid])
            # Re-embedding a second query must not change the corpus of the first.
            vectors.store(db,self.cid,1,eid,model,fingerprint,[0.0,1.0]+[0.0]*1534)
            self.assertEqual(channel.lexical_candidates(db,self.cid,1,'银叶馆'),[eid])
            self.assertEqual(vectors.candidates(db,self.cid,1,vector,model),[eid])
            result = vectors.hybrid(db,self.cid,1,'银叶馆',query_vector=vector,model_version=model)
            self.assertEqual([x for block in result['blocks'] for x in block['entryIds']],[eid])
            updated = meta.revise(db,eid,self.cid,draft['versionId'],{'synopsis':'仍未安装。批准和夜测通过后才讨论。'},1)
            meta.review_field(db,eid,self.cid,'synopsis',updated['fields']['synopsis']['token'],1)
            meta.publish(db,eid,self.cid,updated['versionId'],1)
            self.assertEqual(vectors.candidates(db,self.cid,1,vector,model),[])
            self.assertEqual(channel.lexical_candidates(db,self.cid,1,'银叶馆'),[eid])
            _, new_fingerprint = vectors._input(db,self.cid,eid)
            new_vector = [0.0,1.0]+[0.0]*1534
            vectors.store(db,self.cid,1,eid,model,new_fingerprint,new_vector)
            self.assertEqual(vectors.candidates(db,self.cid,1,new_vector,model),[eid])
            self.assertEqual(vectors.candidates(db,self.cid,1,vector,model),[])

        calls = []
        def unavailable(request):
            calls.append(request.url.path)
            raise httpx.ConnectError('synthetic unavailable', request=request)
        price = {'provider':'openai_embeddings','model':model,'mode':'standard','checkedAt':'2026-11-16',
                 'sourceUrl':'https://developers.openai.com/api/docs/models/text-embedding-3-small',
                 'inputUsdPerMillion':0.02,'outputUsdPerMillion':0}
        api = FastAPI()
        workspace.install(api,app_module.connect,lambda _: {'id':1},embedding_factory=lambda _: (
            embed.Adapter('synthetic-test-key',transport=httpx.MockTransport(unavailable)),price))
        base = '/api/memory-v2/workspace'
        client = TestClient(api)
        failed = client.post(base+'/semantic-search',json={'characterId':self.cid,'modelId':1,
            'query':'银叶馆','approveMaxMicrousd':164})
        # The incremental query task returns its durable unknown state. The
        # failed provider request still keeps its original reservation below.
        self.assertEqual(failed.status_code,200,failed.text)
        self.assertEqual(failed.json()['status'],'unknown',failed.text)
        result = client.get(base+'/recall',params={'characterId':self.cid,'q':'银叶馆'}).json()
        self.assertEqual(result['mode'],'lexical')
        self.assertEqual([x for block in result['blocks'] for x in block['entryIds']],[eid])
        self.assertEqual(calls,['/v1/embeddings'])
        with app_module.connect() as db:
            trial = db.execute("""SELECT b.reserved_microusd,b.spent_estimate_microusd,c.state
                FROM mv3_trial_budgets b JOIN mv3_model_chunks c ON c.trial_id=b.id
                WHERE b.provider='openai_embeddings'""").fetchone()
            self.assertEqual((trial['state'],trial['spent_estimate_microusd']),('unknown',0))
            self.assertGreater(trial['reserved_microusd'],0)

    def test_keyword_fallback_scans_past_one_thousand_rows(self):
        with app_module.connect() as db:
            draft,_ = self.ready(db)
            db.executemany("INSERT INTO mv3_entries(id,character_id,status,created_at) VALUES(?,?,'active','2026-01-01')",
                [(f'!older-{i:04}',self.cid) for i in range(1001)])
            from backend.app import memory_v2_chronicle_embedding as embed
            fallback = embed.cached_search(db,self.cid,1,'休息')
            self.assertEqual(fallback['mode'],'lexical')
            self.assertEqual([x for block in fallback['blocks'] for x in block['entryIds']],[draft['entryId']])

    def test_changed_source_cannot_be_confirmed_and_no_partial_review(self):
        with app_module.connect() as db:
            d=self.draft;v=finite.create(db,self.cid,1,d['entryId'],d['versionId'])
            db.execute('UPDATE messages SET content=? WHERE id=?',('来源改动',self.user))
            with self.assertRaises(ValueError):
                workspace.confirm_narrative(db,d['entryId'],workspace.Confirm(characterId=self.cid,
                    expectedVersion=d['versionId'],versionId=v['id'],inputHash=v['input_hash'],outputHash=v['output_hash']),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0],0)

    def test_extractive_digest_not_allowed_through_new_channel(self):
        with app_module.connect() as db:
            d,v=self.ready(db)
            now=datetime(2026,1,9,12,tzinfo=timezone.utc)
            root=next(n for n in p2._tree(db,self.cid,now,480) if d['entryId'] in [m.entry_id for m in n.members])
            digest=p2.create_extractive_digest(db,self.cid,root.id,now=now)
            self.approve(db,digest['id'])
            self.assertIn('今天累了。',digest['body'])
            result=channel.project(db,self.cid,1,[d['entryId']],now=now)
            self.assertEqual(result['blocks'][0]['projectionKind'],'entry_synopsis')
            self.assertNotIn('摘录式摘要',str(result));self.assertNotIn('RAW',str(result))

    def test_actual_chat_tool_decision_stores_only_refs_and_rechecks_before_request(self):
        from backend.app import memory_v2_chronicle_tools as tools
        with app_module.connect() as db:
            d,v=self.ready(db)
            conv=db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) VALUES(?,'tool test','now','now')",(self.cid,)).lastrowid
            uid=db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','请查休息','now')",(conv,)).lastrowid
            aid=db.execute("""INSERT INTO messages(conversation_id,role,content,created_at,origin_user_message_id,
                origin_user_content_sha256,origin_character_id) VALUES(?,'assistant','','now',?,?,?)""",
                (conv,uid,app_module.tool_origin_fingerprint('请查休息'),self.cid)).lastrowid
            run=db.execute("""INSERT INTO tool_runs(server_id,tool_name,arguments,status,confirmed,created_at,
                conversation_id,assistant_message_id,provider_call_id) VALUES(?,?,?,'pending_confirmation',0,'now',?,?,'call-local')""",
                (tools.SERVER,tools.NAMES[1],json.dumps(dict(entryId=d['entryId'],offset=0,versionId='')),conv,aid)).lastrowid
        with patch.dict(os.environ,{'YOSAKI_CHRONICLE_CONTEXT':'1'}), patch.object(app_module,'current_session',return_value={'id':1,'username':'test'}):
            client=TestClient(app_module.app)
            response=client.post(f'/api/chat/tool-runs/{run}/decision',json={'approve':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['result']['text'],v['body'])
            duplicate=client.post(f'/api/chat/tool-runs/{run}/decision',json={'approve':True})
            self.assertEqual(duplicate.status_code,409)
            with app_module.connect() as db:
                row=db.execute("SELECT * FROM messages WHERE conversation_id=? AND role='tool'",(conv,)).fetchone()
                self.assertNotIn('今天累了',row['content'])
                hydrated=tools.refresh_history(db,self.cid,1,[row])
                self.assertIn('今天累了',hydrated[0]['content'])
                db.execute('UPDATE messages SET content=? WHERE id=?',('REVOKED TEXT',self.user))
                hidden=tools.refresh_history(db,self.cid,1,[row])
                self.assertNotIn('今天累了',hidden[0]['content'])
                self.assertNotIn('REVOKED TEXT',hidden[0]['content'])
                self.assertEqual(json.loads(hidden[0]['content'])['state'],'unavailable')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role=\'tool\'',(conv,)).fetchone()[0],1)

    def test_labels_actual_endpoint_manual_tokens_and_no_keyword_fabrication(self):
        from backend.app import memory_v2_vocabulary as vocab
        with app_module.connect() as db:
            term=vocab.write(db,self.cid,'tag','休息','zh',1)
        api=FastAPI();workspace.install(api,app_module.connect,lambda req:{'id':1})
        client=TestClient(api); path='/api/memory-v2/workspace/entries/'+self.draft['entryId']+'/labels'
        payload=dict(characterId=self.cid,expectedVersion=self.draft['versionId'],categories=[],
                     tags=[term['vocabulary_id']],keywords=['累'],sourceOrdinal=0)
        response=client.post(path,json=payload);self.assertEqual(response.status_code,200,response.text)
        with app_module.connect() as db:
            row=db.execute("SELECT provenance,locked FROM mv3_entry_fields WHERE entry_id=? AND name='tags' ORDER BY created_at DESC LIMIT 1",(self.draft['entryId'],)).fetchone()
            self.assertEqual(row['provenance'],'manual');self.assertEqual(row['locked'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0],0)
        payload.update(expectedVersion=response.json()['versionId'],keywords=['从未出现'])
        response=client.post(path,json=payload);self.assertEqual(response.status_code,409)
        self.assertIn('literal_keyword_not_in_source',response.text)

    def test_date_correction_preserves_raw_review_history_requires_republish(self):
        from backend.app import memory_v2_chronicle_dates as dates
        with app_module.connect() as db:
            dates.init_db(db)
            d,v=self.ready(db)
            original=[tuple(r) for r in db.execute('SELECT id,content,created_at FROM messages ORDER BY id')]
            checks=db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0]
            changed=dates.save(db,self.cid,1,d['entryId'],d['versionId'],'2025-05-01','用户核实日期')
            self.assertEqual(changed['timeEvidence'][0]['occurredDate'],'2025-05-01')
            self.assertEqual(original,[tuple(r) for r in db.execute('SELECT id,content,created_at FROM messages ORDER BY id')])
            self.assertEqual(checks,db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0])
            with self.assertRaises(ValueError): meta.read_active(db,d['entryId'],self.cid)
            self.assertIsNone(derived._validity(db,v['id'],self.cid,set()))
            meta.publish(db,d['entryId'],self.cid,changed['versionId'],1)
            self.assertEqual(meta.read_active(db,d['entryId'],self.cid)['occurredAt'],['2025-05-01','2025-05-01'])
            with self.assertRaises(ValueError): dates.save(db,self.cid,1,d['entryId'],d['versionId'],None,'冲突')

    def test_embedding_actual_transport_ledger_cache_and_no_second_request(self):
        import httpx
        from backend.app import memory_v2_chronicle_embedding as embed, memory_v2_chronicle_vectors as vec
        from backend.app import memory_v2_jobs as jobs,memory_v2_m36_trial as trial,memory_v2_processing_schema as schema
        with app_module.connect() as db:
            d,v=self.ready(db);jobs.init_db(db);trial.init_db(db);vec.init_db(db);db.commit();schema.migrate(db)
            budget=trial.create_trial(db,self.cid,embed.MODEL,{'provider':'openai_embeddings','model':embed.MODEL,'mode':'standard','checkedAt':'2026-11-16','sourceUrl':'https://developers.openai.com/api/docs/models/text-embedding-3-small','inputUsdPerMillion':0.02,'outputUsdPerMillion':0},max_requests=2,max_microusd=1000)
        calls=[]
        def respond(req):
            payload=json.loads(req.content);calls.append(payload)
            return httpx.Response(200,json={'model':embed.MODEL,'data':[{'index':i,'embedding':[1.0]+[0.0]*1535} for i in range(len(payload['input']))],'usage':{'prompt_tokens':20,'total_tokens':20}})
        adapter=embed.Adapter('synthetic-test-key',transport=httpx.MockTransport(respond))
        a=embed.run(app_module.connect,self.cid,1,'需要歇一会',budget,adapter)
        b=embed.run(app_module.connect,self.cid,1,'需要歇一会',budget,adapter)
        self.assertEqual(len(calls),1);self.assertEqual(a['mode'],'hybrid');self.assertEqual(a['blocks'],b['blocks'])
        with app_module.connect() as db:
            constrained=embed.cached_search(db,self.cid,1,'需要歇一会',budget_tokens=0,limit=1)
            self.assertEqual((constrained['mode'],constrained['blocks']),('hybrid',[]))
            row=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(budget,)).fetchone()
            self.assertEqual(row['spent_estimate_microusd'],1);self.assertEqual(row['reserved_microusd'],0)
            self.assertEqual(row['dispatched_requests'],1)
        adapter.close()

    def test_embedding_expired_worker_settles_without_vector(self):
        import httpx
        from backend.app import memory_v2_chronicle_embedding as embed, memory_v2_chronicle_vectors as vec
        from backend.app import memory_v2_jobs as jobs,memory_v2_m36_trial as trial,memory_v2_processing_schema as schema
        with app_module.connect() as db:
            self.ready(db);jobs.init_db(db);trial.init_db(db);vec.init_db(db);db.commit();schema.migrate(db)
            budget=trial.create_trial(db,self.cid,embed.MODEL,{'provider':'openai_embeddings','model':embed.MODEL,'mode':'standard','checkedAt':'synthetic','sourceUrl':'synthetic','inputUsdPerMillion':0.02,'outputUsdPerMillion':0},max_requests=1,max_microusd=1000)
        def expired(req):
            data=json.loads(req.content)
            with app_module.connect() as db: db.execute("UPDATE mv3_jobs SET lease_until=0 WHERE kind='A3_PROCESSING'")
            return httpx.Response(200,json={'model':embed.MODEL,'data':[{'index':i,'embedding':[1.0]+[0.0]*1535} for i in range(len(data['input']))],'usage':{'prompt_tokens':20,'total_tokens':20}})
        adapter=embed.Adapter('synthetic',transport=httpx.MockTransport(expired))
        with self.assertRaisesRegex(ValueError,'embedding_rejected'):embed.run(app_module.connect,self.cid,1,'需要休息',budget,adapter)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_vectors').fetchone()[0],0)
            t=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(budget,)).fetchone()
            self.assertEqual(t['spent_estimate_microusd'],1);self.assertEqual(t['reserved_microusd'],0)
        adapter.close()

    def test_maintenance_restart_preserves_manual_and_creates_no_remote_requests(self):
        from backend.app import memory_v2_chronicle_processing as upkeep,memory_v2_chronicle_schema as schema
        with app_module.connect() as db:
            db.commit();schema.install(db)
        first=upkeep.maintain(app_module.connect,self.cid,1)
        second=upkeep.maintain(app_module.connect,self.cid,1)
        self.assertEqual(first['providerRequests'],0);self.assertEqual(second['providerRequests'],0)
        self.assertEqual(first['entries'][0]['status'],'prepared',first)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0],0)
            v=db.execute("SELECT COUNT(*) FROM mv3_versions WHERE rule_version=?",(finite.RULE,)).fetchone()[0]
            self.assertEqual(v,1)

    def test_explicit_schema_gate_is_readonly_and_rejects_partial_install(self):
        from backend.app import memory_v2_chronicle_schema as schema, memory_v2_chronicle_gate as gate
        with app_module.connect() as db:
            db.commit()
            self.assertEqual(schema.install(db)['status'],'ready')
            before=db.total_changes
            self.assertEqual(schema.install(db)['status'],'ready')
            self.assertEqual(gate.verify(db)['readOnly'],True)
            after=db.total_changes
            gate.verify(db)
            self.assertEqual(db.total_changes,after)
            db.execute('SAVEPOINT damaged')
            db.execute('DROP TABLE mv3_chronicle_dates')
            with self.assertRaisesRegex(ValueError,'explicit_migration_required'): gate.verify(db)
            db.execute('ROLLBACK TO damaged');db.execute('RELEASE damaged')
            self.assertEqual(gate.verify(db)['status'],'ready')
