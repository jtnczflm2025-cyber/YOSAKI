"""Explicit call-limit migration. Never imports main.py or contacts providers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from backend.app.voice import continuous_daily


PROJECT = Path(__file__).resolve().parents[1]
DAILY_DB = (PROJECT / "data" / "yosaki.sqlite3").resolve()
CALL_COLUMNS = (
    "call_id", "owner_id", "character_id", "conversation_id",
    "model_profile_id", "started_at", "ended_at", "stt_connections",
    "model_turns", "tts_requests", "tts_characters", "last_message_id",
    "claim_trace_json",
)
SCHEMA_NAMES = {
    "voice_continuous_daily_calls", "voice_continuous_daily_active_owner",
    "voice_continuous_owner_settings",
    "sqlite_autoindex_voice_continuous_daily_calls_1",
}


def _schema(db: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    return {row[1]: (row[0], row[2]) for row in db.execute(
        "SELECT type,name,sql FROM sqlite_master")}


def _other_rows(db: sqlite3.Connection) -> dict[str, str]:
    names = [row[0] for row in db.execute("""SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%' AND
        name NOT IN ('voice_continuous_daily_calls','voice_continuous_owner_settings')""")]
    return {name: hashlib.sha256("\n".join(sorted(repr(tuple(row)) for row in
        db.execute('SELECT * FROM "' + name.replace('"', '""') + '"'))).encode()).hexdigest()
        for name in names}


def migrate(database: Path, mode: str, *, apply: bool = False) -> dict:
    path = database.expanduser().resolve(strict=True)
    if mode == "development":
        if path != DAILY_DB:
            raise ValueError("daily_database_path_mismatch")
    elif mode == "isolated":
        if path == DAILY_DB or not path.is_relative_to(
                Path(os.getenv("TEMP") or tempfile.gettempdir()).resolve()):
            raise ValueError("isolated_database_path_required")
    else:
        raise ValueError("mode_invalid")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    db.execute("PRAGMA foreign_keys=ON")
    reference_v1 = sqlite3.connect(":memory:")
    reference_v2 = sqlite3.connect(":memory:")
    try:
        continuous_daily.init_db(reference_v1)
        continuous_daily.init_db_v2(reference_v2)
        expected_v1, expected_v2 = _schema(reference_v1), _schema(reference_v2)
        before_schema = _schema(db)
        for name in ("voice_continuous_daily_calls", "voice_continuous_daily_active_owner"):
            if before_schema.get(name) != expected_v1.get(name):
                raise ValueError("old_call_schema_drift")
        if "voice_continuous_owner_settings" in before_schema:
            raise ValueError("call_settings_already_present")
        if db.execute("SELECT 1 FROM voice_continuous_daily_calls WHERE ended_at IS NULL LIMIT 1").fetchone():
            raise ValueError("active_call_migration_forbidden")
        if not apply:
            return {"database": str(path), "mode": mode, "ready": True,
                    "applied": False}
        db.execute("BEGIN IMMEDIATE")
        before_rows = list(db.execute("SELECT " + ",".join(CALL_COLUMNS) +
                                      " FROM voice_continuous_daily_calls ORDER BY call_id"))
        other_before = _other_rows(db)
        db.execute("DROP INDEX voice_continuous_daily_active_owner")
        db.execute("ALTER TABLE voice_continuous_daily_calls RENAME TO voice_continuous_daily_calls_v1_migration")
        continuous_daily.init_db_v2(db)
        db.execute("""INSERT INTO voice_continuous_daily_calls (""" +
                   ",".join(CALL_COLUMNS) + ") SELECT " + ",".join(CALL_COLUMNS) +
                   " FROM voice_continuous_daily_calls_v1_migration")
        db.execute("DROP TABLE voice_continuous_daily_calls_v1_migration")
        after_schema = _schema(db)
        for name in SCHEMA_NAMES:
            if after_schema.get(name) != expected_v2.get(name):
                raise ValueError("new_call_schema_drift")
        other_names = set(before_schema) - SCHEMA_NAMES
        if {name: before_schema[name] for name in other_names} != {
                name: after_schema.get(name) for name in other_names}:
            raise ValueError("unapproved_schema_change")
        after_rows = list(db.execute("SELECT " + ",".join(CALL_COLUMNS) +
                                     " FROM voice_continuous_daily_calls ORDER BY call_id"))
        if after_rows != before_rows or _other_rows(db) != other_before:
            raise ValueError("old_record_changed")
        if db.execute("SELECT COUNT(*) FROM voice_continuous_owner_settings").fetchone()[0]:
            raise ValueError("unexpected_settings_record")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or \
                db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("integrity_failed")
        db.execute("COMMIT")
        return {"database": str(path), "mode": mode, "applied": True,
                "old_call_rows_preserved": len(before_rows),
                "new_table": "voice_continuous_owner_settings",
                "new_call_columns": ["limit_rounds", "limit_seconds",
                                     "limit_tts_requests", "limit_tts_characters"]}
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        reference_v1.close()
        reference_v2.close()
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
