"""Normal chat's isolated reviewed Plan path, with synthetic raw SSE only."""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from tests.voice_main_test_support import reload_main_with_class_cleanup


REAL_ASYNC_CLIENT = httpx.AsyncClient
TEXT = "今夜はゆっくりでいいよ。"
ITEM = {"type": "message", "id": "msg-local-review", "role": "assistant",
        "phase": "final_answer", "content": [{"type": "output_text", "text": TEXT}]}


def frame(event):
    return ("event: " + event["type"] + "\n" +
            "data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()


def wire(*, decision=None, extra=None, complete=True):
    events = [
        {"type": "response.output_item.added", "item": ITEM},
        {"type": "response.output_text.delta", "item_id": ITEM["id"],
         "delta": TEXT},
    ]
    if decision is not None or extra is not None:
        envelope = dict(extra or {})
        if decision is not None:
            envelope["final_answer_moderation"] = {
                "item_id": ITEM["id"], "moderated": True,
                "display": {"version": 1, "mode": decision,
                            "trustedContacts": "skip"} if decision == "show" else
                           {"version": 1, "mode": decision, "replacement": ""}}
        events.append({"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": envelope}})
    if complete:
        events.append({"type": "response.completed", "response": {
            "output": [ITEM], "usage": {"input_tokens": 4,
                                         "output_tokens": 8, "total_tokens": 12}}})
    return b"".join(frame(event) for event in events)


def events_from(response):
    result = []
    for block in response.text.split("\n\n"):
        if not block.startswith("event: "):
            continue
        lines = block.splitlines()
        result.append((lines[0][7:], json.loads(lines[1][6:])))
    return result


class AutoReadDisplayGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="yosaki-auto-review-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": cls.temp.name,
            "YOSAKI_VOICE_ENABLED": "0",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
            "YOSAKI_CHATGPT_PLAN_ENABLED": "1",
        })
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.main = reload_main_with_class_cleanup(cls)
        cls.client = TestClient(cls.main.app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        response = cls.client.post("/api/auth/setup", json={
            "username": "synthetic_review_owner",
            "password": "synthetic-password-2026"})
        assert response.status_code == 201, response.text
        with cls.main.connect() as db:
            db.execute("INSERT INTO model_profiles(name,provider_name,base_url,model_name,"
                "protocol,temperature,top_p,max_tokens,context_limit,supports_streaming,"
                "enabled,created_at,updated_at) VALUES "
                "('fake plan','OpenAI','https://api.openai.com/v1','gpt-fake',"
                "'chatgpt_plan',0.8,1,128,10000,1,1,'now','now')")
            cls.profile_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def post(self, body, *, auto_read=False):
        calls = []

        replies=body if isinstance(body,list) else [body]
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=replies[len(calls)-1],
                                  headers={"content-type": "text/event-stream"})

        def mock_client(**kwargs):
            return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

        with patch.object(self.main.httpx, "AsyncClient", side_effect=mock_client), \
             patch("backend.app.chatgpt_plan.access_token", new_callable=AsyncMock,
                   return_value="fake-token-never-sent"):
            response = self.client.post("/api/chat/stream", json={
                "character_id": 1, "conversation_id": None,
                "message": "local only", "model": "fake plan",
                "model_profile_id": self.profile_id,
                "voice_auto_read_requested": auto_read})
        self.assertEqual(len(calls), len(replies))
        self.last_model_request = json.loads(calls[0].content)
        self.assertEqual(response.status_code, 200)
        return events_from(response)

    def test_allow_only_after_complete_and_binding(self):
        for body in (wire(), wire(decision="show")):
            with self.subTest(body=body[:100]):
                events = self.post(body)
                complete = [data for name, data in events if name == "message_complete"]
                self.assertEqual(len(complete), 1)
                self.assertTrue(complete[0]["voice_auto_read_eligible"])
                self.assertEqual("".join(data["text"] for name, data in events
                                         if name == "text_delta"), TEXT)
                with self.main.connect() as db:
                    self.assertEqual(db.execute("SELECT content FROM messages WHERE id=?",
                        (complete[0]["message_id"],)).fetchone()[0], TEXT)

    def test_restricted_never_publishes_or_saves_assistant(self):
        for body in (wire(decision="hide"), wire(extra={"unknown_control": {}}),
                     wire(complete=False)):
            with self.subTest(body=body[:100]):
                with self.main.connect() as db:
                    before = db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
                events = self.post(body)
                self.assertTrue(any(name == "error" for name, _ in events))
                self.assertFalse(any(name in {"text_delta", "message_complete"}
                                     for name, _ in events))
                with self.main.connect() as db:
                    after = db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
                self.assertEqual(after, before)

    def test_auto_read_does_not_shorten_normal_model_output(self):
        self.post(wire(), auto_read=True)
        self.assertNotIn("max_output_tokens", self.last_model_request)
        self.post(wire(), auto_read=False)
        self.assertNotIn("max_output_tokens", self.last_model_request)

    def test_textless_search_requires_matching_terminal_then_answers_without_click(self):
        item = {"type":"function_call","call_id":"test-search","name":"yosaki_chronicle_search",
                "arguments":'{"query":"synthetic"}'}
        added = frame({"type":"response.output_item.added","output_index":0,"item":item})
        with patch.dict(os.environ, {"YOSAKI_CHRONICLE_CONTEXT":"1"}):
            for terminal, expected in ((dict(item,arguments="{}"), "error"), (item, "message_complete")):
                body=added + frame({"type":"response.completed","response":{"output":[terminal]}})
                events = self.post(body if expected=="error" else [body,wire()])
                names = [name for name, _ in events]
                self.assertIn(expected, names)
                self.assertNotIn("tool_waiting_confirmation",names)
                if expected=="error":self.assertNotIn("text_delta",names)
                else:self.assertIn("memory_lookup",names)


if __name__ == "__main__":
    unittest.main()
