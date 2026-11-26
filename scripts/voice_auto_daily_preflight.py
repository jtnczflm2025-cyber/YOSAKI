"""Read-only daily source, consistent backup, independent restore, isolated startup.

This script never imports main against the daily database and never calls a
provider. The two private SQLite copies are retained for review/recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "data" / "yosaki.sqlite3").resolve()
ROOT_OUT = (Path(os.environ["LOCALAPPDATA"]) / "YOSAKI" / "voice-auto-preflight").resolve()


def observe(path: Path) -> dict:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        actual = Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve()
        if actual != path.resolve():
            raise RuntimeError("database_path_mismatch")
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        objects = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        schema = hashlib.sha256(repr(objects).encode()).hexdigest()
        digest = hashlib.sha256()
        counts = {}
        for _, name, _ in objects:
            if not any(kind == "table" and item == name for kind, item, _ in objects):
                continue
            safe = name.replace('"', '""')
            rows = db.execute(f'SELECT * FROM "{safe}"')
            count = 0
            for row in rows:
                digest.update(name.encode()); digest.update(repr(tuple(row)).encode())
                count += 1
            counts[name] = count
        return {"path": str(actual), "integrity": integrity,
                "foreign_key_errors": len(foreign_keys), "schema_sha256": schema,
                "data_sha256": digest.hexdigest(), "counts": counts}


def main() -> None:
    if not SOURCE.is_file():
        raise RuntimeError("daily_database_missing")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT_OUT / stamp
    if folder.exists():
        raise RuntimeError("preflight_directory_exists")
    backup_dir, restore_dir = folder / "backup", folder / "data"
    backup_dir.mkdir(parents=True)
    restore_dir.mkdir()
    backup, restored = backup_dir / "yosaki.sqlite3", restore_dir / "yosaki.sqlite3"
    with sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True) as origin:
        origin.execute("PRAGMA query_only=ON")
        with sqlite3.connect(backup) as target:
            origin.backup(target)
    before = observe(backup)
    if before["integrity"] != "ok" or before["foreign_key_errors"]:
        raise RuntimeError("backup_validation_failed")
    with sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True) as origin:
        with sqlite3.connect(restored) as target:
            origin.backup(target)
    independent = observe(restored)
    if any(before[key] != independent[key] for key in
           ("integrity", "foreign_key_errors", "schema_sha256", "data_sha256", "counts")):
        raise RuntimeError("independent_restore_mismatch")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.migrate_voice_auto_daily_budget import migrate
    from scripts.yosaki_runtime import configuration, preflight
    runtime = configuration("development", project_root=folder,
                            isolated_development_fixture=True)
    os.environ.update({"YOSAKI_VOICE_ENABLED": "0",
        "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
        "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"})
    try:
        preflight(runtime, voice_acceptance=True)
    except ValueError as exc:
        if "日常自动朗读额度表尚未受控迁移" not in str(exc):
            raise
    else:
        raise RuntimeError("unmigrated_daily_schema_was_accepted")
    migration = migrate(restored, before["schema_sha256"], True)
    expected_added = {"voice_auto_owner_day_budget", "voice_auto_owner_settings",
                      "sqlite_autoindex_voice_auto_owner_day_budget_1"}
    if set(migration["added_objects"]) != expected_added:
        raise RuntimeError("unexpected_daily_migration_objects")
    migrated = observe(restored)
    if (migrated["integrity"] != "ok" or migrated["foreign_key_errors"]
            or migrated["counts"].get("voice_auto_owner_day_budget") !=
               migration["carried_active_owner_count"]
            or migrated["counts"].get("voice_auto_owner_settings") != 0):
        raise RuntimeError("isolated_daily_migration_invalid")
    preflight(runtime, voice_acceptance=True)
    os.environ.update({"YOSAKI_DATA_DIR": str(restore_dir),
        "YOSAKI_VOICE_ENABLED": "1", "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0", "YOSAKI_CHATGPT_PLAN_ENABLED": "0",
        "YOSAKI_SCRIBE_DRAFT_ISOLATED": "0"})
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.app import main as app_module
    from fastapi.testclient import TestClient
    with app_module.connect() as db:
        actual = Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve()
    if actual != restored:
        raise RuntimeError("isolated_import_path_mismatch")
    with TestClient(app_module.app) as client:
        health = client.get("/health")
        if health.status_code != 200:
            raise RuntimeError("isolated_health_failed")
    after = observe(restored)
    drift = [key for key in ("schema_sha256", "data_sha256", "counts")
             if migrated[key] != after[key]]
    print(json.dumps({"source": str(SOURCE), "backup": str(backup),
        "independent_restore": str(restored), "database_path": str(actual),
        "backup_integrity": before["integrity"],
        "backup_foreign_key_errors": before["foreign_key_errors"],
        "restore_matches_backup": True, "isolated_health": health.status_code,
        "preflight_rejected_before_migration": True,
        "preflight_passed_after_migration": True,
        "migration_added_objects": migration["added_objects"],
        "carried_active_owner_count": migration["carried_active_owner_count"],
        "old_data_unchanged": migration["old_data_unchanged"],
        "startup_drift": drift, "table_count": len(before["counts"]),
        "voice_auto_budget_table_present": "voice_auto_session_budget" in before["counts"],
        "voice_profiles": before["counts"].get("voice_profiles"),
        "voice_privacy_consents": before["counts"].get("voice_privacy_consents")},
        ensure_ascii=False))
    if drift:
        raise RuntimeError("isolated_startup_changed_schema_or_data")


if __name__ == "__main__":
    main()
