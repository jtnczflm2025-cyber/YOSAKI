"""Explicit Scribe quota migration. Never imported by a runtime entry point.

Use only after a fresh consistent backup and independent restore check. This
module does not import backend.app.main. --execute requires a schema digest.
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
from backend.app.voice import scribe_budget


NAMES = {"voice_scribe_owner_settings", "voice_scribe_owner_day_budget",
         "voice_scribe_attempts", "sqlite_autoindex_voice_scribe_owner_day_budget_1",
         "sqlite_autoindex_voice_scribe_attempts_1"}
PRIOR_DAY = "2026-11-15"
PRIOR_ATTEMPT = "4437288ae4034c7ea13f83c3231e1578"
PRIOR_ACTUAL_BYTES = 180_180


def objects(db: sqlite3.Connection) -> list[tuple]:
    return [tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def schema_hash(db: sqlite3.Connection) -> str:
    return hashlib.sha256(repr(objects(db)).encode()).hexdigest()


def old_data_hash(db: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for kind, name, _, _ in objects(db):
        if kind != "table" or name in NAMES:
            continue
        quoted = name.replace('"', '""')
        for row in db.execute(f'SELECT * FROM "{quoted}"'):
            digest.update(name.encode())
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def migrate(path: Path, expected_schema: str, execute: bool) -> dict:
    path = path.expanduser().resolve(strict=True)
    if path.name != "yosaki.sqlite3" or not path.is_file():
        raise ValueError("database_path_invalid")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as ro:
        ro.execute("PRAGMA query_only=ON")
        if (Path(ro.execute("PRAGMA database_list").fetchone()[2]).resolve() != path
                or schema_hash(ro) != expected_schema):
            raise ValueError("database_or_schema_drift")
        if (ro.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or ro.execute("PRAGMA foreign_key_check").fetchone()):
            raise ValueError("database_integrity_failed")
        before_data = old_data_hash(ro)
    if not execute:
        return {"database": str(path), "executed": False,
                "schema_sha256": expected_schema, "old_data_sha256": before_data}
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if schema_hash(db) != expected_schema or old_data_hash(db) != before_data:
            raise ValueError("database_changed_before_transaction")
        before = {name for _, name, _, _ in objects(db)}
        if before & NAMES:
            raise ValueError("scribe_daily_schema_already_present")
        scribe_budget.init_db(db)
        added = {name for _, name, _, _ in objects(db)} - before
        if added != NAMES:
            raise ValueError("unapproved_schema_delta")
        carried = False
        day, _ = scribe_budget.period()
        if day == PRIOR_DAY:
            if not db.execute("SELECT 1 FROM users WHERE id=1").fetchone():
                raise ValueError("prior_owner_missing")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.execute("""INSERT INTO voice_scribe_owner_day_budget
                (owner_id,local_day,attempts,connections,reserved_audio_bytes,
                 actual_audio_bytes,created_at,updated_at)
                VALUES (1,?,1,1,?,?,?,?)""",
                (day, scribe_budget.ATTEMPT_AUDIO_BYTES,
                 PRIOR_ACTUAL_BYTES, now, now))
            db.execute("""INSERT INTO voice_scribe_attempts
                (attempt_id,owner_id,local_day,reserved_audio_bytes,
                 actual_audio_bytes,created_at,updated_at)
                VALUES (?,1,?,?,?,?,?)""",
                (PRIOR_ATTEMPT, day, scribe_budget.ATTEMPT_AUDIO_BYTES,
                 PRIOR_ACTUAL_BYTES, now, now))
            carried = True
        if (old_data_hash(db) != before_data or
                db.execute("SELECT COUNT(*) FROM voice_scribe_owner_settings").fetchone()[0] != 0 or
                db.execute("PRAGMA foreign_key_check").fetchone()):
            raise ValueError("migration_validation_failed")
        db.commit()
    return {"database": str(path), "executed": True,
            "added_objects": sorted(added), "old_data_unchanged": True,
            "prior_r2_attempt_carried_for_today": carried,
            "microphone_default_enabled": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-schema-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    digest = args.expected_schema_sha256
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("schema_fingerprint_invalid")
    print(json.dumps(migrate(args.database, digest, args.execute), ensure_ascii=False))


if __name__ == "__main__":
    main()
