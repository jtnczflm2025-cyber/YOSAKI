"""Memory V2 M1 acceptance. The application is imported only with a temp data dir."""
import os
import base64
import tempfile
import unittest
from pathlib import Path
import httpx

_import_data = tempfile.TemporaryDirectory(prefix="yosaki-mv2-import-")
os.environ["YOSAKI_DATA_DIR"] = _import_data.name
from backend.app import main as app_module  # noqa: E402
from backend.app import memory_v2_sources as sources  # noqa: E402
from backend.app import memory_v2_index as indexer  # noqa: E402
from backend.app import backup_v2  # noqa: E402


class MemoryV2Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-mv2-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        self.assertEqual(app_module.DB.parent, root)
        with app_module.connect() as db:
            self.character_id = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]

    def test_m11_schema_idempotent_and_role_cascade(self):
        app_module.init_db()
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM mv2_meta WHERE character_id=?", (self.character_id,)).fetchone()[0], "needs_rebuild")
            other = db.execute("""INSERT INTO characters(name,created_at,updated_at)
                VALUES('synthetic','2026-01-01','2026-01-01')""").lastrowid
        app_module.init_db()
        with app_module.connect() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM mv2_meta WHERE character_id=?", (other,)).fetchone())
            db.execute("DELETE FROM characters WHERE id=?", (other,))
            self.assertIsNone(db.execute("SELECT 1 FROM mv2_meta WHERE character_id=?", (other,)).fetchone())

    def test_m11_source_safety_and_time(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01','2026-01-01')""", (self.character_id,)).lastrowid
            user = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','阿夜 美咲','2026-01-01')", (conv,)).lastrowid
            assistant = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at,reasoning_content,monologue_content,provider_state_json)
                VALUES(?,'assistant','可见正文','2026-01-01','PRIVATE-REASON','PRIVATE-MONOLOGUE','PRIVATE-OPAQUE')""", (conv,)).lastrowid
            system = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'system','PRIVATE-SYSTEM','2026-01-01')", (conv,)).lastrowid
            imported = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at,generation_status)
                VALUES(?,'user','历史正文','2026-01-02','imported')""", (conv,)).lastrowid
            a, b, c, d = (sources.source(db,"message",str(x)) for x in (user,assistant,system,imported))
        self.assertEqual(a.occurred_at,"2026-01-01")
        self.assertEqual(b.text,"可见正文")
        self.assertNotIn("PRIVATE", b.text)
        self.assertIsNone(c)
        self.assertIsNone(d.occurred_at)
        self.assertIsNone(d.recorded_at)
        self.assertIn("阿夜", sources.terms(a.text))
        self.assertIn("美咲", sources.terms(a.text))

    def _finish(self, job_id):
        for _ in range(30):
            with app_module.connect() as db:
                job = indexer.step_job(db, job_id)
            if job["status"] == "completed":
                return job
        self.fail("M1 index job did not finish")

    def test_m12_batch_resume_idempotence_and_invalidation(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')""",
                (self.character_id,)).lastrowid
            ids = [db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user',?,'2026-01-01T00:00:00+00:00')""",
                (conv, "阿夜 美咲" if i == 0 else "普通正文" + str(i))).lastrowid for i in range(105)]
            db.execute("""INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,
                max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json)
                VALUES('mv2-session',?,0,'completed','2026-01-01T00:00:00+00:00','2026-01-01T01:00:00+00:00',0,0,'[]','{}')""",
                (self.character_id,))
            db.execute("""INSERT INTO activity_operations(id,character_id,session_id,kind,action_id,arguments_json,
                status,result_json,created_at,updated_at)
                VALUES('mv2-op',?,'mv2-session','environment','inspect:map','{"secret":"PRIVATE-ARG"}',
                'unknown','{"secret":"PRIVATE-RESULT"}','2026-01-01T00:01:00+00:00','2026-01-01T00:02:00+00:00')""",
                (self.character_id,))
            job = indexer.start_job(db, self.character_id, "rebuild")
        with app_module.connect() as db:
            first = indexer.step_job(db, job["job_id"])
            self.assertEqual(first["processed"], 100)
            indexer.set_status(db, job["job_id"], "paused")
        app_module.init_db()
        with app_module.connect() as db:
            self.assertEqual(indexer._job(db, job["job_id"])["status"], "paused")
            indexer.set_status(db, job["job_id"], "running")
        completed = self._finish(job["job_id"])
        self.assertEqual(completed["processed"], 106)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_sources").fetchone()[0], 106)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_segments").fetchone()[0], 2)
            text = " ".join(row[0] for row in db.execute("SELECT term FROM mv2_terms"))
            self.assertNotIn("PRIVATE", text)
            self.assertIn("阿夜", text)
            self.assertIn("美咲", text)
            self.assertEqual(db.execute("SELECT source_status FROM mv2_sources WHERE source_kind='activity_operation'").fetchone()[0], "unknown")
            db.execute("UPDATE messages SET content='修订正文' WHERE id=?", (ids[0],))
            db.execute("DELETE FROM messages WHERE id=?", (ids[1],))
            db.execute("UPDATE activity_operations SET status='succeeded',result_json=? WHERE id='mv2-op'",
                       ('{"secret":"PRIVATE-RESULT-2"}',))
            next_job = indexer.start_job(db, self.character_id, "incremental")
        updated = self._finish(next_job["job_id"])
        self.assertGreaterEqual(updated["changed"], 2)
        self.assertEqual(updated["invalidated"], 1)
        with app_module.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM mv2_sources WHERE source_kind='message' AND source_id=?", (str(ids[1]),)).fetchone())
            self.assertNotIn("阿夜", {row[0] for row in db.execute("SELECT term FROM mv2_terms")})
            self.assertEqual(db.execute("SELECT source_status FROM mv2_sources WHERE source_kind='activity_operation'").fetchone()[0], "succeeded")
            repeat = indexer.start_job(db, self.character_id, "incremental")
        unchanged = self._finish(repeat["job_id"])
        self.assertEqual(unchanged["changed"], 0)
        with app_module.connect() as db:
            prior = db.execute("SELECT active_generation FROM mv2_meta WHERE character_id=?", (self.character_id,)).fetchone()[0]
            staging = indexer.start_job(db, self.character_id, "rebuild")
            indexer.step_job(db, staging["job_id"])
            self.assertEqual(db.execute("SELECT active_generation FROM mv2_meta WHERE character_id=?", (self.character_id,)).fetchone()[0], prior)
        self._finish(staging["job_id"])
        with app_module.connect() as db:
            self.assertGreater(db.execute("SELECT active_generation FROM mv2_meta WHERE character_id=?", (self.character_id,)).fetchone()[0], prior)


