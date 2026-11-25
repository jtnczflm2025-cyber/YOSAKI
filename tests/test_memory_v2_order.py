"""Synthetic M1 order-contract and signed-cursor acceptance; no private sources."""

import base64
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_bootstrap = tempfile.TemporaryDirectory(prefix="yosaki-mv2-order-bootstrap-")
os.environ["YOSAKI_DATA_DIR"] = _bootstrap.name
from backend.app import main as app_module  # noqa: E402
from backend.app import memory_v2_api as api  # noqa: E402
from backend.app import memory_v2_import as importer  # noqa: E402
from backend.app import memory_v2_import_schema as import_schema  # noqa: E402
from backend.app import memory_v2_index as indexer  # noqa: E402
from backend.app import memory_v2_schema as schema  # noqa: E402
from backend.app import memory_v2_sources as sources  # noqa: E402


def document(count, name="external-session", *, times=None):
    rows = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"合成阿夜消息第{i + 1}条", "created_at":
             times[i] if times else "2026-07-20 19:38"} for i in range(count)]
    return json.dumps({"format": "yosaki-conversation", "version": 2,
                       "conversation": {"id": name}, "messages": rows}, ensure_ascii=False).encode()


class OrderContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-mv2-order-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        with app_module.connect() as db:
            import_schema.init_db(db)
            self.cid = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]

    def prepare(self, files):
        with app_module.connect() as db:
            result = importer.prepare(db, self.cid, files)
            importer.confirm(db, result["batchId"], self.cid)
            return result["batchId"]

    def import_all(self, batch_id):
        with app_module.connect() as db:
            while True:
                status = importer.step(db, batch_id, self.cid)
                if status["status"] == "raw_complete":
                    return

    def build(self, mode="rebuild", job_id=None):
        with app_module.connect() as db:
            job = indexer.start_job(db, self.cid, mode) if job_id is None else indexer._job(db, job_id)
            for _ in range(1000):
                job = indexer.step_job(db, job["job_id"])
                if job["status"] != "running":
                    return job
        self.fail("index_job_exceeded_step_limit")

    def client(self):
        app = FastAPI()
        api.install(app, app_module.connect, lambda: b"K" * 32)
        return TestClient(app)

    def test_130_ordinal_resume_and_unknown_time(self):
        batch = self.prepare([("first.json", document(130))])
        with app_module.connect() as db:
            first = importer.step(db, batch, self.cid)
            self.assertEqual(first["processed"], 100)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_import_receipts").fetchone()[0], 100)
        job = self.build()
        self.assertEqual((job["status"], job["error_code"]), ("paused", "archive_order_invalid"))
        with app_module.connect() as db:
            importer.step(db, batch, self.cid)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_import_receipts").fetchone()[0], 130)
            indexer.set_status(db, job["job_id"], "running")
        completed = self.build(job_id=job["job_id"])
        self.assertEqual(completed["status"], "completed")
        with app_module.connect() as db:
            rows = db.execute("""SELECT s.source_id,s.archive_ordinal,sm.ordinal,s.occurred_at
                FROM mv2_segment_members sm JOIN mv2_sources s
                ON s.generation=sm.generation AND s.source_kind=sm.source_kind AND s.source_id=sm.source_id
                ORDER BY sm.ordinal""").fetchall()
            self.assertEqual(len(rows), 130)
            self.assertEqual([r["archive_ordinal"] for r in rows], list(range(1, 131)))
            self.assertEqual([r["source_id"] for r in rows[:4]], ["1", "2", "3", "4"])
            self.assertTrue(all(r["occurred_at"] is None for r in rows))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_segments").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT start_at FROM mv2_segments").fetchone()[0])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            started = time.perf_counter()
            tracemalloc.start()
            snapshot = api._snapshot(db,self.cid,db.execute("SELECT active_generation FROM mv2_meta").fetchone()[0],
                                     terms=sorted(sources.query_terms("阿夜")))
            elapsed = time.perf_counter()-started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(len(snapshot),64)
            print(f"M1 synthetic snapshot sources=130 elapsed_s={elapsed:.3f} peak_bytes={peak}")
        before = [(r["source_id"],r["archive_ordinal"]) for r in rows]
        self.build()
        with app_module.connect() as db:
            rebuilt = db.execute("""SELECT s.source_id,s.archive_ordinal FROM mv2_segment_members sm
                JOIN mv2_sources s ON s.generation=sm.generation AND s.source_kind=sm.source_kind
                AND s.source_id=sm.source_id WHERE s.generation=(SELECT active_generation FROM mv2_meta WHERE character_id=?)
                ORDER BY sm.ordinal""", (self.cid,)).fetchall()
            self.assertEqual([(r[0],r[1]) for r in rebuilt], before)

    def test_rebuild_refuses_source_change_before_generation_switch(self):
        batch = self.prepare([("a.json", document(3))])
        self.import_all(batch)
        with app_module.connect() as db:
            job = indexer.start_job(db, self.cid, "rebuild")
            for _ in range(100):
                job = indexer.step_job(db, job["job_id"])
                if job["phase"] == "segments":
                    break
            self.assertEqual(job["phase"], "segments")
            while True:
                job = indexer.step_job(db, job["job_id"])
                if json.loads(job["segment_cursor_json"]):
                    break
            db.execute("UPDATE messages SET content=? WHERE id=1", ("已变化的合成正文",))
            job = indexer.step_job(db, job["job_id"])
            self.assertEqual((job["status"], job["error_code"]),
                             ("paused", "source_changed_during_index"))
            self.assertEqual(db.execute("SELECT active_generation FROM mv2_meta").fetchone()[0], 0)

    def test_file_scopes_and_reverse_time(self):
        batch = self.prepare([("a.json", document(2, "a", times=["2026-07-20T19:38:00+08:00",
                                  "2026-07-19T17:37:00+08:00"])),
                              ("b.json", document(2, "b"))])
        self.import_all(batch)
        self.assertEqual(self.build()["status"], "completed")
        with app_module.connect() as db:
            groups = db.execute("""SELECT s.archive_file_id,s.stream_id,s.archive_ordinal,sm.order_warning,
                sm.segment_id FROM mv2_segment_members sm JOIN mv2_sources s
                ON s.generation=sm.generation AND s.source_kind=sm.source_kind AND s.source_id=sm.source_id
                ORDER BY s.archive_file_id,s.archive_ordinal""").fetchall()
            self.assertEqual(len({r["archive_file_id"] for r in groups}), 2)
            by_file = {}
            for row in groups:
                by_file.setdefault(row["archive_file_id"], []).append(row)
            self.assertEqual([r["archive_ordinal"] for rows in by_file.values() for r in rows], [1, 2, 1, 2])
            reverse = next(rows for rows in by_file.values() if rows[0]["stream_id"] == "1")
            self.assertEqual(reverse[1]["order_warning"], "time_order_conflict")
            self.assertEqual(reverse[0]["segment_id"], reverse[1]["segment_id"])

    def test_completed_file_receipt_tamper_and_cross_session_reject(self):
        batch = self.prepare([("a.json", document(3))])
        self.import_all(batch)
        with app_module.connect() as db:
            ids = [r[0] for r in db.execute("SELECT message_id FROM mv2_import_receipts ORDER BY ordinal")]
            self.assertIsNotNone(sources.source(db, "message", str(ids[0])))
            db.execute("UPDATE mv2_import_receipts SET ordinal=9 WHERE message_id=?", (ids[2],))
            self.assertIsNone(sources.source(db, "message", str(ids[0])))
            db.execute("UPDATE mv2_import_receipts SET ordinal=3 WHERE message_id=?", (ids[2],))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE mv2_import_receipts SET ordinal=2 WHERE message_id=?", (ids[2],))
            db.execute("SAVEPOINT missing_receipt")
            db.execute("DELETE FROM mv2_import_receipts WHERE message_id=?", (ids[2],))
            self.assertIsNone(sources.source(db, "message", str(ids[0])))
            db.execute("ROLLBACK TO missing_receipt")
            db.execute("RELEASE missing_receipt")
            db.execute("SAVEPOINT wrong_parser")
            db.execute("UPDATE mv2_import_receipts SET parser_version='forged' WHERE message_id=?", (ids[2],))
            self.assertIsNone(sources.source(db, "message", str(ids[0])))
            db.execute("ROLLBACK TO wrong_parser")
            db.execute("RELEASE wrong_parser")
            db.execute("SAVEPOINT wrong_batch_manifest")
            db.execute("UPDATE mv2_import_batches SET total=total+1 WHERE id=?", (batch,))
            self.assertIsNone(sources.source(db, "message", str(ids[0])))
            db.execute("ROLLBACK TO wrong_batch_manifest")
            db.execute("RELEASE wrong_batch_manifest")
            other = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at,archive_only)
                VALUES(?,'other','2026-01-01','2026-01-01',1)""", (self.cid,)).lastrowid
            db.execute("UPDATE messages SET conversation_id=? WHERE id=?", (other,ids[2]))
            self.assertIsNone(sources.source(db, "message", str(ids[0])))

    def test_signed_cursor_source_change_and_resource_cap(self):
        batch = self.prepare([("a.json", document(130))])
        self.import_all(batch)
        self.build()
        client = self.client()
        query = base64.urlsafe_b64encode("合成阿夜".encode()).decode().rstrip("=")
        params = {"characterId":self.cid,"limit":1}
        first = client.get("/api/memory-v2/search", params=params,
                           headers={"X-Memory-Query-B64":query})
        self.assertEqual(first.status_code,200,first.text)
        cursor = first.json()["nextCursor"]
        self.assertTrue(cursor)
        different_query = base64.urlsafe_b64encode("美咲".encode()).decode().rstrip("=")
        self.assertEqual(client.get("/api/memory-v2/search",params={**params,"cursor":cursor},
                                    headers={"X-Memory-Query-B64":different_query}).status_code,422)
        changed = cursor[:-2] + ("A" if cursor[-2] != "A" else "B") + cursor[-1]
        self.assertEqual(client.get("/api/memory-v2/search",params={**params,"cursor":changed},
                                    headers={"X-Memory-Query-B64":query}).status_code,422)
        with app_module.connect() as db:
            db.execute("UPDATE mv2_import_receipts SET original_time_text='changed' WHERE ordinal=1")
        stale = client.get("/api/memory-v2/search",params={**params,"cursor":cursor},
                           headers={"X-Memory-Query-B64":query})
        self.assertEqual(stale.status_code,409,stale.text)
        with app_module.connect() as db:
            db.execute("UPDATE mv2_import_receipts SET original_time_text='2026-07-20 19:38' WHERE ordinal=1")
            db.execute("UPDATE mv2_import_receipts SET timezone_basis='manual_correction' WHERE ordinal=1")
        self.assertEqual(self.build(mode="incremental")["status"],"completed")
        self.assertEqual(client.get("/api/memory-v2/search",params={**params,"cursor":cursor},
                                    headers={"X-Memory-Query-B64":query}).status_code,409)
        unavailable = FastAPI()
        api.install(unavailable,app_module.connect,lambda:None)
        self.assertEqual(TestClient(unavailable).get("/api/memory-v2/search",params=params,
                         headers={"X-Memory-Query-B64":query}).status_code,503)
        old_limit = api.MAX_CURSOR_SNAPSHOT_SOURCES
        api.MAX_CURSOR_SNAPSHOT_SOURCES = 100
        try:
            self.assertEqual(client.get("/api/memory-v2/search",params=params,
                                        headers={"X-Memory-Query-B64":query}).status_code,409)
        finally:
            api.MAX_CURSOR_SNAPSHOT_SOURCES = old_limit

    def test_5000_and_10000_snapshot_measurements(self):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01','2026-01-01')""", (self.cid,)).lastrowid
            db.executemany("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user',?,'2026-01-01')""",
                ((conv,f"阿夜合成消息{i}") for i in range(5000)))
        self.assertEqual(self.build()["status"], "completed")
        def measure(count):
            with app_module.connect() as db:
                terms = sorted(sources.query_terms("阿夜"))
                tracemalloc.start()
                started = time.perf_counter()
                digest = api._snapshot(db,self.cid,db.execute("SELECT active_generation FROM mv2_meta").fetchone()[0],terms=terms)
                elapsed = time.perf_counter()-started
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            self.assertEqual(len(digest),64)
            print(f"M1 synthetic snapshot sources={count} elapsed_s={elapsed:.3f} peak_bytes={peak}")
        measure(5000)
        with app_module.connect() as db:
            db.executemany("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user',?,'2026-01-01')""",
                ((conv,f"阿夜合成消息{i}") for i in range(5000,10000)))
        self.assertEqual(self.build(mode="incremental")["status"], "completed")
        measure(10000)

    def test_old_index_startup_does_not_migrate(self):
        path = Path(self.temp.name) / "legacy-order.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""CREATE TABLE characters(id INTEGER PRIMARY KEY);
                INSERT INTO characters VALUES(1);
                CREATE TABLE mv2_meta(character_id INTEGER PRIMARY KEY,schema_version INTEGER,
                  active_generation INTEGER,state TEXT,updated_at TEXT);
                INSERT INTO mv2_meta VALUES(1,1,1,'ready','2026-01-01');
                CREATE TABLE mv2_sources(generation INTEGER,source_kind TEXT,source_id TEXT,
                  character_id INTEGER,stream_id TEXT,occurred_at TEXT,recorded_at TEXT,content_hash TEXT,
                  PRIMARY KEY(generation,source_kind,source_id));
                CREATE TABLE mv2_segment_members(generation INTEGER,segment_id TEXT,
                  source_kind TEXT,source_id TEXT,ordinal INTEGER);
                CREATE TABLE mv2_jobs(job_id TEXT PRIMARY KEY,character_id INTEGER,status TEXT,
                  error_code TEXT,updated_at TEXT,target_generation INTEGER);
                INSERT INTO mv2_jobs VALUES('old',1,'paused',NULL,'2026-01-01',1);""")
            schema.init_db(db)
            self.assertNotIn("order_revision",{r[1] for r in db.execute("PRAGMA table_info(mv2_meta)")})
            self.assertEqual(db.execute("SELECT schema_version,state FROM mv2_meta").fetchone()[:],(1,"ready"))
            db.commit()
            schema.migrate_order_v2(db)
            self.assertEqual(db.execute("SELECT schema_version,state FROM mv2_meta").fetchone()[:],(2,"needs_rebuild"))
            self.assertEqual(db.execute("SELECT status,error_code FROM mv2_jobs").fetchone()[:],
                             ("failed","order_contract_upgrade"))
            revision = db.execute("SELECT order_revision FROM mv2_meta").fetchone()[0]
            schema.migrate_order_v2(db)
            self.assertEqual(db.execute("SELECT order_revision FROM mv2_meta").fetchone()[0],revision)


if __name__ == "__main__":
    unittest.main()
