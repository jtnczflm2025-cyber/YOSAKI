"""Isolated Phase 6 API and rule acceptance; no project database is imported."""
import json
import os
import random
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
from test_local_acceptance import MockService, app_module


class PsychologyAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-psychology-")
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
        app_module.app.state.psychology_service.resume_after_restore()

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def owner(self):
        self.assertEqual((await self.client.post("/api/auth/setup", json={"username": "owner", "password": "temporary-password-123"})).status_code, 201)
        cid = (await self.client.get("/api/characters")).json()[0]["id"]
        await self.client.put(f"/api/desires/{cid}/enabled", json={"enabled": True})
        return cid, f"/api/desires/{cid}"

    async def test_state_idempotence_conflict_graph_and_long_simulation(self):
        from backend.app import psychology
        cid, base = await self.owner()
        service = app_module.app.state.psychology_service
        with app_module.connect() as db:
            before = service.snapshot(db, cid)
            revision_before_empty = before["revision"]
            service.record_reflections(db, cid, "observation:empty", [])
            self.assertEqual(service.snapshot(db, cid)["revision"], revision_before_empty)
            original_drive = tuple(db.execute("SELECT baseline,activation FROM drive_states WHERE character_id=? AND drive_id='exploration.curiosity'", (cid,)).fetchone())
            event = {"status": "succeeded", "kind": "environment", "activityType": "research"}
            self.assertTrue(service.process_event(db, cid, "operation:phase6-1", "operation", "phase6-1", event))
            self.assertFalse(service.process_event(db, cid, "operation:phase6-1", "operation", "phase6-1", event))
            state = service.snapshot(db, cid, project=False)
            self.assertLess(state["body"]["energy"], before["body"]["energy"])
            self.assertEqual(tuple(db.execute("SELECT baseline,activation FROM drive_states WHERE character_id=? AND drive_id='exploration.curiosity'", (cid,)).fetchone()), original_drive)
            service.record_reflections(db, cid, "observation:test", [
                {"targetType": "drive", "targetId": "exploration.curiosity", "attitude": "endorse", "strength": .6, "confidence": .6, "reasonSummary": "想继续"},
                {"targetType": "drive", "targetId": "exploration.curiosity", "attitude": "resist", "strength": .6, "confidence": .6, "reasonSummary": "也想停下"}])
            state = service.snapshot(db, cid, project=False)
            self.assertEqual(len(state["secondOrderDesires"]), 2)
            self.assertTrue(any(x["id"].startswith("reflection:") for x in state["conflicts"]))
        desire = state["secondOrderDesires"][0]
        action = {"schemaVersion": 6, "requestId": "phase6-archive-reflection", "expectedVersion": 1, "action": "archive"}
        response = await self.client.post(base + f"/psychology/second-order/{desire['id']}/transition", json=action)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.client.post(base + f"/psychology/second-order/{desire['id']}/transition", json=action)).status_code, 200)
        edge = await self.client.post(base + "/psychology/edges", json={"schemaVersion": 6, "requestId": "phase6-edge-propose", "sourceType": "emotion", "sourceId": "tension", "targetType": "body_state", "targetId": "cognitiveLoad", "relationType": "amplify", "weight": .2, "confidence": .5, "reason": "观察有界关联"})
        self.assertEqual(edge.status_code, 200, edge.text)
        self.assertEqual(edge.json()["enabled"], 0)
        self.assertEqual((await self.client.post(base + "/psychology/edges", json={"schemaVersion": 6, "requestId": "phase6-edge-propose", "sourceType": "emotion", "sourceId": "tension", "targetType": "body_state", "targetId": "cognitiveLoad", "relationType": "amplify", "weight": .3, "confidence": .5, "reason": "观察有界关联"})).status_code, 409)
        self.assertEqual((await self.client.post(base + "/psychology/edges", json={"schemaVersion": 6, "requestId": "phase6-inert-edge", "sourceType": "goal", "sourceId": "anything", "targetType": "emotion", "targetId": "tension", "relationType": "amplify", "weight": .2, "confidence": .5, "reason": "不可执行节点"})).status_code, 422)
        edge_id = edge.json()["id"]
        approved = await self.client.post(base + f"/psychology/edges/{edge_id}/decision", json={"schemaVersion": 6, "requestId": "phase6-edge-approve", "expectedVersion": 1, "action": "approve"})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual((await self.client.post(base + f"/psychology/edges/{edge_id}/decision", json={"schemaVersion": 6, "requestId": "phase6-edge-approve", "expectedVersion": 1, "action": "reject"})).status_code, 409)
        self.assertEqual((await self.client.post(base + f"/psychology/edges/{edge_id}/decision", json={"schemaVersion": 6, "requestId": "phase6-stale-edge", "expectedVersion": 1, "action": "disable"})).status_code, 409)
        with app_module.connect() as db:
            for index in range(100):
                service.process_event(db, cid, f"cycle:{index}", "feedback", f"feedback:{index}", {"objectiveOutcome": "failure", "review": {"enjoyment": .1, "frustration": 1, "confidence": .9}, "driveChanges": {"exploration.curiosity": .1}})
            state = service.snapshot(db, cid, project=False)
            self.assertTrue(all(0 <= x <= 1 for x in state["body"].values()))
            self.assertTrue(-1 <= state["emotion"]["valence"] <= 1)
            self.assertTrue(all(abs(x) <= .1 for x in state["driveHints"].values()))
            history = db.execute("SELECT before_json,after_json,rule_version FROM psychological_events WHERE event_id='cycle:0'").fetchone()
            self.assertEqual(history["rule_version"], psychology.RULE_VERSION)
            self.assertNotEqual(history["before_json"], history["after_json"])
        tick = await self.client.post(base + "/psychology/tick", json={"schemaVersion": 6, "requestId": "phase6-time-advance", "expectedRevision": state["revision"]})
        self.assertEqual(tick.status_code, 200, tick.text)
        replay = await self.client.get(base + "/psychology/replay")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["consistent"])
        rng = random.Random(6026)
        for count in (100, 500):
            events = [{"kind": "operation", "status": "succeeded", "activityType": "rest" if i % 7 == 0 else "research"} if i % 3 else {"kind": "feedback", "enjoyment": rng.random()} for i in range(count)]
            result = await self.client.post(base + "/psychology/simulate", json={"schemaVersion": 6, "events": events})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(result.json()["stateChanged"])
            self.assertEqual(result.json()["trajectory"][-1]["step"], count)
            for point in result.json()["trajectory"]:
                self.assertTrue(all(0 <= x <= 1 for x in point["body"].values()))
                self.assertTrue(-1 <= point["emotion"]["valence"] <= 1)
            rested = await self.client.post(base + "/psychology/simulate", json={"schemaVersion": 6, "events": [*events[:-1], {"kind": "tick", "hours": 72}]})
            self.assertEqual(rested.status_code, 200, rested.text)
            self.assertGreater(rested.json()["trajectory"][-1]["body"]["energy"], .55)
            self.assertLess(rested.json()["trajectory"][-1]["body"]["fatigue"], .5)
        self.assertEqual((await self.client.get(base + "/psychology")).json()["revision"], tick.json()["revision"])

    async def test_observation_appraisal_failure_backup_and_role_isolation(self):
        cid, base = await self.owner()
        model = (await self.client.post("/api/models", json={"name": "psychology-mock", "base_url": self.url, "model_name": "mock", "api_key": "test-key"})).json()
        MockService.decision_response = json.dumps({"schemaVersion": 2, "selfDescription": "既想继续，也想停下", "observedDriveIds": ["exploration.curiosity"], "metaDesires": [{"targetDriveId": "exploration.curiosity", "stance": "accept", "explanation": "想探索"}, {"targetDriveId": "exploration.curiosity", "stance": "restrain", "explanation": "也想休息"}], "unresolvedQuestions": []})
        observed = await self.client.post(base + "/observations", json={"schemaVersion": 2, "requestId": "phase6-observation", "modelProfileId": model["id"], "mode": "reflective"})
        self.assertEqual(observed.status_code, 200, observed.text)
        self.assertTrue((await self.client.get(base + "/psychology")).json()["conflicts"])
        with app_module.connect() as db:
            session = "phase6-appraisal-session"
            db.execute("INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,ended_at,max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json,mode,psychology_appraisal_limit,psychology_appraisal_tokens) VALUES(?,?,?,'completed',?,?,?,?,0,'[]','{}','free',1,500)", (session, cid, model["id"], app_module.now(), app_module.now(), app_module.now(), 1))
            db.execute("INSERT INTO feedback_activities_v4(id,character_id,session_id,schema_version,title,objective_outcome,end_reason,evidence_json,source_event_ids_json,expected_enjoyment,review_json,status,created_at,updated_at,applied_at) VALUES(?,?,?,4,'研究','partial','finish','[]','[]',.7,?,'applied',?,?,?)", ("phase6-feedback", cid, session, json.dumps({"enjoyment": .9, "frustration": .1, "confidence": .8}), app_module.now(), app_module.now(), app_module.now()))
            db.execute("INSERT INTO psychological_appraisal_runs VALUES(?,?,?,?,0,'pending',NULL,NULL,?,?)", (session, cid, model["id"], 500, app_module.now(), app_module.now()))
        MockService.decision_response = "invalid json"
        await app_module.app.state.psychology_service.run_appraisal(session)
        runs = (await self.client.get(base + "/psychology/appraisals")).json()
        self.assertEqual(runs[0]["status"], "failed")
        self.assertGreater((await self.client.get(base + "/psychology")).json()["emotion"]["valence"], 0)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM psychological_events WHERE event_id='feedback:phase6-feedback'").fetchone()[0], 1)
        backup = await self.client.post("/api/system/backup/export", data={"passphrase": "phase6-backup-password"})
        self.assertEqual(backup.status_code, 200)
        restored = await self.client.post("/api/system/backup/import", data={"passphrase": "phase6-backup-password", "confirm_replace": "true"}, files={"backup": ("phase6.ybackup", backup.content, "application/octet-stream")})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual((await self.client.post("/api/auth/login", json={"username": "owner", "password": "temporary-password-123"})).status_code, 200)
        self.assertEqual(len((await self.client.get(base + "/psychology")).json()["secondOrderDesires"]), 2)
        copy = (await self.client.post(f"/api/characters/{cid}/duplicate")).json()
        await self.client.put(f"/api/desires/{copy['id']}/enabled", json={"enabled": True})
        self.assertEqual((await self.client.get(f"/api/desires/{copy['id']}/psychology")).json()["secondOrderDesires"], [])
        self.assertEqual((await self.client.delete(f"/api/characters/{cid}")).status_code, 204)
        with app_module.connect() as db:
            for table in ("psychological_snapshots", "psychological_events", "second_order_desires", "psychological_edges", "psychological_appraisal_runs"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE character_id=?", (cid,)).fetchone()[0], 0, table)

    async def test_valid_appraisal_is_bounded_and_budgeted(self):
        cid, base = await self.owner()
        model = (await self.client.post("/api/models", json={"name": "psychology-valid", "base_url": self.url, "model_name": "mock", "api_key": "test-key"})).json()
        with app_module.connect() as db:
            service = app_module.app.state.psychology_service
            service.seed(db, cid)
            stamp = app_module.now()
            db.execute("INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,ended_at,max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json,mode,psychology_appraisal_limit,psychology_appraisal_tokens) VALUES(?,?,?,'completed',?,?,?,?,0,'[]','{}','free',1,500)", ("phase6-valid-session", cid, model["id"], stamp, stamp, stamp, 1))
            db.execute("INSERT INTO feedback_activities_v4(id,character_id,session_id,schema_version,title,objective_outcome,end_reason,evidence_json,source_event_ids_json,expected_enjoyment,review_json,status,created_at,updated_at,applied_at) VALUES(?,?,?,4,'看地图','partial','finish','[]','[]',.7,?,'applied',?,?,?)", ("phase6-valid-feedback", cid, "phase6-valid-session", json.dumps({"enjoyment": .9, "novelty": .8, "confidence": .8}), stamp, stamp, stamp))
            db.execute("INSERT INTO psychological_appraisal_runs VALUES(?,?,?,?,0,'pending',NULL,NULL,?,?)", ("phase6-valid-session", cid, model["id"], 500, stamp, stamp))
        MockService.decision_response = json.dumps({"schemaVersion": 6, "appraisals": [{"feedbackId": "phase6-valid-feedback", "novelty": .8, "goalRelevance": None, "expectationMismatch": .2, "perceivedControllability": .7, "perceivedProgress": .4, "autonomyAlignment": None, "socialSignificance": None, "confidence": .8, "evidenceSummary": "体验评价来自已返回结果"}]})
        service = app_module.app.state.psychology_service
        await service.run_appraisal("phase6-valid-session")
        with app_module.connect() as db:
            run = db.execute("SELECT * FROM psychological_appraisal_runs WHERE session_id='phase6-valid-session'").fetchone()
            self.assertEqual(run["status"], "completed")
            self.assertLessEqual(run["token_used"], 500)
            event = db.execute("SELECT appraisal_json FROM psychological_events WHERE event_id='feedback:phase6-valid-feedback'").fetchone()
            self.assertAlmostEqual(json.loads(event[0])["expectationMismatch"], .2)
        await service.run_appraisal("phase6-valid-session")
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM psychological_events WHERE event_id='feedback:phase6-valid-feedback'").fetchone()[0], 1)
        context = (await self.client.get(base + "/psychology/context")).json()
        self.assertTrue(context["simulated"])
        self.assertIn("emotion", context)
