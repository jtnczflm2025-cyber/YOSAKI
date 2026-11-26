"""H5-A synthetic, filesystem-isolated backup tests. No application DB import."""

import base64
import asyncio
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from backend.app import backup_v2


def make_database(path: Path, size=2_000_000):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE characters(id INTEGER PRIMARY KEY);
        CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER REFERENCES characters(id));
        CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER REFERENCES conversations(id),content TEXT);
        CREATE TABLE model_profiles(id INTEGER PRIMARY KEY,api_key_encrypted TEXT);
        CREATE TABLE mcp_servers(id INTEGER PRIMARY KEY,auth_secret_encrypted TEXT,session_id TEXT,status TEXT,last_error TEXT);
        CREATE TABLE plugins(id INTEGER PRIMARY KEY);
        CREATE TABLE auth_sessions(id INTEGER PRIMARY KEY);
        CREATE TABLE auth_login_attempts(id INTEGER PRIMARY KEY);
        CREATE TABLE mv3_versions(id TEXT PRIMARY KEY,body TEXT);
        CREATE TABLE mv3_reviews(id TEXT PRIMARY KEY,version_id TEXT REFERENCES mv3_versions(id));
    """)
    db.execute("INSERT INTO users VALUES(1)")
    db.execute("INSERT INTO characters VALUES(1)")
    db.execute("INSERT INTO conversations VALUES(1,1)")
    db.execute("INSERT INTO messages VALUES(1,1,?)", (os.urandom(size).hex(),))
    db.execute("INSERT INTO model_profiles VALUES(1,'encrypted-secret')")
    db.execute("INSERT INTO mcp_servers VALUES(1,'encrypted-secret','session','connected',NULL)")
    db.execute("INSERT INTO auth_sessions VALUES(1)")
    db.execute("INSERT INTO auth_login_attempts VALUES(1)")
    db.execute("INSERT INTO mv3_versions VALUES('v1','synthetic reviewed draft')")
    db.execute("INSERT INTO mv3_reviews VALUES('r1','v1')")
    db.commit()
    db.close()


class BackupV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h5a-synthetic-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite3"
        self.backup = self.root / "sample.ybackup"
        self.work = self.root / "work"
        self.password = "synthetic-password-123"
        make_database(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def create(self):
        return backup_v2.create_backup(self.source, self.backup, self.password, self.work)

    def test_roundtrip_redaction_and_newer_source_preserved(self):
        manifest = self.create()
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("INSERT INTO messages VALUES(2,1,'newer record')")
            db.commit()
        result = backup_v2.restore_to_directory(self.backup, self.password, self.root / "restored", self.work)
        self.assertEqual(result["database_sha256"], manifest["database_sha256"])
        self.assertFalse((self.root / "restored" / ".secret_key").exists())
        with closing(sqlite3.connect(self.root / "restored" / "yosaki.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_reviews").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT api_key_encrypted FROM model_profiles").fetchone()[0])
            self.assertIsNone(db.execute("SELECT auth_secret_encrypted FROM mcp_servers").fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 0)
        with closing(sqlite3.connect(self.source)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT api_key_encrypted FROM model_profiles").fetchone()[0], "encrypted-secret")

    def test_wrong_password_and_tampering_do_not_publish(self):
        self.create()
        original = self.backup.read_bytes()
        variants = [original[:-1], original + b"extra", original[:200] + bytes([original[200] ^ 1]) + original[201:],
                    original[:220] + original[240:260] + original[220:240] + original[260:]]
        for index, data in enumerate(variants):
            changed = self.root / f"changed-{index}.ybackup"
            changed.write_bytes(data)
            target = self.root / f"failed-{index}"
            with self.assertRaises(backup_v2.BackupError):
                backup_v2.restore_to_directory(changed, self.password, target, self.work)
            self.assertFalse(target.exists())
        with self.assertRaises(backup_v2.BackupError):
            backup_v2.restore_to_directory(self.backup, "wrong-password-123", self.root / "wrong", self.work)
        self.assertFalse((self.root / "wrong").exists())

    def test_malicious_header_and_existing_target(self):
        self.create()
        data = bytearray(self.backup.read_bytes())
        hlen = int.from_bytes(data[len(backup_v2.MAGIC):len(backup_v2.MAGIC) + 2], "big")
        offset = len(backup_v2.MAGIC) + 2
        header = json.loads(bytes(data[offset:offset + hlen]))
        header["iterations"] = 999_999_999
        replacement = backup_v2._json(header)
        data = data[:len(backup_v2.MAGIC)] + len(replacement).to_bytes(2, "big") + replacement + data[offset + hlen:]
        bad = self.root / "bad-kdf.ybackup"
        bad.write_bytes(data)
        with self.assertRaisesRegex(backup_v2.BackupError, "invalid_header"):
            backup_v2.restore_to_directory(bad, self.password, self.root / "bad", self.work)
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(backup_v2.BackupError, "target_exists"):
            backup_v2.restore_to_directory(self.backup, self.password, existing, self.work)

    def test_distinct_random_nonce_for_same_snapshot(self):
        self.create()
        other = self.root / "second.ybackup"
        backup_v2.create_backup(self.source, other, self.password, self.work)
        first = self.backup.read_bytes()
        second = other.read_bytes()
        self.assertNotEqual(first[:256], second[:256])
        self.assertEqual(backup_v2.restore_to_directory(other, self.password, self.root / "second-restored", self.work)["format_version"], 2)

    def test_legacy_v1_restores_to_new_directory(self):
        # Fixture uses the documented V1 wire format, without importing main.py.
        raw = self.source.read_bytes()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("database.sqlite3", raw)
            zf.writestr("manifest.json", json.dumps({"format": "yosaki-full-backup", "version": 1}))
        salt = os.urandom(16)
        key = base64.urlsafe_b64encode(backup_v2._key(self.password, salt, backup_v2.KDF_ITERATIONS))
        legacy = self.root / "legacy.ybackup"
        legacy.write_bytes(b"YOSAKI-ENCRYPTED-BACKUP-V1\n" + salt + Fernet(key).encrypt(archive.getvalue()))
        # V1 produced by the real exporter is already redacted. Simulate that here.
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("UPDATE model_profiles SET api_key_encrypted=NULL")
            db.execute("UPDATE mcp_servers SET auth_secret_encrypted=NULL,session_id=NULL")
            db.execute("DELETE FROM auth_sessions")
            db.execute("DELETE FROM auth_login_attempts")
            db.commit()
        # Repack the sanitized legacy fixture.
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("database.sqlite3", self.source.read_bytes())
            zf.writestr("manifest.json", json.dumps({"format": "yosaki-full-backup", "version": 1}))
        legacy.write_bytes(b"YOSAKI-ENCRYPTED-BACKUP-V1\n" + salt + Fernet(key).encrypt(archive.getvalue()))
        result = backup_v2.restore_to_directory(legacy, self.password, self.root / "legacy-restored", self.work)
        self.assertEqual(result["format_version"], 1)
        with self.assertRaises(backup_v2.BackupError):
            backup_v2.restore_to_directory(legacy, "wrong-password-123", self.root / "legacy-wrong", self.work)

    def test_wal_writer_during_snapshot(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        started = threading.Event()
        written = []

        def writer():
            with closing(sqlite3.connect(self.source, timeout=10)) as db:
                started.set()
                for index in range(15):
                    db.execute("INSERT INTO messages VALUES(?,?,?)", (index + 10, 1, "concurrent-" + str(index)))
                    db.commit()
                    written.append(index)
                    time.sleep(0.01)

        task = threading.Thread(target=writer)
        task.start()
        self.assertTrue(started.wait(2))
        self.create()
        task.join(timeout=10)
        self.assertEqual(len(written), 15)
        result = backup_v2.restore_to_directory(self.backup, self.password, self.root / "wal-restored", self.work)
        self.assertEqual(result["format_version"], 2)
        with closing(sqlite3.connect(self.root / "wal-restored" / "yosaki.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertGreaterEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_interrupted_backup_restore_and_space_failure_keep_previous_file(self):
        self.create()
        original = self.backup.read_bytes()
        with self.assertRaisesRegex(backup_v2.BackupError, "destination_exists"):
            self.create()
        self.assertEqual(self.backup.read_bytes(), original)

        failed = self.root / "interrupted.ybackup"

        def interrupted(_snapshot, _manifest, partial, _passphrase):
            partial.write_bytes(b"incomplete")
            raise OSError("synthetic interruption")

        with patch.object(backup_v2, "_encode", side_effect=interrupted):
            with self.assertRaises(OSError):
                backup_v2.create_backup(self.source, failed, self.password, self.work)
        self.assertFalse(failed.exists())
        self.assertFalse(list(self.root.glob("*.partial")))
        with patch.object(backup_v2, "_check_space", side_effect=backup_v2.BackupError("insufficient_space")):
            with self.assertRaisesRegex(backup_v2.BackupError, "insufficient_space"):
                backup_v2.create_backup(self.source, failed, self.password, self.work)
        self.assertFalse(failed.exists())
        target = self.root / "interrupted-restore"
        with patch.object(backup_v2.shutil, "copyfileobj", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                backup_v2.restore_to_directory(self.backup, self.password, target, self.work)
        self.assertFalse(target.exists())
        self.assertEqual(self.backup.read_bytes(), original)

    def test_deadline_guard_does_not_publish_or_replace_existing_backup(self):
        self.create()
        original = self.backup.read_bytes()
        candidate = self.root / "timed-out.ybackup"
        def expired():
            raise backup_v2.BackupError("backup_timed_out")
        with self.assertRaisesRegex(backup_v2.BackupError, "backup_timed_out"):
            backup_v2.create_backup(self.source, candidate, self.password, self.work, expired)
        self.assertFalse(candidate.exists())
        self.assertEqual(self.backup.read_bytes(), original)

    def test_operational_limit_rejects_decompressed_database(self):
        self.create()
        target = self.root / "over-limit-restored"
        with self.assertRaisesRegex(backup_v2.BackupError, "operational_capacity_exceeded"):
            backup_v2.restore_to_directory(self.backup, self.password, target, self.work,
                                           max_database_bytes=1)
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(backup_v2.BackupError, "operational_capacity_exceeded"):
            backup_v2.create_backup(self.source, self.root / "over-limit.ybackup",
                                    self.password, self.work, max_database_bytes=1)
        self.assertFalse((self.root / "over-limit.ybackup").exists())

    def test_stale_temporary_cleanup_is_prefix_and_directory_bound(self):
        self.work.mkdir()
        stale = self.work / "backup-crashed"
        preserved = self.work / "user-archive"
        stale.mkdir()
        preserved.mkdir()
        backup_v2.mark_temporary_directory(stale)
        (stale / "snapshot.sqlite3").write_bytes(b"synthetic")
        (preserved / "keep.txt").write_text("synthetic")
        unmarked = self.work / "backup-personal-archive"
        unmarked.mkdir()
        (unmarked / "keep.txt").write_text("synthetic")
        old = time.time() - 25 * 3600
        os.utime(stale, (old, old))
        os.utime(preserved, (old, old))
        os.utime(unmarked, (old, old))
        backup_v2._work_dir(self.work)
        self.assertFalse(stale.exists())
        self.assertTrue((preserved / "keep.txt").exists())
        self.assertTrue((unmarked / "keep.txt").exists())

    def test_invalid_foreign_key_and_receipt_fingerprint_rejected(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("INSERT INTO messages VALUES(3,999,'orphan')")
            db.commit()
        with self.assertRaisesRegex(backup_v2.BackupError, "sqlite_foreign_key_failed"):
            self.create()
        self.assertFalse(self.backup.exists())
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("DELETE FROM messages WHERE id=3")
            db.executescript("""
                CREATE TABLE mv2_import_files(id TEXT PRIMARY KEY,character_id INTEGER,conversation_id INTEGER);
                CREATE TABLE mv2_import_receipts(message_id INTEGER,file_id TEXT,character_id INTEGER,content_sha256 TEXT);
                INSERT INTO mv2_import_files VALUES('file',1,1);
                INSERT INTO mv2_import_receipts VALUES(1,'file',1,'wrong-fingerprint');
            """)
            db.commit()
        with self.assertRaisesRegex(backup_v2.BackupError, "import_receipt_mismatch"):
            self.create()
        self.assertFalse(self.backup.exists())

    def test_valid_import_receipt_survives_restore(self):
        with closing(sqlite3.connect(self.source)) as db:
            content = db.execute("SELECT content FROM messages WHERE id=1").fetchone()[0]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            db.executescript("""
                CREATE TABLE mv2_import_files(id TEXT PRIMARY KEY,character_id INTEGER,conversation_id INTEGER);
                CREATE TABLE mv2_import_receipts(message_id INTEGER,file_id TEXT,character_id INTEGER,content_sha256 TEXT);
                INSERT INTO mv2_import_files VALUES('file',1,1);
            """)
            db.execute("INSERT INTO mv2_import_receipts VALUES(1,'file',1,?)", (digest,))
            db.commit()
        self.create()
        result = backup_v2.restore_to_directory(self.backup, self.password, self.root / "receipt-restored", self.work)
        self.assertEqual(result["tables"]["mv2_import_receipts"], 1)

    def test_m3_entry_review_and_p2_binding_survive_restore(self):
        from backend.app import memory_v2_derivation_schema, memory_v2_time_projection, memory_v2_sources

        full = self.root / "full.sqlite3"
        with closing(sqlite3.connect(full)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER REFERENCES characters(id));
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER REFERENCES conversations(id),
                    role TEXT,content TEXT,created_at TEXT,generation_status TEXT);
                CREATE TABLE imported_archives(conversation_id INTEGER,imported_at TEXT);
                CREATE TABLE model_profiles(id INTEGER PRIMARY KEY,api_key_encrypted TEXT);
                CREATE TABLE mcp_servers(id INTEGER PRIMARY KEY,auth_secret_encrypted TEXT,session_id TEXT,status TEXT,last_error TEXT);
                CREATE TABLE plugins(id INTEGER PRIMARY KEY);
                CREATE TABLE auth_sessions(id INTEGER PRIMARY KEY);
                CREATE TABLE auth_login_attempts(id INTEGER PRIMARY KEY);
                INSERT INTO users VALUES(1);
                INSERT INTO characters VALUES(1,1);
                INSERT INTO conversations VALUES(1,1);
                INSERT INTO messages VALUES(1,1,'user','合成经历','2026-01-01','completed');
            """)
            memory_v2_derivation_schema.init_db(db)
            memory_v2_time_projection.init_db(db)
            ref = memory_v2_sources.source(db, "message", "1")
            db.execute("INSERT INTO mv3_derivations VALUES('d1',1,'NARRATIVE','experience',NULL,'2026-01-01')")
            db.execute("""INSERT INTO mv3_versions(id,derivation_id,version,body,status,review_status,
                author_kind,input_hash,output_hash,rule_version,created_at)
                VALUES('v1','d1',1,'我说：合成经历','needs_review','unreviewed','user','input','output','test','2026-01-01')""")
            db.execute("INSERT INTO mv3_source_refs VALUES('v1',0,'message','1',1,'user','user_attested',?,?)",
                       (ref.content_hash, ref.state_hash))
            db.execute("INSERT INTO mv3_reviews VALUES('r1','v1',1,'synthetic-binding','2026-01-01')")
            db.execute("INSERT INTO mv3_entries VALUES('e1',1,'draft',NULL,'2026-01-01')")
            db.execute("""INSERT INTO mv3_entry_versions(id,entry_id,version,title,keywords_json,tags_json,
                synopsis,narrative_version_id,time_basis,source_hash,metadata_hash,status,review_status,created_at)
                VALUES('ev1','e1',1,'合成标题','[]','[]','合成梗概','v1','occurred','source','metadata',
                'draft','unreviewed','2026-01-01')""")
            db.execute("INSERT INTO mv3_entry_sources VALUES('ev1',0,'message','1',1,'user','user_attested',?,?)",
                       (ref.content_hash, ref.state_hash))
            db.execute("INSERT INTO mv3_entry_reviews VALUES('er1','ev1',1,'synthetic-binding','2026-01-01')")
            db.execute("""INSERT INTO mv3_time_digest_bindings VALUES('b1',1,'node','L1','2026-W01',
                'occurred_at','raw_sources_v1','scope','v1','2026-01-01')""")
            db.commit()
        output = self.root / "full.ybackup"
        backup_v2.create_backup(full, output, self.password, self.work)
        result = backup_v2.restore_to_directory(output, self.password, self.root / "full-restored", self.work)
        for table in ("mv3_versions", "mv3_reviews", "mv3_entries", "mv3_entry_versions",
                      "mv3_entry_sources", "mv3_entry_reviews", "mv3_time_digest_bindings"):
            self.assertEqual(result["tables"][table], 1, table)

    def test_isolated_api_prepare_download_verify(self):
        import httpx
        from fastapi import FastAPI, Request
        from backend.app import backup_v2_api, backup_v2_environment, backup_v2_limits

        app = FastAPI()

        @app.middleware("http")
        async def owner(request: Request, call_next):
            request.state.user = {"token_hash": request.headers.get("x-synthetic-owner", "first")}
            return await call_next(request)

        backup_v2_api.install(app, self.source, self.root)
        previous = os.environ.get("YOSAKI_H5A_ENABLED")
        os.environ["YOSAKI_H5A_ENABLED"] = "1"

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                prepared = await client.post("/api/system/backup/v2/prepare", data={"passphrase": self.password})
                self.assertEqual(prepared.status_code, 200, prepared.text)
                ticket = prepared.json()["download_ticket"]
                self.assertEqual((await client.post("/api/system/backup/v2/download", data={"ticket": ticket}, headers={"x-synthetic-owner": "other"})).status_code, 404)
                downloaded = await client.post("/api/system/backup/v2/download", data={"ticket": ticket})
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.headers["cache-control"], "no-store")
                self.assertEqual((await client.post("/api/system/backup/v2/download", data={"ticket": ticket})).status_code, 404)
                verified = await client.post("/api/system/backup/verify", data={"passphrase": self.password},
                                             files={"backup": ("synthetic.ybackup", downloaded.content)})
                self.assertEqual(verified.status_code, 200, verified.text)
                self.assertTrue(verified.json()["live_database_unchanged"])
                self.assertEqual(verified.json()["table_counts"]["messages"], 1)
                self.assertEqual((await client.post("/api/system/backup/verify", data={"passphrase": "wrong-password-123"},
                                                    files={"backup": ("synthetic.ybackup", downloaded.content)})).status_code, 422)
                pending = await client.post("/api/system/backup/v2/prepare", data={"passphrase": self.password})
                self.assertEqual(pending.status_code, 200)
                self.assertEqual((await client.post("/api/system/backup/v2/prepare", data={"passphrase": self.password})).status_code, 429)
                pending_ticket = pending.json()["download_ticket"]
                self.assertEqual((await client.post("/api/system/backup/v2/cancel", data={"ticket": pending_ticket},
                                                    headers={"x-synthetic-owner": "other"})).status_code, 404)
                self.assertEqual((await client.post("/api/system/backup/v2/cancel", data={"ticket": pending_ticket})).status_code, 200)
                self.assertEqual((await client.post("/api/system/backup/v2/download", data={"ticket": pending_ticket})).status_code, 404)
                expiring = await client.post("/api/system/backup/v2/prepare", data={"passphrase": self.password})
                self.assertEqual(expiring.status_code, 200)
                expiring_ticket = expiring.json()["download_ticket"]
                with backup_v2_api._lock:
                    path, owner_hash, _ = backup_v2_api._downloads[expiring_ticket]
                    backup_v2_api._downloads[expiring_ticket] = (path, owner_hash, time.time() - 1)
                self.assertEqual((await client.post("/api/system/backup/v2/download",
                                                    data={"ticket": expiring_ticket})).status_code, 404)
                self.assertFalse(path.exists())
                after_restart = await client.post("/api/system/backup/v2/prepare", data={"passphrase": self.password})
                self.assertEqual(after_restart.status_code, 200)
                lost_ticket = after_restart.json()["download_ticket"]
                with backup_v2_api._lock:
                    orphan, _, _ = backup_v2_api._downloads.pop(lost_ticket)
                self.assertEqual((await client.post("/api/system/backup/v2/download",
                                                    data={"ticket": lost_ticket})).status_code, 404)
                old = time.time() - 25 * 3600
                os.utime(orphan, (old, old))
                fresh = await client.post("/api/system/backup/v2/prepare", data={"passphrase": self.password})
                self.assertEqual(fresh.status_code, 200)
                self.assertFalse(orphan.exists())
                self.assertEqual((await client.post("/api/system/backup/v2/cancel",
                                                    data={"ticket": fresh.json()["download_ticket"]})).status_code, 200)

        try:
            with patch.object(backup_v2_environment, "validate_work_root", side_effect=lambda path: Path(path).mkdir(parents=True, exist_ok=True) or Path(path)), \
                    patch.object(backup_v2_limits, "require_process_lease"):
                asyncio.run(run())
        finally:
            if previous is None:
                os.environ.pop("YOSAKI_H5A_ENABLED", None)
            else:
                os.environ["YOSAKI_H5A_ENABLED"] = previous

    def test_running_backup_can_be_cancelled_without_publishing(self):
        import httpx
        from fastapi import FastAPI, Request
        from backend.app import backup_v2_api, backup_v2_environment, backup_v2_limits

        app = FastAPI()

        @app.middleware("http")
        async def owner(request: Request, call_next):
            request.state.user = {"token_hash": request.headers.get("x-synthetic-owner", "first")}
            return await call_next(request)

        backup_v2_api.install(app, self.source, self.root)
        started = threading.Event()

        def wait_for_abort(_source, _output, _phrase, _root, check, _limit):
            started.set()
            while True:
                check()
                time.sleep(0.01)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                pending = asyncio.create_task(client.post("/api/system/backup/v2/prepare",
                                                           data={"passphrase": self.password}))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertEqual((await client.post("/api/system/backup/v2/abort",
                                                    headers={"x-synthetic-owner": "other"})).status_code, 404)
                self.assertEqual((await client.post("/api/system/backup/v2/abort")).status_code, 200)
                result = await asyncio.wait_for(pending, 5)
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.json()["detail"], "backup_cancelled")
                self.assertFalse(list(self.root.glob(".backup-work/download-*.ybackup")))

        previous = os.environ.get("YOSAKI_H5A_ENABLED")
        os.environ["YOSAKI_H5A_ENABLED"] = "1"
        try:
            with patch.object(backup_v2_environment, "validate_work_root",
                              side_effect=lambda path: Path(path).mkdir(parents=True, exist_ok=True) or Path(path)), \
                    patch.object(backup_v2_limits, "require_process_lease"), \
                    patch.object(backup_v2, "create_backup", side_effect=wait_for_abort):
                asyncio.run(run())
        finally:
            if previous is None:
                os.environ.pop("YOSAKI_H5A_ENABLED", None)
            else:
                os.environ["YOSAKI_H5A_ENABLED"] = previous


if __name__ == "__main__":
    unittest.main()
