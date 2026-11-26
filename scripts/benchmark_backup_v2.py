"""Synthetic H5-A capacity benchmark; creates and removes only its own temp DB."""

import argparse
import ctypes
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import tracemalloc
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import backup_v2, backup_v2_limits  # noqa: E402


def _windows_peak_working_set() -> int | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    value = Counters()
    value.cb = ctypes.sizeof(value)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    get_memory.restype = wintypes.BOOL
    ok = get_memory(get_process(), ctypes.byref(value), value.cb)
    return int(value.PeakWorkingSetSize) if ok else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=int, required=True)
    args = parser.parse_args()
    if not (64 <= args.mib <= 2048):
        parser.error("--mib must be between 64 and 2048")
    with tempfile.TemporaryDirectory(prefix="yosaki-h5a-capacity-") as temporary:
        root = Path(temporary)
        source = root / "synthetic.sqlite3"
        connection = sqlite3.connect(source)
        connection.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE characters(id INTEGER PRIMARY KEY);
            CREATE TABLE conversations(id INTEGER PRIMARY KEY);
            CREATE TABLE messages(id INTEGER PRIMARY KEY,content TEXT);
            CREATE TABLE model_profiles(id INTEGER PRIMARY KEY,api_key_encrypted TEXT);
            CREATE TABLE mcp_servers(id INTEGER PRIMARY KEY,auth_secret_encrypted TEXT,session_id TEXT,status TEXT,last_error TEXT);
            CREATE TABLE plugins(id INTEGER PRIMARY KEY);
            CREATE TABLE auth_sessions(id INTEGER PRIMARY KEY);
            CREATE TABLE auth_login_attempts(id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES(1);
        """)
        target = args.mib * 1024**2
        cursor = 0
        while source.stat().st_size < target:
            cursor += 1
            connection.execute("INSERT INTO messages VALUES(?,?)", (cursor, os.urandom(512 * 1024).hex()))
            if cursor % 16 == 0:
                connection.commit()
        connection.commit()
        connection.close()
        work = root / "work"
        work.mkdir()
        admitted_bytes = backup_v2_limits.preflight_create(source, root / "capacity.ybackup", work)
        base_free = shutil.disk_usage(root).free
        peak_disk_delta = [0]
        stop = threading.Event()

        def sample_disk():
            while not stop.wait(0.05):
                peak_disk_delta[0] = max(peak_disk_delta[0], base_free - shutil.disk_usage(root).free)

        monitor = threading.Thread(target=sample_disk, daemon=True)
        monitor.start()
        started = time.perf_counter()
        tracemalloc.start()
        try:
            manifest = backup_v2.create_backup(source, root / "capacity.ybackup",
                                               "synthetic-capacity-password", work)
            backup_seconds = time.perf_counter() - started
            started = time.perf_counter()
            restored = backup_v2.restore_to_directory(root / "capacity.ybackup",
                "synthetic-capacity-password", root / "restored", work)
            restore_seconds = time.perf_counter() - started
            _, python_peak = tracemalloc.get_traced_memory()
            windows_peak = _windows_peak_working_set()
        finally:
            tracemalloc.stop()
            stop.set()
            monitor.join(timeout=1)
        if restored["database_sha256"] != manifest["database_sha256"]:
            raise RuntimeError("restored SHA-256 mismatch")
        print(json.dumps({"requested_mib": args.mib, "source_bytes": source.stat().st_size,
                          "admission_source_bytes": admitted_bytes,
                          "encrypted_target_attested": False,
                          "backup_bytes": (root / "capacity.ybackup").stat().st_size,
                          "restored_bytes": (root / "restored" / "yosaki.sqlite3").stat().st_size,
                          "backup_seconds": round(backup_seconds, 3),
                          "restore_seconds": round(restore_seconds, 3),
                          "python_peak_bytes": python_peak,
                          "process_peak_working_set_bytes": windows_peak,
                          "sampled_peak_extra_disk_bytes": peak_disk_delta[0],
                          "sha256_match": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
