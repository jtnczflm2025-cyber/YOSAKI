"""Synthetic acceptance; never imports the application against its default data."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx

_import=tempfile.TemporaryDirectory(prefix='yosaki-chat-import-')
os.environ['YOSAKI_DATA_DIR']=_import.name
os.environ['YOSAKI_BACKGROUND_TASKS_ENABLED']='0'
from backend.app import main as m
from backend.app import chat_history as h, chat_requests as r, responses_adapter as ra
from backend.app import chat_api,chat_attachments as attachments,chat_context as context


def endpoint(path,method='POST'):
    return next(route.endpoint for route in m.app.routes if getattr(route,'path','')==path and method in route.methods)


class ChatEnhancement(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-chat-synthetic-')
        self.addCleanup(self.temp.cleanup)
        m.DATA=Path(self.temp.name);m.DB=m.DATA/'yosaki.sqlite3';m.SECRET_FILE=m.DATA/'.secret_key'
        m.init_db()
        with m.connect() as db:
            self.cid=db.execute('SELECT id FROM characters LIMIT 1').fetchone()[0]
            self.conv=db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) VALUES(?,'synthetic',?,?)",(self.cid,m.now(),m.now())).lastrowid
            self.profile=db.execute("INSERT INTO model_profiles(name,base_url,model_name,protocol,created_at,updated_at) VALUES('synthetic','http://127.0.0.1:1/v1','synthetic','openai_responses',?,?)",(m.now(),m.now())).lastrowid
        self.wires=[]
        self.events=[{'type':'response.output_text.delta','delta':'synthetic answer'},
            {'type':'response.completed','response':{'id':'synthetic-response','model':'returned-model','usage':{'input_tokens':12,'output_tokens':8},'output':[]}}]
        original=httpx.AsyncClient
        def handler(request):
            self.wires.append(json.loads(request.content))
            return httpx.Response(200,content=''.join('data: '+json.dumps(e)+'\n\n' for e in self.events).encode())
        self.client_patch=patch.object(m.httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(handler)))
        self.client_patch.start();self.addCleanup(self.client_patch.stop)

    def seed(self,role,body):
        with m.connect() as db:
            return db.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',(self.conv,role,body,m.now())).lastrowid

    async def send(self,**kw):
        payload=m.ChatInput(character_id=self.cid,conversation_id=self.conv,model_profile_id=self.profile,message='new input',**kw)
        response=await m.chat_stream(payload)
        return ''.join([part async for part in response.body_iterator])

    async def test_regenerate_preserves_old_answer_and_sends_user_once(self):
        u=self.seed('user','only once');a=self.seed('assistant','old answer')
        result=await self.send(reuse_last_user=True)
        self.assertIn('message_complete',result)
        self.assertEqual(sum(i.get('content')=='only once' for i in self.wires[0]['input']),1)
        with m.connect() as db:
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=?',(a,)).fetchone()[0],'old answer')
            self.assertNotIn(a,[x['id'] for x in h.rows(db,self.conv)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE role="user"').fetchone()[0],1)

    async def test_summary_diagnostics_do_not_inherit_memory_switch(self):
        with m.connect() as db:
            settings={'character_id':self.cid,'memory_enabled':False}
            def states(body=''):
                return {x['channel']:x['state'] for x in context.trace(db,self.conv,settings,[],body,'',{})['channels']}
            self.assertEqual(states()['manual_memory'],'disabled')
            self.assertEqual(states()['conversation_summary'],'not_selected')
            db.execute('INSERT INTO conversation_summaries VALUES(?,?,?,?)',(self.conv,'legacy reference',0,m.now()))
            self.assertEqual(states()['conversation_summary'],'disabled')
            db.execute('UPDATE conversation_summaries SET enabled=1 WHERE conversation_id=?',(self.conv,))
            self.assertEqual(states('legacy reference')['conversation_summary'],'included')
            self.assertEqual(states()['structured_volition'],'not_connected')

    async def test_incomplete_preserves_reliable_usage_and_status(self):
        self.events[-1]['type']='response.incomplete'
        await self.send()
        with m.connect() as db:
            a=db.execute('SELECT * FROM chat_attempts').fetchone()
            self.assertEqual(json.loads(a['usage_json'])['output_tokens'],8)
            self.assertEqual(a['status'],'incomplete')
            self.assertEqual(db.execute('SELECT generation_status FROM messages WHERE role="assistant"').fetchone()[0],'incomplete')

    async def test_failed_response_retains_usage_without_answer(self):
        self.events[-1]['type']='response.failed'
        await self.send()
        with m.connect() as db:
            a=db.execute('SELECT * FROM chat_attempts').fetchone()
            self.assertEqual(json.loads(a['usage_json'])['input_tokens'],12)
            self.assertEqual(a['status'],'failed')

    async def test_concurrent_claim_precedes_second_user_save(self):
        first=await m.chat_stream(m.ChatInput(character_id=self.cid,conversation_id=self.conv,message='one',model_profile_id=self.profile))
        with self.assertRaises(m.HTTPException) as caught:
            await self.send()
        self.assertEqual(caught.exception.status_code,409)
        with m.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE role="user"').fetchone()[0],1)
        _=[p async for p in first.body_iterator]

    async def test_unverified_plan_limit_override_refused_before_save(self):
        with m.connect() as db:db.execute("UPDATE model_profiles SET protocol='chatgpt_plan' WHERE id=?",(self.profile,))
        with self.assertRaises(m.HTTPException):await self.send(overrides={'max_tokens':5})
        self.assertFalse(self.wires)
        with m.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    async def test_unknown_usage_is_not_zero(self):
        self.events[-1]['response'].pop('usage')
        await self.send()
        with m.connect() as db:
            totals=r.totals(db,self.conv)
            self.assertEqual(totals['tokens']['input']['unknown'],1)
            self.assertEqual(totals['tokens']['input']['provided'],0)

    async def test_snapshot_is_exact_wire_except_private_state(self):
        await self.send()
        with m.connect() as db:
            a=db.execute('SELECT * FROM chat_attempts').fetchone()
            self.assertEqual(json.loads(a['snapshot_json']),self.wires[0])
            self.assertEqual(a['returned_model'],'returned-model')

    def test_branch_shares_prefix_and_does_not_inherit_summary_or_temporary(self):
        u=self.seed('user','common');a=self.seed('assistant','future')
        with m.connect() as db:
            db.execute('INSERT INTO conversation_summaries VALUES(?,?,1,?)',(self.conv,'future summary',m.now()))
            db.execute("INSERT INTO memories(character_id,conversation_id,scope,content,enabled,created_at,updated_at) VALUES(?,?,'temporary','future temporary',1,?,?)",(self.cid,self.conv,m.now(),m.now()))
            db.execute("INSERT INTO memories(character_id,scope,content,enabled,created_at,updated_at) VALUES(?,'long','shared role fact',1,?,?)",(self.cid,m.now(),m.now()))
            branch=h.fork(db,self.conv,u,m.now())
            self.assertEqual([x['id'] for x in h.rows(db,branch)],[u])
            memories,summary,_=m.memory_context(db,self.cid,branch)
            self.assertEqual(summary,'')
            self.assertEqual([x['content'] for x in memories],['shared role fact'])
            with h.reading(db,branch):
                self.assertTrue(h.source_allowed(db,u,self.conv))
                self.assertFalse(h.source_allowed(db,a,self.conv))
            self.assertEqual(db.execute('SELECT content FROM conversation_summaries WHERE conversation_id=?',(self.conv,)).fetchone()[0],'future summary')

    def test_usage_subsets_are_not_added_to_totals(self):
        self.assertEqual(r.normalize_usage({'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':90},'output_tokens_details':{'reasoning_tokens':8}}),
            {'input':100,'output':20,'cache_read':90,'cache_write':None,'reasoning':8})

    def test_adapter_incomplete_and_failed_return_usage(self):
        for kind in ('response.incomplete','response.failed'):
            state=ra.StreamState()
            self.assertEqual(state.normalize({'type':kind,'response':{'usage':{'output_tokens':7}}})['usage'],{'output_tokens':7})

    async def test_preview_is_read_only_and_matches_sent_wire(self):
        self.seed('user','past');self.seed('assistant','past answer')
        with m.connect() as db:before='\n'.join(db.iterdump())
        preview=m.context_preview(m.PreviewInput(character_id=self.cid,conversation_id=self.conv,model_profile_id=self.profile,message='new input',overrides={'max_tokens':777}))
        with m.connect() as db:self.assertEqual(before,'\n'.join(db.iterdump()))
        await self.send(overrides={'max_tokens':777})
        self.assertEqual(preview['serialized'],self.wires[0])
        self.assertEqual(self.wires[0]['max_output_tokens'],777)

    async def test_edit_forks_and_does_not_copy_consumption(self):
        u=self.seed('user','original');a=self.seed('assistant','old answer')
        changed=endpoint('/api/chat/messages/{mid}/edit')(u,chat_api.Action(conversation_id=self.conv,expected_head=a,content='corrected'))
        with m.connect() as db:
            self.assertEqual([r['content'] for r in h.rows(db,self.conv)],['original','old answer'])
            self.assertEqual([r['content'] for r in h.rows(db,changed['conversation_id'])],['corrected'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_attempts').fetchone()[0],0)

    def test_inherited_delete_and_undo_are_path_local(self):
        u=self.seed('user','common');a=self.seed('assistant','common answer')
        with m.connect() as db:branch=h.fork(db,self.conv,a,m.now())
        endpoint('/api/chat/messages/{mid}/delete')(u,chat_api.Action(conversation_id=branch,expected_head=a))
        with m.connect() as db:
            self.assertEqual(h.rows(db,branch),[])
            self.assertEqual([r['id'] for r in h.rows(db,self.conv)],[u,a])
        endpoint('/api/chat/messages/{mid}/restore')(u,chat_api.Action(conversation_id=branch,expected_head=0))
        with m.connect() as db:self.assertEqual([r['id'] for r in h.rows(db,branch)],[u,a])

    async def test_candidate_selection_keeps_usage_once(self):
        await self.send()
        with m.connect() as db:old=h.rows(db,self.conv)[-1]['id']
        await self.send(reuse_last_user=True)
        with m.connect() as db:new=h.rows(db,self.conv)[-1]['id']
        details=endpoint('/api/chat/messages/{mid}/details','GET')(new,self.conv)
        self.assertIn(old,[v['id'] for v in details['versions']])
        endpoint('/api/chat/messages/{mid}/select-version')(old,chat_api.Action(conversation_id=self.conv,expected_head=new))
        with m.connect() as db:
            self.assertEqual(h.rows(db,self.conv)[-1]['id'],old)
            self.assertEqual(r.totals(db,self.conv)['tokens']['input']['known'],24)

    async def test_translation_cache_and_model_configuration_invalidation(self):
        u=self.seed('user','translation synthetic')
        payload=chat_api.Auxiliary(conversation_id=self.conv,expected_head=u,model_id=self.profile,language='English')
        route=endpoint('/api/chat/messages/{mid}/translate')
        first=await route(u,payload);second=await route(u,payload)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(self.wires),1)
        with m.connect() as db:db.execute('UPDATE model_profiles SET max_tokens=1000 WHERE id=?',(self.profile,))
        await route(u,payload);self.assertEqual(len(self.wires),2)
        with m.connect() as db:self.assertEqual(len(h.rows(db,self.conv)),1)

    async def test_summary_coverage_invalidates_on_path_removal(self):
        u=self.seed('user','old fact');self.seed('assistant','old condition');last=self.seed('user','corrected fact')
        await endpoint('/api/chat/conversations/{cid}/summary')(self.conv,chat_api.Auxiliary(conversation_id=self.conv,expected_head=last,model_id=self.profile))
        with m.connect() as db:
            self.assertIsNotNone(context.summary(db,self.conv))
            self.assertEqual([r['id'] for r in context.uncovered(db,self.conv,h.rows(db,self.conv))],[last])
        endpoint('/api/chat/messages/{mid}/delete')(u,chat_api.Action(conversation_id=self.conv,expected_head=last))
        with m.connect() as db:self.assertIsNone(context.summary(db,self.conv))

    async def test_failed_summary_preserves_original_history(self):
        self.seed('user','one');self.seed('assistant','two');last=self.seed('user','three')
        self.events[-1]['type']='response.failed'
        with self.assertRaises(m.HTTPException):await endpoint('/api/chat/conversations/{cid}/summary')(self.conv,chat_api.Auxiliary(conversation_id=self.conv,expected_head=last,model_id=self.profile))
        with m.connect() as db:
            self.assertEqual(len(h.rows(db,self.conv)),3)
            self.assertIsNone(context.summary(db,self.conv))
            self.assertEqual(r.totals(db,self.conv)['tokens']['input']['known'],12)

    def test_legacy_summary_requires_explicit_branch_selection(self):
        u=self.seed('user','one')
        with m.connect() as db:
            db.execute('INSERT INTO conversation_summaries VALUES(?,?,1,?)',(self.conv,'legacy',m.now()))
            branch=h.fork(db,self.conv,u,m.now())
            self.assertEqual(m.memory_context(db,self.cid,branch)[1],'')
            self.assertEqual(m.memory_context(db,self.cid,self.conv)[1],'legacy')
        view=endpoint('/api/chat/conversations/{cid}/state','GET')(branch)
        self.assertEqual(view['legacy_summary']['content'],'legacy')
        endpoint('/api/chat/conversations/{cid}/choose-legacy-summary')(branch,chat_api.Action(conversation_id=branch,expected_head=u,content='legacy'))
        with m.connect() as db:self.assertEqual(m.memory_context(db,self.cid,branch)[1],'legacy')

    async def test_text_attachment_replayed_on_regeneration_and_preview(self):
        with m.connect() as db:f=attachments.save(db,self.cid,'facts.md',b'synthetic attachment fact',m.now())
        preview=m.context_preview(m.PreviewInput(character_id=self.cid,conversation_id=self.conv,model_profile_id=self.profile,message='new input',attachment_ids=[f['id']]))
        await self.send(attachment_ids=[f['id']]);await self.send(reuse_last_user=True)
        self.assertEqual(preview['serialized'],self.wires[0])
        self.assertIn('synthetic attachment fact',json.dumps(self.wires[1]))
        self.assertEqual(json.dumps(self.wires[1]).count('synthetic attachment fact'),1)

    async def test_image_is_real_native_block_and_unknown_model_refused(self):
        from PIL import Image
        import io,base64
        stream=io.BytesIO();Image.new('RGB',(4,4),'red').save(stream,format='PNG')
        with m.connect() as db:f=attachments.save(db,self.cid,'synthetic.png',stream.getvalue(),m.now())
        with self.assertRaises(m.HTTPException):await self.send(attachment_ids=[f['id']])
        with m.connect() as db:db.execute('INSERT INTO chat_model_capabilities VALUES(?,1,?,?)',(self.profile,'synthetic test only',m.now()))
        await self.send(attachment_ids=[f['id']])
        image=[p for i in self.wires[0]['input'] for p in (i['content'] if isinstance(i.get('content'),list) else []) if p.get('type')=='input_image'][0]
        self.assertEqual(base64.b64decode(image['image_url'].split(',')[1]),stream.getvalue())
        with m.connect() as db:
            snapshot=db.execute('SELECT snapshot_json FROM chat_attempts').fetchone()[0]
            self.assertNotIn(base64.b64encode(stream.getvalue()).decode(),snapshot)

    async def test_vision_declaration_revoked_when_channel_changes(self):
        from PIL import Image
        import io
        data=io.BytesIO();Image.new('RGB',(2,2),'blue').save(data,format='PNG')
        with m.connect() as db:
            f=attachments.save(db,self.cid,'synthetic.png',data.getvalue(),m.now())
            db.execute('INSERT INTO chat_model_capabilities VALUES(?,1,?,?)',(self.profile,'synthetic evidence',m.now()))
        payload=m.ModelProfileInput(name='renamed only',base_url='http://127.0.0.1:1/v1',model_name='synthetic',protocol='openai_responses')
        m.update_model(self.profile,payload)
        with m.connect() as db:self.assertIsNotNone(db.execute('SELECT 1 FROM chat_model_capabilities WHERE model_id=?',(self.profile,)).fetchone())
        payload.model_name='different-model'
        m.update_model(self.profile,payload)
        with self.assertRaises(m.HTTPException):await self.send(attachment_ids=[f['id']])
        with m.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_attachments').fetchone()[0],1)
        self.assertEqual(self.wires,[])

    def test_attachment_worker_format_and_limits(self):
        import io,zipfile
        self.assertEqual(attachments.isolated_parse('synthetic.txt',b'bounded text')[1],'bounded text')
        with self.assertRaises(m.HTTPException):attachments.isolated_parse('fake.png',b'not png')
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml','<!DOCTYPE x><x/>')
        with self.assertRaises(m.HTTPException):attachments.isolated_parse('unsafe.docx',raw.getvalue())

    async def test_snapshot_does_not_return_revoked_memory(self):
        with m.connect() as db:mid=db.execute("INSERT INTO memories(character_id,scope,content,enabled,created_at,updated_at) VALUES(?,'long','synthetic secret reference',1,?,?)",(self.cid,m.now(),m.now())).lastrowid
        await self.send()
        with m.connect() as db:
            aid=db.execute('SELECT id FROM chat_attempts').fetchone()[0]
            db.execute('UPDATE memories SET enabled=0 WHERE id=?',(mid,))
        detail=endpoint('/api/chat/attempts/{aid}','GET')(aid)
        self.assertEqual(detail['snapshot_state'],'eligibility_rejected');self.assertIsNone(detail['snapshot'])

    async def test_duplicate_late_usage_and_wrong_response_cannot_overwrite(self):
        await self.send()
        with m.connect() as db:aid=db.execute('SELECT id FROM chat_attempts').fetchone()[0]
        r.observe(m.connect,aid,self.events[-1]);r.observe(m.connect,aid,self.events[-1])
        r.observe(m.connect,aid,{'response':{'id':'different','usage':{'input_tokens':900,'output_tokens':900}}})
        with m.connect() as db:self.assertEqual(r.totals(db,self.conv)['tokens']['input']['known'],12)
        r.observe(m.connect,aid,{'response':{'id':'synthetic-response','usage':{'input_tokens_details':{'cached_tokens':4}}}})
        with m.connect() as db:
            self.assertEqual(r.totals(db,self.conv)['tokens']['input']['known'],12)
            self.assertEqual(r.totals(db,self.conv)['tokens']['cache_read']['known'],4)
        r.observe(m.connect,aid,{'response':{'id':'synthetic-response','usage':{'input_tokens':99,'output_tokens':99}}})
        with m.connect() as db:
            self.assertEqual(r.totals(db,self.conv)['conflicting_receipts'],1)
            self.assertEqual(r.totals(db,self.conv)['tokens']['input']['unknown'],1)

    def test_dead_worker_is_unknown_and_never_replayed(self):
        with m.connect() as db:
            r.begin(db,'lost',self.conv,self.cid,None,'chat')
            db.execute("UPDATE chat_attempts SET dispatched=1 WHERE id='lost'")
            db.execute("UPDATE chat_attempt_workers SET pid=99999999 WHERE attempt_id='lost'")
            with patch.object(r,'process_identity',return_value=None):h.assert_idle(db,self.conv)
            self.assertEqual(db.execute("SELECT status FROM chat_attempts WHERE id='lost'").fetchone()[0],'unknown')
        self.assertEqual(self.wires,[])

    def test_budget_drops_complete_turn_and_preserves_current_and_system(self):
        messages=[{'role':'system','content':'fixed'}, {'role':'user','content':'x'*1000},{'role':'assistant','content':'y'*1000}, {'role':'user','content':'latest'}, {'role':'system','content':'suffix'}]
        selected,report=context.constrain(messages,[],{'context_limit':1400,'max_tokens':200},{'enabled':True,'trigger_ratio':.75,'target_ratio':.58})
        self.assertEqual([x['content'] for x in selected],['fixed','latest','suffix'])
        self.assertEqual(report['removed_turns'],1)

    async def test_partial_eof_is_unknown_and_public_text_is_preserved(self):
        self.events=self.events[:1]
        await self.send()
        with m.connect() as db:
            row=db.execute('SELECT * FROM chat_attempts').fetchone()
            self.assertEqual(row['status'],'unknown')
            message=db.execute('SELECT * FROM messages WHERE id=?',(row['assistant_message_id'],)).fetchone()
            self.assertEqual(message['content'],'synthetic answer')
            self.assertEqual(message['generation_status'],'incomplete')

    async def test_source_revoked_between_prepare_and_dispatch_is_not_sent(self):
        with m.connect() as db:mid=db.execute("INSERT INTO memories(character_id,scope,content,enabled,created_at,updated_at) VALUES(?,'long','reference',1,?,?)",(self.cid,m.now(),m.now())).lastrowid
        original=r.dispatched
        def revoke(connect,aid):
            with connect() as db:db.execute('UPDATE memories SET enabled=0 WHERE id=?',(mid,))
            return original(connect,aid)
        with patch.object(r,'dispatched',revoke):await self.send()
        self.assertFalse(self.wires)
        with m.connect() as db:self.assertEqual(db.execute('SELECT dispatched FROM chat_attempts').fetchone()[0],0)

    async def test_web_requires_explicit_confirmation_then_continuation(self):
        from backend.app import chat_web
        with m.connect() as db:db.execute("UPDATE model_profiles SET protocol='openai_compatible' WHERE id=?",(self.profile,))
        self.events=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'public-call','type':'function','function':{'name':chat_web.NAME,'arguments':'{"url":"https://example.test"}'}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':10,'completion_tokens':5}}]
        with patch.dict(os.environ,{'YOSAKI_CHAT_WEB_ENABLED':'1'}),patch.object(chat_web,'read',return_value={'text':'synthetic page','source_url':'https://example.test'}) as reader:
            result=await self.send();self.assertIn('tool_batch_waiting',result);reader.assert_not_called()
            with m.connect() as db:run=db.execute('SELECT id FROM tool_runs').fetchone()[0]
            result=await m.decide_chat_tool_run(run,m.ToolDecisionInput(approve=True));self.assertEqual(result['status'],'success');reader.assert_called_once()
            self.assertEqual(len(self.wires),1)
            with self.assertRaises(m.HTTPException):await m.decide_chat_tool_run(run,m.ToolDecisionInput(approve=True))
            self.events=[{'choices':[{'delta':{'content':'page conclusion'},'finish_reason':'stop'}],'usage':{'prompt_tokens':15,'completion_tokens':5}}]
            response=await m.continue_after_tool(run);result=''.join([part async for part in response.body_iterator])
            self.assertIn('message_complete',result);self.assertEqual(len(self.wires),2)

    async def test_v1_v2_backup_preserve_attachment_and_ledger_without_credentials(self):
        import sqlite3
        from backend.app import backup_v2
        data=b'synthetic attachment preserved'
        with m.connect() as db:
            file=attachments.save(db,self.cid,'synthetic.txt',data,m.now())
            db.execute("INSERT INTO users(username,password_hash,created_at) VALUES('synthetic','not-real',?)",(m.now(),))
            db.execute("UPDATE model_profiles SET api_key_encrypted='synthetic-secret' WHERE id=?",(self.profile,))
        # Clear only the synthetic fake key before mocked model auth preparation.
        with m.connect() as db:db.execute('UPDATE model_profiles SET api_key_encrypted=NULL WHERE id=?',(self.profile,))
        await self.send(attachment_ids=[file['id']])
        with m.connect() as db:db.execute("UPDATE model_profiles SET api_key_encrypted='synthetic-secret' WHERE id=?",(self.profile,))
        password='synthetic-backup-password'
        blob=m.build_encrypted_backup(password);db_bytes,_=m.decrypt_backup(blob,password)
        legacy=sqlite3.connect(':memory:');legacy.deserialize(db_bytes)
        self.assertEqual(legacy.execute('SELECT data FROM chat_attachments').fetchone()[0],data)
        self.assertIsNone(legacy.execute('SELECT api_key_encrypted FROM model_profiles').fetchone()[0]);legacy.close()
        root=Path(self.temp.name);backup=root/'synthetic.ybackup'
        backup_v2.create_backup(m.DB,backup,password,root/'work')
        backup_v2.restore_to_directory(backup,password,root/'restored',root/'work')
        restored=sqlite3.connect(root/'restored'/'yosaki.sqlite3')
        try:
            self.assertEqual(restored.execute('SELECT data FROM chat_attachments').fetchone()[0],data)
            self.assertEqual(restored.execute('SELECT COUNT(*) FROM chat_attempts').fetchone()[0],1)
            self.assertIsNone(restored.execute('SELECT api_key_encrypted FROM model_profiles').fetchone()[0])
        finally:restored.close()

    def test_additive_schema_rehearsal_keeps_old_rows_and_source_bytes(self):
        import sqlite3,hashlib
        from scripts.chat_schema_candidate import prepare
        source=Path(self.temp.name)/'legacy.sqlite3';db=sqlite3.connect(source)
        db.executescript('CREATE TABLE characters(id INTEGER PRIMARY KEY);CREATE TABLE conversations(id INTEGER PRIMARY KEY);CREATE TABLE messages(id INTEGER PRIMARY KEY,content TEXT);CREATE TABLE memories(id INTEGER PRIMARY KEY);CREATE TABLE model_profiles(id INTEGER PRIMARY KEY);INSERT INTO messages VALUES(1,"original synthetic row");');db.commit();db.close()
        digest=hashlib.sha256(source.read_bytes()).hexdigest();dest=Path(self.temp.name)/'candidate.sqlite3'
        result=prepare(source,dest);self.assertTrue(result['source_unchanged'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
        db=sqlite3.connect(dest)
        self.assertEqual(db.execute('SELECT content FROM messages').fetchone()[0],'original synthetic row');db.close()
        with self.assertRaises(ValueError):prepare(source,dest)

    async def test_cancel_keeps_public_text_and_already_reliable_usage(self):
        self.seed('user','cancel synthetic')
        with m.connect() as db:r.begin(db,'cancelled-test',self.conv,self.cid,None,'chat')
        r.dispatched(m.connect,'cancelled-test')
        ready=asyncio.Event()
        async def stream():
            r.observe(m.connect,'cancelled-test',{'type':'response.incomplete','response':{'id':'cancel-response','usage':{'input_tokens':12,'output_tokens':3}}})
            yield m.sse('text_delta',{'text':'partial public'})
            ready.set();await asyncio.sleep(10)
        async def consume():return [x async for x in r.tracked(stream(),m.connect,'cancelled-test')]
        task=asyncio.create_task(consume());await ready.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        with m.connect() as db:
            attempt=db.execute("SELECT * FROM chat_attempts WHERE id='cancelled-test'").fetchone()
            self.assertEqual(attempt['status'],'cancelled')
            self.assertEqual(r.totals(db,self.conv)['tokens']['input']['known'],12)
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=?',(attempt['assistant_message_id'],)).fetchone()[0],'partial public')

    def test_scanned_pdf_and_docx_extraction_diagnostics(self):
        import io,zipfile
        from pypdf import PdfWriter
        pdf=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(pdf)
        mime,text,diagnostic=attachments.isolated_parse('scan.pdf',pdf.getvalue())
        self.assertEqual(mime,'application/pdf');self.assertEqual(diagnostic['empty_pages'],[1])
        doc=io.BytesIO()
        with zipfile.ZipFile(doc,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>synthetic DOCX fact</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(attachments.isolated_parse('facts.docx',doc.getvalue())[1],'synthetic DOCX fact')

    async def test_upload_limits_chunked_body_before_framework(self):
        from backend.app.chat_upload_limit import UploadBodyLimit
        called=[];sent=[];size=attachments.MAX_BYTES+128*1024
        async def app(scope,receive,send):called.append(True)
        incoming=iter([{'type':'http.request','body':b'x'*(size-1),'more_body':True},{'type':'http.request','body':b'yy','more_body':False}])
        async def receive():return next(incoming)
        async def send(value):sent.append(value)
        await UploadBodyLimit(app)({'type':'http','method':'POST','path':'/api/chat/attachments','headers':[]},receive,send)
        self.assertFalse(called);self.assertEqual(sent[0]['status'],413)

    def test_new_branch_temporary_note_has_explicit_coverage(self):
        user=self.seed('user','shared');future=self.seed('assistant','after fork')
        with m.connect() as db:branch=h.fork(db,self.conv,user,m.now())
        note=m.create_memory(m.MemoryInput(character_id=self.cid,conversation_id=branch,scope='temporary',content='branch note'))
        with m.connect() as db:
            self.assertIn(note['id'],[row['id'] for row in m.memory_context(db,self.cid,branch)[0]])
            db.execute('INSERT INTO chat_path_exclusions VALUES(?,?,?,?)',(branch,user,user,m.now()))
            self.assertNotIn(note['id'],[row['id'] for row in m.memory_context(db,self.cid,branch)[0]])

    async def test_auto_summary_uses_selected_aux_model_and_is_incremental(self):
        self.seed('user','a'*2000);self.seed('assistant','b'*2000);head=self.seed('user','recent complete turn')
        with m.connect() as db:
            auxiliary=db.execute("INSERT INTO model_profiles(name,base_url,model_name,protocol,context_limit,created_at,updated_at) VALUES('aux','http://127.0.0.1:1/v1','aux-only','openai_responses',32768,?,?)",(m.now(),m.now())).lastrowid
            db.execute('UPDATE model_profiles SET context_limit=5000,max_tokens=200 WHERE id=?',(self.profile,))
        endpoint('/api/chat/characters/{cid}/context-policy','PUT')(self.cid,chat_api.Policy(enabled=True,auto_summary=True,summary_model_id=auxiliary))
        await self.send(expected_head=head)
        self.assertEqual([body['model'] for body in self.wires],['aux-only','synthetic'])
        with m.connect() as db:
            self.assertIsNotNone(context.summary(db,self.conv))
            self.assertEqual(r.totals(db,self.conv)['by_purpose']['summary']['attempts'],1)
        last=self.seed('user','next complete turn')
        await endpoint('/api/chat/conversations/{cid}/summary')(self.conv,chat_api.Auxiliary(conversation_id=self.conv,expected_head=last,model_id=auxiliary))
        wire=json.dumps(self.wires[-1],ensure_ascii=False)
        self.assertIn('原范围摘要',wire);self.assertNotIn('a'*2000,wire)


if __name__=='__main__':unittest.main()
