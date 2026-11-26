"""Durable, per-call limits for opt-in daily continuous voice.

The table is installed only by the explicit migration. Daily Scribe and HTTP
speech ledgers remain the cross-call limits; this record never refunds an
attempted upstream request.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from ..owner_billing import metered

import json
import re
import sqlite3
import time
from .. import chat_history


CALL_ID = re.compile(r"[a-f0-9]{32}\Z")
SECONDS = 180
STT_CONNECTIONS = 2
MODEL_TURNS = 2
TTS_REQUESTS = 2
TTS_CHARACTERS = 160


@dataclass(frozen=True)
class CallLimits:
    rounds: int = 2
    seconds: int = 180
    tts_requests: int = 2
    tts_characters: int = 160


DEFAULT_LIMITS = CallLimits()
MAX_LIMITS = CallLimits(6, 600, 12, 1200)


def maximum() -> CallLimits:
    from .budget_schema import configured_limit
    limits = CallLimits(*(configured_limit(f"YOSAKI_VOICE_CALL_MAX_{key.upper()}",
                                         value, 60 if key == "seconds" else 1,
                                         DailyCallRejected,
                                         maximum=86400 if key == "seconds" else 2_147_483_647)
                          for key, value in vars(MAX_LIMITS).items()))
    if min(limits.tts_requests, limits.tts_characters) < limits.rounds:
        raise DailyCallRejected("voice_call_configuration_invalid")
    return limits


def effective_limits(db, owner_id: int) -> CallLimits:
    limits, _ = _limits(db, owner_id)
    ceiling = maximum()
    return CallLimits(*(min(value, getattr(ceiling, key))
                        for key, value in vars(limits).items()))


def _has_v2(db: sqlite3.Connection) -> bool:
    return any(row[1] == "limit_rounds" for row in db.execute(
        "PRAGMA table_info(voice_continuous_daily_calls)"))


def _limits(db: sqlite3.Connection, owner_id: int) -> tuple[CallLimits, int]:
    if not _has_v2(db):
        return DEFAULT_LIMITS, 0
    row = db.execute("""SELECT rounds,seconds,tts_requests,tts_characters,revision
        FROM voice_continuous_owner_settings WHERE owner_id=?""", (owner_id,)).fetchone()
    return (CallLimits(*row[:4]), row[4]) if row else (DEFAULT_LIMITS, 0)


def settings(connect, owner_id: int) -> dict:
    with connect() as db:
        limits, revision = _limits(db, owner_id)
        migrated = _has_v2(db)
    return {"limits": vars(limits), "maximum": vars(maximum()),
            "revision": revision, "configurable": migrated}


def put_settings(connect, owner_id: int, limits: CallLimits,
                 expected_revision: int, confirm_increase: bool) -> dict:
    ceiling = maximum()
    if not (1 <= limits.rounds <= ceiling.rounds and
            60 <= limits.seconds <= ceiling.seconds and
            limits.rounds <= limits.tts_requests <= ceiling.tts_requests and
            limits.rounds <= limits.tts_characters <= ceiling.tts_characters):
        raise DailyCallRejected("voice_call_settings_out_of_range")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _has_v2(db):
            raise DailyCallRejected("voice_call_settings_migration_required")
        previous, revision = _limits(db, owner_id)
        if expected_revision != revision:
            raise DailyCallRejected("voice_call_settings_revision_conflict")
        if (any(getattr(limits, name) > getattr(previous, name) for name in
                ("rounds", "seconds", "tts_requests", "tts_characters")) and
                not confirm_increase):
            raise DailyCallRejected("voice_call_settings_increase_confirmation_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if revision == 0:
            db.execute("""INSERT INTO voice_continuous_owner_settings
                (owner_id,rounds,seconds,tts_requests,tts_characters,revision,updated_at)
                VALUES (?,?,?,?,?,1,?)""", (owner_id, limits.rounds, limits.seconds,
                limits.tts_requests, limits.tts_characters, now))
        else:
            db.execute("""UPDATE voice_continuous_owner_settings SET
                rounds=?,seconds=?,tts_requests=?,tts_characters=?,revision=revision+1,
                updated_at=? WHERE owner_id=? AND revision=?""",
                (limits.rounds, limits.seconds, limits.tts_requests,
                 limits.tts_characters, now, owner_id, revision))
    return settings(connect, owner_id)


def _row_limits(row) -> CallLimits:
    return (CallLimits(row["limit_rounds"], row["limit_seconds"],
                       row["limit_tts_requests"], row["limit_tts_characters"])
            if "limit_rounds" in row.keys() else DEFAULT_LIMITS)


class DailyCallRejected(ValueError):
    pass


def init_db(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS voice_continuous_daily_calls (
        call_id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE RESTRICT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
        model_profile_id INTEGER NOT NULL REFERENCES model_profiles(id) ON DELETE RESTRICT,
        started_at REAL NOT NULL,
        ended_at REAL,
        stt_connections INTEGER NOT NULL DEFAULT 0 CHECK(stt_connections BETWEEN 0 AND 2),
        model_turns INTEGER NOT NULL DEFAULT 0 CHECK(model_turns BETWEEN 0 AND 2),
        tts_requests INTEGER NOT NULL DEFAULT 0 CHECK(tts_requests BETWEEN 0 AND 2),
        tts_characters INTEGER NOT NULL DEFAULT 0 CHECK(tts_characters BETWEEN 0 AND 160),
        last_message_id INTEGER,
        claim_trace_json TEXT NOT NULL DEFAULT '[]'
    )""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS voice_continuous_daily_active_owner
        ON voice_continuous_daily_calls(owner_id) WHERE ended_at IS NULL""")


