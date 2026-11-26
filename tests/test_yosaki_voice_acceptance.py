"""Guarded Voice startup tests; all databases are synthetic and disposable."""

import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import yosaki_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]


class VoiceAcceptanceEntry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-voice-entry-")
        self.project = Path(self.temp.name) / "开发 副本"
        data = self.project / "data"
        data.mkdir(parents=True)
        self.database = data / "yosaki.sqlite3"
        env = os.environ.copy()
        env.update({"YOSAKI_DATA_DIR": str(data), "YOSAKI_VOICE_ENABLED": "1",
                    "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
                    "ELEVENLABS_API_KEY": "synthetic-only"})
        result = subprocess.run(
            [sys.executable, "-c", "from backend.app import main"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.entry = runtime.configuration(
            "development", project_root=self.project,
            isolated_development_fixture=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_voice_schema_required_only_for_explicit_acceptance(self):
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"}):
            runtime.preflight(self.entry, voice_acceptance=True)
            with closing(sqlite3.connect(self.database)) as db:
                db.execute("DROP INDEX voice_audio_cache_expiry")
            runtime.preflight(self.entry)  # Default Development remains Voice-off.
            with self.assertRaisesRegex(ValueError, "Voice V2 Schema"):
                runtime.preflight(self.entry, voice_acceptance=True)
            with closing(sqlite3.connect(self.database)) as db:
                db.execute("DROP TABLE voice_message_audio")
            with self.assertRaisesRegex(ValueError, "Voice V2 Schema"):
                runtime.preflight(self.entry, voice_acceptance=True)

    def test_acceptance_rejects_other_modes_and_inherited_voice_override(self):
        preview = runtime.configuration(
            "preview", project_root=self.project,
            preview_dir=Path(self.temp.name) / "preview",
        )
        test = runtime.configuration(
            "test", project_root=self.project,
            test_dir=Path(self.temp.name) / "test-separate",
        )
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"}):
            for entry in (preview, test):
                with self.assertRaisesRegex(ValueError, "仅允许 Development"):
                    runtime.preflight(entry, voice_acceptance=True)
            with self.assertRaisesRegex(ValueError, "YOSAKI_DATA_DIR"):
                runtime.preflight(
                    self.entry, inherited_data_dir=str(preview.data_dir),
                    voice_acceptance=True,
                )
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "1"}):
            with self.assertRaisesRegex(ValueError, "显式验收入口"):
                runtime.preflight(self.entry, voice_acceptance=True)

    def test_guarded_import_sets_flags_without_schema_or_data_writes(self):
        with closing(sqlite3.connect(self.database)) as db:
            before_schema = db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
            before_counts = {
                name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for name in ("users", "characters", "messages", "voice_profiles",
                             "voice_privacy_consents", "voice_audio_cache", "voice_message_audio")
            }
        script = (
            "import os,uvicorn; from pathlib import Path; "
            "from scripts.yosaki_runtime import configuration,run; "
            "uvicorn.run=lambda app,host,port: print('ACCEPTANCE_FLAGS=' + "
            "os.environ['YOSAKI_VOICE_ENABLED'] + ',' + "
            "os.environ['YOSAKI_BACKGROUND_TASKS_ENABLED']); "
            f"entry=configuration('development',project_root=Path({str(self.project)!r}),"
            "isolated_development_fixture=True); run(entry,voice_acceptance=True)"
        )
        env = os.environ.copy()
        env.update({"YOSAKI_VOICE_ENABLED": "0", "YOSAKI_BACKGROUND_TASKS_ENABLED": "1",
                    "ELEVENLABS_API_KEY": "synthetic-only"})
        env.pop("YOSAKI_DATA_DIR", None)
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertIn("Voice Enabled=true", result.stdout)
        self.assertIn("Background Tasks Enabled=false", result.stdout)
        self.assertIn("ACCEPTANCE_FLAGS=1,0", result.stdout)
        self.assertIn(f"Database Path={self.database}", result.stdout)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(before_schema, db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall())
            self.assertEqual(before_counts, {
                name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for name in before_counts
            })

    def test_script_preflight_imports_storage_without_pythonpath(self):
        script_path = ROOT / "scripts" / "yosaki_runtime.py"
        code = (
            "import importlib.util,sys; from pathlib import Path; "
            f"p=Path({str(script_path)!r}); "
            "spec=importlib.util.spec_from_file_location('isolated_runtime',p); "
            "module=importlib.util.module_from_spec(spec); "
            "sys.modules[spec.name]=module; spec.loader.exec_module(module); "
            f"entry=module.configuration('development',project_root=Path({str(self.project)!r}),"
            "isolated_development_fixture=True); "
            "module.preflight(entry,voice_acceptance=True); "
            "assert 'backend.app.main' not in sys.modules; print('PREFLIGHT_OK')"
        )
        env = os.environ.copy()
        env.update({"YOSAKI_VOICE_ENABLED": "0", "YOSAKI_BACKGROUND_TASKS_ENABLED": "0"})
        env.pop("YOSAKI_DATA_DIR", None)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-I", "-c", code], cwd=self.temp.name, env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertIn("PREFLIGHT_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
