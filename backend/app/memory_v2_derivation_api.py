"""Installable M3 draft/read router. Not registered by the production app."""

import json

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field, ConfigDict

from . import memory_v2_derivation as derivation
from . import memory_v2_jobs as jobs


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRefInput(StrictInput):
    kind: str
    id: str


class DerivationInput(StrictInput):
    characterId: int
    viewType: str
    sourceRefs: list[SourceRefInput] = Field(min_length=1, max_length=500)


class DraftInput(StrictInput):
    characterId: int
    sourceRefs: list[SourceRefInput] = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=200000)
    childVersionIds: list[str] = Field(default_factory=list, max_length=500)


class ReviewInput(StrictInput):
    characterId: int
    outputHash: str
    inputHash: str


class ModelJobInput(StrictInput):
    characterId: int
    kind: str
    sourceRefs: list[SourceRefInput] = Field(min_length=1, max_length=500)
    modelMode: bool
    level: str | None = None
    period: str | None = None
    childVersionIds: list[str] = Field(default_factory=list, max_length=500)


def install(app, connect, authenticate=None, *, enable_model_jobs=False):
    def owner(request: Request):
        session = authenticate(request) if authenticate else None
        if not session:
            raise HTTPException(401, "需要登录后继续")
        if request.method not in ("GET", "HEAD") and request.headers.get("origin"):
            if request.headers["origin"].rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "跨站请求来源未获允许")
        return session

    router = APIRouter(prefix="/api/memory-v2/derivations", dependencies=[Depends(owner)])

    @router.post('/jobs', status_code=201)
    def create_job(payload: ModelJobInput):
        if not enable_model_jobs or payload.modelMode is not True:
            raise HTTPException(503, '隔离模型任务未启用')
        with connect() as db:
            try:
                result=jobs.enqueue_model(db,payload.characterId,payload.kind,
                    [r.model_dump() for r in payload.sourceRefs],level=payload.level,
                    period=payload.period,children=payload.childVersionIds)
                return {'jobId':result['id'],'status':result['status']}
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None

    @router.get('/jobs/{job_id}')
    def job_detail(job_id: str, characterId: int):
        if not enable_model_jobs:
            raise HTTPException(503,'隔离模型任务未启用')
        with connect() as db:
            try:
                return jobs.model_status(db,job_id,characterId)
            except ValueError:
                raise HTTPException(404,'任务不可访问') from None

    @router.post('/jobs/{job_id}/{action}')
    def job_control(job_id: str, action: str, characterId: int):
        if not enable_model_jobs or action not in ('pause','resume'):
            raise HTTPException(503,'隔离模型任务不可用')
        with connect() as db:
            try:
                return {'status':jobs.control(db,job_id,characterId,action)}
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None

    def checked(db, derivation_id, version_id, character_id):
        if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
            raise HTTPException(404, "角色不可访问")
        item = derivation._row(db, "mv3_derivations", derivation_id)
        version = derivation._row(db, "mv3_versions", version_id)
        if item is None or item["character_id"] != character_id or version is None or version["derivation_id"] != derivation_id:
            raise HTTPException(404, "派生版本不存在")
        return version

    @router.post("/{derivation_id}/versions/{version_id}/review")
    def review(derivation_id: str, version_id: str, payload: ReviewInput, session=Depends(owner)):
        with connect() as db:
            checked(db, derivation_id, version_id, payload.characterId)
            try:
                return derivation.review_version(db, version_id, payload.characterId, session["id"], payload.outputHash, payload.inputHash)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/{derivation_id}/versions/{version_id}/activate")
    def activate(derivation_id: str, version_id: str, characterId: int):
        with connect() as db:
            checked(db, derivation_id, version_id, characterId)
            try:
                result = derivation.activate_version(db, version_id, characterId)
                return {k: v for k, v in result.items() if k != "body"}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/{derivation_id}/versions")
    def history(derivation_id: str, characterId: int):
        with connect() as db:
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (characterId,)).fetchone():
                raise HTTPException(404, "角色不可访问")
            item = derivation._row(db, "mv3_derivations", derivation_id)
            if item is None or item["character_id"] != characterId:
                raise HTTPException(404, "派生项不存在")
            return [dict(row) for row in db.execute("""SELECT id,version,status,review_status,
                validation_status,author_kind,created_at FROM mv3_versions WHERE derivation_id=? ORDER BY version""", (derivation_id,))]

    @router.get("/{derivation_id}/versions/{version_id}/preview")
    def preview(derivation_id: str, version_id: str, characterId: int):
        with connect() as db:
            version = checked(db, derivation_id, version_id, characterId)
            reason = derivation._validity(db, version_id, characterId, set(), draft=True)
            if reason:
                raise HTTPException(409, reason)
            return {"body": version["body"], "lifecycleStatus": version["status"],
                    "validationStatus": version["validation_status"], "reviewStatus": version["review_status"],
                    "eligibleForRecall": False, "authorKind": "system_derived_text",
                    "sourceRefs": derivation._refs(db, version_id),
                    "sourceMapping": json.loads(version['mapping_json'] or '[]')}

    @router.get("/{derivation_id}/versions/{version_id}/sources")
    def sources(derivation_id: str, version_id: str, characterId: int):
        with connect() as db:
            checked(db, derivation_id, version_id, characterId)
            reason = derivation._validity(db, version_id, characterId, set(), draft=True)
            if reason:
                raise HTTPException(409, reason)
            return [{"sourceRef": ref, "text": derivation.live_source(db, characterId,
                    ref["source_kind"], ref["source_id"]).text} for ref in derivation._refs(db, version_id)]

    @router.post("", status_code=201)
    def create(payload: DerivationInput):
        with connect() as db:
            try:
                return derivation.create_derivation(db, payload.characterId, payload.viewType,
                    [ref.model_dump() for ref in payload.sourceRefs])
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/{derivation_id}")
    def detail(derivation_id: str, characterId: int):
        with connect() as db:
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (characterId,)).fetchone():
                raise HTTPException(404, "角色不可访问")
            row = derivation._row(db, "mv3_derivations", derivation_id)
            if row is None or row["character_id"] != characterId:
                raise HTTPException(404, "派生项不存在")
            return row

    @router.post("/{derivation_id}/versions", status_code=201)
    def draft(derivation_id: str, payload: DraftInput, session=Depends(owner)):
        with connect() as db:
            owner = derivation._row(db, "mv3_derivations", derivation_id)
            if owner is None or owner["character_id"] != payload.characterId:
                raise HTTPException(404, "派生项不存在")
            try:
                result = derivation.add_version(db, derivation_id,
                    [ref.model_dump() for ref in payload.sourceRefs], payload.body,
                    author_kind="user", child_version_ids=payload.childVersionIds)
                db.execute("UPDATE mv3_versions SET edited_by=? WHERE id=?", (session["id"], result["id"]))
                return {key: value for key, value in result.items() if key != "body"}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/{derivation_id}/versions/{version_id}")
    def version_detail(derivation_id: str, version_id: str, characterId: int):
        with connect() as db:
            owner = derivation._row(db, "mv3_derivations", derivation_id)
            version = derivation._row(db, "mv3_versions", version_id)
            if (owner is None or owner["character_id"] != characterId or version is None
                    or version["derivation_id"] != derivation_id):
                raise HTTPException(404, "派生版本不存在")
            try:
                return derivation.read_version(db, version_id, characterId)
            except ValueError:
                raise HTTPException(409, "派生版本待核验或来源已失效") from None

    @app.middleware("http")
    async def no_store_derivation(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/memory-v2/derivations"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
