"""Authenticated M1 archive APIs. GET routes never mutate index or source data."""

import base64
import binascii
import hashlib
import hmac
import json
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from . import memory_v2_index as indexer
from . import memory_v2_sources as adapters
from . import memory_v2_read


CURSOR_VERSION = 2
SEARCH_SORT_VERSION = "m1-term-time-v1"
MAX_CURSOR_SNAPSHOT_SOURCES = 20000


def _cursor(data, secret):
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode()
    tag = hmac.new(secret, body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + tag).decode().rstrip("=")


def _decode_cursor(value, character_id, generation, revision, fingerprint, snapshot, secret):
    if not value:
        return None
    if len(value) > 4096:
        raise HTTPException(422, "分页游标无效")
    try:
        data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(data) < 33 or not hmac.compare_digest(data[-32:], hmac.new(secret, data[:-32], hashlib.sha256).digest()):
            raise ValueError("invalid_signature")
        parsed = json.loads(data[:-32])
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(422, "分页游标无效") from None
    if not isinstance(parsed, dict) or parsed.get("version") != CURSOR_VERSION or parsed.get("characterId") != character_id or parsed.get("fingerprint") != fingerprint:
        raise HTTPException(422, "分页游标不属于此查询")
    if parsed.get("generation") != generation or parsed.get("revision") != revision or parsed.get("snapshot") != snapshot:
        raise HTTPException(409, "来源或索引已改变，请从第一页重新查询")
    key = parsed.get("key")
    if not isinstance(key, list) or len(key) != 3 or any(not isinstance(x, str) for x in key):
        raise HTTPException(422, "分页游标无效")
    return key


def _snapshot(db, character_id, generation, *, terms=None, kind=None, segment_id=None):
    """Bounded, read-only live snapshot of all candidates in this paginated view."""
    if terms is not None:
        placeholders = ",".join("?" for _ in terms)
        query = f"""SELECT s.* FROM mv2_sources s JOIN mv2_terms t
            ON t.generation=s.generation AND t.source_kind=s.source_kind AND t.source_id=s.source_id
            WHERE s.character_id=? AND s.generation=? AND t.term IN ({placeholders})"""
        args = [character_id, generation, *terms]
        if kind:
            query += " AND s.source_kind=?"
            args.append(kind)
        query += " GROUP BY s.generation,s.source_kind,s.source_id HAVING COUNT(DISTINCT t.term)=?"
        args.append(len(terms))
    else:
        query = """SELECT s.*,sm.segment_id AS member_segment,sm.ordinal AS member_ordinal,
            sm.order_warning,seg.start_at AS member_start,seg.end_at AS member_end
            FROM mv2_segment_members sm JOIN mv2_sources s
            ON s.generation=sm.generation AND s.source_kind=sm.source_kind AND s.source_id=sm.source_id
            JOIN mv2_segments seg ON seg.generation=sm.generation AND seg.segment_id=sm.segment_id
            WHERE seg.character_id=? AND sm.generation=?"""
        args = [character_id, generation]
        if segment_id:
            query += " AND sm.segment_id=?"
            args.append(segment_id)
    query += " ORDER BY s.source_kind,s.source_id" + (",member_segment,member_ordinal" if terms is None else "")
    rows = db.execute(query + " LIMIT ?", (*args, MAX_CURSOR_SNAPSHOT_SOURCES + 1)).fetchall()
    if len(rows) > MAX_CURSOR_SNAPSHOT_SOURCES:
        raise HTTPException(409, "分页来源范围过大，请缩小筛选范围")
    result = hashlib.sha256()
    cache = {}
    for row in rows:
        live = memory_v2_read.valid_source(db, row, character_id, cache)
        record = [row["source_kind"], row["source_id"], row["stream_id"],
                  row["archive_file_id"], row["archive_ordinal"], row["content_hash"], row["state_hash"]]
        if terms is None:
            record += [row["member_segment"], row["member_ordinal"], row["order_warning"],
                       row["member_start"], row["member_end"]]
        record += [live.content_hash, live.state_hash, live.archive_file_id, live.archive_ordinal] if live else ["invalid"]
        result.update(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode())
        result.update(b"\n")
    return result.hexdigest()


