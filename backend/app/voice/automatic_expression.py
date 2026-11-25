"""Freeze one local expression decision per public reply and profile revision."""
from __future__ import annotations

import json
import math
import re
import sqlite3

VERSION = "v3-auto-1"
DAILY_STYLES = frozenset({"restrained"})
# Tags come from the application allowlist, never from text, profiles or snapshots.
RULES = {
    "relaxed": (r"のんびり|まったり|ゆっくり過ご|懒洋洋|慢悠悠", r"慵懒|放松|穏やか|ゆったり", "relaxed"),
    "restrained": (r"言葉を選|落ち着いて考|慎重に考|斟酌|克制", r"克制|冷静|慎重|落ち着", "thoughtful"),
    "dry_humor": (r"冗談だけど|皮肉を言|冷笑话|冷幽默|讽刺一下", r"冷幽默|冷笑话|皮肉|ドライ", "sarcastic"),
    "resigned": (r"仕方がない|しょうがない|やれやれ|无奈|没办法", r"无奈|克制|ため息|諦め", "sighs"),
    "whisper": (r"小声で|ひそひそ|耳元で|低声|轻声|悄悄说", r"低语|轻声|小声|囁", "whispers"),
    "chuckle": (r"ふふっ|くすっと|忍不住轻笑|轻笑一声", r"轻笑|幽默|笑い|ふふ", "chuckles"),
}


def init_db(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS voice_auto_expression_decisions (
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        source_sha256 TEXT NOT NULL,
        style TEXT NOT NULL,
        tag TEXT,
        PRIMARY KEY(message_id,source_sha256))""")


def schema_present(db) -> bool:
    return bool(db.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='voice_auto_expression_decisions'""").fetchone())


def _choose(text: str, personality: str, state: dict | None) -> tuple[str, str | None]:
    if not isinstance(state, dict) or not isinstance(state.get("emotion"), dict):
        return "snapshot_missing", None
    emotion = state["emotion"]
    try:
        arousal, tension, valence = (float(emotion[key]) for key in ("arousal","tension","valence"))
    except (KeyError, TypeError, ValueError):
        return "snapshot_missing", None
    if (not all(math.isfinite(x) for x in (arousal, tension, valence)) or
            not 0 <= arousal <= 1 or not 0 <= tension <= 1 or not -1 <= valence <= 1):
        return "snapshot_missing", None
    matches = [(style, tag) for style, (phrase, preference, tag) in RULES.items()
               if re.search(phrase, text) and re.search(preference, personality)]
    if len(matches) != 1 or tension > .65 or arousal > .75:
        return "uncertain", None
    style, tag = matches[0]
    if (style in ("relaxed", "whisper") and arousal > .5 or
            style in ("dry_humor", "chuckle") and valence < 0 or
            style == "resigned" and valence > .2):
        return "uncertain", None
    return style, tag


def decision(db, base: dict, personality: str) -> tuple[str, str | None]:
    # Serialize the first read across HTTP preview, cache lookup and dispatch.
    db.execute("BEGIN IMMEDIATE")
    key = (base["message_id"], base["digest"] + ":" + VERSION)
    existing = db.execute("""SELECT style,tag FROM voice_auto_expression_decisions
        WHERE message_id=? AND source_sha256=?""", key).fetchone()
    if existing:
        return tuple(existing)
    try:
        row = db.execute("""SELECT state_json FROM psychological_snapshots
            WHERE character_id=?""", (base["character_id"],)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        row = None
    try:
        state = json.loads(row[0]) if row else None
    except (ValueError, TypeError):
        state = None
    result = _choose(base["segments"][0], personality, state)
    db.execute("INSERT INTO voice_auto_expression_decisions VALUES (?,?,?,?)", (*key,*result))
    return result