class MemoryV2API(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def search_header(text):
        return {"X-Memory-Query-B64":base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-mv2-api-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")
        response = await self.client.post("/api/auth/setup", json={"username":"owner","password":"synthetic-password-123"})
        self.assertEqual(response.status_code, 201, response.text)
        self.character_id = (await self.client.get("/api/characters")).json()[0]["id"]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def _build(self, character_id):
        response = await self.client.post("/api/memory-v2/index/jobs", json={"characterId":character_id,"mode":"rebuild"})
        self.assertEqual(response.status_code, 201, response.text)
        job = response.json()
        for _ in range(25):
            response = await self.client.post(f"/api/memory-v2/index/jobs/{job['job_id']}/step", params={"characterId":character_id})
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] == "completed":
                return job
        self.fail("API job did not finish")

    async def test_m11_new_character_can_start_without_restart(self):
        created = await self.client.post("/api/characters",json={"name":"synthetic second"})
        self.assertEqual(created.status_code,201,created.text)
        cid = created.json()["id"]
        self.assertEqual((await self.client.get("/api/memory-v2/status",params={"characterId":cid})).json()["state"],"needs_rebuild")
        await self._build(cid)
        self.assertEqual((await self.client.get("/api/memory-v2/status",params={"characterId":cid})).json()["state"],"ready")

    async def test_m13_search_unfold_stale_and_role_guard(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),base_url="http://testserver") as anonymous:
            unauthenticated = await anonymous.get("/api/memory-v2/status",params={"characterId":self.character_id})
            self.assertEqual(unauthenticated.status_code,401)
            self.assertEqual(unauthenticated.headers["cache-control"],"no-store")
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')""",
                (self.character_id,)).lastrowid
            first = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','阿夜与美咲讨论地图','2026-01-01T00:00:00+00:00')""", (conv,)).lastrowid
            second = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'assistant','阿夜与美咲讨论地图','2026-01-01T00:01:00+00:00')""", (conv,)).lastrowid
            db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'system','PRIVATE-NEVER-INDEX','2026-01-01T00:02:00+00:00')""", (conv,))
            other = db.execute("""INSERT INTO characters(name,created_at,updated_at)
                VALUES('other','2026-01-01','2026-01-01')""").lastrowid
        self.assertEqual((await self.client.get("/api/memory-v2/status",params={"characterId":self.character_id})).json()["state"],"needs_rebuild")
        await self._build(self.character_id)
        response = await self.client.get("/api/memory-v2/search", params={"characterId":self.character_id,"limit":1},headers=self.search_header("阿夜"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"],"no-store")
        page1 = response.json()
        self.assertEqual(len(page1["items"]),1)
        self.assertEqual(page1["items"][0]["possibleDuplicateCount"],1)
        self.assertIsNotNone(page1["nextCursor"])
        page2 = (await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id,"limit":1,"cursor":page1["nextCursor"]},headers=self.search_header("阿夜"))).json()
        self.assertEqual(len(page2["items"]),1)
        refs = {page1["items"][0]["sourceRef"]["id"],page2["items"][0]["sourceRef"]["id"]}
        self.assertEqual(refs,{str(first),str(second)})
        japanese = await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id},headers=self.search_header("美咲"))
        self.assertEqual(len(japanese.json()["items"]),2)
        secret = await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id},headers=self.search_header("PRIVATE"))
        self.assertEqual(secret.json()["items"],[])
        detail = await self.client.get(f"/api/memory-v2/sources/message/{first}",params={"characterId":self.character_id})
        self.assertEqual(detail.status_code,200,detail.text)
        unfold = await self.client.get(f"/api/memory-v2/sources/message/{first}/unfold",params={"characterId":self.character_id,"limit":2})
        self.assertEqual(unfold.status_code,200,unfold.text)
        self.assertEqual(unfold.json()["text"],"阿夜")
        next_page = await self.client.get(f"/api/memory-v2/sources/message/{first}/unfold",params={"characterId":self.character_id,"offset":2,"expectedFingerprint":unfold.json()["contentFingerprint"]})
        self.assertEqual(next_page.status_code,200,next_page.text)
        app_module.init_db()
        await self._build(other)
        cross = await self.client.get(f"/api/memory-v2/sources/message/{first}",params={"characterId":other})
        self.assertEqual(cross.status_code,404)
        with app_module.connect() as db:
            db.execute("UPDATE messages SET content='已修改的正文' WHERE id=?",(first,))
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{first}",params={"characterId":self.character_id})).status_code,404)
        stale = await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id},headers=self.search_header("阿夜"))
        self.assertEqual([x["sourceRef"]["id"] for x in stale.json()["items"]],[str(second)])
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{first}/unfold",params={"characterId":self.character_id,"offset":2,"expectedFingerprint":unfold.json()["contentFingerprint"]})).status_code,404)

    async def test_m13_activity_redaction_segments_and_generation_cursor(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')""",
                (self.character_id,)).lastrowid
            db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','阿夜','2026-01-01T00:00:00+00:00')",(conv,))
            db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','阿夜','2026-01-01T01:00:00+00:00')",(conv,))
            db.execute("""INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,
                max_model_calls,max_tool_calls,allowed_tools_json,snapshot_json)
                VALUES('mv2-api-session',?,0,'completed','2026-01-01','2026-01-02',0,0,'[]','{}')""",(self.character_id,))
            db.execute("""INSERT INTO activity_operations(id,character_id,session_id,kind,action_id,arguments_json,status,
                result_json,created_at,updated_at) VALUES('mv2-api-op',?,'mv2-api-session','tool',NULL,
                '{"secret":"PRIVATE-ARG"}','unknown','{"secret":"PRIVATE-RESULT"}',
                '2026-01-01T00:10:00+00:00','2026-01-01T00:11:00+00:00')""",(self.character_id,))
        await self._build(self.character_id)
        segments = await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id})
        self.assertEqual(segments.status_code,200,segments.text)
        self.assertEqual(len(segments.json()["items"]),3)
        source = await self.client.get("/api/memory-v2/sources/activity_operation/mv2-api-op/unfold",params={"characterId":self.character_id})
        self.assertEqual(source.status_code,200,source.text)
        self.assertTrue(source.json()["redactedToSafeFields"])
        self.assertIn("结果未知",source.json()["text"])
        self.assertNotIn("PRIVATE",source.text)
        searched = await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id},headers=self.search_header("工具"))
        self.assertEqual(len(searched.json()["items"]),1)
        first_page = await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id,"limit":1})
        cursor = first_page.json()["nextCursor"]
        self.assertIsNotNone(cursor)
        with app_module.connect() as db:
            update = indexer.start_job(db, self.character_id, "incremental")
        for _ in range(10):
            with app_module.connect() as db:
                update = indexer.step_job(db, update["job_id"])
            if update["phase"] == "segments":
                break
        self.assertEqual(update["phase"], "segments")
        with app_module.connect() as db:
            indexer.step_job(db, update["job_id"])
            indexer.set_status(db, update["job_id"], "paused")
        self.assertEqual((await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id})).status_code,409)
        self.assertEqual((await self.client.get(f"/api/memory-v2/segments/{segments.json()['items'][0]['segmentId']}/sources",params={"characterId":self.character_id})).status_code,409)
        with app_module.connect() as db:
            indexer.set_status(db, update["job_id"], "running")
        for _ in range(10):
            with app_module.connect() as db:
                update = indexer.step_job(db, update["job_id"])
            if update["status"] == "completed":
                break
        self.assertEqual(update["status"], "completed")
        await self._build(self.character_id)
        stale_cursor = await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id,"limit":1,"cursor":cursor})
        self.assertEqual(stale_cursor.status_code,409)

    async def test_m14_deleted_source_never_surfaces_from_segment(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01','2026-01-01')""",(self.character_id,)).lastrowid
            first = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','阿夜原文一','2026-01-01T00:00:00+00:00')",(conv,)).lastrowid
            second = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'assistant','阿夜原文二','2026-01-01T00:01:00+00:00')",(conv,)).lastrowid
        await self._build(self.character_id)
        before = (await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id})).json()
        self.assertEqual(before["items"][0]["memberCount"],2)
        segment_id = before["items"][0]["segmentId"]
        with app_module.connect() as db:
            db.execute("DELETE FROM messages WHERE id=?",(first,))
        after = (await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id})).json()
        self.assertEqual(after["items"][0]["memberCount"],1)
        members = (await self.client.get(f"/api/memory-v2/segments/{segment_id}/sources",params={"characterId":self.character_id})).json()
        self.assertEqual([x["sourceRef"]["id"] for x in members["items"]],[str(second)])
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{first}",params={"characterId":self.character_id})).status_code,404)
        with app_module.connect() as db:
            db.execute("DELETE FROM messages WHERE id=?",(second,))
        empty = (await self.client.get("/api/memory-v2/segments",params={"characterId":self.character_id})).json()
        self.assertEqual(empty["items"],[])

    async def test_m14_old_backup_rebuild_and_no_business_events(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01','2026-01-01')""",(self.character_id,)).lastrowid
            msg = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','旧备份阿夜','2026-01-01')",(conv,)).lastrowid
            baseline = {name:db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                        for name in ("messages","activity_operations","learning_evidence","feedback_activities_v4",
                                     "psychological_events","behavior_model_calls")}
            for name in ("mv2_segment_members","mv2_segments","mv2_terms","mv2_sources","mv2_jobs","mv2_meta"):
                db.execute(f"DROP TABLE {name}")
        password = "synthetic-backup-password"
        backup = app_module.build_encrypted_backup(password)
        response = await self.client.post("/api/system/backup/import",
            data={"confirm_replace":"true","passphrase":password},
            files={"backup":("synthetic-old.ybackup",backup,"application/octet-stream")})
        self.assertEqual(response.status_code,409,response.text)
        root = Path(self.temp.name)
        backup_file = root / "synthetic-old.ybackup"
        backup_file.write_bytes(backup)
        backup_v2.restore_to_directory(backup_file,password,root / "isolated-restore",root / "backup-work")
        app_module.init_db()
        status = await self.client.get("/api/memory-v2/status",params={"characterId":self.character_id})
        self.assertEqual(status.json()["state"],"needs_rebuild")
        self.assertEqual((await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id},headers=self.search_header("阿夜"))).status_code,409)
        await self._build(self.character_id)
        found = await self.client.get("/api/memory-v2/search",params={"characterId":self.character_id},headers=self.search_header("阿夜"))
        self.assertEqual([x["sourceRef"]["id"] for x in found.json()["items"]],[str(msg)])
        with app_module.connect() as db:
            after = {name:db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in baseline}
        self.assertEqual(after,baseline)

    async def test_m14_regeneration_and_role_reassignment(self):
        sent = await self.client.post("/api/chat/stream",json={"character_id":self.character_id,"message":"阿夜"})
        self.assertEqual(sent.status_code,200,sent.text)
        with app_module.connect() as db:
            conv = db.execute("SELECT id FROM conversations WHERE character_id=? ORDER BY id DESC LIMIT 1",(self.character_id,)).fetchone()[0]
            assistant = db.execute("SELECT id FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",(conv,)).fetchone()[0]
        await self._build(self.character_id)
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{assistant}",params={"characterId":self.character_id})).status_code,200)
        regenerated = await self.client.post("/api/chat/regenerate",json={"character_id":self.character_id,"conversation_id":conv})
        self.assertEqual(regenerated.status_code,200,regenerated.text)
        # Regeneration now preserves the old version for owner inspection.
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{assistant}",params={"characterId":self.character_id})).status_code,200)
        from backend.app import chat_history, memory_v2_sources
        with app_module.connect() as db:
            with chat_history.reading(db, conv):
                self.assertIsNone(memory_v2_sources.source(db, 'message', str(assistant)))
        with app_module.connect() as db:
            current = db.execute("SELECT id FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",(conv,)).fetchone()[0]
            self.assertNotEqual(current,assistant)
            other = db.execute("INSERT INTO characters(name,created_at,updated_at) VALUES('other','2026-01-01','2026-01-01')").lastrowid
        await self._build(self.character_id)
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{current}",params={"characterId":self.character_id})).status_code,200)
        app_module.init_db()
        with app_module.connect() as db:
            db.execute("UPDATE conversations SET character_id=? WHERE id=?",(other,conv))
        self.assertEqual((await self.client.get(f"/api/memory-v2/sources/message/{current}",params={"characterId":self.character_id})).status_code,404)
        await self._build(other)
        cross = await self.client.get("/api/memory-v2/search",params={"characterId":other},headers=self.search_header("阿夜"))
        self.assertTrue(any(item["sourceRef"]["kind"]=="message" for item in cross.json()["items"]))