def init_db_v2(db: sqlite3.Connection) -> None:
    """Explicit migration target; never called by application startup."""
    db.execute("""CREATE TABLE voice_continuous_daily_calls (
        call_id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE RESTRICT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
        model_profile_id INTEGER NOT NULL REFERENCES model_profiles(id) ON DELETE RESTRICT,
        started_at REAL NOT NULL,
        ended_at REAL,
        stt_connections INTEGER NOT NULL DEFAULT 0 CHECK(stt_connections BETWEEN 0 AND 6),
        model_turns INTEGER NOT NULL DEFAULT 0 CHECK(model_turns BETWEEN 0 AND 6),
        tts_requests INTEGER NOT NULL DEFAULT 0 CHECK(tts_requests BETWEEN 0 AND 12),
        tts_characters INTEGER NOT NULL DEFAULT 0 CHECK(tts_characters BETWEEN 0 AND 1200),
        last_message_id INTEGER,
        claim_trace_json TEXT NOT NULL DEFAULT '[]',
        limit_rounds INTEGER NOT NULL DEFAULT 2 CHECK(limit_rounds BETWEEN 1 AND 6),
        limit_seconds INTEGER NOT NULL DEFAULT 180 CHECK(limit_seconds BETWEEN 60 AND 600),
        limit_tts_requests INTEGER NOT NULL DEFAULT 2 CHECK(limit_tts_requests BETWEEN 1 AND 12),
        limit_tts_characters INTEGER NOT NULL DEFAULT 160 CHECK(limit_tts_characters BETWEEN 1 AND 1200)
    )""")
    db.execute("""CREATE UNIQUE INDEX voice_continuous_daily_active_owner
        ON voice_continuous_daily_calls(owner_id) WHERE ended_at IS NULL""")
    db.execute("""CREATE TABLE voice_continuous_owner_settings (
        owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        rounds INTEGER NOT NULL DEFAULT 2 CHECK(rounds BETWEEN 1 AND 6),
        seconds INTEGER NOT NULL DEFAULT 180 CHECK(seconds BETWEEN 60 AND 600),
        tts_requests INTEGER NOT NULL DEFAULT 2 CHECK(tts_requests BETWEEN 1 AND 12),
        tts_characters INTEGER NOT NULL DEFAULT 160 CHECK(tts_characters BETWEEN 1 AND 1200),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        updated_at TEXT NOT NULL
    )""")


