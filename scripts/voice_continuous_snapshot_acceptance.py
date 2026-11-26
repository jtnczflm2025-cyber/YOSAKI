"""Read-only daily snapshot -> explicit batch migration -> independent restore.

The source database is only opened read-only. All writes are confined to a
fresh TEMP directory, which is retained for the separately reviewed cleanup.
No provider is contacted and no user content or credential is printed.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing, contextmanager
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile

from scripts.prepare_voice_continuous_batch import DAILY_DB, prepare


@contextmanager
def open_db(path: Path, *, readonly: bool = True):
    suffix = "?mode=ro" if readonly else "?mode=rw"
    db = sqlite3.connect(path.resolve().as_uri() + suffix, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        db.close()


def objects(db: sqlite3.Connection) -> dict[tuple[str, str], str | None]:
    return {(row["type"], row["name"]): row["sql"] for row in db.execute(
        """SELECT type,name,sql FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%' OR name LIKE 'sqlite_autoindex_%'""")}


def data_fingerprints(db: sqlite3.Connection, *, old_conversation_max: int) -> dict[str, tuple[int, str]]:
    result = {}
    tables = [row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for name in tables:
        if name == "voice_continuous_batch_receipts":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        where = " WHERE id<=?" if name == "conversations" else ""
        params = (old_conversation_max,) if where else ()
        hashes = sorted(hashlib.sha256(repr(tuple(row)).encode("utf-8")).digest()
                        for row in db.execute(f"SELECT * FROM {quoted}{where}", params))
        digest = hashlib.sha256(b"".join(hashes)).hexdigest()
        result[name] = (len(hashes), digest)
    return result


def integrity(db: sqlite3.Connection) -> bool:
    return (db.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and
            db.execute("PRAGMA foreign_key_check").fetchone() is None)


STARTUP = r'''
import json, os
from pathlib import Path
from fastapi.testclient import TestClient
from scripts.yosaki_runtime import Runtime, preflight
data_dir = Path(os.environ['YOSAKI_DATA_DIR']).resolve()
database = data_dir / 'yosaki.sqlite3'
preflight(Runtime('development', data_dir, database, 18800), voice_acceptance=True)
os.environ['YOSAKI_VOICE_ENABLED'] = '1'
from backend.app import main
with TestClient(main.app) as client:
    health = client.get('/health').status_code
with main.connect() as db:
    actual = db.execute('PRAGMA database_list').fetchone()[2]
print('SNAPSHOT_STARTUP=' + json.dumps({'health': health, 'database': actual}))
'''


def main() -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    run_dir = Path(tempfile.mkdtemp(prefix="yosaki-batch-snapshot-", dir=temp_root)).resolve()
    if not run_dir.is_relative_to(temp_root) or run_dir == temp_root:
        raise RuntimeError("isolated_path_invalid")
    copied = run_dir / "yosaki.sqlite3"
    restored_dir = run_dir / "restored"
    restored_dir.mkdir()
    restored = restored_dir / "yosaki.sqlite3"
    batch_id = secrets.token_hex(16)
    with open_db(DAILY_DB) as source:
        with closing(sqlite3.connect(copied)) as target:
            source.backup(target)
    with open_db(copied) as db:
        before_objects = objects(db)
        old_conversation_max = db.execute("SELECT COALESCE(MAX(id),0) FROM conversations").fetchone()[0]
        before_data = data_fingerprints(db, old_conversation_max=old_conversation_max)
        if not integrity(db):
            raise RuntimeError("snapshot_integrity_failed")
    prepared = prepare(copied, "isolated", batch_id, apply=True)
    with open_db(copied) as db:
        after_objects = objects(db)
        expected_new = {
            ("table", "voice_continuous_batch_receipts"),
            ("index", "sqlite_autoindex_voice_continuous_batch_receipts_1"),
            ("index", "sqlite_autoindex_voice_continuous_batch_receipts_2"),
        }
        if (after_objects.keys() - before_objects.keys() != expected_new or
                before_objects.keys() - after_objects.keys() or
                any(after_objects[key] != sql for key, sql in before_objects.items())):
            raise RuntimeError("unexpected_schema_change")
        if data_fingerprints(db, old_conversation_max=old_conversation_max) != before_data:
            raise RuntimeError("old_data_changed")
        if not integrity(db):
            raise RuntimeError("migrated_integrity_failed")
        new_conversations = db.execute("SELECT COUNT(*) FROM conversations WHERE id>?",
                                       (old_conversation_max,)).fetchone()[0]
        receipt_rows = db.execute("SELECT COUNT(*) FROM voice_continuous_batch_receipts").fetchone()[0]
        if new_conversations != 1 or receipt_rows != 1:
            raise RuntimeError("unexpected_new_rows")
        with closing(sqlite3.connect(restored)) as target:
            db.backup(target)
    with open_db(restored) as db:
        if (objects(db) != after_objects or
                data_fingerprints(db, old_conversation_max=old_conversation_max) != before_data or
                not integrity(db)):
            raise RuntimeError("independent_restore_mismatch")
    env = os.environ.copy()
    env.update({
        "YOSAKI_DATA_DIR": str(restored_dir),
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_VOICE_ENABLED": "0",
        "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
        "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
        "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
        "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "1",
        "YOSAKI_VOICE_CONTINUOUS_BATCH_ID": batch_id,
        "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
        "YOSAKI_CHATGPT_PLAN_ENABLED": "1",
    })
    result = subprocess.run([sys.executable, "-c", STARTUP], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=60)
    marker = next((line.removeprefix("SNAPSHOT_STARTUP=") for line in result.stdout.splitlines()
                   if line.startswith("SNAPSHOT_STARTUP=")), None)
    if result.returncode != 0 or marker is None:
        raise RuntimeError(f"isolated_startup_failed_exit_{result.returncode}")
    started = json.loads(marker)
    if started != {"health": 200, "database": str(restored.resolve())}:
        raise RuntimeError("isolated_startup_wrong_database_or_health")
    with open_db(restored) as db:
        if (objects(db) != after_objects or
                data_fingerprints(db, old_conversation_max=old_conversation_max) != before_data or
                not integrity(db)):
            raise RuntimeError("startup_created_unapproved_schema_or_changed_old_data")
    print(json.dumps({
        "isolated_directory": str(run_dir),
        "source_read_only": str(DAILY_DB),
        "snapshot_integrity": True,
        "migration_new_objects": sorted(f"{kind}:{name}" for kind, name in expected_new),
        "old_tables_unchanged": len(before_data),
        "old_rows_unchanged": True,
        "new_conversations": new_conversations,
        "new_receipts": receipt_rows,
        "independent_restore": True,
        "startup_health": started["health"],
        "startup_database": started["database"],
        "startup_no_extra_schema_or_old_data_change": True,
        "migration_schema_created": prepared["schema_created"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
