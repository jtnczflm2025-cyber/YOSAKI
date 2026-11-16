from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from . import audio_cache, auto_budget, config, expression_product, manager, scribe_budget, storage
from .audio_jobs import VoiceJobs
from .schemas import AutoBudgetSettingsInput, ConsentInput, ProfileInput, ScribeBudgetSettingsInput, SpeakInput


def make_router(connect, jobs: VoiceJobs | None = None) -> APIRouter:
    jobs = jobs or VoiceJobs(connect)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await jobs.shutdown()

    router = APIRouter(prefix="/api/voice", tags=["voice"], lifespan=lifespan)

    def available() -> None:
        if not config.enabled():
            raise HTTPException(503, "ElevenLabs 语音未启用")

    @router.get("/status")
    def get_status():
        result = manager.status()
        result["auto_daily_budget_enabled"] = auto_budget.daily_enabled()
        result["scribe_daily_enabled"] = scribe_budget.enabled()
        if result["expression_enabled"]:
            with connect() as db:
                result["expression_enabled"] = expression_product.schema_present(db)
        return result

    @router.get("/privacy")
    def get_privacy(request: Request):
        with connect() as db:
            record = storage.consent(db, request.state.user["id"]) if config.enabled() else None
        return {"notice_version": config.NOTICE_VERSION, "provider": config.PROVIDER,
                "accepted": bool(record and record["notice_version"] == config.NOTICE_VERSION),
                "accepted_at": record["accepted_at"] if record else None}

    @router.put("/privacy/consent")
    def accept_privacy(payload: ConsentInput, request: Request):
        available()
        with connect() as db:
            record = storage.put_consent(db, request.state.user["id"])
        return {"notice_version": record["notice_version"], "accepted": True,
                "accepted_at": record["accepted_at"]}

    @router.delete("/privacy/consent", status_code=204)
    def revoke_privacy(request: Request):
        available()
        jobs.cancel_owner(request.state.user["id"])
        with connect() as db:
            storage.revoke_consent(db, request.state.user["id"])
        return Response(status_code=204)

    @router.get("/profiles")
    def get_profile(character_id: int):
        with connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_profiles'").fetchone() is None:
                raise HTTPException(409, "声音档案尚未初始化")
            if db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone() is None:
                raise HTTPException(404, "角色不存在")
            return storage.profile(db, character_id)

    @router.put("/profiles/{character_id}")
    def update_profile(character_id: int, payload: ProfileInput, request: Request):
        if not config.enabled() and payload.active:
            raise HTTPException(409, "语音服务关闭时只能保存非活动声音档案")
        with connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_profiles'").fetchone() is None:
                raise HTTPException(409, "声音档案尚未初始化")
            if payload.active:
                row = db.execute("SELECT enabled FROM characters WHERE id=?", (character_id,)).fetchone()
                if row is None:
                    raise HTTPException(404, "角色不存在")
                if not row["enabled"]:
                    raise HTTPException(409, "角色已停用")
                record = storage.consent(db, request.state.user["id"])
                if not record or record["notice_version"] != config.NOTICE_VERSION:
                    raise HTTPException(403, "请先确认第三方音频处理告知")
            try:
                result = storage.put_profile(db, character_id, payload)
                jobs.cancel_owner(request.state.user["id"], character_id)
                return result
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.post("/profiles/{character_id}/test")
    def test_profile(character_id: int):
        available()
        raise HTTPException(409, "真实 ElevenLabs API 验收尚未获单独批准")

    @router.get("/messages/{message_id}/speech-preview")
    def speech_preview(message_id: int, request: Request,
                       mode: Literal["none", "thoughtful"] = "none"):
        context = manager.speech_context(connect, message_id, request.state.user["id"], mode)
        return {"message_id": message_id, "segments": context["segments"],
                "safe_segments": context["safe_segments"],
                "mode": context["mode"], "expression_tag": context["expression_tag"],
                "expression_planner_version": context["expression_planner_version"],
                "skipped_reasons": context["skipped_reasons"],
                "characters": sum(map(len, context["segments"])),
                "non_whitespace_characters": sum(
                    not char.isspace() for segment in context["segments"] for char in segment),
                "requests": len(context["segments"]),
                "reference_language": context["reference_language"],
                "language_code": None if context["language"] == "auto" else context["language"],
                "language_reason": context["language_reason"],
                "model_id": "eleven_v4", "output_format": "mp3_44100_128",
                "profile_revision": context["revision"],
                "voice_id_masked": "…" + context["voice_id"][-4:],
                "effective_parameters": {"settings": "omitted"},
                "preview_sha256": context["digest"],
                "billing": "实际费用以供应商账户为准"}

    @router.get("/messages/{message_id}/audio")
    def cached_message_audio(message_id: int, request: Request,
                             mode: Literal["none", "thoughtful"] = "none"):
        """Return only a currently authorized, complete cache binding."""
        context = manager.speech_context(connect, message_id, request.state.user["id"], mode)
        return {"message_id": message_id, "audio": jobs.bindings(context)}

    @router.post("/messages/{message_id}/speak", status_code=202)
    async def speak(message_id: int, payload: SpeakInput, request: Request):
        context = manager.speech_context(connect, message_id, request.state.user["id"], payload.mode)
        if context["digest"] != payload.preview_sha256:
            raise HTTPException(409, "消息或声音档案已变化，请重新预览")
        return jobs.submit(context, request.state.user["id"])

    @router.get("/auto-budget")
    def auto_budget_status(request: Request):
        try:
            return auto_budget.usage(connect, request.state.user["token_hash"],
                                     request.state.user["id"])
        except auto_budget.BudgetUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/auto-budget/settings")
    def auto_budget_settings(request: Request):
        try:
            return auto_budget.settings(connect, request.state.user["id"])
        except auto_budget.BudgetUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/scribe-budget")
    def scribe_budget_status(request: Request):
        try:
            return scribe_budget.usage(connect, request.state.user["id"])
        except scribe_budget.BudgetUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/scribe-budget/settings")
    def scribe_budget_settings(request: Request):
        try:
            return scribe_budget.settings(connect, request.state.user["id"])
        except scribe_budget.BudgetUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put("/scribe-budget/settings")
    def save_scribe_budget_settings(payload: ScribeBudgetSettingsInput, request: Request):
        try:
            return scribe_budget.put_settings(
                connect, request.state.user["id"], payload.enabled,
                scribe_budget.Limits(payload.attempts, payload.connections,
                                     payload.audio_seconds),
                payload.expected_revision, payload.confirm_increase,
                payload.confirm_enable)
        except (scribe_budget.BudgetUnavailable, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put("/auto-budget/settings")
    def save_auto_budget_settings(payload: AutoBudgetSettingsInput, request: Request):
        try:
            return auto_budget.put_settings(
                connect, request.state.user["id"],
                auto_budget.Limits(payload.jobs, payload.requests, payload.characters),
                payload.expected_revision, payload.confirm_increase)
        except (auto_budget.BudgetUnavailable, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/messages/{message_id}/auto-speak", status_code=202)
    async def auto_speak(message_id: int, payload: SpeakInput, request: Request):
        if not auto_budget.enabled():
            raise HTTPException(503, "自动朗读尚未启用")
        if payload.mode != "none":
            raise HTTPException(409, "自动朗读首版只支持无标签模式")
        context = manager.speech_context(connect, message_id, request.state.user["id"], "none")
        if context["digest"] != payload.preview_sha256:
            raise HTTPException(409, "消息或声音档案已变化，请重新预览")
        # The route itself is default-off. The durable limit is checked again,
        # atomically, immediately before each new provider request.
        try:
            auto_budget.usage(connect, request.state.user["token_hash"],
                              request.state.user["id"])
        except auto_budget.BudgetUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc
        return jobs.submit(context, request.state.user["id"],
                           auto_session_hash=request.state.user["token_hash"])

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        return jobs.get(job_id, request.state.user["id"])

    @router.delete("/jobs/{job_id}", status_code=204)
    def cancel_job(job_id: str, request: Request):
        jobs.cancel(job_id, request.state.user["id"])
        return Response(status_code=204)

    @router.get("/audio/{binding_id}")
    def get_audio(binding_id: int, request: Request):
        path = jobs.audio_path(binding_id, request.state.user["id"])
        return FileResponse(path, media_type="audio/mpeg",
                            headers={"Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @router.get("/expression-audio/{binding_id}")
    def get_expression_audio(binding_id: int, request: Request):
        if not config.enabled() or not config.expression_enabled():
            raise HTTPException(403, "实验表达朗读尚未启用")
        with connect() as db:
            if not expression_product.schema_present(db):
                raise HTTPException(409, "表达音频绑定表尚未受控迁移")
        path = jobs.audio_path(binding_id, request.state.user["id"], expression=True)
        return FileResponse(path, media_type="audio/mpeg",
                            headers={"Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @router.delete("/cache")
    def clear_cache(request: Request, character_id: int | None = None):
        if any(job.owner_id == request.state.user["id"] and
               job.status in ("queued", "generating") for job in jobs.jobs.values()):
            raise HTTPException(409, "请先停止当前朗读生成任务")
        with connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_audio_cache'").fetchone() is None:
                return {"removed": 0}
            if character_id is not None and db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone() is None:
                raise HTTPException(404, "角色不存在")
        return {"removed": audio_cache.clear(connect, character_id)}

    return router
