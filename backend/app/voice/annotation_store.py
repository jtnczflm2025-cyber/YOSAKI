"""Isolated append-only annotation revisions and one-attempt claims.

Never opened by main.py. Refuses the project tree and non-lab databases.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from .annotation_planner import AnnotationError

ANNOTATION_TABLE_SQL = '                CREATE TABLE IF NOT EXISTS annotation_plans(\n                    id TEXT PRIMARY KEY,owner_id INTEGER NOT NULL,input_sha TEXT NOT NULL,\n                    prepared TEXT NOT NULL,state TEXT NOT NULL,reason TEXT,\n                    revision INTEGER NOT NULL DEFAULT 0,model_attempts INTEGER NOT NULL DEFAULT 0,\n                    usage TEXT,UNIQUE(owner_id,input_sha));\n                CREATE TABLE IF NOT EXISTS annotation_operations(\n                    owner_id INTEGER NOT NULL,operation_id TEXT NOT NULL,\n                    input_sha TEXT NOT NULL,plan_id TEXT NOT NULL REFERENCES annotation_plans(id),\n                    PRIMARY KEY(owner_id,operation_id));\n                CREATE TABLE IF NOT EXISTS annotation_revisions(\n                    plan_id TEXT NOT NULL REFERENCES annotation_plans(id),revision INTEGER NOT NULL,\n                    annotations TEXT NOT NULL,metadata TEXT NOT NULL,PRIMARY KEY(plan_id,revision));\n                CREATE TABLE IF NOT EXISTS annotation_audio(\n                    preview_sha TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES annotation_plans(id),\n                    revision INTEGER NOT NULL,state TEXT NOT NULL,characters INTEGER NOT NULL,\n                    audio BLOB,reason TEXT);\n            '


class AnnotationStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        project = Path(__file__).resolve().parents[3]
        if self.path.is_relative_to(project):
            raise ValueError("annotation_store_must_be_outside_project")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "annotation_lab_identity" not in tables:
                raise ValueError("not_an_annotation_lab_database")
            db.executescript("CREATE TABLE IF NOT EXISTS annotation_lab_identity(version INTEGER PRIMARY KEY); INSERT OR IGNORE INTO annotation_lab_identity VALUES(1);" + ANNOTATION_TABLE_SQL)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        """Explicit single-worker startup only; a claim is never released."""
        with self.connect() as db:
            db.execute("UPDATE annotation_plans SET state='failed_unknown',reason='process_ended' "
                       "WHERE state='planning'")
            db.execute("UPDATE annotation_audio SET state='failed_unknown',reason='process_ended' "
                       "WHERE state='attempted'")

    def claim(self, owner: int, operation: str, prepared: dict) -> tuple[dict, bool]:
        sha = prepared["input_sha256"]
        with self.connect() as db:
            old = db.execute("SELECT * FROM annotation_operations WHERE owner_id=? AND operation_id=?",
                             (owner, operation)).fetchone()
            if old and old["input_sha"] != sha:
                raise AnnotationError("operation_binding_mismatch", 409)
            row = db.execute("SELECT * FROM annotation_plans WHERE owner_id=? AND input_sha=?",
                             (owner, sha)).fetchone()
            fresh = row is None
            if fresh:
                identity = uuid.uuid4().hex
                db.execute("INSERT INTO annotation_plans(id,owner_id,input_sha,prepared,state) "
                           "VALUES(?,?,?,?,'planning')",
                           (identity, owner, sha, json.dumps(prepared, ensure_ascii=False)))
            else:
                identity = row["id"]
            db.execute("INSERT OR IGNORE INTO annotation_operations VALUES(?,?,?,?)",
                       (owner, operation, sha, identity))
        return self.get(identity, owner), fresh

    def get(self, identity: str, owner: int) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM annotation_plans WHERE id=? AND owner_id=?",
                             (identity, owner)).fetchone()
            if row is None:
                raise AnnotationError("plan_not_found", 404)
            value = dict(row)
            value["prepared"] = json.loads(value["prepared"])
            value["usage"] = json.loads(value["usage"]) if value["usage"] else None
            value["versions"] = [{"revision": r[0], "annotations": json.loads(r[1]),
                                  "metadata": json.loads(r[2])}
                for r in db.execute("SELECT revision,annotations,metadata FROM annotation_revisions "
                                    "WHERE plan_id=? ORDER BY revision", (identity,))]
            return value

    def model_started(self, identity: str):
        with self.connect() as db:
            result = db.execute("UPDATE annotation_plans SET model_attempts=1 "
                                "WHERE id=? AND state='planning' AND model_attempts=0", (identity,))
            if result.rowcount != 1:
                raise AnnotationError("model_claim_unavailable", 409)

    def usage(self, identity: str, usage: dict | None):
        if usage is None:
            return
        # Accept reliable counters only, never arbitrary provider payloads.
        safe = {key: value for key, value in usage.items() if key in
                ("input_tokens", "output_tokens", "total_tokens") and
                type(value) is int and value >= 0}
        if safe:
            with self.connect() as db:
                db.execute("UPDATE annotation_plans SET usage=? WHERE id=?",
                           (json.dumps(safe), identity))

    def finish(self, identity: str, state: str, annotations: list, reason=None):
        with self.connect() as db:
            changed = db.execute("UPDATE annotation_plans SET state=?,reason=?,revision=1 "
                                 "WHERE id=? AND state='planning'", (state, reason, identity)).rowcount
            if changed:
                db.execute("INSERT INTO annotation_revisions VALUES(?,1,?,?)",
                           (identity, json.dumps(annotations),
                            json.dumps({"fallback": state == "fallback", "reason": reason})))

    def stop(self, identity: str, state: str, reason: str):
        with self.connect() as db:
            db.execute("UPDATE annotation_plans SET state=?,reason=? WHERE id=?",
                       (state, reason, identity))
            db.execute("UPDATE annotation_audio SET state='cancelled',audio=NULL,reason=? "
                       "WHERE plan_id=? AND state='attempted'", (reason, identity))

    def edit(self, identity: str, expected: int, annotations: list):
        with self.connect() as db:
            row = db.execute("SELECT revision,state FROM annotation_plans WHERE id=?", (identity,)).fetchone()
            if not row or row[0] != expected or row[1] not in ("ready", "empty", "fallback"):
                raise AnnotationError("plan_revision_expired", 409)
            db.execute("INSERT INTO annotation_revisions VALUES(?,?,?,?)",
                       (identity, expected + 1, json.dumps(annotations),
                        json.dumps({"fallback": False, "reason": None})))
            db.execute("UPDATE annotation_plans SET revision=?,state=?,reason=NULL WHERE id=?",
                       (expected + 1, "ready" if annotations else "empty", identity))

    def claim_audio(self, identity: str, revision: int, preview: dict) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT revision,state FROM annotation_plans WHERE id=?", (identity,)).fetchone()
            if not row or row[0] != revision or row[1] not in ("ready", "empty", "fallback"):
                raise AnnotationError("plan_revision_expired", 409)
            return bool(db.execute("INSERT OR IGNORE INTO annotation_audio "
                "(preview_sha,plan_id,revision,state,characters) VALUES(?,?,?,'attempted',?)",
                (preview["preview_sha256"], identity, revision, preview["request_characters"])).rowcount)

    def audio(self, sha: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM annotation_audio WHERE preview_sha=?", (sha,)).fetchone()
            return dict(row) if row else None

    def finish_audio(self, sha: str, state: str, data: bytes | None, reason=None):
        with self.connect() as db:
            db.execute("UPDATE annotation_audio SET state=?,audio=?,reason=? "
                       "WHERE preview_sha=? AND state='attempted'", (state, data, reason, sha))

    def counters(self) -> dict:
        with self.connect() as db:
            models = db.execute("SELECT COALESCE(SUM(model_attempts),0) FROM annotation_plans").fetchone()[0]
            count, chars = db.execute("SELECT COUNT(*),COALESCE(SUM(characters),0) FROM annotation_audio").fetchone()
        return {"mock_model_attempts": models, "mock_tts_attempts": count,
                "mock_tts_characters": chars, "real_provider_requests": 0}
