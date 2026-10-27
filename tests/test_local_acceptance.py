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
    decision_response = None
    decision_responses = []
    plan_text = "Responses done"

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
                    events.append({"type":"response.output_text.delta","delta":self.plan_text})
                events.append({"type":"response.completed","response":{"usage":{"input_tokens":11,"output_tokens":4,"input_tokens_details":{"cached_tokens":2}},"output":[]}})
                body="".join(event(item) for item in events).encode()
                content_type="text/event-stream"
        elif not payload.get("stream"):
            self.__class__.last_payload=payload
            selected = self.decision_responses.pop(0) if self.decision_responses else self.decision_response or "OK"
            body = json.dumps({"choices": [{"message": {"content": selected}}]}).encode()
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
        MockService.decision_response = None
        MockService.decision_responses = []
        MockService.plan_text = "Responses done"
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

    async def test_v3_desires_and_manual_awakening(self):
        await self.setup_owner()
        character = (await self.client.get("/api/characters")).json()[0]
        cid = character["id"]
        base = f"/api/desires/{cid}"
        self.assertFalse((await self.client.get(base)).json()["enabled"])
        self.assertEqual((await self.client.post(base + "/events", json={"event_id":"event-0001","description":"test","effects":[{"drive_id":"exploration.curiosity","activation_delta":0.8}]})).status_code, 409)
        self.assertEqual((await self.client.put(base + "/enabled", json={"enabled":True})).status_code, 200)
        event = {"event_id":"event-0001","description":"发现新问题","effects":[{"drive_id":"exploration.curiosity","activation_delta":0.8},{"drive_id":"physiological.rest","activation_delta":0.75}]}
        self.assertTrue((await self.client.post(base + "/events", json=event)).json()["applied"])
        duplicate = (await self.client.post(base + "/events", json=event)).json()
        self.assertFalse(duplicate["applied"])
        self.assertTrue(duplicate["advisory"]["conflicts"])
        self.assertEqual((await self.client.post(base + "/events", json={"event_id":"event-0002","description":"bad","effects":[{"drive_id":"unknown","activation_delta":0.2}]})).status_code, 422)
        simulation = (await self.client.post(base + "/simulate", json={"hours":720,"events":[]})).json()
        self.assertFalse(simulation["saved"])
        self.assertLess(next(item["activation"] for item in simulation["drives"] if item["drive_id"] == "exploration.curiosity"), 0.5)
        self.assertGreater((await self.client.get(base)).json()["drives"][0]["version"], 0)
        shot = (await self.client.post(base + "/snapshots", json={})).json()
        self.assertEqual((await self.client.post(base + f"/snapshots/{shot['id']}/restore", json={})).status_code, 200)
        with app_module.connect() as db:
            saved = json.loads(db.execute("SELECT state_json FROM desire_snapshots WHERE id=?", (shot["id"],)).fetchone()[0])
            saved["drives"][0]["activation"] = 9
            db.execute("UPDATE desire_snapshots SET state_json=? WHERE id=?", (json.dumps(saved), shot["id"]))
        self.assertEqual((await self.client.post(base + f"/snapshots/{shot['id']}/restore", json={})).status_code, 422)
        negative = {"event_id":"feedback-negative","description":"未完成探索","activity_id":"activity-negative","completed":False,"outcome":"negative","effects":[{"drive_id":"exploration.curiosity","activation_delta":0.1,"satisfaction_delta":-0.1}]}
        self.assertTrue((await self.client.post(base + "/feedback", json=negative)).json()["applied"])
        for index in range(3):
            feedback = {"event_id":f"feedback-{index}","description":"完成探索","activity_id":f"activity-{index}","completed":True,"outcome":"positive","effects":[{"drive_id":"exploration.curiosity","activation_delta":-0.1,"satisfaction_delta":0.2}]}
            self.assertTrue((await self.client.post(base + "/feedback", json=feedback)).json()["applied"])
            if index == 1:
                self.assertEqual((await self.client.get(base + "/preferences")).json(), [])
        suggestion = (await self.client.get(base + "/preferences")).json()[0]
        self.assertEqual(suggestion["status"], "pending")
        self.assertEqual((await self.client.post(base + f"/preferences/{suggestion['id']}/decision", json={"approve":True})).json()["status"], "applied")
        self.assertEqual((await self.client.post(base + f"/preferences/{suggestion['id']}/rollback", json={})).json()["status"], "rolled_back")
        other = (await self.client.post("/api/characters", json={"name":"另一个角色"})).json()
        self.assertFalse((await self.client.get(f"/api/desires/{other['id']}")).json()["enabled"])
        duplicate_character = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        self.assertFalse((await self.client.get(f"/api/desires/{duplicate_character['id']}")).json()["enabled"])
        model = (await self.client.post("/api/models", json={"name":"mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        mcp = (await self.client.post("/api/mcp/servers", json={"name":"mock","endpoint":self.url+"/mcp"})).json()
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/connect")
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/refresh-tools")
        tool = (await self.client.get(f"/api/mcp/servers/{mcp['id']}/tools")).json()[0]
        await self.client.patch(f"/api/mcp/tools/{tool['id']}", json={"enabled":True})
        await self.client.put(base + f"/tools/{tool['id']}/policy", json={"acknowledged_read_only":True})
        options = {"model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":2,"max_tool_calls":1,"allowed_tool_ids":[tool["id"]]}
        session = (await self.client.post(base + "/awakening", json=options)).json()
        sid = session["id"]
        MockService.decision_response = json.dumps({"decision":"tool","goal":"核对信息","reasonSummary":"轻量探索","relatedDrives":["exploration.curiosity"],"toolId":tool["id"],"arguments":{"value":"ok"}})
        result = await self.client.post(base + f"/awakening/{sid}/step", json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["tool_calls"], 1)
        self.assertEqual(result.json()["history"][-1]["kind"], "tool_result")
        self.assertEqual((await self.client.get(base + "/goals")).json()[0]["status"], "chosen")
        self.assertEqual((await self.client.post(base + f"/awakening/{sid}/pause", json={})).json()["status"], "paused")
        self.assertEqual((await self.client.post(base + f"/awakening/{sid}/resume", json={})).json()["status"], "ready")
        MockService.decision_response = json.dumps({"decision":"rest","goal":"休息","reasonSummary":"当前选择休息","relatedDrives":[],"suggestedEffects":[{"drive_id":"physiological.rest","activation_delta":-0.1,"satisfaction_delta":0.2}]})
        self.assertEqual((await self.client.post(base + f"/awakening/{sid}/step", json={})).json()["status"], "completed")
        before = (await self.client.get(base)).json()
        rest_before = next(item["satisfaction"] for item in before["drives"] if item["drive_id"] == "physiological.rest")
        proposed = {"event_id":"rest-feedback","description":"确认休息反馈","activity_id":sid,"completed":True,"outcome":"positive","effects":[{"drive_id":"physiological.rest","activation_delta":-0.1,"satisfaction_delta":0.2}]}
        self.assertTrue((await self.client.post(base + "/feedback", json=proposed)).json()["applied"])
        self.assertFalse((await self.client.post(base + "/feedback", json={**proposed,"event_id":"rest-feedback-other"})).json()["applied"])
        rest_after = next(item["satisfaction"] for item in (await self.client.get(base)).json()["drives"] if item["drive_id"] == "physiological.rest")
        self.assertGreater(rest_after, rest_before)
        self.assertEqual((await self.client.get(base + "/goals")).json()[0]["status"], "completed")
        second = (await self.client.post(base + "/awakening", json=options)).json()
        self.assertEqual((await self.client.post(base + f"/awakening/{second['id']}/cancel", json={})).json()["status"], "cancelled")
        third = (await self.client.post(base + "/awakening", json=options)).json()
        await self.client.put(base + f"/tools/{tool['id']}/policy", json={"acknowledged_read_only":False})
        MockService.decision_response = json.dumps({"decision":"tool","goal":"核对信息","reasonSummary":"继续探索","relatedDrives":[],"toolId":tool["id"],"arguments":{"value":"ok"}})
        pending = (await self.client.post(base + f"/awakening/{third['id']}/step", json={})).json()
        self.assertEqual(pending["status"], "awaiting_confirmation")
        self.assertFalse(pending["tool_request"]["read_only_preapproved"])
        await self.client.patch(f"/api/mcp/tools/{tool['id']}", json={"enabled":False})
        await self.client.patch(f"/api/mcp/tools/{tool['id']}", json={"enabled":True})
        self.assertEqual((await self.client.post(base + f"/awakening/{third['id']}/tools/decision", json={"approve":True})).status_code, 403)
        self.assertEqual((await self.client.post(base + f"/awakening/{third['id']}/tools/decision", json={"approve":False})).json()["status"], "ready")
        self.assertEqual((await self.client.post(base + f"/awakening/{third['id']}/cancel", json={})).json()["status"], "cancelled")
        fourth = (await self.client.post(base + "/awakening", json={**options,"max_model_calls":1,"allowed_tool_ids":[]})).json()
        MockService.decision_response = "invalid"
        self.assertEqual((await self.client.post(base + f"/awakening/{fourth['id']}/step", json={})).status_code, 502)
        self.assertEqual((await self.client.get(base + f"/awakening/{fourth['id']}")).json()["status"], "ready")
        self.assertEqual((await self.client.post(base + f"/awakening/{fourth['id']}/step", json={})).json()["status"], "completed")
        with app_module.connect() as db:
            db.execute("UPDATE awakening_sessions SET status='acting' WHERE id=?", (second["id"],))
        app_module.init_db()
        self.assertEqual((await self.client.get(base + f"/awakening/{second['id']}")).json()["status"], "paused")
        exported = await self.client.post("/api/system/backup/export", data={"passphrase":"test-password-123"})
        self.assertEqual(exported.status_code, 200)
        self.assertIn(b"YOSAKI", exported.content[:30])
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM drive_states WHERE character_id=?", (cid,)).fetchone()[0], 0)
        restored = await self.client.post("/api/system/backup/import", data={"passphrase":"test-password-123","confirm_replace":"true"}, files={"backup":("v3.ybackup",exported.content,"application/octet-stream")})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username":"owner","password":"temporary-password-123"})).status_code, 200)
        restored_state = (await self.client.get(base)).json()
        self.assertTrue(restored_state["enabled"])
        self.assertEqual(len(restored_state["drives"]), 12)

    async def wait_free_status(self, base, session_id, expected):
        import asyncio
        for _ in range(80):
            response = await self.client.get(base + f"/awakening/{session_id}")
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()
            if item["status"] in expected:
                return item
            await asyncio.sleep(.05)
        self.fail(f"free activity did not reach {expected}: {item['status']}")

    async def test_phase5_incremental_preference_memory_and_rollback(self):
        from backend.app import learning
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled": True})

        def seed(index, rating, *, context="local-studio-v1", title="地图研究"):
            stamp = app_module.now()
            sid, fid, pid, oid = (f"learn-session-{index}", f"learn-feedback-{index}", f"learn-plan-{index}", f"learn-operation-{index}")
            with app_module.connect() as db:
                db.execute("""INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json,mode,environment_id)
                    VALUES(?,?,1,'completed',?,?,1,1,'[]','{}','free',?)""", (sid,cid,stamp,stamp,context))
                db.execute("INSERT INTO activity_plans(id,character_id,session_id,title,status,created_at,updated_at) VALUES(?,?,?,?,'completed',?,?)", (pid,cid,sid,title,stamp,stamp))
                db.execute("INSERT INTO activity_operations(id,character_id,session_id,kind,arguments_json,status,created_at,updated_at) VALUES(?,?,?,'environment','{}','succeeded',?,?)", (oid,cid,sid,stamp,stamp))
                review = {"enjoyment": rating,"reflectionSummary":"这次体验与以前不同","confidence":0.8}
                db.execute("""INSERT INTO feedback_activities_v4(id,character_id,session_id,plan_id,title,objective_outcome,end_reason,evidence_json,source_event_ids_json,review_json,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,'partial','finish',?,?,?,'applied',?,?)""", (fid,cid,sid,pid,title,json.dumps([{"operationId":oid,"status":"succeeded"}]),json.dumps([oid]),json.dumps(review),stamp,stamp))
            return fid

        for index, rating in enumerate((0.8, 0.85, 0.75), 1):
            seed(index, rating)
        overview = (await self.client.get(base + "/learning")).json()
        self.assertEqual(overview["newEvidence"], 3)
        self.assertEqual(len(overview["evidence"]), 3)
        self.assertEqual((await self.client.get(base + "/learning")).json()["newEvidence"], 0)
        pref = overview["preferences"][0]
        self.assertEqual(pref["estimate"], 0.5)
        proposal = overview["proposals"][0]
        self.assertGreater(proposal["after"]["estimate"], 0.5)
        self.assertEqual(len(proposal["evidence_ids"]), 3)
        decision = {"schemaVersion":5,"requestId":"learn-approve-001","expectedVersion":proposal["version"],"approve":True,"adjustedEstimate":0.9,"adjustedConfidence":0.7}
        applied = await self.client.post(base + f"/learning/preferences/{proposal['id']}/decision", json=decision)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["preference"]["estimate"], 0.9)
        self.assertEqual((await self.client.post(base + f"/learning/preferences/{proposal['id']}/decision", json=decision)).json(), applied.json())
        stale = await self.client.post(base + f"/learning/preferences/{proposal['id']}/decision", json={**decision,"requestId":"learn-stale-001"})
        self.assertEqual(stale.status_code, 409)
        ctx = (await self.client.get(base + "/learning/context", params={"topic":"地图研究"})).json()
        self.assertEqual(ctx["preferences"][0]["subject"], "地图研究")
        memories = (await self.client.get(base + "/learning")).json()["memories"]
        self.assertEqual(len(memories), 3)
        memory = memories[0]
        self.assertIn("操作", memory["fact"])
        self.assertIn("体验", memory["content"])
        accepted = await self.client.post(base + f"/learning/memories/{memory['id']}/decision", json={"schemaVersion":5,"requestId":"learn-memory-001","expectedVersion":memory["version"],"action":"accept"})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        remaining = [x for x in (await self.client.get(base + "/learning")).json()["memories"] if x["status"] == "candidate"]
        merged = await self.client.post(base + f"/learning/memories/{remaining[0]['id']}/merge", json={"schemaVersion":5,"requestId":"learn-merge-001","expectedVersion":remaining[0]["version"],"otherId":remaining[1]["id"],"otherExpectedVersion":remaining[1]["version"]})
        self.assertEqual(merged.status_code, 200, merged.text)
        self.assertEqual(len(merged.json()["source_ids"]), 2)
        self.assertIn(remaining[1]["id"], merged.json()["merged_ids"])
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT scope FROM memories WHERE id=?", (accepted.json()["memory_id"],)).fetchone()[0], "long")
        history = (await self.client.get(base + f"/learning/preferences/{pref['id']}/timeline")).json()
        self.assertEqual(len(history), 1)
        revision = (await self.client.get(base + f"/learning/preferences/{proposal['id']}/revision")).json()
        rolled = await self.client.post(base + f"/learning/preferences/{proposal['id']}/rollback", json={"schemaVersion":5,"requestId":"learn-rollback-001","expectedVersion":revision["version"]})
        self.assertEqual(rolled.status_code, 200, rolled.text)
        self.assertEqual(rolled.json()["preference"]["estimate"], 0.5)
        self.assertEqual(len((await self.client.get(base + f"/learning/preferences/{pref['id']}/timeline")).json()), 2)
        self.assertEqual(len((await self.client.get(base + "/learning")).json()["evidence"]), 3)
        self.assertEqual(learning.preference_score([])["estimate"], 0.5)
        model = (await self.client.post("/api/models", json={"name":"learning-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        source_id = overview["evidence"][0]["id"]
        MockService.decision_response = json.dumps({"schemaVersion":5,"memories":[{"sourceEvidenceIds":[source_id],"subject":"地图研究","fact":"查看了地图","interpretation":"可能喜欢研究地图","content":"查看地图后产生了新的兴趣解释","confidence":0.6}],"strategies":[{"sourceEvidenceIds":[source_id],"name":"分步研究","description":"先查看线索再总结","condition":"地图研究","confidence":0.5}],"subjects":[{"sourceEvidenceIds":[source_id],"subject":"旧地图探索","contextKey":"local-studio-v1","reason":"与旧地图有关"}]},ensure_ascii=False)
        batch = {"schemaVersion":5,"requestId":"learn-batch-001","modelProfileId":model["id"],"maxOutputTokens":800}
        consolidated = await self.client.post(base + "/learning/consolidate", json=batch)
        self.assertEqual(consolidated.status_code, 200, consolidated.text)
        self.assertEqual(consolidated.json()["status"], "completed")
        self.assertEqual((await self.client.post(base + "/learning/consolidate", json=batch)).json()["id"], consolidated.json()["id"])
        self.assertEqual((await self.client.post(base + "/learning/consolidate", json={**batch,"maxOutputTokens":801})).status_code, 409)
        subject = (await self.client.get(base + "/learning")).json()["subjects"][0]
        classified = await self.client.post(base + f"/learning/subjects/{subject['id']}/decision", json={"schemaVersion":5,"requestId":"learn-subject-001","expectedVersion":subject["version"],"action":"approve"})
        self.assertEqual(classified.status_code, 200, classified.text)
        self.assertEqual(classified.json()["status"], "applied")
        self.assertTrue(any(x["subject"] == "旧地图探索" for x in (await self.client.get(base + "/learning")).json()["preferences"]))
        candidate = (await self.client.get(base + "/learning")).json()["strategies"][0]
        approved_strategy = await self.client.post(base + f"/learning/strategies/{candidate['id']}/decision", json={"schemaVersion":5,"requestId":"learn-strategy-001","expectedVersion":candidate["version"],"action":"approve"})
        self.assertEqual(approved_strategy.status_code, 200, approved_strategy.text)
        self.assertEqual(approved_strategy.json()["status"], "available")
        seed(4, 0.9)
        with app_module.connect() as db:
            db.execute("UPDATE activity_operations SET strategy_id=? WHERE id='learn-operation-4'", (candidate["id"],))
        self.assertEqual((await self.client.get(base + "/learning")).json()["newEvidence"], 1)
        strategy = (await self.client.get(base + "/learning")).json()["strategies"][0]
        self.assertEqual(strategy["usage_count"], 1)
        evaluated = await self.client.post(base + f"/learning/strategies/{strategy['id']}/decision", json={"schemaVersion":5,"requestId":"learn-strategy-evaluate-001","expectedVersion":strategy["version"],"action":"evaluate"})
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertGreater(evaluated.json()["effectiveness"], 0.5)
        repeated_evaluation = await self.client.post(base + f"/learning/strategies/{strategy['id']}/decision", json={"schemaVersion":5,"requestId":"learn-strategy-evaluate-repeat","expectedVersion":evaluated.json()["version"],"action":"evaluate"})
        self.assertEqual(repeated_evaluation.status_code, 409)
        self.assertTrue((await self.client.get(base + "/learning/context", params={"topic":"地图研究"})).json()["strategies"])
        MockService.decision_response = json.dumps({"schemaVersion":2,"type":"no_action","reasonSummary":"可以先休息"},ensure_ascii=False)
        decision_result = await self.client.post(base + "/decisions", json={"schemaVersion":2,"requestId":"learn-volition-context-001","modelProfileId":model["id"],"environmentSummary":"地图研究"})
        self.assertEqual(decision_result.status_code, 200, decision_result.text)
        self.assertIn("longTermLearning", json.dumps(MockService.last_payload,ensure_ascii=False))
        self.assertIn("分步研究", json.dumps(MockService.last_payload,ensure_ascii=False))
        MockService.decision_response = json.dumps({"schemaVersion":3,"choice":"finish","reasonSummary":"此刻先结束"},ensure_ascii=False)
        awakened = await self.client.post(base + "/awakening", json={"mode":"free","request_id":"learn-next-awakening-001","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":1,"max_tool_calls":0,"max_output_tokens":800})
        self.assertEqual(awakened.status_code, 200, awakened.text)
        await self.wait_free_status(base, awakened.json()["id"], {"completed"})
        self.assertIn("longTermLearning", json.dumps(MockService.last_payload,ensure_ascii=False))
        self.assertIn("分步研究", json.dumps(MockService.last_payload,ensure_ascii=False))
        MockService.decision_response = "invalid"
        failed = await self.client.post(base + "/learning/consolidate", json={**batch,"requestId":"learn-batch-invalid-001"})
        self.assertEqual(failed.status_code, 502)
        self.assertEqual((await self.client.get(base + "/learning")).json()["strategies"][0]["status"], "available")
        for count in (100, 500):
            score = learning.preference_score([{"session_id":str(i),"selection_kind":"voluntary","enjoyment":0.8 if i%2 else 0.2} for i in range(count)])
            self.assertTrue(0 <= score["estimate"] <= 1)
            self.assertTrue(0 <= score["confidence"] <= 1)
            self.assertLess(abs(score["estimate"]-0.5), 0.05)
        simulated = await self.client.post(base + "/learning/simulate", json=[0.8 if i%2 else 0.2 for i in range(500)])
        self.assertEqual(simulated.status_code, 200, simulated.text)
        self.assertFalse(simulated.json()["stateChanged"])
        self.assertEqual(simulated.json()["trajectory"][-1]["step"], 500)
        backup = await self.client.post("/api/system/backup/export", data={"passphrase":"phase5-backup-password"})
        self.assertEqual(backup.status_code, 200)
        copied = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        await self.client.put(f"/api/desires/{copied['id']}/enabled", json={"enabled":True})
        self.assertEqual((await self.client.get(f"/api/desires/{copied['id']}/learning")).json()["evidence"], [])
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        restored = await self.client.post("/api/system/backup/import", data={"passphrase":"phase5-backup-password","confirm_replace":"true"}, files={"backup":("phase5.ybackup",backup.content,"application/octet-stream")})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username":"owner","password":"temporary-password-123"})).status_code, 200)
        self.assertGreaterEqual(len((await self.client.get(base + "/learning")).json()["evidence"]), 4)

    async def test_phase5_model_failure_empty_and_role_isolation(self):
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled":True})
        model = (await self.client.post("/api/models", json={"name":"learning-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        empty = await self.client.post(base + "/learning/consolidate", json={"schemaVersion":5,"requestId":"learn-empty-001","modelProfileId":model["id"],"maxOutputTokens":300})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["status"], "empty")
        copy = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        await self.client.put(f"/api/desires/{copy['id']}/enabled", json={"enabled":True})
        self.assertEqual((await self.client.get(f"/api/desires/{copy['id']}/learning")).json()["evidence"], [])
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        with app_module.connect() as db:
            for table in ("learning_evidence","learning_preferences","learning_memories","learning_strategies","learning_goal_notes","learning_audit"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE character_id=?", (cid,)).fetchone()[0], 0)

    async def test_phase4_review_expectation_confirmation_and_backup(self):
        import asyncio
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        self.assertEqual((await self.client.put(base + "/enabled", json={"enabled": True})).status_code, 200)
        model = (await self.client.post("/api/models", json={"name":"feedback-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        before = (await self.client.get(base)).json()
        drive_before = next(x for x in before["drives"] if x["drive_id"] == "exploration.curiosity")
        review = {"schemaVersion":4,"reviews":[{"feedbackId":"placeholder","enjoyment":0.9,"fulfillment":0.8,"frustration":0.1,"fatigue":0.1,"novelty":0.9,"wantsToContinue":True,"affectedDrives":[{"driveId":"exploration.curiosity","relevance":0.9,"reason":"实际发现了新线索"}],"newInterests":[],"reflectionSummary":"原本以为平淡，实际颇有趣。","confidence":0.8}]}
        MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":"environment","goalTitle":"研究地图","actionId":"inspect:map","expectedEnjoyment":0.7}),json.dumps({"schemaVersion":3,"choice":"finish"})]
        started = await self.client.post(base + "/awakening", json={"mode":"free","request_id":"phase4-start-001","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":2,"max_tool_calls":1,"max_output_tokens":1600,"feedback_review_calls":1,"feedback_review_tokens":600})
        self.assertEqual(started.status_code, 200, started.text)
        sid = started.json()["id"]
        ended = await self.wait_free_status(base, sid, {"completed"})
        review["reviews"][0]["feedbackId"] = ended["current_plan_id"]
        MockService.decision_responses = [json.dumps(review, ensure_ascii=False)]
        items = (await self.client.get(base + "/feedback-v4")).json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["objective_outcome"], "partial")
        self.assertEqual(items[0]["expected_enjoyment"], 0.7)
        self.assertEqual((await self.client.get(base)).json()["drives"][6]["baseline"], drive_before["baseline"])
        for _ in range(100):
            items = (await self.client.get(base + "/feedback-v4")).json()
            if items[0]["status"] == "ready" and items[0]["review"]:
                break
            await asyncio.sleep(.05)
        self.assertEqual(items[0]["status"], "ready", items[0])
        self.assertAlmostEqual(items[0]["expectation_gap"], 0.2)
        self.assertTrue(items[0]["proposal"])
        unchanged = next(x for x in (await self.client.get(base)).json()["drives"] if x["drive_id"] == "exploration.curiosity")
        self.assertEqual(unchanged["satisfaction"], drive_before["satisfaction"])
        decision = {"schemaVersion":4,"requestId":"phase4-apply-001","expectedVersion":items[0]["version"],"apply":True}
        applied = await self.client.post(base + f"/feedback-v4/{items[0]['id']}/decision", json=decision)
        if applied.status_code == 409:
            refreshed = (await self.client.get(base + f"/feedback-v4/{items[0]['id']}")).json()
            self.assertEqual(refreshed["status"], "ready")
            decision = {**decision, "requestId": "phase4-apply-after-recompute", "expectedVersion": refreshed["version"]}
            applied = await self.client.post(base + f"/feedback-v4/{items[0]['id']}/decision", json=decision)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["status"], "applied")
        self.assertEqual((await self.client.post(base + f"/feedback-v4/{items[0]['id']}/decision", json=decision)).json()["status"], "applied")
        after = next(x for x in (await self.client.get(base)).json()["drives"] if x["drive_id"] == "exploration.curiosity")
        self.assertGreater(after["satisfaction"], drive_before["satisfaction"])
        self.assertEqual(after["baseline"], drive_before["baseline"])
        self.assertEqual(len((await self.client.get(base + "/feedback-v4/interests/candidates")).json()), 1)
        self.assertEqual(len((await self.client.get(base + "/feedback-v4/timeline/events")).json()), 1)
        self.assertEqual(len((await self.client.get(base + "/feedback-v4/goals/events")).json()), 1)
        learning_state = (await self.client.get(base + "/learning")).json()
        self.assertEqual(len(learning_state["evidence"]), 1)
        self.assertIn("本地环境返回", learning_state["memories"][0]["fact"])
        backup = await self.client.post("/api/system/backup/export", data={"passphrase":"phase4-backup-password"})
        self.assertEqual(backup.status_code, 200)
        restored = await self.client.post("/api/system/backup/import", data={"passphrase":"phase4-backup-password","confirm_replace":"true"}, files={"backup":("phase4.ybackup",backup.content,"application/octet-stream")})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username":"owner","password":"temporary-password-123"})).status_code, 200)
        self.assertEqual((await self.client.get(base + f"/feedback-v4/{items[0]['id']}")).json()["status"], "applied")
        copied = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        await self.client.put(f"/api/desires/{copied['id']}/enabled", json={"enabled": True})
        self.assertEqual((await self.client.get(f"/api/desires/{copied['id']}/feedback-v4")).json(), [])
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        with app_module.connect() as db:
            for table in ("feedback_activities_v4","experience_reviews_v4","drive_regulation_v4","activity_cooldowns_v4","interest_evidence_v4","goal_feedback_v4"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE character_id=?", (cid,)).fetchone()[0], 0, table)

    async def test_phase4_simulation_bounds_and_no_collapse(self):
        from backend.app import feedback
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled": True})
        for count in (100, 500):
            events = [{"driveId":"exploration.curiosity" if index%2 else "physiological.rest","relevance":0.8,"confidence":0.7,"fulfillment":0.8 if index%2 else 0.3,"novelty":0.5 if index%5==0 else 0.1,"frustration":0.2,"fatigue":0.3,"repeatCount":index//2} for index in range(count)]
            response = await self.client.post(base + "/feedback-v4/simulate", json={"schemaVersion":4,"events":events})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertFalse(result["saved"])
            self.assertEqual(result["trajectory"][-1]["step"], count)
            for point in result["trajectory"]:
                values = [d["activation"] for d in point["drives"].values()]
                self.assertFalse(all(value <= 0.02 for value in values))
                self.assertFalse(all(value >= 0.98 for value in values))
                self.assertTrue(all(0 <= value <= 1 for value in values))
                satisfaction = [d["satisfaction"] for d in point["drives"].values()]
                self.assertFalse(all(value <= 0.02 for value in satisfaction))
                self.assertFalse(all(value >= 0.98 for value in satisfaction))
            samples = [point["drives"]["exploration.curiosity"]["activation"] for point in result["trajectory"]]
            changes = [b-a for a,b in zip(samples, samples[1:])]
            strong_reversals = sum(a*b < 0 and min(abs(a),abs(b)) >= 0.08 for a,b in zip(changes,changes[1:]))
            self.assertLess(strong_reversals, 3, f"persistent oscillation in {count}-step simulation")
        baseline = {x["drive_id"]: x for x in (await self.client.get(base)).json()["drives"]}
        self.assertEqual(baseline["exploration.curiosity"]["activation"], 0)
        self.assertLess(abs(feedback.regulate({"drive_id":"exploration.curiosity","activation":0.5,"satisfaction":0.5,"inhibition":0.1},0.8,0.7,{"fulfillment":0.8,"novelty":0.3,"frustration":0.2},0)["activation_delta"]),0.12)

    async def test_phase4_invalid_review_and_drive_version_conflict(self):
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled": True})
        model = (await self.client.post("/api/models", json={"name":"feedback-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":"environment","goalTitle":"查看地图","actionId":"inspect:map"}),json.dumps({"schemaVersion":3,"choice":"finish"})]
        started = (await self.client.post(base + "/awakening", json={"mode":"free","request_id":"phase4-conflict-start","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":2,"max_tool_calls":1,"max_output_tokens":1600,"feedback_review_calls":0})).json()
        await self.wait_free_status(base, started["id"], {"completed"})
        item = (await self.client.get(base + "/feedback-v4")).json()[0]
        self.assertEqual(item["status"], "ready")
        MockService.decision_responses = ["invalid JSON"]
        invalid = await self.client.post(base + f"/feedback-v4/{item['id']}/review", json={"schemaVersion":4,"requestId":"phase4-invalid-review","expectedVersion":item["version"],"maxOutputTokens":500})
        self.assertEqual(invalid.status_code, 200, invalid.text)
        self.assertEqual(invalid.json()["status"], "review_failed")
        self.assertEqual((await self.client.get(base)).json()["drives"][6]["activation"], 0)
        item = invalid.json()
        MockService.decision_responses = [json.dumps({"schemaVersion":4,"reviews":[{"feedbackId":item["id"],"fulfillment":0.8,"novelty":0.5,"confidence":0.8,"affectedDrives":[{"driveId":"exploration.curiosity","relevance":1,"reason":"查看了地图"}]}]})]
        valid = await self.client.post(base + f"/feedback-v4/{item['id']}/review", json={"schemaVersion":4,"requestId":"phase4-valid-review","expectedVersion":item["version"],"maxOutputTokens":500})
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["status"], "ready")
        item = valid.json()
        event = await self.client.post(base + "/events", json={"event_id":"phase4-concurrent-event","description":"另一个已确认事件","effects":[{"drive_id":"exploration.curiosity","activation_delta":0.2}]})
        self.assertEqual(event.status_code, 200, event.text)
        first = await self.client.post(base + f"/feedback-v4/{item['id']}/decision", json={"schemaVersion":4,"requestId":"phase4-stale-apply","expectedVersion":item["version"],"apply":True})
        self.assertEqual(first.status_code, 409, first.text)
        refreshed = (await self.client.get(base + f"/feedback-v4/{item['id']}")).json()
        self.assertGreater(refreshed["version"], item["version"])
        with app_module.connect() as db:
            before_crash = db.execute("SELECT satisfaction FROM drive_states WHERE character_id=? AND drive_id='exploration.curiosity'", (cid,)).fetchone()[0]
            db.execute("CREATE TRIGGER phase4_test_abort BEFORE INSERT ON drive_regulation_v4 BEGIN SELECT RAISE(ABORT,'simulated crash'); END")
        try:
            with self.assertRaises(Exception):
                await self.client.post(base + f"/feedback-v4/{item['id']}/decision", json={"schemaVersion":4,"requestId":"phase4-crash-apply","expectedVersion":refreshed["version"],"apply":True})
        finally:
            with app_module.connect() as db:
                db.execute("DROP TRIGGER phase4_test_abort")
                self.assertEqual(db.execute("SELECT satisfaction FROM drive_states WHERE character_id=? AND drive_id='exploration.curiosity'", (cid,)).fetchone()[0], before_crash)
                self.assertIsNone(db.execute("SELECT id FROM desire_events WHERE character_id=? AND event_id=?", (cid, f"phase4:{item['id']}")).fetchone())
                self.assertEqual(db.execute("SELECT status FROM feedback_activities_v4 WHERE id=?", (item["id"],)).fetchone()[0], "ready")
        final = await self.client.post(base + f"/feedback-v4/{item['id']}/decision", json={"schemaVersion":4,"requestId":"phase4-fresh-apply","expectedVersion":refreshed["version"],"apply":True})
        self.assertEqual(final.status_code, 200, final.text)
        self.assertEqual(final.json()["status"], "applied")

    async def test_phase4_regulation_scenarios_and_negative_surprise(self):
        from backend.app import feedback
        state = {"drive_id":"exploration.curiosity","activation":0.6,"satisfaction":0.4,"inhibition":0.1,"baseline":0.5}
        discovery = feedback.regulate(state, 1, 0.9, {"fulfillment":0.8,"novelty":1,"frustration":0.1}, 0)
        self.assertGreater(discovery["after"]["satisfaction"], state["satisfaction"])
        self.assertGreater(discovery["after"]["activation"], state["activation"])
        dull = feedback.regulate(state, 1, 0.9, {"fulfillment":0.1,"novelty":0,"frustration":0}, 0)
        self.assertLess(dull["after"]["satisfaction"], state["satisfaction"])
        self.assertEqual(state["baseline"], 0.5)
        blank = feedback.regulate(state, 1, 0.9, {"fulfillment":None,"novelty":None,"frustration":None}, 0)
        self.assertEqual(blank["satisfaction_delta"], 0)
        sample = {"expected_enjoyment":0.9,"review_json":json.dumps({"enjoyment":0.4}),"evidence_json":"[]","source_event_ids_json":"[]","proposal_json":"[]","drive_versions_json":"{}"}
        self.assertEqual(app_module.app.state.feedback_service.view(sample)["expectation_gap"], -0.5)

    async def test_phase3_free_environment_continuity_and_backup(self):
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        self.assertEqual((await self.client.put(base + "/enabled", json={"enabled": True})).status_code, 200)
        model = (await self.client.post("/api/models", json={"name":"free-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        decisions = [
            {"schemaVersion":3,"choice":"environment","goalTitle":"研究地图","reasonSummary":"想查看场景资料","actionId":"inspect:map"},
            {"schemaVersion":3,"choice":"environment","goalTitle":"研究地图","reasonSummary":"继续查看笔记","actionId":"inspect:notes"},
            {"schemaVersion":3,"choice":"environment","goalTitle":"研究地图","reasonSummary":"对照线索","actionId":"compare:map-notes"},
            {"schemaVersion":3,"choice":"finish","reasonSummary":"这次先到这里"},
        ]
        MockService.decision_responses = [json.dumps(item, ensure_ascii=False) for item in decisions]
        options = {"mode":"free","request_id":"phase3-start-001","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":4,"max_tool_calls":3,"max_output_tokens":3200,"allowed_tool_ids":[],"environment_id":"local-studio-v1"}
        created = await self.client.post(base + "/awakening", json=options)
        self.assertEqual(created.status_code, 200, created.text)
        sid = created.json()["id"]
        self.assertEqual((await self.client.post(base + "/awakening", json=options)).json()["id"], sid)
        self.assertEqual((await self.client.post(base + "/awakening", json={**options,"request_id":"phase3-start-002"})).status_code, 409)
        ended = await self.wait_free_status(base, sid, {"completed", "failed", "paused"})
        self.assertEqual(ended["status"], "completed", ended)
        self.assertEqual(ended["termination_reason"], "finish")
        self.assertEqual(ended["tool_calls"], 3)
        self.assertEqual(ended["output_tokens_used"], 3200)
        operations = (await self.client.get(base + f"/awakening/{sid}/operations")).json()
        self.assertEqual([item["status"] for item in operations], ["succeeded"] * 3)
        self.assertEqual(len((await self.client.get(base + "/activity-feedback")).json()), 3)
        scene = (await self.client.get(base + "/environments/local-studio-v1")).json()
        self.assertEqual(len(scene["discoveries"]), 3)
        self.assertTrue(all(item["inspected"] for item in scene["objects"][:2]))
        intentions = (await self.client.get(base + "/intentions")).json()
        self.assertEqual(len([item for item in intentions if item["title"] == "研究地图"]), 1)
        self.assertEqual(next(item for item in intentions if item["title"] == "研究地图")["status"], "active")
        MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":"no_action","reasonSummary":"今天想休息"}, ensure_ascii=False)]
        second = (await self.client.post(base + "/awakening", json={**options,"request_id":"phase3-start-003","max_model_calls":1,"max_tool_calls":0,"max_output_tokens":800})).json()
        second_end = await self.wait_free_status(base, second["id"], {"completed", "paused"})
        self.assertEqual(second_end["termination_reason"], "no_action")
        prior_context = MockService.last_payload["messages"][1]["content"]
        self.assertIn("previousExperiences", prior_context)
        self.assertIn("研究地图", prior_context)
        self.assertIn("岬角", prior_context)
        backup = await self.client.post("/api/system/backup/export", data={"passphrase":"phase3-backup-password"})
        self.assertEqual(backup.status_code, 200)
        restored = await self.client.post("/api/system/backup/import", data={"passphrase":"phase3-backup-password","confirm_replace":"true"}, files={"backup":("phase3.ybackup",backup.content,"application/octet-stream")})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username":"owner","password":"temporary-password-123"})).status_code, 200)
        self.assertEqual(len((await self.client.get(base + "/environments/local-studio-v1")).json()["discoveries"]), 3)

    async def test_phase3_tool_confirmation_restart_and_revision(self):
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled": True})
        model = (await self.client.post("/api/models", json={"name":"free-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        mcp = (await self.client.post("/api/mcp/servers", json={"name":"mock","endpoint":self.url+"/mcp"})).json()
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/connect")
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/refresh-tools")
        tool = (await self.client.get(f"/api/mcp/servers/{mcp['id']}/tools")).json()[0]
        await self.client.patch(f"/api/mcp/tools/{tool['id']}", json={"enabled":True})
        MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":"tool","goalTitle":"核对资料","toolId":tool["id"],"arguments":{"value":"ok"}}), json.dumps({"schemaVersion":3,"choice":"finish"})]
        options = {"mode":"free","request_id":"phase3-tool-start","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":2,"max_tool_calls":1,"max_output_tokens":1600,"allowed_tool_ids":[tool["id"]]}
        started = (await self.client.post(base + "/awakening", json=options)).json()
        sid = started["id"]
        pending = await self.wait_free_status(base, sid, {"awaiting_confirmation", "paused", "failed"})
        self.assertEqual(pending["status"], "awaiting_confirmation", pending)
        op = (await self.client.get(base + f"/awakening/{sid}/operations")).json()[0]
        self.assertEqual(op["status"], "pending_confirmation")
        self.assertEqual(MockService.calls.count("tools/call"), 0)
        wrong = {"schemaVersion":3,"requestId":"phase3-wrong-revision","expectedRevision":pending["revision"]-1,"approve":True}
        self.assertEqual((await self.client.post(base + f"/awakening/{sid}/operations/{op['id']}/decision", json=wrong)).status_code, 409)
        approval = {**wrong,"requestId":"phase3-approval","expectedRevision":pending["revision"]}
        self.assertEqual((await self.client.post(base + f"/awakening/{sid}/operations/{op['id']}/decision", json=approval)).status_code, 200)
        self.assertEqual((await self.client.post(base + f"/awakening/{sid}/operations/{op['id']}/decision", json=approval)).status_code, 200)
        ended = await self.wait_free_status(base, sid, {"completed", "paused", "failed"})
        self.assertEqual(ended["status"], "completed", ended)
        self.assertEqual(MockService.calls.count("tools/call"), 1)
        with app_module.connect() as db:
            db.execute("UPDATE activity_operations SET status='running' WHERE id=?", (op["id"],))
            db.execute("UPDATE awakening_sessions SET status='acting' WHERE id=?", (sid,))
        app_module.init_db()
        restored = (await self.client.get(base + f"/awakening/{sid}")).json()
        self.assertEqual(restored["status"], "paused")
        self.assertEqual((await self.client.get(base + f"/awakening/{sid}/operations")).json()[0]["status"], "unknown")
        self.assertEqual(MockService.calls.count("tools/call"), 1)
        control = {"schemaVersion":3,"requestId":"phase3-stop-restarted","expectedRevision":restored["revision"]}
        stopped = await self.client.post(base + f"/awakening/{sid}/stop", json=control)
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["termination_reason"], "early_stop")

    async def test_phase3_budget_model_failure_and_role_isolation(self):
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled":True})
        model = (await self.client.post("/api/models", json={"name":"free-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        options = {"mode":"free","request_id":"phase3-budget-start","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":1,"max_tool_calls":1,"max_output_tokens":800,"allowed_tool_ids":[]}
        MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":"environment","goalTitle":"随意看看","actionId":"inspect:map"}, ensure_ascii=False)]
        sid = (await self.client.post(base + "/awakening", json=options)).json()["id"]
        result = await self.wait_free_status(base, sid, {"completed", "paused", "failed"})
        self.assertEqual(result["termination_reason"], "budget_exhausted")
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual((await self.client.get(base + f"/awakening/{sid}/operations")).json()[0]["status"], "succeeded")
        self.assertEqual(len((await self.client.get(base + "/activity-feedback")).json()), 1)
        self.assertEqual(next(item for item in (await self.client.get(base + "/intentions")).json() if item["title"] == "随意看看")["status"], "active")
        MockService.decision_responses = ["not JSON"]
        bad = (await self.client.post(base + "/awakening", json={**options,"request_id":"phase3-invalid-model"})).json()
        bad_result = await self.wait_free_status(base, bad["id"], {"paused", "completed", "failed"})
        self.assertEqual(bad_result["status"], "paused")
        self.assertEqual(bad_result["termination_reason"], "model_error")
        self.assertEqual((await self.client.get(base + f"/awakening/{bad['id']}/operations")).json(), [])
        stop = {"schemaVersion":3,"requestId":"phase3-invalid-stop","expectedRevision":bad_result["revision"]}
        self.assertEqual((await self.client.post(base + f"/awakening/{bad['id']}/stop", json=stop)).status_code, 200)
        copy = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        self.assertFalse((await self.client.get(f"/api/desires/{copy['id']}")).json()["enabled"])
        self.assertEqual((await self.client.get(f"/api/desires/{copy['id']}/environments/local-studio-v1")).status_code, 409)
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        with app_module.connect() as db:
            for table in ("environment_states", "environment_discoveries", "activity_plans", "activity_operations", "activity_timeline", "pending_activity_feedback"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE character_id=?", (cid,)).fetchone()[0], 0)

    async def test_phase3_external_timeout_is_unknown_not_replayed(self):
        await self.setup_owner()
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        base = f"/api/desires/{cid}"
        await self.client.put(base + "/enabled", json={"enabled":True})
        model = (await self.client.post("/api/models", json={"name":"free-mock","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        mcp = (await self.client.post("/api/mcp/servers", json={"name":"mock","endpoint":self.url+"/mcp"})).json()
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/connect")
        await self.client.post(f"/api/mcp/servers/{mcp['id']}/refresh-tools")
        tool = (await self.client.get(f"/api/mcp/servers/{mcp['id']}/tools")).json()[0]
        await self.client.patch(f"/api/mcp/tools/{tool['id']}", json={"enabled":True})
        await self.client.put(base + f"/tools/{tool['id']}/policy", json={"acknowledged_read_only":True})
        MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":"tool","goalTitle":"核对","toolId":tool["id"],"arguments":{"value":"ok"}})]
        service = app_module.app.state.free_activity_service
        original = service.mcp_request
        async def lost_result(*_args, **_kwargs):
            raise httpx.ReadTimeout("connection lost after dispatch")
        service.mcp_request = lost_result
        try:
            options = {"mode":"free","request_id":"phase3-timeout-start","model_profile_id":model["id"],"duration_minutes":5,"max_model_calls":1,"max_tool_calls":1,"max_output_tokens":800,"allowed_tool_ids":[tool["id"]]}
            sid = (await self.client.post(base + "/awakening", json=options)).json()["id"]
            result = await self.wait_free_status(base, sid, {"paused", "completed", "failed"})
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["termination_reason"], "tool_result_unknown")
            self.assertEqual((await self.client.get(base + f"/awakening/{sid}/operations")).json()[0]["status"], "unknown")
            self.assertEqual((await self.client.get(base + "/activity-feedback")).json(), [])
            self.assertEqual(MockService.calls.count("tools/call"), 0)
            control = {"schemaVersion":3,"requestId":"phase3-timeout-stop","expectedRevision":result["revision"]}
            self.assertEqual((await self.client.post(base + f"/awakening/{sid}/stop", json=control)).status_code, 200)
            self.assertEqual(MockService.calls.count("tools/call"), 0)
            uncertain = (await self.client.get(base + "/feedback-v4")).json()
            self.assertEqual(len(uncertain), 1)
            self.assertEqual(uncertain[0]["objective_outcome"], "unknown")
            self.assertEqual(uncertain[0]["status"], "awaiting_evidence")
            self.assertEqual(uncertain[0]["proposal"], [])
            self.assertEqual((await self.client.post(base + f"/feedback-v4/{uncertain[0]['id']}/decision", json={"schemaVersion":4,"requestId":"phase4-unknown-apply","expectedVersion":uncertain[0]["version"],"apply":True})).status_code, 409)
            verified = await self.client.post(base + f"/feedback-v4/{uncertain[0]['id']}/resolve", json={"schemaVersion":4,"requestId":"phase4-unknown-resolve","expectedVersion":uncertain[0]["version"],"outcome":"failure","evidenceNote":"用户在外部核实，操作未完成"})
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json()["status"], "ready")
            self.assertEqual(verified.json()["evidence"][-1]["result"]["source"], "user_attestation")
        finally:
            service.mcp_request = original

    async def test_phase2_observation_and_pending_intentions(self):
        await self.setup_owner()
        character = (await self.client.get("/api/characters")).json()[0]
        cid = character["id"]
        base = f"/api/desires/{cid}"
        self.assertEqual((await self.client.put(base + "/enabled", json={"enabled": True})).status_code, 200)
        model = (await self.client.post("/api/models", json={"name": "phase2-mock", "base_url": self.url, "model_name": "mock", "api_key": "test-key"})).json()
        await self.client.post(base + "/events", json={"event_id": "phase2-event", "description": "兴趣与休息并存", "effects": [
            {"drive_id": "exploration.curiosity", "activation_delta": .9},
            {"drive_id": "physiological.rest", "activation_delta": .75}]})
        report = (await self.client.get(base + "/advisory")).json()
        self.assertEqual(report["schemaVersion"], 2)
        self.assertEqual(report["conflicts"][0]["kind"], "potential_tension")
        self.assertIn("phase2-event", next(x for x in report["tendencies"] if x["driveId"] == "exploration.curiosity")["evidenceEventIds"])
        before = (await self.client.get(base)).json()["drives"]
        MockService.decision_response = json.dumps({"schemaVersion": 2, "selfDescription": "", "observedDriveIds": [], "metaDesires": [], "unresolvedQuestions": []})
        observation_request = {"schemaVersion": 2, "requestId": "observe-001", "modelProfileId": model["id"], "mode": "passive"}
        observation = await self.client.post(base + "/observations", json=observation_request)
        self.assertEqual(observation.status_code, 200, observation.text)
        self.assertEqual(observation.json()["result"]["selfDescription"], "")
        self.assertEqual((await self.client.post(base + "/observations", json=observation_request)).json()["id"], observation.json()["id"])
        after = (await self.client.get(base)).json()["drives"]
        self.assertEqual([(x["drive_id"], x["satisfaction"]) for x in before], [(x["drive_id"], x["satisfaction"]) for x in after])
        self.assertEqual((await self.client.get(base + "/intentions")).json(), [])

        MockService.decision_response = "not json"
        decision_request = {"schemaVersion": 2, "requestId": "decision-bad", "modelProfileId": model["id"]}
        self.assertEqual((await self.client.post(base + "/decisions", json=decision_request)).status_code, 502)
        self.assertEqual((await self.client.get(base + "/intentions")).json(), [])
        MockService.decision_response = json.dumps({"schemaVersion": 2, "type": "create_goal", "proposedItem": {
            "title": "研究白石岬附近的旧地图", "source": "self_generated", "stage": "candidate", "relatedDriveIds": ["exploration.curiosity"]},
            "reasonSummary": "有兴趣，尚未决定", "selfNote": "以后可以再看看。"})
        create_request = {"schemaVersion": 2, "requestId": "decision-create", "modelProfileId": model["id"]}
        created = await self.client.post(base + "/decisions", json=create_request)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual((await self.client.post(base + "/decisions", json=create_request)).json()["id"], created.json()["id"])
        candidate = (await self.client.get(base + "/intentions")).json()[0]
        self.assertEqual(candidate["stage"], "candidate")
        self.assertEqual(len((await self.client.get(base + "/intentions")).json()), 1)

        MockService.decision_response = json.dumps({"schemaVersion": 2, "type": "defer_goal", "targetItemId": candidate["id"],
            "reasonSummary": "现在时间不足", "selfNote": "下次有空再看看。"})
        deferred = await self.client.post(base + "/decisions", json={"schemaVersion": 2, "requestId": "decision-defer", "modelProfileId": model["id"], "targetItemId": candidate["id"]})
        self.assertEqual(deferred.status_code, 200, deferred.text)
        pending = (await self.client.get(base + "/intentions")).json()[0]
        self.assertEqual(pending["stage"], "pending")
        with app_module.connect() as db:
            db.execute("UPDATE volition_items SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (pending["id"],))
        self.assertEqual((await self.client.get(base + "/intentions")).json()[0]["stage"], "pending")
        for kind in ("no_action", "undecided"):
            MockService.decision_response = json.dumps({"schemaVersion": 2, "type": kind, "reasonSummary": kind})
            self.assertEqual((await self.client.post(base + "/decisions", json={"schemaVersion": 2, "requestId": "decision-" + kind, "modelProfileId": model["id"]})).status_code, 200)
        self.assertEqual((await self.client.get(base + "/intentions")).json()[0]["stage"], "pending")
        MockService.decision_response = json.dumps({"schemaVersion": 2, "type": "revise_goal", "targetItemId": pending["id"],
            "proposedItem": {"title": pending["title"], "stage": "committed", "source": "self_generated", "relatedDriveIds": ["exploration.curiosity"]},
            "reasonSummary": "决定长期研究"})
        self.assertEqual((await self.client.post(base + "/decisions", json={"schemaVersion": 2, "requestId": "decision-commit", "modelProfileId": model["id"], "targetItemId": pending["id"]})).status_code, 200)
        committed = (await self.client.get(base + "/intentions")).json()[0]
        self.assertEqual(committed["stage"], "committed")
        self.assertEqual((await self.client.post(base + f"/intentions/{committed['id']}/transition", json={"schemaVersion": 2, "requestId": "archive-001", "expectedVersion": pending["version"], "action": "archive"})).status_code, 409)
        archived = (await self.client.post(base + f"/intentions/{committed['id']}/transition", json={"schemaVersion": 2, "requestId": "archive-002", "expectedVersion": committed["version"], "action": "archive"})).json()
        self.assertEqual(archived["status"], "archived")
        self.assertEqual((await self.client.post(base + f"/intentions/{committed['id']}/transition", json={"schemaVersion": 2, "requestId": "archive-002", "expectedVersion": committed["version"], "action": "archive"})).json()["version"], archived["version"])
        reopened = (await self.client.post(base + f"/intentions/{committed['id']}/transition", json={"schemaVersion": 2, "requestId": "restore-001", "expectedVersion": archived["version"], "action": "reconsider"})).json()
        self.assertEqual(reopened["stage"], "candidate")
        self.assertEqual(reopened["status"], "active")
        MockService.decision_response = json.dumps({"schemaVersion": 2, "type": "choose_goal", "targetItemId": "does-not-exist", "reasonSummary": "错误引用"})
        self.assertEqual((await self.client.post(base + "/decisions", json={"schemaVersion": 2, "requestId": "decision-invalid-id", "modelProfileId": model["id"]})).status_code, 404)
        self.assertEqual(len((await self.client.get(base + "/intentions")).json()), 1)
        MockService.decision_response = json.dumps({"decision": "defer", "goal": "", "targetItemId": reopened["id"], "reasonSummary": "稍后再做", "relatedDrives": ["exploration.curiosity"]})
        session = (await self.client.post(base + "/awakening", json={"model_profile_id": model["id"], "duration_minutes": 5, "max_model_calls": 1, "max_tool_calls": 0, "allowed_tool_ids": []})).json()
        self.assertEqual((await self.client.post(base + f"/awakening/{session['id']}/step", json={})).status_code, 200)
        self.assertEqual((await self.client.get(base + "/intentions")).json()[0]["stage"], "pending")
        self.assertEqual(len((await self.client.get(base + "/intentions")).json()), 1)
        duplicate = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        self.assertFalse((await self.client.get(f"/api/desires/{duplicate['id']}")).json()["enabled"])
        duplicate_base = f"/api/desires/{duplicate['id']}"
        await self.client.put(duplicate_base + "/enabled", json={"enabled": True})
        self.assertEqual((await self.client.get(duplicate_base + "/intentions")).json(), [])
        self.assertEqual((await self.client.post(duplicate_base + f"/intentions/{reopened['id']}/transition", json={"schemaVersion": 2, "requestId": "cross-role-001", "expectedVersion": reopened["version"], "action": "archive"})).status_code, 404)
        backup = await self.client.post("/api/system/backup/export", data={"passphrase": "phase2-password-123"})
        self.assertEqual(backup.status_code, 200)
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM volition_items WHERE character_id=?", (cid,)).fetchone()[0], 0)
        restored = await self.client.post("/api/system/backup/import", data={"passphrase": "phase2-password-123", "confirm_replace": "true"}, files={"backup": ("phase2.ybackup", backup.content, "application/octet-stream")})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username": "owner", "password": "temporary-password-123"})).status_code, 200)
        self.assertEqual((await self.client.get(base + "/intentions")).json()[0]["stage"], "pending")

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
        import asyncio
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
            self.assertEqual((await self.client.put(f"/api/desires/{character_id}/enabled",json={"enabled":True})).status_code,200)
            MockService.plan_text=json.dumps({"schemaVersion":2,"selfDescription":"先观察","observedDriveIds":[],"metaDesires":[],"unresolvedQuestions":[]})
            observed=await self.client.post(f"/api/desires/{character_id}/observations",json={"schemaVersion":2,"requestId":"plan-observation-001","modelProfileId":model["id"],"mode":"passive"})
            self.assertEqual(observed.status_code,200,observed.text)
            self.assertTrue(MockService.last_payload["stream"])
            self.assertTrue(MockService.last_payload["text"]["format"]["strict"])
            self.assertEqual(MockService.last_payload["max_output_tokens"],model["max_tokens"])
            MockService.plan_text=json.dumps({"schemaVersion":2,"type":"no_action","reasonSummary":"目前先不行动","selfNote":"可以稍后再看"})
            decided=await self.client.post(f"/api/desires/{character_id}/decisions",json={"schemaVersion":2,"requestId":"plan-decision-001","modelProfileId":model["id"]})
            self.assertEqual(decided.status_code,200,decided.text)
            MockService.plan_text=json.dumps({"schemaVersion":3,"choice":"no_action","reasonSummary":"暂时没有要做的事"})
            started=await self.client.post(f"/api/desires/{character_id}/awakening",json={"mode":"free","request_id":"plan-free-session-001","model_profile_id":model["id"],"environment_id":"local-studio-v1","duration_minutes":1,"max_model_calls":1,"max_tool_calls":0,"max_output_tokens":800,"allowed_tool_ids":[],"feedback_review_calls":0,"psychology_appraisal_calls":0})
            self.assertEqual(started.status_code,200,started.text)
            for _ in range(50):
                session=(await self.client.get(f"/api/desires/{character_id}/awakening/{started.json()['id']}")).json()
                if session["status"] in ("completed","cancelled","failed","paused"):
                    break
                await asyncio.sleep(.1)
            self.assertEqual(session["status"],"completed",session)
            self.assertTrue(MockService.last_payload["stream"])
            self.assertEqual(MockService.last_payload["text"]["format"]["schema"]["properties"]["targetItemId"],{"type":"null"})
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
