import os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.yosaki_runtime import Runtime,_ai_http_preflight
from backend.app.voice.ai_daily import migrate

class ProtectedAIEntry(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.runtime=Runtime('development',Path(tempfile.gettempdir())/'ai-guard',Path(tempfile.gettempdir())/'ai-guard/yosaki.sqlite3',18890)
    def tearDown(self):self.db.close()
    def flags(self):return patch.dict(os.environ,{'YOSAKI_VOICE_AI_HTTP_ENABLED':'1','YOSAKI_VOICE_AI_HTTP_DATABASE':str(self.runtime.database),'YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED':'1'})
    def test_default_off_does_not_install_tables(self):
        with patch.dict(os.environ,{'YOSAKI_VOICE_AI_HTTP_ENABLED':'0'}):_ai_http_preflight(self.db,self.runtime,True)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0],0)
    def test_guard_path_and_missing_schema_are_rejected(self):
        with self.flags():
            with self.assertRaisesRegex(ValueError,'受保护'):_ai_http_preflight(self.db,self.runtime,False)
            with patch.dict(os.environ,{'YOSAKI_VOICE_AI_HTTP_DATABASE':'wrong'}):
                with self.assertRaisesRegex(ValueError,'绑定'):_ai_http_preflight(self.db,self.runtime,True)
            with self.assertRaisesRegex(ValueError,'受控迁移'):_ai_http_preflight(self.db,self.runtime,True)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()[0],0)
    def test_exact_six_table_schema_and_drift(self):
        self.db.execute('BEGIN');migrate(self.db);self.db.commit()
        with self.flags():
            _ai_http_preflight(self.db,self.runtime,True)
            self.db.execute('ALTER TABLE annotation_plans ADD COLUMN unexpected TEXT')
            with self.assertRaisesRegex(ValueError,'结构不符'):_ai_http_preflight(self.db,self.runtime,True)
    def test_actual_cli_rejects_non_daily_switch_before_import(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,str(root/'scripts/yosaki_runtime.py'),'development','--ai-expression'],capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertIn(b'--ai-expression',result.stderr)

if __name__=='__main__':unittest.main()
