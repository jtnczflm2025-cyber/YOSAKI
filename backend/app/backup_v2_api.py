"""Opt-in H5-A backup endpoints; no live database restore endpoint."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from . import backup_v2, backup_v2_environment, backup_v2_limits


ENABLED = "YOSAKI_H5A_ENABLED"
DOWNLOAD_TTL = 600
_downloads: dict[str, tuple[Path, str, float]] = {}
_lock = threading.Lock()
_active: tuple[str, threading.Event] | None = None
OPERATION_TIMEOUT_SECONDS = 1800
ACTIONABLE_ERRORS = {
    "unsafe_work_directory_acl": "备份工作目录权限过宽。请在加密卷上创建仅当前账户、SYSTEM 和管理员可访问的专用目录。",
    "encrypted_volume_unverified": "无法确认备份工作目录位于受保护的加密卷；请检查 BitLocker/EFS 状态或查询权限。",
    "work_directory_protection_unverified": "无法验证备份目录的访问权限；请检查目录 ACL 和系统查询权限。",
    "backup_requires_single_worker": "H5 备份仅支持一个后端 worker；请检查启动参数及运行中的实例。",
    "insufficient_combined_space": "备份及独立恢复所需的同卷或跨卷临时空间不足；请释放空间后重试。",
    "operational_capacity_exceeded": "文件超过当前 H5 隔离验收的操作容量上限。",
}


def _begin_operation(token_hash: str):
    global _active
    event = threading.Event()
    with _lock:
        _active = (token_hash, event)
    deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS

    def check():
        if event.is_set():
            raise backup_v2.BackupError("backup_cancelled")
        if time.monotonic() >= deadline:
            raise backup_v2.BackupError("backup_timed_out")

    return event, check


def _end_operation(event: threading.Event) -> None:
    global _active
    with _lock:
        if _active is not None and _active[1] is event:
            _active = None


class _OversizedBody(Exception):
    pass


class _BodyTimedOut(Exception):
    pass


class BackupAdmission:
    """Reject oversized multipart bodies before Starlette reads them."""

    def __init__(self, app, database: Path, data_dir: Path, session_validator=None,
                 allowed_origins=None):
        self.app, self.database, self.data_dir = app, Path(database), Path(data_dir)
        self.session_validator = session_validator
        self.allowed_origins = allowed_origins

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in {
            "/api/system/backup/v2/prepare", "/api/system/backup/v2/download",
            "/api/system/backup/verify", "/api/system/backup/v2/cancel",
        }:
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = (backup_v2_limits.OPERATIONAL_FILE_MAX + 64 * 1024
                 if path.endswith("/verify") else 8192)

        async def reject(status: int, reason: str):
            await JSONResponse({"detail": ACTIONABLE_ERRORS.get(reason, reason), "code": reason}, status_code=status,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)

        if os.environ.get(ENABLED) != "1":
            return await reject(503, "H5-A 备份入口尚未启用")
        origin = dict(scope.get("headers", [])).get(b"origin")
        if origin and self.allowed_origins is not None and origin.decode("utf-8", "replace") not in self.allowed_origins:
            return await reject(403, "origin_not_allowed")
        if self.session_validator is not None and self.session_validator(Request(scope)) is None:
            return await reject(401, "需要登录后继续")
        try:
            backup_v2_limits.require_single_worker()
            lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
            if any(name.lower() == b"transfer-encoding" for name, _ in scope.get("headers", [])):
                return await reject(400, "backup_transfer_encoding_unsupported")
            if not lengths:
                return await reject(411, "backup_content_length_required")
            if len(lengths) != 1:
                return await reject(400, "invalid_content_length")
            try:
                declared = int(lengths[0])
            except ValueError:
                return await reject(400, "invalid_content_length")
            if declared < 0 or declared > limit:
                return await reject(413, "backup_body_too_large")
            if not backup_v2_limits.acquire_operation():
                return await reject(429, "backup_operation_busy")
            try:
                root = backup_v2_environment.validate_work_root(_work_root(self.data_dir))
                backup_v2_limits.require_process_lease(root)
                if path.endswith("/prepare") or path.endswith("/verify"):
                    if path.endswith("/prepare"):
                        backup_v2_limits.preflight_create(self.database, root / "preflight.ybackup", root)
                    else:
                        backup_v2_limits.preflight_upload(root, declared)
                received = 0
                started = False
                body_complete = False
                body_deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS

                async def bounded_receive():
                    nonlocal received, body_complete
                    if body_complete:
                        return await receive()
                    remaining = body_deadline - time.monotonic()
                    if remaining <= 0:
                        raise _BodyTimedOut()
                    try:
                        message = await asyncio.wait_for(receive(), timeout=min(60, remaining))
                    except asyncio.TimeoutError as exc:
                        raise _BodyTimedOut() from exc
                    if message.get("type") == "http.request":
                        received += len(message.get("body", b""))
                        if received > declared or received > limit:
                            raise _OversizedBody()
                        if not message.get("more_body", False):
                            body_complete = True
                    return message

                async def track_send(message):
                    nonlocal started
                    if message.get("type") == "http.response.start":
                        started = True
                    await send(message)

                try:
                    return await self.app(scope, bounded_receive, track_send)
                except _OversizedBody:
                    if not started:
                        return await reject(413, "backup_body_too_large")
                    raise
                except _BodyTimedOut:
                    if not started:
                        return await reject(408, "backup_body_timed_out")
                    raise
            finally:
                backup_v2_limits.release_operation()
        except backup_v2.BackupError as exc:
            code = (503 if exc.code in {"encrypted_volume_unverified", "unsafe_work_directory_acl",
                                         "work_directory_protection_unverified", "backup_requires_single_worker"}
                    else 413 if exc.code == "operational_capacity_exceeded" else 507)
            return await reject(code, exc.code)


def _require_enabled() -> None:
    if os.environ.get(ENABLED) != "1":
        raise HTTPException(503, "H5-A 备份入口尚未启用")


def _work_root(data_dir: Path) -> Path:
    return Path(os.environ.get("YOSAKI_BACKUP_WORK_DIR", str(data_dir / ".backup-work"))).expanduser().resolve()


def _cleanup() -> None:
    now = time.time()
    with _lock:
        expired = [key for key, (_, _, until) in _downloads.items() if until <= now]
        paths = [_downloads.pop(key)[0] for key in expired]
    for path in paths:
        path.unlink(missing_ok=True)


def _sweep_orphan_downloads(root: Path) -> None:
    """Remove only H5's expired encrypted downloads left by a crashed worker."""
    cutoff = time.time() - 24 * 3600
    if not root.exists():
        return
    for path in root.glob("download-*.ybackup"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def install(app, database: Path, data_dir: Path, session_validator=None, allowed_origins=None):
    app.add_middleware(BackupAdmission, database=database, data_dir=data_dir,
                       session_validator=session_validator, allowed_origins=allowed_origins)
    @app.post("/api/system/backup/v2/prepare")
    def prepare(request: Request, passphrase: str = Form(...)):
        _require_enabled()
        _cleanup()
        with _lock:
            if _downloads:
                raise HTTPException(429, "已有待下载备份；请下载、取消或等待票据过期")
        try:
            root = backup_v2_environment.validate_work_root(_work_root(data_dir))
        except backup_v2.BackupError as exc:
            raise HTTPException(503, exc.code) from exc
        _sweep_orphan_downloads(root)
        output = root / ("download-" + secrets.token_hex(16) + ".ybackup")
        event, check = _begin_operation(request.state.user["token_hash"])
        try:
            manifest = backup_v2.create_backup(database, output, passphrase, root, check,
                                               backup_v2_limits.OPERATIONAL_DATABASE_MAX)
        except backup_v2.BackupError as exc:
            raise HTTPException(422, exc.code) from exc
        finally:
            _end_operation(event)
        ticket = secrets.token_urlsafe(32)
        with _lock:
            _downloads[ticket] = (output, request.state.user["token_hash"], time.time() + DOWNLOAD_TTL)
        response = JSONResponse({"download_ticket": ticket,
                                 "expires_in": DOWNLOAD_TTL, "format_version": 2,
                                 "database_bytes": manifest["database_bytes"]})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/system/backup/v2/download")
    def download(request: Request, ticket: str = Form(...)):
        _require_enabled()
        _cleanup()
        with _lock:
            record = _downloads.get(ticket)
            if record is None or record[1] != request.state.user["token_hash"]:
                raise HTTPException(404, "下载已失效")
            path, _, _ = _downloads.pop(ticket)
        if not path.is_file():
            raise HTTPException(404, "下载已失效")
        return FileResponse(path, media_type="application/vnd.yosaki.backup",
                            filename="yosaki-backup-v2.ybackup",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.post("/api/system/backup/v2/cancel")
    def cancel(request: Request, ticket: str = Form(...)):
        _require_enabled()
        with _lock:
            record = _downloads.get(ticket)
            if record is None or record[1] != request.state.user["token_hash"]:
                raise HTTPException(404, "下载已失效")
            path, _, _ = _downloads.pop(ticket)
        path.unlink(missing_ok=True)
        return JSONResponse({"cancelled": True}, headers={"Cache-Control": "no-store"})

    @app.post("/api/system/backup/v2/abort")
    def abort(request: Request):
        """Owner may cancel a running prepare/verify; never deletes a published backup."""
        _require_enabled()
        with _lock:
            if _active is None or _active[0] != request.state.user["token_hash"]:
                raise HTTPException(404, "当前会话没有可取消的备份任务")
            _active[1].set()
        return JSONResponse({"cancellation_requested": True}, headers={"Cache-Control": "no-store"})

    @app.post("/api/system/backup/verify")
    async def verify(request: Request, passphrase: str = Form(...), backup: UploadFile = File(...)):
        _require_enabled()
        try:
            root = backup_v2_environment.validate_work_root(_work_root(data_dir))
        except backup_v2.BackupError as exc:
            raise HTTPException(503, exc.code) from exc
        _sweep_orphan_downloads(root)
        event, check = _begin_operation(request.state.user["token_hash"])
        try:
            with tempfile.TemporaryDirectory(prefix="upload-", dir=root) as scratch:
                backup_v2.mark_temporary_directory(Path(scratch))
                encrypted = Path(scratch) / "uploaded.ybackup"
                total = 0
                with encrypted.open("xb") as output:
                    while block := await asyncio.wait_for(backup.read(backup_v2.CHUNK), timeout=60):
                        check()
                        total += len(block)
                        if total > backup_v2_limits.OPERATIONAL_FILE_MAX:
                            raise HTTPException(413, "备份文件超过容量上限")
                        if shutil.disk_usage(root).free < len(block) + 64 * 1024**2:
                            raise HTTPException(507, "隔离恢复空间不足")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                backup_v2_limits.preflight_restore(encrypted, root)
                result = await asyncio.to_thread(backup_v2.restore_to_directory, encrypted, passphrase,
                                                 Path(scratch) / "restored", root, check,
                                                 backup_v2_limits.OPERATIONAL_DATABASE_MAX)
        except (backup_v2.BackupError, asyncio.TimeoutError) as exc:
            raise HTTPException(422, exc.code if isinstance(exc, backup_v2.BackupError) else "backup_upload_timed_out") from exc
        finally:
            _end_operation(event)
        response = JSONResponse({"valid": True, "format_version": result["format_version"],
                                 "exported_at": result["exported_at"],
                                 "database_sha256": result["database_sha256"],
                                 "table_counts": result["tables"],
                                 "source_audit": result["source_audit"],
                                 "live_database_unchanged": True})
        response.headers["Cache-Control"] = "no-store"
        return response