def _search_text(encoded):
    if len(encoded) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise HTTPException(422, "搜索词编码无效")
    try:
        text = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(422, "搜索词编码无效") from None
    if not 2 <= len(text) <= 200:
        raise HTTPException(422, "搜索词长度须为 2～200 字符")
    return text


def _character(db, character_id):
    row = db.execute("SELECT id,enabled FROM characters WHERE id=?", (character_id,)).fetchone()
    if row is None or not row["enabled"]:
        raise HTTPException(404, "角色不存在或不可访问")
    return row


def _meta(db, character_id):
    _character(db, character_id)
    row = db.execute("SELECT * FROM mv2_meta WHERE character_id=?", (character_id,)).fetchone()
    if row is None or row["schema_version"] != 2 or row["state"] != "ready":
        raise HTTPException(409, "记忆档案索引需要显式重建")
    return row


def _valid(db, indexed, character_id, cache=None):
    return memory_v2_read.valid_source(db, indexed, character_id, cache)


def _source_row(db, character_id, kind, source_id):
    meta = _meta(db, character_id)
    if kind not in adapters.KINDS:
        raise HTTPException(404, "来源不存在")
    row = db.execute("""SELECT * FROM mv2_sources WHERE generation=? AND source_kind=? AND source_id=?
        AND character_id=?""", (meta["active_generation"], kind, source_id, character_id)).fetchone()
    live = _valid(db, row, character_id) if row else None
    if live is None:
        raise HTTPException(404, "来源不存在、已变更或已失效")
    return meta, row, live


def _duplicate_count(db, meta, row, character_id):
    if row["source_kind"] != "message" or row["text_length"] < 8:
        return 0
    candidates = db.execute("""SELECT * FROM mv2_sources WHERE character_id=? AND generation=?
        AND source_kind='message' AND content_hash=? AND text_length>=8""",
        (character_id, meta["active_generation"], row["content_hash"])).fetchall()
    return max(0, sum(1 for candidate in candidates if _valid(db, candidate, character_id)) - 1)


def _card(db, meta, row, live, character_id):
    return {"sourceRef": {"kind": live.kind, "id": live.source_id}, "characterId": character_id,
            "sourceRole": live.role, "sourceStatus": live.status, "evidenceKind": live.evidence_kind,
            "occurredAt": live.occurred_at, "recordedAt": live.recorded_at,
            "outcomeRecordedAt": live.outcome_recorded_at, "indexedAt": row["indexed_at"],
            "timeBasis": live.time_basis, "textLength": len(live.text),
            "possibleDuplicateCount": _duplicate_count(db, meta, row, character_id)}


