"""Opt-in V3 message expression bindings; never auto-migrate a daily database."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from fastapi import HTTPException

from .expression_planner import EXPRESSION_PLANNER_VERSION, build
from .speech_planner import SpeechPlanError


PRODUCT_MODES = ("none", "thoughtful", "auto")


def init_db(db: sqlite3.Connection) -> None:
    """Only an explicitly approved migration or an isolated fixture calls this."""
    db.execute("""CREATE TABLE IF NOT EXISTS voice_expression_audio (
        id INTEGER PRIMARY KEY,
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        cache_id INTEGER NOT NULL REFERENCES voice_audio_cache(id) ON DELETE CASCADE,
        mode TEXT NOT NULL CHECK(mode IN ('thoughtful','auto')),
        sequence INTEGER NOT NULL DEFAULT 0 CHECK(sequence=0),
        source_sha256 TEXT NOT NULL, profile_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(message_id,mode,sequence))""")
    db.execute("""CREATE INDEX IF NOT EXISTS voice_expression_audio_cache
        ON voice_expression_audio(cache_id)""")


def schema_present(db: sqlite3.Connection) -> bool:
    return db.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='voice_expression_audio'""").fetchone() is not None


def context(base: dict, mode: str) -> dict:
    if mode not in ("none", "thoughtful"):
        raise HTTPException(422, "日常消息朗读不支持该表达模式")
    if mode == "none":
        return {**base, "mode": "none", "expression_tag": None,
                "expression_planner_version": None,
                "safe_segments": base["segments"]}
    if len(base["segments"]) != 1 or base["language"] != "ja":
        raise HTTPException(422, "实验表达仅支持确定为日语的单片段朗读；请使用无标签模式")
    try:
        planned = build(base["body"], "thoughtful")
    except SpeechPlanError as exc:
        raise HTTPException(422, str(exc)) from exc
    if planned.safe_text != base["segments"][0] or planned.language != "ja":
        raise HTTPException(409, "表达规划与公开正文不一致，请重新预览")
    value = {"source_sha256": base["digest"], "mode": mode,
             "version": EXPRESSION_PLANNER_VERSION,
             "request_text": planned.request_text,
             "model_id": "eleven_v4", "output_format": "mp3_44100_128",
             "language_code": "ja", "settings": "omitted"}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return {**base, "mode": mode, "expression_tag": planned.tag,
            "expression_planner_version": EXPRESSION_PLANNER_VERSION,
            "safe_segments": base["segments"], "segments": [planned.request_text],
            "skipped_reasons": list(planned.skipped_reasons),
            "digest": hashlib.sha256(raw).hexdigest()}


def automatic_context(base: dict, style: str, tag: str | None) -> dict:
    from .automatic_expression import RULES, VERSION
    from .speech_planner import MAX_SEGMENT
    plain = {**context(base, "none"), "expression_style": style}
    if style not in RULES or tag != RULES[style][2]:
        return plain
    bracket = f"[{tag}]"
    text = f"{bracket} {base['segments'][0]}"
    if len(text) > MAX_SEGMENT:
        return plain
    digest = hashlib.sha256(json.dumps([base["digest"], VERSION, style, text],
        ensure_ascii=False).encode()).hexdigest()
    return {**plain, "mode":"auto", "expression_tag":bracket,
            "expression_planner_version":VERSION, "segments":[text], "digest":digest}
