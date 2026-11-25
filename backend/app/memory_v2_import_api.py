"""Installable Chronicle import API; deliberately not registered by main.py."""

import hashlib
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import archives
from . import memory_v2_import as importer
from . import memory_v2_index as indexer
from .memory_v2_import_preview import preview_upload, batch_preview_fingerprint, MAX_FILES


class BoundaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    characterId: int = Field(gt=0)
    fileId: str
    expectedVersion: int = Field(ge=1)
    segments: list[dict] = Field(min_length=1, max_length=500)


def install(app, connect, authenticate, *, allowed_origins=()):
    trusted_origins = {origin.rstrip("/") for origin in allowed_origins}
    def owner(request: Request):
        if not authenticate(request):
            raise HTTPException(401, "需要登录后继续")
        if request.method not in ("GET", "HEAD") and request.headers.get("origin"):
            if request.headers["origin"].rstrip("/") not in trusted_origins | {str(request.base_url).rstrip("/")}:
                raise HTTPException(403, "跨站请求来源未获允许")

    router = APIRouter(prefix="/api/memory-v2/imports", dependencies=[Depends(owner)])

    def response(data):
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def uploads(files):
        if not files or len(files) > MAX_FILES:
            raise HTTPException(413, "文件数量无效或超过 200")
        bodies, total = [], 0
        for file in files:
            limit = archives.MAX_TOTAL if (file.filename or "").lower().endswith(".zip") else archives.MAX_FILE
            try:
                body = await file.read(limit + 1)
            finally:
                await file.close()
            total += len(body)
            if len(body) > limit or total > archives.MAX_TOTAL:
                raise HTTPException(413, "上传文件或总量超限")
            bodies.append((file.filename or "", body))
        return bodies

    @router.post("/preview")
    async def preview(characterId: int = Form(...), files: list[UploadFile] = File(...),
                      confirmedOffsetMinutes: int | None = Form(None)):
        bodies = await uploads(files)
        with connect() as db:
            if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (characterId,)).fetchone():
                raise HTTPException(404, "角色不可访问")
            previews = []
            for name, body in bodies:
                previews.extend(preview_upload(name, body, db, characterId, confirmedOffsetMinutes))
                if len(previews) > MAX_FILES:
                    raise HTTPException(413, "展开后文件数量超过 200")
        return response({"files": previews, "confirmed": False,
                         "previewFingerprint": batch_preview_fingerprint(previews)})

    @router.post("/batches")
    async def prepare(request: Request, tasks: BackgroundTasks, characterId: int = Form(...), files: list[UploadFile] = File(...),
                      withPlanSynopsis: bool = Form(False),
                      confirmedOffsetMinutes: int | None = Form(None),
                      expectedPreviewFingerprint: str | None = Form(None)):
        bodies = await uploads(files)
        with connect() as db:
            user=authenticate(request) if withPlanSynopsis else None
            result=importer.prepare(db, characterId, bodies, confirmedOffsetMinutes,
                                    expectedPreviewFingerprint,plan_owner=user['id'] if user else None)
        if withPlanSynopsis:
            from .memory_v2_import_processing import run
            tasks.add_task(run,connect,result['batchId'])
        return response(result)

    @router.post("/batches/{batch_id}/prepare-review")
    def prepare_review(batch_id: str, characterId: int, request: Request, tasks: BackgroundTasks, onlyUnsent: bool=False):
        from . import memory_v2_import_processing as pipeline
        with connect() as db:
            user=authenticate(request)
            if not user:raise HTTPException(401,"需要登录")
            if importer._batch(db,batch_id,characterId)['status']!='raw_complete':
                raise HTTPException(409,"请先完成原文归档")
            if onlyUnsent:
                pipeline.continue_unsent(db,batch_id,characterId,user['id'])
            else:
                pipeline.grant(db,batch_id,characterId,user['id'])
        tasks.add_task(pipeline.run,connect,batch_id)
        return response({'batchId':batch_id})

    @router.get("/batches/{batch_id}")
    def batch_status(batch_id: str, characterId: int):
        with connect() as db:
            result=importer.status(db, batch_id, characterId)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_jobs'").fetchone():
                from .memory_v2_import_processing import progress
                result['processing']=progress(db,batch_id,characterId)
            return response(result)

    @router.get("/batches")
    def batches(characterId: int):
        with connect() as db:
            return response({"items": importer.list_batches(db, characterId)})

    @router.get("/raw-files")
    def raw_files(characterId: int, fromDate: str | None = None, toDate: str | None = None):
        with connect() as db:
            return response({"items": importer.raw_files(db, characterId, fromDate, toDate)})

    @router.get("/raw-search")
    def raw_search(characterId: int, q: str, afterId: int = 0, limit: int = 30,
                   snapshot: str | None = None):
        with connect() as db:
            return response(importer.search_raw(db, characterId, q, afterId, limit, snapshot))

    @router.delete("/batches/{batch_id}")
    def discard(batch_id: str, characterId: int):
        with connect() as db:
            return response(importer.discard_preview(db, batch_id, characterId))

    @router.post("/batches/{batch_id}/confirm")
    def confirm(batch_id: str, characterId: int, expectedVersion: int | None = None,
                expectedPreviewFingerprint: str | None = None):
        with connect() as db:
            return response(importer.confirm(db, batch_id, characterId, expectedVersion,
                                             expectedPreviewFingerprint))

    @router.post("/batches/{batch_id}/step")
    def step(batch_id: str, characterId: int):
        with connect() as db:
            return response(importer.step(db, batch_id, characterId))

    @router.post("/batches/{batch_id}/pause")
    def pause(batch_id: str, characterId: int):
        with connect() as db:
            return response(importer.set_paused(db, batch_id, characterId, True))

    @router.post("/batches/{batch_id}/resume")
    def resume(batch_id: str, characterId: int):
        with connect() as db:
            return response(importer.set_paused(db, batch_id, characterId, False))

    @router.put("/batches/{batch_id}/boundaries")
    def boundaries(batch_id: str, payload: BoundaryInput):
        with connect() as db:
            return response(importer.replace_segments(db, batch_id, payload.characterId,
                                                      payload.fileId, payload.segments, payload.expectedVersion))

    @router.post("/batches/{batch_id}/draft-entries")
    def draft_entries(batch_id: str, characterId: int):
        with connect() as db:
            return response({"entries": importer.create_draft_entries(db, batch_id, characterId)})

    @router.post("/index/start")
    def index_start(characterId: int, reuseRunning: bool = False):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            importer.list_batches(db, characterId)
            if reuseRunning:
                active = db.execute("SELECT * FROM mv2_jobs WHERE character_id=? AND status='running'", (characterId,)).fetchone()
                if active:
                    return response(dict(active))
            try:
                meta = db.execute('SELECT state FROM mv2_meta WHERE character_id=?', (characterId,)).fetchone()
                mode = 'incremental' if meta and meta['state'] == 'ready' else 'rebuild'
                return response(indexer.start_job(db, characterId, mode))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.post("/index/{job_id}/step")
    def index_step(job_id: str, characterId: int):
        with connect() as db:
            importer.list_batches(db, characterId)
            job = indexer._job(db, job_id)
            if job is None or job["character_id"] != characterId:
                raise HTTPException(404, "索引任务不存在")
            try:
                return response(indexer.step_job(db, job_id))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/batches/{batch_id}/raw/{file_id}")
    def raw(batch_id: str, file_id: str, characterId: int, after: int = 0,
            limit: int = 30, snapshot: str | None = None):
        if not 0 <= after or not 1 <= limit <= 50:
            raise HTTPException(422, "分页范围无效")
        with connect() as db:
            importer._batch(db, batch_id, characterId)
            file = db.execute("SELECT * FROM mv2_import_files WHERE id=? AND batch_id=? AND character_id=?",
                              (file_id, batch_id, characterId)).fetchone()
            if file is None:
                raise HTTPException(404, "档案文件不存在")
            if not importer.valid_raw_file(db, file, characterId):
                raise HTTPException(409, "档案来源已失效，请刷新")
            rows = db.execute("""SELECT r.*,m.content,m.role,m.conversation_id,c.archive_only,c.character_id AS owner_character
                FROM mv2_import_receipts r JOIN messages m ON m.id=r.message_id
                JOIN conversations c ON c.id=m.conversation_id
                WHERE r.file_id=? ORDER BY r.ordinal""", (file_id,)).fetchall()
            current = hashlib.sha256(json.dumps([(r["message_id"], r["ordinal"], r["content"], r["owner_character"])
                                                 for r in rows], ensure_ascii=False).encode()).hexdigest()
            if snapshot is not None and snapshot != current:
                raise HTTPException(409, "档案内容已变化，请从第一页刷新")
            for row in rows:
                if (row["owner_character"] != characterId or row["character_id"] != characterId
                        or row["conversation_id"] != file["conversation_id"] or not row["archive_only"] or
                        hashlib.sha256(row["content"].encode()).hexdigest() != row["content_sha256"]):
                    raise HTTPException(409, "档案来源已失效，请刷新")
            visible = [r for r in rows if r["role"] in ("user", "assistant") and r["ordinal"] > after]
            page = visible[:limit]
            return response({"fileId": file_id, "status": file["status"], "snapshot": current,
                             "items": [{"messageId": row["message_id"], "ordinal": row["ordinal"],
                                        "role": row["role"], "content": row["content"],
                                        "originalTime": row["original_time_text"], "occurredAt": row["occurred_at"],
                                        "occurredDate": row["occurred_date"], "timePrecision": row["time_precision"],
                                        "externalMessageId": row["external_message_id"],
                                        "conversionNote": row["conversion_note"]} for row in page],
                             "nextAfter": page[-1]["ordinal"] if len(visible) > limit else None,
                             "excludedNonDialogue": len(rows) - sum(r["role"] in ("user", "assistant") for r in rows)})

    app.include_router(router)
