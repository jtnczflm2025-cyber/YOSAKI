"""Durable batch receipt tests. No provider or daily database is used."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.app.voice import auto_budget, continuous_batch, scribe_budget


class ContinuousBatchReceiptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-batch-")
        self.path = Path(self.temp.name) / "isolated.sqlite3"
        self.batch_id = "c" * 32

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
                CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER);
                INSERT INTO users VALUES(1);
                INSERT INTO auth_sessions VALUES('test-session',1);
                CREATE TABLE conversations (
                id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL,
                model_profile_id INTEGER NOT NULL);
                CREATE TABLE messages (id INTEGER PRIMARY KEY,
                conversation_id INTEGER NOT NULL,role TEXT NOT NULL);
                INSERT INTO conversations VALUES (7,1,5),(8,1,5);
                INSERT INTO messages VALUES (1,7,'user'),(2,7,'assistant');""")
            continuous_batch.init_db(db)
        continuous_batch.prepare(connect, self.batch_id, 1, 1, 8, 5)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_new_empty_conversation_is_bound_and_other_messages_fail(self):
        with self.assertRaisesRegex(ValueError, "binding_invalid"):
            continuous_batch.start(self.connect, self.batch_id, 1, 1, 7, 5)
        with self.connect() as db:
            db.execute("INSERT INTO messages VALUES (3,8,'user')")
        with self.assertRaisesRegex(ValueError, "conversation_changed"):
            continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5)

    def test_batch_is_atomic_across_connections_and_restart(self):
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5)

        def claim_stt(_):
            try:
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    continuous_batch.claim(db, self.batch_id, 1, "stt")
                return 1
            except continuous_batch.BatchRejected:
                return 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim_stt, range(8))), 2)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "model")
        # A fresh connection sees spent quota, as after a process restart.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "model")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "exhausted"):
                continuous_batch.claim(db, self.batch_id, 1, "model")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "tts", requests=1,
                                   characters=70)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "tts", requests=1,
                                   characters=90)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "exhausted"):
                continuous_batch.claim(db, self.batch_id, 1, "tts", requests=1,
                                       characters=1)
        used = continuous_batch.status(self.connect, self.batch_id, 1)["used"]
        self.assertEqual(used, {"stt_connections": 2, "model_turns": 2,
                                "tts_requests": 2, "tts_characters": 160})
        self.assertFalse(continuous_batch.can_continue(self.connect, self.batch_id, 1))

    def test_failed_transaction_does_not_claim_or_authorize_dispatch(self):
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                continuous_batch.claim(db, self.batch_id, 1, "model")
                raise RuntimeError("pre_dispatch_failure")
        except RuntimeError:
            pass
        self.assertEqual(continuous_batch.status(self.connect, self.batch_id, 1)
                         ["used"]["model_turns"], 0)

    def test_expiry_and_message_receipt_binding(self):
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "model")
            db.execute("INSERT INTO messages VALUES (3,8,'user'),(4,8,'assistant')")
        continuous_batch.record_message(self.connect, self.batch_id, 1, 4)
        with self.connect() as db:
            db.execute("UPDATE voice_continuous_batch_receipts SET started_at=?",
                       (time.time() - continuous_batch.SECONDS - 1,))
        with self.assertRaisesRegex(ValueError, "restart_forbidden"):
            continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "expired"):
                continuous_batch.claim(db, self.batch_id, 1, "stt")

    def test_claim_timeline_binds_call_attempt_turn_and_saved_message(self):
        call_id, attempt_id, turn_id = "a" * 32, "b" * 32, "d" * 32
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5,
                               call_id=call_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "stt",
                                   call_id=call_id, attempt_id=attempt_id)
            continuous_batch.claim(db, self.batch_id, 1, "model",
                                   call_id=call_id, turn_id=turn_id)
            db.execute("INSERT INTO messages VALUES (3,8,'user'),(4,8,'assistant')")
        continuous_batch.record_message(self.connect, self.batch_id, 1, 4,
                                        call_id=call_id, turn_id=turn_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            continuous_batch.claim(db, self.batch_id, 1, "tts", requests=1,
                                   characters=14, call_id=call_id, turn_id=turn_id,
                                   message_id=4)
        with self.connect() as db:
            trace = json.loads(db.execute("""SELECT claim_trace_json
                FROM voice_continuous_batch_receipts WHERE batch_id=?""",
                (self.batch_id,)).fetchone()[0])
        self.assertEqual([event["action"] for event in trace],
                         ["call_start", "stt", "model", "message_saved", "tts"])
        self.assertEqual(trace[1]["attempt_id"], attempt_id)
        self.assertEqual(trace[2]["turn_id"], turn_id)
        self.assertEqual(trace[3]["message_id"], 4)
        self.assertEqual(trace[3]["turn_id"], turn_id)
        self.assertEqual(trace[4]["message_id"], 4)
        self.assertTrue(all(event.get("call_id", call_id) == call_id for event in trace))

    def test_two_processes_cannot_start_same_batch_and_end_never_refunds(self):
        first, second = "a" * 32, "b" * 32
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5,
                               call_id=first)
        with self.assertRaisesRegex(ValueError, "already_active"):
            continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5,
                                   call_id=second)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "call_binding_invalid"):
                continuous_batch.claim(db, self.batch_id, 1, "stt",
                                       call_id=second, attempt_id="e" * 32)
            continuous_batch.claim(db, self.batch_id, 1, "stt",
                                   call_id=first, attempt_id="d" * 32)
        continuous_batch.end_call(self.connect, self.batch_id, 1, first)
        with self.assertRaisesRegex(ValueError, "restart_forbidden"):
            continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5,
                                   call_id=second)
        self.assertEqual(continuous_batch.status(self.connect, self.batch_id, 1)
                         ["used"]["stt_connections"], 1)
        continuous_batch.end_call(self.connect, self.batch_id, 1, first)
        with self.connect() as db:
            active = db.execute("""SELECT active_call_id FROM voice_continuous_batch_receipts
                WHERE batch_id=?""", (self.batch_id,)).fetchone()[0]
        self.assertIsNone(active)

    def test_daily_and_batch_tts_claims_commit_or_roll_back_together(self):
        call_id, turn_id = "a" * 32, "d" * 32
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5,
                               call_id=call_id)
        with self.connect() as db:
            auto_budget.init_db(db)
            auto_budget.init_daily_db(db)
        claim = {"batch_id": self.batch_id, "call_id": call_id,
                 "turn_id": turn_id, "message_id": 4}
        day, _ = auto_budget.daily_period()
        with patch.dict(os.environ, {"YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
                                  "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"}):
            with self.connect() as db:
                db.execute("""INSERT INTO voice_auto_owner_day_budget
                    (owner_id,local_day,synth_jobs,request_count,character_count,
                     created_at,updated_at) VALUES (1,?,2,2,0,'now','now')""",
                    (day,))
            with self.assertRaisesRegex(auto_budget.BudgetExhausted, "daily"):
                auto_budget.reserve(self.connect, "test-session", 1, 14,
                                    first_request=True, batch_claim=claim)
            self.assertEqual(continuous_batch.status(self.connect, self.batch_id, 1)
                             ["used"]["tts_requests"], 0)
            with self.connect() as db:
                db.execute("""UPDATE voice_auto_owner_day_budget SET
                    synth_jobs=0,request_count=0 WHERE owner_id=1 AND local_day=?""",
                    (day,))
                db.execute("""UPDATE voice_continuous_batch_receipts SET
                    tts_requests=2 WHERE batch_id=?""", (self.batch_id,))
            with self.assertRaisesRegex(auto_budget.BudgetExhausted, "batch"):
                auto_budget.reserve(self.connect, "test-session", 1, 14,
                                    first_request=True, batch_claim=claim)
            with self.connect() as db:
                row = db.execute("""SELECT synth_jobs,request_count,character_count
                    FROM voice_auto_owner_day_budget WHERE owner_id=1 AND local_day=?""",
                    (day,)).fetchone()
                self.assertEqual(tuple(row), (0, 0, 0))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_auto_session_budget")
                                 .fetchone()[0], 0)
                db.execute("UPDATE voice_continuous_batch_receipts SET tts_requests=0")
            auto_budget.reserve(self.connect, "test-session", 1, 14,
                                first_request=True, batch_claim=claim)
            self.assertEqual(continuous_batch.status(self.connect, self.batch_id, 1)
                             ["used"]["tts_requests"], 1)
            with self.connect() as db:
                row = db.execute("""SELECT synth_jobs,request_count,character_count
                    FROM voice_auto_owner_day_budget WHERE owner_id=1 AND local_day=?""",
                    (day,)).fetchone()
                self.assertEqual(tuple(row), (1, 1, 14))

    def test_daily_and_batch_stt_claims_commit_or_roll_back_together(self):
        call_id = "a" * 32
        continuous_batch.start(self.connect, self.batch_id, 1, 1, 8, 5,
                               call_id=call_id)
        with self.connect() as db:
            scribe_budget.init_db(db)
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "1",
                                  "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
                                  "YOSAKI_SCRIBE_DAILY_ENABLED": "1"}):
            scribe_budget.put_settings(self.connect, 1, True,
                scribe_budget.Limits(1, 1, 15), 0, False, True)
            day, _ = scribe_budget.period()
            with self.connect() as db:
                db.execute("""INSERT INTO voice_scribe_owner_day_budget
                    (owner_id,local_day,attempts,connections,reserved_audio_bytes,
                     created_at,updated_at) VALUES(1,?,1,1,480000,'now','now')""",
                    (day,))
            with self.assertRaisesRegex(scribe_budget.BudgetExhausted, "daily"):
                scribe_budget.reserve(self.connect, 1, "b" * 32,
                                      batch_id=self.batch_id, call_id=call_id)
            self.assertEqual(continuous_batch.status(self.connect, self.batch_id, 1)
                             ["used"]["stt_connections"], 0)
            with self.connect() as db:
                db.execute("""UPDATE voice_scribe_owner_day_budget SET
                    attempts=0,connections=0,reserved_audio_bytes=0 WHERE owner_id=1""")
                db.execute("UPDATE voice_continuous_batch_receipts SET stt_connections=2")
            with self.assertRaisesRegex(continuous_batch.BatchRejected, "exhausted"):
                scribe_budget.reserve(self.connect, 1, "c" * 32,
                                      batch_id=self.batch_id, call_id=call_id)
            with self.connect() as db:
                row = db.execute("""SELECT attempts,connections,reserved_audio_bytes
                    FROM voice_scribe_owner_day_budget WHERE owner_id=1""").fetchone()
                self.assertEqual(tuple(row), (0, 0, 0))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_scribe_attempts")
                                 .fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
