"""Profile persistence and preview-only overrides; synthetic SQLite, no provider."""
import os
import tempfile
from pathlib import Path
import unittest
from fastapi.testclient import TestClient
_import=tempfile.TemporaryDirectory(prefix='yosaki-model-settings-import-')
os.environ['YOSAKI_DATA_DIR']=_import.name
os.environ['YOSAKI_BACKGROUND_TASKS_ENABLED']='0'
from backend.app import main as m

class ModelSettingsPersistence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-model-settings-')
        self.addCleanup(self.temp.cleanup)
        m.DATA=Path(self.temp.name);m.DB=m.DATA/'yosaki.sqlite3';m.SECRET_FILE=m.DATA/'.secret_key';m.init_db()
        self.client=TestClient(m.app);self.addCleanup(self.client.close)
        self.client.post('/api/auth/setup',json={'username':'synthetic-owner','password':'Synthetic-Only-Password'})
        self.cid=self.client.get('/api/characters').json()[0]['id']
    def payload(self,effort='high',protocol='openai_responses'):
        return dict(name='synthetic-only',base_url='http://127.0.0.1:1/v1',model_name='synthetic-only',protocol=protocol,reasoning_effort=effort)
    def test_save_read_sql_and_reopen_for_both_protocols(self):
        for protocol in ['openai_responses','chatgpt_plan']:
            created=self.client.post('/api/models',json=self.payload(protocol=protocol));self.assertEqual(created.status_code,201)
            mid=created.json()['id']
            for value in ['low','high',None,'medium']:
                response=self.client.patch('/api/models/'+str(mid),json=self.payload(value,protocol));self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['reasoning_effort'],value)
                reread=next(p for p in self.client.get('/api/models').json() if p['id']==mid)
                self.assertEqual(reread['reasoning_effort'],value)
                with m.connect() as db:self.assertEqual(db.execute('SELECT reasoning_effort FROM model_profiles WHERE id=?',(mid,)).fetchone()[0],value)
    def test_rejected_effort_preserves_saved_profile(self):
        mid=self.client.post('/api/models',json=self.payload()).json()['id']
        response=self.client.patch('/api/models/'+str(mid),json=self.payload('unsupported'))
        self.assertEqual(response.status_code,422)
        with m.connect() as db:self.assertEqual(db.execute('SELECT reasoning_effort FROM model_profiles WHERE id=?',(mid,)).fetchone()[0],'high')
    def test_preview_override_clear_and_inherit_never_persist(self):
        mid=self.client.post('/api/models',json=self.payload()).json()['id']
        for override,expected in [({},'high'),({'reasoning_effort':'low'},'low'),({'reasoning_effort':None},None)]:
            response=self.client.post('/api/chat/context-preview',json={'character_id':self.cid,'model_profile_id':mid,'message':'合成预览','overrides':override})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['serialized'].get('reasoning',{}).get('effort'),expected)
            with m.connect() as db:
                self.assertEqual(db.execute('SELECT reasoning_effort FROM model_profiles WHERE id=?',(mid,)).fetchone()[0],'high')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_attempts').fetchone()[0],0)
