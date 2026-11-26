"""Normal demo-chat SSE identity and saved-message receipt in temporary SQLite."""

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from tests.voice_main_test_support import reload_main_with_class_cleanup


class ChatLifecycleReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="yosaki-chat-life-")
        cls.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": cls.temp.name,
            "YOSAKI_VOICE_ENABLED": "0",
            "YOSAKI_V4_DYNAMIC_CHAT_ISOLATED": "0",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        })
        cls.env.start()
        cls.main = reload_main_with_class_cleanup(cls)
        cls.client = TestClient(cls.main.app)
        cls.client.__enter__()
        result = cls.client.post("/api/auth/setup", json={
            "username": "receipt_temp_owner", "password": "synthetic-password-12345"})
        assert result.status_code == 201, result.text

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.env.stop()
        cls.temp.cleanup()

    def test_generation_starts_before_real_message_insert_and_finishes_with_hash(self):
        with self.main.connect() as db:
            before = db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
        async def stream():
            response = await self.main.chat_stream(self.main.ChatInput(
                character_id=1, conversation_id=None,
                message="synthetic hello", model="YOSAKI · demo"))
            iterator = response.body_iterator.__aiter__()
            first = await iterator.__anext__()
            with self.main.connect() as db:
                at_start = db.execute(
                    "SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
            rest = [part async for part in iterator]
            return first, rest, at_start
        first, rest, at_start = asyncio.run(stream())
        self.assertEqual(at_start, before)
        frames = "".join([first, *rest]).split("\n\n")
        events = []
        for value in frames:
            if "event: " not in value:
                continue
            lines = value.splitlines()
            events.append((lines[0][7:], json.loads(lines[1][6:])))
        start = next(data for name, data in events if name == "message_start")
        complete = next(data for name, data in events if name == "message_complete")
        self.assertEqual(start["generation_id"], complete["generation_id"])
        self.assertEqual(len(start["generation_id"]), 32)
        self.assertEqual(start["owner_id"], 1)
        self.assertEqual(start["character_id"], 1)
        self.assertEqual(start["conversation_id"], complete["conversation_id"])
        self.assertEqual(complete["generation_status"], "completed")
        public = "".join(data["text"] for name, data in events if name == "text_delta")
        with self.main.connect() as db:
            saved = db.execute("SELECT role,content FROM messages WHERE id=?",
                               (complete["message_id"],)).fetchone()
            after = db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
            receipt = self.main.assistant_completion_receipt(db, complete["message_id"])
        self.assertEqual(after, before + 1)
        self.assertEqual(saved["role"], "assistant")
        self.assertEqual(saved["content"], public)
        self.assertEqual(complete["public_text_sha256"], hashlib.sha256(public.encode()).hexdigest())
        self.assertEqual(complete["message_version_sha256"], receipt["message_version_sha256"])

    def test_continuous_context_excludes_stopped_assistant_only(self):
        history = [
            {"role": "user", "content": "first", "generation_status": None},
            {"role": "assistant", "content": "partial", "generation_status": "stopped"},
            {"role": "user", "content": "second", "generation_status": None},
            {"role": "assistant", "content": "finished", "generation_status": "completed"},
        ]
        self.assertEqual([row["content"] for row in self.main.voice_call_history(history)],
                         ["first", "second", "finished"])

    def test_button_acceptance_validates_serialized_request_before_transport(self):
        from backend.app import chatgpt_plan
        from backend.app.voice.continuous_call import CallManager

        gate = CallManager(self.main.connect)
        with self.main.connect() as db:
            profile_id = db.execute("""INSERT INTO model_profiles
                (name,provider_name,base_url,model_name,protocol,temperature,top_p,
                 max_tokens,context_limit,supports_streaming,enabled,created_at,updated_at)
                VALUES ('Synthetic Plan','test','https://invalid.local','gpt-6.1-sol',
                        'chatgpt_plan',0.8,1,2048,32768,1,1,?,?)""",
                (self.main.now(), self.main.now())).lastrowid

        cases = [
            ("valid", None), ("missing_wire_field", "缺少 max_output_tokens"),
            ("wrong_wire_value", "必须为 2048"), ("wrong_wire_type", "必须为 2048"),
            ("lower_profile_limit", "必须为 2048"),
            ("unsupported_endpoint", "所选模型接口无法确认支持输出 token 上限"),
            ("unsupported_protocol", "所选模型接口无法确认支持输出 token 上限"),
            ("daily_call", None),
        ]
        for case, rejection in cases:
            with self.subTest(case=case):
                seen = []

                def provider(request):
                    seen.append(json.loads(request.content))
                    # A synthetic terminal status is sufficient to identify
                    # the request that actually reached the model transport.
                    return httpx.Response(429)

                class ModelClient(httpx.AsyncClient):
                    def __init__(self, **kwargs):
                        super().__init__(transport=httpx.MockTransport(provider), **kwargs)

                    def build_request(self, *args, **kwargs):
                        body = kwargs.get("json")
                        if case == "missing_wire_field":
                            body.pop("max_output_tokens")
                        elif case == "wrong_wire_value":
                            body["max_output_tokens"] = 4096
                        elif case == "wrong_wire_type":
                            body["max_output_tokens"] = "2048"
                        return super().build_request(*args, **kwargs)

                with self.main.connect() as db:
                    protocol = ("openai_responses" if case == "unsupported_protocol"
                                else "chatgpt_plan")
                    db.execute("UPDATE model_profiles SET protocol=?,max_tokens=? WHERE id=?",
                               (protocol, 1024 if case == "lower_profile_limit" else 2048, profile_id))
                    conversation_id = db.execute("""INSERT INTO conversations
                        (character_id,title,model_profile_id,created_at,updated_at)
                        VALUES(1,'Synthetic budget',?,?,?)""",
                        (profile_id, self.main.now(), self.main.now())).lastrowid
                endpoint = ("https://unverified.invalid/v1" if case == "unsupported_endpoint"
                            else "https://api.openai.com/v1")
                with (
                    patch.object(self.main.app.state, "voice_continuous", gate, create=True),
                    patch.object(gate, "acceptance", return_value=case != "daily_call"),
                    patch.object(gate, "headset_conversation", return_value=None),
                    patch.object(gate, "claim_model", return_value=1),
                    patch.object(chatgpt_plan, "API_BASE", endpoint),
                    patch.object(chatgpt_plan, "access_token", AsyncMock(return_value="synthetic")),
                    patch.object(self.main.httpx, "AsyncClient", ModelClient),
                ):
                    reply = self.client.post("/api/chat/stream", json={
                        "character_id": 1, "conversation_id": conversation_id,
                        "model_profile_id": profile_id, "message": "Synthetic transcript",
                        "voice_call_id": "a" * 32, "voice_call_turn_id": "b" * 32,
                    })
                if rejection:
                    self.assertEqual(seen, [])
                    self.assertIn(rejection, reply.text)
                    self.assertNotIn("message_complete", reply.text)
                else:
                    self.assertEqual(len(seen), 1)
                    self.assertIn("HTTP 429", reply.text)
                    if case == "daily_call":
                        self.assertNotIn("max_output_tokens", seen[0])
                    else:
                        self.assertEqual(seen[0]["max_output_tokens"], 2048)


if __name__ == "__main__":
    unittest.main()
