"""Isolated Scribe draft relay: no provider socket, microphone or daily DB."""

from __future__ import annotations

import base64
from contextlib import contextmanager
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

from backend.app.voice import scribe_draft, storage
from backend.app.voice.schemas import ProfileInput


class FakeScribe:
    def __init__(self):
        self.calls = []

    async def feed(self, pcm, *, commit):
        self.calls.append((len(pcm), commit))
        return ([{"type": "committed", "text": "模拟最终转写"}] if commit else
                [{"type": "partial", "text": "模拟临时字幕"}])


class ScribeDraftRelay(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-scribe-draft-")
        self.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": self.temp.name,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_SCRIBE_DRAFT_ISOLATED": "1",
        })
        self.env.start()
        path = Path(self.temp.name) / "yosaki.sqlite3"

        @contextmanager
        def connect():
            db = sqlite3.connect(path)
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
                CREATE TABLE characters(id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
                INSERT INTO users VALUES(1); INSERT INTO characters VALUES(1,1);
                INSERT INTO characters VALUES(2,1);""")
            storage.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(
                expected_revision=0, voice_id="synthetic_voice_only", active=True))
        self.fake = FakeScribe()
        self.app = FastAPI()
        scribe_draft.install(self.app, connect,
            lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
            ["http://127.0.0.1:15193"],
            provider_factory=lambda *_: self.fake)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"origin": "http://127.0.0.1:15193",
                        "cookie": "isolated=1"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    @staticmethod
    def start(ws, character_id=1):
        ws.send_json({"type": "start", "character_id": character_id,
                      "language_code": "auto", "attempt_id": uuid.uuid4().hex})
        return ws.receive_json()

    def test_committed_only_after_audio_and_manual_commit(self):
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.assertEqual(self.start(ws)["type"], "ready")
            ws.send_json({"type": "audio", "pcm_base64": base64.b64encode(bytes([1])*3200).decode()})
            self.assertEqual(ws.receive_json()["type"], "partial")
            ws.send_json({"type": "commit"})
            self.assertEqual(ws.receive_json(), {"type": "committed", "text": "模拟最终转写"})
        self.assertEqual(self.fake.calls, [(3200, False), (0, True)])

    def test_auth_role_and_client_override_rejected_before_audio(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                               headers={"origin": self.headers["origin"]}):
                pass
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.assertEqual(self.start(ws, 2)["code"], "start_or_authorization_invalid")
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            ws.send_json({"type": "start", "character_id": 1,
                          "language_code": "auto", "attempt_id": uuid.uuid4().hex,
                          "provider_url": "wss://elsewhere"})
            self.assertEqual(ws.receive_json()["code"], "start_or_authorization_invalid")
        self.assertEqual(self.fake.calls, [])

    def test_duplicate_and_cancel_release_owner(self):
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as first:
            self.assertEqual(self.start(first)["type"], "ready")
            with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                               headers=self.headers) as second:
                self.assertEqual(self.start(second)["code"], "scribe_busy")
            first.send_json({"type": "stop"})
            self.assertEqual(first.receive_json()["type"], "stopped")
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as next_ws:
            self.assertEqual(self.start(next_ws)["type"], "ready")

    def test_budget_revocation_and_default_gate(self):
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.start(ws)
            ws.send_json({"type": "audio", "pcm_base64": base64.b64encode(bytes(32002)).decode()})
            self.assertEqual(ws.receive_json()["code"], "audio_budget_exceeded")
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.start(ws)
            with self.connect() as db:
                storage.revoke_consent(db, 1)
            ws.send_json({"type": "commit"})
            self.assertEqual(ws.receive_json()["code"], "authorization_revoked")
        with patch.dict(os.environ, {"YOSAKI_SCRIBE_DRAFT_ISOLATED": "0"}):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                                   headers=self.headers):
                    pass

    def test_real_provider_one_shot_receipt_survives_app_restart(self):
        receipt = Path(self.temp.name) / "one-shot.claimed"
        factory_calls = []

        class EmptyTranscriptScribe(FakeScribe):
            async def feed(self, pcm, *, commit):
                self.calls.append((len(pcm), commit))
                return [{"type": "committed", "text": ""}] if commit else []

        empty_fake = EmptyTranscriptScribe()

        def factory(*args):
            factory_calls.append(args)
            return empty_fake

        with patch.dict(os.environ, {
            "YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED": "1",
            "YOSAKI_SCRIBE_DRAFT_RECEIPT_PATH": str(receipt),
        }):
            first_app = FastAPI()
            scribe_draft.install(first_app, self.connect,
                lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
                ["http://127.0.0.1:15193"], provider_factory=factory)
            with TestClient(first_app) as first_client:
                with first_client.websocket_connect("/api/voice/scribe-draft/ws",
                                                    headers=self.headers) as ws:
                    self.assertEqual(self.start(ws, 2)["code"],
                                     "start_or_authorization_invalid")
                self.assertFalse(receipt.exists())
                with first_client.websocket_connect("/api/voice/scribe-draft/ws",
                                                    headers=self.headers) as ws:
                    self.assertEqual(self.start(ws)["type"], "ready")
                    ws.send_json({"type": "audio", "pcm_base64":
                                  base64.b64encode(bytes([1])*3200).decode()})
                    ws.send_json({"type": "commit"})
                    self.assertEqual(ws.receive_json(), {"type": "committed", "text": ""})
                self.assertEqual(receipt.read_bytes(), b"claimed\n")
                with first_client.websocket_connect("/api/voice/scribe-draft/ws",
                                                    headers=self.headers) as ws:
                    self.assertEqual(self.start(ws)["code"],
                                     "scribe_attempt_budget_exceeded")
            second_app = FastAPI()
            scribe_draft.install(second_app, self.connect,
                lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
                ["http://127.0.0.1:15193"], provider_factory=factory)
            with TestClient(second_app) as second_client:
                with second_client.websocket_connect("/api/voice/scribe-draft/ws",
                                                     headers=self.headers) as ws:
                    self.assertEqual(self.start(ws)["code"],
                                     "scribe_attempt_budget_exceeded")
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(empty_fake.calls, [(3200, False), (0, True)])

    def test_real_provider_requires_absolute_receipt(self):
        with patch.dict(os.environ, {"YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED": "1"},
                        clear=False):
            os.environ.pop("YOSAKI_SCRIBE_DRAFT_RECEIPT_PATH", None)
            with self.assertRaisesRegex(RuntimeError, "one-shot receipt"):
                scribe_draft.install(FastAPI(), self.connect, lambda _ws: {"id": 1},
                                     ["http://127.0.0.1:15193"],
                                     provider_factory=lambda *_: self.fake)

    def test_upstream_open_timeout_stops_once(self):
        attempts = []
        class SlowOpen:
            async def open(self):
                attempts.append("open")
                raise TimeoutError
            async def aclose(self):
                attempts.append("close")
        app = FastAPI()
        scribe_draft.install(app, self.connect,
            lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
            ["http://127.0.0.1:15193"], provider_factory=lambda *_: SlowOpen())
        with TestClient(app) as client:
            with client.websocket_connect("/api/voice/scribe-draft/ws",
                                          headers=self.headers) as ws:
                self.assertEqual(self.start(ws)["code"], "scribe_timeout")
        self.assertEqual(attempts, ["open", "close"])


if __name__ == "__main__":
    unittest.main()
