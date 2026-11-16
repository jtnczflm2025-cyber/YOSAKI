"""Normal demo-chat SSE identity and saved-message receipt in temporary SQLite."""

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
