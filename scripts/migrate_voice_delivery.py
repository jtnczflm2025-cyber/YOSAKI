"""Explicit Voice settings and automatic-expression migration."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile

from backend.app.voice import auto_budget, automatic_expression, continuous_daily, expression_product, scribe_budget
from backend.app.voice.budget_schema import BUDGET_TABLES, expanded_ddl
from scripts.migrate_voice_continuous_settings import DAILY_DB, _other_rows, _schema


REBUILD = (*BUDGET_TABLES, "voice_expression_audio")
NEW_TABLE = "voice_auto_expression_decisions"
NEW_OBJECTS = {NEW_TABLE, f"sqlite_autoindex_{NEW_TABLE}_1"}


def _reference():
    with closing(sqlite3.connect(":memory:")) as db:
        auto_budget.init_daily_db(db)
        scribe_budget.init_db(db)
        continuous_daily.init_db_v2(db)
        expression_product.init_db(db)
        automatic_expression.init_db(db)
        return _schema(db)


def _rows(db, names):
    return {name: sorted(repr(tuple(row)) for row in db.execute(f'SELECT * FROM "{name}"'))
            for name in names}


def migrate(path: Path, mode: str, *, apply=False):
    path = path.resolve(strict=True)
    if mode == "development":
        if path != DAILY_DB:
            raise ValueError("voice_daily_path_mismatch")
    elif mode != "isolated" or path == DAILY_DB or not path.is_relative_to(
            Path(tempfile.gettempdir()).resolve()):
        raise ValueError("voice_isolated_path_required")
    reference = _reference()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        schema = _schema(db)
        target = {}
        for name in REBUILD:
            actual_type, actual_sql = schema[name]
            expected_type, expected_sql = reference[name]
            updated = expanded_ddl(name, actual_sql)
            allowed = {expected_sql, expanded_ddl(name, expected_sql)}
            if name == "voice_expression_audio":
                allowed.add(expected_sql.replace("CHECK(mode IN ('thoughtful','auto'))",
                                                 "CHECK(mode='thoughtful')"))
            if actual_type != expected_type or actual_sql not in allowed:
                raise ValueError(f"voice_schema_drift:{name}")
            target[name] = actual_type, updated
        for name in ("voice_continuous_daily_active_owner", "voice_expression_audio_cache"):
            if schema.get(name) != reference[name]:
                raise ValueError(f"voice_schema_drift:{name}")
        for name in NEW_OBJECTS & schema.keys():
            if schema[name] != reference[name]:
                raise ValueError(f"voice_schema_drift:{name}")
        if db.execute("SELECT 1 FROM voice_continuous_daily_calls WHERE ended_at IS NULL").fetchone():
            raise ValueError("voice_active_call_migration_forbidden")
        if not apply:
            return {"ready": True, "applied": False}
        rows = _rows(db, REBUILD)
        all_rows = _other_rows(db)
        for name, (_, ddl) in target.items():
            if schema[name][1] == ddl:
                continue
            old_name = name + "_voice_migration"
            indexes = list(db.execute("""SELECT sql FROM sqlite_master
                WHERE type='index' AND tbl_name=? AND sql IS NOT NULL""", (name,)))
            db.execute(f'ALTER TABLE "{name}" RENAME TO "{old_name}"')
            db.execute(ddl)
            db.execute(f'INSERT INTO "{name}" SELECT * FROM "{old_name}"')
            db.execute(f'DROP TABLE "{old_name}"')
            for (index_ddl,) in indexes:
                db.execute(index_ddl)
        automatic_expression.init_db(db)
        if _schema(db) != {**schema, **target, **{name: reference[name] for name in NEW_OBJECTS}}:
            raise ValueError("voice_unapproved_schema_change")
        updated_rows = _other_rows(db)
        if NEW_TABLE not in all_rows:
            updated_rows.pop(NEW_TABLE)
        if rows != _rows(db, REBUILD) or all_rows != updated_rows:
            raise ValueError("voice_existing_rows_changed")
        if (db.execute("PRAGMA integrity_check").fetchone() != ("ok",) or
                db.execute("PRAGMA foreign_key_check").fetchone()):
            raise ValueError("voice_integrity_failed")
        return {"ready": True, "applied": True, "rebuilt": list(REBUILD),
                "added": sorted(NEW_OBJECTS), "existing_tables_verified": len(all_rows) + 2}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("development", "isolated"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.database, args.mode, apply=args.apply), ensure_ascii=False))
