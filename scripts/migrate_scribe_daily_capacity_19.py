"""Explicit Scribe settings capacity migration; never imports main.py."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from backend.app.voice import scribe_budget


DAILY_DB = (Path(__file__).resolve().parents[1] / "data" / "yosaki.sqlite3").resolve()
TABLE = "voice_scribe_owner_settings"
COLUMNS = "owner_id,enabled,attempts,connections,audio_seconds,revision,created_at,updated_at"
OLD_CHECKS = ("attempts BETWEEN 0 AND 18", "connections BETWEEN 0 AND 18",
              "audio_seconds BETWEEN 0 AND 270")
NEW_CHECKS = ("attempts BETWEEN 0 AND 19", "connections BETWEEN 0 AND 19",
              "audio_seconds BETWEEN 0 AND 285")


def _schema(db: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    return {name: (kind, sql) for kind, name, sql in db.execute(
        "SELECT type,name,sql FROM sqlite_master")}


def _rows_digest(db: sqlite3.Connection, name: str) -> str:
    quoted = '"' + name.replace('"', '""') + '"'
    rows = sorted(repr(tuple(row)) for row in db.execute(f"SELECT * FROM {quoted}"))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def migrate(database: Path, mode: str, *, apply: bool = False) -> dict:
    path = database.resolve(strict=True)
    if mode == "development":
        if path != DAILY_DB:
            raise ValueError("daily_database_path_mismatch")
    elif mode == "isolated":
        if path == DAILY_DB or not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError("isolated_database_path_required")
    else:
        raise ValueError("mode_invalid")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    try:
        before = _schema(db)
        old_sql = (before.get(TABLE) or (None, None))[1] or ""
        if not all(check in old_sql for check in OLD_CHECKS) or any(
                check in old_sql for check in NEW_CHECKS):
            raise ValueError("old_scribe_settings_schema_drift")
        expected = sqlite3.connect(":memory:")
        try:
            expected.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
            scribe_budget.init_db(expected)
            target_sql = _schema(expected)[TABLE][1]
        finally:
            expected.close()
        if not apply:
            return {"database": str(path), "ready": True, "applied": False}
        old_rows = list(db.execute(f"SELECT {COLUMNS} FROM {TABLE} ORDER BY owner_id"))
        other_tables = [name for name, (kind, _) in before.items()
                        if kind == "table" and name != TABLE and not name.startswith("sqlite_")]
        old_digests = {name: _rows_digest(db, name) for name in other_tables}
        db.execute("BEGIN IMMEDIATE")
        db.execute(f"ALTER TABLE {TABLE} RENAME TO {TABLE}_capacity_old")
        scribe_budget.init_db(db)
        db.execute(f"INSERT INTO {TABLE} ({COLUMNS}) SELECT {COLUMNS} FROM {TABLE}_capacity_old")
        db.execute(f"DROP TABLE {TABLE}_capacity_old")
        after = _schema(db)
        if after[TABLE][1] != target_sql or {
                key: value for key, value in after.items() if key != TABLE} != {
                key: value for key, value in before.items() if key != TABLE}:
            raise ValueError("unapproved_schema_change")
        if list(db.execute(f"SELECT {COLUMNS} FROM {TABLE} ORDER BY owner_id")) != old_rows:
            raise ValueError("scribe_settings_record_changed")
        if any(_rows_digest(db, name) != digest for name, digest in old_digests.items()):
            raise ValueError("other_record_changed")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or list(
                db.execute("PRAGMA foreign_key_check")):
            raise ValueError("integrity_failed")
        db.execute("COMMIT")
        return {"database": str(path), "applied": True,
                "settings_rows_preserved": len(old_rows),
                "schema_change": "Scribe settings CHECK 18/18/270 to 19/19/285"}
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--mode", choices=("development", "isolated"), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.database, args.mode, apply=args.apply), ensure_ascii=False))
