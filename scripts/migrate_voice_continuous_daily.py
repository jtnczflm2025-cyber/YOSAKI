"""Explicit, audited daily-call schema migration; never imports main.py."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from backend.app.voice import continuous_daily


PROJECT = Path(__file__).resolve().parents[1]
DAILY_DB = (PROJECT / "data" / "yosaki.sqlite3").resolve()
OBJECTS = {
    ("table", "voice_continuous_daily_calls"),
    ("index", "sqlite_autoindex_voice_continuous_daily_calls_1"),
    ("index", "voice_continuous_daily_active_owner"),
}


def migrate(database: Path, mode: str, *, apply: bool = False) -> dict:
    path = database.expanduser().resolve(strict=True)
    temp = Path(os.getenv("TEMP") or tempfile.gettempdir()).resolve()
    if mode == "development":
        if path != DAILY_DB:
            raise ValueError("daily_database_path_mismatch")
    elif mode == "isolated":
        if path == DAILY_DB or not path.is_relative_to(temp):
            raise ValueError("isolated_database_path_required")
    else:
        raise ValueError("mode_invalid")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    try:
        before = {(r[0], r[1]) for r in db.execute(
            "SELECT type,name FROM sqlite_master")}
        if before & OBJECTS:
            raise ValueError("daily_call_schema_already_present")
        if not apply:
            return {"database": str(path), "mode": mode, "ready": True,
                    "applied": False}
        reference = sqlite3.connect(":memory:")
        try:
            continuous_daily.init_db(reference)
            expected_sql = {r[0]: r[1] for r in reference.execute(
                "SELECT name,sql FROM sqlite_master WHERE name IN "
                "('voice_continuous_daily_calls','voice_continuous_daily_active_owner')")}
        finally:
            reference.close()
        db.execute("BEGIN IMMEDIATE")
        changes = db.total_changes
        continuous_daily.init_db(db)
        after = {(r[0], r[1]) for r in db.execute(
            "SELECT type,name FROM sqlite_master")}
        actual_sql = {r[0]: r[1] for r in db.execute(
            "SELECT name,sql FROM sqlite_master WHERE name IN "
            "('voice_continuous_daily_calls','voice_continuous_daily_active_owner')")}
        if after - before != OBJECTS or before - after or actual_sql != expected_sql:
            raise ValueError("daily_call_schema_drift")
        if db.total_changes != changes:
            raise ValueError("daily_call_unapproved_record_write")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or \
                db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("daily_call_integrity_failed")
        db.execute("COMMIT")
        return {"database": str(path), "mode": mode, "applied": True,
                "new_objects": sorted([list(item) for item in OBJECTS]),
                "record_writes": 0}
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--mode", choices=("development", "isolated"), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.database, args.mode, apply=args.apply),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
