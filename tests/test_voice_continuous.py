"""No-provider checks for the default-off continuous call gate."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import base64
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from websockets.frames import CloseCode

from backend.app.voice import continuous_batch, continuous_call, scribe_budget, scribe_draft, storage
from backend.app.voice.schemas import ProfileInput


@contextmanager
def unused_connect():
    db = sqlite3.connect(":memory:")
    try:
        yield db
        db.commit()
    finally:
        db.close()


class ContinuousCallGateTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "1",
            "YOSAKI_VOICE_CONTINUOUS_BATCH_ID": "a" * 32,
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
        })
        self.env.start()
        self.patches = [
            patch("backend.app.voice.continuous_call.scribe_budget.usage",
                  return_value={"available_attempts": 2}),
            patch("backend.app.voice.continuous_call.auto_budget.effective_limits",
                  return_value=continuous_call.auto_budget.Limits(2, 2, 160)),
            patch("backend.app.voice.continuous_call.continuous_batch.start"),
            patch("backend.app.voice.continuous_call.continuous_batch.claim"),
            patch("backend.app.voice.continuous_call.continuous_batch.record_message"),
            patch("backend.app.voice.continuous_call.continuous_batch.end_call"),
            patch("backend.app.voice.continuous_call.continuous_batch.can_continue",
                  return_value=True),
        ]
        for item in self.patches:
            item.start()
        self.manager = continuous_call.CallManager(unused_connect)
        self.manager._authorized = lambda _call: True

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.env.stop()

    def begin(self):
        return self.manager.start(1, 1, 5, 7, True)["call_id"]

    def test_default_off_and_explicit_auto_send_confirmation(self):
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            self.manager.start(1, 1, 5, 7, False)
        with patch.dict(os.environ, {"YOSAKI_VOICE_CONTINUOUS_ISOLATED": "0"}):
            with self.assertRaisesRegex(ValueError, "disabled"):
                self.begin()

        with patch("backend.app.voice.continuous_call.scribe_budget.usage",
                   return_value={"available_attempts": 0}):
            with self.assertRaisesRegex(ValueError, "stt_daily_exhausted"):
                self.begin()
        self.assertFalse(self.manager.calls)

    def test_two_turns_limits_duplicate_and_cached_playback(self):
        call_id = self.begin()
        with self.assertRaisesRegex(ValueError, "already_active"):
            self.begin()
        for number in (1, 2):
            attempt = f"attempt-{number}"
            text = f"turn {number}"
            self.manager.reserve_stt(call_id, 1, 1, attempt)
            turn_id = self.manager.transcript(call_id, 1, attempt, text)
            with self.assertRaisesRegex(ValueError, "stale"):
                self.manager.transcript(call_id, 1, attempt, text)
            self.manager.claim_model(call_id, turn_id, 1, 1, 5, 7, text)
            with self.assertRaisesRegex(ValueError, "binding_invalid"):
                self.manager.claim_model(call_id, turn_id, 1, 1, 5, 7, text)
            self.assertTrue(self.manager.model_active(call_id, turn_id, 1))
            self.assertTrue(self.manager.finish_model(call_id, turn_id, 1, number * 10))
            self.manager.claim_tts(call_id, turn_id, 1, number * 10, 1, 50)
            with self.assertRaisesRegex(ValueError, "binding_invalid"):
                self.manager.claim_tts(call_id, turn_id, 1, number * 10, 1, 50, cached=True)
            if number == 2:
                with self.assertRaisesRegex(ValueError, "tts_limit"):
                    self.manager.claim_tts(call_id, turn_id, 1, 20, 1, 61)
            self.manager.check_tts_dispatch(call_id, turn_id, 1, number * 10, 50)
            self.assertEqual(self.manager.playback_drained(
                call_id, turn_id, 1, number * 10), number == 1)
        summary = self.manager.summary(self.manager.calls[call_id])
        self.assertEqual(summary["used"], {"stt_connections": 2,
            "model_turns": 2, "tts_requests": 2, "tts_characters": 100})
        with self.assertRaisesRegex(ValueError, "voice_call_ended"):
            self.manager.reserve_stt(call_id, 1, 1, "attempt-3")
        self.assertFalse(self.manager.playback_drained(call_id, turn_id, 1, 20))
        with self.assertRaisesRegex(ValueError, "voice_call_ended"):
            self.manager.claim_tts(call_id, turn_id, 1, 20, 1, 61)

    def test_interrupt_revocation_timeout_and_wrong_identity(self):
        call_id = self.begin()
        self.manager.reserve_stt(call_id, 1, 1, "attempt")
        turn_id = self.manager.transcript(call_id, 1, "attempt", "hello")
        self.manager.claim_model(call_id, turn_id, 1, 1, 5, 7, "hello")
        self.assertEqual(self.manager.interrupt(call_id, 1),
                         {"cancel_generation": True, "can_continue": True})
        self.assertFalse(self.manager.model_active(call_id, turn_id, 1))
        self.assertFalse(self.manager.finish_model(call_id, turn_id, 1, 10))
        with self.assertRaisesRegex(ValueError, "binding_invalid"):
            self.manager.claim_tts(call_id, turn_id, 1, 10, 1, 20)
        self.assertFalse(self.manager.valid(call_id, 2))
        self.manager.end(call_id, 2)
        self.assertTrue(self.manager.valid(call_id, 1))
        self.manager.calls[call_id].opened -= continuous_call.CALL_SECONDS + 1
        self.assertFalse(self.manager.valid(call_id, 1))
        replacement = self.begin()
        self.assertNotEqual(replacement, call_id)
        self.assertFalse(self.manager.valid(call_id, 1))
        self.assertEqual(self.manager.by_owner[1], replacement)

    def test_authorization_revocation_ends_call_before_next_dispatch(self):
        call_id = self.begin()
        self.manager._authorized = lambda _call: False
        self.assertFalse(self.manager.valid(call_id, 1))
        with self.assertRaisesRegex(ValueError, "ended"):
            self.manager.reserve_stt(call_id, 1, 1, "late-attempt")
        self.assertIsNone(self.manager.by_owner.get(1))

    def test_concurrent_claims_are_single_use(self):
        call_id = self.begin()
        self.manager.reserve_stt(call_id, 1, 1, "attempt")
        turn = self.manager.transcript(call_id, 1, "attempt", "hello")

        def claim(_):
            try:
                self.manager.claim_model(call_id, turn, 1, 1, 5, 7, "hello")
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(8))), 1)

    def test_playback_interrupt_rejects_late_drain_and_audio_claim(self):
        call_id = self.begin()
        self.manager.reserve_stt(call_id, 1, 1, "attempt")
        turn = self.manager.transcript(call_id, 1, "attempt", "hello")
        self.manager.claim_model(call_id, turn, 1, 1, 5, 7, "hello")
        self.assertTrue(self.manager.finish_model(call_id, turn, 1, 10))
        self.manager.claim_tts(call_id, turn, 1, 10, 1, 20)
        self.manager.check_tts_dispatch(call_id, turn, 1, 10, 20)
        self.assertEqual(self.manager.interrupt(call_id, 1),
                         {"cancel_generation": False, "can_continue": True})
        with self.assertRaisesRegex(ValueError, "playback_binding_invalid"):
            self.manager.playback_drained(call_id, turn, 1, 10)
        with self.assertRaisesRegex(ValueError, "binding_invalid"):
            self.manager.claim_tts(call_id, turn, 1, 10, 1, 20)
        self.assertEqual(self.manager.calls[call_id].tts_requests, 1)
        self.assertFalse(self.manager.calls[call_id].playback_active)

    def test_each_tts_dispatch_uses_the_bound_preclaim(self):
        call_id = self.begin()
        self.manager.reserve_stt(call_id, 1, 1, "attempt")
        turn = self.manager.transcript(call_id, 1, "attempt", "hello")
        self.manager.claim_model(call_id, turn, 1, 1, 5, 7, "hello")
        self.assertTrue(self.manager.finish_model(call_id, turn, 1, 10))
        self.manager.claim_tts(call_id, turn, 1, 10, 1, 20)
        self.manager.check_tts_dispatch(call_id, turn, 1, 10, 20)
        with self.assertRaisesRegex(ValueError, "not_reserved"):
            self.manager.check_tts_dispatch(call_id, turn, 1, 10, 20)
        self.manager.end(call_id, 1)
        with self.assertRaisesRegex(ValueError, "ended"):
            self.manager.check_tts_dispatch(call_id, turn, 1, 10, 20)

    def test_actual_route_requires_login_and_rejects_duplicate(self):
        app = FastAPI()

        @app.middleware("http")
        async def authenticate(request, call_next):
            if request.headers.get("x-local-test") == "yes":
                request.state.user = {"id": 1}
            else:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "login_required"}, status_code=401)
            return await call_next(request)

        manager = continuous_call.install(app, unused_connect)
        manager._authorized = lambda _call: True
        with TestClient(app) as client:
            payload = {"character_id": 1, "conversation_id": 5,
                       "model_profile_id": 7, "acknowledge_auto_send": True}
            self.assertEqual(client.post("/api/voice/calls", json=payload).status_code, 401)
            headers = {"x-local-test": "yes"}
            created = client.post("/api/voice/calls", json=payload, headers=headers)
            self.assertEqual(created.status_code, 200)
            self.assertEqual(client.post("/api/voice/calls", json=payload,
                                         headers=headers).status_code, 409)
            call_id = created.json()["call_id"]
            self.assertEqual(client.post(f"/api/voice/calls/{call_id}/interrupt",
                                         headers=headers).status_code, 200)
            self.assertEqual(client.delete(f"/api/voice/calls/{call_id}",
                                           headers=headers).status_code, 204)
            self.assertEqual(client.get(f"/api/voice/calls/{call_id}",
                                        headers=headers).status_code, 409)


class ScribeContinuousRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-continuous-route-")
        self.path = Path(self.temp.name) / "isolated.sqlite3"
        self.env = patch.dict(os.environ, {
            "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "1",
            "YOSAKI_VOICE_CONTINUOUS_BATCH_ID": "b" * 32,
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
        })
        self.env.start()

        @contextmanager
        def connect():
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                yield db
                db.commit()
            finally:
                db.close()

        self.connect = connect
        with connect() as db:
            db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL,
                    default_model_id INTEGER);
                CREATE TABLE model_profiles(id INTEGER PRIMARY KEY,protocol TEXT,enabled INTEGER);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER,
                    model_profile_id INTEGER);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER,
                    role TEXT);
                INSERT INTO users VALUES(1); INSERT INTO characters VALUES(1,1,7);
                INSERT INTO model_profiles VALUES(7,'chatgpt_plan',1);
                INSERT INTO conversations VALUES(5,1,7);""")
            storage.init_db(db)
            scribe_budget.init_db(db)
            continuous_batch.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(
                expected_revision=0, voice_id="synthetic_continuous_voice", active=True))
        continuous_batch.prepare(connect, "b" * 32, 1, 1, 5, 7)
        scribe_budget.put_settings(connect, 1, True, scribe_budget.Limits(10, 10, 150),
                                   0, True, True)
        self.provider_opens = 0
        self.duplicate_final = False

        class Provider:
            async def open(inner):
                self.provider_opens += 1

            async def feed(inner, pcm, *, commit):
                if not commit:
                    return []
                final = {"type": "committed", "text": "模拟最终转写"}
                return [final, final] if self.duplicate_final else [final]

            async def aclose(inner):
                pass

        self.app = FastAPI()
        self.manager = continuous_call.CallManager(connect)
        with patch("backend.app.voice.continuous_call.auto_budget.effective_limits",
                   return_value=continuous_call.auto_budget.Limits(2, 2, 160)):
            self.call_id = self.manager.start(1, 1, 5, 7, True)["call_id"]
        scribe_draft.install(self.app, connect,
            lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
            ["http://127.0.0.1:15193"], lambda *_: Provider(), self.manager)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"origin": "http://127.0.0.1:15193", "cookie": "isolated=1"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_final_transcript_binds_one_chat_turn(self):
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            attempt = uuid.uuid4().hex
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": attempt,
                          "call_id": self.call_id})
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.send_json({"type": "audio", "pcm_base64":
                          base64.b64encode(bytes([1]) * 3200).decode()})
            ws.send_json({"type": "commit"})
            final = ws.receive_json()
        self.assertEqual(final["type"], "committed")
        self.assertEqual(self.provider_opens, 1)
        self.manager.claim_model(self.call_id, final["call_turn_id"],
                                 1, 1, 5, 7, final["text"])
        with self.assertRaisesRegex(ValueError, "binding_invalid"):
            self.manager.claim_model(self.call_id, final["call_turn_id"],
                                     1, 1, 5, 7, final["text"])

    def test_revocation_rejects_before_provider_connection(self):
        self.manager.end(self.call_id, 1)
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": uuid.uuid4().hex,
                          "call_id": self.call_id})
            self.assertEqual(ws.receive_json()["code"], "voice_call_invalid")
        self.assertEqual(self.provider_opens, 0)

    def test_empty_audio_and_disconnect_release_attempt(self):
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            attempt = uuid.uuid4().hex
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": attempt,
                          "call_id": self.call_id})
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.send_json({"type": "commit"})
            self.assertEqual(ws.receive_json()["code"], "empty_audio")
        self.assertIsNone(self.manager.calls[self.call_id].pending_attempt)
        self.assertEqual(self.provider_opens, 1)
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            attempt = uuid.uuid4().hex
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": attempt,
                          "call_id": self.call_id})
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.close(code=CloseCode.NO_STATUS_RCVD)
        self.assertIsNone(self.manager.calls[self.call_id].pending_attempt)
        self.assertEqual(self.provider_opens, 2)

    def test_daily_stt_denial_does_not_charge_batch_or_call(self):
        day, _ = scribe_budget.period()
        with self.connect() as db:
            db.execute("""INSERT INTO voice_scribe_owner_day_budget
                (owner_id,local_day,attempts,connections,reserved_audio_bytes,
                 created_at,updated_at) VALUES(1,?,10,10,0,'now','now')""", (day,))
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": uuid.uuid4().hex,
                          "call_id": self.call_id})
            self.assertEqual(ws.receive_json()["code"], "scribe_daily_budget_exhausted")
        self.assertEqual(self.provider_opens, 0)
        self.assertEqual(self.manager.calls[self.call_id].stt_connections, 0)
        self.assertEqual(continuous_batch.status(self.connect, "b" * 32, 1)
                         ["used"]["stt_connections"], 0)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts")
                             .fetchone()[0], 0)

    def test_duplicate_provider_final_only_creates_one_turn(self):
        self.duplicate_final = True
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            attempt = uuid.uuid4().hex
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": attempt,
                          "call_id": self.call_id})
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.send_json({"type": "audio", "pcm_base64":
                          base64.b64encode(bytes([1]) * 3200).decode()})
            ws.send_json({"type": "commit"})
            final = ws.receive_json()
        self.assertEqual(final["type"], "committed")
        self.assertEqual(self.manager.calls[self.call_id].pending_transcript,
                         (final["call_turn_id"], final["text"]))
        self.assertEqual(self.provider_opens, 1)

    def test_batch_limit_and_no_restart_reject_third_before_provider_open(self):
        for index in (1, 2):
            with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                               headers=self.headers) as ws:
                ws.send_json({"type": "start", "character_id": 1,
                              "language_code": "auto", "attempt_id": uuid.uuid4().hex,
                              "call_id": self.call_id})
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "commit"})
                self.assertEqual(ws.receive_json()["code"], "empty_audio")
        self.manager.end(self.call_id, 1)
        replacement = continuous_call.CallManager(self.connect)  # Process restart.
        with patch("backend.app.voice.continuous_call.auto_budget.effective_limits",
                   return_value=continuous_call.auto_budget.Limits(2, 2, 160)):
            with self.assertRaisesRegex(ValueError, "voice_batch_restart_forbidden"):
                replacement.start(1, 1, 5, 7, True)
        self.assertEqual(self.provider_opens, 2)
        with self.connect() as db:
            row = db.execute("""SELECT stt_connections FROM voice_continuous_batch_receipts
                WHERE batch_id=?""", ("b" * 32,)).fetchone()
            self.assertEqual(row[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts").fetchone()[0], 2)

    def test_restart_and_second_tab_share_active_call_and_batch(self):
        second_manager = continuous_call.CallManager(self.connect)
        with patch("backend.app.voice.continuous_call.auto_budget.effective_limits",
                   return_value=continuous_call.auto_budget.Limits(2, 2, 160)):
            with self.assertRaisesRegex(ValueError, "already_active"):
                second_manager.start(1, 1, 5, 7, True)
            self.manager.end(self.call_id, 1)
            with self.assertRaisesRegex(ValueError, "restart_forbidden"):
                second_manager.start(1, 1, 5, 7, True)
        self.assertEqual(self.provider_opens, 0)
        self.assertEqual(continuous_batch.status(self.connect, "b" * 32, 1)
                         ["used"]["stt_connections"], 0)

    def test_wrong_conversation_refused_before_any_provider_dispatch(self):
        self.manager.end(self.call_id, 1)
        with self.connect() as db:
            db.execute("INSERT INTO conversations VALUES(6,1,7)")
        second_manager = continuous_call.CallManager(self.connect)
        with patch("backend.app.voice.continuous_call.auto_budget.effective_limits",
                   return_value=continuous_call.auto_budget.Limits(2, 2, 160)):
            with self.assertRaisesRegex(ValueError, "binding_invalid"):
                second_manager.start(1, 1, 6, 7, True)
        self.assertEqual(self.provider_opens, 0)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=6")
                             .fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
