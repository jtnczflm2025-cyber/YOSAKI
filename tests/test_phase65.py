"""Phase 6.5 regression checks use only the temporary database from Phase 6 fixtures."""
import json
import os
import unittest
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

from fastapi import HTTPException

from test_local_acceptance import app_module, MockService


class BehaviorAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-phase65-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockService)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        MockService.decision_response = None
        MockService.decision_responses = []
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        app_module.app.state.feedback_service.resume_after_restore()
        app_module.app.state.psychology_service.resume_after_restore()

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def owner(self):
        self.assertEqual((await self.client.post("/api/auth/setup", json={"username":"owner","password":"temporary-password-123"})).status_code, 201)
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        await self.client.put(f"/api/desires/{cid}/enabled", json={"enabled":True})
        return cid, f"/api/desires/{cid}"

    async def wait_free_status(self, base, session_id, expected):
        import asyncio
        for _ in range(80):
            response = await self.client.get(base + f"/awakening/{session_id}")
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()
            if item["status"] in expected: return item
            await asyncio.sleep(.05)
        self.fail("自由活动未在时限内达到目标状态")

    async def test_thirty_scripted_days_use_real_free_worker_and_bounded_state(self):
        cid, base = await self.owner()
        profile = (await self.client.post("/api/models", json={"name":"phase65-script","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        service = app_module.app.state.free_activity_service
        psychology = app_module.app.state.psychology_service
        original_now = service.now
        start = datetime.now(timezone.utc) - timedelta(days=29)
        clock = [start]
        service.now = lambda: clock[0].isoformat(timespec="seconds")
        with app_module.connect() as db:
            psychology.seed(db, cid)
            db.execute("UPDATE psychological_snapshots SET updated_at=? WHERE character_id=?", (start.isoformat(timespec="seconds"), cid))
        try:
            for day in range(30):
                clock[0] = start + timedelta(days=day)
                action = ["inspect:map", "inspect:notes", "compare:map-notes"][day] if day < 3 else None
                choice = "environment" if action else "rest" if day % 3 == 0 else "no_action"
                MockService.decision_responses = [json.dumps({"schemaVersion":3,"choice":choice,"goalTitle":"研究地图" if action else "", "actionId":action}, ensure_ascii=False)]
                request = {"mode":"free","request_id":f"phase65-day-{day:02d}","model_profile_id":profile["id"],"duration_minutes":5,
                           "max_model_calls":1,"max_tool_calls":1 if action else 0,"max_output_tokens":800,
                           "feedback_review_calls":0,"psychology_appraisal_calls":0}
                started = await self.client.post(base + "/awakening", json=request)
                self.assertEqual(started.status_code, 200, started.text)
                ended = await self.wait_free_status(base, started.json()["id"], {"completed", "paused", "failed"})
                self.assertEqual(ended["status"], "completed", ended)
                with app_module.connect() as db:
                    psychology.process_event(db, cid, f"phase65:idle:{day}", "tick", f"day:{day}", {}, at=(clock[0] + timedelta(hours=12)).isoformat())
            feedback_items = (await self.client.get(base + "/feedback-v4")).json()
            self.assertEqual(len(feedback_items), 3)
            last_context = json.loads(MockService.last_payload["messages"][1]["content"])
            self.assertIn("advisory", last_context)
            self.assertIn("psychologicalReference", last_context)
            self.assertIn("longTermLearning", last_context)
            first = feedback_items[0]
            MockService.decision_responses = [json.dumps({"schemaVersion":4,"reviews":[{"feedbackId":first["id"],"enjoyment":.8,"fulfillment":.7,
                "novelty":.7,"confidence":.8,"affectedDrives":[{"driveId":"exploration.curiosity","relevance":.7,"reason":"场景发现"}]}]})]
            reviewed = await self.client.post(base + f"/feedback-v4/{first['id']}/review", json={"schemaVersion":4,"requestId":"phase65-manual-review","expectedVersion":first["version"],"maxOutputTokens":600})
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            applied = await self.client.post(base + f"/feedback-v4/{first['id']}/decision", json={"schemaVersion":4,"requestId":"phase65-confirm-feedback","expectedVersion":reviewed.json()["version"],"apply":True})
            self.assertEqual(applied.status_code, 200, applied.text)
            learning = (await self.client.get(base + "/learning")).json()
            self.assertGreaterEqual(len(learning["evidence"]), 1)
            with app_module.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM awakening_sessions WHERE character_id=? AND mode='free'", (cid,)).fetchone()[0], 30)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM activity_operations WHERE character_id=? AND status='succeeded'", (cid,)).fetchone()[0], 3)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM behavior_resource_samples WHERE character_id=?", (cid,)).fetchone()[0], 30)
                sampled = db.execute("SELECT row_counts_json FROM behavior_resource_samples WHERE character_id=? AND session_id=?", (cid, first["session_id"])).fetchone()
                self.assertIsNotNone(sampled)
                self.assertGreaterEqual(json.loads(sampled[0])["learning_evidence"], 1)
                self.assertGreaterEqual(json.loads(sampled[0])["feedback_activities_v4"], 3)
                snapshot = psychology.snapshot(db, cid, project=False)
                self.assertTrue(all(0 <= value <= 1 for value in snapshot["body"].values()))
                self.assertTrue(all(abs(value) <= .1 for value in snapshot["driveHints"].values()))
                self.assertEqual(psychology.collect(db, cid), 0)
            recent = (await self.client.get(base + "/behavior/report", params={"days":31})).json()
            self.assertEqual(len(recent["sessions"]), 30)
            self.assertGreaterEqual(sum(len(x["sessionIds"]) for x in recent["daily"]), 30)
        finally:
            service.now = original_now
    async def test_operation_completion_is_not_goal_progress(self):
        from backend.app import psychology
        cid, _ = await self.owner()
        for status in ("succeeded", "failed"):
            self.assertIsNone(psychology.objective_appraisal("operation", {"status": status, "kind": "environment"})["perceivedProgress"])
        service = app_module.app.state.psychology_service
        with app_module.connect() as db:
            self.assertTrue(service.process_event(db, cid, "operation:phase65", "operation", "phase65", {"status": "succeeded", "kind": "environment"}))
            self.assertFalse(service.process_event(db, cid, "operation:phase65", "operation", "phase65", {"status": "succeeded", "kind": "environment"}))
            row = db.execute("SELECT appraisal_json,rule_version FROM psychological_events WHERE character_id=? AND event_id=?", (cid, "operation:phase65")).fetchone()
            self.assertIsNone(json.loads(row["appraisal_json"])["perceivedProgress"])
            self.assertEqual(row["rule_version"], psychology.RULE_VERSION)

    async def test_graph_capacity_and_cross_entry_operation_deduplication(self):
        from backend.app import psychology
        cid, _ = await self.owner()
        state = psychology.neutral()
        edges = [{"source_type":"body_state","source_id":"fatigue","target_type":"emotion","target_id":"tension","relation_type":"amplify","weight":.3}] * 41
        with self.assertRaises(ValueError): psychology.graph_effects_for_edges(edges, state, psychology.neutral())
        profile = (await self.client.post("/api/models", json={"name":"phase65-dedup","base_url":self.url,"model_name":"mock","api_key":"test-key"})).json()
        with app_module.connect() as db:
            service = app_module.app.state.psychology_service
            stamp = app_module.now()
            db.execute("INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json,mode,environment_id) VALUES(?,?,?,'completed',?,?,1,1,'[]','{}','free','local-studio-v1')",
                       ("phase65-session", cid, profile["id"], stamp, stamp))
            db.execute("INSERT INTO activity_operations(id,character_id,session_id,kind,action_id,arguments_json,status,created_at,updated_at) VALUES(?,?,?,'environment','inspect:map','{}','succeeded',?,?)",
                       ("phase65-operation", cid, "phase65-session", stamp, stamp))
            self.assertTrue(service.process_operation(db, cid, "phase65-operation"))
            revision = service.snapshot(db, cid, project=False)["revision"]
            self.assertFalse(service.process_operation(db, cid, "phase65-operation"))
            self.assertFalse(service.process_event(db, cid, "operation:phase65-operation", "operation", "phase65-operation", {"status":"succeeded","kind":"environment"}))
            self.assertEqual(service.snapshot(db, cid, project=False)["revision"], revision)

    async def test_accepted_learning_memory_is_not_injected_twice(self):
        cid, base = await self.owner()
        with app_module.connect() as db:
            stamp = app_module.now()
            memory_id = db.execute("INSERT INTO memories(character_id,conversation_id,scope,content,enabled,created_at,updated_at) VALUES(?,NULL,'long',?,1,?,?)",
                                   (cid, "白石岬的地图线索", stamp, stamp)).lastrowid
            db.execute("INSERT INTO learning_memories(id,character_id,memory_id,subject,fact,interpretation,content,source_ids_json,confidence,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'accepted',1,?,?)",
                       ("phase65-memory", cid, memory_id, "地图", "白石岬的地图线索", "有趣", "白石岬的地图线索", "[]", .7, stamp, stamp))
            context = app_module.app.state.learning_context(db, cid, "地图", {memory_id})
            self.assertEqual(context["memories"], [])
        response = await self.client.get(base + "/behavior/context-preview", params={"topic": "地图"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["duplicateSourceIds"], [])
        self.assertEqual([x["sourceId"] for x in response.json()["entries"] if x["sourceId"].startswith(("memory:", "learning:"))], [f"memory:{memory_id}"])

    async def test_daily_cap_and_private_report(self):
        cid, base = await self.owner()
        prior = os.environ.get("YOSAKI_OBSERVATION_MODE")
        os.environ["YOSAKI_OBSERVATION_MODE"] = "1"
        async def fake(): return ("{}", 12)
        try:
            for index in range(4):
                await app_module.app.state.behavior_model_call(cid, None, f"phase65:{index}", "test", 100, fake)
            with self.assertRaises(HTTPException) as blocked:
                await app_module.app.state.behavior_model_call(cid, None, "phase65:blocked", "test", 100, fake)
            self.assertEqual(blocked.exception.status_code, 429)
        finally:
            if prior is None: os.environ.pop("YOSAKI_OBSERVATION_MODE", None)
            else: os.environ["YOSAKI_OBSERVATION_MODE"] = prior
        report = await self.client.get(base + "/behavior/report")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(len(report.json()["modelCalls"]), 4)
        self.assertEqual(report.json()["metrics"]["invalidModelOutput"]["denominator"], 4)
        exported = await self.client.get(base + "/behavior/export", params={"format": "csv"})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertNotIn("temporary-password", exported.text)
