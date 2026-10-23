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
    response_mode = "tool"
    last_messages = []
    last_payload = {}
    backups = {}
    fail_puts = 0
    put_attempts = 0
    oauth_key = None
    oauth_nonce = ""
    oauth_refreshes = 0
    oauth_revocations = 0
    last_authorization = ""

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path=="/.well-known/jwks.json":
            import base64
            numbers=self.oauth_key.public_key().public_numbers()
            encode=lambda value:base64.urlsafe_b64encode(value.to_bytes((value.bit_length()+7)//8,"big")).rstrip(b"=").decode()
            body=json.dumps({"keys":[{"kty":"RSA","kid":"mock-key","alg":"RS256","n":encode(numbers.n),"e":encode(numbers.e)}]}).encode();kind="application/json"
        elif self.path=="/.well-known/openid-configuration": body=json.dumps({"revocation_endpoint":f"http://127.0.0.1:{self.server.server_port}/api/accounts/oauth/revoke"}).encode();kind="application/json"
        elif self.path=="/models": body=json.dumps({"models":[{"visibility":"list","slug":"mock-plan","display_name":"Mock Plan"},{"visibility":"hidden","slug":"secret"}]}).encode();kind="application/json"
        elif self.path=="/repos/owner/backup": body=json.dumps({"private":True}).encode(); kind="application/json"
        elif self.path=="/repos/owner/backup/contents/backups":
            body=json.dumps([{"name":name,"size":len(data),"url":f"http://127.0.0.1:{self.server.server_port}/repos/owner/backup/contents/backups/{name}","sha":"mock-sha"} for name,data in self.backups.items()]).encode();kind="application/json"
        elif self.path.startswith("/repos/owner/backup/contents/backups/"):
            name=self.path.rsplit("/",1)[-1];body=self.backups.get(name,b"");kind="application/octet-stream"
        else: self.send_error(404);return
        self.send_response(200);self.send_header("Content-Type",kind);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)

    def do_PUT(self):
        import base64
        payload=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.put_attempts+=1
        if self.fail_puts:
            self.__class__.fail_puts-=1
            self.send_error(503)
            return
        self.backups[self.path.rsplit("/",1)[-1]]=base64.b64decode(payload["content"])
        body=b'{}';self.send_response(201);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)

    def do_DELETE(self):
        self.rfile.read(int(self.headers.get("Content-Length","0")))
        self.backups.pop(self.path.rsplit("/",1)[-1],None)
        self.send_response(204);self.end_headers()

    def do_POST(self):
        raw=self.rfile.read(int(self.headers["Content-Length"]))
        if self.path=="/api/accounts/oauth/token":
            import base64,time
            from urllib.parse import parse_qs
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            form={key:values[0] for key,values in parse_qs(raw.decode()).items()}
            if form.get("grant_type")=="refresh_token":
                self.__class__.oauth_refreshes+=1
                token={"token_type":"Bearer","access_token":"mock-access-refreshed","refresh_token":"mock-refresh-refreshed","expires_in":3600,"scope":"openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"}
            else:
                encode=lambda value:base64.urlsafe_b64encode(json.dumps(value,separators=(",",":")).encode()).rstrip(b"=").decode()
                header=encode({"alg":"RS256","kid":"mock-key"})
                claims=encode({"iss":f"http://127.0.0.1:{self.server.server_port}","aud":form["client_id"],"sub":"mock-subject","email":"mock@example.test","nonce":self.oauth_nonce,"exp":int(time.time())+3600})
                content=f"{header}.{claims}".encode()
                signature=base64.urlsafe_b64encode(self.oauth_key.sign(content,padding.PKCS1v15(),hashes.SHA256())).rstrip(b"=").decode()
                token={"token_type":"Bearer","access_token":"mock-access-initial","refresh_token":"mock-refresh-initial","id_token":f"{header}.{claims}.{signature}","expires_in":3600,"scope":"openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"}
            body=json.dumps(token).encode();self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
        if self.path=="/api/accounts/oauth/revoke":
            self.__class__.oauth_revocations+=1
            self.send_response(200);self.send_header("Content-Length","0");self.end_headers();return
        payload = json.loads(raw)
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
        elif self.path == "/responses":
            self.__class__.last_payload=payload
            self.__class__.last_authorization=self.headers.get("Authorization","")
            if not payload.get("stream"):
                body=json.dumps({"output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}).encode()
                content_type="application/json"
            else:
                def event(item): return "data: "+json.dumps(item)+"\n\n"
                reasoning={"type":"reasoning","encrypted_content":"opaque-test-only","summary":[{"type":"summary_text","text":"公开摘要"}]}
                events=[{"type":"response.reasoning_summary_text.delta","delta":"公开摘要"},{"type":"response.output_item.done","item":reasoning}]
                if payload.get("tools") and not any(item.get("type")=="function_call_output" for item in payload["input"]):
                    name=("yosaki."+payload["tools"][0]["tools"][0]["name"]) if payload["tools"][0]["type"]=="namespace" else payload["tools"][0]["name"]
                    events.extend([{"type":"response.output_item.added","output_index":1,"item":{"type":"function_call","call_id":"response-call","name":name,"arguments":""}},{"type":"response.function_call_arguments.delta","output_index":1,"delta":"{\"value\":\"ok\"}"}])
                else:
                    events.append({"type":"response.output_text.delta","delta":"Responses done"})
                events.append({"type":"response.completed","response":{"usage":{"input_tokens":11,"output_tokens":4,"input_tokens_details":{"cached_tokens":2}},"output":[]}})
                body="".join(event(item) for item in events).encode()
                content_type="text/event-stream"
        elif not payload.get("stream"):
            body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
            content_type = "application/json"
        else:
            self.__class__.last_payload=payload
            self.__class__.last_messages=payload["messages"]
            has_tool_result = any(message.get("role") == "tool" for message in payload["messages"])
            if self.response_mode == "monologue":
                delta={"content":"<inner>藏在心里的话</inner><say>说出口的话</say>","reasoning_content":"公开摘要"}
            elif has_tool_result:
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
        MockService.response_mode = "tool"
        MockService.last_messages = []
        MockService.last_payload = {}
        MockService.backups = {}
        MockService.fail_puts = 0
        MockService.put_attempts = 0
        MockService.oauth_nonce = ""
        MockService.oauth_refreshes = 0
        MockService.oauth_revocations = 0
        MockService.last_authorization = ""
        from cryptography.hazmat.primitives.asymmetric import rsa
        MockService.oauth_key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        from backend.app import chatgpt_plan
        chatgpt_plan._data_dir=root
        chatgpt_plan._attempt=None
        chatgpt_plan._last_error=""
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
        success_conversation_id=None
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
            if expected=="success": success_conversation_id=conversation_id
            MockService.calls.clear()
        import io,zipfile
        archive=await self.client.post("/api/archives/export",json={"conversation_ids":[success_conversation_id],"format":"json"})
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped: document=zipped.read(zipped.namelist()[0])
        self.assertTrue(any(run["status"]=="success" for message in json.loads(document)["messages"] for run in message.get("tool_runs",[])))
        imported=await self.client.post(f"/api/archives/import?character_id={character_id}",files={"files":("tool-history.json",document,"application/json")})
        imported_id=imported.json()[0]["conversation_id"]
        messages=(await self.client.get(f"/api/conversations/{imported_id}/messages")).json()
        self.assertTrue(any(run["status"]=="archived" for message in messages for run in message.get("tool_runs",[])))
        self.assertEqual((await self.client.get("/api/chat/tool-runs",params={"conversation_id":imported_id})).json(),[])
        self.assertNotIn("tools/call",MockService.calls)

    async def test_v2_context_archive_and_two_region_reply(self):
        self.assertEqual(app_module.split_monologue("<inner>秘密",True),("","秘密","incomplete"))
        self.assertEqual(app_module.split_monologue("<inner>秘密</inner><say>你好",True),("你好","秘密","incomplete"))
        await self.setup_owner()
        character=(await self.client.get("/api/characters")).json()[0]
        cid=character["id"]
        context={"history_limit":100,"memory_enabled":True,"window_mode":"strict","window_size":2,"window_keep":1,"reasoning_prompt":"保持简洁","monologue_enabled":True,"monologue_prompt":"写角色独白"}
        self.assertEqual((await self.client.put(f"/api/characters/{cid}/context-settings",json=context)).status_code,200)
        model=(await self.client.post("/api/models",json={"name":"mock","base_url":self.url,"model_name":"mock","reasoning_effort":"medium"})).json()
        MockService.response_mode="monologue"
        conversation_id=None
        for i in range(3):
            response=await self.client.post("/api/chat/stream",json={"character_id":cid,"conversation_id":conversation_id,"model_profile_id":model["id"],"message":f"第{i}轮"})
            self.assertIn("monologue_delta",response.text)
            self.assertIn("reasoning_delta",response.text)
            if conversation_id is None: conversation_id=json.loads(response.text.split("event: message_start\ndata: ")[1].split("\n")[0])["conversation_id"]
        self.assertEqual(len([x for x in MockService.last_messages if x["role"]=="user"]),3)
        self.assertEqual(MockService.last_payload["reasoning_effort"],"medium")
        self.assertNotIn("藏在心里的话",str(MockService.last_messages))
        messages=(await self.client.get(f"/api/conversations/{conversation_id}/messages")).json()
        self.assertEqual(messages[-1]["content"],"说出口的话")
        self.assertEqual(messages[-1]["monologue_content"],"藏在心里的话")
        self.assertEqual(messages[-1]["reasoning_content"],"公开摘要")
        context.update(window_mode="chunked",window_size=2,window_keep=1)
        await self.client.put(f"/api/characters/{cid}/context-settings",json=context)
        for i in range(3,5):
            await self.client.post("/api/chat/stream",json={"character_id":cid,"conversation_id":conversation_id,"model_profile_id":model["id"],"message":f"第{i}轮"})
        self.assertEqual(len([x for x in MockService.last_messages if x["role"]=="user"]),2)
        regenerated=await self.client.post("/api/chat/regenerate",json={"character_id":cid,"conversation_id":conversation_id,"model_profile_id":model["id"]})
        self.assertIn("message_complete",regenerated.text)
        self.assertNotIn("藏在心里的话",str(MockService.last_messages))
        refreshed=(await self.client.get(f"/api/conversations/{conversation_id}/messages")).json()
        self.assertEqual(len([x for x in refreshed if x["role"]=="user"]),5)
        preview=await self.client.post("/api/archives/export-preview",json={"conversation_ids":[conversation_id],"format":"markdown","include_monologue":True,"user_name":"我"})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertIn("内心独白",preview.json()["selected"])
        archive=await self.client.post("/api/archives/export",json={"conversation_ids":[conversation_id],"format":"json"})
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z: content=z.read(z.namelist()[0])
        files={"files":("chat.json",content,"application/json")}
        self.assertFalse((await self.client.post("/api/archives/preview",files=files)).json()[0]["duplicate"])
        imported=await self.client.post(f"/api/archives/import?character_id={cid}",files=files)
        self.assertEqual(imported.json()[0]["status"],"imported",imported.text)
        self.assertTrue((await self.client.post("/api/archives/preview",files=files)).json()[0]["duplicate"])
        self.assertEqual((await self.client.post(f"/api/archives/import?character_id={cid}",files=files)).json()[0]["status"],"skipped")

    async def test_v2_scheduled_local_backup(self):
        await self.setup_owner()
        path=Path(self.temp.name)/"sync"
        plan={"enabled":True,"frequency":"daily","hour":3,"minute":0,"weekday":0,"keep":1,"local_dir":str(path),"github_repo":""}
        self.assertEqual((await self.client.put("/api/system/backup-plan",json=plan)).status_code,200)
        self.assertEqual((await self.client.post("/api/system/backup-plan/run")).status_code,422)
        os.environ["YOSAKI_BACKUP_PASSWORD"]="test-schedule-password"
        try:
            for _ in range(2):
                result=await self.client.post("/api/system/backup-plan/run")
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(result.json()["local"],"success")
            self.assertEqual(len(list(path.glob("*.ybackup"))),1)
            self.assertEqual(len((await self.client.get("/api/system/backup-plan")).json()["runs"]),2)
            from unittest.mock import patch
            with patch.object(Path,"unlink",side_effect=OSError("mock retention failure")):
                retained=await self.client.post("/api/system/backup-plan/run")
            self.assertEqual(retained.status_code,200,retained.text)
            self.assertEqual(retained.json()["local"],"retention_failed")
            self.assertEqual(len(list(path.glob("*.ybackup"))),2)
            self.assertEqual((await self.client.get("/api/system/backup-plan")).json()["runs"][0]["local_status"],"retention_failed")
        finally: os.environ.pop("YOSAKI_BACKUP_PASSWORD",None)

    async def test_v2_archive_formats_dates_and_invalid_files(self):
        import io
        import zipfile
        await self.setup_owner()
        character_id=(await self.client.get("/api/characters")).json()[0]["id"]
        first=await self.client.post("/api/chat/stream",json={"character_id":character_id,"message":"旧消息"})
        conversation_id=json.loads(first.text.split("event: message_start\ndata: ")[1].split("\n")[0])["conversation_id"]
        await self.client.post("/api/chat/stream",json={"character_id":character_id,"conversation_id":conversation_id,"message":"新消息 ```py\nsecret\n```"})
        with app_module.connect() as db:
            ids=[row["id"] for row in db.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY id",(conversation_id,))]
            for mid in ids[:2]: db.execute("UPDATE messages SET created_at=? WHERE id=?",("2026-10-22T15:59:59+00:00",mid))
            for mid in ids[2:]: db.execute("UPDATE messages SET created_at=? WHERE id=?",("2026-10-22T16:00:00+00:00",mid))
        for format_name,extension in (("json","json"),("markdown","md"),("txt","txt")):
            result=await self.client.post("/api/archives/export",json={"conversation_ids":[conversation_id],"format":format_name,"after":"2026-10-23","before":"2026-10-23","include_code":False})
            self.assertEqual(result.status_code,200,result.text)
            with zipfile.ZipFile(io.BytesIO(result.content)) as archive: body=archive.read(archive.namelist()[0])
            self.assertIn("新消息".encode(),body)
            self.assertEqual(body.count("旧消息".encode()),1)  # conversation title remains unchanged
            if format_name!="json": self.assertNotIn(b"secret",body)
            files={"files":(f"chat.{extension}",body,"application/octet-stream")}
            self.assertEqual((await self.client.post("/api/archives/preview",files=files)).status_code,200)
            imported=await self.client.post(f"/api/archives/import?character_id={character_id}",files=files)
            self.assertEqual(imported.json()[0]["status"],"imported",imported.text)
            self.assertEqual((await self.client.post(f"/api/archives/import?character_id={character_id}",files=files)).json()[0]["status"],"skipped")
        bad={"files":("bad.md",b"# unknown\n## stranger\nhello", "text/markdown")}
        self.assertIn("error",(await self.client.post("/api/archives/preview",files=bad)).json()[0])
        self.assertEqual((await self.client.post(f"/api/archives/import?character_id={character_id}",files=bad)).json()[0]["status"],"failed")
        malformed={"files":("bad.json",b'{"format":"yosaki-conversation","version":2,"title":{"unexpected":true},"messages":[{"role":"user","content":"x"}]}',"application/json")}
        self.assertIn("error",(await self.client.post("/api/archives/preview",files=malformed)).json()[0])
        self.assertEqual((await self.client.post(f"/api/archives/import?character_id={character_id}",files=malformed)).json()[0]["status"],"failed")
        partial_txt={"files":("partial.txt",b"YOSAKI-CHAT-TXT/1\nExample\n\n@user\nvalid\n@end\n\n@unknown\nignored\n@end\n","text/plain")}
        self.assertIn("error",(await self.client.post("/api/archives/preview",files=partial_txt)).json()[0])
        self.assertEqual((await self.client.post(f"/api/archives/import?character_id={character_id}",files=partial_txt)).json()[0]["status"],"failed")
        from backend.app.archives import ExportOptions, load_file, render
        for fmt,extension,content in (("markdown","md","## 正文标题\n仍是正文"),("txt","txt","@end\n仍是正文")):
            options=ExportOptions(conversation_ids=[conversation_id],format=fmt)
            body=render("边界标记","阿夜",[{"role":"user","content":content,"created_at":"2026-10-23T12:00:00+08:00"}],options)
            self.assertEqual(load_file(f"roundtrip.{extension}",body)[1][0]["content"],content)

    async def test_v2_responses_summary_and_tool_approval(self):
        await self.setup_owner()
        character_id=(await self.client.get("/api/characters")).json()[0]["id"]
        model=(await self.client.post("/api/models",json={"name":"Responses mock","base_url":self.url,"model_name":"mock","protocol":"openai_responses","reasoning_effort":"medium"})).json()
        self.assertEqual((await self.client.post(f"/api/models/{model['id']}/test")).json()["reply_preview"],"OK")
        mcp=(await self.client.post("/api/mcp/servers",json={"name":"mock","endpoint":self.url+"/mcp"})).json()
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/connect")
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/refresh-tools")
        tool=(await self.client.get(f"/api/mcp/servers/{mcp['id']}/tools")).json()[0]
        await self.client.patch(f"/api/mcp/tools/{tool['id']}",json={"enabled":True})
        start=await self.client.post("/api/chat/stream",json={"character_id":character_id,"model_profile_id":model["id"],"message":"use tool"})
        self.assertIn("reasoning_delta",start.text)
        self.assertIn("tool_waiting_confirmation",start.text)
        self.assertNotIn("tools/call",MockService.calls)
        self.assertEqual(MockService.last_payload["reasoning"],{"effort":"medium","summary":"auto"})
        conversation_id=json.loads(start.text.split("event: message_start\ndata: ")[1].split("\n")[0])["conversation_id"]
        messages=(await self.client.get(f"/api/conversations/{conversation_id}/messages")).json()
        self.assertEqual(messages[-1]["reasoning_source"],"responses_summary")
        self.assertNotIn("provider_state_json",messages[-1])
        runs=(await self.client.get("/api/chat/tool-runs",params={"conversation_id":conversation_id})).json()
        self.assertEqual((await self.client.post(f"/api/chat/tool-runs/{runs[0]['id']}/decision",json={"approve":True})).json()["status"],"success")
        continued=await self.client.post(f"/api/chat/tool-runs/{runs[0]['id']}/continue")
        self.assertIn("Responses done",continued.text)
        self.assertIn("usage",continued.text)
        input_types=[item.get("type") for item in MockService.last_payload["input"]]
        self.assertIn("reasoning",input_types)
        self.assertIn("function_call",input_types)
        self.assertIn("function_call_output",input_types)

    async def test_chatgpt_plan_oauth_stream_tools_refresh_and_revoke(self):
        import time
        from urllib.parse import parse_qs, urlencode, urlsplit
        from backend.app import chatgpt_plan
        await self.setup_owner()
        old_auth,old_api=chatgpt_plan.AUTH_BASE,chatgpt_plan.API_BASE
        chatgpt_plan.AUTH_BASE=self.url
        chatgpt_plan.API_BASE=self.url
        os.environ["YOSAKI_CHATGPT_PLAN_ENABLED"]="1"
        try:
            cookie=self.client.cookies.get("yosaki_session")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),base_url="http://127.0.0.1:5173",cookies={"yosaki_session":cookie}) as local_client:
                started=await local_client.post("/api/chatgpt/start")
            self.assertEqual(started.status_code,200,started.text)
            authorization=started.json()["authorization_url"]
            params={key:values[0] for key,values in parse_qs(urlsplit(authorization).query).items()}
            self.assertEqual(params["client_id"],"dynamic_agent_client")
            self.assertEqual(params["code_challenge_method"],"S256")
            self.assertTrue(params["ext_agent_host_id"].startswith("urn:uuid:"))
            MockService.oauth_nonce=params["nonce"]
            callback=params["redirect_uri"]
            async with httpx.AsyncClient() as browser:
                invalid=await browser.get(callback,params={"state":"wrong","code":"mock-code","client_id":"oaiapp_mock"})
                self.assertEqual(invalid.status_code,400)
                authorized=await browser.get(callback,params={"state":params["state"],"code":"mock-code","client_id":"oaiapp_mock"})
                self.assertEqual(authorized.status_code,200,authorized.text)
                self.assertIn("history.replaceState",authorized.text)
            status=(await self.client.get("/api/chatgpt/status")).json()
            self.assertTrue(status["connected"] and status["plan_allowed"])
            self.assertNotIn("mock-access",json.dumps(status))
            encrypted=(Path(self.temp.name)/".chatgpt_plan").read_bytes()
            self.assertNotIn(b"mock-access",encrypted)
            self.assertNotIn(b"mock-refresh",encrypted)
            catalog=(await self.client.get("/api/chatgpt/models")).json()
            self.assertEqual(catalog,[{"slug":"mock-plan","display_name":"Mock Plan"}])
            bad=await self.client.post("/api/models",json={"name":"Plan","base_url":"https://bad.example","model_name":"mock-plan","protocol":"chatgpt_plan","api_key":"should-not-store"})
            self.assertEqual(bad.status_code,422)
            model=(await self.client.post("/api/models",json={"name":"Plan","base_url":"https://bad.example","model_name":"mock-plan","protocol":"chatgpt_plan"})).json()
            self.assertEqual(model["base_url"],"https://api.openai.com/v1")
            self.assertFalse(model["api_key_configured"])
            test=await self.client.post(f"/api/models/{model['id']}/test")
            self.assertEqual(test.status_code,200,test.text)
            self.assertEqual(test.json()["reply_preview"],"Responses done")
            character_id=(await self.client.get("/api/characters")).json()[0]["id"]
            mcp=(await self.client.post("/api/mcp/servers",json={"name":"mock","endpoint":self.url+"/mcp"})).json()
            await self.client.post(f"/api/mcp/servers/{mcp['id']}/connect")
            await self.client.post(f"/api/mcp/servers/{mcp['id']}/refresh-tools")
            tool=(await self.client.get(f"/api/mcp/servers/{mcp['id']}/tools")).json()[0]
            await self.client.patch(f"/api/mcp/tools/{tool['id']}",json={"enabled":True})
            first=await self.client.post("/api/chat/stream",json={"character_id":character_id,"model_profile_id":model["id"],"message":"use tool"})
            self.assertIn("tool_waiting_confirmation",first.text)
            self.assertNotIn("tools/call",MockService.calls)
            self.assertEqual(MockService.last_authorization,"Bearer mock-access-initial")
            request=MockService.last_payload
            self.assertEqual(request["store"],False)
            self.assertEqual(request["stream"],True)
            self.assertNotIn("max_output_tokens",request)
            self.assertNotIn("temperature",request)
            self.assertEqual(request["tools"][0]["type"],"namespace")
            self.assertTrue(all(item.get("role")!="system" for item in request["input"]))
            conversation_id=json.loads(first.text.split("event: message_start\ndata: ")[1].split("\n")[0])["conversation_id"]
            run=(await self.client.get("/api/chat/tool-runs",params={"conversation_id":conversation_id})).json()[0]
            self.assertEqual((await self.client.post(f"/api/chat/tool-runs/{run['id']}/decision",json={"approve":True})).json()["status"],"success")
            continued=await self.client.post(f"/api/chat/tool-runs/{run['id']}/continue")
            self.assertIn("Responses done",continued.text)
            account=chatgpt_plan._read()
            account["account"]["expires_at"]=time.time()-1
            chatgpt_plan._write(account)
            self.assertEqual((await self.client.get("/api/chatgpt/models")).status_code,200)
            self.assertEqual(MockService.oauth_refreshes,1)
            disconnected=await self.client.post("/api/chatgpt/disconnect")
            self.assertTrue(disconnected.json()["remote_revoked"])
            self.assertEqual(MockService.oauth_revocations,1)
            self.assertFalse((await self.client.get("/api/chatgpt/status")).json()["connected"])
            last_request=MockService.last_payload
            denied=await self.client.post("/api/chat/stream",json={"character_id":character_id,"model_profile_id":model["id"],"message":"after disconnect"})
            self.assertIn("请先连接已授权 ChatGPT 套餐的账户",denied.text)
            self.assertEqual(MockService.last_payload,last_request)
        finally:
            chatgpt_plan.AUTH_BASE,chatgpt_plan.API_BASE=old_auth,old_api
            os.environ.pop("YOSAKI_CHATGPT_PLAN_ENABLED",None)

    async def test_v2_mock_github_backup(self):
        await self.setup_owner()
        from backend.app import scheduled_backup
        old_base=scheduled_backup.GITHUB_API_BASE
        scheduled_backup.GITHUB_API_BASE=self.url
        os.environ["YOSAKI_BACKUP_PASSWORD"]="test-schedule-password"
        os.environ["YOSAKI_BACKUP_GITHUB_TOKEN"]="mock-github-token"
        try:
            plan={"enabled":False,"frequency":"daily","hour":3,"minute":0,"weekday":0,"keep":7,"local_dir":"","github_repo":"owner/backup"}
            self.assertEqual((await self.client.put("/api/system/backup-plan",json=plan)).status_code,200)
            run=await self.client.post("/api/system/backup-plan/run")
            self.assertEqual(run.status_code,200,run.text)
            self.assertEqual(run.json()["github"],"success")
            listing=(await self.client.get("/api/system/backup-plan/github")).json()
            self.assertEqual(len(listing),1)
            download=await self.client.get("/api/system/backup-plan/github/"+listing[0]["name"])
            self.assertEqual(download.status_code,200)
            self.assertNotIn(b"mock-github-token",download.content)
            self.assertNotIn(b"test-schedule-password",download.content)
            MockService.fail_puts=2
            retried=await self.client.post("/api/system/backup-plan/run")
            self.assertEqual(retried.json()["github"],"success")
            self.assertEqual(MockService.put_attempts,4)
            local=Path(self.temp.name)/"sync"
            plan["local_dir"]=str(local)
            self.assertEqual((await self.client.put("/api/system/backup-plan",json=plan)).status_code,200)
            MockService.fail_puts=3
            failed=await self.client.post("/api/system/backup-plan/run")
            self.assertEqual(failed.json()["local"],"success")
            self.assertEqual(failed.json()["github"],"failed")
            self.assertEqual(len(list(local.glob("*.ybackup"))),1)
            self.assertEqual(MockService.put_attempts,7)
            before_retention=set(MockService.backups)
            plan["keep"]=1
            self.assertEqual((await self.client.put("/api/system/backup-plan",json=plan)).status_code,200)
            retained=await self.client.post("/api/system/backup-plan/run")
            self.assertEqual(retained.json()["github"],"success")
            self.assertEqual(len(MockService.backups),1)
            self.assertFalse(set(MockService.backups).issubset(before_retention))
        finally:
            scheduled_backup.GITHUB_API_BASE=old_base
            os.environ.pop("YOSAKI_BACKUP_PASSWORD",None)
            os.environ.pop("YOSAKI_BACKUP_GITHUB_TOKEN",None)

    async def test_v2_scheduler_catchup_and_concurrent_run(self):
        import asyncio
        from backend.app import scheduled_backup
        await self.setup_owner()
        local=Path(self.temp.name)/"catchup"
        plan={"enabled":True,"frequency":"daily","hour":3,"minute":0,"weekday":0,"keep":7,"local_dir":str(local),"github_repo":""}
        self.assertEqual((await self.client.put("/api/system/backup-plan",json=plan)).status_code,200)
        with app_module.connect() as db:
            db.execute("UPDATE backup_plan SET next_run_at='2020-01-01T00:00:00+00:00' WHERE id=1")
        os.environ["YOSAKI_BACKUP_PASSWORD"]="test-schedule-password"
        try:
            scheduled_backup.run_lock.acquire()
            try:
                self.assertEqual((await self.client.post("/api/system/backup-plan/run")).status_code,409)
            finally: scheduled_backup.run_lock.release()
            for handler in app_module.app.router.on_startup: await handler()
            await asyncio.sleep(0.2)
            self.assertEqual(len(list(local.glob("*.ybackup"))),1)
            self.assertEqual(len((await self.client.get("/api/system/backup-plan")).json()["runs"]),1)
        finally:
            for handler in app_module.app.router.on_shutdown: await handler()
            await asyncio.sleep(0)
            os.environ.pop("YOSAKI_BACKUP_PASSWORD",None)


if __name__ == "__main__":
    unittest.main()
