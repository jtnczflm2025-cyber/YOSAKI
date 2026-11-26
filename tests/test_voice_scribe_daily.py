"""Isolated daily Scribe limits and the actual draft WebSocket route."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.voice import backup_policy, router, scribe_budget, scribe_draft, storage
from backend.app.voice.schemas import ProfileInput
from scripts import migrate_voice_scribe_daily, yosaki_runtime


class FakeProvider:
    def __init__(self):
        self.opens = 0
        self.feeds = []
        self.closes = 0

    async def open(self):
        self.opens += 1

    async def feed(self, pcm, *, commit):
        self.feeds.append((len(pcm), commit))
        return [{"type": "committed", "text": "本地最终草稿"}] if commit else []

    async def aclose(self):
        self.closes += 1


class DailyScribeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-scribe-daily-")
        self.path = Path(self.temp.name) / "isolated.sqlite3"
        self.env = patch.dict(os.environ, {
            "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
            "YOSAKI_SCRIBE_DRAFT_ISOLATED": "0",
            "YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED": "0",
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
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
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL);
                INSERT INTO users VALUES(1); INSERT INTO characters VALUES(1,1);""")
            storage.init_db(db)
            scribe_budget.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(
                expected_revision=0, voice_id="synthetic_daily_voice", active=True))
        self.provider = FakeProvider()
        self.app = FastAPI()
        scribe_draft.install(self.app, connect,
            lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
            ["http://127.0.0.1:15193"],
            provider_factory=lambda *_: self.provider)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"origin": "http://127.0.0.1:15193", "cookie": "isolated=1"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def enable(self, limits=scribe_budget.DEFAULT):
        current = scribe_budget.settings(self.connect, 1)
        return scribe_budget.put_settings(self.connect, 1, True, limits,
            current["revision"], limits != scribe_budget.DEFAULT, True)

    def start(self, ws, attempt=None):
        attempt = attempt or uuid.uuid4().hex
        ws.send_json({"type": "start", "character_id": 1,
                      "language_code": "auto", "attempt_id": attempt})
        return ws.receive_json(), attempt

    def test_metered_keeps_usage_and_rejects_duplicate_attempt(self):
        with patch.dict(os.environ, {"YOSAKI_RUN_MODE": "development", "YOSAKI_OWNER_BILLING": "metered"}):
            self.enable()
            attempts = scribe_budget.DEFAULT.attempts + 1
            for _ in range(attempts):
                identity = uuid.uuid4().hex
                scribe_budget.reserve(self.connect, 1, identity)
            self.assertEqual(scribe_budget.usage(self.connect, 1)["used"]["attempts"], attempts)
            with self.assertRaisesRegex(scribe_budget.BudgetExhausted, "reused"):
                scribe_budget.reserve(self.connect, 1, identity)

    def test_default_closed_and_explicit_enable(self):
        self.assertFalse(scribe_budget.settings(self.connect, 1)["enabled"])
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.assertEqual(self.start(ws)[0]["code"], "scribe_user_disabled")
        self.assertEqual(self.provider.opens, 0)
        with self.assertRaisesRegex(ValueError, "enable_confirmation"):
            scribe_budget.put_settings(self.connect, 1, True, scribe_budget.DEFAULT,
                                       0, False, False)
        self.enable()
        self.assertEqual(scribe_budget.settings(self.connect, 1)["revision"], 1)

    def test_daily_and_one_shot_modes_cannot_mix(self):
        with patch.dict(os.environ, {"YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED": "1"}):
            with self.assertRaisesRegex(RuntimeError, "cannot be combined"):
                scribe_draft.install(FastAPI(), self.connect,
                    lambda _ws: {"id": 1}, ["http://127.0.0.1:15193"],
                    provider_factory=lambda *_: self.provider)

    def test_service_gate_stays_off_with_voice_or_background_disabled(self):
        self.enable()
        for overrides in ({"YOSAKI_VOICE_ENABLED": "0"},
                          {"YOSAKI_BACKGROUND_TASKS_ENABLED": "1"}):
            with patch.dict(os.environ, overrides):
                self.assertFalse(scribe_budget.usage(self.connect, 1)["service_enabled"])
                with self.assertRaisesRegex(scribe_budget.BudgetUnavailable, "disabled"):
                    scribe_budget.reserve(self.connect, 1, uuid.uuid4().hex)
        self.assertEqual(scribe_budget.usage(self.connect, 1)["used"]["attempts"], 0)

    def test_reserved_before_connection_and_no_refund_after_cancel_restart(self):
        self.enable()
        first = uuid.uuid4().hex
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.assertEqual(self.start(ws, first)[0]["type"], "ready")
            ws.send_json({"type": "audio", "pcm_base64":
                          base64.b64encode(bytes([1]) * 3200).decode()})
            ws.send_json({"type": "stop"})
            self.assertEqual(ws.receive_json()["type"], "stopped")
        self.assertEqual(self.provider.opens, 1)
        self.assertEqual(self.provider.closes, 1)
        used = scribe_budget.usage(self.connect, 1)
        self.assertEqual(used["remaining"],
                         {"attempts": 0, "connections": 0, "audio_seconds": 0})
        self.assertEqual(used["used"]["actual_audio_seconds"], 0.1)
        second_app = FastAPI()
        scribe_draft.install(second_app, self.connect,
            lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
            ["http://127.0.0.1:15193"], provider_factory=lambda *_: self.provider)
        with TestClient(second_app) as client:
            with client.websocket_connect("/api/voice/scribe-draft/ws",
                                          headers=self.headers) as ws:
                self.assertEqual(self.start(ws)[0]["code"],
                                 "scribe_daily_budget_exhausted")
        self.assertEqual(self.provider.opens, 1)

    def test_displayed_available_attempts_matches_atomic_reservation(self):
        self.enable(scribe_budget.Limits(10, 10, 50))
        initial = scribe_budget.usage(self.connect, 1)
        self.assertEqual(initial["per_attempt_audio_seconds"], 15)
        self.assertEqual(initial["available_attempts"], 3)
        for expected in (2, 1, 0):
            scribe_budget.reserve(self.connect, 1, uuid.uuid4().hex)
            self.assertEqual(scribe_budget.usage(self.connect, 1)["available_attempts"], expected)
        after = scribe_budget.usage(self.connect, 1)
        self.assertEqual(after["remaining"],
                         {"attempts": 7, "connections": 7, "audio_seconds": 5.0})
        with self.assertRaisesRegex(scribe_budget.BudgetExhausted, "budget_exhausted"):
            scribe_budget.reserve(self.connect, 1, uuid.uuid4().hex)
        self.assertEqual(self.provider.opens, 0)
        self.assertEqual(scribe_budget.settings(self.connect, 1)["per_attempt_audio_seconds"], 15)

    def test_increase_revision_duplicate_and_atomic_competition(self):
        self.enable()
        with self.assertRaisesRegex(ValueError, "increase_confirmation"):
            scribe_budget.put_settings(self.connect, 1, True,
                scribe_budget.Limits(2, 2, 30), 1, False, False)
        updated = scribe_budget.put_settings(self.connect, 1, True,
            scribe_budget.Limits(2, 2, 30), 1, True, False)
        self.assertEqual(updated["revision"], 2)
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            scribe_budget.put_settings(self.connect, 1, True,
                scribe_budget.DEFAULT, 1, False, False)
        attempt = uuid.uuid4().hex
        def reserve():
            try:
                scribe_budget.reserve(self.connect, 1, attempt)
                return "reserved"
            except scribe_budget.BudgetExhausted as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: reserve(), range(2)))
        self.assertEqual(sorted(results), ["reserved", "scribe_attempt_reused"])
        self.assertEqual(scribe_budget.usage(self.connect, 1)["used"]["attempts"], 1)

    def test_profile_consent_and_auth_reject_before_reservation(self):
        self.enable()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                headers={"origin": self.headers["origin"]}):
                pass
        with self.connect() as db:
            storage.revoke_consent(db, 1)
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.assertEqual(self.start(ws)[0]["code"],
                             "start_or_authorization_invalid")
        self.assertEqual(scribe_budget.usage(self.connect, 1)["used"]["attempts"], 0)
        self.assertEqual(self.provider.opens, 0)

    def test_switch_off_during_capture_stops_before_next_audio(self):
        self.enable()
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.assertEqual(self.start(ws)[0]["type"], "ready")
            scribe_budget.put_settings(self.connect, 1, False,
                scribe_budget.DEFAULT, 1, False, False)
            ws.send_json({"type": "audio", "pcm_base64":
                          base64.b64encode(bytes([1]) * 3200).decode()})
            self.assertEqual(ws.receive_json()["code"], "scribe_user_disabled")
        self.assertEqual(self.provider.feeds, [])
        self.assertEqual(scribe_budget.usage(self.connect, 1)["used"]["attempts"], 1)

    def test_authenticated_settings_api_requires_explicit_opt_in(self):
        app = FastAPI()
        @app.middleware("http")
        async def synthetic_owner(request, call_next):
            request.state.user = {"id": 1, "token_hash": "isolated-only"}
            return await call_next(request)
        app.include_router(router.make_router(self.connect))
        with TestClient(app) as client:
            initial = client.get("/api/voice/scribe-budget/settings").json()
            self.assertFalse(initial["enabled"])
            self.assertEqual(client.get("/api/voice/scribe-budget").json()["remaining"]["attempts"], 1)
            payload = {"expected_revision": 0, "enabled": True,
                "attempts": 1, "connections": 1, "audio_seconds": 15}
            rejected = client.put("/api/voice/scribe-budget/settings", json=payload)
            self.assertEqual(rejected.status_code, 409)
            payload["confirm_enable"] = True
            saved = client.put("/api/voice/scribe-budget/settings", json=payload)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["revision"], 1)
            self.assertTrue(saved.json()["enabled"])
            self.assertEqual(client.put("/api/voice/scribe-budget/settings", json=payload).status_code, 409)
            over_limit = {**payload, "expected_revision": 1,
                "attempts": 100, "connections": 100, "audio_seconds": 50,
                "confirm_increase": True}
            self.assertEqual(client.put("/api/voice/scribe-budget/settings",
                                        json=over_limit).status_code, 422)
            self.assertEqual(client.get("/api/voice/scribe-budget/settings").json()["revision"], 1)
            within_limit = {**over_limit, "attempts": 3, "connections": 3}
            updated = client.put("/api/voice/scribe-budget/settings", json=within_limit)
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["revision"], 2)
            self.assertEqual(client.get("/api/voice/scribe-budget/settings").json()["limits"],
                             {"attempts": 3, "connections": 3, "audio_seconds": 50})

    def test_snapshot_clears_usage_and_disables_mic(self):
        self.enable()
        scribe_budget.reserve(self.connect, 1, uuid.uuid4().hex)
        clone = sqlite3.connect(":memory:")
        with closing(sqlite3.connect(self.path)) as source:
            source.backup(clone)
        backup_policy.sanitize_voice_snapshot(clone)
        self.assertEqual(clone.execute("SELECT COUNT(*) FROM voice_scribe_attempts").fetchone()[0], 0)
        self.assertEqual(clone.execute("SELECT COUNT(*) FROM voice_scribe_owner_day_budget").fetchone()[0], 0)
        self.assertEqual(clone.execute("SELECT enabled FROM voice_scribe_owner_settings").fetchone()[0], 0)
        self.assertTrue(scribe_budget.settings(self.connect, 1)["enabled"])
        clone.close()

    def test_controlled_migration_only_in_isolated_copy_and_preflight(self):
        target = Path(self.temp.name) / "migration" / "yosaki.sqlite3"
        target.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        with closing(sqlite3.connect(target)) as db:
            for name in ("voice_scribe_owner_settings", "voice_scribe_owner_day_budget",
                         "voice_scribe_attempts"):
                db.execute(f"DROP TABLE {name}")
            db.commit()
            digest = migrate_voice_scribe_daily.schema_hash(db)
        self.assertFalse(migrate_voice_scribe_daily.migrate(target, digest, False)["executed"])
        with closing(sqlite3.connect(target)) as db:
            self.assertFalse(yosaki_runtime._scribe_daily_schema_matches(db))
        outcome = migrate_voice_scribe_daily.migrate(target, digest, True)
        self.assertEqual(len(outcome["added_objects"]), 5)
        self.assertTrue(outcome["old_data_unchanged"])
        with closing(sqlite3.connect(target)) as db:
            self.assertTrue(yosaki_runtime._scribe_daily_schema_matches(db))
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaisesRegex(ValueError, "drift"):
            migrate_voice_scribe_daily.migrate(target, digest, True)


if __name__ == "__main__":
    unittest.main()
