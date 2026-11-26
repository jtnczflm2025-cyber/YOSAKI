"""R2 startup contract, using only disposable databases and loopback ports."""

import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import closing
from unittest.mock import patch

from scripts import yosaki_runtime as runtime


ROOT = pathlib.Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RuntimeModes(unittest.TestCase):
    def test_isolated_daily_voice_acceptance_uses_exact_loopback_origin(self):
        with (patch.dict(os.environ, {}, clear=True),
              patch.object(runtime, "configuration", return_value=object()),
              patch.object(runtime, "run") as run,
              patch.object(sys, "argv", ["yosaki_runtime", "development",
                                         "--voice-acceptance", "--daily-voice",
                                         "--daily-ttd-ws", "--port", "8001",
                                         "--acceptance-frontend-port", "5175"])):
            self.assertEqual(runtime.main(), 0)
            self.assertEqual(os.environ["YOSAKI_ALLOWED_ORIGINS"],
                             "http://127.0.0.1:5175")
            self.assertEqual(os.environ["YOSAKI_V4_REAL_PROVIDER_ENABLED"], "1")
            self.assertEqual(run.call_args.kwargs["acceptance_frontend_port"], 5175)
        with patch.object(sys, "argv", ["yosaki_runtime", "development",
                                             "--voice-acceptance", "--daily-voice",
                                             "--port", "8000",
                                             "--acceptance-frontend-port", "5175"]):
            with self.assertRaises(SystemExit):
                runtime.main()

    def test_daily_ttd_ws_is_explicit_and_keeps_default_off(self):
        with patch.object(sys, "argv", ["yosaki_runtime", "development", "--daily-ttd-ws"]):
            with self.assertRaises(SystemExit):
                runtime.main()
        for extra, expected in (([], "0"), (["--daily-ttd-ws"], "1")):
            with (patch.dict(os.environ, {}, clear=True),
                  patch.object(runtime, "configuration", return_value=object()),
                  patch.object(runtime, "run") as run,
                  patch.object(sys, "argv", ["yosaki_runtime", "development",
                                             "--voice-acceptance", "--daily-voice", *extra])):
                self.assertEqual(runtime.main(), 0)
                self.assertEqual(os.environ["YOSAKI_V4_DAILY_OUTPUT_ENABLED"], expected)
                self.assertEqual(os.environ["YOSAKI_V4_REAL_PROVIDER_ENABLED"], expected)
                self.assertEqual(os.environ["YOSAKI_V4_OUTPUT_ENABLED"], "0")
                run.assert_called_once()

    def test_daily_ttd_ws_preflight_requires_exact_experimental_flags(self):
        from backend.app.voice import auto_budget
        with tempfile.TemporaryDirectory(prefix="yosaki-daily-ttd-gate-") as folder:
            root = pathlib.Path(folder)
            database = root / "yosaki.sqlite3"
            with closing(sqlite3.connect(database)):
                pass
            target = runtime.Runtime("development", root, database, 18888)
            flags = dict(runtime.DAILY_VOICE_FLAGS)
            flags.update({"YOSAKI_VOICE_ENABLED": "0",
                          "YOSAKI_V4_DAILY_OUTPUT_ENABLED": "1",
                          "YOSAKI_V4_REAL_PROVIDER_ENABLED": "1"})
            with (patch.dict(os.environ, flags, clear=True),
                  patch.object(runtime, "REQUIRED_DEVELOPMENT_TABLES", frozenset()),
                  patch.object(runtime, "REQUIRED_DEVELOPMENT_COLUMNS", {}),
                  patch.object(runtime, "_voice_schema_matches", return_value=True),
                  patch.object(runtime, "_auto_budget_schema_matches", return_value=True),
                  patch.object(runtime, "_auto_daily_schema_matches", return_value=True),
                  patch.object(runtime, "_scribe_daily_schema_matches", return_value=True),
                  patch.object(runtime, "_continuous_daily_ready", return_value=True),
                  patch.object(runtime, "_daily_voice_local_dependencies"),
                  patch.object(auto_budget, "daily_limits", return_value=object())):
                runtime.preflight(target, voice_acceptance=True, daily_ttd_ws=True)
                with self.assertRaisesRegex(ValueError, "YOSAKI_V4_DAILY_OUTPUT_ENABLED"):
                    runtime.preflight(target, voice_acceptance=True)

    def test_daily_voice_missing_model_gate_rejected_before_import(self):
        with tempfile.TemporaryDirectory(prefix="yosaki-daily-preflight-") as folder:
            root = pathlib.Path(folder)
            database = root / "data" / "yosaki.sqlite3"
            database.parent.mkdir()
            with closing(sqlite3.connect(database)):
                pass
            target = runtime.Runtime("development", database.parent, database, 18888)
            flags = dict(runtime.DAILY_VOICE_FLAGS)
            flags.pop("YOSAKI_CHATGPT_PLAN_ENABLED")
            flags["YOSAKI_VOICE_ENABLED"] = "0"
            with (patch.dict(os.environ, flags, clear=True),
                  patch.object(runtime, "REQUIRED_DEVELOPMENT_TABLES", frozenset()),
                  patch.object(runtime, "REQUIRED_DEVELOPMENT_COLUMNS", {}),
                  patch.object(runtime, "_voice_schema_matches", return_value=True),
                  patch.object(runtime, "_auto_budget_schema_matches", return_value=True),
                  patch.object(runtime, "_auto_daily_schema_matches", return_value=True),
                  patch.object(runtime, "_scribe_daily_schema_matches", return_value=True),
                  patch.object(runtime, "_continuous_daily_ready", return_value=True)):
                with self.assertRaisesRegex(ValueError, "YOSAKI_CHATGPT_PLAN_ENABLED"):
                    runtime.preflight(target, voice_acceptance=True)
            self.assertEqual(database.stat().st_size, 0)

    def test_voice_environment_restored_on_success_and_error(self):
        key = "YOSAKI_VOICE_ENABLED"
        for original in (None, "0", "1"):
            with patch.dict(os.environ):
                if original is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original
                for fail in (False, True):
                    @patch.dict(os.environ, {key: "0"})
                    def isolated():
                        self.assertEqual(os.environ[key], "0")
                        if fail:
                            raise RuntimeError("synthetic failure")
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "synthetic"):
                            isolated()
                    else:
                        isolated()
                    self.assertEqual(os.environ.get(key), original)

    @patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"})
    def test_path_guards_run_before_app_import(self):
        with tempfile.TemporaryDirectory(prefix="yosaki-r2-guards-") as folder:
            base = pathlib.Path(folder)
            fake_root = base / "fake-project"
            dev = runtime.configuration(
                "development", project_root=fake_root,
                isolated_development_fixture=True,
            )
            preview = runtime.configuration(
                "preview", project_root=fake_root, preview_dir=base / "preview",
            )
            test = runtime.configuration(
                "test", project_root=fake_root, preview_dir=base / "preview",
                test_dir=base / "test-unique",
            )
            with self.assertRaisesRegex(ValueError, "Schema|不存在"):
                runtime.preflight(dev)
            with self.assertRaisesRegex(ValueError, "YOSAKI_DATA_DIR"):
                runtime.preflight(dev, inherited_data_dir=str(preview.data_dir))
            with self.assertRaisesRegex(ValueError, "YOSAKI_DATA_DIR"):
                runtime.preflight(preview, inherited_data_dir=str(dev.data_dir))
            with self.assertRaisesRegex(ValueError, "YOSAKI_DATA_DIR"):
                runtime.preflight(test, inherited_data_dir=str(dev.data_dir))
            with self.assertRaisesRegex(ValueError, "Development"):
                runtime.configuration(
                    "preview", project_root=fake_root,
                    preview_dir=fake_root / "data",
                )
            with self.assertRaisesRegex(ValueError, "独立"):
                runtime.configuration(
                    "test", project_root=fake_root,
                    preview_dir=base / "preview", test_dir=base / "preview",
                )
            with self.assertRaisesRegex(ValueError, "独立"):
                runtime.configuration(
                    "test", project_root=fake_root,
                    preview_dir=base / "preview", test_dir=base / "preview" / "child",
                )
            dev.database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(dev.database)):
                pass
            with self.assertRaisesRegex(ValueError, "Schema"):
                runtime.preflight(dev)
            self.assertEqual(dev.database.stat().st_size, 0)
            self.assertTrue(dev.database.exists())

    def test_three_modes_start_with_separate_databases(self):
        with tempfile.TemporaryDirectory(prefix="yosaki-r2-modes-") as folder:
            base = pathlib.Path(folder)
            fake_root = base / "fake-project"
            (fake_root / "data").mkdir(parents=True)
            env = dict(os.environ)
            env.pop("YOSAKI_DATA_DIR", None)
            env.pop("ELEVENLABS_API_KEY", None)
            env["YOSAKI_VOICE_ENABLED"] = "0"
            env["YOSAKI_DATA_DIR"] = str(fake_root / "data")
            subprocess.run(
                [sys.executable, "-c",
                 "from backend.app import main; "
                 "from backend.app.voice import storage; "
                 "with_connection=main.connect(); "
                 "storage.init_db(with_connection); "
                 "with_connection.commit(); with_connection.close()"],
                cwd=ROOT, env=env, check=True, capture_output=True, text=True,
            )
            development_db = fake_root / "data" / runtime.DATABASE_NAME
            with closing(sqlite3.connect(
                f"file:{development_db.as_posix()}?mode=ro", uri=True
            )) as db:
                development_schema_before = db.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
            for mode in ("development", "preview", "test"):
                with self.subTest(mode=mode):
                    port = free_port()
                    config = runtime.configuration(
                        mode, project_root=fake_root,
                        preview_dir=base / "preview",
                        test_dir=base / "test-unique" if mode == "test" else None,
                        isolated_development_fixture=True, port=port,
                    )
                    self.assertEqual(config.database.exists(), mode == "development")
                    child_env = dict(env)
                    child_env.pop("YOSAKI_DATA_DIR", None)
                    command = (
                        "from scripts.yosaki_runtime import configuration,run; "
                        f"from pathlib import Path; r=configuration({mode!r},"
                        f"project_root=Path({str(fake_root)!r}),"
                        f"preview_dir=Path({str(base / 'preview')!r}),"
                        f"test_dir=Path({str(base / 'test-unique')!r}) if {mode!r}=='test' else None,"
                        f"isolated_development_fixture=True,port={port}); run(r)"
                    )
                    process = subprocess.Popen(
                        [sys.executable, "-c", command], cwd=ROOT, env=child_env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    )
                    try:
                        for _ in range(100):
                            if process.poll() is not None:
                                self.fail(f"{mode} exited before health")
                            try:
                                with urllib.request.urlopen(
                                    f"http://127.0.0.1:{port}/health", timeout=1
                                ) as response:
                                    self.assertEqual(response.status, 200)
                                break
                            except OSError:
                                time.sleep(0.1)
                        else:
                            self.fail(f"{mode} did not start")
                    finally:
                        process.terminate()
                        output, _ = process.communicate(timeout=10)
                    self.assertIn(f"Mode={mode}", output)
                    self.assertIn(f"Database Path={config.database}", output)
                    self.assertIn("Voice Enabled=false", output)
                    with closing(sqlite3.connect(
                        f"file:{config.database.as_posix()}?mode=ro", uri=True
                    )) as db:
                        db.execute("PRAGMA query_only=ON")
                        actual = pathlib.Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve()
                        self.assertEqual(actual, config.database)
                        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                        tables = {
                            row[0] for row in db.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        self.assertIn("users", tables)
                        self.assertIn("characters", tables)
                        self.assertIn("mv2_sources", tables)
                        if mode == "development":
                            self.assertIn("voice_profiles", tables)
                            self.assertEqual(
                                development_schema_before,
                                db.execute(
                                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                                ).fetchall(),
                            )
                        else:
                            self.assertNotIn("voice_profiles", tables)
            self.assertEqual(len({
                fake_root / "data" / runtime.DATABASE_NAME,
                base / "preview" / runtime.DATABASE_NAME,
                base / "test-unique" / runtime.DATABASE_NAME,
            }), 3)


if __name__ == "__main__":
    unittest.main()
