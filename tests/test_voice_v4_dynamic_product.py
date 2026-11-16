"""Actual isolated product route, temporary SQLite, fake chat and fake TTD."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests.voice_main_test_support import reload_main_with_class_cleanup


ORIGIN = "http://127.0.0.1:15177"
PCM = base64.b64encode(b"\x00\x00\x10\x27" * 240).decode()


class FakeSupplier:
    local_fake_provider = True
    def __init__(self, sent, mode="normal"):
        self.sent = sent
        self.mode = mode
        self.queue = asyncio.Queue()
        self.closed = False

    async def __aenter__(self):
        self.sent.append({"connection": True})
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def send(self, raw):
        item = json.loads(raw)
        self.sent.append(item)
        if "inputs" in item:
            if self.mode == "quota":
                await self.queue.put('{"error":{"code":"quota_exceeded"}}')
            elif self.mode == "disconnect":
                raise OSError("fake transport loss")
            elif self.mode == "slow":
                async def later():
                    await asyncio.sleep(.9)
                    await self.queue.put(json.dumps({"audio": PCM,
                        "is_final_audio_for_turn": True}))
                asyncio.create_task(later())
            else:
                await self.queue.put(json.dumps({"audio": PCM,
                    "is_final_audio_for_turn": True}))
        if item.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self):
        return await self.queue.get()


class DynamicProductRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="yosaki-v4-dynamic-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.receipts = cls.root / "receipts"
        cls.receipts.mkdir()
        cls.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": cls.temp.name,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_V4_DYNAMIC_CHAT_ISOLATED": "1",
            "YOSAKI_V4_DYNAMIC_FAKE_ONLY": "1",
            "YOSAKI_V4_REAL_PROVIDER_ENABLED": "0",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_ALLOWED_ORIGINS": ORIGIN,
            "ELEVENLABS_API_KEY": "fake-only-do-not-use",
        })
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.main = reload_main_with_class_cleanup(cls)
        cls.client = TestClient(cls.main.app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        setup = cls.client.post("/api/auth/setup", json={
            "username": "dynamic_temp_owner",
            "password": "synthetic-test-password-123"})
        assert setup.status_code == 201, setup.text
        from backend.app.voice import storage
        from backend.app.voice.schemas import ProfileInput, VoiceConfiguration
        with cls.main.connect() as db:
            db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) "
                       "VALUES (1,'dynamic isolation','now','now')")
            cls.conversation_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=0,
                voice_id="synthetic_ivc_voice", active=True,
                configuration=VoiceConfiguration(language="ja", audio_format="pcm_24000")))
        cls.gate = cls.main.app.state.voice_v4_dynamic

    def setUp(self):
        from backend.app.voice import realtime_dynamic_receipt as receipts
        self.sent = []
        self.mode = "normal"
        self.gate.fake_mode = "normal"
        self.gate.chat_source_factory = None
        self.gate.active_owners.clear()
        self.gate.connector_factory = lambda _bound: FakeSupplier(self.sent, self.mode)
        self.run_id = "dynamic-test-" + uuid.uuid4().hex[:16]
        self.gate.run_id = self.run_id
        self.gate.receipt_dir = self.receipts
        with self.main.connect() as db:
            from backend.app.voice import storage
            profile = storage.profile(db, 1)
            consent = storage.consent(db, 1)
        self.manifest = {
            "schema_version": 1, "mode": "mock_only", "run_id": self.run_id,
            "generation_id": uuid.uuid4().hex, "owner_id": 1,
            "character_id": 1, "conversation_id": self.conversation_id,
            "profile_revision": profile["revision"],
            "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
            "notice_version": consent["notice_version"],
            "chat_model_id": "isolated_mock_chat_v1", "tts_model_id": "eleven_v4_turbo",
            "output_format": "pcm_24000", "language_code": "ja",
            "limits": {"max_characters": 400, "max_segments": 8,
                       "max_connections": 8, "max_audio_bytes": 100_000,
                       "max_turn_seconds": 5},
        }
        self.prepared = receipts.issue_mock(self.receipts, self.manifest)
        self.request_id = str(uuid.uuid4())

    def start(self, **changes):
        return {"type": "start_chat", "request_id": self.request_id,
                "run_id": self.run_id, "character_id": 1,
                "conversation_id": self.conversation_id, **changes}

    def texts(self):
        return [item["inputs"][0]["text"] for item in self.sent if "inputs" in item]

    def socket(self, *, cookie=True, origin=ORIGIN):
        headers = {"origin": origin}
        if not cookie:
            headers["cookie"] = "yosaki_session=invalid"
        return self.client.websocket_connect("/api/voice/realtime/chat-isolated",
                                             headers=headers)

    def collect(self, ws, *, ack=True):
        events = []
        for _ in range(100):
            try:
                item = ws.receive_json()
            except WebSocketDisconnect:
                break
            events.append(item)
            if item["type"] == "voice_turn_completed" and ack:
                ws.send_json({"type": "metric", "request_id": self.request_id,
                              "name": "playback_ended"})
            if item["type"] in ("turn_summary", "input_rejected"):
                break
        return events

    def test_normal_multi_segment_real_route_and_saved_reconciliation(self):
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        types = [item["type"] for item in events]
        self.assertIn("chat_message_start", types)
        self.assertIn("chat_message_complete", types)
        self.assertIn("voice_turn_completed", types)
        self.assertLess(types.index("audio"), types.index("chat_message_complete"))
        self.assertEqual([item["segment_sequence"] for item in events
                          if item["type"] == "started"], [1, 2, 3])
        self.assertEqual(len(self.texts()), 3)
        self.assertEqual(sum("connection" in item for item in self.sent), 3)
        self.assertNotIn("internal reasoning", str(self.texts()))
        self.assertNotIn("internal monologue", str(self.texts()))
        self.assertNotIn("internal tool", str(self.texts()))
        summary = next(item for item in events if item["type"] == "turn_summary")
        self.assertEqual(summary["segments"], 3)
        self.assertEqual(summary["connections"], 3)
        self.assertTrue(summary["browser_playback_ended"])
        self.assertTrue(self.prepared.used_path.exists())
        complete = next(item for item in events if item["type"] == "chat_message_complete")
        with self.main.connect() as db:
            saved = db.execute("SELECT content,generation_status FROM messages WHERE id=?",
                               (complete["message_id"],)).fetchone()
        self.assertEqual(saved["generation_status"], "completed")
        self.assertEqual(summary["public_text_sha256"], hashlib.sha256(
            saved["content"].encode()).hexdigest())
        self.assertEqual(len(summary["speech_segments"]), 3)
        self.assertEqual([part["sequence"] for part in summary["speech_segments"]],
                         [1, 2, 3])
        self.assertEqual([part["spoken_text"] for part in summary["speech_segments"]],
                         self.texts())
        for part in summary["speech_segments"]:
            source = saved["content"][part["source_start"]:part["source_end"]]
            self.assertEqual(part["source_sha256"], hashlib.sha256(
                source.encode()).hexdigest())
            self.assertEqual(part["spoken_text_sha256"], hashlib.sha256(
                part["spoken_text"].encode()).hexdigest())
        self.assertEqual("".join(item["text"] for item in events if
            item["type"] == "chat_text_delta"), saved["content"])
        self.assertEqual(sum(len(text) for text in self.texts()), summary["characters"])
        self.assertRegex(complete["message_version_sha256"], r"^[0-9a-f]{64}$")

    def test_missing_mismatched_and_reused_receipt_never_connect(self):
        self.prepared.used_path.unlink(missing_ok=True)
        (self.receipts / f"{self.run_id}.approval.json").unlink()
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        self.assertEqual(self.sent, [])

        from backend.app.voice import realtime_dynamic_receipt as receipts
        self.prepared = receipts.issue_mock(self.receipts, self.manifest)
        with self.socket() as ws:
            ws.send_json(self.start(character_id=2))
            self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        self.assertEqual(self.sent, [])
        with self.socket() as ws:
            ws.send_json(self.start())
            self.collect(ws)
        prior = len(self.sent)
        with self.socket() as ws:
            ws.send_json(self.start())
            rejected = self.collect(ws)
        self.assertEqual(rejected[0]["type"], "input_rejected")
        self.assertEqual(len(self.sent), prior)

    def test_slow_supplier_does_not_delay_chat_save(self):
        self.mode = "slow"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        types = [item["type"] for item in events]
        self.assertLess(types.index("chat_message_complete"), types.index("voice_turn_completed"))
        self.assertEqual(len(self.texts()), 3)
        self.assertTrue(self.prepared.used_path.exists())

    def test_stop_voice_keeps_text_and_stop_generation_saves_partial(self):
        self.mode = "slow"
        with self.socket() as ws:
            ws.send_json(self.start())
            while True:
                event = ws.receive_json()
                if event["type"] == "chat_text_delta":
                    break
            ws.send_json({"type": "stop_voice", "request_id": self.request_id})
            events = self.collect(ws, ack=False)
        complete = next(item for item in events if item["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "completed")
        self.assertTrue(any(item["type"] == "voice_turn_stopped" for item in events))
        self.assertEqual(sum("connection" in item for item in self.sent), 1)

        # New test run/receipt; an explicit generation stop saves a stopped row.
        self.setUp()
        with self.socket() as ws:
            ws.send_json(self.start())
            while True:
                event = ws.receive_json()
                if event["type"] == "chat_text_delta":
                    break
            ws.send_json({"type": "stop_generation", "request_id": self.request_id})
            events = self.collect(ws, ack=False)
        complete = next(item for item in events if item["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "stopped")
        with self.main.connect() as db:
            status = db.execute("SELECT generation_status FROM messages WHERE id=?",
                                (complete["message_id"],)).fetchone()[0]
        self.assertEqual(status, "stopped")

    def test_save_failure_and_final_hash_mismatch_stop_remaining_voice(self):
        for mode in ("save_failure", "final_mismatch"):
            self.setUp()
            self.gate.fake_mode = mode
            with self.socket() as ws:
                ws.send_json(self.start())
                events = self.collect(ws, ack=False)
            self.assertTrue(any(item["type"] == "voice_turn_stopped" for item in events))
            self.assertFalse(any(item["type"] == "voice_turn_completed" for item in events))
            self.assertTrue(self.prepared.used_path.exists())

    def test_budget_exhaustion_stops_without_silent_drop(self):
        from backend.app.voice import realtime_dynamic_receipt as receipts
        (self.receipts / f"{self.run_id}.approval.json").unlink()
        self.manifest["limits"]["max_characters"] = 24
        self.prepared = receipts.issue_mock(self.receipts, self.manifest)
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws, ack=False)
        stopped = next(item for item in events if item["type"] == "voice_turn_stopped")
        self.assertEqual(stopped["reason"], "speech_character_or_segment_budget")
        self.assertLessEqual(len(self.texts()), 1)

    def test_receipt_budget_accumulates_and_rejects_real_connector(self):
        from backend.app.voice import realtime_dynamic_receipt as receipts
        budget = receipts.TurnBudget(self.prepared)
        budget.reserve_segment(200)
        budget.reserve_segment(200)
        with self.assertRaisesRegex(receipts.ReceiptError, "text_budget"):
            budget.reserve_segment(1)
        self.gate.connector_factory = lambda _bound: object()
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws, ack=False)
        self.assertEqual(sum("connection" in item for item in self.sent), 0)
        self.assertFalse(self.prepared.used_path.exists())
        self.assertTrue(any(item["type"] == "voice_turn_stopped" for item in events))

    def test_mock_receipt_atomic_claim_and_restart_reuse_rejection(self):
        from backend.app.voice import realtime_dynamic_receipt as receipts
        receipts.claim(self.prepared)
        with self.assertRaisesRegex(receipts.ReceiptError, "already_used"):
            receipts.prepare(self.receipts, self.run_id)
        with self.assertRaisesRegex(receipts.ReceiptError, "already_used"):
            receipts.claim(self.prepared)
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        self.assertEqual(self.sent, [])

    def test_auth_origin_scope_and_disabled_gate_reject_before_supplier(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.socket(cookie=False) as ws:
                ws.send_json(self.start())
                ws.receive_json()
        with self.assertRaises(WebSocketDisconnect):
            with self.socket(origin="http://untrusted.invalid") as ws:
                ws.send_json(self.start())
                ws.receive_json()
        with self.socket() as ws:
            ws.send_json(self.start(conversation_id=self.conversation_id + 1000))
            self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        with patch.dict(os.environ, {"YOSAKI_V4_DYNAMIC_CHAT_ISOLATED": "0"}):
            with self.assertRaises(WebSocketDisconnect):
                with self.socket() as ws:
                    ws.send_json(self.start())
                    ws.receive_json()
        self.assertEqual(self.sent, [])
        self.assertFalse(self.prepared.used_path.exists())

    def test_concurrent_start_and_disconnect_do_not_reuse_dispatch(self):
        self.mode = "slow"
        with self.socket() as first:
            first.send_json(self.start())
            while first.receive_json()["type"] != "chat_text_delta":
                pass
            with self.socket() as second:
                second.send_json({**self.start(), "request_id": str(uuid.uuid4())})
                denied = self.collect(second)
            self.assertEqual(denied[0]["type"], "input_rejected")
            first.send_json({"type": "stop_voice", "request_id": self.request_id})
            self.collect(first, ack=False)
        prior = len(self.sent)
        with self.socket() as retry:
            retry.send_json(self.start())
            self.assertEqual(self.collect(retry)[0]["type"], "input_rejected")
        self.assertEqual(len(self.sent), prior)


if __name__ == "__main__":
    unittest.main()
