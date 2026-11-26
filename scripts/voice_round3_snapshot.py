"""Consistent daily SQLite backup and independent restore verification, no main import."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "data" / "yosaki.sqlite3").resolve()


def digest(db: sqlite3.Connection) -> dict[str, str]:
    result = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                              "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        data = "\n".join(sorted(repr(tuple(row)) for row in db.execute(
            f"SELECT * FROM {quoted}")))
        result[name] = hashlib.sha256(data.encode()).hexdigest()
    return result


def inspect(db: sqlite3.Connection) -> dict:
    schema = sorted(tuple(row) for row in db.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"))
    return {"schema_sha256": hashlib.sha256(repr(schema).encode()).hexdigest(),
            "table_row_sha256": digest(db),
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(list(db.execute("PRAGMA foreign_key_check")))}


def run() -> dict:
    root = Path(os.environ["LOCALAPPDATA"]) / "YOSAKI" / "backups"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = (root / f"voice-round3-{stamp}").resolve()
    if not folder.is_relative_to(root.resolve()):
        raise RuntimeError("backup_path_invalid")
    folder.mkdir(parents=True, exist_ok=False)
    backup = folder / "daily-consistent.sqlite3"
    restore = folder / "independent-restore.sqlite3"
    source = sqlite3.connect(SOURCE.as_uri() + "?mode=ro", uri=True)
    source.execute("PRAGMA query_only=ON")
    try:
        before = inspect(source)
        with sqlite3.connect(backup) as output:
            source.backup(output)
    finally:
        source.close()
    backup_db = sqlite3.connect(backup)
    try:
        after = inspect(backup_db)
        if after != before:
            raise RuntimeError("backup_mismatch")
        with sqlite3.connect(restore) as output:
            backup_db.backup(output)
    finally:
        backup_db.close()
    restored = sqlite3.connect(restore.as_uri() + "?mode=ro", uri=True)
    restored.execute("PRAGMA query_only=ON")
    try:
        verified = inspect(restored)
    finally:
        restored.close()
    if verified != before or before["integrity"] != "ok" or before["foreign_key_errors"]:
        raise RuntimeError("independent_restore_mismatch")
    return {"backup": str(backup), "independent_restore": str(restore),
            "table_count": len(before["table_row_sha256"]),
            "schema_sha256": before["schema_sha256"],
            "table_row_sha256": before["table_row_sha256"],
            "integrity": "ok", "foreign_key_errors": 0}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