def _row(db: sqlite3.Connection, call_id: str, owner_id: int):
    if not CALL_ID.fullmatch(call_id):
        raise DailyCallRejected("voice_call_id_invalid")
    try:
        row = db.execute("SELECT * FROM voice_continuous_daily_calls WHERE call_id=? AND owner_id=?",
                         (call_id, owner_id)).fetchone()
    except sqlite3.OperationalError as exc:
        raise DailyCallRejected("voice_call_daily_schema_missing") from exc
    if row is None:
        raise DailyCallRejected("voice_call_not_found")
    return row


def _trace(db: sqlite3.Connection, call_id: str, action: str, **ids) -> None:
    row = db.execute("SELECT claim_trace_json FROM voice_continuous_daily_calls WHERE call_id=?",
                     (call_id,)).fetchone()
    events = json.loads(row[0])
    events.append({"at": time.time(), "action": action, **ids})
    db.execute("UPDATE voice_continuous_daily_calls SET claim_trace_json=? WHERE call_id=?",
               (json.dumps(events[-512:], separators=(",", ":")), call_id))


def _available_rounds_in_transaction(db: sqlite3.Connection, owner_id: int,
                                     session_hash: str) -> int:
    """Check both existing daily ledgers under the call-start write lock."""
    from . import auto_budget, scribe_budget
    session = db.execute("SELECT user_id FROM auth_sessions WHERE token_hash=?",
                         (session_hash,)).fetchone()
    if not session or session[0] != owner_id:
        return 0
    try:
        scribe_enabled, scribe_limits, _ = scribe_budget.effective_settings(db, owner_id)
        speech_limits = auto_budget.effective_limits(db, owner_id)
    except (scribe_budget.BudgetUnavailable, auto_budget.BudgetUnavailable) as exc:
        raise DailyCallRejected("voice_call_daily_budget_unavailable") from exc
    if not scribe_enabled or not scribe_budget.service_ready():
        return 0
    if metered():
        return _limits(db, owner_id)[0].rounds
    scribe_day, _ = scribe_budget.period()
    stt = db.execute("""SELECT attempts,connections,reserved_audio_bytes
        FROM voice_scribe_owner_day_budget WHERE owner_id=? AND local_day=?""",
        (owner_id, scribe_day)).fetchone()
    remaining_stt = min(scribe_limits.attempts - (stt[0] if stt else 0),
        scribe_limits.connections - (stt[1] if stt else 0),
        (scribe_limits.audio_seconds * scribe_budget.PCM_BYTES_PER_SECOND -
         (stt[2] if stt else 0)) // scribe_budget.ATTEMPT_AUDIO_BYTES)
    speech_day, _ = auto_budget.daily_period()
    tts = db.execute("""SELECT synth_jobs,request_count,character_count
        FROM voice_auto_owner_day_budget WHERE owner_id=? AND local_day=?""",
        (owner_id, speech_day)).fetchone()
    remaining_tts = min(speech_limits.jobs - (tts[0] if tts else 0),
        speech_limits.requests - (tts[1] if tts else 0),
        speech_limits.characters - (tts[2] if tts else 0))
    limits = effective_limits(db, owner_id)
    return max(0, min(limits.rounds, remaining_stt, remaining_tts))


def start(connect, call_id: str, owner_id: int, character_id: int,
          conversation_id: int, model_profile_id: int,
          session_hash: str = "", *, acceptance: bool = False,
          headset: bool = False, single_button: bool = False) -> CallLimits:
    if not CALL_ID.fullmatch(call_id):
        raise DailyCallRejected("voice_call_id_invalid")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        now = time.time()
        # A process crash cannot restore consumed daily quota. An orphaned call
        # remains exclusive until the limit captured when it started expires.
        v2 = _has_v2(db)
        if single_button and not v2:
            raise DailyCallRejected("voice_call_settings_schema_required")
        if v2:
            db.execute("""UPDATE voice_continuous_daily_calls SET ended_at=?
                WHERE owner_id=? AND ended_at IS NULL AND started_at+limit_seconds<=?""",
                (now, owner_id, now))
        else:
            db.execute("""UPDATE voice_continuous_daily_calls SET ended_at=?
                WHERE owner_id=? AND ended_at IS NULL AND started_at<=?""",
                (now, owner_id, now - SECONDS))
        if db.execute("""SELECT 1 FROM voice_continuous_daily_calls
            WHERE owner_id=? AND ended_at IS NULL""", (owner_id,)).fetchone():
            raise DailyCallRejected("voice_call_already_active")
        if _available_rounds_in_transaction(db, owner_id, session_hash) < 1:
            raise DailyCallRejected("voice_call_daily_exhausted")
        conversation = db.execute("""SELECT character_id,model_profile_id FROM conversations
            WHERE id=?""", (conversation_id,)).fetchone()
        if not conversation or tuple(conversation) != (character_id, model_profile_id):
            raise DailyCallRejected("voice_call_binding_invalid")
        path=chat_history.rows(db,conversation_id)
        latest=path[-1] if path else None
        if (headset or single_button) and (latest or db.execute("""SELECT 1 FROM voice_continuous_daily_calls
            WHERE conversation_id=?""", (conversation_id,)).fetchone()):
            raise DailyCallRejected("voice_call_single_button_already_used" if single_button
                                    else "voice_call_headset_already_used")
        if latest and (latest["role"] != "assistant" or
                       latest["generation_status"] not in (None, "completed", "imported")):
            raise DailyCallRejected("voice_call_conversation_incomplete")
        limits = effective_limits(db, owner_id)
        if single_button:
            limits = CallLimits(1, min(limits.seconds, SECONDS), 1,
                                min(limits.tts_characters, 80))
        elif acceptance:
            limits = replace(limits, rounds=min(limits.rounds, MODEL_TURNS),
                             seconds=min(limits.seconds, SECONDS),
                             tts_requests=min(limits.tts_requests, TTS_REQUESTS),
                             tts_characters=min(limits.tts_characters, TTS_CHARACTERS))
        if v2:
            db.execute("""INSERT INTO voice_continuous_daily_calls
                (call_id,owner_id,character_id,conversation_id,model_profile_id,
                 started_at,last_message_id,limit_rounds,limit_seconds,
                 limit_tts_requests,limit_tts_characters)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (call_id, owner_id, character_id, conversation_id, model_profile_id,
                 now, latest["id"] if latest else None, limits.rounds,
                 limits.seconds, limits.tts_requests, limits.tts_characters))
        else:
            db.execute("""INSERT INTO voice_continuous_daily_calls
                (call_id,owner_id,character_id,conversation_id,model_profile_id,
                 started_at,last_message_id) VALUES (?,?,?,?,?,?,?)""",
                (call_id, owner_id, character_id, conversation_id, model_profile_id,
                 now, latest["id"] if latest else None))
        _trace(db, call_id, "call_start")
        return limits


def claim(db: sqlite3.Connection, call_id: str, owner_id: int,
          action: str, *, requests: int = 1, characters: int = 0,
          attempt_id: str | None = None, turn_id: str | None = None,
          message_id: int | None = None) -> None:
    row = _row(db, call_id, owner_id)
    limits = _row_limits(row)
    if row["ended_at"] is not None or time.time() - row["started_at"] >= limits.seconds:
        raise DailyCallRejected("voice_call_ended")
    if action == "stt" and requests == 1 and characters == 0:
        column, amount, limit = "stt_connections", 1, limits.rounds
    elif action == "model" and requests == 1 and characters == 0:
        path=chat_history.rows(db,row['conversation_id'])
        latest=path[-1] if path else None
        if (latest['id'] if latest else None) != row["last_message_id"]:
            raise DailyCallRejected("voice_call_conversation_changed")
        column, amount, limit = "model_turns", 1, limits.rounds
    elif action == "tts" and requests == 1 and characters > 0 and (metered() or characters <= limits.tts_characters):
        if not metered() and row["tts_characters"] + characters > limits.tts_characters:
            raise DailyCallRejected("voice_call_tts_limit")
        column, amount, limit = "tts_requests", 1, limits.tts_requests
    else:
        raise DailyCallRejected("voice_call_claim_invalid")
    changed = db.execute(f"""UPDATE voice_continuous_daily_calls
        SET {column}={column}+? WHERE call_id=? AND owner_id=? AND ended_at IS NULL
        AND (? OR {column}+?<=?)""", (amount, call_id, owner_id, metered(), amount, limit)).rowcount
    if changed != 1:
        raise DailyCallRejected("voice_call_limit_exhausted")
    if action == "tts":
        db.execute("""UPDATE voice_continuous_daily_calls
            SET tts_characters=tts_characters+? WHERE call_id=?""",
            (characters, call_id))
    ids = {}
    if attempt_id is not None:
        ids["attempt_id"] = attempt_id
    if turn_id is not None:
        ids["turn_id"] = turn_id
    if message_id is not None:
        ids["message_id"] = message_id
    _trace(db, call_id, action, **ids)


def record_message(connect, call_id: str, owner_id: int, message_id: int,
                   *, turn_id: str | None = None) -> None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, call_id, owner_id)
        if row["ended_at"] is not None:
            raise DailyCallRejected("voice_call_ended")
        rows=[item for item in chat_history.rows(db,row['conversation_id']) if item['id']>(row['last_message_id'] or 0)]
        if len(rows) != 2 or rows[0]["role"] != "user" or \
                rows[1]["role"] != "assistant" or rows[1]["id"] != message_id:
            raise DailyCallRejected("voice_call_message_binding_invalid")
        db.execute("UPDATE voice_continuous_daily_calls SET last_message_id=? WHERE call_id=?",
                   (message_id, call_id))
        _trace(db, call_id, "message_saved", message_id=message_id,
               **({"turn_id": turn_id} if turn_id else {}))


def record_interrupted_message(connect, call_id: str, owner_id: int,
                               *, turn_id: str) -> int:
    """Advance only across the exact user/stopped-assistant pair for this call.

    A caller may try the next Scribe connection only after the canceled model
    stream has finished saving its stopped assistant. This never refunds the
    model attempt or accepts an unrelated message as the call's next turn.
    """
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, call_id, owner_id)
        if row["ended_at"] is not None:
            raise DailyCallRejected("voice_call_ended")
        rows=[item for item in chat_history.rows(db,row['conversation_id']) if item['id']>(row['last_message_id'] or 0)]
        if (len(rows) != 2 or rows[0]["role"] != "user" or
                rows[1]["role"] != "assistant" or
                rows[1]["generation_status"] != "stopped"):
            raise DailyCallRejected("voice_call_interrupted_message_pending")
        message_id = rows[1]["id"]
        db.execute("UPDATE voice_continuous_daily_calls SET last_message_id=? WHERE call_id=?",
                   (message_id, call_id))
        _trace(db, call_id, "message_interrupted", message_id=message_id,
               turn_id=turn_id)
        return message_id


def can_continue(connect, call_id: str, owner_id: int) -> bool:
    with connect() as db:
        row = _row(db, call_id, owner_id)
        limits = _row_limits(row)
        return bool(row["ended_at"] is None and
                    time.time() - row["started_at"] < limits.seconds and
                    (metered() or (row["stt_connections"] < limits.rounds and
                    row["model_turns"] < limits.rounds and
                    row["tts_requests"] < limits.tts_requests)))


def conversation_locked(db: sqlite3.Connection, conversation_id: int) -> bool:
    if _has_v2(db):
        return db.execute("""SELECT 1 FROM voice_continuous_daily_calls
            WHERE conversation_id=? AND ended_at IS NULL AND started_at+limit_seconds>?
            LIMIT 1""", (conversation_id, time.time())).fetchone() is not None
    return db.execute("""SELECT 1 FROM voice_continuous_daily_calls
        WHERE conversation_id=? AND ended_at IS NULL AND started_at>? LIMIT 1""",
        (conversation_id, time.time() - SECONDS)).fetchone() is not None


def end_call(connect, call_id: str, owner_id: int) -> None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, call_id, owner_id)
        if row["ended_at"] is None:
            db.execute("UPDATE voice_continuous_daily_calls SET ended_at=? WHERE call_id=?",
                       (time.time(), call_id))
            _trace(db, call_id, "call_end")
