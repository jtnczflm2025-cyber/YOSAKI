"""Explicit, one-table Voice auto budget migration. Never imports main.py.

Requires a fresh expected schema fingerprint and ``--execute``. The caller
must first make and validate a consistent independent backup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend.app.voice import auto_budget


def objects(db):
    return db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()


def schema_hash(db) -> str:
    return hashlib.sha256(repr(objects(db)).encode()).hexdigest()


def old_data_hash(db) -> str:
    digest = hashlib.sha256()
    tables = [name for kind, name, _ in objects(db) if kind == "table" and
              name != "voice_auto_session_budget"]
    for name in tables:
        safe = name.replace('"', '""')
        for row in db.execute(f'SELECT * FROM "{safe}"'):
            digest.update(name.encode()); digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def migrate(path: Path, expected_schema: str, execute: bool) -> dict:
    path = path.expanduser().resolve(strict=True)
    if not path.is_file() or path.name != "yosaki.sqlite3":
        raise ValueError("database_path_invalid")
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as ro:
        ro.execute("PRAGMA query_only=ON")
        actual = Path(ro.execute("PRAGMA database_list").fetchone()[2]).resolve()
        if actual != path or schema_hash(ro) != expected_schema:
            raise ValueError("database_or_schema_drift")
        if ro.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or \
                ro.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("database_integrity_failed")
        old_data = old_data_hash(ro)
    if not execute:
        return {"database": str(path), "executed": False,
                "schema_sha256": expected_schema, "old_data_sha256": old_data}
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if schema_hash(db) != expected_schema or old_data_hash(db) != old_data:
            raise ValueError("database_changed_before_transaction")
        before = {(kind, name) for kind, name, _ in objects(db)}
        existing = db.execute("SELECT sql FROM sqlite_master WHERE name='voice_auto_session_budget'").fetchone()
        if existing is not None:
            raise ValueError("budget_table_already_present")
        auto_budget.init_db(db)
        added = {(kind, name) for kind, name, _ in objects(db)} - before
        if added != {("table", "voice_auto_session_budget"),
                     ("index", "sqlite_autoindex_voice_auto_session_budget_1")}:
            raise ValueError("unapproved_schema_delta")
        if old_data_hash(db) != old_data or \
                db.execute("SELECT COUNT(*) FROM voice_auto_session_budget").fetchone()[0] != 0 or \
                db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("migration_validation_failed")
        db.commit()
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as ro:
        new_schema = schema_hash(ro)
    return {"database": str(path), "executed": True,
            "added_objects": sorted(name for _, name in added),
            "old_data_unchanged": True, "new_schema_sha256": new_schema}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-schema-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if len(args.expected_schema_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.expected_schema_sha256):
        raise ValueError("schema_fingerprint_invalid")
    print(json.dumps(migrate(args.database, args.expected_schema_sha256, args.execute), ensure_ascii=False))


if __name__ == "__main__":
    main()
