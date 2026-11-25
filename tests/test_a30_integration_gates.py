"""Synthetic integration gates; legacy source is immutable baseline evidence."""
import os,subprocess,sys,unittest
from pathlib import Path
from backend.app import memory_v2_jobs as jobs,memory_v2_processing_schema as schema
from test_memory_v2_a3_recovery import database
ROOT=Path(__file__).resolve().parents[1]
class IntegrationGates(unittest.TestCase):
 def test_runtime_test_initialization_does_not_migrate_a3(self):
  script="""
import tempfile, pathlib, sqlite3, uvicorn, os
from unittest.mock import patch
from scripts import yosaki_runtime as r
folder=pathlib.Path(tempfile.mkdtemp(prefix='a3-runtime-gate-'))
config=r.configuration('test',test_dir=folder)
with patch.object(uvicorn,'run') as serve:
 r.run(config,inherited_data_dir=None)
 assert serve.call_count==1
with sqlite3.connect(config.database) as db:
 tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 assert 'users' in tables and not any(x.startswith('mv3_a3') for x in tables)
 assert 'mv3_jobs' not in tables
 assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
assert os.environ['YOSAKI_VOICE_ENABLED']=='0'
"""
  env=dict(os.environ);env.pop('YOSAKI_DATA_DIR',None)
  result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_current_old_entry_rejects_unknown_contract(self):
  db=database()
  try:
   schema.migrate(db)
   db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at,contract_version) VALUES('future',1,'NARRATIVE','h','{}','queued','now','unsupported-future')")
   db.commit();before=list(db.iterdump())
   with self.assertRaisesRegex(ValueError,'a3_executor_required'): jobs.claim(db,'future',1)
   self.assertEqual(before,list(db.iterdump()))
  finally: db.close()
 def test_synthetic_development_disables_background_and_voice(self):
  script="""
import tempfile,pathlib,sqlite3,uvicorn,os,asyncio
from unittest.mock import patch
from scripts import yosaki_runtime as r
base=pathlib.Path(tempfile.mkdtemp(prefix='a3-development-gate-'))
config=r.configuration('test',test_dir=base/'project'/'data')
with patch.object(uvicorn,'run'): r.run(config,inherited_data_dir=None)
from backend.app import main
from backend.app.voice import storage
with main.connect() as db: storage.init_db(db)
os.environ['YOSAKI_BACKGROUND_TASKS_ENABLED']='1'
dev=r.configuration('development',project_root=base/'project',isolated_development_fixture=True)
with patch.object(uvicorn,'run'): r.run(dev,inherited_data_dir=None)
assert os.environ['YOSAKI_BACKGROUND_TASKS_ENABLED']=='0'
assert os.environ['YOSAKI_VOICE_ENABLED']=='0'
async def check():
 with patch('asyncio.create_task',side_effect=AssertionError('background created')):
  async with main.app.router.lifespan_context(main.app): pass
asyncio.run(check())
"""
  env=dict(os.environ);env.pop('YOSAKI_DATA_DIR',None)
  result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
  self.assertEqual(result.returncode,0,result.stderr)
