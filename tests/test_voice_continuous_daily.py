"""Daily continuous-call contracts using only a temporary SQLite database."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.voice import (auto_budget, continuous_call, continuous_daily,
                               scribe_budget)
from scripts.migrate_voice_continuous_daily import migrate
from scripts.yosaki_runtime import _continuous_daily_ready


SESSION = "s" * 64


class DailyCallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-continuous-daily-",
                                                 dir=os.getenv("TEMP"))
        self.path = Path(self.temp.name) / "yosaki.sqlite3"
        self.env = patch.dict(os.environ, {
            "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "0",
            "YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED": "1",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
        })
        self.env.start()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY);
                CREATE TABLE characters (id INTEGER PRIMARY KEY, enabled INTEGER);
                CREATE TABLE model_profiles (id INTEGER PRIMARY KEY, enabled INTEGER, protocol TEXT);
                CREATE TABLE conversations (id INTEGER PRIMARY KEY, character_id INTEGER,
                    model_profile_id INTEGER, title TEXT);
                CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER,
                    role TEXT, content TEXT, generation_status TEXT);
                CREATE TABLE auth_sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER);
                INSERT INTO users VALUES (1);
                INSERT INTO characters VALUES (1,1);
                INSERT INTO model_profiles VALUES (5,1,'chatgpt_plan');
                INSERT INTO conversations VALUES (7,1,5,'日常对话');
                INSERT INTO auth_sessions VALUES ('""" + SESSION + """',1);
            """)
            auto_budget.init_db(db)
            auto_budget.init_daily_db(db)
            scribe_budget.init_db(db)
            db.execute("""INSERT INTO voice_scribe_owner_settings
                (owner_id,enabled,attempts,connections,audio_seconds,revision,created_at,updated_at)
                VALUES (1,1,3,3,45,1,'now','now')""")
        self.manager = continuous_call.CallManager(self.connect)
        self.manager._authorized = lambda _call: True

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

    def migrated(self):
        result = migrate(self.path, "isolated", apply=True)
        self.assertTrue(result["applied"])
        with self.connect() as db:
            self.assertTrue(_continuous_daily_ready(db))

    def begin(self, manager=None):
        manager = manager or self.manager
        return manager.start(1, 1, 7, 5, True, SESSION)["call_id"]

    def turn(self, call_id, number, *, manager=None):
        manager = manager or self.manager
        attempt = f"{number:032x}"
        text = f"turn {number}"
        manager.reserve_stt(call_id, 1, 1, attempt)
        scribe_budget.reserve(self.connect, 1, attempt, daily_call_id=call_id)
        turn_id = manager.transcript(call_id, 1, attempt, text)
        manager.claim_model(call_id, turn_id, 1, 1, 7, 5, text)
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (7,'user',?)", (text,))
            message_id = db.execute("""INSERT INTO messages(conversation_id,role,content)
                VALUES (7,'assistant',?)""", (f"reply {number}",)).lastrowid
        self.assertTrue(manager.finish_model(call_id, turn_id, 1, message_id))
        manager.claim_tts(call_id, turn_id, 1, message_id, 1, 10)
        manager.check_tts_dispatch(call_id, turn_id, 1, message_id, 10)
        auto_budget.reserve(self.connect, SESSION, 1, 10, first_request=True,
            batch_claim={"daily_call_id": call_id, "turn_id": turn_id,
                         "message_id": message_id})
        return manager.playback_drained(call_id, turn_id, 1, message_id)

    def test_migration_is_explicit_and_exact(self):
        with self.connect() as db:
            before = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        self.migrated()
        with self.connect() as db:
            names = {(r[0], r[1]) for r in db.execute("""SELECT type,name FROM sqlite_master
                WHERE name LIKE 'voice_continuous_daily_%' OR
                name='sqlite_autoindex_voice_continuous_daily_calls_1'""")}
            self.assertEqual(names, {
                ("table", "voice_continuous_daily_calls"),
                ("index", "voice_continuous_daily_active_owner"),
                ("index", "sqlite_autoindex_voice_continuous_daily_calls_1")})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_continuous_daily_calls").fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, "already_present"):
            migrate(self.path, "isolated", apply=True)

    def test_two_rounds_and_daily_budget_across_restart(self):
        self.migrated()
        self.assertEqual(self.manager.availability(1, 1, 7, 5, SESSION)["available_rounds"], 2)
        call = self.begin()
        self.assertTrue(continuous_daily.conversation_locked(next_db := sqlite3.connect(self.path), 7))
        next_db.close()
        restarted = continuous_call.CallManager(self.connect)
        restarted._authorized = lambda _call: True
        with self.assertRaisesRegex(ValueError, "already_active"):
            self.begin(restarted)
        self.assertTrue(self.turn(call, 1))
        self.assertFalse(self.turn(call, 2))
        self.manager.end(call, 1)
        with self.assertRaisesRegex(ValueError, "daily_exhausted"):
            self.begin(restarted)
        with self.connect() as db:
            row = db.execute("""SELECT ended_at,stt_connections,model_turns,tts_requests,
                tts_characters FROM voice_continuous_daily_calls WHERE call_id=?""", (call,)).fetchone()
            self.assertIsNotNone(row["ended_at"])
            self.assertEqual(tuple(row)[1:], (2, 2, 2, 20))
            self.assertFalse(continuous_daily.conversation_locked(db, 7))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=7").fetchone()[0], 4)

    def test_duplicate_start_refresh_and_cancel_late_events(self):
        self.migrated()
        call = self.begin()
        active = self.manager.availability(1, 1, 7, 5, SESSION)
        self.assertEqual(active["available_rounds"], 0)
        self.assertTrue(active["active_call"])
        with self.assertRaisesRegex(ValueError, "already_active"):
            self.begin()
        self.manager.reserve_stt(call, 1, 1, "a" * 32)
        turn = self.manager.transcript(call, 1, "a" * 32, "hello")
        self.manager.claim_model(call, turn, 1, 1, 7, 5, "hello")
        self.assertEqual(self.manager.interrupt(call, 1),
                         {"cancel_generation": True, "can_continue": True})
        self.assertFalse(self.manager.finish_model(call, turn, 1, 99))
        with self.assertRaisesRegex(ValueError, "binding_invalid"):
            self.manager.claim_tts(call, turn, 1, 99, 1, 10)
        self.manager.end(call, 1)
        restarted = continuous_call.CallManager(self.connect)
        restarted._authorized = lambda _call: True
        self.assertNotEqual(self.begin(restarted), call)

    def test_interrupted_model_advances_only_its_stopped_message(self):
        self.migrated()
        call = self.begin()
        first_attempt = "a" * 32
        self.manager.reserve_stt(call, 1, 1, first_attempt)
        scribe_budget.reserve(self.connect, 1, first_attempt, daily_call_id=call)
        first_turn = self.manager.transcript(call, 1, first_attempt, "first")
        self.manager.claim_model(call, first_turn, 1, 1, 7, 5, "first")
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (7,'user','first')")
        self.assertEqual(self.manager.interrupt(call, 1),
                         {"cancel_generation": True, "can_continue": True})
        with self.assertRaisesRegex(ValueError, "interrupted_message_pending"):
            self.manager.reserve_stt(call, 1, 1, "b" * 32)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT stt_connections FROM voice_continuous_daily_calls WHERE call_id=?",
                                        (call,)).fetchone()[0], 1)
            stopped_id = db.execute("""INSERT INTO messages
                (conversation_id,role,content,generation_status)
                VALUES (7,'assistant','partial','stopped')""").lastrowid
        self.manager.reserve_stt(call, 1, 1, "b" * 32)
        scribe_budget.reserve(self.connect, 1, "b" * 32, daily_call_id=call)
        second_turn = self.manager.transcript(call, 1, "b" * 32, "second")
        self.manager.claim_model(call, second_turn, 1, 1, 7, 5, "second")
        with self.connect() as db:
            row = db.execute("""SELECT last_message_id,stt_connections,model_turns,
                claim_trace_json FROM voice_continuous_daily_calls WHERE call_id=?""",
                (call,)).fetchone()
            self.assertEqual(tuple(row)[:3], (stopped_id, 2, 2))
            self.assertIn('"action":"message_interrupted"', row["claim_trace_json"])

    def test_interrupted_cursor_rejects_unrelated_message(self):
        self.migrated()
        call = self.begin()
        attempt = "a" * 32
        self.manager.reserve_stt(call, 1, 1, attempt)
        scribe_budget.reserve(self.connect, 1, attempt, daily_call_id=call)
        turn = self.manager.transcript(call, 1, attempt, "first")
        self.manager.claim_model(call, turn, 1, 1, 7, 5, "first")
        self.manager.interrupt(call, 1)
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (7,'user','first')")
            db.execute("""INSERT INTO messages(conversation_id,role,content,generation_status)
                VALUES (7,'assistant','not stopped','completed')""")
        with self.assertRaisesRegex(ValueError, "interrupted_message_pending"):
            self.manager.reserve_stt(call, 1, 1, "b" * 32)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT stt_connections FROM voice_continuous_daily_calls WHERE call_id=?",
                                        (call,)).fetchone()[0], 1)

    def test_interrupt_after_second_model_exhausts_call_before_third_mic(self):
        self.migrated()
        call = self.begin()
        self.assertTrue(self.turn(call, 1))
        attempt = "c" * 32
        self.manager.reserve_stt(call, 1, 1, attempt)
        scribe_budget.reserve(self.connect, 1, attempt, daily_call_id=call)
        turn = self.manager.transcript(call, 1, attempt, "second")
        self.manager.claim_model(call, turn, 1, 1, 7, 5, "second")
        self.assertEqual(self.manager.interrupt(call, 1),
                         {"cancel_generation": True, "can_continue": False})
        with self.assertRaisesRegex(ValueError, "stt_limit"):
            self.manager.reserve_stt(call, 1, 1, "d" * 32)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts").fetchone()[0], 2)

    def test_interrupt_timeline_correlates_click_cancel_and_next_attempt(self):
        self.migrated()
        call = self.begin()
        first_attempt = "a" * 32
        self.manager.reserve_stt(call, 1, 1, first_attempt)
        scribe_budget.reserve(self.connect, 1, first_attempt, daily_call_id=call)
        turn = self.manager.transcript(call, 1, first_attempt, "first")
        self.manager.claim_model(call, turn, 1, 1, 7, 5, "first")
        result = self.manager.interrupt(call, 1,
            continuous_call.InterruptObservation(phase="waiting",
                client_click_ms=120.5, client_audio_stop_ms=121.1,
                turn_id=turn))
        self.assertEqual(result, {"cancel_generation": True, "can_continue": True})
        self.manager.model_cancel_observed(call, turn, 1)
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (7,'user','first')")
            db.execute("""INSERT INTO messages(conversation_id,role,content,generation_status)
                VALUES (7,'assistant','partial','stopped')""")
        self.manager.reserve_stt(call, 1, 1, "b" * 32)
        events = self.manager.timeline(call, 1)["events"]
        names = [event["event"] for event in events]
        self.assertLess(names.index("model_request_claimed"), names.index("button_interrupt_received"))
        self.assertLess(names.index("button_interrupt_received"), names.index("model_cancel_requested"))
        self.assertLess(names.index("model_cancel_requested"), names.index("model_stream_cancel_observed"))
        self.assertLess(names.index("model_stream_cancel_observed"),
                        len(names) - 1)
        self.assertEqual(names[-1], "stt_attempt_reserved")
        self.assertEqual(events[names.index("button_interrupt_received")]["client_monotonic_ms"], 120.5)
        self.assertEqual(events[names.index("client_audio_stop_reported")]["client_monotonic_ms"], 121.1)
        self.assertTrue(all("server_elapsed_ms" in event for event in events))
        self.assertFalse(any("first" in str(event) or "partial" in str(event) for event in events))
        with self.assertRaisesRegex(ValueError, "ended"):
            self.manager.timeline(call, 2)

    def test_daily_quota_rejects_before_new_call_or_provider_attempt(self):
        self.migrated()
        day, _ = auto_budget.daily_period()
        with self.connect() as db:
            db.execute("""INSERT INTO voice_auto_owner_day_budget
                (owner_id,local_day,synth_jobs,request_count,character_count,created_at,updated_at)
                VALUES (1,?,2,2,160,'now','now')""", (day,))
        self.assertEqual(self.manager.availability(1, 1, 7, 5, SESSION)["available_rounds"], 0)
        with self.assertRaisesRegex(ValueError, "daily_exhausted"):
            self.begin()
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_continuous_daily_calls").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts").fetchone()[0], 0)

    def test_incomplete_conversation_is_unavailable_before_recording(self):
        self.migrated()
        with self.connect() as db:
            db.execute("""INSERT INTO messages(conversation_id,role,content)
                VALUES (7,'user','unanswered')""")
        available = self.manager.availability(1, 1, 7, 5, SESSION)
        self.assertTrue(available["conversation_incomplete"])
        self.assertEqual(available["available_rounds"], 0)
        with self.assertRaisesRegex(ValueError, "conversation_incomplete"):
            self.begin()
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_continuous_daily_calls").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts").fetchone()[0], 0)

    def test_incomplete_assistant_is_unavailable_before_recording(self):
        self.migrated()
        with self.connect() as db:
            db.execute("""INSERT INTO messages(conversation_id,role,content,generation_status)
                VALUES (7,'assistant','partial answer','incomplete')""")
        available = self.manager.availability(1, 1, 7, 5, SESSION)
        self.assertTrue(available["conversation_incomplete"])
        self.assertEqual(available["available_rounds"], 0)
        with self.assertRaisesRegex(ValueError, "conversation_incomplete"):
            self.begin()
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts").fetchone()[0], 0)

    def test_ordinary_message_blocked_only_for_bound_conversation(self):
        self.migrated()
        call = self.begin()
        with self.connect() as db:
            self.assertTrue(self.manager.conversation_locked(db, 7))
            self.assertFalse(self.manager.conversation_locked(db, 8))
        self.manager.end(call, 1)
        with self.connect() as db:
            self.assertFalse(self.manager.conversation_locked(db, 7))

    def test_concurrent_start_and_long_reply_keep_text(self):
        self.migrated()
        second = continuous_call.CallManager(self.connect)
        second._authorized = lambda _call: True
        def attempt(manager):
            try:
                return manager.start(1, 1, 7, 5, True, SESSION)["call_id"]
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (self.manager, second)))
        self.assertEqual(sum(bool(item) for item in results), 1)
        call = next(item for item in results if item)
        manager = self.manager if call in self.manager.calls else second
        manager.reserve_stt(call, 1, 1, "b" * 32)
        turn = manager.transcript(call, 1, "b" * 32, "hello")
        manager.claim_model(call, turn, 1, 1, 7, 5, "hello")
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content) VALUES(7,'user','hello')")
            message_id = db.execute("""INSERT INTO messages(conversation_id,role,content)
                VALUES(7,'assistant',?)""", ("long answer" * 30,)).lastrowid
        self.assertTrue(manager.finish_model(call, turn, 1, message_id))
        with self.assertRaisesRegex(ValueError, "tts_text_too_long"):
            manager.claim_tts(call, turn, 1, message_id, 1, 161)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT length(content) FROM messages WHERE id=?",
                                        (message_id,)).fetchone()[0], 330)
            self.assertEqual(db.execute("SELECT tts_requests FROM voice_continuous_daily_calls WHERE call_id=?",
                                        (call,)).fetchone()[0], 0)
        manager.end(call, 1)

    def test_route_login_disabled_and_target(self):
        self.migrated()
        app = FastAPI()
        @app.middleware("http")
        async def auth(request, call_next):
            if request.headers.get("x-test") != "yes":
                return JSONResponse({"detail": "login_required"}, status_code=401)
            request.state.user = {"id": 1, "token_hash": SESSION}
            return await call_next(request)
        manager = continuous_call.install(app, self.connect)
        manager._authorized = lambda _call: True
        with (patch("backend.app.voice.continuous_call._local_model_path_reason",
                    return_value=None),
              patch("backend.app.chatgpt_plan.access_token", new_callable=AsyncMock)
              as token_check, TestClient(app) as client):
            url = "/api/voice/calls/availability?character_id=1&conversation_id=7&model_profile_id=5"
            self.assertEqual(client.get(url).status_code, 401)
            self.assertEqual(client.get(url, headers={"x-test": "yes"}).json()["available_rounds"], 2)
            payload = {"character_id": 1, "conversation_id": 7,
                       "model_profile_id": 5, "acknowledge_auto_send": True}
            with patch("backend.app.voice.continuous_call._local_model_path_reason",
                       return_value="voice_call_model_disabled"):
                blocked = client.get(url, headers={"x-test": "yes"}).json()
                self.assertEqual(blocked["available_rounds"], 0)
                self.assertFalse(blocked["model_path_ready"])
                self.assertEqual(client.post("/api/voice/calls", json=payload,
                                             headers={"x-test": "yes"}).status_code, 409)
            token_check.assert_not_awaited()
            token_check.side_effect = HTTPException(401, "ChatGPT 授权已失效")
            self.assertEqual(client.post("/api/voice/calls", json=payload,
                                         headers={"x-test": "yes"}).status_code, 401)
            with self.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_continuous_daily_calls").fetchone()[0], 0)
            token_check.side_effect = None
            token_check.reset_mock()
            self.assertEqual(client.post("/api/voice/calls", json=payload,
                                         headers={"x-test": "yes"}).status_code, 200)
            token_check.assert_awaited_once()
            self.assertEqual(client.get(url, headers={"x-test": "yes"}).json()["available_rounds"], 0)
            self.assertEqual(client.post("/api/voice/calls", json=payload,
                                         headers={"x-test": "yes"}).status_code, 409)
            with patch.dict(os.environ, {"YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED": "0"}):
                self.assertEqual(client.get(url, headers={"x-test": "yes"}).status_code, 409)
