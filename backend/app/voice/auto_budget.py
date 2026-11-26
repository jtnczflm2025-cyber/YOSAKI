"""Durable per-login and owner/day limits for opt-in automatic HTTP speech.

Only the controlled migration calls ``init_db``. Importing this module never
changes a database. A reservation is consumed before a provider attempt and
is deliberately never refunded for an uncertain outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from .budget_schema import configured_limit
from datetime import datetime, time, timedelta, timezone
from ..owner_billing import metered

import os
import sqlite3


@dataclass(frozen=True)
class Limits:
    jobs: int = 2
    requests: int = 2
    characters: int = 160


LIMITS = Limits()
MAX_LIMITS = Limits(20, 40, 4000)
HONG_KONG = timezone(timedelta(hours=8))


class BudgetUnavailable(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv("YOSAKI_VOICE_AUTO_SPEAK_ENABLED", "0") == "1"


def daily_enabled() -> bool:
    return os.getenv("YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED", "0") == "1"


def daily_limits() -> Limits:
    """Deployment safety ceilings; user settings default to the acceptance limits."""
    result = []
    for key, ceiling in (("JOBS", MAX_LIMITS.jobs), ("REQUESTS", MAX_LIMITS.requests),
                         ("CHARACTERS", MAX_LIMITS.characters)):
        maximum = configured_limit(f"YOSAKI_VOICE_AUTO_MAX_{key}", ceiling, 0,
                                   BudgetUnavailable)
        value = configured_limit(f"YOSAKI_VOICE_AUTO_DAILY_{key}", maximum, 0,
                                 BudgetUnavailable)
        if value > maximum:
            raise BudgetUnavailable("auto_daily_configuration_invalid")
        result.append(value)
    return Limits(*result)


def daily_period() -> tuple[str, str]:
    local_day = datetime.now(HONG_KONG).date()
    reset = datetime.combine(local_day + timedelta(days=1), time.min,
                             tzinfo=HONG_KONG)
    return local_day.isoformat(), reset.isoformat(timespec="seconds")


def init_db(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS voice_auto_session_budget (
        session_hash TEXT PRIMARY KEY REFERENCES auth_sessions(token_hash) ON DELETE CASCADE,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        synth_jobs INTEGER NOT NULL DEFAULT 0 CHECK(synth_jobs >= 0),
        request_count INTEGER NOT NULL DEFAULT 0 CHECK(request_count >= 0),
        character_count INTEGER NOT NULL DEFAULT 0 CHECK(character_count >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")


def init_daily_db(db: sqlite3.Connection) -> None:
    """Only a separate, approved migration calls this on a daily database."""
    db.execute("""CREATE TABLE IF NOT EXISTS voice_auto_owner_day_budget (
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        local_day TEXT NOT NULL,
        synth_jobs INTEGER NOT NULL DEFAULT 0 CHECK(synth_jobs >= 0),
        request_count INTEGER NOT NULL DEFAULT 0 CHECK(request_count >= 0),
        character_count INTEGER NOT NULL DEFAULT 0 CHECK(character_count >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,local_day)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS voice_auto_owner_settings (
        owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        synth_jobs INTEGER NOT NULL DEFAULT 2 CHECK(synth_jobs BETWEEN 0 AND 20),
        request_count INTEGER NOT NULL DEFAULT 2 CHECK(request_count BETWEEN 0 AND 40),
        character_count INTEGER NOT NULL DEFAULT 160 CHECK(character_count BETWEEN 0 AND 4000),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")


def _settings(db: sqlite3.Connection, owner_id: int) -> tuple[Limits, int]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_auto_owner_settings'").fetchone():
        raise BudgetUnavailable("auto_daily_schema_missing")
    row = db.execute("""SELECT synth_jobs,request_count,character_count,revision
        FROM voice_auto_owner_settings WHERE owner_id=?""", (owner_id,)).fetchone()
    configured = (Limits(row["synth_jobs"], row["request_count"],
                         row["character_count"]) if row else LIMITS)
    return configured, row["revision"] if row else 0


def effective_limits(db: sqlite3.Connection, owner_id: int) -> Limits:
    configured, _ = _settings(db, owner_id)
    ceiling = daily_limits()
    return Limits(*(min(getattr(configured, name), getattr(ceiling, name))
                    for name in ("jobs", "requests", "characters")))


def settings(connect, owner_id: int) -> dict:
    if not daily_enabled():
        raise BudgetUnavailable("auto_daily_disabled")
    with connect() as db:
        configured, revision = _settings(db, owner_id)
        effective = effective_limits(db, owner_id)
    return {"limits": vars(configured), "effective_limits": vars(effective),
            "ceilings": vars(daily_limits()), "revision": revision,
            "daily_enabled": True}


def put_settings(connect, owner_id: int, requested: Limits,
                 expected_revision: int, confirm_increase: bool) -> dict:
    if not daily_enabled():
        raise BudgetUnavailable("auto_daily_disabled")
    ceilings = daily_limits()
    if any(getattr(requested, name) < 0 or
           getattr(requested, name) > getattr(ceilings, name)
           for name in ("jobs", "requests", "characters")):
        raise ValueError("auto_daily_limit_out_of_range")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous, revision = _settings(db, owner_id)
        if expected_revision != revision:
            raise ValueError("auto_daily_revision_conflict")
        if any(getattr(requested, name) > getattr(previous, name)
               for name in ("jobs", "requests", "characters")) and not confirm_increase:
            raise ValueError("auto_daily_increase_confirmation_required")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if revision == 0:
            db.execute("""INSERT INTO voice_auto_owner_settings
                (owner_id,synth_jobs,request_count,character_count,revision,created_at,updated_at)
                VALUES (?,?,?,?,1,?,?)""",
                (owner_id, requested.jobs, requested.requests,
                 requested.characters, now, now))
        else:
            db.execute("""UPDATE voice_auto_owner_settings SET synth_jobs=?,
                request_count=?,character_count=?,revision=revision+1,updated_at=?
                WHERE owner_id=? AND revision=?""",
                (requested.jobs, requested.requests, requested.characters,
                 now, owner_id, revision))
    return settings(connect, owner_id)


def _reserve_daily(db: sqlite3.Connection, owner_id: int, characters: int,
                   first_request: bool, now: str, limits: Limits) -> None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_auto_owner_day_budget'").fetchone():
        raise BudgetUnavailable("auto_daily_schema_missing")
    local_day, _ = daily_period()
    db.execute("""INSERT OR IGNORE INTO voice_auto_owner_day_budget
        (owner_id,local_day,created_at,updated_at) VALUES (?,?,?,?)""",
        (owner_id, local_day, now, now))
    changed = db.execute("""UPDATE voice_auto_owner_day_budget SET
        synth_jobs=synth_jobs+?, request_count=request_count+1,
        character_count=character_count+?, updated_at=?
        WHERE owner_id=? AND local_day=? AND (? OR (synth_jobs+?<=?
          AND request_count+1<=? AND character_count+?<=?))""",
        (int(first_request), characters, now, owner_id, local_day, metered(),
         int(first_request), limits.jobs, limits.requests, characters,
         limits.characters)).rowcount
    if changed != 1:
        raise BudgetExhausted("auto_daily_budget_exhausted")


def reserve(connect, session_hash: str, owner_id: int, characters: int,
            *, first_request: bool, limits: Limits = LIMITS,
            batch_claim: dict | None = None) -> None:
    if not enabled():
        raise BudgetUnavailable("auto_speak_disabled")
    if not session_hash or characters < 1:
        raise BudgetExhausted("auto_budget_exhausted")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_auto_session_budget'").fetchone():
            raise BudgetUnavailable("auto_budget_schema_missing")
        session = db.execute("SELECT user_id FROM auth_sessions WHERE token_hash=?", (session_hash,)).fetchone()
        if session is None or session["user_id"] != owner_id:
            raise BudgetUnavailable("auto_session_expired")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute("""INSERT OR IGNORE INTO voice_auto_session_budget
            (session_hash,owner_id,created_at,updated_at) VALUES (?,?,?,?)""",
            (session_hash, owner_id, now, now))
        if daily_enabled():
            limits = effective_limits(db, owner_id)
            _reserve_daily(db, owner_id, characters, first_request, now, limits)
        elif not metered() and characters > limits.characters:
            raise BudgetExhausted("auto_budget_exhausted")
        if daily_enabled() or metered():
            # The owner/day reservation is the dispatch limit. Keep this row
            # as a lifetime audit for the login; it must not cap a new day or
            # a user-confirmed increase to the daily limit.
            changed = db.execute("""UPDATE voice_auto_session_budget SET
                synth_jobs=synth_jobs+?, request_count=request_count+1,
                character_count=character_count+?, updated_at=?
                WHERE session_hash=? AND owner_id=?""",
                (int(first_request), characters, now, session_hash, owner_id)).rowcount
        else:
            changed = db.execute("""UPDATE voice_auto_session_budget SET
                synth_jobs=synth_jobs+?, request_count=request_count+1,
                character_count=character_count+?, updated_at=?
                WHERE session_hash=? AND owner_id=? AND synth_jobs+?<=?
                  AND request_count+1<=? AND character_count+?<=?""",
                (int(first_request), characters, now, session_hash, owner_id,
                 int(first_request), limits.jobs, limits.requests, characters,
                 limits.characters)).rowcount
        if changed != 1:
            raise BudgetExhausted("auto_budget_exhausted")
        if batch_claim is not None:
            from . import continuous_batch, continuous_daily
            try:
                if "daily_call_id" in batch_claim:
                    continuous_daily.claim(db, batch_claim["daily_call_id"],
                        owner_id, "tts", requests=1, characters=characters,
                        turn_id=batch_claim["turn_id"],
                        message_id=batch_claim["message_id"])
                else:
                    continuous_batch.claim(db, batch_claim["batch_id"], owner_id, "tts",
                        requests=1, characters=characters,
                        call_id=batch_claim["call_id"], turn_id=batch_claim["turn_id"],
                        message_id=batch_claim["message_id"])
            except (continuous_batch.BatchRejected,
                    continuous_daily.DailyCallRejected) as exc:
                # The daily/session updates above roll back with this transaction.
                raise BudgetExhausted(str(exc)) from exc


def usage(connect, session_hash: str, owner_id: int) -> dict:
    with connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_auto_session_budget'").fetchone():
            raise BudgetUnavailable("auto_budget_schema_missing")
        row = db.execute("""SELECT synth_jobs,request_count,character_count
            FROM voice_auto_session_budget WHERE session_hash=? AND owner_id=?""",
            (session_hash, owner_id)).fetchone()
        daily = None
        limits = LIMITS
        if daily_enabled():
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_auto_owner_day_budget'").fetchone():
                raise BudgetUnavailable("auto_daily_schema_missing")
            limits = effective_limits(db, owner_id)
            local_day, reset_at = daily_period()
            day = db.execute("""SELECT synth_jobs,request_count,character_count
                FROM voice_auto_owner_day_budget WHERE owner_id=? AND local_day=?""",
                (owner_id, local_day)).fetchone()
            used = {"jobs": day["synth_jobs"] if day else 0,
                    "requests": day["request_count"] if day else 0,
                    "characters": day["character_count"] if day else 0}
            daily = {"local_day": local_day, "reset_at": reset_at,
                     "limits": vars(limits), "used": used,
                     "remaining": {name: max(0, getattr(limits, name) - used[name])
                                   for name in used}}
    return {"metered":metered(),"enabled": enabled(), "limits": vars(limits),
            "used": {"jobs": row["synth_jobs"] if row else 0,
                     "requests": row["request_count"] if row else 0,
                     "characters": row["character_count"] if row else 0},
            "daily": daily}
