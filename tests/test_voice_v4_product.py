"""Product V4 WS route with temporary main.app, owner cookie, and fake supplier."""

import asyncio
import base64
import hashlib
import json
import os
import sqlite3
import sys
import subprocess
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect
from tests.voice_main_test_support import reload_main_with_class_cleanup


ORIGIN = "http://127.0.0.1:5173"
PCM = base64.b64encode(b"\x00\x00\x10\x27" * 16).decode()


class FakeSupplier:
    def __init__(self, sent, mode="normal"):
        self.sent = sent
        self.mode = mode
        self.queue = asyncio.Queue()
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def send(self, raw):
        value = json.loads(raw)
        self.sent.append(value)
        if "inputs" in value:
            if self.mode == "error":
                await self.queue.put(json.dumps({"error": {"code": "quota_exceeded"}}))
            elif self.mode == "remote_close":
                raise OSError("fake lost connection")
            elif self.mode == "normal":
                await self.queue.put(json.dumps({"audio": PCM,
                    "is_final_audio_for_turn": True}))
        if value.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self):
        return await self.queue.get()


class ProductOutputRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="yosaki-v4-product-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": cls.temp.name,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_V4_OUTPUT_ENABLED": "1",
            "YOSAKI_V4_REAL_PROVIDER_ENABLED": "0",
            "YOSAKI_V4_TTD_PROXY": "",
            "YOSAKI_V4_OUTPUT_MAX_ATTEMPTS_PER_OWNER": "5",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "ELEVENLABS_API_KEY": "fake-local-only",
        })
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.main = reload_main_with_class_cleanup(cls)
        cls.client = TestClient(cls.main.app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        setup = cls.client.post("/api/auth/setup", json={
            "username": "v4_temp_owner", "password": "v4-test-only-password-123"})
        assert setup.status_code == 201, setup.text
        from backend.app.voice import storage
        from backend.app.voice.schemas import ProfileInput
        with cls.main.connect() as db:
            db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) "
                       "VALUES (1,'isolation','now','now')")
            cls.conversation_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO messages(conversation_id,role,content,generation_status,created_at) "
                       "VALUES (?,'assistant','[whispers] 今日は静かな夜です。','completed','now')",
                       (cls.conversation_id,))
            cls.message_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO messages(conversation_id,role,content,generation_status,created_at) "
                       "VALUES (?,'assistant','途中です','stopped','now')",
                       (cls.conversation_id,))
            cls.partial_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=0,
                voice_id="synthetic_ivc_voice", active=True))
        cls.gate = cls.main.app.state.voice_v4_output

    def setUp(self):
        from backend.app.voice import manager, realtime_receipt, storage
        self.gate.attempts.clear()
        self.gate.active_owners.clear()
        self.sent = []
        self.connections = []
        self.mode = "normal"
        self.fixture = tempfile.TemporaryDirectory(prefix="approval-", dir=self.temp.name)
        self.addCleanup(self.fixture.cleanup)
        root = Path(self.fixture.name)
        self.source_db = root / "source.sqlite3"
        with self.main.connect() as original, closing(sqlite3.connect(self.source_db)) as snapshot:
            original.backup(snapshot)
        self.manifest_path = root / "manifest.json"
        self.receipt_root = root / "receipts"
        self.receipt_root.mkdir()
        context = manager.speech_context(self.main.connect, self.message_id, 1)
        with self.main.connect() as db:
            profile = storage.profile(db, 1)
            consent = storage.consent(db, 1)
            actual = db.execute("PRAGMA database_list").fetchone()[2]
        self.manifest = {
            "schema_version": 1, "status": "awaiting_single_real_approval",
            "run_id": "voice-v4-product-test-r1", "owner_id": 1,
            "character_id": 1, "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "message_version_sha256": context["digest"],
            "request_text": context["segments"][0],
            "request_text_sha256": hashlib.sha256(
                context["segments"][0].encode()).hexdigest(),
            "characters": len(context["segments"][0]),
            "profile_revision": profile["revision"],
            "voice_id_sha256": hashlib.sha256(
                profile["voice_id"].encode()).hexdigest(),
            "notice_version": consent["notice_version"],
            "provider": "elevenlabs", "model_id": "eleven_v4_turbo",
            "output_format": "pcm_24000", "pcm_sample_rate_hz": 24000,
            "pcm_encoding": "s16le", "pcm_channels_expected": 1,
            "language_code": "ja", "voice_settings": "omitted",
            "source_database_path": str(self.source_db.resolve()),
            "runtime_database_path": str(Path(actual).resolve()),
            "network_proxy_url": None,
            "limits": {**realtime_receipt.EXPECTED_LIMITS,
                       "tts_characters": len(context["segments"][0])},
            "failure_policy": "stop_on_auth_quota_protocol_unknown_timeout_or_disconnect; never_replay_dispatched_text",
            "billing": "unknown_until_provider_account_observation",
        }
        self.manifest_path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")
        receipt = realtime_receipt.expected_receipt(self.manifest,
            realtime_receipt.digest(self.manifest), realtime_receipt.code_set_sha256())
        self.approval_path = self.receipt_root / f'{self.manifest["run_id"]}.approval.json'
        self.approval_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.approval_used = self.approval_path.with_name(self.approval_path.name + ".used")
        self.env_gate = patch.dict(os.environ, {
            "YOSAKI_V4_PRODUCT_MANIFEST_PATH": str(self.manifest_path),
            "YOSAKI_V4_RECEIPT_DIR": str(self.receipt_root),
        })
        self.env_gate.start()
        self.addCleanup(self.env_gate.stop)

        def connector(_bound):
            connection = FakeSupplier(self.sent, self.mode)
            self.connections.append(connection)
            return lambda: connection
        self.gate.connector_factory = connector

    def tearDown(self):
        # TestClient may return the terminal frame before the WS finally block
        # has persisted its observation and released the isolated SQLite files.
        for _ in range(100):
            if not self.gate.active_owners:
                break
            time.sleep(.02)
        self.assertFalse(self.gate.active_owners)

    def start(self, **changes):
        return {"type": "start", "request_id": str(uuid.uuid4()),
                "message_id": self.message_id,
                "conversation_id": self.conversation_id,
                "character_id": 1, **changes}

    def socket(self, *, origin=ORIGIN, cookie=True):
        headers = {"origin": origin}
        if not cookie:
            headers["cookie"] = "yosaki_session=invalid"
        return self.client.websocket_connect("/api/voice/realtime/output", headers=headers)

    @staticmethod
    def until(ws, wanted):
        result = []
        for _ in range(12):
            item = ws.receive_json()
            result.append(item)
            if item["type"] == wanted:
                return result
        raise AssertionError(result)

    def test_saved_message_uses_owner_session_binding_and_filtered_server_text(self):
        with self.socket() as ws:
            request = self.start()
            ws.send_json(request)
            events = self.until(ws, "completed")
            self.assertEqual([x["type"] for x in events if x["type"] == "audio"], ["audio"])
            self.assertIn("final_protocol_event", [x["type"] for x in events])
            self.assertEqual(len(self.connections), 1)
            self.assertEqual(len([x for x in self.sent if "inputs" in x]), 1)
            self.assertEqual(self.sent[1]["inputs"], [{
                "text": "今日は静かな夜です。", "voice_id": "synthetic_ivc_voice"}])
            self.assertTrue(all("api_key" not in json.dumps(x) for x in events))
            ws.send_json({"type": "metric", "name": "playback_ended",
                          "request_id": request["request_id"]})
        self.assertFalse(self.gate.active_owners)
        observation = json.loads((self.receipt_root /
            f'{self.manifest["run_id"]}.observation.json').read_text(encoding="utf-8"))
        self.assertEqual(observation["request_outcome"], "completed")
        self.assertTrue(observation["browser_playback_ended_reported"])

    def test_all_rejections_precede_supplier_dispatch(self):
        cases = [
            self.start(message_id=self.partial_id),
            self.start(conversation_id=99999),
            self.start(character_id=99999),
            {**self.start(), "text": "arbitrary client body"},
            {**self.start(), "voice_id": "client_override"},
            {**self.start(), "model_id": "client_override"},
            {**self.start(), "provider_url": "ws://127.0.0.1"},
        ]
        for request in cases:
            with self.subTest(request=request):
                with self.socket() as ws:
                    ws.send_json(request)
                    self.assertEqual(ws.receive_json()["type"], "input_rejected")
        self.assertEqual(self.connections, [])
        with self.assertRaises(WebSocketDisconnect):
            with self.socket(cookie=False):
                pass
        with self.assertRaises(WebSocketDisconnect):
            with self.socket(origin="http://evil.invalid"):
                pass
        self.assertEqual(self.connections, [])

    def test_switches_budget_and_revocation_reject_before_provider(self):
        from backend.app.voice import storage
        for env in ({"YOSAKI_VOICE_ENABLED": "0"},
                    {"YOSAKI_V4_OUTPUT_ENABLED": "0"}):
            with patch.dict(os.environ, env):
                with self.assertRaises(WebSocketDisconnect):
                    with self.socket():
                        pass
        with patch.dict(os.environ, {"YOSAKI_V4_OUTPUT_MAX_ATTEMPTS_PER_OWNER": "0"}):
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(ws.receive_json()["reason"], "output_budget_exhausted")
        with self.main.connect() as db:
            storage.revoke_consent(db, 1)
        try:
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(ws.receive_json()["reason"], "binding_or_permission_invalid")
        finally:
            with self.main.connect() as db:
                storage.put_consent(db, 1)
        with self.main.connect() as db:
            db.execute("UPDATE voice_profiles SET active=0 WHERE character_id=1")
        try:
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(ws.receive_json()["reason"], "binding_or_permission_invalid")
        finally:
            with self.main.connect() as db:
                db.execute("UPDATE voice_profiles SET active=1 WHERE character_id=1")
        self.assertEqual(self.connections, [])

    def test_duplicate_and_concurrent_start_stop_without_text_resend(self):
        self.mode = "hold"
        with self.socket() as first:
            request = self.start()
            first.send_json(request)
            self.until(first, "text_dispatch_attempt")
            with self.socket() as second:
                second.send_json(self.start())
                self.assertEqual(second.receive_json()["reason"], "approval_already_used")
            first.send_json(self.start())
            self.assertEqual(self.until(first, "input_rejected")[-1]["reason"],
                             "duplicate_start")
            first.send_json({"type": "stop", "request_id": request["request_id"]})
            self.until(first, "stopped")
        self.assertFalse(self.gate.active_owners)
        self.assertEqual(len([x for x in self.sent if "inputs" in x]), 1)
        self.assertTrue(self.connections[0].closed)

    def test_browser_disconnect_cancels_and_releases_without_retry(self):
        self.mode = "hold"
        with self.socket() as ws:
            ws.send_json(self.start())
            self.until(ws, "text_dispatch_attempt")
        for _ in range(30):
            if not self.gate.active_owners:
                break
            time.sleep(.05)
        self.assertFalse(self.gate.active_owners)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(len([x for x in self.sent if "inputs" in x]), 1)

    def test_supplier_error_stops_without_retry(self):
        self.mode = "error"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.until(ws, "failed")
            self.assertEqual(events[-1]["error_category"], "quota")
        self.assertEqual(len(self.connections), 1)

    def test_abrupt_transport_after_text_is_unknown_and_never_resends(self):
        self.mode = "remote_close"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.until(ws, "failed")
            self.assertEqual(events[-1]["request_outcome"], "unknown")
            self.assertEqual(events[-1]["close_origin"], "transport_lost")
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(len([x for x in self.sent if "inputs" in x]), 1)
        self.assertTrue(self.approval_used.exists())
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["reason"], "approval_already_used")

    def test_owner_attempt_limit_is_enforced_after_first_run(self):
        with patch.dict(os.environ, {"YOSAKI_V4_OUTPUT_MAX_ATTEMPTS_PER_OWNER": "1"}):
            with self.socket() as ws:
                request = self.start()
                ws.send_json(request)
                self.until(ws, "completed")
                ws.send_json({"type": "metric", "name": "playback_ended",
                              "request_id": request["request_id"]})
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(ws.receive_json()["reason"], "approval_already_used")
        self.assertEqual(len(self.connections), 1)

    def test_consent_revoked_during_output_cancels_task(self):
        from backend.app.voice import storage
        self.mode = "hold"
        with self.socket() as ws:
            ws.send_json(self.start())
            self.until(ws, "text_dispatch_attempt")
            with self.main.connect() as db:
                storage.revoke_consent(db, 1)
            try:
                events = self.until(ws, "stopped")
                self.assertEqual(events[-1]["close_origin"], "local_cancel_requested")
            finally:
                with self.main.connect() as db:
                    storage.put_consent(db, 1)
        self.assertFalse(self.gate.active_owners)
        self.assertEqual(len(self.connections), 1)

    def test_missing_or_mismatched_receipt_never_opens_supplier(self):
        self.approval_path.unlink()
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["reason"], "approval_file_invalid")
        self.assertEqual(self.connections, [])
        self.assertFalse(self.approval_used.exists())
        from backend.app.voice import realtime_receipt
        wrong = realtime_receipt.expected_receipt(self.manifest,
            realtime_receipt.digest(self.manifest), "0" * 64)
        self.approval_path.write_text(json.dumps(wrong), encoding="utf-8")
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["reason"], "approval_mismatch")
        self.assertEqual(self.connections, [])
        self.assertFalse(self.approval_used.exists())

    def test_source_or_runtime_drift_between_preview_and_dispatch_rejects(self):
        with closing(sqlite3.connect(self.source_db)) as source:
            with source:
                source.execute("UPDATE messages SET content='別の文章です。' WHERE id=?", (self.message_id,))
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["reason"], "source_binding_changed")
        self.assertEqual(self.connections, [])
        self.assertFalse(self.approval_used.exists())
        with self.main.connect() as db:
            db.execute("UPDATE messages SET content='変更された本文。' WHERE id=?", (self.message_id,))
        try:
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(ws.receive_json()["reason"], "manifest_or_binding_changed")
        finally:
            with self.main.connect() as db:
                db.execute("UPDATE messages SET content='[whispers] 今日は静かな夜です。' WHERE id=?",
                           (self.message_id,))
        self.assertEqual(self.connections, [])

    def test_fresh_gate_after_restart_cannot_reuse_occupied_receipt(self):
        from backend.app.voice.realtime_product import install
        with self.socket() as ws:
            request = self.start()
            ws.send_json(request)
            self.until(ws, "completed")
            ws.send_json({"type": "metric", "name": "playback_ended",
                          "request_id": request["request_id"]})
        self.assertTrue(self.approval_used.exists())
        fresh_app = FastAPI()
        fresh_gate = install(fresh_app, self.main.connect,
                             self.main.current_session, [ORIGIN])
        fresh_gate.connector_factory = lambda _bound: (lambda: FakeSupplier([], "normal"))
        with TestClient(fresh_app) as client:
            client.cookies.update(self.client.cookies)
            with client.websocket_connect("/api/voice/realtime/output",
                                          headers={"origin": ORIGIN}) as ws:
                ws.send_json(self.start())
                self.assertEqual(ws.receive_json()["reason"], "approval_already_used")
        self.assertEqual(len(self.connections), 1)

    def test_interrupted_dispatch_keeps_receipt_occupied(self):
        class BrokenConnection:
            async def __aenter__(self):
                raise OSError("local fake connect failure")
            async def __aexit__(self, *_):
                return None
        constructed = []
        def factory(_bound):
            constructed.append(1)
            return lambda: BrokenConnection()
        self.gate.connector_factory = factory
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(self.until(ws, "failed")[-1]["request_outcome"], "not_dispatched")
        self.assertTrue(self.approval_used.exists())
        self.assertEqual(constructed, [1])
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["reason"], "approval_already_used")

    def test_claim_is_atomic_across_independent_callers(self):
        from backend.app.voice import realtime_receipt
        approval = realtime_receipt.PreparedApproval(
            self.manifest, realtime_receipt.digest(self.manifest),
            realtime_receipt.code_set_sha256(), self.manifest_path,
            self.approval_path, self.approval_used,
            self.receipt_root / "observation.json")
        def try_claim(_):
            try:
                realtime_receipt.claim(approval)
                return "occupied"
            except realtime_receipt.ApprovalError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(try_claim, range(2)))
        self.assertCountEqual(outcomes, ["occupied", "approval_already_used"])
        self.assertTrue(self.approval_used.exists())
        self.assertEqual(self.connections, [])

    def test_runtime_bootstrap_uses_only_isolated_database(self):
        # Fresh process: main.py must first be imported after the isolated
        # runtime data directory is fixed. The source is a synthetic snapshot.
        root = Path(self.fixture.name) / "runtime"
        root.mkdir()
        program = r'''
import json, os, sqlite3, sys
from pathlib import Path
from scripts import voice_v4_product_runtime as runtime
source, root, message_id = sys.argv[1:]
runtime.SOURCE = Path(source)
runtime.RUN_ROOT = Path(root)
runtime.MESSAGE_ID = int(message_id)
runtime.RUNTIME_DB = runtime.RUN_ROOT / "data" / "yosaki.sqlite3"
runtime.MANIFEST = runtime.RUN_ROOT / "manifest.json"
runtime.RECEIPTS = runtime.RUN_ROOT / "receipts"
runtime.RECEIPTS.mkdir()
manifest = runtime.selected_manifest()
manifest["network_proxy_url"] = None
credential = runtime._make_minimal_database(manifest)
from backend.app import main
with main.connect() as db:
    actual = db.execute("PRAGMA database_list").fetchone()[2]
    messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    profiles = db.execute("SELECT COUNT(*) FROM voice_profiles").fetchone()[0]
    consents = db.execute("SELECT COUNT(*) FROM voice_privacy_consents").fetchone()[0]
assert Path(actual).resolve() == runtime.RUNTIME_DB.resolve()
assert (messages, profiles, consents) == (1, 1, 1)
assert credential.is_file()
print(json.dumps({"isolated": True, "messages": messages,
                  "profiles": profiles, "consents": consents}))
'''
        env = dict(os.environ, YOSAKI_BACKGROUND_TASKS_ENABLED="0",
                   YOSAKI_V4_TTD_PROXY="", ELEVENLABS_API_KEY="fake-local-only")
        result = subprocess.run([sys.executable, "-c", program,
            str(self.source_db), str(root), str(self.message_id)],
            cwd=Path(__file__).resolve().parents[1], env=env,
            capture_output=True, text=True, timeout=35)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertEqual(json.loads(result.stdout.strip()),
                         {"isolated": True, "messages": 1,
                          "profiles": 1, "consents": 1})
        self.assertEqual(self.connections, [])


if __name__ == "__main__":
    unittest.main()
