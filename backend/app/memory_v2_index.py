"""Explicit, bounded M1 indexing jobs. Only mv2_* tables are written."""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from . import memory_v2_sources as adapters
from . import memory_v2_read as reader


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job(db, job_id):
    row = db.execute("SELECT * FROM mv2_jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def _delete_source(db, generation, kind, source_id):
    row = db.execute("""SELECT s.character_id,m.active_generation FROM mv2_sources s
        JOIN mv2_meta m ON m.character_id=s.character_id
        WHERE s.generation=? AND s.source_kind=? AND s.source_id=?""",
        (generation, kind, source_id)).fetchone()
    deleted = db.execute("DELETE FROM mv2_sources WHERE generation=? AND source_kind=? AND source_id=?",
                         (generation, kind, source_id)).rowcount
    if deleted and row and row["active_generation"] == generation:
        db.execute("UPDATE mv2_meta SET order_revision=order_revision+1 WHERE character_id=?",
                   (row["character_id"],))
    return deleted


def _upsert_source(db, job, snapshot):
    generation = job["target_generation"]
    old = db.execute("""SELECT content_hash,state_hash,character_id,archive_file_id,archive_ordinal FROM mv2_sources
        WHERE generation=? AND source_kind=? AND source_id=?""",
        (generation, snapshot.kind, snapshot.source_id)).fetchone()
    if old and old["character_id"] != job["character_id"]:
        # A source changed owner. Its former role must lose the derived row.
        _delete_source(db, generation, snapshot.kind, snapshot.source_id)
        old = None
    changed = (old is None or old["content_hash"] != snapshot.content_hash or old["state_hash"] != snapshot.state_hash
               or old["archive_file_id"] != snapshot.archive_file_id
               or old["archive_ordinal"] != snapshot.archive_ordinal)
    if changed:
        db.execute("""INSERT INTO mv2_sources(generation,source_kind,source_id,archive_file_id,archive_ordinal,
            character_id,stream_kind,stream_id,
            source_role,source_status,evidence_kind,occurred_at,recorded_at,outcome_recorded_at,time_basis,indexed_at,
            content_hash,state_hash,text_length,seen_job_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(generation,source_kind,source_id) DO UPDATE SET
            archive_file_id=excluded.archive_file_id,archive_ordinal=excluded.archive_ordinal,
            character_id=excluded.character_id,stream_kind=excluded.stream_kind,stream_id=excluded.stream_id,
            source_role=excluded.source_role,source_status=excluded.source_status,evidence_kind=excluded.evidence_kind,
            occurred_at=excluded.occurred_at,recorded_at=excluded.recorded_at,
            outcome_recorded_at=excluded.outcome_recorded_at,time_basis=excluded.time_basis,
            indexed_at=excluded.indexed_at,content_hash=excluded.content_hash,state_hash=excluded.state_hash,
            text_length=excluded.text_length,seen_job_id=excluded.seen_job_id""",
            (generation, snapshot.kind, snapshot.source_id, snapshot.archive_file_id,snapshot.archive_ordinal,
             snapshot.character_id, snapshot.stream_kind,
             snapshot.stream_id, snapshot.role, snapshot.status, snapshot.evidence_kind, snapshot.occurred_at,
             snapshot.recorded_at, snapshot.outcome_recorded_at, snapshot.time_basis, _now(),
             snapshot.content_hash, snapshot.state_hash, len(snapshot.text), job["job_id"]))
        db.execute("DELETE FROM mv2_terms WHERE generation=? AND source_kind=? AND source_id=?",
                   (generation, snapshot.kind, snapshot.source_id))
        db.executemany("INSERT INTO mv2_terms(generation,source_kind,source_id,term) VALUES(?,?,?,?)",
                       [(generation, snapshot.kind, snapshot.source_id, term) for term in sorted(adapters.terms(snapshot.text))])
        db.execute("""UPDATE mv2_meta SET order_revision=order_revision+1 WHERE character_id=?
            AND active_generation=?""", (job["character_id"], generation))
    else:
        db.execute("UPDATE mv2_sources SET seen_job_id=? WHERE generation=? AND source_kind=? AND source_id=?",
                   (job["job_id"], generation, snapshot.kind, snapshot.source_id))
    return int(changed)


def start_job(db, character_id, mode):
    if mode not in ("incremental", "rebuild"):
        raise ValueError("invalid_mode")
    meta = db.execute("SELECT * FROM mv2_meta WHERE character_id=?", (character_id,)).fetchone()
    if meta is None:
        if db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone() is None:
            raise LookupError("character_missing")
        db.execute("""INSERT INTO mv2_meta(character_id,schema_version,active_generation,state,updated_at)
            VALUES(?,2,0,'needs_rebuild',?)""", (character_id, _now()))
        meta = db.execute("SELECT * FROM mv2_meta WHERE character_id=?", (character_id,)).fetchone()
    if meta["schema_version"] != 2:
        raise ValueError("order_migration_required")
    if db.execute("SELECT 1 FROM mv2_jobs WHERE character_id=? AND status IN ('running','paused')", (character_id,)).fetchone():
        raise ValueError("job_already_open")
    if mode == "incremental" and meta["state"] != "ready":
        raise ValueError("rebuild_required")
    used = db.execute("SELECT MAX(target_generation) FROM mv2_jobs WHERE character_id=?", (character_id,)).fetchone()[0] or 0
    generation = max(meta["active_generation"], used) + 1 if mode == "rebuild" else meta["active_generation"]
    job_id = str(uuid.uuid4())
    stamp = _now()
    db.execute("""INSERT INTO mv2_jobs(job_id,character_id,mode,status,phase,target_generation,created_at,updated_at)
        VALUES(?,? ,?,'running','messages',?,?,?)""", (job_id, character_id, mode, generation, stamp, stamp))
    return _job(db, job_id)


def set_status(db, job_id, status):
    job = _job(db, job_id)
    if job is None:
        raise LookupError("job_missing")
    expected = "running" if status == "paused" else "paused"
    if status not in ("paused", "running") or job["status"] != expected:
        raise ValueError("invalid_job_transition")
    db.execute("UPDATE mv2_jobs SET status=?,updated_at=?,error_code=NULL WHERE job_id=?", (status, _now(), job_id))
    return _job(db, job_id)


def _scan_messages(db, job):
    has_archive = any(column[1] == "archive_only" for column in db.execute("PRAGMA table_info(conversations)"))
    archive_column = "c.archive_only" if has_archive else "0 AS archive_only"
    rows = db.execute(f"""SELECT m.id,m.role,{archive_column} FROM messages m JOIN conversations c ON c.id=m.conversation_id
        WHERE c.character_id=? AND m.id>? ORDER BY m.id LIMIT 100""",
        (job["character_id"], job["message_cursor"])).fetchall()
    if not rows:
        db.execute("UPDATE mv2_jobs SET phase='activities',updated_at=? WHERE job_id=?", (_now(), job["job_id"]))
        return 0
    cache = {}
    snapshots = [(row, adapters.source(db, "message", str(row["id"]), cache)) for row in rows]
    if any(row["archive_only"] and row["role"] in ("user", "assistant") and item is None
           for row, item in snapshots):
        db.execute("""UPDATE mv2_jobs SET status='paused',error_code='archive_order_invalid',updated_at=?
            WHERE job_id=?""", (_now(), job["job_id"]))
        return 0
    changed = 0
    for row, item in snapshots:
        source_id = str(row["id"])
        if item and item.character_id == job["character_id"]:
            changed += _upsert_source(db, job, item)
        else:
            changed += _delete_source(db, job["target_generation"], "message", source_id)
    db.execute("""UPDATE mv2_jobs SET message_cursor=?,processed=processed+?,changed=changed+?,updated_at=?
        WHERE job_id=?""", (rows[-1]["id"], len(rows), changed, _now(), job["job_id"]))
    return len(rows)


def _scan_activities(db, job):
    rows = db.execute("""SELECT id,created_at FROM activity_operations WHERE character_id=?
        AND (created_at,id)>(?,?) ORDER BY created_at,id LIMIT 100""",
        (job["character_id"], job["activity_time_cursor"], job["activity_id_cursor"])).fetchall()
    if not rows:
        db.execute("UPDATE mv2_jobs SET phase='sweep',updated_at=? WHERE job_id=?", (_now(), job["job_id"]))
        return 0
    changed = 0
    for row in rows:
        item = adapters.source(db, "activity_operation", row["id"])
        if item and item.character_id == job["character_id"]:
            changed += _upsert_source(db, job, item)
        else:
            changed += _delete_source(db, job["target_generation"], "activity_operation", row["id"])
    db.execute("""UPDATE mv2_jobs SET activity_time_cursor=?,activity_id_cursor=?,processed=processed+?,
        changed=changed+?,updated_at=? WHERE job_id=?""",
        (rows[-1]["created_at"], rows[-1]["id"], len(rows), changed, _now(), job["job_id"]))
    return len(rows)


def _sweep(db, job):
    rows = db.execute("""SELECT source_kind,source_id FROM mv2_sources WHERE character_id=? AND generation=?
        AND (source_kind,source_id)>(?,?) AND seen_job_id IS NOT ?
        ORDER BY source_kind,source_id LIMIT 100""",
        (job["character_id"], job["target_generation"], job["sweep_kind_cursor"],
         job["sweep_id_cursor"], job["job_id"])).fetchall()
    if not rows:
        db.execute("UPDATE mv2_jobs SET phase='segments',updated_at=? WHERE job_id=?", (_now(), job["job_id"]))
        return 0
    changed, invalidated = 0, 0
    for row in rows:
        item = adapters.source(db, row["source_kind"], row["source_id"])
        if item and item.character_id == job["character_id"]:
            changed += _upsert_source(db, job, item)
        else:
            invalidated += _delete_source(db, job["target_generation"], row["source_kind"], row["source_id"])
    db.execute("""UPDATE mv2_jobs SET sweep_kind_cursor=?,sweep_id_cursor=?,changed=changed+?,
        invalidated=invalidated+?,updated_at=? WHERE job_id=?""",
        (rows[-1]["source_kind"], rows[-1]["source_id"], changed, invalidated, _now(), job["job_id"]))
    return len(rows)


def _key(row):
    return [row["stream_kind"], row["stream_id"], row["time_basis"],
            row["order_file"], row["order_ordinal"], row["order_time"],
            row["source_kind"], row["source_id"]]


def _validate_target(db, job):
    """Fail closed if a source changed or appeared between scanning and generation switch."""
    cache = {}
    rows = db.execute("SELECT * FROM mv2_sources WHERE character_id=? AND generation=?",
                      (job["character_id"], job["target_generation"]))
    for row in rows:
        if reader.valid_source(db, row, job["character_id"], cache) is None:
            return False
    if db.execute("""SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
        WHERE c.character_id=? AND m.id>? LIMIT 1""",
        (job["character_id"], job["message_cursor"])).fetchone():
        return False
    if db.execute("""SELECT 1 FROM activity_operations WHERE character_id=?
        AND (created_at,id)>(?,?) LIMIT 1""",
        (job["character_id"], job["activity_time_cursor"], job["activity_id_cursor"])).fetchone():
        return False
    return True


def _segments(db, job):
    generation = job["target_generation"]
    state = json.loads(job["segment_cursor_json"])
    if state and state.get("version") != 2:
        db.execute("""UPDATE mv2_jobs SET status='paused',error_code='order_cursor_obsolete',updated_at=?
            WHERE job_id=?""", (_now(), job["job_id"]))
        return 0
    if not state:
        db.execute("DELETE FROM mv2_segments WHERE character_id=? AND generation=?",
                   (job["character_id"], generation))
    key = state.get("key", ["", "", "", "", -1, "", "", ""])
    order_file = "COALESCE(archive_file_id,'')"
    order_ordinal = "COALESCE(archive_ordinal,-1)"
    order_time = "CASE WHEN archive_file_id IS NULL THEN COALESCE(occurred_at,recorded_at,'') ELSE '' END"
    fields = f"stream_kind,stream_id,time_basis,{order_file},{order_ordinal},{order_time},source_kind,source_id"
    rows = db.execute(f"""SELECT *,{order_file} AS order_file,{order_ordinal} AS order_ordinal,
        {order_time} AS order_time FROM mv2_sources WHERE character_id=? AND generation=?
        AND ({fields})>(?,?,?,?,?,?,?,?) ORDER BY {fields} LIMIT 100""",
        (job["character_id"], generation, *key)).fetchall()
    if not rows:
        if not _validate_target(db, job):
            db.execute("""UPDATE mv2_jobs SET status='paused',error_code='source_changed_during_index',updated_at=?
                WHERE job_id=?""", (_now(), job["job_id"]))
            return 0
        if job["mode"] == "rebuild":
            db.execute("""UPDATE mv2_meta SET active_generation=?,state='ready',order_revision=order_revision+1,updated_at=?
                WHERE character_id=?""", (generation, _now(), job["character_id"]))
            db.execute("DELETE FROM mv2_segments WHERE character_id=? AND generation<>?", (job["character_id"], generation))
            db.execute("DELETE FROM mv2_sources WHERE character_id=? AND generation<>?", (job["character_id"], generation))
        db.execute("UPDATE mv2_jobs SET phase='done',status='completed',updated_at=? WHERE job_id=?",
                   (_now(), job["job_id"]))
        return 0
    for row in rows:
        current_key = _key(row)
        archive = row["archive_file_id"] is not None
        group = current_key[:3] + ([row["archive_file_id"]] if archive else [""])
        at = row["occurred_at"] if archive else row["occurred_at"] or row["recorded_at"]
        new_segment = group != state.get("group") or (not archive and (not at or not state.get("end_at")))
        warning = None
        if not new_segment and at and state.get("last_at"):
            try:
                gap = datetime.fromisoformat(at.replace("Z", "+00:00")) - datetime.fromisoformat(state["last_at"].replace("Z", "+00:00"))
                if gap.total_seconds() < 0 and archive:
                    warning = "time_order_conflict"
                else:
                    new_segment = gap.total_seconds() > 1800 or gap.total_seconds() < 0
            except (ValueError, TypeError):
                if not archive:
                    new_segment = True
        if new_segment:
            segment_id = hashlib.sha256(json.dumps([job["character_id"], generation, *group,
                row["source_kind"], row["source_id"]], ensure_ascii=False).encode()).hexdigest()[:32]
            db.execute("""INSERT INTO mv2_segments(generation,segment_id,character_id,stream_kind,stream_id,
                time_basis,start_at,end_at,member_count) VALUES(?,?,?,?,?,?,?,?,1)""",
                (generation, segment_id, job["character_id"], *group[:3], at, at))
            ordinal = 0
        else:
            segment_id = state["segment_id"]
            ordinal = state["ordinal"] + 1
            db.execute("UPDATE mv2_segments SET end_at=?,member_count=member_count+1 WHERE generation=? AND segment_id=?",
                       (max(filter(None,(at,state.get("end_at"))),default=None), generation, segment_id))
        db.execute("""INSERT INTO mv2_segment_members(generation,segment_id,source_kind,source_id,ordinal,order_warning)
            VALUES(?,?,?,?,?,?)""", (generation, segment_id, row["source_kind"], row["source_id"], ordinal, warning))
        state = {"version":2,"key": current_key, "group": group, "end_at": at if new_segment else
                 max(filter(None,(at,state.get("end_at"))),default=None),
                 "last_at": at, "segment_id": segment_id, "ordinal": ordinal}
    db.execute("UPDATE mv2_jobs SET segment_cursor_json=?,updated_at=? WHERE job_id=?",
               (json.dumps(state, ensure_ascii=False), _now(), job["job_id"]))
    return len(rows)


def step_job(db, job_id):
    job = _job(db, job_id)
    if job is None:
        raise LookupError("job_missing")
    if job["status"] != "running":
        raise ValueError("job_not_running")
    for phase, action in (("messages", _scan_messages), ("activities", _scan_activities),
                          ("sweep", _sweep), ("segments", _segments)):
        if job["phase"] == phase:
            action(db, job)
            return _job(db, job_id)
    raise ValueError("invalid_job_phase")