def install(app, connect, cursor_key_provider=None):
    router = APIRouter(prefix="/api/memory-v2")
    def signing_key():
        try:
            key = cursor_key_provider() if cursor_key_provider else None
        except Exception:
            key = None
        if not isinstance(key, bytes) or len(key) != 32:
            raise HTTPException(503, "分页签名密钥不可用")
        return key
    @app.middleware("http")
    async def no_store_memory_v2(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/memory-v2"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/status")
    def status(characterId: int):
        with connect() as db:
            _character(db, characterId)
            meta = db.execute("SELECT * FROM mv2_meta WHERE character_id=?", (characterId,)).fetchone()
            job = db.execute("""SELECT job_id,mode,status,phase,processed,changed,invalidated FROM mv2_jobs
                WHERE character_id=? ORDER BY created_at DESC,job_id DESC LIMIT 1""", (characterId,)).fetchone()
            state = meta["state"] if meta and meta["schema_version"] == 2 else "needs_rebuild"
            return {"characterId": characterId, "state": state,
                    "activeGeneration": meta["active_generation"] if meta else 0,
                    "latestJob": dict(job) if job else None}

    class JobInput(BaseModel):
        characterId: int
        mode: Literal["incremental", "rebuild"]

    @router.post("/index/jobs", status_code=201)
    def start(payload: JobInput):
        with connect() as db:
            _character(db, payload.characterId)
            try:
                return indexer.start_job(db, payload.characterId, payload.mode)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    def job_for(db, job_id, character_id):
        _character(db, character_id)
        job = indexer._job(db, job_id)
        if job is None or job["character_id"] != character_id:
            raise HTTPException(404, "索引任务不存在")
        return job

    @router.get("/index/jobs/{job_id}")
    def get_job(job_id: str, characterId: int):
        with connect() as db:
            return job_for(db, job_id, characterId)

    @router.post("/index/jobs/{job_id}/pause")
    def pause(job_id: str, characterId: int):
        with connect() as db:
            job_for(db, job_id, characterId)
            try:
                return indexer.set_status(db, job_id, "paused")
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/index/jobs/{job_id}/resume")
    def resume(job_id: str, characterId: int):
        with connect() as db:
            job_for(db, job_id, characterId)
            try:
                return indexer.set_status(db, job_id, "running")
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/index/jobs/{job_id}/step")
    def step(job_id: str, characterId: int):
        try:
            with connect() as db:
                job_for(db, job_id, characterId)
                return indexer.step_job(db, job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except sqlite3.DatabaseError:
            with connect() as db:
                db.execute("UPDATE mv2_jobs SET status='paused',error_code='index_error' WHERE job_id=? AND status='running'", (job_id,))
            raise HTTPException(500, "索引批次失败；任务已暂停") from None

    @router.get("/sources/{kind}/{source_id}")
    def detail(kind: str, source_id: str, characterId: int):
        with connect() as db:
            meta, row, live = _source_row(db, characterId, kind, source_id)
            return {**_card(db, meta, row, live, characterId), "preview": live.text[:160],
                    "contentFingerprint": live.content_hash}

    @router.get("/sources/{kind}/{source_id}/unfold")
    def unfold(kind: str, source_id: str, characterId: int, offset: int = Query(0, ge=0),
               limit: int = Query(4096, ge=1, le=4096), expectedFingerprint: str | None = None):
        if offset and not expectedFingerprint:
            raise HTTPException(422, "后续展开页须提供首屏正文指纹")
        with connect() as db:
            meta, row, live = _source_row(db, characterId, kind, source_id)
            if expectedFingerprint and expectedFingerprint != live.content_hash:
                raise HTTPException(409, "来源正文已改变，请重新展开")
            text = live.text[offset:offset + limit]
            next_offset = offset + len(text)
            return {"sourceRef": {"kind": kind, "id": source_id}, "text": text,
                    "contentFingerprint": live.content_hash,
                    "nextOffset": next_offset if next_offset < len(live.text) else None,
                    "redactedToSafeFields": kind == "activity_operation"}

    @router.get("/search")
    def search(characterId: int, q_encoded: str = Header(..., alias="X-Memory-Query-B64"),
               kind: str | None = None, limit: int = Query(20, ge=1, le=50), cursor: str | None = None):
        q = _search_text(q_encoded)
        if kind is not None and kind not in adapters.KINDS:
            raise HTTPException(422, "未知来源类型")
        terms = sorted(adapters.query_terms(q))
        if not terms:
            raise HTTPException(422, "请输入至少两个有效字符")
        fingerprint = hashlib.sha256(json.dumps([SEARCH_SORT_VERSION, terms, kind], ensure_ascii=False).encode()).hexdigest()
        with connect() as db:
            db.execute("BEGIN")
            meta = _meta(db, characterId)
            generation = meta["active_generation"]
            secret = signing_key()
            snapshot = _snapshot(db, characterId, generation, terms=terms, kind=kind)
            key = _decode_cursor(cursor, characterId, generation, meta["order_revision"],
                                 fingerprint, snapshot, secret)
            output = []
            scanned = 0
            last = key
            cache = {}
            while len(output) < limit and scanned < 10000:
                placeholders = ",".join("?" for _ in terms)
                sql = f"""SELECT s.*,COALESCE(s.occurred_at,s.recorded_at,'') AS sort_time
                    FROM mv2_sources s JOIN mv2_terms t ON t.generation=s.generation
                      AND t.source_kind=s.source_kind AND t.source_id=s.source_id
                    WHERE s.character_id=? AND s.generation=? AND t.term IN ({placeholders})"""
                args = [characterId, generation, *terms]
                if kind:
                    sql += " AND s.source_kind=?"
                    args.append(kind)
                if last:
                    sql += " AND (COALESCE(s.occurred_at,s.recorded_at,'') < ? OR (COALESCE(s.occurred_at,s.recorded_at,'')=? AND (s.source_kind,s.source_id)>(?,?)))"
                    args.extend([last[0], last[0], last[1], last[2]])
                sql += " GROUP BY s.generation,s.source_kind,s.source_id HAVING COUNT(DISTINCT t.term)=?"
                args.append(len(terms))
                sql += " ORDER BY sort_time DESC,s.source_kind,s.source_id LIMIT 100"
                rows = db.execute(sql, args).fetchall()
                if not rows:
                    break
                for row in rows:
                    last = [row["sort_time"], row["source_kind"], row["source_id"]]
                    scanned += 1
                    live = _valid(db, row, characterId, cache)
                    if live is not None:
                        output.append(_card(db, meta, row, live, characterId))
                    if len(output) >= limit or scanned >= 10000:
                        break
                if len(rows) < 100:
                    break
            next_cursor = _cursor({"version":CURSOR_VERSION,"characterId": characterId,
                "generation": generation,"revision":meta["order_revision"],"snapshot":snapshot,
                "fingerprint": fingerprint, "key": last}, secret) if last and (len(output) >= limit or scanned >= 10000) else None
            return {"items": output, "nextCursor": next_cursor, "indexGeneration": generation}

    def segment_view(db, meta, segment, character_id, cache=None):
        members = db.execute("""SELECT s.*,sm.order_warning FROM mv2_segment_members sm JOIN mv2_sources s
            ON s.generation=sm.generation AND s.source_kind=sm.source_kind AND s.source_id=sm.source_id
            WHERE sm.generation=? AND sm.segment_id=? ORDER BY sm.ordinal""",
            (segment["generation"], segment["segment_id"])).fetchall()
        valid = [(row, _valid(db, row, character_id, cache)) for row in members]
        valid = [(row, live) for row, live in valid if live is not None]
        if not valid:
            return None
        times = [live.occurred_at or live.recorded_at for _, live in valid if live.occurred_at or live.recorded_at]
        first, last = (min(times), max(times)) if times else (None, None)
        basis = segment["time_basis"]
        title = ("发生于" if basis == "occurred" else "记录于" if basis == "recorded" else "时间未知")
        if first:
            title += " " + first + (" 至 " + last if last != first else "")
        return {"segmentId": segment["segment_id"], "kind": "temporal_segment", "title": title,
                "timeBasis": basis, "startAt": first, "endAt": last, "memberCount": len(valid),
                "timeOrderConflictCount": sum(row["order_warning"] == "time_order_conflict" for row, _ in valid)}

    def segments_updating(db, character_id, generation):
        return db.execute("""SELECT 1 FROM mv2_jobs WHERE character_id=? AND target_generation=?
            AND status IN ('running','paused') AND phase='segments'""",
            (character_id, generation)).fetchone() is not None

    @router.get("/segments")
    def segments(characterId: int, limit: int = Query(20, ge=1, le=50), cursor: str | None = None):
        with connect() as db:
            db.execute("BEGIN")
            meta = _meta(db, characterId)
            generation = meta["active_generation"]
            if segments_updating(db, characterId, generation):
                raise HTTPException(409, "时间片段正在重建，请稍后查询")
            secret = signing_key()
            snapshot = _snapshot(db, characterId, generation)
            key = _decode_cursor(cursor, characterId, generation, meta["order_revision"],
                                 "segments", snapshot, secret)
            output, last = [], key
            cache = {}
            exhausted = False
            for _ in range(100):
                sql = """SELECT *,COALESCE(start_at,'') AS sort_time FROM mv2_segments
                    WHERE character_id=? AND generation=?"""
                args = [characterId, generation]
                if last:
                    sql += " AND (COALESCE(start_at,'') < ? OR (COALESCE(start_at,'')=? AND segment_id>?))"
                    args.extend([last[0], last[0], last[2]])
                sql += " ORDER BY sort_time DESC,segment_id LIMIT 100"
                rows = db.execute(sql, args).fetchall()
                if not rows:
                    exhausted = True
                    break
                for segment in rows:
                    last = [segment["sort_time"], "", segment["segment_id"]]
                    item = segment_view(db, meta, segment, characterId, cache)
                    if item:
                        output.append(item)
                    if len(output) >= limit:
                        break
                if len(rows) < 100:
                    exhausted = True
                if len(output) >= limit or exhausted:
                    break
            next_cursor = _cursor({"version":CURSOR_VERSION,"characterId": characterId,
                "generation": generation,"revision":meta["order_revision"],"snapshot":snapshot,
                "fingerprint": "segments", "key": last}, secret) if last and (len(output) >= limit or not exhausted) else None
            return {"items": output, "nextCursor": next_cursor, "indexGeneration": generation}

    @router.get("/segments/{segment_id}/sources")
    def segment_sources(segment_id: str, characterId: int, limit: int = Query(20, ge=1, le=50),
                        cursor: str | None = None):
        with connect() as db:
            db.execute("BEGIN")
            meta = _meta(db, characterId)
            generation = meta["active_generation"]
            if segments_updating(db, characterId, generation):
                raise HTTPException(409, "时间片段正在重建，请稍后查询")
            segment = db.execute("SELECT * FROM mv2_segments WHERE character_id=? AND generation=? AND segment_id=?",
                                 (characterId, generation, segment_id)).fetchone()
            if segment is None:
                raise HTTPException(404, "时间片段不存在")
            secret = signing_key()
            snapshot = _snapshot(db, characterId, generation, segment_id=segment_id)
            key = _decode_cursor(cursor, characterId, generation, meta["order_revision"],
                                 "segment:" + segment_id, snapshot, secret)
            try:
                ordinal = int(key[0]) if key else -1
            except ValueError:
                raise HTTPException(422, "分页游标无效") from None
            output = []
            last = ordinal
            scanned = 0
            exhausted = False
            cache = {}
            while len(output) < limit and scanned < 10000:
                rows = db.execute("""SELECT sm.ordinal,sm.order_warning,s.* FROM mv2_segment_members sm JOIN mv2_sources s
                    ON s.generation=sm.generation AND s.source_kind=sm.source_kind AND s.source_id=sm.source_id
                    WHERE sm.generation=? AND sm.segment_id=? AND sm.ordinal>?
                    ORDER BY sm.ordinal LIMIT 100""", (generation, segment_id, last)).fetchall()
                if not rows:
                    exhausted = True
                    break
                for row in rows:
                    last = row["ordinal"]
                    scanned += 1
                    live = _valid(db, row, characterId, cache)
                    if live:
                        output.append({**_card(db, meta, row, live, characterId),
                                       "orderWarning": row["order_warning"]})
                    if len(output) >= limit or scanned >= 10000:
                        break
                if len(rows) < 100:
                    exhausted = True
                    break
            next_cursor = _cursor({"version":CURSOR_VERSION,"characterId": characterId,
                "generation": generation,"revision":meta["order_revision"],"snapshot":snapshot,
                "fingerprint": "segment:" + segment_id,
                "key": [str(last), "", ""]}, secret) if last >= 0 and (len(output) >= limit or not exhausted) else None
            return {"items": output, "nextCursor": next_cursor, "indexGeneration": generation}

    app.include_router(router)
