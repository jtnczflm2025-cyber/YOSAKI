"""Installable P2 browse API for isolated tests; production main does not install it."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from . import memory_v2_time_projection as p2


def install(app, connect, authenticate):
    def owner(request: Request):
        session = authenticate(request)
        if not session:
            raise HTTPException(401, "需要登录后继续")
        return session

    def as_of(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise HTTPException(422, "时间视角格式错误") from None

    router = APIRouter(prefix="/api/memory-v2/chronicle", dependencies=[Depends(owner)])

    @router.get("/tree")
    def tree(characterId: int, asOf: str, offsetMinutes: int = 480, levels: str | None = None,
             limit: int = 20, cursor: str | None = None):
        selected = None if levels is None else [None if part == "undated" else part
                                                  for part in levels.split(",")]
        with connect() as db:
            try:
                return p2.browse(db, characterId, now=as_of(asOf), offset_minutes=offsetMinutes,
                                 levels=selected, limit=limit, cursor=cursor)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/nodes/{node_id}")
    def node(node_id: str, characterId: int, asOf: str, offsetMinutes: int = 480,
             offset: int = 0, limit: int = 4096, memberLimit: int = 50,
             memberCursor: str | None = None, digestVersionId: str | None = None):
        with connect() as db:
            try:
                return p2.expand_node(db, characterId, node_id, now=as_of(asOf),
                                      offset_minutes=offsetMinutes, offset=offset, limit=limit,
                                      member_limit=memberLimit, member_cursor=memberCursor,
                                      expected_digest_version_id=digestVersionId)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @router.get("/entries/{entry_id}/raw/{ordinal}")
    def raw(entry_id: str, ordinal: int, characterId: int, offset: int = 0,
            limit: int = 4096, contentHash: str | None = None):
        with connect() as db:
            try:
                return p2.unfold_raw(db, characterId, entry_id, ordinal, offset=offset,
                                     limit=limit, expected_content_hash=contentHash)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/memory-v2/chronicle"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
