"""H2 RAW ingestion and isolation; all data is synthetic and temporary."""

import json
import asyncio
import io
import zipfile
import sqlite3
from datetime import datetime, timezone
import os
import tempfile
import unittest
from pathlib import Path

_bootstrap = tempfile.TemporaryDirectory(prefix="yosaki-import-h2-bootstrap-")
os.environ["YOSAKI_DATA_DIR"] = _bootstrap.name

from backend.app import main as app_module  # noqa: E402
from backend.app import memory_v2_import as importer  # noqa: E402
from backend.app import memory_v2_import_schema as schema  # noqa: E402
from backend.app import memory_v2_sources as sources  # noqa: E402
from backend.app import memory_v2_derivation_schema as m3schema  # noqa: E402
from backend.app import memory_v2_index as indexer  # noqa: E402
from backend.app import memory_v2_entries as entries  # noqa: E402
from backend.app import memory_v2_derivation as m3  # noqa: E402
from backend.app import memory_v2_time_projection as p2  # noqa: E402
from backend.app import memory_v2_import_api as import_api  # noqa: E402
from backend.app import archives as legacy_archives  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def archive(rows):
    return json.dumps({"format": "yosaki-conversation", "version": 2,
                       "conversation": {"id": "external-session"}, "messages": rows}, ensure_ascii=False).encode()


class ImportRawTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-import-h2-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        with app_module.connect() as db:
            m3schema.init_db(db)
            schema.init_db(db)
            schema.init_db(db)
            self.cid = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]

    def test_batch_resume_idempotence_and_live_chat_denial(self):
        rows = [{"id": f"m{i}", "role": "user" if i % 2 else "assistant",
                 "content": f"合成对话 {i}", "created_at": "2022-01-01T10:00:00+08:00"} for i in range(201)]
        with app_module.connect() as db:
            batch = importer.prepare(db, self.cid, [("history.json", archive(rows))])["batchId"]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            importer.confirm(db, batch, self.cid)
            first = importer.step(db, batch, self.cid)
            self.assertEqual(first["processed"], 100)
            convo = first["files"][0]["conversation_id"]
            self.assertEqual(db.execute("SELECT archive_only FROM conversations WHERE id=?", (convo,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (convo,)).fetchone()[0], 100)
            self.assertIsNone(sources.source(db, "message", str(db.execute("SELECT MIN(id) FROM messages").fetchone()[0])))
            importer.set_paused(db, batch, self.cid, True)
            with self.assertRaises(HTTPException):
                importer.step(db, batch, self.cid)
            importer.set_paused(db, batch, self.cid, False)
            importer.step(db, batch, self.cid)
            done = importer.step(db, batch, self.cid)
            self.assertEqual((done["status"], done["processed"]), ("raw_complete", 201))
            self.assertIsNone(db.execute("SELECT staged_json FROM mv2_import_files WHERE batch_id=?", (batch,)).fetchone()[0])
            first_id = db.execute("SELECT MIN(id) FROM messages WHERE conversation_id=?", (convo,)).fetchone()[0]
            evidence = sources.source(db, "message", str(first_id))
            self.assertEqual(evidence.occurred_at, "2022-01-01T10:00:00+08:00")
            self.assertEqual(evidence.recorded_at[:4], "2026")
            with self.assertRaises(HTTPException):
                importer.step(db, batch, self.cid)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (convo,)).fetchone()[0], 201)
            with self.assertRaises(HTTPException):
                importer.prepare(db, self.cid, [("history.json", archive(rows))])
            receipt = db.execute("SELECT * FROM mv2_import_receipts WHERE message_id=?", (first_id,)).fetchone()
            self.assertEqual((receipt["external_message_id"], receipt["ordinal"], receipt["file_sha256"][:4]),
                             ("m0", 1, first["files"][0]["file_sha256"][:4]))
            with self.assertRaises(HTTPException):
                app_module.require_live_conversation(db, convo)
            other = db.execute("INSERT INTO characters(name,created_at,updated_at) VALUES('other','2026-01-01','2026-01-01')").lastrowid
            db.execute("UPDATE conversations SET character_id=? WHERE id=?", (other, convo))
            self.assertIsNone(sources.source(db, "message", str(first_id)))
            db.execute("UPDATE conversations SET character_id=? WHERE id=?", (self.cid, convo))
        self.assertFalse(any(item["id"] == convo for item in app_module.list_conversations(self.cid)))
        for call in (lambda: app_module.list_messages(convo), lambda: app_module.get_summary(convo),
                     lambda: app_module.list_chat_tool_runs(convo), lambda: app_module.export_conversation(convo),
                     lambda: app_module.context_preview(app_module.PreviewInput(character_id=self.cid, conversation_id=convo)),
                     lambda: asyncio.run(app_module.chat_stream(app_module.ChatInput(character_id=self.cid, conversation_id=convo, message="hi"))),
                     lambda: asyncio.run(app_module.regenerate_chat(app_module.ChatInput(character_id=self.cid, conversation_id=convo)))):
            with self.assertRaises(HTTPException):
                call()
        legacy_export = next(route.endpoint for route in app_module.app.routes
                             if getattr(route, "path", "") == "/api/archives/export" and "POST" in route.methods)
        with self.assertRaises(HTTPException):
            legacy_export(legacy_archives.ExportOptions(conversation_ids=[convo]))

    def test_import_schema_does_not_change_native_message_fingerprint(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER);
            CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER REFERENCES characters(id));
            CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER REFERENCES conversations(id),
                role TEXT,content TEXT,created_at TEXT,generation_status TEXT);
            CREATE TABLE imported_archives(conversation_id INTEGER,imported_at TEXT);
            INSERT INTO characters VALUES(1,1);
            INSERT INTO conversations VALUES(1,1);
            INSERT INTO messages VALUES(1,1,'user','原生合成消息','2024-01-01T00:00:00+00:00',NULL);""")
        before = sources.source(db, "message", "1")
        schema.init_db(db)
        after = sources.source(db, "message", "1")
        self.assertEqual((after.content_hash, after.state_hash), (before.content_hash, before.state_hash))
        db.close()

    def test_day_precision_and_source_tamper_fail_closed(self):
        body = archive([{"role": "user", "content": "日期已知", "created_at": "2020-05-06"}])
        with app_module.connect() as db:
            batch = importer.prepare(db, self.cid, [("day.json", body)])["batchId"]
            importer.confirm(db, batch, self.cid)
            done = importer.step(db, batch, self.cid)
            message_id = db.execute("SELECT message_id FROM mv2_import_receipts").fetchone()[0]
            receipt = db.execute("SELECT * FROM mv2_import_receipts WHERE message_id=?", (message_id,)).fetchone()
            self.assertEqual((receipt["occurred_at"], receipt["occurred_date"], receipt["time_precision"]),
                             (None, "2020-05-06", "day"))
            source = sources.source(db, "message", str(message_id))
            self.assertIsNone(source.occurred_at)
            job = indexer.start_job(db, self.cid, "rebuild")
            for _ in range(30):
                if indexer.step_job(db, job["job_id"])["status"] == "completed":
                    break
            refs = importer.create_draft_entries(db, batch, self.cid)[0]
            db.execute("INSERT INTO users(username,password_hash,created_at) VALUES('day-synthetic','x','2026-01-01')")
            derivation = m3.create_derivation(db, self.cid, "NARRATIVE", refs["sourceRefs"])
            narrative = m3.add_version(db, derivation["id"], refs["sourceRefs"], "她说：日期已知。", author_kind="user")
            m3.review_version(db, narrative["id"], self.cid, 1, narrative["output_hash"], narrative["input_hash"])
            m3.activate_version(db, narrative["id"], self.cid)
            version = entries.add_version(db, refs["entryId"], refs["sourceRefs"],
                                          "2020-05-06 的记录", ["日期"], [], "用户记录了一个日期。", narrative["id"])
            self.assertEqual((version["time_basis"], version["occurred_start"]), ("occurred", "2020-05-06"))
            entries.review_version(db, version["id"], self.cid, 1, version["metadata_hash"], version["source_hash"])
            entries.activate_version(db, version["id"], self.cid)
            members = p2._members(db, self.cid, datetime(2026, 1, 1, tzinfo=timezone.utc), 480)
            self.assertEqual(members[0].time_basis, "occurred_day")
            db.execute("UPDATE messages SET content='tampered' WHERE id=?", (message_id,))
            self.assertIsNone(sources.source(db, "message", str(message_id)))

    def test_unverified_local_datetime_cannot_become_entry_event_age(self):
        body = archive([{"role": "user", "content": "无时区的合成记录",
                         "created_at": "2020-05-06T23:30:00"}])
        with app_module.connect() as db:
            batch = importer.prepare(db, self.cid, [("local.json", body)])["batchId"]
            importer.confirm(db, batch, self.cid)
            importer.step(db, batch, self.cid)
            message_id = db.execute("SELECT message_id FROM mv2_import_receipts").fetchone()[0]
            evidence = sources.source(db, "message", str(message_id))
            self.assertEqual((evidence.occurred_at, evidence.occurred_date,
                              evidence.time_precision), (None, "2020-05-06", "local_datetime_unverified"))
            self.assertEqual(entries._times([evidence])[-1], "mixed")
            with self.assertRaises(ValueError):
                entries._check_title_date("2020-05-06 的确定日期", [evidence], "mixed")

    def test_30_minute_suggestions_edit_and_one_stable_draft(self):
        rows = [{"role": "user", "content": f"消息 {i}", "created_at": stamp} for i, stamp in enumerate(
            ("2020-01-01T10:00:00+08:00", "2020-01-01T10:31:00+08:00", "2020-01-01T10:32:00+08:00"), 1)]
        with app_module.connect() as db:
            prepared = importer.prepare(db, self.cid, [("segments.json", archive(rows))])
            batch = prepared["batchId"]
            view = importer.status(db, batch, self.cid)
            self.assertEqual([(s["start_ordinal"], s["end_ordinal"]) for s in view["segments"]], [(1, 1), (2, 3)])
            file_id = view["files"][0]["id"]
            changed = importer.replace_segments(db, batch, self.cid, file_id,
                                                [{"start": 1, "end": 3, "title": "人工合并"}], view["planVersion"])
            self.assertEqual(len(changed["segments"]), 1)
            with self.assertRaises(HTTPException):
                importer.replace_segments(db, batch, self.cid, file_id,
                                          [{"start": 1, "end": 2, "title": "遗漏"}], changed["planVersion"])
            importer.confirm(db, batch, self.cid)
            importer.step(db, batch, self.cid)
            job = indexer.start_job(db, self.cid, "rebuild")
            for _ in range(30):
                result = indexer.step_job(db, job["job_id"])
                if result["status"] == "completed":
                    break
            else:
                self.fail("M1 index did not complete")
            first = importer.create_draft_entries(db, batch, self.cid)
            second = importer.create_draft_entries(db, batch, self.cid)
            self.assertEqual(first[0]["entryId"], second[0]["entryId"])
            self.assertEqual([r["id"] for r in first[0]["sourceRefs"]],
                             [str(r[0]) for r in db.execute("SELECT message_id FROM mv2_import_receipts ORDER BY ordinal")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entries").fetchone()[0], 1)
            found = importer.search_raw(db, self.cid, "消息")
            self.assertEqual(len(found["items"]), 3)
            self.assertTrue(all(item["batchId"] == batch for item in found["items"]))
            first_message = int(first[0]["sourceRefs"][0]["id"])
            db.execute("UPDATE messages SET content='已修改' WHERE id=?", (first_message,))
            self.assertEqual(len(importer.search_raw(db, self.cid, "消息")["items"]), 2)

    def test_isolated_api_preview_import_raw_and_role_guards(self):
        with app_module.connect() as db:
            db.execute("INSERT INTO users(username,password_hash,created_at) VALUES('synthetic','test','2026-01-01')")
            db.execute("""INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at)
                VALUES(?,1,'2026-01-01','2099-01-01')""", (app_module.session_digest("synthetic-import-session"),))
        app = FastAPI()
        import_api.install(app, app_module.connect, app_module.current_session)
        with TestClient(app) as client:
            data = {"characterId": str(self.cid)}
            body = archive([{"role": "user", "content": "安全 RAW", "created_at": "2020-05-06"}])
            upload = {"files": ("safe.json", body, "application/json")}
            self.assertEqual(client.post("/api/memory-v2/imports/preview", data=data, files=upload).status_code, 401)
            client.cookies.set(app_module.SESSION_COOKIE, "synthetic-import-session")
            self.assertEqual(client.post("/api/memory-v2/imports/preview", data=data, files=upload,
                                         headers={"Origin": "https://evil.invalid"}).status_code, 403)
            response = client.post("/api/memory-v2/imports/preview", data=data, files=upload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(client.post("/api/memory-v2/imports/batches", data={"characterId": "999"},
                                         files=upload).status_code, 404)
            response = client.post("/api/memory-v2/imports/batches", data=data, files=upload)
            self.assertEqual(response.status_code, 200)
            batch = response.json()["batchId"]
            view = client.get(f"/api/memory-v2/imports/batches/{batch}", params={"characterId": self.cid}).json()
            file_id = view["files"][0]["id"]
            self.assertEqual(client.post(f"/api/memory-v2/imports/batches/{batch}/confirm",
                                         params={"characterId": self.cid}).status_code, 200)
            self.assertEqual(client.post(f"/api/memory-v2/imports/batches/{batch}/step",
                                         params={"characterId": self.cid}).json()["status"], "raw_complete")
            raw = client.get(f"/api/memory-v2/imports/batches/{batch}/raw/{file_id}",
                             params={"characterId": self.cid})
            self.assertEqual(raw.status_code, 200)
            self.assertEqual(raw.json()["items"][0]["content"], "安全 RAW")
            self.assertEqual(client.get(f"/api/memory-v2/imports/batches/{batch}/raw/{file_id}",
                                        params={"characterId": 999}).status_code, 404)
            self.assertEqual(client.get(f"/api/memory-v2/imports/batches/{batch}/raw/{file_id}",
                                        params={"characterId": self.cid, "snapshot": "wrong"}).status_code, 409)
            with app_module.connect() as db:
                db.execute("UPDATE messages SET content='source modified' WHERE id=(SELECT MIN(message_id) FROM mv2_import_receipts)")
            self.assertEqual(client.get(f"/api/memory-v2/imports/batches/{batch}/raw/{file_id}",
                                        params={"characterId": self.cid}).status_code, 409)
            files = client.get("/api/memory-v2/imports/raw-files", params={"characterId": self.cid}).json()["items"]
            self.assertEqual((files[0]["sourceStatus"], files[0]["title"]), ("source_stale", None))
            with app_module.connect() as db:
                db.execute("DELETE FROM messages WHERE id=(SELECT MIN(message_id) FROM mv2_import_receipts)")
            self.assertEqual(client.get(f"/api/memory-v2/imports/batches/{batch}/raw/{file_id}",
                                        params={"characterId": self.cid}).status_code, 409)

    def test_zip_receipt_backup_restore_and_old_schema(self):
        body = archive([{"id": "z1", "role": "user", "content": "合成 ZIP 来源",
                         "created_at": "2021-01-01T12:00:00+08:00"}])
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as output:
            output.writestr("nested/history.json", body)
        with app_module.connect() as db:
            batch = importer.prepare(db, self.cid, [("synthetic.zip", packed.getvalue())])["batchId"]
            importer.confirm(db, batch, self.cid)
            importer.step(db, batch, self.cid)
            receipt = db.execute("SELECT * FROM mv2_import_receipts").fetchone()
            self.assertEqual(receipt["source_path"], "synthetic.zip!nested/history.json")
            self.assertEqual(len(receipt["container_sha256"]), 64)
        encrypted = app_module.build_encrypted_backup("SyntheticBackupOnly_2026!")
        plain, _ = app_module.decrypt_backup(encrypted, "SyntheticBackupOnly_2026!")
        restored = sqlite3.connect(":memory:")
        restored.deserialize(plain)
        self.assertEqual(restored.execute("SELECT COUNT(*) FROM mv2_import_receipts").fetchone()[0], 1)
        restored.close()
        old = sqlite3.connect(":memory:")
        old.execute("PRAGMA foreign_keys=ON")
        old.executescript("""CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER);
            CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER REFERENCES characters(id));
            CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER REFERENCES conversations(id));""")
        schema.init_db(old)
        schema.init_db(old)
        self.assertEqual(old.execute("SELECT COUNT(*) FROM mv2_import_receipts").fetchone()[0], 0)
        self.assertIn("archive_only", [r[1] for r in old.execute("PRAGMA table_info(conversations)")])
        old.close()

    def test_external_identity_overlap_is_rejected_before_second_raw_write(self):
        first = archive([{"id": "same", "role": "user", "content": "first"}])
        second = archive([{"id": "same", "role": "user", "content": "changed"}])
        with app_module.connect() as db:
            batch = importer.prepare(db, self.cid, [("first.json", first)])["batchId"]
            with self.assertRaises(HTTPException):
                importer.prepare(db, self.cid, [("second.json", second)])
            importer.confirm(db, batch, self.cid)
            importer.step(db, batch, self.cid)
            with self.assertRaises(HTTPException) as caught:
                importer.prepare(db, self.cid, [("second.json", second)])
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            stale = archive([{"id": "new", "role": "user", "content": "staged only"}])
            pending = importer.prepare(db, self.cid, [("pending.json", stale)])["batchId"]
            db.execute("UPDATE mv2_import_batches SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (pending,))
            importer.purge_stale_previews(db, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
            self.assertIsNone(db.execute("SELECT 1 FROM mv2_import_batches WHERE id=?", (pending,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT 1 FROM mv2_import_batches WHERE id=?", (batch,)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
