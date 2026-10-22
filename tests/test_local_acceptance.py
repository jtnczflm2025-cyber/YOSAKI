"""Local acceptance checks. Never import the application against the real data directory."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

_import_data = tempfile.TemporaryDirectory(prefix="yosaki-test-import-")
os.environ["YOSAKI_DATA_DIR"] = _import_data.name
from backend.app import main as app_module  # noqa: E402


class MockService(BaseHTTPRequestHandler):
    calls = []
    arguments = {"value": "ok"}

    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/mcp":
            method = payload["method"]
            self.calls.append(method)
            result = {
                "server/discover": {"supportedVersions": ["2026-07-28"], "serverInfo": {"name": "local-mock"}},
                "tools/list": {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}]},
                "tools/call": {"content": [{"type": "text", "text": "echoed"}]},
            }[method]
            body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}).encode()
            content_type = "application/json"
        elif not payload.get("stream"):
            body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
            content_type = "application/json"
        else:
            has_tool_result = any(message.get("role") == "tool" for message in payload["messages"])
            if has_tool_result:
                delta = {"content": "done"}
            else:
                tool_name = payload["tools"][0]["function"]["name"]
                delta = {"tool_calls": [{"index": 0, "id": "mock-call", "type": "function", "function": {"name": tool_name, "arguments": json.dumps(self.arguments)}}]}
            body = ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]}) + "\n\ndata: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-acceptance-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        self.assertEqual(app_module.DB.parent, root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockService)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        MockService.calls = []
        MockService.arguments = {"value": "ok"}
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    async def asyncSetUp(self):
        import asyncio
        asyncio.get_running_loop().slow_callback_duration = 2.0
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def setup_owner(self):
        response = await self.client.post("/api/auth/setup", json={"username": "owner", "password": "temporary-password-123"})
        self.assertEqual(response.status_code, 201, response.text)

    async def test_health_auth_and_demo_persistence(self):
        self.assertEqual((await self.client.get("/health")).json()["status"], "ok")
        self.assertTrue((await self.client.get("/api/auth/status")).json()["setup_required"])
        self.assertEqual((await self.client.get("/api/characters")).status_code, 401)
        await self.setup_owner()
        self.assertEqual((await self.client.post("/api/auth/setup", json={"username": "again", "password": "temporary-password-123"})).status_code, 409)
        characters = (await self.client.get("/api/characters")).json()
        self.assertEqual(len(characters), 1)
        reply = await self.client.post("/api/chat/stream", json={"character_id": characters[0]["id"], "message": "hello"})
        self.assertIn("message_complete", reply.text)
        conversations = (await self.client.get("/api/conversations")).json()
        messages = (await self.client.get(f"/api/conversations/{conversations[0]['id']}/messages")).json()
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        await self.client.post("/api/auth/logout")
        self.assertEqual((await self.client.get("/api/characters")).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username": "owner", "password": "wrong-password"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username": "owner", "password": "temporary-password-123"})).status_code, 200)
        self.assertEqual(len((await self.client.get("/api/conversations")).json()), 1)

    async def test_backup_rejections_restore_and_secret_redaction(self):
        await self.setup_owner()
        original = (await self.client.get("/api/characters")).json()[0]
        model = await self.client.post("/api/models", json={"name": "mock", "base_url": self.url, "model_name": "mock", "api_key": "model-secret-test"})
        self.assertEqual(model.status_code, 201, model.text)
        self.assertNotIn("model-secret-test", model.text)
        mcp = await self.client.post("/api/mcp/servers", json={"name": "mock", "endpoint": self.url + "/mcp", "auth_type": "bearer", "auth_secret": "mcp-secret-test"})
        self.assertEqual(mcp.status_code, 201, mcp.text)
        self.assertNotIn("mcp-secret-test", mcp.text)
        backup = await self.client.post("/api/system/backup/export", data={"passphrase": "backup-password-123"})
        self.assertEqual(backup.status_code, 200, backup.text)
        files = {"backup": ("test.ybackup", backup.content, "application/octet-stream")}
        self.assertEqual((await self.client.post("/api/system/backup/import", data={"passphrase": "backup-password-123"}, files=files)).status_code, 428)
        self.assertEqual((await self.client.post("/api/system/backup/import", data={"passphrase": "wrong-password-123", "confirm_replace": "true"}, files=files)).status_code, 422)
        await self.client.patch(f"/api/characters/{original['id']}", json={"name": "changed"})
        restored = await self.client.post("/api/system/backup/import", data={"passphrase": "backup-password-123", "confirm_replace": "true"}, files=files)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.get("/api/characters")).status_code, 401)
        await self.client.post("/api/auth/login", json={"username": "owner", "password": "temporary-password-123"})
        self.assertEqual((await self.client.get("/api/characters")).json()[0]["name"], original["name"])
        self.assertFalse((await self.client.get("/api/models")).json()[0]["api_key_configured"])
        self.assertFalse((await self.client.get("/api/mcp/servers")).json()[0]["auth_configured"])

    async def test_mock_model_mcp_tool_decisions(self):
        await self.setup_owner()
        character_id = (await self.client.get("/api/characters")).json()[0]["id"]
        model = (await self.client.post("/api/models", json={"name": "mock", "base_url": self.url, "model_name": "mock", "api_key": "model-secret-test"})).json()
        self.assertEqual((await self.client.post(f"/api/models/{model['id']}/test")).json()["reply_preview"], "OK")
        mcp = (await self.client.post("/api/mcp/servers", json={"name": "mock", "endpoint": self.url + "/mcp"})).json()
        self.assertEqual((await self.client.post(f"/api/mcp/servers/{mcp['id']}/connect")).status_code, 200)
        streamed = httpx.Response(200, headers={"content-type": "text/event-stream", "mcp-session-id": "local-session"}, text='event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n\n')
        self.assertEqual(app_module.parse_mcp_response(streamed)["session_id"], "local-session")
        self.assertEqual((await self.client.post(f"/api/mcp/servers/{mcp['id']}/refresh-tools")).json()["tools_count"], 1)
        tool = (await self.client.get(f"/api/mcp/servers/{mcp['id']}/tools")).json()[0]
        self.assertTrue((await self.client.patch(f"/api/mcp/tools/{tool['id']}", json={"enabled": True})).json()["enabled"])
        for decision, arguments, expected in [(True, {"value": "ok"}, "success"), (False, {"value": "ok"}, "rejected"), (True, {}, "invalid")]:
            MockService.arguments = arguments
            response = await self.client.post("/api/chat/stream", json={"character_id": character_id, "model_profile_id": model["id"], "message": "use tool"})
            self.assertIn("tool_waiting_confirmation", response.text)
            self.assertNotIn("tools/call", MockService.calls)
            conversation_id = json.loads(response.text.split("event: message_start\ndata: ")[1].split("\n")[0])["conversation_id"]
            runs = (await self.client.get("/api/chat/tool-runs", params={"conversation_id": conversation_id})).json()
            run_id = runs[-1]["id"]
            self.assertEqual((await self.client.post("/api/chat/stream", json={"character_id": character_id, "conversation_id": conversation_id, "message": "another turn"})).status_code, 409)
            self.assertEqual((await self.client.post(f"/api/chat/tool-runs/{run_id}/decision", json={"approve": decision})).json()["status"], expected)
            self.assertEqual((await self.client.post(f"/api/chat/tool-runs/{run_id}/decision", json={"approve": decision})).status_code, 409)
            self.assertEqual("tools/call" in MockService.calls, expected == "success")
            continuation = await self.client.post(f"/api/chat/tool-runs/{run_id}/continue")
            self.assertIn("message_complete", continuation.text)
            MockService.calls.clear()


if __name__ == "__main__":
    unittest.main()
