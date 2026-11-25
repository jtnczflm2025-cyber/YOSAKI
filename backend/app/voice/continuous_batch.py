"""Explicit, durable receipt for one isolated continuous-call acceptance batch.

This schema is created only by an explicit preparation step on an isolated
database. Normal startup never migrates a database for this feature.
"""

from __future__ import annotations

import re
import json
import sqlite3
import time


BATCH_ID = re.compile(r"[a-f0-9]{32}\Z")
SECONDS = 180
STT_CONNECTIONS = 2
MODEL_TURNS = 2
TTS_REQUESTS = 2
TTS_CHARACTERS = 160


class BatchRejected(ValueError):
    pass


def init_db(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS voice_continuous_batch_receipts (
        batch_id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        conversation_id INTEGER NOT NULL UNIQUE,
        model_profile_id INTEGER NOT NULL,
        started_at REAL,
        active_call_id TEXT,
        stt_connections INTEGER NOT NULL DEFAULT 0 CHECK(stt_connections BETWEEN 0 AND 2),
        model_turns INTEGER NOT NULL DEFAULT 0 CHECK(model_turns BETWEEN 0 AND 2),
        tts_requests INTEGER NOT NULL DEFAULT 0 CHECK(tts_requests BETWEEN 0 AND 2),
        tts_characters INTEGER NOT NULL DEFAULT 0 CHECK(tts_characters BETWEEN 0 AND 160),
        last_message_id INTEGER,
        claim_trace_json TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE RESTRICT
    )""")


def prepare(connect, batch_id: str, owner_id: int, character_id: int,
            conversation_id: int, model_profile_id: int) -> None:
    """Bind a previously created, empty conversation; never create daily data here."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prepare_in_transaction(db, batch_id, owner_id, character_id,
                               conversation_id, model_profile_id)


def prepare_in_transaction(db: sqlite3.Connection, batch_id: str, owner_id: int,
                           character_id: int, conversation_id: int,
                           model_profile_id: int) -> None:
    """Use after creating the bound conversation in the same explicit transaction."""
    if not BATCH_ID.fullmatch(batch_id):
        raise BatchRejected("voice_batch_id_invalid")
    row = db.execute("""SELECT 1 FROM conversations WHERE id=? AND character_id=?
        AND model_profile_id=?""",
        (conversation_id, character_id, model_profile_id)).fetchone()
    if not row or db.execute("SELECT 1 FROM messages WHERE conversation_id=? LIMIT 1",
                             (conversation_id,)).fetchone():
        raise BatchRejected("voice_batch_requires_empty_conversation")
    if db.execute("SELECT 1 FROM voice_continuous_batch_receipts WHERE batch_id=?",
                  (batch_id,)).fetchone():
        raise BatchRejected("voice_batch_already_prepared")
    db.execute("""INSERT INTO voice_continuous_batch_receipts
        (batch_id,owner_id,character_id,conversation_id,model_profile_id,created_at)
        VALUES (?,?,?,?,?,?)""",
        (batch_id, owner_id, character_id, conversation_id, model_profile_id,
         time.time()))


def _row(db: sqlite3.Connection, batch_id: str, owner_id: int):
    if not BATCH_ID.fullmatch(batch_id):
        raise BatchRejected("voice_batch_missing")
    try:
        row = db.execute("""SELECT * FROM voice_continuous_batch_receipts
            WHERE batch_id=? AND owner_id=?""", (batch_id, owner_id)).fetchone()
    except sqlite3.OperationalError as exc:
        raise BatchRejected("voice_batch_schema_missing") from exc
    if row is None:
        raise BatchRejected("voice_batch_missing")
    return row


def status(connect, batch_id: str, owner_id: int) -> dict:
    with connect() as db:
        row = _row(db, batch_id, owner_id)
        return {"batch_id": row["batch_id"], "character_id": row["character_id"],
                "conversation_id": row["conversation_id"],
                "model_profile_id": row["model_profile_id"],
                "started": row["started_at"] is not None,
                "used": {"stt_connections": row["stt_connections"],
                         "model_turns": row["model_turns"],
                         "tts_requests": row["tts_requests"],
                         "tts_characters": row["tts_characters"]}}


def start(connect, batch_id: str, owner_id: int, character_id: int,
          conversation_id: int, model_profile_id: int, *, call_id: str | None = None) -> None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, batch_id, owner_id)
        if (row["character_id"], row["conversation_id"], row["model_profile_id"]) != \
                (character_id, conversation_id, model_profile_id):
            raise BatchRejected("voice_batch_binding_invalid")
        # This acceptance receipt binds exactly one call. Ending, refreshing or
        # restarting a process must not turn a failed round into another call.
        if row["started_at"] is not None:
            if row["active_call_id"] is not None:
                raise BatchRejected("voice_call_already_active")
            raise BatchRejected("voice_batch_restart_forbidden")
        if (row["stt_connections"] >= STT_CONNECTIONS or
                row["model_turns"] >= MODEL_TURNS or
                row["tts_requests"] >= TTS_REQUESTS):
            raise BatchRejected("voice_batch_exhausted")
        now = time.time()
        messages = db.execute("""SELECT id,role FROM messages WHERE conversation_id=?
            ORDER BY id""", (conversation_id,)).fetchall()
        if (len(messages) != row["model_turns"] * 2 or
                (messages and (messages[-1]["id"] != row["last_message_id"] or
                               messages[-1]["role"] != "assistant"))):
            raise BatchRejected("voice_batch_conversation_changed")
        if row["started_at"] is None:
            db.execute("""UPDATE voice_continuous_batch_receipts SET started_at=?
                WHERE batch_id=? AND started_at IS NULL""", (now, batch_id))
        if call_id is not None:
            if not BATCH_ID.fullmatch(call_id):
                raise BatchRejected("voice_batch_trace_invalid")
            db.execute("""UPDATE voice_continuous_batch_receipts SET active_call_id=?
                WHERE batch_id=?""", (call_id, batch_id))
            _trace(db, batch_id, "call_start", call_id=call_id)


