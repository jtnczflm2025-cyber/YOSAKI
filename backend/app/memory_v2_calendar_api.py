"""M5-A installable read-only Chronicle adapter. Never installed by main.py."""

import unicodedata
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import memory_v2_derivation as m3
from . import memory_v2_entries as entries
from . import memory_v2_time_projection as p2
from .memory_v2_contract import SourceRef, experience_key


class ProjectionPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characterId: int = Field(gt=0)
    entryIds: list[str] = Field(min_length=1, max_length=50)
    asOf: str
    offsetMinutes: int = Field(default=480, ge=-840, le=840)
    budgetTokens: int = Field(default=512, ge=0, le=p2.MAX_BUDGET)
    maxBlocks: int = Field(default=5, ge=1, le=20)


def _view_time(value, offset_minutes):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        p2._checked_view(parsed, offset_minutes)
        return parsed
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "时间视角无效") from None


def search_members(db, character_id, as_of, offset_minutes, from_date, to_date, query=""):
    now = _view_time(as_of, offset_minutes)
    start = datetime.fromisoformat(from_date).date() if from_date else None
    end = datetime.fromisoformat(to_date).date() if to_date else None
    if (start and end and start > end) or len(query) > 100:
        raise ValueError("invalid_search_filters")
    zone = timezone(timedelta(minutes=offset_minutes))
    for member in p2._members(db, character_id, now, offset_minutes):
        day = member.timestamp.astimezone(zone).date() if member.timestamp else None
        if (start or end) and (day is None or start and day < start or end and day > end):
            continue
        if matches_search(member.detail, query):
            yield member


def matches_search(detail, query):
    term = unicodedata.normalize("NFKC", query).casefold().strip()
    if not term:
        return True
    values = (detail["title"], detail["synopsis"] or "", *detail["keywords"], *detail["tags"])
    return term in unicodedata.normalize("NFKC", " ".join(values)).casefold()


def _digest_sources(db, character_id, version):
    """Rebuild the rule output byte-for-byte before hiding its machine wrappers."""
    refs = m3._refs(db, version["id"])
    sections = []
    for ordinal, ref in enumerate(refs):
        source = m3.live_source(db, character_id, ref["source_kind"], ref["source_id"])
        if source is None or source.content_hash != ref["content_hash"] or source.state_hash != ref["state_hash"]:
            raise HTTPException(409, "digest_source_changed")
        sections.append({"ordinal": ordinal, "sourceRef": {"kind": source.kind, "id": source.source_id},
                         "role": source.role, "status": source.status,
                         "evidenceKind": source.evidence_kind, "text": source.text})
    return sections


def _extractive_body_matches(version, node, sections):
    header = f"摘录式摘要 {node.level} / {node.period}（系统派生；未作语义概括）"
    parts = [header]
    for section in sections:
        ref = section["sourceRef"]
        parts.append(f'[{ref["kind"]}:{ref["id"]} | {section["role"]} | {section["status"]}]「'
                     + section["text"] + "」")
    return "\n\n".join(parts) == version["body"]


