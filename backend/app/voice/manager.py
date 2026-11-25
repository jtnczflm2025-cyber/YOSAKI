"""Voice availability and saved-message authority checks."""
from __future__ import annotations

from fastapi import HTTPException

from . import automatic_expression, config, expression_product, storage
from .speech_planner import MAX_SEGMENT, PLANNER_VERSION, SpeechPlanError, digest, plan, request_language


def status() -> dict:
    return {"enabled": config.enabled(), "provider": config.PROVIDER,
            "api_key_configured": config.configured(), "real_api_verified": False,
            "models": config.defaults(), "planner_version": PLANNER_VERSION,
            "expression_enabled": config.enabled() and config.expression_enabled()}


def speech_context(connect, message_id: int, owner_id: int, mode: str = "none") -> dict:
    if mode not in expression_product.PRODUCT_MODES:
        raise HTTPException(422, "日常消息朗读不支持该表达模式")
    if not config.enabled():
        raise HTTPException(503, "ElevenLabs 语音未启用")
    if not config.configured():
        raise HTTPException(503, "ElevenLabs 密钥未配置")
    with connect() as db:
        if mode == "thoughtful":
            if not config.expression_enabled():
                raise HTTPException(403, "实验表达朗读尚未启用")
            if not expression_product.schema_present(db):
                raise HTTPException(409, "表达音频绑定表尚未受控迁移")
        row = db.execute("""SELECT m.*,c.character_id,c.archive_only AS archived,
                   ch.enabled AS character_enabled
                   FROM messages m JOIN conversations c ON c.id=m.conversation_id
                   JOIN characters ch ON ch.id=c.character_id WHERE m.id=?""",
                         (message_id,)).fetchone() if any(
                             x[1] == "archive_only" for x in db.execute("PRAGMA table_info(conversations)")) else None
        if row is None:
            row = db.execute("""SELECT m.*,c.character_id,0 AS archived,
                       ch.enabled AS character_enabled
                       FROM messages m JOIN conversations c ON c.id=m.conversation_id
                       JOIN characters ch ON ch.id=c.character_id WHERE m.id=?""",
                             (message_id,)).fetchone()
        from .. import chat_schema,chat_history
        if chat_schema.installed(db) and not chat_history.message_available(db,message_id):
            raise HTTPException(409,'这份回复已退出当前路径，请先选定要朗读的版本')
        if row is None or row["archived"]:
            raise HTTPException(404, "消息不存在")
        if (not row["character_enabled"] or row["role"] != "assistant" or
                not row["content"].strip() or row["generation_status"] not in (None, "completed") or
                row["tool_calls_json"] or row["tool_batch_status"]):
            raise HTTPException(409, "此消息不支持朗读")
        profile = storage.profile(db, row["character_id"])
        consent = storage.consent(db, owner_id)
        if not profile or not profile["active"] or not consent or consent["notice_version"] != config.NOTICE_VERSION:
            raise HTTPException(403, "请启用声音档案并确认第三方处理告知")
        settings = profile["configuration"]
        if settings["model_standard"] != "eleven_v4" or settings["audio_format"] != "mp3_44100_128":
            raise HTTPException(409, "当前声音档案不支持 V2 HTTP 朗读格式")
        try:
            skipped_reasons: set[str] = set()
            segments = plan(row["content"], skipped_reasons)
        except SpeechPlanError as exc:
            raise HTTPException(422, str(exc)) from exc
        language, language_reason = request_language(segments)
        base = {"message_id": message_id, "character_id": row["character_id"],
                "body": row["content"], "status": row["generation_status"],
                "revision": profile["revision"], "voice_id": profile["voice_id"],
                "language": language, "language_reason": language_reason,
                "reference_language": settings["language"], "segments": segments,
                "skipped_reasons": sorted(skipped_reasons),
                "digest": digest(message_id, row["content"], row["generation_status"],
                                 profile["revision"], segments, voice_id=profile["voice_id"],
                                 model_id=settings["model_standard"],
                                 output_format=settings["audio_format"],
                                 language_code=language,
                                 reference_language=settings["language"])}
        if mode == "auto":
            current = settings.get("expression_mode", "none")
            if (current == "none" or not config.expression_enabled()
                    or not expression_product.schema_present(db)):
                return expression_product.automatic_context(base, "disabled", None)
            if language != "ja" or len(segments) != 1 or len(segments[0]) + len("[thoughtful] ") > MAX_SEGMENT:
                return expression_product.automatic_context(base, "path_or_text_unsupported", None)
            if current == "thoughtful":
                return expression_product.context(base, "thoughtful")
            if not config.automatic_expression_enabled() or not automatic_expression.schema_present(db):
                return expression_product.automatic_context(base, "path_or_text_unsupported", None)
            style, tag = automatic_expression.decision(db, base, settings["personality"])
            if tag and style not in automatic_expression.DAILY_STYLES:
                return expression_product.automatic_context(base, "candidate_not_enabled", None)
            return expression_product.automatic_context(base, style, tag)
    return expression_product.context(base, mode)
