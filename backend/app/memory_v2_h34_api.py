"""Owner-session-only H3.4 API for an explicitly installed isolated app."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import memory_v2_h34 as titles
from .memory_v2_derivation import _now


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TitleItem(Strict):
    entryId: str
    title: str = Field(min_length=1, max_length=200)
    expectedVersion: str
    expectedFieldId: str
    expectedFieldToken: str


class Confirmation(Strict):
    characterId: int
    batchId: str
    fileId: str
    batchKey: str = Field(min_length=1, max_length=120)
    expectedFileSha: str
    expectedParser: str
    expectedSourceSnapshot: str
    expectedPlanHash: str
    items: list[TitleItem] = Field(min_length=13, max_length=13)


def install(app, connect, authenticate, *, allowed_origins=()):
    def owner(request: Request, response: Response):
        identity = authenticate(request)
        if not identity:
            raise HTTPException(401, 'authentication_required')
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') not in {
                *allowed_origins, str(request.base_url).rstrip('/')}:
            raise HTTPException(403, 'origin_rejected')
        response.headers['Cache-Control'] = 'no-store'
        return identity

    router = APIRouter(prefix='/api/memory-v2/h34', dependencies=[Depends(owner)])

    @router.post('/title-confirmations')
    def confirm(payload: Confirmation, identity=Depends(owner)):
        try:
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                token_hash = identity.get('token_hash')
                if (not token_hash or not db.execute('''SELECT 1 FROM auth_sessions s
                    JOIN users u ON u.id=s.user_id
                    JOIN characters c ON c.id=? AND c.enabled=1
                    WHERE s.token_hash=? AND s.user_id=? AND s.expires_at>?''',
                    (payload.characterId, token_hash, identity['id'], _now())).fetchone()):
                    raise HTTPException(404, 'role_unavailable')
                titles.init_db(db)
                return titles.confirm_titles(db, payload.characterId,
                    payload.batchId, payload.fileId, batch_key=payload.batchKey,
                    expected_file_sha=payload.expectedFileSha,
                    expected_parser=payload.expectedParser,
                    expected_source_snapshot=payload.expectedSourceSnapshot,
                    expected_plan_hash=payload.expectedPlanHash,
                    items=[item.model_dump() for item in payload.items],
                    reviewer_id=identity['id'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    app.include_router(router)

    @app.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/memory-v2/h34'):
            response.headers['Cache-Control'] = 'no-store'
        return response
