"""Conservative H5-A2 admission limits for the isolated single-owner service."""

from __future__ import annotations

import os
import shutil
import threading
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .backup_v2 import BackupError


# Format limits remain in backup_v2.py. These smaller admission limits are
# provisional until an encrypted target volume passes capacity acceptance.
OPERATIONAL_DATABASE_MAX = 1024**3
OPERATIONAL_FILE_MAX = OPERATIONAL_DATABASE_MAX + 64 * 1024**2
SPACE_MARGIN = 128 * 1024**2
_heavy_operation = threading.BoundedSemaphore(1)
_lease_guard = threading.Lock()
_worker_leases: dict[Path, object] = {}


def require_single_worker() -> None:
    # The download ticket registry is deliberately process-local. Do not
    # enable it when the launcher announces more than one worker.
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = os.environ.get(name)
        if raw and raw.strip() != "1":
            raise BackupError("backup_requires_single_worker")


def require_process_lease(root: Path) -> None:
    """Only one process may serve H5 tickets for a protected work root."""
    root = Path(root).resolve()
    with _lease_guard:
        if root in _worker_leases:
            return
        lease = (root / ".h5a-worker.lock").open("a+b")
        try:
            lease.seek(0)
            if lease.read(1) != b"1":
                lease.seek(0)
                lease.write(b"1")
                lease.flush()
            lease.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lease.close()
            raise BackupError("backup_requires_single_worker") from exc
        _worker_leases[root] = lease


def acquire_operation() -> bool:
    return _heavy_operation.acquire(blocking=False)


def release_operation() -> None:
    _heavy_operation.release()


@contextmanager
def heavy_operation():
    if not acquire_operation():
        raise BackupError("backup_operation_busy")
    try:
        yield
    finally:
        release_operation()


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _same_volume(left: Path, right: Path) -> bool:
    return left.stat().st_dev == right.stat().st_dev


def preflight_create(source: Path, destination: Path, work_root: Path) -> int:
    source, destination, work_root = Path(source), Path(destination), Path(work_root)
    size = source.stat().st_size
    wal = source.with_name(source.name + "-wal")
    if wal.is_file():
        size += wal.stat().st_size
    if size > OPERATIONAL_DATABASE_MAX:
        raise BackupError("operational_capacity_exceeded")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Snapshot + authenticated compressed stream + SQLite candidate + verified
    # copy can coexist in work_root; encrypted partial resides at destination.
    work_need = 4 * size + SPACE_MARGIN
    destination_need = 2 * size + SPACE_MARGIN  # Allows incompressible overhead.
    if _same_volume(work_root, destination.parent):
        if _free(work_root) < work_need + destination_need:
            raise BackupError("insufficient_combined_space")
    elif _free(work_root) < work_need or _free(destination.parent) < destination_need:
        raise BackupError("insufficient_combined_space")
    return size


def preflight_upload(work_root: Path, declared_body_bytes: int) -> None:
    if declared_body_bytes < 1 or declared_body_bytes > OPERATIONAL_FILE_MAX + 64 * 1024:
        raise BackupError("operational_capacity_exceeded")
    # Starlette may spool the still-encrypted multipart file in system temp.
    # Count that file separately if the protected plaintext work root is on
    # another volume. Candidate and verified DBs stay in work_root.
    work_root = Path(work_root)
    spool = Path(tempfile.gettempdir()).resolve()
    work_need = 2 * declared_body_bytes + 2 * OPERATIONAL_DATABASE_MAX + SPACE_MARGIN
    spool_need = declared_body_bytes + SPACE_MARGIN
    if _same_volume(work_root, spool):
        if _free(work_root) < work_need + spool_need:
            raise BackupError("insufficient_combined_space")
    elif _free(work_root) < work_need or _free(spool) < spool_need:
        raise BackupError("insufficient_combined_space")


def preflight_restore(encrypted: Path, work_root: Path) -> None:
    size = Path(encrypted).stat().st_size
    if size > OPERATIONAL_FILE_MAX:
        raise BackupError("operational_capacity_exceeded")
    # The encrypted upload already exists; these are additional work files.
    if _free(Path(work_root)) < size + 2 * OPERATIONAL_DATABASE_MAX + SPACE_MARGIN:
        raise BackupError("insufficient_combined_space")
