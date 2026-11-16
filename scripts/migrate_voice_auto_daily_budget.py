"""Explicit future owner/day quota migration; never imports main.py.

The caller must validate a fresh, independently restored backup first.
This script is not called by any normal startup path.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend.app.voice import auto_budget


def objects(db: sqlite3.Connection):
    return [tuple(row) for row in db.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name")]


def schema_hash(db: sqlite3.Connection) -> str:
    return hashlib.sha256(repr(objects(db)).encode()).hexdigest()


def data_hash(db: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for kind, name, _ in objects(db):
        if kind != "table" or name in ("voice_auto_owner_day_budget", "voice_auto_owner_settings"):
            continue
        safe_name = name.replace('"', '""')
        for row in db.execute(f'SELECT * FROM "{safe_name}"'):
            digest.update(name.encode())
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def migrate(path: Path, expected_schema: str, execute: bool) -> dict:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file() or path.name != "yosaki.sqlite3":
        raise ValueError("database_path_invalid")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as ro:
        ro.execute("PRAGMA query_only=ON")
        actual = Path(ro.execute("PRAGMA database_list").fetchone()[2]).resolve()
        if actual != path or schema_hash(ro) != expected_schema:
            raise ValueError("database_or_schema_drift")
        if (ro.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or ro.execute("PRAGMA foreign_key_check").fetchone()):
            raise ValueError("database_integrity_failed")
        before_data = data_hash(ro)
    if not execute:
        return {"database": str(path), "executed": False,
                "schema_sha256": expected_schema, "old_data_sha256": before_data}
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if schema_hash(db) != expected_schema or data_hash(db) != before_data:
            raise ValueError("database_changed_before_transaction")
        before = {(kind, name) for kind, name, _ in objects(db)}
        if any(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
               for name in ("voice_auto_owner_day_budget", "voice_auto_owner_settings")):
            raise ValueError("daily_budget_table_already_present")
        auto_budget.init_daily_db(db)
        # Carry forward usage still visible in active login ledgers. This is
        # conservative for sessions spanning days; deleted sessions cannot be
        # reconstructed from the pre-daily schema.
        local_day, _ = auto_budget.daily_period()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        expected_usage = db.execute("""SELECT owner_id,SUM(synth_jobs),
            SUM(request_count),SUM(character_count) FROM voice_auto_session_budget
            WHERE synth_jobs>0 OR request_count>0 OR character_count>0
            GROUP BY owner_id ORDER BY owner_id""").fetchall()
        db.execute("""INSERT INTO voice_auto_owner_day_budget
            (owner_id,local_day,synth_jobs,request_count,character_count,created_at,updated_at)
            SELECT owner_id,?,SUM(synth_jobs),SUM(request_count),
                   SUM(character_count),?,?
            FROM voice_auto_session_budget
            WHERE synth_jobs>0 OR request_count>0 OR character_count>0
            GROUP BY owner_id""", (local_day, now, now))
        added = {(kind, name) for kind, name, _ in objects(db)} - before
        expected = {("table", "voice_auto_owner_day_budget"),
                    ("index", "sqlite_autoindex_voice_auto_owner_day_budget_1"),
                    ("table", "voice_auto_owner_settings")}
        if added != expected:
            raise ValueError("unapproved_schema_delta")
        actual_usage = db.execute("""SELECT owner_id,synth_jobs,request_count,
            character_count FROM voice_auto_owner_day_budget
            WHERE local_day=? ORDER BY owner_id""", (local_day,)).fetchall()
        if (data_hash(db) != before_data
                or actual_usage != expected_usage
                or db.execute("SELECT COUNT(*) FROM voice_auto_owner_settings").fetchone()[0] != 0
                or db.execute("PRAGMA foreign_key_check").fetchone()):
            raise ValueError("migration_validation_failed")
        db.commit()
    return {"database": str(path), "executed": True,
            "added_objects": sorted(name for _, name in added),
            "old_data_unchanged": True,
            "carried_active_owner_count": len(expected_usage)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-schema-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if (len(args.expected_schema_sha256) != 64
            or any(c not in "0123456789abcdef" for c in args.expected_schema_sha256)):
        raise ValueError("schema_fingerprint_invalid")
    print(json.dumps(migrate(args.database, args.expected_schema_sha256,
                             args.execute), ensure_ascii=False))


if __name__ == "__main__":
    main()
