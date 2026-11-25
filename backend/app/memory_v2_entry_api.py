"""Installable P1-A API for isolated acceptance; production main does not register it."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import memory_v2_entries as entries
from . import memory_v2_derivation as m3


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ref(StrictInput):
    kind: str
    id: str


class Create(StrictInput):
    characterId: int
    sourceRefs: list[Ref] = Field(min_length=1, max_length=500)
    title: str
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    synopsis: str
    narrativeVersionId: str | None = None


class Revision(Create):
    pass


class Review(StrictInput):
    characterId: int
    metadataHash: str
    sourceHash: str


def install(app, connect, authenticate, *, allowed_origins=()):
    trusted_origins = {origin.rstrip("/") for origin in allowed_origins}
    def owner(request: Request, response: Response):
        session = authenticate(request)
        if not session:
            raise HTTPException(401, "需要登录后继续")
        if request.method not in ("GET", "HEAD") and request.headers.get("origin"):
            if request.headers["origin"].rstrip("/") not in trusted_origins | {str(request.base_url).rstrip("/")}:
                raise HTTPException(403, "跨站请求来源未获允许")
        response.headers["Cache-Control"] = "no-store"
        return session

    router = APIRouter(prefix="/api/memory-v2/entries", dependencies=[Depends(owner)])

    def checked(db, entry_id, character_id):
        entry = entries._entry(db, entry_id)
        if entry is None or entry["character_id"] != character_id:
            raise HTTPException(404, "条目不存在")
        if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
            raise HTTPException(404, "角色不可访问")
        return entry

    @router.post("", status_code=201)
    def create(payload: Create, session=Depends(owner)):
        with connect() as db:
            try:
                entry = entries.create_entry(db, payload.characterId)
                version = entries.add_version(db, entry["id"], [r.model_dump() for r in payload.sourceRefs],
                    payload.title, payload.keywords, payload.tags, payload.synopsis,
                    payload.narrativeVersionId, session["id"])
                return {"entryId": entry["id"], "versionId": version["id"], "status": "draft",
                        "metadataHash": version["metadata_hash"], "sourceHash": version["source_hash"]}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/{entry_id}/versions", status_code=201)
    def revise(entry_id: str, payload: Revision, session=Depends(owner)):
        with connect() as db:
            checked(db, entry_id, payload.characterId)
            try:
                version = entries.add_version(db, entry_id, [r.model_dump() for r in payload.sourceRefs],
                    payload.title, payload.keywords, payload.tags, payload.synopsis,
                    payload.narrativeVersionId, session["id"])
                return {"entryId": entry_id, "versionId": version["id"], "status": "draft",
                        "metadataHash": version["metadata_hash"], "sourceHash": version["source_hash"]}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/{entry_id}/versions/{version_id}/review")
    def review(entry_id: str, version_id: str, payload: Review, session=Depends(owner)):
        with connect() as db:
            checked(db, entry_id, payload.characterId)
            version = entries._version(db, version_id)
            if version is None or version["entry_id"] != entry_id:
                raise HTTPException(404, "版本不存在")
            try:
                return entries.review_version(db, version_id, payload.characterId, session["id"],
                                              payload.metadataHash, payload.sourceHash)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/{entry_id}/versions/{version_id}/activate")
    def activate(entry_id: str, version_id: str, characterId: int):
        with connect() as db:
            checked(db, entry_id, characterId)
            version = entries._version(db, version_id)
            if version is None or version["entry_id"] != entry_id:
                raise HTTPException(404, "版本不存在")
            try:
                result = entries.activate_version(db, version_id, characterId)
                return {"entryId": entry_id, "versionId": result["id"], "status": "active"}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/{entry_id}")
    def detail(entry_id: str, characterId: int):
        with connect() as db:
            checked(db, entry_id, characterId)
            try:
                return entries.read_entry(db, entry_id, characterId)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/{entry_id}/versions")
    def history(entry_id: str, characterId: int):
        with connect() as db:
            checked(db, entry_id, characterId)
            return [dict(row) for row in db.execute("""SELECT id,version,status,review_status,created_at
                FROM mv3_entry_versions WHERE entry_id=? ORDER BY version""", (entry_id,))]

    @router.get("/{entry_id}/versions/{version_id}/preview")
    def preview(entry_id: str, version_id: str, characterId: int):
        with connect() as db:
            checked(db, entry_id, characterId)
            version = entries._version(db, version_id)
            if version is None or version["entry_id"] != entry_id:
                raise HTTPException(404, "版本不存在")
            reason = entries.validity(db, version_id, characterId, preview=True)
            if reason:
                raise HTTPException(409, reason)
            return {"entryId": entry_id, "versionId": version_id, "title": version["title"],
                    "keywords": json.loads(version["keywords_json"]),
                    "tags": json.loads(version["tags_json"]),
                    "synopsis": version["synopsis"], "narrativeVersionId": version["narrative_version_id"],
                    "timeBasis": version["time_basis"], "occurredAt": [version["occurred_start"], version["occurred_end"]],
                    "recordedAt": [version["recorded_start"], version["recorded_end"]],
                    "lifecycleStatus": version["status"], "reviewStatus": version["review_status"],
                    "eligibleForRecall": False, "authority": "system_derived_text",
                    "sourceRefs": [{"kind": r["source_kind"], "id": r["source_id"]}
                                   for r in entries._refs(db, version_id)]}

    @router.get("/{entry_id}/versions/{version_id}/sources")
    def sources(entry_id: str, version_id: str, characterId: int):
        with connect() as db:
            checked(db, entry_id, characterId)
            version = entries._version(db, version_id)
            if version is None or version["entry_id"] != entry_id:
                raise HTTPException(404, "版本不存在")
            reason = entries.validity(db, version_id, characterId, preview=True)
            if reason:
                raise HTTPException(409, reason)
            unfolded = []
            for ref in entries._refs(db, version_id):
                live = m3.live_source(db, characterId, ref["source_kind"], ref["source_id"])
                if live is None or live.content_hash != ref["content_hash"] or live.state_hash != ref["state_hash"]:
                    raise HTTPException(409, "source_missing_or_stale")
                unfolded.append({"sourceRef": {"kind": ref["source_kind"], "id": ref["source_id"]},
                                 "text": live.text})
            return unfolded

    @app.middleware("http")
    async def no_store_entries(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/memory-v2/entries"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