def end_call(connect, batch_id: str, owner_id: int, call_id: str) -> None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, batch_id, owner_id)
        if row["active_call_id"] == call_id:
            db.execute("""UPDATE voice_continuous_batch_receipts SET active_call_id=NULL
                WHERE batch_id=? AND owner_id=? AND active_call_id=?""",
                (batch_id, owner_id, call_id))
            _trace(db, batch_id, "call_end", call_id=call_id)


def _trace(db: sqlite3.Connection, batch_id: str, action: str, **identifiers) -> None:
    """Keep a small ID-only timeline in the same write transaction as each claim."""
    for name, value in identifiers.items():
        if name == "message_id":
            if type(value) is not int or value < 1:
                raise BatchRejected("voice_batch_trace_invalid")
        elif not BATCH_ID.fullmatch(value or ""):
            raise BatchRejected("voice_batch_trace_invalid")
    row = db.execute("""SELECT claim_trace_json FROM voice_continuous_batch_receipts
        WHERE batch_id=?""", (batch_id,)).fetchone()
    events = json.loads(row[0])
    if len(events) >= 32:
        raise BatchRejected("voice_batch_trace_full")
    events.append({"at": time.time(), "action": action, **identifiers})
    db.execute("UPDATE voice_continuous_batch_receipts SET claim_trace_json=? WHERE batch_id=?",
               (json.dumps(events, separators=(",", ":")), batch_id))


def claim(db: sqlite3.Connection, batch_id: str, owner_id: int,
          action: str, *, requests: int = 1, characters: int = 0,
          call_id: str | None = None, attempt_id: str | None = None,
          turn_id: str | None = None, message_id: int | None = None) -> None:
    row = _row(db, batch_id, owner_id)
    if row["started_at"] is None or time.time() - row["started_at"] >= SECONDS:
        raise BatchRejected("voice_batch_expired")
    if call_id is not None and row["active_call_id"] != call_id:
        raise BatchRejected("voice_batch_call_binding_invalid")
    if action == "stt" and requests == 1 and characters == 0:
        changed = db.execute("""UPDATE voice_continuous_batch_receipts
            SET stt_connections=stt_connections+1 WHERE batch_id=? AND owner_id=?
            AND stt_connections < ?""", (batch_id, owner_id, STT_CONNECTIONS)).rowcount
    elif action == "model" and requests == 1 and characters == 0:
        changed = db.execute("""UPDATE voice_continuous_batch_receipts
            SET model_turns=model_turns+1 WHERE batch_id=? AND owner_id=?
            AND model_turns < ?""", (batch_id, owner_id, MODEL_TURNS)).rowcount
    elif action == "tts" and 0 < requests <= TTS_REQUESTS and \
            0 < characters <= TTS_CHARACTERS:
        changed = db.execute("""UPDATE voice_continuous_batch_receipts
            SET tts_requests=tts_requests+?,tts_characters=tts_characters+?
            WHERE batch_id=? AND owner_id=? AND tts_requests+?<=?
            AND tts_characters+?<=?""",
            (requests, characters, batch_id, owner_id, requests, TTS_REQUESTS,
             characters, TTS_CHARACTERS)).rowcount
    else:
        raise BatchRejected("voice_batch_claim_invalid")
    if changed != 1:
        raise BatchRejected("voice_batch_exhausted")
    identifiers = {"call_id": call_id} if call_id is not None else {}
    if action == "stt" and attempt_id is not None:
        identifiers["attempt_id"] = attempt_id
    if action in ("model", "tts") and turn_id is not None:
        identifiers["turn_id"] = turn_id
    if action == "tts" and message_id is not None:
        identifiers["message_id"] = message_id
    _trace(db, batch_id, action, **identifiers)


def record_message(connect, batch_id: str, owner_id: int, message_id: int, *,
                   call_id: str | None = None, turn_id: str | None = None) -> None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _row(db, batch_id, owner_id)
        if call_id is not None and row["active_call_id"] != call_id:
            raise BatchRejected("voice_batch_call_binding_invalid")
        messages = db.execute("""SELECT id,role FROM messages WHERE conversation_id=?
            ORDER BY id""", (row["conversation_id"],)).fetchall()
        if (len(messages) != row["model_turns"] * 2 or not messages or
                messages[-1]["id"] != message_id or messages[-1]["role"] != "assistant"):
            raise BatchRejected("voice_batch_message_binding_invalid")
        db.execute("""UPDATE voice_continuous_batch_receipts SET last_message_id=?
            WHERE batch_id=? AND owner_id=?""", (message_id, batch_id, owner_id))
        identifiers = {"message_id": message_id}
        if call_id is not None:
            identifiers["call_id"] = call_id
        if turn_id is not None:
            identifiers["turn_id"] = turn_id
        _trace(db, batch_id, "message_saved", **identifiers)


def can_continue(connect, batch_id: str, owner_id: int) -> bool:
    with connect() as db:
        row = _row(db, batch_id, owner_id)
        return bool(row["started_at"] is not None and
                    time.time() - row["started_at"] < SECONDS and
                    row["stt_connections"] < STT_CONNECTIONS and
                    row["model_turns"] < MODEL_TURNS)
