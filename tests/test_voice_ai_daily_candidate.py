import json
import hashlib
import importlib
import os
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from scripts.voice_ai_chat_candidate_fixture import Fixture


class DailyCandidate(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-ai-daily-candidate-')
        self.f=Fixture(Path(self.temp.name))
    def tearDown(self):
        self.f.close();self.temp.cleanup()
    def enable(self):
        r=self.f.client.get('/api/voice/ai-http/settings');self.assertEqual(r.status_code,200,r.text)
        old=r.json();self.assertFalse(old['enabled'])
        r=self.f.client.put('/api/voice/ai-http/settings',json={'enabled':True,'model_profile_id':self.f.model_id,
            'expected_revision':old['revision'],'confirm_planning':True})
        self.assertEqual(r.status_code,200,r.text)
    def wait(self,identity):
        for _ in range(100):
            r=self.f.client.get('/api/voice/ai-http/plans/'+identity)
            self.assertEqual(r.status_code,200,r.text)
            value=r.json()
            if value['bridge_state'] not in ('planning','synthesizing'):return value
            time.sleep(.02)
        self.fail('synthetic_pipeline_not_finished')
    def test_actual_chat_receipt_then_plan_http_and_refresh(self):
        self.enable();saved=self.f.chat();message=saved['message_id']
        state=self.f.client.get(f'/api/voice/ai-http/messages/{message}/state')
        self.assertEqual(state.status_code,200,state.text);self.assertTrue(state.json()['eligible'],state.text)
        r=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={})
        self.assertEqual(r.status_code,200,r.text);identity=r.json()['id']
        value=self.wait(identity);self.assertEqual(value['bridge_state'],'completed',value)
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,1))
        for _ in range(2):self.assertEqual(self.f.client.get(value['audio'][0]['url']).status_code,200)
        again=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={})
        self.wait(again.json()['id']);self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))

    def test_old_completed_message_has_no_receipt_and_normal_preview_survives(self):
        self.enable()
        with self.f.main.connect() as db:
            conv=db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) VALUES(1,'legacy',?,?)",(self.f.main.now(),self.f.main.now())).lastrowid
            message=db.execute("INSERT INTO messages(conversation_id,role,content,model,created_at,generation_status) VALUES(?,'assistant','今夜は静かだ。','legacy',?,'completed')",(conv,self.f.main.now())).lastrowid
        state=self.f.client.get(f'/api/voice/ai-http/messages/{message}/state').json()
        self.assertFalse(state['eligible']);self.assertEqual(state['reason'],'complete_display_binding_required')
        self.assertEqual(self.f.client.get(f'/api/voice/messages/{message}/speech-preview').status_code,200)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(0,0))

    def test_default_off_explicit_selected_profile_and_settings_revision(self):
        old=self.f.client.get('/api/voice/ai-http/settings').json();self.assertFalse(old['enabled'])
        bad={'enabled':True,'model_profile_id':self.f.model_id,'expected_revision':0,'confirm_planning':False}
        self.assertEqual(self.f.client.put('/api/voice/ai-http/settings',json=bad).status_code,422)
        bad['confirm_planning']=True
        self.assertEqual(self.f.client.put('/api/voice/ai-http/settings',json=bad).status_code,200)
        self.assertEqual(self.f.client.put('/api/voice/ai-http/settings',json=bad).status_code,409)
        self.assertEqual(self.f.client.get('/api/voice/ai-http/settings').json()['model_profile_id'],self.f.model_id)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(0,0))

    def test_invalid_planning_falls_back_once_and_keeps_complete_text(self):
        self.enable();message=self.f.chat()['message_id'];self.f.scenario='invalid'
        r=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={});value=self.wait(r.json()['id'])
        self.assertEqual(value['bridge_state'],'completed');self.assertEqual(value['preview']['effective_mode'],'none')
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))
        with self.f.main.connect() as db:
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=?',(message,)).fetchone()[0],value['preview']['request_text'])

    def test_unknown_planning_stops_without_tts_or_replanning(self):
        self.enable();message=self.f.chat()['message_id'];self.f.scenario='unknown'
        r=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={});identity=r.json()['id'];value=self.wait(identity)
        self.assertEqual(value['bridge_state'],'failed_unknown')
        self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={});self.wait(identity)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,0))

    def test_edit_adds_version_and_original_audio_remains_readable(self):
        self.enable();message=self.f.chat()['message_id']
        r=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={});identity=r.json()['id'];first=self.wait(identity)
        old=first['audio'][0]['url']
        r=self.f.client.patch('/api/voice/ai-http/plans/'+identity,json={'expected_revision':1,'annotations':[]})
        self.assertEqual(r.status_code,200,r.text)
        self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={});second=self.wait(identity)
        self.assertEqual(second['revision'],2);self.assertEqual(len(second['cached_versions']),2)
        self.assertEqual(self.f.client.get(old).status_code,200)
        self.assertNotEqual(old,second['audio'][0]['url'])
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,2))

    def test_cancel_before_planning_finishes_does_not_dispatch_tts(self):
        self.enable();message=self.f.chat()['message_id'];self.f.scenario='hold'
        r=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={});identity=r.json()['id']
        self.assertTrue(self.f.entered.wait(1))
        self.assertEqual(self.f.client.delete('/api/voice/ai-http/plans/'+identity).status_code,200)
        self.f.release.set();value=self.wait(identity)
        self.assertEqual(value['bridge_state'],'cancelled');self.assertEqual(self.f.tts_count,0)

    def test_completion_receipt_bound_to_generation_and_source_changes_rejected(self):
        self.enable();saved=self.f.chat();message=saved['message_id']
        with self.f.main.connect() as db:
            row=db.execute('SELECT * FROM voice_ai_completion_receipts WHERE message_id=?',(message,)).fetchone()
            self.assertEqual(row['generation_id'],saved['generation_id'])
            db.execute("UPDATE messages SET content='変わった本文だ。' WHERE id=?",(message,))
        state=self.f.client.get(f'/api/voice/ai-http/messages/{message}/state').json()
        self.assertFalse(state['eligible']);self.assertEqual((self.f.plan_count,self.f.tts_count),(0,0))

    def test_full_product_snapshot_migrate_restore_and_disabled_compatibility(self):
        from backend.app.voice.ai_daily import migrate,TABLES
        from backend.app.voice import audio_cache,auto_budget,manager
        from scripts.voice_ai_http_lab import MP3,BODY
        root=Path(self.temp.name)
        conv=self.f.client.post('/api/conversations',json={'character_id':1,'title':'legacy snapshot'}).json()['id']
        with self.f.main.connect() as db:
            message=db.execute("INSERT INTO messages(conversation_id,role,content,model,created_at,generation_status) VALUES(?,'assistant',?,'legacy',?,'completed')",(conv,BODY,self.f.main.now())).lastrowid
        context=manager.speech_context(self.f.main.connect,message,1)
        _,cache,attempt=audio_cache.claim(self.f.main.connect,1,'synthetic-voice',3,'ja',BODY,set())
        audio_cache.publish(self.f.main.connect,cache,attempt,MP3)
        with self.f.main.connect() as db:
            db.execute('INSERT INTO voice_message_audio(message_id,cache_id,sequence,source_sha256,profile_revision,created_at) VALUES(?,?,0,?,3,?)',(message,cache,context['digest'],audio_cache.stamp()))
        auto_budget.reserve(self.f.main.connect,self.f.main.session_digest(self.f.client.cookies.get('yosaki_session')),1,14,first_request=True)
        source=sqlite3.connect(self.f.main.DB);copy=sqlite3.connect(root/'migration.sqlite3');source.backup(copy);source.close()
        for name in ['annotation_audio','annotation_operations','annotation_revisions','annotation_plans',
                     'voice_ai_completion_receipts','voice_ai_owner_settings']:copy.execute('DROP TABLE '+name)
        copy.commit()
        names=[r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        def rows(db):return {n:db.execute('SELECT * FROM "'+n+'"').fetchall() for n in names}
        before=rows(copy)
        copy.execute('BEGIN IMMEDIATE');migrate(copy);copy.commit()
        self.assertEqual(rows(copy),before)
        restored=sqlite3.connect(root/'restored.sqlite3');copy.backup(restored)
        self.assertEqual(rows(restored),before)
        self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(restored.execute('PRAGMA foreign_key_check').fetchall(),[])
        restored.close();copy.close()
        # Reopen actual main with the feature off; no rollback of data is needed.
        self.f.close()
        with patch.dict(os.environ,{'YOSAKI_DATA_DIR':str(root),'YOSAKI_VOICE_AI_HTTP_ENABLED':'0',
                                    'YOSAKI_VOICE_ENABLED':'1','YOSAKI_BACKGROUND_TASKS_ENABLED':'0'}):
            module=importlib.reload(self.f.main)
            with TestClient(module.app) as client:
                self.assertEqual(client.get('/health').status_code,200)
                login=client.post('/api/auth/login',json={'username':'synthetic','password':'synthetic-only-password-2026'})
                self.assertEqual(login.status_code,200)
                self.assertFalse(client.get('/api/voice/ai-http/settings').json()['available'])
                self.assertEqual(client.get(f'/api/voice/messages/{message}/speech-preview').status_code,200)
                cached=client.get(f'/api/voice/messages/{message}/audio?mode=none')
                self.assertEqual(cached.status_code,200,cached.text)
                self.assertEqual(len(cached.json()['audio']),1,cached.text)
                self.assertEqual(client.get(cached.json()['audio'][0]['url']).status_code,200)
                self.assertEqual((self.f.plan_count,self.f.tts_count),(0,0))

    def test_backup_copy_removes_ai_runtime_and_disables_ai_opt_in(self):
        self.enable();message=self.f.chat()['message_id']
        identity=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={}).json()['id']
        self.wait(identity)
        source=sqlite3.connect(self.f.main.DB);snapshot=sqlite3.connect(Path(self.temp.name)/'redacted.sqlite3')
        source.backup(snapshot);source.close()
        from backend.app.voice.backup_policy import sanitize_voice_snapshot
        sanitize_voice_snapshot(snapshot)
        for table in ['annotation_plans','annotation_operations','annotation_revisions','annotation_audio','voice_ai_completion_receipts']:
            self.assertEqual(snapshot.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        self.assertEqual(snapshot.execute('SELECT enabled FROM voice_ai_owner_settings').fetchone()[0],0)
        self.assertEqual(snapshot.execute('SELECT content FROM messages WHERE id=?',(message,)).fetchone()[0],self.f.tts_requests[0]['inputs'][0]['text'].removeprefix('[thoughtful] '))
        snapshot.close()
        self.assertEqual(self.f.client.get('/api/voice/ai-http/settings').json()['enabled'],True)

    def test_real_chat_display_rejection_does_not_mint_permission(self):
        self.enable();self.f.scenario='chat_restricted'
        r=self.f.client.post('/api/chat/stream',json={'character_id':1,'message':'短い日本語で。','model_profile_id':self.f.model_id})
        self.assertIn('展示控制',r.text)
        with self.f.main.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_ai_completion_receipts').fetchone()[0],0)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(0,0))

    def test_cancel_then_late_tts_has_no_ready_audio(self):
        self.enable();message=self.f.chat()['message_id'];self.f.scenario='late_tts'
        identity=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={}).json()['id']
        self.assertTrue(self.f.tts_entered.wait(1))
        self.f.client.delete('/api/voice/ai-http/plans/'+identity)
        with self.f.main.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM annotation_audio WHERE state='ready'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT generation_status FROM messages WHERE id=?',(message,)).fetchone()[0],'completed')
        self.assertEqual(self.f.tts_count,1)


