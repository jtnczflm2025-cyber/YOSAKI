"""Final isolated product route with fake Responses and fake TTD only."""

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from starlette.websockets import WebSocketDisconnect
from tests.voice_main_test_support import reload_main_with_class_cleanup


ORIGIN = "http://127.0.0.1:15188"
TEXT = "今夜は、ゆっくりでいい。ここで、君の話を聞いている。"
PROMPT = "阿夜，请只用日语说两句简短的话：先说今晚可以慢慢来，再说你会在这里听我讲。只输出正文，合计不超过 50 个日文字符；不要 Markdown 或 Audio Tags。"
REAL_ASYNC_CLIENT = httpx.AsyncClient


def sse(event, label=None):
    return (f"event: {label or event['type']}\n"
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n").encode("utf-8")


class ChunkedSSE(httpx.AsyncByteStream):
    """Synthetic wire bytes, not a recording of an R3 provider response."""

    def __init__(self, frames, pause_after=None):
        self.frames = frames
        self.pause_after = pause_after or {}

    async def __aiter__(self):
        for index, frame in enumerate(self.frames):
            for offset in range(0, len(frame), 3):
                yield frame[offset:offset + 3]
            if index in self.pause_after:
                await asyncio.sleep(self.pause_after[index])


class HangingConnect:
    local_fake_provider = True

    def __init__(self):
        self.enter_started = threading.Event()
        self.enter_cancelled = threading.Event()
        self.exit_called = threading.Event()

    async def __aenter__(self):
        self.enter_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.enter_cancelled.set()
            raise

    async def __aexit__(self, *_):
        self.exit_called.set()


class WaitingSupplier:
    local_fake_provider = True

    def __init__(self):
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.recv_cancelled = threading.Event()
        self.sent = []
        self.late_audio = asyncio.Queue()

    async def __aenter__(self):
        self.entered.set()
        return self

    async def __aexit__(self, *_):
        self.closed.set()

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        try:
            return await self.late_audio.get()
        except asyncio.CancelledError:
            self.recv_cancelled.set()
            raise


class FakeSupplier:
    local_fake_provider = True

    def __init__(self, sent, mode="normal"):
        self.sent = sent
        self.mode = mode
        self.queue = asyncio.Queue()

    async def __aenter__(self):
        self.sent.append("connection")
        return self

    async def __aexit__(self, *_):
        self.sent.append("closed")

    async def send(self, raw):
        import base64
        item = json.loads(raw)
        self.sent.append(item)
        if "inputs" in item:
            if self.mode == "failure":
                await self.queue.put('{"error":{"code":"quota_exceeded"}}')
            else:
                pcm = base64.b64encode(b"\x00\x00\x10\x27" * 240).decode()
                await self.queue.put(json.dumps({"audio": pcm,
                    "is_final_audio_for_turn": True}))
        if item.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self):
        if self.mode == "disconnect":
            raise OSError("synthetic_transport_lost")
        return await self.queue.get()


class CandidateRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="yosaki-v4-candidate-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.receipts = Path(cls.temp.name) / "receipts"
        cls.receipts.mkdir()
        cls.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": cls.temp.name,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED": "1",
            "YOSAKI_V4_DYNAMIC_REAL_EXECUTE": "0",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_ALLOWED_ORIGINS": ORIGIN,
            "ELEVENLABS_API_KEY": "fake-only-not-a-key",
        })
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.main = reload_main_with_class_cleanup(cls)
        cls.client = TestClient(cls.main.app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        setup = cls.client.post("/api/auth/setup", json={
            "username": "candidate_test_owner", "password": "fake-password-only-2026"})
        assert setup.status_code == 201, setup.text
        from backend.app.voice import storage
        from backend.app.voice.schemas import ProfileInput, VoiceConfiguration
        with cls.main.connect() as db:
            db.execute("INSERT INTO model_profiles(id,name,provider_name,base_url,model_name,"
                "protocol,temperature,top_p,max_tokens,context_limit,supports_streaming,enabled,"
                "created_at,updated_at) VALUES (5,'isolated','OpenAI','https://api.openai.com/v1',"
                "'gpt-6.1-sol','chatgpt_plan',0.8,1.0,2048,128000,1,1,'now','now')")
            db.execute("INSERT INTO conversations(id,character_id,title,model_profile_id,"
                "created_at,updated_at) VALUES (5,1,'candidate',5,'now','now')")
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(expected_revision=0,
                voice_id="synthetic_candidate_voice", active=True,
                configuration=VoiceConfiguration(language="ja", audio_format="mp3_44100_128")))
        cls.gate = cls.main.app.state.voice_v4_candidate

    def setUp(self):
        from backend.app.voice import realtime_dynamic_candidate_receipt as receipts
        from backend.app.voice.realtime_dynamic_candidate import network_proxy
        self.sent = []
        self.model_calls = []
        self.supplier_mode = "normal"
        self.model_mode = "normal"
        self.gate.active_owners.clear()
        self.gate.ttd_connector_factory = lambda _voice: FakeSupplier(self.sent, self.supplier_mode)
        self.gate.model_events_factory = self.fake_model
        self.gate.receipt_dir = self.receipts
        self.gate.run_id = "candidate-test-" + uuid.uuid4().hex[:16]
        self.request_id = str(uuid.uuid4())
        with self.main.connect() as db:
            from backend.app.voice.realtime_dynamic_context import freeze
            from backend.app import chatgpt_plan
            from backend.app.voice import storage
            self.frozen = freeze(db, owner_id=1, character_id=1, conversation_id=5,
                model_profile_id=5, prompt=PROMPT, max_output_tokens=128,
                build_messages=self.main.build_messages,
                plan_payload=chatgpt_plan.payload)
            profile = storage.profile(db, 1)
            consent = storage.consent(db, 1)
        self.manifest = {"schema_version": 1, "mode": "real_dynamic_candidate",
            "run_id": self.gate.run_id, "generation_id": uuid.uuid4().hex,
            "owner_id": 1, "character_id": 1, "conversation_id": 5,
            "model_profile_id": 5, "context_sha256": self.frozen.context_sha256,
            "prompt": PROMPT, "profile_revision": profile["revision"],
            "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
            "notice_version": consent["notice_version"],
            "chat_provider": "chatgpt_plan", "chat_model_id": "gpt-6.1-sol",
            "tts_model_id": "eleven_v4_turbo", "output_format": "pcm_24000",
            "language_code": "ja", "network_proxy_url": network_proxy(),
            "limits": receipts.LIMITS}
        self.prepared = receipts.issue_mock(self.receipts, self.manifest)

    async def fake_model(self, frozen):
        self.model_calls.append(frozen.body)
        if self.model_mode == "slow":
            await asyncio.sleep(.8)
        if self.model_mode == "reasoning":
            yield {"type": "response.reasoning_summary_text.delta",
                   "delta": "private reasoning fixture"}
        if self.model_mode == "tool":
            yield {"type": "response.output_item.added", "output_index": 0,
                   "item": {"type": "function_call", "call_id": "call-1",
                            "name": "unsafe_tool", "arguments": "{}"}}
            return
        if self.model_mode == "unknown":
            yield {"type": "provider_unrecognized_event"}
            return
        if self.model_mode == "parse_error":
            yield {"type": "response.output_item.added", "item": {
                "type": "function_call", "name": "fixture"}}
            return
        yield {"type": "response.output_text.delta", "delta": TEXT[:12]}
        await asyncio.sleep(.05)
        yield {"type": "response.output_text.delta", "delta": TEXT[12:]}
        if self.model_mode == "no_complete":
            return
        if self.model_mode == "provider_failed":
            yield {"type": "response.failed", "response": {"error": {
                "code": "server_error"}, "usage": {"input_tokens": 30}}}
            return
        if self.model_mode == "incomplete":
            yield {"type": "response.incomplete", "response": {"incomplete_details": {
                "reason": "max_output_tokens"}}}
        elif self.model_mode == "too_long":
            yield {"type": "response.output_text.delta", "delta": "長い。" * 30}
            yield {"type": "response.completed", "response": {}}
        else:
            await asyncio.sleep(.1)
            yield {"type": "response.completed", "response": {"usage": {
                "input_tokens": 30, "output_tokens": 20, "total_tokens": 50}}}

    def start(self, **changes):
        return {"type": "start_chat", "request_id": self.request_id,
            "run_id": self.gate.run_id, "character_id": 1,
            "conversation_id": 5, **changes}

    def socket(self, *, cookie=True):
        headers = {"origin": ORIGIN}
        if not cookie:
            headers["cookie"] = "yosaki_session=invalid"
        return self.client.websocket_connect("/api/voice/realtime/chat-candidate",
                                             headers=headers)

    def collect(self, ws, ack=True):
        result = []
        for _ in range(100):
            try:
                item = ws.receive_json()
            except WebSocketDisconnect:
                break
            result.append(item)
            if item["type"] == "voice_turn_completed" and ack:
                ws.send_json({"type": "metric", "request_id": self.request_id,
                              "name": "playback_ended"})
            if item["type"] in ("turn_summary", "input_rejected"):
                break
        return result

    def diagnostics(self):
        path = self.receipts / f"{self.gate.run_id}.diagnostic.jsonl"
        self.assertTrue(path.exists())
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def replay_raw_sse(self, frames, *, pause_after=None):
        """Product WS -> real SSE decoder -> StreamState, all with MockTransport."""
        from backend.app.voice import realtime_dynamic_candidate as candidate
        requests = []

        def handler(request):
            requests.append({"url": str(request.url),
                             "body": json.loads(request.content),
                             "auth_present": bool(request.headers.get("authorization"))})
            return httpx.Response(200, stream=ChunkedSSE(frames, pause_after))

        def client_factory(**kwargs):
            return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

        self.gate.model_events_factory = lambda frozen: self.gate.real_model_events(frozen)
        with patch.dict(os.environ, {"YOSAKI_V4_SOURCE_DATA_DIR": self.temp.name}), \
             patch.object(candidate, "_read_only_plan_token", return_value="synthetic-only"), \
             patch.object(candidate.httpx, "AsyncClient", side_effect=client_factory):
            with self.socket() as ws:
                ws.send_json(self.start())
                events = self.collect(ws)
        return events, requests

    def test_voice_error_before_model_completion_preserves_completion_and_usage(self):
        self.supplier_mode = "failure"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "completed")
        self.assertEqual(complete["model_usage"]["output_tokens"], 20)
        self.assertNotIn("voice_turn_completed", [x["type"] for x in events])
        records = self.diagnostics()
        self.assertTrue(all(x["run_id"] == self.gate.run_id and
            x["generation_id"] == self.manifest["generation_id"] for x in records))
        self.assertEqual([x["elapsed_ms"] for x in records],
            sorted(x["elapsed_ms"] for x in records))
        self.assertNotIn("fake-only-not-a-key", json.dumps(records))
        self.assertTrue(any(x["event"] == "segment_pre_send" and
            x["spoken_text"] and x["characters"] == len(x["spoken_text"]) and
            x["source_end"] > x["source_start"] for x in records))
        self.assertTrue(any(x["event"] == "text_send_return" and
            x["request_outcome"] == "send_returned_provider_receipt_unconfirmed"
            for x in records))
        self.assertTrue(any(x["event"] == "model_terminal" and
            x["model_status"] == "completed" and x["usage"]["output_tokens"] == 20
            for x in records))
        self.assertTrue(any(x["event"] == "turn_summary" and
            x["tts_connections"] == 1 and x["tts_characters"] > 0 for x in records))

    def test_missing_completion_is_incomplete_with_diagnostic(self):
        self.model_mode = "no_complete"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "incomplete")
        self.assertEqual(complete["finish_reason"], "model_stream_without_completion")
        self.assertTrue(any(x["event"] == "model_terminal" and
            x["model_status"] == "incomplete" for x in self.diagnostics()))

    def test_provider_failed_usage_retained_and_parser_error_separated(self):
        self.model_mode = "provider_failed"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["finish_reason"], "provider_reported_failure")
        self.assertEqual(complete["model_usage"], {"input_tokens": 30})
        self.assertTrue(any(x["event"] == "model_failure" and
            x["stage"] == "provider_event" and x["error_code"] == "server_error"
            for x in self.diagnostics()))

    def test_parser_error_and_save_failure_have_distinct_diagnostics(self):
        self.model_mode = "parse_error"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["finish_reason"], "model_parse_error")
        self.assertTrue(any(x["event"] == "model_failure" and
            x["error_code"] == "model_parse_error" for x in self.diagnostics()))

    def test_transport_disconnect_does_not_fail_completed_chat(self):
        self.supplier_mode = "disconnect"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "completed")
        self.assertEqual(complete["model_usage"]["output_tokens"], 20)
        self.assertTrue(any(x["event"] == "voice_terminal" and
            x["voice_status"] == "failed" for x in self.diagnostics()))

    def test_save_failure_still_records_known_counts(self):
        with patch.object(self.gate, "saved_receipt",
                          side_effect=sqlite3.OperationalError("synthetic_save_failure")):
            with self.socket() as ws:
                ws.send_json(self.start())
                self.collect(ws)
        records = self.diagnostics()
        self.assertTrue(any(x["event"] == "message_save_failed" and
            x["error_code"] == "sqlite_error" for x in records))
        self.assertTrue(any(x["event"] == "turn_summary" and
            x["model_generations"] == 1 and
            x["usage"]["output_tokens"] == 20 for x in records))

    def test_diagnostic_write_failure_blocks_provider_dispatch(self):
        from backend.app.voice.realtime_dynamic_diagnostics import RunDiagnostics
        original = RunDiagnostics._write

        def failing_write(log, item):
            if item["event"] == "model_request_attempt":
                raise OSError("synthetic_diagnostic_write_failure")
            return original(log, item)

        with patch.object(RunDiagnostics, "_write", failing_write):
            with self.socket() as ws:
                ws.send_json(self.start())
                events = self.collect(ws)
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.sent, [])
        self.assertTrue(self.prepared.used_path.exists())
        summary = next(x for x in events if x["type"] == "turn_summary")
        self.assertEqual(summary["diagnostic_status"], "failed")
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")

    def test_terminal_diagnostic_write_failure_keeps_completed_text(self):
        from backend.app.voice.realtime_dynamic_diagnostics import RunDiagnostics
        original = RunDiagnostics._write

        def failing_write(log, item):
            if item["event"] == "model_terminal":
                raise OSError("synthetic_diagnostic_terminal_failure")
            return original(log, item)

        with patch.object(RunDiagnostics, "_write", failing_write):
            with self.socket() as ws:
                ws.send_json(self.start())
                events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "completed")
        self.assertEqual(complete["model_usage"]["output_tokens"], 20)
        summary = next(x for x in events if x["type"] == "turn_summary")
        self.assertEqual(summary["diagnostic_status"], "failed")
        self.assertEqual(len(self.model_calls), 1)

    def test_preview_exposes_exact_request_and_excludes_daily_context(self):
        r = self.client.get("/api/voice/realtime/chat-candidate/preview",
            params={"character_id": 1, "conversation_id": 5})
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()["model_request"]
        self.assertEqual(p["context_sha256"], self.manifest["context_sha256"])
        self.assertEqual(p["request_body"]["max_output_tokens"], 128)
        self.assertEqual(p["request_body"]["input"], [{"role": "user", "content": PROMPT}])
        self.assertNotIn("tools", p["request_body"])
        self.assertTrue(all(m["role"] in ("system", "user") for m in p["messages"]))
        self.assertTrue(all(block["included"] is False for block in
            p["enabled_prompt_blocks"] if block["injection_position"] == "memory"))
        for block in p["enabled_prompt_blocks"]:
            if block["injection_position"] == "memory":
                self.assertNotIn(block["content"], p["request_body"]["instructions"])
        self.assertEqual(p["persona_characters"], sum(len(m["content"])
                         for m in p["messages"][:-1]))

    def test_success_uses_one_model_and_bounded_ttd(self):
        with patch("backend.app.voice.realtime_dynamic_candidate.httpx.AsyncClient",
                   side_effect=AssertionError("external_model_http_attempt")), \
             patch("backend.app.voice.realtime_dynamic_candidate.RealTTDConnector",
                   side_effect=AssertionError("external_ttd_attempt")):
            with self.socket() as ws:
                ws.send_json(self.start())
                events = self.collect(ws)
        types = [x["type"] for x in events]
        self.assertIn("chat_message_complete", types)
        self.assertIn("voice_turn_completed", types)
        self.assertLess(types.index("audio"), types.index("chat_message_complete"))
        self.assertEqual(len(self.model_calls), 1)
        summary = next(x for x in events if x["type"] == "turn_summary")
        self.assertEqual(summary["model_generations"], 1)
        self.assertEqual(summary["model_usage"], {"input_tokens": 30,
            "output_tokens": 20, "total_tokens": 50})
        self.assertLessEqual(summary["tts_characters"], 80)
        self.assertLessEqual(summary["tts_connections"], 2)
        self.assertEqual(len(summary["speech_segments"]), summary["tts_segments"])
        for segment in summary["speech_segments"]:
            source = TEXT[segment["source_start"]:segment["source_end"]]
            self.assertEqual(segment["source_sha256"], hashlib.sha256(
                source.encode()).hexdigest())
        self.assertTrue(summary["browser_playback_ended"])
        self.assertTrue(self.prepared.used_path.exists())
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        with self.main.connect() as db:
            row = db.execute("SELECT content,generation_status FROM messages WHERE id=?",
                             (complete["message_id"],)).fetchone()
        self.assertEqual(row["content"], TEXT)
        self.assertEqual(row["generation_status"], "completed")

    def test_missing_receipt_and_scope_reject_before_model(self):
        self.prepared.used_path.unlink(missing_ok=True)
        (self.receipts / f"{self.gate.run_id}.approval.json").unlink()
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.sent, [])

    def test_context_drift_rejects_before_claim(self):
        with self.main.connect() as db:
            original = db.execute("SELECT system_prompt FROM characters WHERE id=1").fetchone()[0]
            db.execute("UPDATE characters SET system_prompt=? WHERE id=1", (original + " 临时变化",))
        try:
            with self.socket() as ws:
                ws.send_json(self.start())
                events = self.collect(ws)
            self.assertEqual(events[0]["type"], "input_rejected")
            self.assertEqual(self.model_calls, [])
            self.assertFalse(self.prepared.used_path.exists())
        finally:
            with self.main.connect() as db:
                db.execute("UPDATE characters SET system_prompt=? WHERE id=1", (original,))

    def test_model_profile_or_proxy_drift_rejects_before_model(self):
        with self.main.connect() as db:
            original = db.execute("SELECT temperature FROM model_profiles WHERE id=5").fetchone()[0]
            db.execute("UPDATE model_profiles SET temperature=? WHERE id=5", (original + .1,))
        try:
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
            self.assertEqual(self.model_calls, [])
            self.assertFalse(self.prepared.used_path.exists())
        finally:
            with self.main.connect() as db:
                db.execute("UPDATE model_profiles SET temperature=? WHERE id=5", (original,))
        with patch("backend.app.voice.realtime_dynamic_candidate.network_proxy",
                   return_value="http://127.0.0.1:9999"):
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        self.assertEqual(self.model_calls, [])

    def test_consent_revoked_before_start_rejects_without_claim(self):
        with self.main.connect() as db:
            db.execute("DELETE FROM voice_privacy_consents WHERE owner_id=1")
        try:
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
            self.assertEqual(self.model_calls, [])
            self.assertFalse(self.prepared.used_path.exists())
        finally:
            with self.main.connect() as db:
                from backend.app.voice import storage
                storage.put_consent(db, 1)

    def test_code_fingerprint_mismatch_rejects_without_claim(self):
        path = self.receipts / f"{self.gate.run_id}.approval.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["code_sha256"] = "0" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
        self.assertEqual(self.model_calls, [])
        self.assertFalse(self.prepared.used_path.exists())

    def test_reuse_after_completion_or_restart_rejected(self):
        with self.socket() as ws:
            ws.send_json(self.start())
            self.collect(ws)
        self.gate.active_owners.clear()  # Simulated process restart.
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertEqual(events[0]["type"], "input_rejected")
        self.assertEqual(len(self.model_calls), 1)

    def test_incomplete_model_is_not_completed_and_voice_stops(self):
        self.model_mode = "incomplete"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "incomplete")
        self.assertNotIn("voice_turn_completed", [x["type"] for x in events])
        self.assertEqual(len(self.model_calls), 1)

    def test_supplier_failure_does_not_regenerate_model(self):
        self.supplier_mode = "failure"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertIn("voice_turn_stopped", [x["type"] for x in events])
        self.assertEqual(len(self.model_calls), 1)
        self.assertIn("chat_message_complete", [x["type"] for x in events])

    def test_reasoning_is_not_speech_and_tool_request_is_rejected(self):
        self.model_mode = "reasoning"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        texts = [item["inputs"][0]["text"] for item in self.sent
                 if isinstance(item, dict) and "inputs" in item]
        self.assertNotIn("private reasoning fixture", "".join(texts))
        self.assertNotIn("private reasoning fixture", json.dumps(self.diagnostics(),
            ensure_ascii=False))
        self.assertIn("chat_message_complete", [x["type"] for x in events])

    def test_stop_voice_keeps_model_and_text_save_running(self):
        self.model_mode = "slow"
        with self.socket() as ws:
            ws.send_json(self.start())
            while ws.receive_json()["type"] != "chat_text_delta":
                pass
            ws.send_json({"type": "stop_voice", "request_id": self.request_id})
            events = self.collect(ws)
        self.assertIn("chat_message_complete", [x["type"] for x in events])
        self.assertIn("voice_turn_stopped", [x["type"] for x in events])
        self.assertEqual(len(self.model_calls), 1)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "completed")

    def test_tool_call_cannot_be_dispatched_or_spoken(self):
        self.model_mode = "tool"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.model_calls), 1)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")

    def test_model_timeout_or_unknown_result_is_not_retried(self):
        async def timeout_model(frozen):
            self.model_calls.append(frozen.body)
            raise TimeoutError("synthetic_model_timeout")
            yield {}
        self.gate.model_events_factory = timeout_model
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.prepared.used_path.exists())
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], "model_timeout")
        self.assertTrue(any(x["event"] == "model_failure" and
            x["timeout_type"] == "model_generation" for x in self.diagnostics()))

    def test_unrecognized_model_event_stops_without_ttd(self):
        self.model_mode = "unknown"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(self.sent, [])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")

    def test_tts_budget_exhaustion_preserves_text(self):
        self.model_mode = "too_long"
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertIn("voice_turn_stopped", [x["type"] for x in events])
        self.assertEqual(len(self.model_calls), 1)

    def test_mock_receipt_cannot_use_real_connector(self):
        self.gate.ttd_connector_factory = lambda _voice: object()
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(self.sent, [])
        self.assertIn("voice_turn_stopped", [x["type"] for x in events])

    def test_missing_login_rejects_before_model_or_ttd(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.socket(cookie=False) as ws:
                ws.send_json(self.start())
                ws.receive_json()
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.sent, [])
        self.assertFalse(self.prepared.used_path.exists())

    def test_concurrent_start_is_rejected_without_second_model(self):
        self.model_mode = "slow"
        with self.socket() as first:
            first.send_json(self.start())
            self.assertEqual(first.receive_json()["type"], "chat_message_start")
            with self.socket() as second:
                second.send_json(self.start(request_id=str(uuid.uuid4())))
                self.assertEqual(self.collect(second)[0]["type"], "input_rejected")
            self.collect(first)
        self.assertEqual(len(self.model_calls), 1)

    def test_stop_generation_cancels_model_and_marks_partial(self):
        self.model_mode = "slow"
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["type"], "chat_message_start")
            ws.send_json({"type": "stop_generation", "request_id": self.request_id})
            events = self.collect(ws)
        self.assertIn("voice_turn_stopped", [x["type"] for x in events])
        self.assertLessEqual(len(self.model_calls), 1)
        self.assertTrue(self.prepared.used_path.exists())
        with self.main.connect() as db:
            status = db.execute("SELECT generation_status FROM messages WHERE role='assistant' "
                                "ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(status, "stopped")
        self.assertTrue(any(x["event"] == "cancel" and
            x["cancel_source"] == "user_stop_generation" for x in self.diagnostics()))

    def test_profile_revocation_stops_without_regeneration(self):
        self.model_mode = "slow"
        with self.socket() as ws:
            ws.send_json(self.start())
            self.assertEqual(ws.receive_json()["type"], "chat_message_start")
            with self.main.connect() as db:
                db.execute("DELETE FROM voice_privacy_consents WHERE owner_id=1")
            events = self.collect(ws)
        self.assertIn("voice_turn_stopped", [x["type"] for x in events])
        self.assertLessEqual(len(self.model_calls), 1)
        self.assertEqual(self.sent, [])
        with self.main.connect() as db:
            from backend.app.voice import storage
            storage.put_consent(db, 1)

    def test_durable_claim_is_atomic_between_competing_processes(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.app.voice import realtime_dynamic_candidate_receipt as receipts
        def attempt():
            try:
                receipts.claim(self.prepared)
                return "claimed"
            except receipts.ReceiptError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), ["claimed", "rejected"])

    def test_disabled_real_execute_never_uses_real_approved_receipt(self):
        path = self.receipts / f"{self.gate.run_id}.approval.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["provider_kind"] = "real_approved"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.socket() as ws:
            ws.send_json(self.start())
            events = self.collect(ws)
        self.assertEqual(events[0]["type"], "input_rejected")
        self.assertEqual(self.model_calls, [])
        self.assertFalse(self.prepared.used_path.exists())

    def test_real_candidate_requires_reviewed_display_switch(self):
        path = self.receipts / f"{self.gate.run_id}.approval.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["provider_kind"] = "real_approved"
        path.write_text(json.dumps(value), encoding="utf-8")
        with patch.dict(os.environ, {"YOSAKI_V4_DYNAMIC_REAL_EXECUTE": "1",
                                  "YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "0"}):
            response = self.client.get("/api/voice/realtime/chat-candidate/preview",
                params={"character_id": 1, "conversation_id": 5})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.sent, [])
        self.assertFalse(self.prepared.used_path.exists())

    def test_real_approved_preview_and_dispatch_with_no_network_substitutes(self):
        """Exercise the real receipt branch without model or TTD access."""
        path = self.receipts / f"{self.gate.run_id}.approval.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["provider_kind"] = "real_approved"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.gate.model_events_factory = None
        self.gate.ttd_connector_factory = None

        class NoNetworkConnector:
            def __init__(_, _key, _proxy):
                pass

            def __call__(_):
                return FakeSupplier(self.sent)

        with patch.dict(os.environ, {
                "YOSAKI_V4_DYNAMIC_REAL_EXECUTE": "1",
                "YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1",
                "YOSAKI_V4_SOURCE_DATA_DIR": self.temp.name}), \
             patch("backend.app.voice.realtime_dynamic_candidate._read_only_plan_token",
                   return_value="synthetic-token-not-sent"), \
             patch("backend.app.voice.realtime_dynamic_candidate.RealTTDConnector",
                   NoNetworkConnector), \
             patch.object(self.gate, "real_model_events", self.fake_model):
            preview = self.client.get("/api/voice/realtime/chat-candidate/preview",
                params={"character_id": 1, "conversation_id": 5})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["receipt_kind"], "real_approved")
            self.assertTrue(preview.json()["approved_for_real_execution"])
            self.assertEqual(preview.json()["run_id"], self.gate.run_id)

            with self.socket() as ws:
                ws.send_json(self.start(run_id="wrong-run-id"))
                self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
            self.assertEqual(self.model_calls, [])
            self.assertEqual(self.sent, [])

            with self.socket() as ws:
                ws.send_json(self.start(run_id=preview.json()["run_id"]))
                events = self.collect(ws)
            self.assertIn("chat_message_complete", [x["type"] for x in events])
            self.assertEqual(len(self.model_calls), 1)
            self.assertTrue(self.prepared.used_path.exists())
            self.assertEqual(self.client.get(
                "/api/voice/realtime/chat-candidate/preview", params={
                    "character_id": 1, "conversation_id": 5}).status_code, 409)
            with self.socket() as ws:
                ws.send_json(self.start())
                self.assertEqual(self.collect(ws)[0]["type"], "input_rejected")
            self.assertEqual(len(self.model_calls), 1)

    def test_real_candidate_preview_rejects_unknown_approval_kind(self):
        path = self.receipts / f"{self.gate.run_id}.approval.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["provider_kind"] = "unrecognized"
        path.write_text(json.dumps(value), encoding="utf-8")
        response = self.client.get("/api/voice/realtime/chat-candidate/preview",
            params={"character_id": 1, "conversation_id": 5})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.sent, [])

    def test_raw_sse_notices_after_public_text_complete_with_usage(self):
        secret = "synthetic_private_reasoning_must_not_escape"
        frames = [
            sse({"type": "response.queued"}),
            sse({"type": "response.created"}),
            sse({"type": "response.in_progress"}),
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse({"type": "response.metadata", "metadata": {}}),
            sse({"type": "response.reasoning_summary_part.added",
                 "part": {"type": "summary_text", "text": secret}}),
            sse({"type": "response.reasoning_summary_text.delta", "delta": secret}),
            sse({"type": "response.reasoning_summary_text.done", "text": secret}),
            sse({"type": "response.reasoning_summary_part.done",
                 "part": {"type": "summary_text", "text": secret}}),
            sse({"type": "response.reasoning_text.delta", "delta": secret}),
            sse({"type": "response.reasoning_text.done", "text": secret}),
            sse({"type": "response.output_text.delta", "delta": TEXT[12:]}),
            sse({"type": "response.output_text.done", "text": TEXT}),
            sse({"type": "response.metadata", "metadata": {}}),
            sse({"type": "response.output_text.annotation.added",
                 "annotation": {"type": "url_citation", "url": "https://invalid.example/"}}),
            sse({"type": "response.completed", "response": {"usage": {
                "input_tokens": 30, "output_tokens": 20, "total_tokens": 50}}}),
            b"data: [DONE]\n\n",
        ]
        events, requests = self.replay_raw_sse(frames)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "completed")
        self.assertEqual(complete["model_usage"]["output_tokens"], 20)
        self.assertIn("voice_turn_completed", [x["type"] for x in events])
        self.assertEqual("".join(x["text"] for x in events if
                         x["type"] == "chat_text_delta"), TEXT)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(requests[0]["body"], self.frozen.body)
        self.assertTrue(requests[0]["auth_present"])
        records = self.diagnostics()
        self.assertTrue(any(x["event"] == "model_event" and
            x["event_type"] == "response.reasoning_summary_part.added" and
            x["parse_branch"] == "notice" for x in records))
        self.assertEqual(sum(x["event"] == "model_event" and
            x.get("event_type") == "response.metadata" and
            x.get("parse_branch") == "notice" for x in records), 2)
        summary = next(x for x in records if x["event"] == "turn_summary")
        self.assertEqual(sum(x["event"] == "segment_pre_send" for x in records),
                         summary["tts_segments"])
        self.assertLessEqual(summary["tts_segments"], 2)
        self.assertLessEqual(summary["tts_characters"], 80)
        self.assertNotIn("synthetic_private_metadata", json.dumps(events))
        self.assertNotIn("synthetic_private_metadata", json.dumps(records))
        self.assertNotIn(secret, json.dumps(records))
        self.assertNotIn(secret, json.dumps(self.sent))

    def test_raw_sse_unknown_event_keeps_safe_type_and_fails(self):
        frames = [sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
                  sse({"type": "response.metadata", "metadata": {}}),
                  sse({"type": "response.future_notice", "payload": "private fixture"})]
        events, requests = self.replay_raw_sse(frames, pause_after={0: .08})
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], "model_event_unknown")
        self.assertEqual(len(requests), 1)
        records = self.diagnostics()
        self.assertTrue(any(x["event"] == "model_event" and
            x["event_type"] == "response.future_notice" and
            x["parse_branch"] == "unsupported" and
            x["event_layer"] == "local_fixture_event" for x in records))
        self.assertTrue(any(x["event"] == "model_failure" and
            x["error_code"] == "model_event_unknown" and
            x["error_category"] == "unknown_event" for x in records))
        self.assertNotIn("private fixture", json.dumps(records))

    def test_raw_sse_unsafe_event_type_is_hashed_not_logged(self):
        unsafe = "response.\nprivate_fixture_value"
        events, requests = self.replay_raw_sse([
            ("data: " + json.dumps({"type": unsafe, "payload": "private fixture"}) +
             "\n\n").encode("utf-8")])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(len(requests), 1)
        unknown = next(x for x in self.diagnostics() if
                       x["event"] == "model_event")
        self.assertEqual(unknown["event_type"], "unrecognized")
        self.assertEqual(unknown["event_type_sha256"],
                         hashlib.sha256(unsafe.encode()).hexdigest())
        self.assertNotIn("private_fixture_value", json.dumps(self.diagnostics()))

    def test_raw_sse_provider_error_stops_without_completion(self):
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse({"type": "error", "code": "server_error",
                 "message": "synthetic private error text"})])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], "provider_stream_error")
        self.assertEqual(len(requests), 1)
        records = self.diagnostics()
        self.assertTrue(any(x["event"] == "model_failure" and
            x["error_code"] == "server_error" for x in records))
        self.assertNotIn("synthetic private error text", json.dumps(records))

    def test_raw_sse_response_failed_keeps_reliable_usage(self):
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse({"type": "response.metadata", "metadata": {}}),
            sse({"type": "response.failed", "response": {
                "error": {"code": "server_error", "message": "private fixture"},
                "usage": {"input_tokens": 30, "output_tokens": 5}}})])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], "provider_reported_failure")
        self.assertEqual(complete["model_usage"]["output_tokens"], 5)
        self.assertEqual(len(requests), 1)
        self.assertTrue(any(x["event"] == "model_failure" and
            x["error_code"] == "server_error" for x in self.diagnostics()))
        self.assertNotIn("private fixture", json.dumps(self.diagnostics()))

    def test_raw_sse_incomplete_preserves_observed_usage(self):
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse({"type": "response.metadata", "metadata": {}}),
            sse({"type": "response.incomplete", "response": {
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 30, "output_tokens": 128}}})])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "incomplete")
        self.assertEqual(complete["model_usage"]["output_tokens"], 128)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("voice_turn_completed", [x["type"] for x in events])

    def test_raw_sse_eof_without_complete_is_incomplete(self):
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse({"type": "response.metadata", "metadata": {}})])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "incomplete")
        self.assertEqual(complete["finish_reason"], "model_stream_without_completion")
        self.assertEqual(len(requests), 1)

    def assert_metadata_stop(self, event, branch, *, reason="model_metadata_control_unsupported"):
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse(event),
            sse({"type": "response.completed", "response": {"usage": {
                "input_tokens": 30, "output_tokens": 20}}}),
        ])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], reason)
        self.assertEqual("".join(x["text"] for x in events if
                         x["type"] == "chat_text_delta"), TEXT[:12])
        self.assertEqual(len(requests), 1)
        records = self.diagnostics()
        self.assertTrue(any(x["event"] == "model_event" and
            x["parse_branch"] == branch for x in records))
        self.assertTrue(any(x["event"] == "model_failure" and
            x["error_code"] == reason for x in records))
        self.assertTrue(any(x["event"] == "voice_terminal" and
            x["voice_status"] == "voice_turn_stopped" for x in records))
        self.assertNotIn("private fixture", json.dumps(records))
        self.assertNotIn("voice_turn_completed", [x["type"] for x in events])

    def test_raw_sse_model_verification_stops_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.metadata", "metadata": {
            "openai_verification_recommendation": ["trusted_access_for_cyber"]}},
            "metadata_model_verification")

    def test_raw_sse_answer_moderation_stops_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": {
                "final_answer_moderation": {"moderated": True,
                    "display": {"mode": "hide", "private": "private fixture"}}}}},
            "metadata_answer_moderation")

    def reviewed_frames(self, display, *, order="before", terminal="completed"):
        item = {"type": "message", "id": "msg-synthetic-final",
                "role": "assistant", "phase": "final_answer",
                "content": [{"type": "output_text", "text": TEXT}]}
        meta = sse({"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": {"final_answer_moderation": {
                "item_id": item["id"], "moderated": True, "display": display}}}})
        complete = sse({"type": f"response.{terminal}", "response": {
            "output": [item], "usage": {"input_tokens": 30,
                "output_tokens": 20, "total_tokens": 50}}})
        frames = [sse({"type": "response.output_item.added", "item": item}),
                  sse({"type": "response.output_text.delta", "item_id": item["id"],
                       "delta": TEXT})]
        frames.extend([meta, complete] if order == "before" else [complete, meta])
        return frames

    def test_reviewed_show_waits_for_completion_and_stream_eof(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        for order in ("before", "after"):
            with self.subTest(order=order):
                if order == "after":
                    self.setUp()
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, requests = self.replay_raw_sse(self.reviewed_frames(show, order=order))
                self.assertEqual(len(requests), 1)  # local MockTransport only
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["model_status"], "completed")
                self.assertEqual(complete["display_status"], "allow")
                self.assertEqual("".join(x["text"] for x in events if
                    x["type"] == "chat_text_delta"), TEXT)
                records = self.diagnostics()
                names = [x["event"] for x in records]
                self.assertLess(names.index("display_terminal"), names.index("segment_pre_send"))
                self.assertLess(names.index("model_terminal"), names.index("text_send_attempt"))
                self.assertEqual(sum(x["event"] == "text_send_attempt" for x in records), 2)
                self.assertIn("voice_turn_completed", [x["type"] for x in events])

    def test_reviewed_raw_moderation_envelope_allows_independent_metadata(self):
        # Synthetic raw SSE at the official transport layer. Codex extracts
        # final_answer_moderation from a metadata object without requiring
        # that it be the object's only field.
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        frames = self.reviewed_frames(show)
        event = json.loads(frames[-2].decode().split("data: ", 1)[1])
        event["metadata"]["openai_chatgpt_moderation_metadata"]["presentation"] = "inline"
        frames[-2] = sse(event)
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, requests = self.replay_raw_sse(frames)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["display_status"], "allow")
        self.assertEqual(len(requests), 1)
        record = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
        self.assertEqual(record["answer_binding_status"], "matched")
        self.assertRegex(record["extra_fields_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(record["decision_presence"], "present")

    def test_reviewed_observed_non_display_objects_follow_final_decision(self):
        # Synthetic SSE exercises the same decoder, adapter and product path.
        # The objects contain private fixture values which must never be logged.
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        hide = {"mode": "hide", "version": 1,
                "replacement": {"kind": "wellbeingPromptHelpline"}}
        ancillary = {name: {"private": "never log"} for name in
                     ("prompt", "tool_call", "tool_response", "generation")}
        for marker, expected, presence in (
            ("missing", "allow", "field_missing"),
            (None, "allow", "explicit_null"),
            ({"item_id": "msg-synthetic-final", "moderated": False,
              "display": show}, "allow", "present"),
            ({"item_id": "msg-synthetic-final", "moderated": True,
              "display": hide}, "restrict", "present"),
        ):
            with self.subTest(marker=marker):
                self.setUp()
                envelope = dict(ancillary)
                if marker != "missing":
                    envelope["final_answer_moderation"] = marker
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["display_status"], expected)
                self.assertEqual("".join(x["text"] for x in events if
                    x["type"] == "chat_text_delta"), TEXT if expected == "allow" else "")
                self.assertEqual(sum(x["event"] == "text_send_attempt" for x in
                    self.diagnostics()), 2 if expected == "allow" else 0)
                record = next(x for x in self.diagnostics() if
                    x["event"] == "display_terminal")
                self.assertEqual(record["decision_presence"], presence)
                self.assertEqual(record["answer_binding_status"], "matched")
                self.assertNotIn("never log", json.dumps(self.diagnostics()))

        for field in ancillary:
            with self.subTest(invalid_field=field):
                self.setUp()
                envelope = dict(ancillary)
                envelope[field] = []
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                self.assertEqual(next(x for x in events if
                    x["type"] == "chat_message_complete")["display_status"], "restrict")
                record = next(x for x in self.diagnostics() if
                    x["event"] == "moderation_event")
                self.assertEqual(record["failure_field_path"],
                    "metadata.openai_chatgpt_moderation_metadata." + field)
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in
                    self.diagnostics()))

    def test_reviewed_raw_moderation_failures_have_safe_field_diagnostics(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        cases = [
            ({"final_answer_moderation": []},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation", "array"),
            ({"final_answer_moderation": {"item_id": 7, "moderated": True, "display": show}},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation.item_id", "integer"),
            ({"final_answer_moderation": {"item_id": "msg-synthetic-final", "moderated": "yes", "display": show}},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation.moderated", "string"),
            ({"final_answer_moderation": {"item_id": "msg-synthetic-final", "moderated": True}},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation.display", "null"),
            ({"final_answer_moderation": {"item_id": "msg-synthetic-final", "moderated": True,
                "display": {"mode": "show", "version": "1", "trustedContacts": "skip"}}},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation.display.version", "string"),
            ({"final_answer_moderation": {"item_id": "msg-synthetic-final", "moderated": True,
                "display": {"mode": [], "version": 1}}},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation.display.mode", "array"),
            ({"final_answer_moderation": {"item_id": "msg-synthetic-final", "moderated": True,
                "display": {"mode": "show", "version": 1, "trustedContacts": []}}},
             "metadata.openai_chatgpt_moderation_metadata.final_answer_moderation.display.trustedContacts", "array"),
        ]
        for envelope, field, actual in cases:
            with self.subTest(field=field, actual=actual):
                self.setUp()
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["display_status"], "restrict")
                self.assertFalse(any(x["type"] == "chat_text_delta" for x in events))
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))
                record = next(x for x in self.diagnostics() if x["event"] == "moderation_event")
                self.assertEqual(record["failure_field_path"], field)
                self.assertEqual(record["actual_type"], actual)
                self.assertEqual(record["answer_binding_status"],
                    "not_checked" if ".display" in field else
                    "not_checked_format_invalid")
                self.assertIn("final_answer_moderation", record["required_fields_present"])

    def test_reviewed_optional_decision_defaults_only_after_bound_completion(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        for presence, envelope in (("metadata_absent", None),
                                   ("field_missing", {"presentation": "inline"}),
                                   ("explicit_null", {"final_answer_moderation": None,
                                                      "presentation": "inline"})):
            with self.subTest(presence=presence):
                self.setUp()
                frames = self.reviewed_frames(show)
                if envelope is None:
                    frames.pop(-2)
                else:
                    event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                    event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                    frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual((complete["model_status"], complete["display_status"],
                                  complete["generation_status"]),
                                 ("completed", "allow", "completed"))
                self.assertEqual("".join(x["text"] for x in events if
                    x["type"] == "chat_text_delta"), TEXT)
                record = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
                self.assertEqual(record["decision_presence"], presence)
                self.assertEqual(record["display_decision"], "none")
                self.assertEqual(record["display_reason"],
                                 "protocol_default_display_no_explicit_decision")
                self.assertEqual(record["answer_binding_status"], "matched")
                self.assertEqual(sum(x["event"] == "text_send_attempt" for x in
                    self.diagnostics()), 2)
                if presence != "metadata_absent":
                    observed = next(x for x in self.diagnostics() if
                                    x["event"] == "moderation_event")
                    self.assertEqual(observed["decision_presence"], presence)
                    self.assertEqual(observed["display_action"], "pending")

    def test_reviewed_optional_decision_still_requires_answer_binding(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        for presence, envelope in (("field_missing", {}),
                                   ("explicit_null", {"final_answer_moderation": None})):
            with self.subTest(presence=presence):
                self.setUp()
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                terminal = json.loads(frames[-1].decode().split("data: ", 1)[1])
                terminal["response"]["output"][0]["content"][0]["text"] = "changed"
                frames[-1] = sse(terminal)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["display_status"], "restrict")
                record = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
                self.assertEqual(record["decision_presence"], presence)
                self.assertEqual(record["answer_binding_status"], "mismatch")
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_reviewed_duplicate_or_conflicting_decisions_restrict(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        hide = {"mode": "hide", "version": 1,
                "replacement": {"kind": "wellbeingPromptHelpline"}}
        for second in (show, hide, None):
            with self.subTest(second=second):
                self.setUp()
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = (
                    {"presentation": "inline"} if second is None else
                    {"final_answer_moderation": {"item_id": "msg-synthetic-final",
                      "moderated": True, "display": second}})
                frames.insert(-1, sse(event))
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["display_status"], "restrict")
                record = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
                self.assertEqual(record["display_reason"], "moderation_duplicate_or_conflicting")
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_reviewed_generation_ancillary_keeps_optional_and_explicit_limits(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        hide = {"mode": "hide", "version": 1,
                "replacement": {"kind": "wellbeingPromptHelpline"}}
        for display, envelope, expected in (
            (show, {"presentation": "inline", "generation": {"private": "never log"}},
             ("allow", "field_missing", "none")),
            (show, {"final_answer_moderation": None,
                    "generation": {"private": "never log"}},
             ("allow", "explicit_null", "none")),
            (show, {"final_answer_moderation": {"item_id": "msg-synthetic-final",
                    "moderated": False, "display": show},
                    "generation": {"private": "never log"}},
             ("allow", "present", "show")),
            (hide, {"final_answer_moderation": {"item_id": "msg-synthetic-final",
                    "moderated": True, "display": hide},
                    "generation": {"private": "never log"}},
             ("restrict", "present", "hide")),
        ):
            with self.subTest(expected=expected):
                self.setUp()
                frames = self.reviewed_frames(display)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["display_status"], expected[0])
                record = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
                self.assertEqual((record["decision_presence"], record["display_decision"]),
                                 expected[1:])
                attempts = [x for x in self.diagnostics() if x["event"] == "text_send_attempt"]
                self.assertEqual(len(attempts), 2 if expected[0] == "allow" else 0)
                self.assertNotIn("never log", json.dumps(self.diagnostics()))

    def test_reviewed_unrecognized_field_and_bad_ancillary_still_restrict(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        for envelope, reason in (
            ({"presentation": "other"}, "moderation_presentation_unsupported"),
            ({"presentation": "inline", "new_control": {"private": "never log"}},
             "moderation_field_unrecognized"),
            ({"generation": None}, "moderation_ancillary_format_invalid"),
            ({"generation": {"private": "never log"},
              "safety_block": {"status": "loading"}},
             "moderation_field_unrecognized"),
        ):
            with self.subTest(reason=reason):
                self.setUp()
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                self.assertEqual(next(x for x in events if
                    x["type"] == "chat_message_complete")["display_status"], "restrict")
                self.assertEqual(next(x for x in self.diagnostics() if
                    x["event"] == "moderation_event")["display_reason"],
                    reason)
                self.assertNotIn("never log", json.dumps(self.diagnostics()))
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_unknown_keys_are_individually_bounded_and_decision_is_inspected(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        hide = {"mode": "hide", "version": 1,
                "replacement": {"kind": "wellbeingPromptHelpline"}}
        for marker, presence, parse_status, known_reason, decision in (
            (None, "field_missing", "absent", "optional_decision_absent", "none"),
            ({"item_id": "msg-synthetic-final", "moderated": True,
              "display": show}, "present", "parsed", "plain_show", "show"),
            ({"item_id": "msg-synthetic-final", "moderated": True,
              "display": hide}, "present", "parsed", "hide_restricted", "hide"),
            ({"item_id": "msg-synthetic-final", "moderated": True,
              "display": {"mode": "show", "version": "bad"}},
             "present", "invalid_or_unsupported", "display_format_invalid", "unknown"),
        ):
            with self.subTest(presence=presence, decision=decision):
                self.setUp()
                envelope = {"private_a": {"secret": "never log"},
                            "safety_block": ["never log"]}
                if marker is not None:
                    envelope["final_answer_moderation"] = marker
                frames = self.reviewed_frames(show)
                event = json.loads(frames[-2].decode().split("data: ", 1)[1])
                event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
                frames[-2] = sse(event)
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(frames)
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["display_status"], "restrict")
                records = self.diagnostics()
                record = next(x for x in records if x["event"] == "moderation_event")
                self.assertEqual(record["unknown_key_count"], 2)
                self.assertEqual(record["layer_key_count"], len(envelope))
                self.assertFalse(record["unknown_keys_truncated"])
                details = record["unknown_key_details"]
                self.assertEqual(len(details), 2)
                self.assertEqual({x["layer"] for x in details}, {"moderation_envelope"})
                self.assertEqual({x["value_type"] for x in details}, {"object", "array"})
                self.assertEqual({x["key_sha256"] for x in details}, {
                    hashlib.sha256(name.encode()).hexdigest()
                    for name in ("private_a", "safety_block")})
                self.assertEqual([x.get("public_name") for x in details if
                    x.get("public_name")], ["safety_block"])
                self.assertEqual(record["decision_presence"], presence)
                self.assertEqual(record["known_decision_parse_status"], parse_status)
                self.assertEqual(record["known_decision_reason"], known_reason)
                self.assertEqual(record["display_decision"], decision)
                self.assertEqual(record["answer_binding_status"],
                                 "not_checked_restricted")
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in records))
                self.assertNotIn("private_a", json.dumps(records))
                self.assertNotIn("never log", json.dumps(records))

        self.setUp()
        envelope = {f"private_{i}": i for i in range(25)}
        frames = self.reviewed_frames(show)
        event = json.loads(frames[-2].decode().split("data: ", 1)[1])
        event["metadata"]["openai_chatgpt_moderation_metadata"] = envelope
        frames[-2] = sse(event)
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, _ = self.replay_raw_sse(frames)
        record = next(x for x in self.diagnostics() if x["event"] == "moderation_event")
        self.assertEqual(record["unknown_key_count"], 25)
        self.assertEqual(len(record["unknown_key_details"]), 24)
        self.assertTrue(record["unknown_keys_truncated"])
        self.assertEqual(next(x for x in events if
            x["type"] == "chat_message_complete")["display_status"], "restrict")
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_reviewed_raw_mixed_outer_control_still_restricts(self):
        frames = self.reviewed_frames({"mode": "show", "version": 1,
                                       "trustedContacts": "skip"})
        event = json.loads(frames[-2].decode().split("data: ", 1)[1])
        event["metadata"]["unrecognized_control"] = {"private": "do not log"}
        frames[-2] = sse(event)
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, _ = self.replay_raw_sse(frames)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["display_status"], "restrict")
        record = next(x for x in self.diagnostics() if x["event"] == "moderation_event")
        self.assertEqual(record["display_reason"], "mixed_metadata_unsupported")
        self.assertRegex(record["extra_fields_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(record["unknown_key_count"], 1)
        self.assertEqual(record["unknown_key_details"], [{
            "layer": "response_metadata",
            "key_sha256": hashlib.sha256(b"unrecognized_control").hexdigest(),
            "value_type": "object"}])
        self.assertEqual(record["decision_presence"], "present")
        self.assertEqual(record["known_decision_parse_status"], "parsed")
        self.assertEqual(record["known_decision_reason"], "plain_show")
        self.assertNotIn("do not log", json.dumps(self.diagnostics()))
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_unknown_display_key_is_located_without_releasing_text(self):
        frames = self.reviewed_frames({"mode": "show", "version": 1,
                                       "trustedContacts": "skip"})
        event = json.loads(frames[-2].decode().split("data: ", 1)[1])
        display = event["metadata"]["openai_chatgpt_moderation_metadata"][
            "final_answer_moderation"]["display"]
        display["private_requirement"] = {"value": "never log"}
        frames[-2] = sse(event)
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, _ = self.replay_raw_sse(frames)
        record = next(x for x in self.diagnostics() if x["event"] == "moderation_event")
        self.assertEqual(record["display_reason"], "display_format_invalid")
        self.assertEqual(record["unknown_key_count"], 1)
        self.assertEqual(record["layer_key_count"], 4)
        self.assertEqual(record["unknown_key_details"], [{
            "layer": "display_decision",
            "key_sha256": hashlib.sha256(b"private_requirement").hexdigest(),
            "value_type": "object"}])
        self.assertEqual(next(x for x in events if
            x["type"] == "chat_message_complete")["display_status"], "restrict")
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))
        self.assertNotIn("private_requirement", json.dumps(self.diagnostics()))
        self.assertNotIn("never log", json.dumps(self.diagnostics()))

    def test_reviewed_restrictions_never_publish_text_or_tts(self):
        cases = [
            ("hide", {"mode": "hide", "version": 1,
                      "replacement": {"kind": "wellbeingPromptHelpline"}}),
            ("block", {"mode": "block", "version": 1,
                       "reason": {"kind": "cyber"}}),
            ("trusted_contacts", {"mode": "show", "version": 1,
                                  "trustedContacts": "check"}),
            ("annotation", {"mode": "show", "version": 1,
                            "trustedContacts": "skip", "annotation": {
                                "kind": "wellbeingHelpline", "subject": "answer"}}),
            ("version", {"mode": "show", "version": 2,
                         "trustedContacts": "skip"}),
            ("invalid", {"mode": "show", "version": 1}),
        ]
        for name, display in cases:
            with self.subTest(case=name):
                self.setUp()
                with self.main.connect() as db:
                    before = db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant' "
                        "AND content=?", (TEXT,)).fetchone()[0]
                with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
                    events, _ = self.replay_raw_sse(self.reviewed_frames(display))
                complete = next(x for x in events if x["type"] == "chat_message_complete")
                self.assertEqual(complete["model_status"], "completed")
                self.assertEqual(complete["display_status"], "restrict")
                self.assertEqual(complete["generation_status"], "restricted")
                self.assertIsNone(complete["message_id"])
                self.assertFalse(any(x["type"] == "chat_text_delta" for x in events))
                self.assertFalse(any(isinstance(x, dict) and "inputs" in x for x in self.sent))
                self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))
                with self.main.connect() as db:
                    after = db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant' "
                        "AND content=?", (TEXT,)).fetchone()[0]
                self.assertEqual(after, before)

    def test_reviewed_completed_body_mismatch_restricts(self):
        frames = self.reviewed_frames({"mode": "show", "version": 1,
                                       "trustedContacts": "skip"})
        terminal = json.loads(frames[-1].decode("utf-8").split("data: ", 1)[1])
        terminal["response"]["output"][0]["content"][0]["text"] = "changed"
        frames[-1] = sse(terminal)
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, _ = self.replay_raw_sse(frames)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["finish_reason"], "answer_binding_invalid")
        self.assertFalse(any(x["type"] == "chat_text_delta" for x in events))
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))
        terminal = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
        self.assertEqual(terminal["answer_binding_status"], "mismatch")

    def test_reviewed_explicit_show_with_wrong_item_id_restricts(self):
        frames = self.reviewed_frames({"mode": "show", "version": 1,
                                       "trustedContacts": "skip"})
        event = json.loads(frames[-2].decode().split("data: ", 1)[1])
        event["metadata"]["openai_chatgpt_moderation_metadata"][
            "final_answer_moderation"]["item_id"] = "msg-other"
        frames[-2] = sse(event)
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, _ = self.replay_raw_sse(frames)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["display_status"], "restrict")
        record = next(x for x in self.diagnostics() if x["event"] == "display_terminal")
        self.assertEqual(record["answer_binding_status"], "mismatch")
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_reviewed_failed_terminal_restricts_even_with_show(self):
        show = {"mode": "show", "version": 1, "trustedContacts": "skip"}
        with patch.dict(os.environ, {"YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1"}):
            events, _ = self.replay_raw_sse(self.reviewed_frames(show, terminal="failed"))
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertNotEqual(complete["display_status"], "allow")
        self.assertEqual(complete["model_usage"]["output_tokens"], 20)
        self.assertFalse(any(x["type"] == "chat_text_delta" for x in events))
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in self.diagnostics()))

    def test_raw_sse_guardian_assessment_stops_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.metadata",
            "response_id": "resp-synthetic", "metadata": {"guardian": {
                "item_id": "tool-synthetic", "requires_review": True,
                "private": "private fixture"}}}, "metadata_guardian")

    def test_raw_sse_safety_buffering_stops_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.metadata", "metadata": {
            "type": "safety_buffering", "use_cases": ["cyber"],
            "reasons": ["user_risk"], "retry_model": "different-model"}},
            "metadata_safety_buffering")

    def test_raw_sse_top_level_safety_buffering_stops_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.output_text.delta",
            "delta": TEXT[12:], "safety_buffering": {
                "use_cases": ["cyber"], "reasons": ["user_risk"]}},
            "metadata_safety_buffering")

    def test_raw_sse_metadata_headers_stop_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.metadata", "headers": {
            "openai-model": "different-model"}}, "metadata_headers")

    def test_raw_sse_unrecognized_metadata_stops_voice_candidate(self):
        self.assert_metadata_stop({"type": "response.metadata", "metadata": {
            "opaque": "private fixture", "usage": {"output_tokens": 999}}},
            "metadata_unrecognized", reason="model_metadata_unrecognized")

    def test_raw_sse_terminal_metadata_stop_keeps_reliable_usage(self):
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:12]}),
            sse({"type": "response.completed", "response": {"usage": {
                "input_tokens": 30, "output_tokens": 20}},
                "safety_buffering": {"use_cases": ["cyber"],
                    "reasons": ["user_risk"]}}),
        ])
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], "model_metadata_control_unsupported")
        self.assertEqual(complete["model_usage"]["output_tokens"], 20)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("voice_turn_completed", [x["type"] for x in events])

    def test_raw_sse_label_mismatch_fails_at_wire_decode(self):
        frames = [sse({"type": "response.output_text.delta", "delta": TEXT[:12]},
                      label="response.completed")]
        events, requests = self.replay_raw_sse(frames)
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertEqual(complete["finish_reason"], "model_sse_type_mismatch")
        self.assertEqual(len(requests), 1)
        self.assertTrue(any(x["event"] == "model_failure" and
            x["event_layer"] == "provider_sse_frame" and
            x["parse_branch"] == "sse_decode" for x in self.diagnostics()))

    def test_model_parse_failure_cancels_pending_ttd_connect(self):
        slow = HangingConnect()
        self.gate.ttd_connector_factory = lambda _voice: slow
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:13]}),
            sse({"type": "response.future_notice"})], pause_after={0: .12})
        self.assertEqual(len(requests), 1)
        self.assertTrue(slow.enter_started.is_set())
        self.assertTrue(slow.enter_cancelled.is_set())
        self.assertFalse(slow.exit_called.is_set())  # context was never entered
        complete = next(x for x in events if x["type"] == "chat_message_complete")
        self.assertEqual(complete["generation_status"], "failed")
        self.assertNotIn("audio", [x["type"] for x in events])
        records = self.diagnostics()
        self.assertEqual(sum(x["event"] == "connection_attempt" for x in records), 1)
        self.assertFalse(any(x["event"] == "connection_ready" for x in records))
        self.assertFalse(any(x["event"] == "text_send_attempt" for x in records))
        summary = next(x for x in records if x["event"] == "turn_summary")
        self.assertEqual(summary["tts_connections"], 1)
        self.assertEqual(summary["total_audio_bytes"], 0)

    def test_model_parse_failure_closes_established_ttd_without_audio(self):
        supplier = WaitingSupplier()
        self.gate.ttd_connector_factory = lambda _voice: supplier
        events, requests = self.replay_raw_sse([
            sse({"type": "response.output_text.delta", "delta": TEXT[:13]}),
            sse({"type": "response.future_notice"})], pause_after={0: .12})
        self.assertEqual(len(requests), 1)
        self.assertTrue(supplier.entered.is_set())
        self.assertTrue(supplier.recv_cancelled.is_set())
        self.assertTrue(supplier.closed.is_set())
        self.assertEqual(sum("inputs" in x for x in supplier.sent), 1)
        self.assertNotIn("audio", [x["type"] for x in events])
        self.assertEqual(next(x for x in events if x["type"] == "turn_summary")
                         ["tts_audio_bytes"], 0)
        self.assertEqual(sum(x["event"] == "text_send_attempt" for x in
                             self.diagnostics()), 1)


if __name__ == "__main__":
    unittest.main()
