"""H5-A2 admission checks with synthetic paths; no project database import."""

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import backup_v2_api, backup_v2_limits
from backend.app.backup_v2 import BackupError


class BackupLimitsTests(unittest.TestCase):
    def test_same_volume_space_is_added_not_checked_separately(self):
        with tempfile.TemporaryDirectory(prefix="h5a2-space-") as temp:
            root = Path(temp)
            source = root / "synthetic.sqlite3"
            source.write_bytes(b"synthetic")
            with patch.object(backup_v2_limits, "_free", return_value=200 * 1024**2):
                with self.assertRaisesRegex(BackupError, "insufficient_combined_space"):
                    backup_v2_limits.preflight_create(source, root / "out.ybackup", root)

    def test_cross_volume_checks_both_destinations(self):
        with tempfile.TemporaryDirectory(prefix="h5a2-space-") as temp:
            root = Path(temp)
            source = root / "synthetic.sqlite3"
            source.write_bytes(b"synthetic")
            with patch.object(backup_v2_limits, "_same_volume", return_value=False), \
                    patch.object(backup_v2_limits, "_free", side_effect=[500 * 1024**2, 1]):
                with self.assertRaisesRegex(BackupError, "insufficient_combined_space"):
                    backup_v2_limits.preflight_create(source, root / "out.ybackup", root)

    def test_wal_bytes_count_toward_admission(self):
        with tempfile.TemporaryDirectory(prefix="h5a2-wal-space-") as temp:
            root = Path(temp)
            source = root / "synthetic.sqlite3"
            source.write_bytes(b"database")
            source.with_name(source.name + "-wal").write_bytes(b"w" * 128)
            with patch.object(backup_v2_limits, "OPERATIONAL_DATABASE_MAX", 100):
                with self.assertRaisesRegex(BackupError, "operational_capacity_exceeded"):
                    backup_v2_limits.preflight_create(source, root / "out.ybackup", root)

    def test_single_worker_environment_and_concurrent_operation(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "2"}):
            with self.assertRaisesRegex(BackupError, "backup_requires_single_worker"):
                backup_v2_limits.require_single_worker()
        self.assertTrue(backup_v2_limits.acquire_operation())
        try:
            self.assertFalse(backup_v2_limits.acquire_operation())
        finally:
            backup_v2_limits.release_operation()

    def test_process_lease_rejects_another_worker(self):
        with tempfile.TemporaryDirectory(prefix="h5a2-worker-") as temp:
            root = Path(temp)
            code = ("import sys; from pathlib import Path; "
                    "from backend.app.backup_v2_limits import require_process_lease; "
                    "require_process_lease(Path(sys.argv[1])); print('ready', flush=True); sys.stdin.read(1)")
            child = subprocess.Popen([sys.executable, "-c", code, str(root)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready", child.stderr.read() if child.poll() else "")
                with self.assertRaisesRegex(BackupError, "backup_requires_single_worker"):
                    backup_v2_limits.require_process_lease(root)
            finally:
                child.stdin.write("x")
                child.stdin.flush()
                child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()
                child.stderr.close()

    def test_header_rejected_before_multipart_handler(self):
        reached = []

        async def inner(scope, receive, send):
            reached.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        admission = backup_v2_api.BackupAdmission(inner, Path("unused"), Path("unused"))

        async def call(headers, body=b""):
            sent = []
            async def send(message):
                sent.append(message)
            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}
            await admission({"type": "http", "path": "/api/system/backup/verify",
                             "method": "POST", "headers": headers}, receive, send)
            return sent[0]["status"]

        with patch.dict(os.environ, {"YOSAKI_H5A_ENABLED": "1"}):
            self.assertEqual(asyncio.run(call([])), 411)
            self.assertEqual(asyncio.run(call([(b"content-length", b"999999999999")])), 413)
            self.assertEqual(asyncio.run(call([(b"content-length", b"1"),
                                               (b"content-length", b"2")])), 400)
            self.assertEqual(asyncio.run(call([(b"transfer-encoding", b"chunked"),
                                               (b"content-length", b"1")])), 400)
        self.assertEqual(reached, [])

    def test_auth_and_busy_reject_before_work_directory_touch(self):
        async def inner(scope, receive, send):
            raise AssertionError("handler should not run")

        async def call(admission):
            sent = []
            async def send(message):
                sent.append(message)
            async def receive():
                return {"type": "http.request", "body": b"x", "more_body": False}
            await admission({"type": "http", "path": "/api/system/backup/verify", "method": "POST",
                             "headers": [(b"content-length", b"1")]}, receive, send)
            return sent[0]["status"]

        denied = backup_v2_api.BackupAdmission(inner, Path("unused"), Path("unused"),
                                               session_validator=lambda _: None)
        allowed = backup_v2_api.BackupAdmission(inner, Path("unused"), Path("unused"),
                                                session_validator=lambda _: {"id": 1})
        with patch.dict(os.environ, {"YOSAKI_H5A_ENABLED": "1"}), \
                patch.object(backup_v2_api.backup_v2_environment, "validate_work_root",
                             side_effect=AssertionError("work root checked before auth")):
            self.assertEqual(asyncio.run(call(denied)), 401)
            self.assertTrue(backup_v2_limits.acquire_operation())
            try:
                self.assertEqual(asyncio.run(call(allowed)), 429)
            finally:
                backup_v2_limits.release_operation()

    def test_declared_length_cannot_hide_larger_stream(self):
        async def inner(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        admission = backup_v2_api.BackupAdmission(inner, Path("unused"), Path("unused"))

        async def run():
            sent = []
            async def send(message):
                sent.append(message)
            async def receive():
                return {"type": "http.request", "body": b"too long", "more_body": False}
            await admission({"type": "http", "path": "/api/system/backup/verify",
                             "method": "POST", "headers": [(b"content-length", b"1")]},
                            receive, send)
            return sent[0]["status"]

        with patch.dict(os.environ, {"YOSAKI_H5A_ENABLED": "1"}), \
                patch.object(backup_v2_api.backup_v2_environment, "validate_work_root", return_value=Path.cwd()), \
                patch.object(backup_v2_limits, "require_process_lease"), \
                patch.object(backup_v2_limits, "preflight_upload"):
            self.assertEqual(asyncio.run(run()), 413)

    def test_slow_body_times_out_before_handler(self):
        async def inner(scope, receive, send):
            await receive()
            raise AssertionError("slow body should be rejected")

        admission = backup_v2_api.BackupAdmission(inner, Path("unused"), Path("unused"))

        async def run():
            sent = []
            async def send(message):
                sent.append(message)
            async def receive():
                await asyncio.sleep(0.2)
                return {"type": "http.request", "body": b"x", "more_body": False}
            await admission({"type": "http", "path": "/api/system/backup/verify",
                             "method": "POST", "headers": [(b"content-length", b"1")]},
                            receive, send)
            return sent[0]["status"]

        with patch.dict(os.environ, {"YOSAKI_H5A_ENABLED": "1"}), \
                patch.object(backup_v2_api.backup_v2_environment, "validate_work_root", return_value=Path.cwd()), \
                patch.object(backup_v2_limits, "require_process_lease"), \
                patch.object(backup_v2_limits, "preflight_upload"), \
                patch.object(backup_v2_api, "OPERATION_TIMEOUT_SECONDS", 0.01):
            self.assertEqual(asyncio.run(run()), 408)


if __name__ == "__main__":
    unittest.main()