class Migration(unittest.TestCase):
    def test_explicit_transaction_six_tables_backup_restore_and_old_rows_unchanged(self):
        from backend.app.voice.ai_daily import migrate,TABLES
        from backend.app.voice.annotation_planner import fingerprint
        with tempfile.TemporaryDirectory(prefix='yosaki-ai-migration-') as temp:
            db=sqlite3.connect(Path(temp)/'legacy.sqlite3')
            db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY);INSERT INTO users VALUES(1);
                CREATE TABLE characters(id INTEGER PRIMARY KEY);INSERT INTO characters VALUES(1);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY);CREATE TABLE messages(id INTEGER PRIMARY KEY);
                CREATE TABLE model_profiles(id INTEGER PRIMARY KEY);CREATE TABLE old_usage(id INTEGER PRIMARY KEY,used INTEGER);
                INSERT INTO old_usage VALUES(1,137);''')
            before=db.execute('SELECT * FROM old_usage').fetchall()
            with self.assertRaises(ValueError):migrate(db)
            db.execute('BEGIN IMMEDIATE');migrate(db);db.rollback()
            self.assertFalse(TABLES & {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            db.execute('BEGIN IMMEDIATE');migrate(db);db.commit()
            indexes=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")]
            self.assertEqual(len(indexes),6,indexes)
            self.assertTrue(all(n.startswith('sqlite_autoindex_') for n in indexes))
            restored=sqlite3.connect(Path(temp)/'restored.sqlite3');db.backup(restored)
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(restored.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(restored.execute('SELECT * FROM old_usage').fetchall(),before)
            self.assertTrue(TABLES <= {r[0] for r in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            restored.close();db.close()


if __name__=='__main__':unittest.main()
