"""Durable opt-in limits for user-initiated Scribe drafts.

Importing this module never migrates a database. A controlled migration calls
``init_db``. The full per-attempt audio allowance is reserved before a supplier
connection, so a crash or uncertain result cannot silently restore capacity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import os
import re
import sqlite3


HONG_KONG = timezone(timedelta(hours=8))
PCM_BYTES_PER_SECOND = 32_000
ATTEMPT_AUDIO_SECONDS = 15
ATTEMPT_AUDIO_BYTES = ATTEMPT_AUDIO_SECONDS * PCM_BYTES_PER_SECOND
ATTEMPT_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Limits:
    attempts: int = 1
    connections: int = 1
    audio_seconds: int = 15


DEFAULT = Limits()
MAXIMUM = Limits(10, 10, 150)


class BudgetUnavailable(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv("YOSAKI_SCRIBE_DAILY_ENABLED", "0") == "1"


def service_ready() -> bool:
    return (enabled() and os.getenv("YOSAKI_VOICE_ENABLED", "0") == "1"
            and os.getenv("YOSAKI_BACKGROUND_TASKS_ENABLED", "1") == "0")


def period() -> tuple[str, str]:
    day = datetime.now(HONG_KONG).date()
    reset = datetime.combine(day + timedelta(days=1), time.min, tzinfo=HONG_KONG)
    return day.isoformat(), reset.isoformat(timespec="seconds")


def init_db(db: sqlite3.Connection) -> None:
    """DDL only; the caller must use an explicit, reviewed transaction."""
    db.execute("""CREATE TABLE IF NOT EXISTS voice_scribe_owner_settings (
        owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        attempts INTEGER NOT NULL DEFAULT 1 CHECK(attempts BETWEEN 0 AND 10),
        connections INTEGER NOT NULL DEFAULT 1 CHECK(connections BETWEEN 0 AND 10),
        audio_seconds INTEGER NOT NULL DEFAULT 15 CHECK(audio_seconds BETWEEN 0 AND 150),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS voice_scribe_owner_day_budget (
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        local_day TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        connections INTEGER NOT NULL DEFAULT 0 CHECK(connections >= 0),
        reserved_audio_bytes INTEGER NOT NULL DEFAULT 0 CHECK(reserved_audio_bytes >= 0),
        actual_audio_bytes INTEGER NOT NULL DEFAULT 0 CHECK(actual_audio_bytes >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,local_day)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS voice_scribe_attempts (
        attempt_id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        local_day TEXT NOT NULL,
        reserved_audio_bytes INTEGER NOT NULL CHECK(reserved_audio_bytes >= 0),
        actual_audio_bytes INTEGER NOT NULL DEFAULT 0 CHECK(actual_audio_bytes >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")


def _require_schema(db: sqlite3.Connection) -> None:
    names = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('voice_scribe_owner_settings','voice_scribe_owner_day_budget','voice_scribe_attempts')")}
    if len(names) != 3:
        raise BudgetUnavailable("scribe_daily_schema_missing")


def _settings(db: sqlite3.Connection, owner_id: int) -> tuple[bool, Limits, int]:
    _require_schema(db)
    row = db.execute("""SELECT enabled,attempts,connections,audio_seconds,revision
        FROM voice_scribe_owner_settings WHERE owner_id=?""", (owner_id,)).fetchone()
    if row is None:
        return False, DEFAULT, 0
    return bool(row["enabled"]), Limits(row["attempts"], row["connections"],
                                         row["audio_seconds"]), row["revision"]


def settings(connect, owner_id: int) -> dict:
    with connect() as db:
        user_enabled, limits, revision = _settings(db, owner_id)
    return {"enabled": user_enabled, "limits": vars(limits),
            "maximum": vars(MAXIMUM), "revision": revision,
            "service_enabled": service_ready(),
            "per_attempt_audio_seconds": ATTEMPT_AUDIO_SECONDS}


def put_settings(connect, owner_id: int, user_enabled: bool, limits: Limits,
                 expected_revision: int, confirm_increase: bool,
                 confirm_enable: bool) -> dict:
    if any(getattr(limits, name) < 0 or getattr(limits, name) > getattr(MAXIMUM, name)
           for name in ("attempts", "connections", "audio_seconds")):
        raise ValueError("scribe_daily_limit_out_of_range")
    if user_enabled and (limits.attempts < 1 or limits.connections < 1 or
                         limits.audio_seconds < ATTEMPT_AUDIO_SECONDS):
        raise ValueError("scribe_daily_limit_too_small")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old_enabled, old_limits, revision = _settings(db, owner_id)
        if expected_revision != revision:
            raise ValueError("scribe_daily_revision_conflict")
        if user_enabled and not old_enabled and not confirm_enable:
            raise ValueError("scribe_daily_enable_confirmation_required")
        if any(getattr(limits, name) > getattr(old_limits, name)
               for name in ("attempts", "connections", "audio_seconds")) and not confirm_increase:
            raise ValueError("scribe_daily_increase_confirmation_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if revision == 0:
            db.execute("""INSERT INTO voice_scribe_owner_settings
                (owner_id,enabled,attempts,connections,audio_seconds,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,1,?,?)""",
                (owner_id, int(user_enabled), limits.attempts, limits.connections,
                 limits.audio_seconds, now, now))
        else:
            db.execute("""UPDATE voice_scribe_owner_settings SET enabled=?,attempts=?,
                connections=?,audio_seconds=?,revision=revision+1,updated_at=?
                WHERE owner_id=? AND revision=?""",
                (int(user_enabled), limits.attempts, limits.connections,
                 limits.audio_seconds, now, owner_id, revision))
    return settings(connect, owner_id)


def usage(connect, owner_id: int) -> dict:
    with connect() as db:
        user_enabled, limits, revision = _settings(db, owner_id)
        day, reset_at = period()
        row = db.execute("""SELECT attempts,connections,reserved_audio_bytes,actual_audio_bytes
            FROM voice_scribe_owner_day_budget WHERE owner_id=? AND local_day=?""",
            (owner_id, day)).fetchone()
        used = {"attempts": row["attempts"] if row else 0,
                "connections": row["connections"] if row else 0,
                "reserved_audio_seconds": (row["reserved_audio_bytes"] / PCM_BYTES_PER_SECOND) if row else 0,
                "actual_audio_seconds": (row["actual_audio_bytes"] / PCM_BYTES_PER_SECOND) if row else 0}
        remaining_audio_bytes = max(0, limits.audio_seconds * PCM_BYTES_PER_SECOND -
                                    (row["reserved_audio_bytes"] if row else 0))
        remaining_attempts = max(0, limits.attempts - used["attempts"])
        remaining_connections = max(0, limits.connections - used["connections"])
        available_attempts = (min(remaining_attempts, remaining_connections,
                                  remaining_audio_bytes // ATTEMPT_AUDIO_BYTES)
                              if user_enabled and service_ready() else 0)
    return {"service_enabled": service_ready(), "enabled": user_enabled,
            "limits": vars(limits), "revision": revision, "local_day": day,
            "reset_at": reset_at, "used": used,
            "per_attempt_audio_seconds": ATTEMPT_AUDIO_SECONDS,
            "available_attempts": available_attempts,
            "remaining": {"attempts": remaining_attempts,
                          "connections": remaining_connections,
                          "audio_seconds": remaining_audio_bytes / PCM_BYTES_PER_SECOND}}


def reserve(connect, owner_id: int, attempt_id: str) -> None:
    if not service_ready():
        raise BudgetUnavailable("scribe_daily_disabled")
    if not ATTEMPT_ID.fullmatch(attempt_id):
        raise BudgetUnavailable("scribe_attempt_id_invalid")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        user_enabled, limits, _ = _settings(db, owner_id)
        if not user_enabled:
            raise BudgetUnavailable("scribe_user_disabled")
        if db.execute("SELECT 1 FROM voice_scribe_attempts WHERE attempt_id=?",
                      (attempt_id,)).fetchone():
            raise BudgetExhausted("scribe_attempt_reused")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        day, _ = period()
        db.execute("""INSERT OR IGNORE INTO voice_scribe_owner_day_budget
            (owner_id,local_day,created_at,updated_at) VALUES (?,?,?,?)""",
            (owner_id, day, now, now))
        changed = db.execute("""UPDATE voice_scribe_owner_day_budget SET
            attempts=attempts+1,connections=connections+1,
            reserved_audio_bytes=reserved_audio_bytes+?,updated_at=?
            WHERE owner_id=? AND local_day=? AND attempts+1<=? AND connections+1<=?
              AND reserved_audio_bytes+?<=?""",
            (ATTEMPT_AUDIO_BYTES, now, owner_id, day, limits.attempts,
             limits.connections, ATTEMPT_AUDIO_BYTES,
             limits.audio_seconds * PCM_BYTES_PER_SECOND)).rowcount
        if changed != 1:
            raise BudgetExhausted("scribe_daily_budget_exhausted")
        db.execute("""INSERT INTO voice_scribe_attempts
            (attempt_id,owner_id,local_day,reserved_audio_bytes,created_at,updated_at)
            VALUES (?,?,?,?,?,?)""",
            (attempt_id, owner_id, day, ATTEMPT_AUDIO_BYTES, now, now))


def record_audio(connect, owner_id: int, attempt_id: str, count: int) -> None:
    """Record a possible send before feeding the provider; never refund it."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT local_day,actual_audio_bytes,reserved_audio_bytes
            FROM voice_scribe_attempts WHERE attempt_id=? AND owner_id=?""",
            (attempt_id, owner_id)).fetchone()
        if row is None or count < 1 or row["actual_audio_bytes"] + count > row["reserved_audio_bytes"]:
            raise BudgetExhausted("scribe_audio_budget_exhausted")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute("""UPDATE voice_scribe_attempts SET actual_audio_bytes=actual_audio_bytes+?,
            updated_at=? WHERE attempt_id=?""", (count, now, attempt_id))
        db.execute("""UPDATE voice_scribe_owner_day_budget SET
            actual_audio_bytes=actual_audio_bytes+?,updated_at=?
            WHERE owner_id=? AND local_day=?""", (count, now, owner_id, row["local_day"]))
