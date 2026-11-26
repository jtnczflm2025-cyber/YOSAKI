"""Disposable SQLite checks for configurable per-call limits."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.voice import auto_budget, continuous_call, continuous_daily, scribe_budget
from scripts.migrate_voice_continuous_settings import migrate
from scripts.yosaki_runtime import _continuous_daily_ready


SESSION = "z" * 64


class CallSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-call-limits-", dir=os.getenv("TEMP"))
        self.path = Path(self.temp.name) / "yosaki.sqlite3"
        self.env = patch.dict(os.environ, {
            "YOSAKI_VOICE_ENABLED": "1", "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
            "YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED": "1",
            "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "0",
            "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
        })
        self.env.start()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE characters(id INTEGER PRIMARY KEY);
                CREATE TABLE model_profiles(id INTEGER PRIMARY KEY);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER,model_profile_id INTEGER,title TEXT);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER,
                    role TEXT,generation_status TEXT);
                CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER);
                INSERT INTO users VALUES(1);
                INSERT INTO characters VALUES(1);
                INSERT INTO model_profiles VALUES(5);
                INSERT INTO conversations VALUES(7,1,5,'isolated');
                INSERT INTO auth_sessions VALUES('""" + SESSION + """',1);
            """)
            auto_budget.init_db(db)
            auto_budget.init_daily_db(db)
            scribe_budget.init_db(db)
            continuous_daily.init_db(db)
            db.execute("""INSERT INTO voice_scribe_owner_settings
                (owner_id,enabled,attempts,connections,audio_seconds,revision,created_at,updated_at)
                VALUES(1,1,10,10,150,1,'now','now')""")
            db.execute("""INSERT INTO voice_auto_owner_settings
                (owner_id,synth_jobs,request_count,character_count,revision,created_at,updated_at)
                VALUES(1,20,40,4000,1,'now','now')""")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def test_migration_preserves_legacy_calls_and_accepts_v2_preflight(self):
        with self.connect() as db:
            db.execute("""INSERT INTO voice_continuous_daily_calls
                (call_id,owner_id,character_id,conversation_id,model_profile_id,started_at,ended_at)
                VALUES(?,?,?,?,?,?,?)""", ("a" * 32, 1, 1, 7, 5, time.time()-20, time.time()))
            old = tuple(db.execute("SELECT * FROM voice_continuous_daily_calls").fetchone())
        result = migrate(self.path, "isolated", apply=True)
        self.assertEqual(result["old_call_rows_preserved"], 1)
        with self.connect() as db:
            row = db.execute("SELECT * FROM voice_continuous_daily_calls").fetchone()
            self.assertEqual(tuple(row)[:len(old)], old)
            self.assertEqual(tuple(row)[len(old):], (2, 180, 2, 160))
            self.assertTrue(_continuous_daily_ready(db))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT count(*) FROM voice_continuous_owner_settings").fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, "old_call_schema_drift"):
            migrate(self.path, "isolated", apply=True)

    def test_deployment_ceilings_above_legacy_values_preserve_ledgers(self):
        from backend.app.voice import expression_product
        from scripts.migrate_voice_delivery import migrate as expand
        from scripts.yosaki_runtime import _auto_daily_schema_matches, _scribe_daily_schema_matches

        migrate(self.path, "isolated", apply=True)
        with self.connect() as db:
            expression_product.init_db(db)
        scribe_budget.reserve(self.connect, 1, "e" * 32)
        auto_budget.reserve(self.connect, SESSION, 1, 14, first_request=True)
        usage = auto_budget.usage(self.connect, SESSION, 1)["daily"]["used"]
        stt_usage = scribe_budget.usage(self.connect, 1)["used"]
        expand(self.path, "isolated", apply=True)
        with self.connect() as db:
            self.assertTrue(_continuous_daily_ready(db))
            self.assertTrue(_auto_daily_schema_matches(db))
            self.assertTrue(_scribe_daily_schema_matches(db))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE voice_scribe_owner_settings SET attempts='invalid'")
        env = {"YOSAKI_SCRIBE_MAX_ATTEMPTS":"24", "YOSAKI_SCRIBE_MAX_CONNECTIONS":"24",
               "YOSAKI_SCRIBE_MAX_AUDIO_SECONDS":"360", "YOSAKI_VOICE_AUTO_MAX_JOBS":"24",
               "YOSAKI_VOICE_AUTO_MAX_REQUESTS":"48", "YOSAKI_VOICE_AUTO_MAX_CHARACTERS":"5000",
               "YOSAKI_VOICE_CALL_MAX_ROUNDS":"8", "YOSAKI_VOICE_CALL_MAX_SECONDS":"700"}
        with patch.dict(os.environ, env):
            next_scribe = scribe_budget.Limits(24,24,360)
            with self.assertRaisesRegex(ValueError, "increase_confirmation"):
                scribe_budget.put_settings(self.connect,1,True,next_scribe,1,False,False)
            scribe_budget.put_settings(self.connect,1,True,next_scribe,1,True,False)
            with self.assertRaisesRegex(ValueError, "revision_conflict"):
                scribe_budget.put_settings(self.connect,1,True,next_scribe,1,True,False)
            auto_budget.put_settings(self.connect,1,auto_budget.Limits(24,48,5000),1,True)
            call_limits = continuous_daily.CallLimits(8,700,8,1200)
            continuous_daily.put_settings(self.connect,1,call_limits,0,True)
            captured = continuous_daily.start(self.connect,"b"*32,1,1,7,5,SESSION)
            self.assertEqual(captured, call_limits)
            self.assertEqual(auto_budget.usage(self.connect,SESSION,1)["daily"]["used"],usage)
            self.assertEqual(scribe_budget.usage(self.connect,1)["used"],stt_usage)

    def test_limits_are_confirmed_versioned_and_snapshot_per_call(self):
        migrate(self.path, "isolated", apply=True)
        raised = continuous_daily.CallLimits(4, 420, 6, 800)
        with self.assertRaisesRegex(ValueError, "increase_confirmation"):
            continuous_daily.put_settings(self.connect, 1, raised, 0, False)
        saved = continuous_daily.put_settings(self.connect, 1, raised, 0, True)
        self.assertEqual(saved["revision"], 1)
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            continuous_daily.put_settings(self.connect, 1, raised, 0, True)
        call_id = "b" * 32
        self.assertEqual(continuous_daily.start(self.connect, call_id, 1, 1, 7, 5, SESSION), raised)
        continuous_daily.put_settings(self.connect, 1,
            continuous_daily.CallLimits(1, 60, 1, 20), 1, False)
        with self.connect() as db:
            row = db.execute("SELECT * FROM voice_continuous_daily_calls WHERE call_id=?", (call_id,)).fetchone()
            self.assertEqual(continuous_daily._row_limits(row), raised)
            for _ in range(4):
                continuous_daily.claim(db, call_id, 1, "stt")
            with self.assertRaisesRegex(ValueError, "limit_exhausted"):
                continuous_daily.claim(db, call_id, 1, "stt")
            for _ in range(4):
                continuous_daily.claim(db, call_id, 1, "model")
            for _ in range(6):
                continuous_daily.claim(db, call_id, 1, "tts", characters=100)
            with self.assertRaisesRegex(ValueError, "limit_exhausted"):
                continuous_daily.claim(db, call_id, 1, "tts", characters=100)
        self.assertFalse(continuous_daily.can_continue(self.connect, call_id, 1))

    def test_active_call_blocks_migration(self):
        with self.connect() as db:
            db.execute("""INSERT INTO voice_continuous_daily_calls
                (call_id,owner_id,character_id,conversation_id,model_profile_id,started_at)
                VALUES(?,?,?,?,?,?)""", ("c" * 32, 1, 1, 7, 5, time.time()))
        with self.assertRaisesRegex(ValueError, "active_call_migration_forbidden"):
            migrate(self.path, "isolated", apply=True)
        with self.connect() as db:
            self.assertFalse(continuous_daily._has_v2(db))
            self.assertEqual(db.execute("SELECT count(*) FROM voice_continuous_daily_calls").fetchone()[0], 1)

    def test_product_settings_route_and_call_snapshot(self):
        migrate(self.path, "isolated", apply=True)
        app = FastAPI()

        @app.middleware("http")
        async def auth(request, call_next):
            if request.headers.get("x-test") != "yes":
                return JSONResponse({"detail": "login_required"}, status_code=401)
            request.state.user = {"id": 1, "token_hash": SESSION}
            return await call_next(request)

        manager = continuous_call.install(app, self.connect)
        manager._authorized = lambda _call: True
        headers = {"x-test": "yes"}
        limits = {"rounds": 4, "seconds": 420,
                  "tts_requests": 6, "tts_characters": 800}
        with (patch("backend.app.voice.continuous_call._local_model_path_reason",
                    return_value=None),
              patch("backend.app.chatgpt_plan.access_token", new_callable=AsyncMock)
              as token_check, TestClient(app) as client):
            self.assertEqual(client.get("/api/voice/calls/settings").status_code, 401)
            current = client.get("/api/voice/calls/settings", headers=headers).json()
            self.assertEqual(current["limits"]["rounds"], 2)
            self.assertEqual(client.put("/api/voice/calls/settings", headers=headers,
                json={**limits, "expected_revision": 0}).status_code, 409)
            updated = client.put("/api/voice/calls/settings", headers=headers,
                json={**limits, "expected_revision": 0, "confirm_increase": True})
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["revision"], 1)
            available = client.get("/api/voice/calls/availability?character_id=1&conversation_id=7&model_profile_id=5",
                                   headers=headers).json()
            self.assertEqual(available["available_rounds"], 4)
            self.assertTrue(available["model_path_ready"])
            started = client.post("/api/voice/calls", headers=headers,
                json={"character_id":1,"conversation_id":7,"model_profile_id":5,
                      "acknowledge_auto_send":True})
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["limits"]["model_turns"], 4)
            self.assertEqual(started.json()["limits"]["seconds"], 420)
            token_check.assert_awaited_once()
            with self.connect() as db:
                row = db.execute("SELECT limit_rounds,limit_seconds FROM voice_continuous_daily_calls").fetchone()
                self.assertEqual(tuple(row), (4, 420))


if __name__ == "__main__":
    unittest.main()
