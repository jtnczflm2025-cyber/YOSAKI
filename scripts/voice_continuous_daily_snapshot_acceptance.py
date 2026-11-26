"""Check the opt-in daily call migration on a fresh, ephemeral SQLite backup."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing

from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from scripts.migrate_voice_continuous_daily import migrate
from scripts.yosaki_runtime import (DAILY_VOICE_FLAGS, Runtime,
                                    _continuous_daily_ready, preflight)


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = (PROJECT / "data" / "yosaki.sqlite3").resolve()


def summary(path: Path) -> tuple[set[tuple], dict[str, str]]:
    with closing(sqlite3.connect(path)) as db:
        schema = set(db.execute("SELECT type,name,sql FROM sqlite_master"))
        records = {}
        names = [row[0] for row in db.execute("""SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'""")]
        for name in names:
            quoted = name.replace('"', '""')
            rows = sorted(repr(tuple(row)) for row in db.execute(f'SELECT * FROM "{quoted}"'))
            records[name] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        return schema, records


def main() -> None:
    if not SOURCE.is_file():
        raise RuntimeError("daily_database_missing")
    with tempfile.TemporaryDirectory(prefix="yosaki-daily-snapshot-",
                                     dir=os.getenv("TEMP")) as root:
        copy = Path(root) / "yosaki.sqlite3"
        source = sqlite3.connect(SOURCE.as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        before_schema, before_records = summary(copy)
        key = Fernet.generate_key()
        (Path(root) / ".secret_key").write_bytes(key)
        (Path(root) / ".chatgpt_plan").write_bytes(Fernet(key).encrypt(json.dumps({
            "account": {"access_token": "fake", "refresh_token": "fake",
                        "scopes": ["chatgpt.tokens.use.direct"]},
        }).encode()))
        os.environ.pop("YOSAKI_SECRET_KEY", None)
        os.environ.update(DAILY_VOICE_FLAGS)
        os.environ.update({
            "YOSAKI_DATA_DIR": root,
            "YOSAKI_RUN_MODE": "development",
            "YOSAKI_VOICE_ENABLED": "0",
            "ELEVENLABS_API_KEY": "fake-local-only",
        })
        runtime = Runtime("development", Path(root), copy, 18888)
        with closing(sqlite3.connect(copy)) as db:
            schema_preexisting = _continuous_daily_ready(db)
        if not schema_preexisting:
            try:
                preflight(runtime, inherited_data_dir=None, voice_acceptance=True)
            except ValueError as exc:
                if "日常连续通话表缺失或结构不符" not in str(exc):
                    raise
            else:
                raise RuntimeError("missing_daily_schema_was_not_rejected")
            migration = migrate(copy, "isolated", apply=True)
            migrated_schema, migrated_records = summary(copy)
            if not all(migrated_records.get(name) == digest
                       for name, digest in before_records.items()):
                raise RuntimeError("migration_old_record_changed")
        else:
            migration = {"record_writes": 0}
            migrated_schema, migrated_records = before_schema, before_records
        with closing(sqlite3.connect(copy)) as db:
            if not _continuous_daily_ready(db):
                raise RuntimeError("daily_schema_preflight_failed")
        preflight(runtime, inherited_data_dir=None, voice_acceptance=True)
        os.environ["YOSAKI_VOICE_ENABLED"] = "1"
        from backend.app import main as app_module  # Import after snapshot path is set.
        from backend.app import chatgpt_plan
        if not chatgpt_plan._enabled():
            raise RuntimeError("isolated_chatgpt_plan_gate_disabled")
        with TestClient(app_module.app) as client:
            if client.get("/health").status_code != 200:
                raise RuntimeError("isolated_health_failed")
        after_schema, after_records = summary(copy)
        with closing(sqlite3.connect(copy)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
            actual_path = db.execute("PRAGMA database_list").fetchone()[2]
        print(json.dumps({
            "source_database": str(SOURCE),
            "actual_isolated_database": str(Path(actual_path).resolve()),
            "source_tables": len(before_records),
            "migration_new_objects": sorted(row[1] for row in migrated_schema - before_schema),
            "migration_record_writes": migration["record_writes"],
            "schema_preexisting": schema_preexisting,
            "missing_schema_rejected_before_import": not schema_preexisting,
            "startup_extra_objects": sorted(row[1] for row in after_schema - migrated_schema),
            "startup_old_records_unchanged": all(after_records.get(name) == digest
                                                 for name, digest in migrated_records.items()),
            "integrity": integrity,
            "foreign_key_issues": foreign_keys,
            "health_status": 200,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