def _checked_entry(db, character_id, entry_id):
    try:
        return entries.read_entry(db, entry_id, character_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


def _derivation_for(db, character_id, detail, view_type):
    refs = tuple(SourceRef(r["kind"], str(r["id"])) for r in detail["sourceRefs"])
    key = experience_key(character_id, refs)
    return db.execute("""SELECT * FROM mv3_derivations WHERE character_id=?
        AND view_type=? AND experience_key=?""", (character_id, view_type, key)).fetchone()


def _view_version(db, character_id, detail, view_type):
    if view_type == "NARRATIVE":
        version_id = detail["narrativeVersionId"]
    elif view_type == "CLEANED":
        owner = _derivation_for(db, character_id, detail, "CLEANED")
        if owner is None or owner["active_version"] is None:
            return None
        row = db.execute("""SELECT id FROM mv3_versions WHERE derivation_id=? AND version=?""",
                         (owner["id"], owner["active_version"])).fetchone()
        version_id = row["id"] if row else None
    else:
        return None
    if version_id is None:
        return None
    try:
        version = m3.read_version(db, version_id, character_id)
    except ValueError:
        return None
    if [(r["source_kind"], r["source_id"]) for r in m3._refs(db, version_id)] != [
            (r["kind"], str(r["id"])) for r in detail["sourceRefs"]]:
        return None
    return version


def _view_status(db, character_id, detail, view_type):
    version = _view_version(db, character_id, detail, view_type)
    if version:
        return {"status": "available", "versionId": version["id"]}
    if view_type == "NARRATIVE":
        return {"status": "stale"}
    owner = _derivation_for(db, character_id, detail, "CLEANED")
    if owner is None:
        return {"status": "missing"}
    latest = db.execute("""SELECT status FROM mv3_versions WHERE derivation_id=?
        ORDER BY version DESC LIMIT 1""", (owner["id"],)).fetchone()
    return {"status": "needs_review" if latest and latest["status"] == "needs_review" else "stale"}


def install(app, connect, authenticate):
    def owner(request: Request):
        if not authenticate(request):
            raise HTTPException(401, "需要登录后继续")

    router = APIRouter(prefix="/api/memory-v2/calendar", dependencies=[Depends(owner)])

    @router.get("/characters")
    def characters():
        with connect() as db:
            return [{"id": row["id"], "name": row["name"]} for row in db.execute(
                "SELECT id,name FROM characters WHERE enabled=1 ORDER BY id")]

    @router.get("/status")
    def status(characterId: int):
        with connect() as db:
            role = db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (characterId,)).fetchone()
            if not role:
                raise HTTPException(404, "角色不可访问")
            if not p2._table_exists(db, "mv3_entries"):
                return {"state": "needs_schema", "available": 0, "excludedStale": 0}
            ids = [r["id"] for r in db.execute(
                "SELECT id FROM mv3_entries WHERE character_id=? AND status='active'", (characterId,))]
            available = 0
            for entry_id in ids:
                try:
                    entries.read_entry(db, entry_id, characterId)
                    available += 1
                except ValueError:
                    pass
            return {"state": "ready", "available": available, "excludedStale": len(ids) - available}

    @router.get("/entries")
    def search(characterId: int, asOf: str, offsetMinutes: int = 480, q: str = "",
               fromDate: str | None = None, toDate: str | None = None,
               limit: int = Query(20, ge=1, le=50), cursor: str | None = None):
        term = unicodedata.normalize("NFKC", q).casefold().strip()
        with connect() as db:
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (characterId,)).fetchone():
                raise HTTPException(404, "角色不可访问")
            try:
                matched = list(search_members(db, characterId, asOf, offsetMinutes, fromDate, toDate, q))
            except (TypeError, ValueError):
                raise HTTPException(422, "筛选条件无效") from None
            matched.sort(key=lambda m: (m.timestamp or datetime.min.replace(tzinfo=timezone.utc), m.entry_id), reverse=True)
            binding = {"characterId": characterId, "asOf": asOf, "offsetMinutes": offsetMinutes,
                "q": term, "fromDate": fromDate, "toDate": toDate,
                "generation": p2._generation(db, characterId),
                "snapshot": m3._hash([(m.entry_id, m.version_id, [(r["content_hash"], r["state_hash"])
                    for r in m.refs]) for m in matched])}
            offset = 0
            if cursor:
                try:
                    parsed = p2._cursor_decode(cursor)
                except ValueError:
                    raise HTTPException(409, "cursor_view_changed") from None
                if not isinstance(parsed, dict) or any(parsed.get(k) != v for k, v in binding.items()):
                    raise HTTPException(409, "cursor_view_changed")
                offset = parsed.get("offset")
                if type(offset) is not int or offset < 0 or offset >= len(matched):
                    raise HTTPException(409, "cursor_view_changed")
            page = matched[offset:offset + limit]
            next_offset = offset + len(page)
            return {"items": [{"entryId": m.entry_id, "title": m.detail["title"],
                "synopsis": m.detail["synopsis"], "keywords": m.detail["keywords"],
                "tags": m.detail["tags"], "occurredAt": m.detail["occurredAt"],
                "recordedAt": m.detail["recordedAt"], "timeBasis": m.detail["timeBasis"],
                "level": m.level, "sourceRefs": m.detail["sourceRefs"]} for m in page],
                "total": len(matched), "nextCursor": p2._cursor_encode({**binding, "offset": next_offset})
                    if next_offset < len(matched) else None}

    @router.get("/entries/{entry_id}")
    def detail(entry_id: str, characterId: int):
        with connect() as db:
            item = _checked_entry(db, characterId, entry_id)
            return {**item, "views": {"RAW": {"status": "available"},
                "NARRATIVE": _view_status(db, characterId, item, "NARRATIVE"),
                "CLEANED": _view_status(db, characterId, item, "CLEANED")}}

    @router.get("/entries/{entry_id}/views/{view_type}")
    def view(entry_id: str, view_type: str, characterId: int, offset: int = 0,
             limit: int = Query(4096, ge=1, le=4096), versionId: str | None = None):
        if view_type not in ("NARRATIVE", "CLEANED") or offset < 0:
            raise HTTPException(422, "视图或分页无效")
        with connect() as db:
            item = _checked_entry(db, characterId, entry_id)
            version = _view_version(db, characterId, item, view_type)
            if version is None:
                raise HTTPException(409, "视图未审核或已失效")
            if offset and versionId != version["id"]:
                raise HTTPException(409, "view_page_changed")
            body = version["body"]
            return {"entryId": entry_id, "viewType": view_type, "versionId": version["id"],
                "sourceRefs": item["sourceRefs"], "authorKind": "system_derived_text",
                "text": body[offset:offset + limit], "offset": offset, "totalLength": len(body),
                "nextOffset": offset + limit if offset + limit < len(body) else None}

    @router.get("/nodes/{node_id}/digest-display")
    def digest_display(node_id: str, characterId: int, asOf: str, offsetMinutes: int = 480,
                       offset: int = 0, limit: int = Query(4096, ge=1, le=4096),
                       digestVersionId: str | None = None):
        now = _view_time(asOf, offsetMinutes)
        if offset < 0:
            raise HTTPException(422, "分页无效")
        with connect() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (characterId,)).fetchone():
                raise HTTPException(404, "角色不可访问")
            node = p2._find(p2._tree(db, characterId, now, offsetMinutes), node_id)
            if node is None:
                raise HTTPException(409, "node_missing_or_stale")
            version, summary_status = p2._digest(db, characterId, node)
            if version is None:
                if offset or digestVersionId:
                    raise HTTPException(409, "digest_page_changed")
                return {"nodeId": node_id, "summaryStatus": summary_status, "mode": "unavailable",
                        "versionId": None, "sections": [], "text": "", "nextOffset": None}
            if (digestVersionId and digestVersionId != version["id"]) or (offset and not digestVersionId):
                raise HTTPException(409, "digest_page_changed")
            sections = _digest_sources(db, characterId, version)
            source_meta = [{key: section[key] for key in ("ordinal", "sourceRef", "role", "status", "evidenceKind")}
                           for section in sections]
            rule = version["rule_version"] == f"digest-extractive-v1:{node.level}:{node.period}"
            if rule:
                if not _extractive_body_matches(version, node, sections):
                    raise HTTPException(409, "digest_display_unverifiable")
                total = sum(len(section["text"]) for section in sections)
                if offset > total:
                    raise HTTPException(422, "分页无效")
                pieces, cursor, remaining = [], 0, limit
                for section in sections:
                    length = len(section["text"])
                    start = max(0, offset - cursor)
                    if start < length and remaining:
                        piece = section["text"][start:start + remaining]
                        pieces.append({"ordinal": section["ordinal"], "text": piece,
                                       "sourceOffset": start, "incomplete": start > 0 or start + len(piece) < length})
                        remaining -= len(piece)
                    cursor += length
                next_offset = offset + limit if offset + limit < total else None
                return {"nodeId": node_id, "summaryStatus": summary_status, "mode": "extractive",
                        "label": "摘录式摘要", "versionId": version["id"], "sourceMeta": source_meta,
                        "sections": pieces, "text": "", "offset": offset, "totalLength": total,
                        "nextOffset": next_offset}
            body = version["body"]
            if offset > len(body):
                raise HTTPException(422, "分页无效")
            return {"nodeId": node_id, "summaryStatus": summary_status, "mode": "reviewed_digest",
                    "label": "已审核周期摘要", "versionId": version["id"], "sourceMeta": source_meta,
                    "sections": [], "text": body[offset:offset + limit], "offset": offset,
                    "totalLength": len(body), "nextOffset": offset + limit if offset + limit < len(body) else None}

    @router.post("/projection-preview")
    def projection_preview(payload: ProjectionPreviewInput):
        now = _view_time(payload.asOf, payload.offsetMinutes)
        with connect() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1",
                              (payload.characterId,)).fetchone():
                raise HTTPException(404, "角色不可访问")
            try:
                projected = p2.context_projection(db, payload.characterId, payload.entryIds,
                    now=now, offset_minutes=payload.offsetMinutes,
                    budget_tokens=payload.budgetTokens, max_blocks=payload.maxBlocks)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            member_lookup = {m.entry_id: (m, node) for node in p2._tree(db, payload.characterId, now,
                              payload.offsetMinutes) for m in node.members}
            diagnostics = []
            for entry_id in payload.entryIds:
                pair = member_lookup.get(entry_id)
                if pair is None:
                    diagnostics.append({"entryId": entry_id, "eligibility": "missing_or_stale"})
                    continue
                member, node = pair
                _, summary_status = p2._digest(db, payload.characterId, node)
                available_views = ["RAW"] + [view for view in ("CLEANED", "NARRATIVE")
                    if _view_version(db, payload.characterId, member.detail, view) is not None]
                diagnostics.append({"entryId": entry_id, "eligibility": "current_validated",
                    "level": member.level, "period": member.period, "summaryStatus": summary_status,
                    "entryVersionId": member.version_id, "sourceRefs": p2._source_refs((member,)),
                    "availableViews": available_views})
            blocks = []
            for block in projected["blocks"]:
                version_refs = ([{"kind": "DIGEST", "id": block["digestVersionId"]}]
                    if block["projectionKind"] == "period_digest" else
                    [{"kind": "NARRATIVE", "id": block["narrativeVersionId"]}]
                    if block["projectionKind"] == "detailed_narrative" else
                    [{"kind": "ENTRY_METADATA", "entryId": entry_id,
                      "id": member_lookup[entry_id][0].version_id}
                     for entry_id in block["entryIds"]])
                blocks.append({**block, "versionRefs": version_refs})
            return {**projected, "blocks": blocks, "selectionDiagnostics": diagnostics,
                    "selectedCount": len(payload.entryIds),
                    "uniqueExperienceCount": len({m3._hash(sorted((r["source_kind"], r["source_id"])
                        for r in member_lookup[entry_id][0].refs)) for entry_id in payload.entryIds
                        if entry_id in member_lookup}),
                    "projectionOnly": True, "formalModelReceived": False}

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/memory-v2/calendar"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
