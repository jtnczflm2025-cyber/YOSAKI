"""Explicit one-batch preparation. Never imports main.py or runs at startup.

Daily use of ``--apply`` requires a separate migration/service approval. The
isolated mode exists solely for testing this exact transaction on a temp DB.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from backend.app.voice import continuous_batch


PROJECT = Path(__file__).resolve().parents[1]
DAILY_DB = (PROJECT / "data" / "yosaki.sqlite3").resolve()


def prepare(database: Path, mode: str, batch_id: str, *, apply: bool) -> dict:
    path = database.expanduser().resolve(strict=True)
    temporary_root = Path(os.getenv("TEMP") or tempfile.gettempdir()).resolve()
    if mode == "development":
        if path != DAILY_DB:
            raise ValueError("daily_database_path_mismatch")
    elif mode == "isolated":
        if not path.is_relative_to(temporary_root) or path == DAILY_DB:
            raise ValueError("isolated_database_path_required")
    else:
        raise ValueError("mode_invalid")
    if not continuous_batch.BATCH_ID.fullmatch(batch_id):
        raise ValueError("batch_id_invalid")

    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        owner = db.execute("SELECT id FROM users").fetchall()
        character = db.execute("SELECT id,enabled,default_model_id FROM characters WHERE id=1").fetchone()
        model = db.execute("SELECT id,enabled,protocol FROM model_profiles WHERE id=5").fetchone()
        if (len(owner) != 1 or not character or not character["enabled"] or
                not model or not model["enabled"] or model["protocol"] != "chatgpt_plan"):
            raise ValueError("batch_identity_or_model_binding_invalid")
        present = db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
            AND name='voice_continuous_batch_receipts'""").fetchone() is not None
        if not apply:
            return {"mode": mode, "database": str(path), "schema_present": present,
                    "batch_prepared": False}

        db.execute("BEGIN IMMEDIATE")
        before = {(row["type"], row["name"]) for row in db.execute(
            "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_sequence%'")}
        changes_before = db.total_changes
        continuous_batch.init_db(db)
        reference = sqlite3.connect(":memory:")
        try:
            continuous_batch.init_db(reference)
            expected = reference.execute("""SELECT sql FROM sqlite_master
                WHERE name='voice_continuous_batch_receipts'""").fetchone()[0]
        finally:
            reference.close()
        actual = db.execute("""SELECT sql FROM sqlite_master
            WHERE name='voice_continuous_batch_receipts'""").fetchone()[0]
        if actual != expected:
            raise ValueError("batch_schema_conflict")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conversation_id = db.execute("""INSERT INTO conversations
            (character_id,title,model_profile_id,created_at,updated_at)
            VALUES (1,'连续通话验收',5,?,?)""", (stamp, stamp)).lastrowid
        continuous_batch.prepare_in_transaction(db, batch_id, owner[0]["id"],
                                                1, conversation_id, 5)
        after = {(row["type"], row["name"]) for row in db.execute(
            "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_sequence%'")}
        expected_new = set() if present else {
            ("table", "voice_continuous_batch_receipts"),
            ("index", "sqlite_autoindex_voice_continuous_batch_receipts_1"),
            ("index", "sqlite_autoindex_voice_continuous_batch_receipts_2"),
        }
        if after - before != expected_new or before - after:
            raise ValueError("batch_schema_extra_change")
        if db.total_changes - changes_before != 2:
            raise ValueError("batch_unexpected_record_change")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("batch_integrity_failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("batch_foreign_key_failed")
        db.execute("COMMIT")
        return {"mode": mode, "database": str(path), "schema_created": not present,
                "conversation_id": conversation_id, "batch_prepared": True}
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("isolated", "development"))
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.database, args.mode, args.batch_id,
                             apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
