"""Default-off continuous-call gate with isolated and daily ledgers.

The call uses the existing Scribe, chat, and HTTP speech routes. No database
schema is created here. Daily Scribe and automatic speech budgets remain in
force; each mode binds one target conversation and counts upstream attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from .. import chat_history
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import auto_budget, config, continuous_batch, continuous_daily, scribe_budget, storage


CALL_SECONDS = 180
STT_CONNECTIONS = 2
STT_AUDIO_SECONDS = 30
MODEL_TURNS = 2
TTS_REQUESTS = 2
TTS_CHARACTERS = 160
CALL_EVENT_LOG = logging.getLogger("yosaki.voice.call")
BUTTON_ACCEPTANCE_OUTPUT_TOKENS = 2048
BUTTON_ACCEPTANCE_MODEL_URL = httpx.URL("https://api.openai.com/v1/responses")


async def validate_button_acceptance_request(request: httpx.Request) -> None:
    """Validate the serialized request before HTTPX hands it to the transport."""
    if request.method != "POST" or request.url != BUTTON_ACCEPTANCE_MODEL_URL:
        raise HTTPException(409, "按钮打断验收已停止：所选模型接口无法确认支持输出 token 上限")
    try:
        body = json.loads(request.content)
    except (ValueError, httpx.RequestNotRead) as exc:
        raise HTTPException(409, "按钮打断验收已停止：无法核对模型请求体") from exc
    if not isinstance(body, dict) or "max_output_tokens" not in body:
        raise HTTPException(409, "按钮打断验收已停止：模型请求缺少 max_output_tokens")
    limit = body["max_output_tokens"]
    if type(limit) is not int or limit != BUTTON_ACCEPTANCE_OUTPUT_TOKENS:
        raise HTTPException(409, "按钮打断验收已停止：模型请求的输出 token 上限必须为 2048")
    CALL_EVENT_LOG.warning("button_model_request_validated max_output_tokens=%d", limit)


class CallRejected(ValueError):
    pass


@dataclass
class Call:
    id: str
    owner_id: int
    character_id: int
    conversation_id: int
    model_profile_id: int
    batch_id: str
    opened: float
    daily_mode: bool = False
    session_hash: str = ""
    limits: continuous_daily.CallLimits = field(default_factory=lambda: continuous_daily.DEFAULT_LIMITS)
    stt_connections: int = 0
    model_turns: int = 0
    tts_requests: int = 0
    tts_characters: int = 0
    pending_attempt: str | None = None
    pending_transcript: tuple[str, str] | None = None
    model_active: str | None = None
    interrupted_model_turn: str | None = None
    playback_active: bool = False
    tts_reserved_requests: int = 0
    tts_dispatch_count: int = 0
    completed: dict[str, int] = field(default_factory=dict)
    final_drain: tuple[str, int] | None = None
    events: list[dict] = field(default_factory=list)
    ended: bool = False


class CallStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    model_profile_id: int = Field(gt=0)
    acknowledge_auto_send: bool


class CallSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rounds: int = Field(ge=1)
    seconds: int = Field(ge=60)
    tts_requests: int = Field(ge=1)
    tts_characters: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    confirm_increase: bool = False


class InterruptObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["listening", "transcribing", "waiting", "playing"]
    client_click_ms: float = Field(ge=0, le=1_000_000_000_000)
    client_audio_stop_ms: float = Field(ge=0, le=1_000_000_000_000)
    client_playback_started: bool | None = None
    turn_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class CallManager:
    def __init__(self, connect):
        self.connect = connect
        self.lock = threading.RLock()
        self.calls: dict[str, Call] = {}
        self.by_owner: dict[int, str] = {}

    @staticmethod
    def _record(call: Call, event: str, *, turn_id: str | None = None,
                message_id: int | None = None, client_ms: float | None = None,
                phase: str | None = None, can_continue: bool | None = None,
                playback_started: bool | None = None) -> None:
        # Fixed event vocabulary and identifiers only: no text, audio, or secrets.
        item = {"event": event, "call_id": call.id,
                "server_elapsed_ms": round((time.monotonic() - call.opened) * 1000, 1)}
        if turn_id is not None:
            item["turn_id"] = turn_id
        if message_id is not None:
            item["message_id"] = message_id
        if client_ms is not None:
            item["client_monotonic_ms"] = round(client_ms, 1)
        if phase is not None:
            item["phase"] = phase
        if can_continue is not None:
            item["can_continue"] = can_continue
        if playback_started is not None:
            item["client_playback_started"] = playback_started
        call.events.append(item)
        if len(call.events) > 96:
            del call.events[:-96]
        CALL_EVENT_LOG.warning("%s", json.dumps(item, separators=(",", ":")))

    def timeline(self, call_id: str, owner_id: int) -> dict:
        with self.lock:
            call = self.calls.get(call_id)
            if call is None or call.owner_id != owner_id:
                raise CallRejected("voice_call_ended")
            return {"call_id": call.id, "ended": call.ended,
                    "events": [dict(item) for item in call.events]}

    @staticmethod
    def isolated() -> bool:
        return (os.getenv("YOSAKI_VOICE_CONTINUOUS_ISOLATED", "0") == "1"
                and bool(continuous_batch.BATCH_ID.fullmatch(
                    os.getenv("YOSAKI_VOICE_CONTINUOUS_BATCH_ID", ""))))

    @staticmethod
    def daily() -> bool:
        return os.getenv("YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED", "0") == "1"

    @staticmethod
    def acceptance() -> bool:
        return (os.getenv("YOSAKI_VOICE_CONTINUOUS_TWO_ROUND_ACCEPTANCE", "0") == "1"
                or CallManager.single_button_conversation() is not None)

    @staticmethod
    def single_button_conversation() -> int | None:
        raw = os.getenv("YOSAKI_VOICE_SINGLE_BUTTON_CONVERSATION", "")
        return int(raw) if raw else None

    @staticmethod
    def headset_conversation() -> int | None:
        raw = os.getenv("YOSAKI_VOICE_HEADSET_ACCEPTANCE_CONVERSATION", "")
        return int(raw) if raw else None

    @classmethod
    def enabled(cls) -> bool:
        return ((cls.isolated() != cls.daily())
                and os.getenv("YOSAKI_BACKGROUND_TASKS_ENABLED", "1") == "0"
                and os.getenv("YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED", "0") == "1"
                and config.enabled() and scribe_budget.service_ready()
                and auto_budget.enabled() and auto_budget.daily_enabled())

    def _daily_rounds(self, owner_id: int, session_hash: str) -> tuple[int, dict, dict]:
        scribe = scribe_budget.usage(self.connect, owner_id)
        speech = auto_budget.usage(self.connect, session_hash, owner_id)
        remaining = (speech.get("daily") or {}).get("remaining") or {}
        with self.connect() as db:
            limits = continuous_daily.effective_limits(db, owner_id)
        rounds = min(limits.rounds, scribe["available_attempts"],
                     remaining.get("jobs", 0), remaining.get("requests", 0),
                     remaining.get("characters", 0))
        if speech.get("metered") and scribe.get("enabled") and scribe.get("service_enabled"):
            rounds = limits.rounds
        if self.single_button_conversation() is not None:
            rounds = min(rounds, 1)
        elif self.acceptance():
            rounds = min(rounds, MODEL_TURNS)
        return max(0, rounds), scribe, speech

    def availability(self, owner_id: int, character_id: int,
                     conversation_id: int, model_profile_id: int,
                     session_hash: str) -> dict:
        if not self.enabled() or not self.daily():
            raise CallRejected("voice_call_daily_disabled")
        proposed = Call("0" * 32, owner_id, character_id, conversation_id,
                        model_profile_id, "", time.monotonic(), True, session_hash)
        if not self._authorized(proposed):
            raise CallRejected("voice_call_binding_invalid")
        rounds, scribe, speech = self._daily_rounds(owner_id, session_hash)
        with self.connect() as db:
            row = db.execute("SELECT title FROM conversations WHERE id=? AND character_id=?",
                             (conversation_id, character_id)).fetchone()
            if continuous_daily._has_v2(db):
                active = db.execute("""SELECT 1 FROM voice_continuous_daily_calls
                    WHERE owner_id=? AND ended_at IS NULL AND started_at+limit_seconds>?
                    LIMIT 1""", (owner_id, time.time())).fetchone() is not None
            else:
                active = db.execute("""SELECT 1 FROM voice_continuous_daily_calls
                    WHERE owner_id=? AND ended_at IS NULL AND started_at>? LIMIT 1""",
                    (owner_id, time.time() - CALL_SECONDS)).fetchone() is not None
            path=chat_history.rows(db,conversation_id)
            latest=path[-1] if path else None
            incomplete = bool(latest and (latest["role"] != "assistant" or
                latest["generation_status"] not in (None, "completed", "imported")))
            target = self.single_button_conversation() or self.headset_conversation()
            if target is not None and (conversation_id != target or latest or db.execute(
                    "SELECT 1 FROM voice_continuous_daily_calls WHERE conversation_id=?",
                    (conversation_id,)).fetchone()):
                rounds = 0
        return {"character_id": character_id, "conversation_id": conversation_id,
                "model_profile_id": model_profile_id, "conversation_title": row["title"] if row else "",
                "available_rounds": 0 if active or incomplete else rounds,
                "active_call": active, "conversation_incomplete": incomplete,
                "limits": continuous_daily.settings(self.connect, owner_id),
                "scribe_remaining": scribe["remaining"],
                "tts_remaining": speech["daily"]["remaining"]}

    def conversation_locked(self, db: sqlite3.Connection, conversation_id: int) -> bool:
        return self.enabled() and self.daily() and continuous_daily.conversation_locked(
            db, conversation_id)

    def _authorized(self, call: Call) -> bool:
        with self.connect() as db:
            row = db.execute("""SELECT c.character_id,
                COALESCE(c.model_profile_id,ch.default_model_id) AS model_profile_id,
                m.protocol,m.enabled
                FROM conversations c JOIN characters ch ON ch.id=c.character_id
                JOIN model_profiles m ON m.id=COALESCE(c.model_profile_id,ch.default_model_id)
                WHERE c.id=?""", (call.conversation_id,)).fetchone()
            character = db.execute("SELECT enabled FROM characters WHERE id=?",
                                   (call.character_id,)).fetchone()
            profile = storage.profile(db, call.character_id)
            consent = storage.consent(db, call.owner_id)
            scribe = scribe_budget.settings(self.connect, call.owner_id)
        return bool(row and row["character_id"] == call.character_id
                    and row["model_profile_id"] == call.model_profile_id
                    and row["protocol"] == "chatgpt_plan" and row["enabled"]
                    and character and character["enabled"] and profile and profile["active"]
                    and consent and consent["notice_version"] == config.NOTICE_VERSION
                    and scribe["enabled"])

    def _get(self, call_id: str, owner_id: int) -> Call:
        call = self.calls.get(call_id)
        if call is None or call.owner_id != owner_id:
            raise CallRejected("voice_call_ended")
        if (not self.enabled() or call.ended or
                time.monotonic() - call.opened >= call.limits.seconds):
            if not call.ended:
                call.ended = True
                if self.by_owner.get(call.owner_id) == call.id:
                    self.by_owner.pop(call.owner_id, None)
                if call.daily_mode:
                    continuous_daily.end_call(self.connect, call.id, call.owner_id)
            raise CallRejected("voice_call_ended")
        try:
            authorized = self._authorized(call)
        except (sqlite3.Error, scribe_budget.BudgetUnavailable):
            authorized = False
        if not authorized:
            call.ended = True
            if self.by_owner.get(call.owner_id) == call.id:
                self.by_owner.pop(call.owner_id, None)
            if call.daily_mode:
                continuous_daily.end_call(self.connect, call.id, call.owner_id)
            raise CallRejected("voice_call_authorization_revoked")
        return call

    def start(self, owner_id: int, character_id: int, conversation_id: int,
              model_profile_id: int, acknowledge_auto_send: bool,
              session_hash: str = "") -> dict:
        if not self.enabled() or not acknowledge_auto_send:
            raise CallRejected("voice_call_disabled_or_unconfirmed")
        with self.lock:
            old = self.by_owner.get(owner_id)
            if old and old in self.calls and time.monotonic() - self.calls[old].opened >= self.calls[old].limits.seconds:
                self.calls[old].ended = True
                self.by_owner.pop(owner_id, None)
            elif old and old in self.calls and not self.calls[old].ended:
                raise CallRejected("voice_call_already_active")
            daily_mode = self.daily()
            headset = self.headset_conversation()
            single_button = self.single_button_conversation()
            if single_button is not None and (not daily_mode or conversation_id != single_button):
                raise CallRejected("voice_call_single_button_binding_invalid")
            if headset is not None and (not daily_mode or not self.acceptance() or
                                       conversation_id != headset):
                raise CallRejected("voice_call_headset_binding_invalid")
            batch_id = "" if daily_mode else os.getenv("YOSAKI_VOICE_CONTINUOUS_BATCH_ID", "")
            call = Call(secrets.token_hex(16), owner_id, character_id,
                        conversation_id, model_profile_id, batch_id, time.monotonic(),
                        daily_mode, session_hash)
            try:
                authorized = self._authorized(call)
            except (sqlite3.Error, scribe_budget.BudgetUnavailable):
                authorized = False
            if not authorized:
                raise CallRejected("voice_call_binding_invalid")
            if daily_mode:
                if not session_hash or self._daily_rounds(owner_id, session_hash)[0] < 1:
                    raise CallRejected("voice_call_daily_exhausted")
            else:
                if scribe_budget.usage(self.connect, owner_id)["available_attempts"] < 1:
                    raise CallRejected("voice_call_stt_daily_exhausted")
                with self.connect() as db:
                    if auto_budget.effective_limits(db, owner_id).jobs < 1:
                        raise CallRejected("voice_call_tts_daily_exhausted")
            try:
                if daily_mode:
                    call.limits = continuous_daily.start(self.connect, call.id, owner_id,
                                           character_id, conversation_id,
                                           model_profile_id, session_hash,
                                           acceptance=self.acceptance(), headset=headset is not None,
                                           single_button=single_button is not None)
                else:
                    continuous_batch.start(self.connect, batch_id, owner_id,
                                           character_id, conversation_id, model_profile_id,
                                           call_id=call.id)
            except (continuous_batch.BatchRejected, continuous_daily.DailyCallRejected) as exc:
                raise CallRejected(str(exc)) from exc
            self.calls[call.id] = call
            # Ended calls retain a short diagnostic window. SQLite and the
            # structured server log keep the durable records.
            ended = [key for key, value in self.calls.items() if value.ended]
            for key in ended[:-64]:
                del self.calls[key]
            self.by_owner[owner_id] = call.id
            self._record(call, "call_started")
            return self.summary(call)

    @staticmethod
    def summary(call: Call) -> dict:
        return {"call_id": call.id, "character_id": call.character_id,
                "conversation_id": call.conversation_id,
                "limits": {"seconds": call.limits.seconds,
                           "stt_connections": call.limits.rounds,
                           "stt_audio_seconds": call.limits.rounds * scribe_budget.ATTEMPT_AUDIO_SECONDS,
                           "model_turns": call.limits.rounds,
                           "tts_requests": call.limits.tts_requests,
                           "tts_characters": call.limits.tts_characters},
                "used": {"stt_connections": call.stt_connections,
                         "model_turns": call.model_turns,
                         "tts_requests": call.tts_requests,
                         "tts_characters": call.tts_characters}}

    def reserve_stt(self, call_id: str, owner_id: int, character_id: int,
                    attempt_id: str) -> str:
        with self.lock:
            call = self._get(call_id, owner_id)
            if (call.character_id != character_id or call.pending_attempt or
                    call.pending_transcript or call.model_active or
                    call.playback_active or (not (call.daily_mode and auto_budget.metered()) and call.stt_connections >= call.limits.rounds)):
                raise CallRejected("voice_call_stt_limit_or_busy")
            if call.daily_mode and call.interrupted_model_turn:
                try:
                    continuous_daily.record_interrupted_message(
                        self.connect, call.id, owner_id,
                        turn_id=call.interrupted_model_turn)
                except continuous_daily.DailyCallRejected as exc:
                    raise CallRejected(str(exc)) from exc
                call.interrupted_model_turn = None
            call.stt_connections += 1  # Never refund an attempted connection.
            call.pending_attempt = attempt_id
            call.pending_transcript = None
            self._record(call, "stt_attempt_reserved")
            return call.id if call.daily_mode else call.batch_id

    def transcript(self, call_id: str, owner_id: int, attempt_id: str,
                   text: str) -> str:
        with self.lock:
            call = self._get(call_id, owner_id)
            if call.pending_attempt != attempt_id or call.pending_transcript:
                raise CallRejected("voice_call_transcript_stale")
            call.pending_attempt = None
            turn_id = secrets.token_hex(16)
            call.pending_transcript = (turn_id, text)
            self._record(call, "final_transcript_bound", turn_id=turn_id)
            return turn_id

    def abort_stt(self, call_id: str, owner_id: int, attempt_id: str,
                  *, unreserved: bool = False) -> None:
        with self.lock:
            call = self.calls.get(call_id)
            if call and call.owner_id == owner_id and call.pending_attempt == attempt_id:
                call.pending_attempt = None
                if unreserved:
                    # The daily/batch transaction was rejected before any
                    # upstream connection attempt. No persistent claim exists.
                    call.stt_connections -= 1

    def valid(self, call_id: str, owner_id: int) -> bool:
        with self.lock:
            try:
                self._get(call_id, owner_id)
                return True
            except CallRejected:
                return False

    def valid_tts(self, call_id: str, turn_id: str, owner_id: int,
                  message_id: int) -> bool:
        with self.lock:
            try:
                call = self._get(call_id, owner_id)
            except CallRejected:
                return False
            return bool(call.playback_active and
                        call.completed.get(turn_id) == message_id)

    def claim_model(self, call_id: str, turn_id: str, owner_id: int,
                    character_id: int, conversation_id: int,
                    model_profile_id: int, text: str, *, db=None) -> int:
        with self.lock:
            call = self._get(call_id, owner_id)
            if (call.character_id != character_id or
                    call.conversation_id != conversation_id or
                    call.model_profile_id != model_profile_id or
                    call.pending_transcript != (turn_id, text) or
                    call.model_active or (not (call.daily_mode and auto_budget.metered()) and call.model_turns >= call.limits.rounds)):
                raise CallRejected("voice_call_model_limit_or_binding_invalid")
            try:
                ledger = continuous_daily if call.daily_mode else continuous_batch
                kwargs = {"turn_id": turn_id} if call.daily_mode else {"call_id": call.id, "turn_id": turn_id}
                if db is None:
                    with self.connect() as owned_db:
                        owned_db.execute("BEGIN IMMEDIATE")
                        ledger.claim(owned_db, call.id if call.daily_mode else call.batch_id,
                                     owner_id, "model", **kwargs)
                else:
                    ledger.claim(db, call.id if call.daily_mode else call.batch_id,
                                 owner_id, "model", **kwargs)
            except (continuous_batch.BatchRejected, continuous_daily.DailyCallRejected) as exc:
                raise CallRejected(str(exc)) from exc
            call.model_turns += 1  # One generation attempt, even if outcome unknown.
            call.pending_transcript = None
            call.model_active = turn_id
            self._record(call, "model_request_claimed", turn_id=turn_id)
            return call.model_turns

    def model_active(self, call_id: str, turn_id: str, owner_id: int) -> bool:
        with self.lock:
            try:
                return self._get(call_id, owner_id).model_active == turn_id
            except CallRejected:
                return False

    def finish_model(self, call_id: str, turn_id: str,
                     owner_id: int, message_id: int) -> bool:
        with self.lock:
            try:
                call = self._get(call_id, owner_id)
            except CallRejected:
                return False
            if call.model_active != turn_id:
                return False
            call.model_active = None
            try:
                if call.daily_mode:
                    continuous_daily.record_message(self.connect, call.id,
                                                    owner_id, message_id, turn_id=turn_id)
                else:
                    continuous_batch.record_message(self.connect, call.batch_id,
                                                    owner_id, message_id,
                                                    call_id=call.id, turn_id=turn_id)
            except (continuous_batch.BatchRejected, continuous_daily.DailyCallRejected):
                call.ended = True
                self.by_owner.pop(owner_id, None)
                return False
            call.completed[turn_id] = message_id
            self._record(call, "model_completed_saved", turn_id=turn_id,
                         message_id=message_id)
            return True

    def model_cancel_observed(self, call_id: str, turn_id: str, owner_id: int) -> None:
        with self.lock:
            call = self.calls.get(call_id)
            if call and call.owner_id == owner_id:
                self._record(call, "model_stream_cancel_observed", turn_id=turn_id)

    def validate_tts(self, call_id: str, turn_id: str, owner_id: int,
                     message_id: int, requests: int, characters: int,
                     *, cached: bool = False) -> Call:
        with self.lock:
            call = self._get(call_id, owner_id)
            if (call.completed.get(turn_id) != message_id or call.playback_active or
                    requests < 1 or characters < 1):
                raise CallRejected("voice_call_tts_limit_or_binding_invalid")
            if not cached and (not (call.daily_mode and auto_budget.metered()) and call.tts_characters + characters > call.limits.tts_characters):
                raise CallRejected("voice_call_tts_text_too_long")
            if self.headset_conversation() is not None and characters > (110 if call.model_turns == 1 else 50):
                raise CallRejected("voice_call_tts_text_too_long")
            if not cached and (not (call.daily_mode and auto_budget.metered()) and call.tts_requests + requests > call.limits.tts_requests):
                raise CallRejected("voice_call_tts_request_limit")
            return call

    def claim_tts(self, call_id: str, turn_id: str, owner_id: int,
                  message_id: int, requests: int, characters: int,
                  *, cached: bool = False) -> str:
        with self.lock:
            call = self.validate_tts(call_id, turn_id, owner_id, message_id,
                                     requests, characters, cached=cached)
            # A queued job is not a provider attempt. Batch and daily budgets
            # are claimed together, immediately before each new HTTP dispatch.
            call.tts_reserved_requests = 0 if cached else requests
            call.tts_dispatch_count = 0
            call.playback_active = True
            self._record(call, "tts_bound", turn_id=turn_id,
                         message_id=message_id)
            return call.id if call.daily_mode else call.batch_id

    def check_tts_dispatch(self, call_id: str, turn_id: str,
                           owner_id: int, message_id: int, characters: int) -> None:
        """Fail before each outbound HTTP request if the bound reservation is spent."""
        with self.lock:
            call = self._get(call_id, owner_id)
            if (not call.playback_active or call.completed.get(turn_id) != message_id or
                    call.tts_dispatch_count >= call.tts_reserved_requests or
                    (not (call.daily_mode and auto_budget.metered()) and call.tts_characters + characters > call.limits.tts_characters)):
                raise CallRejected("voice_call_tts_dispatch_not_reserved")
            call.tts_dispatch_count += 1  # An attempted send is never refunded.
            call.tts_requests += 1
            call.tts_characters += characters
            self._record(call, "tts_dispatch_claimed", turn_id=turn_id,
                         message_id=message_id)

    def playback_drained(self, call_id: str, turn_id: str,
                         owner_id: int, message_id: int) -> bool:
        with self.lock:
            previous = self.calls.get(call_id)
            if (previous and previous.owner_id == owner_id and previous.ended
                    and previous.final_drain == (turn_id, message_id)):
                return False
            call = self._get(call_id, owner_id)
            if not call.playback_active or call.completed.get(turn_id) != message_id:
                raise CallRejected("voice_call_playback_binding_invalid")
            ledger_continue = (continuous_daily.can_continue(self.connect, call.id, owner_id)
                if call.daily_mode else
                continuous_batch.can_continue(self.connect, call.batch_id, owner_id))
            daily_continue = (self._daily_rounds(owner_id, call.session_hash)[0] > 0
                              if call.daily_mode else True)
            can_continue = (ledger_continue and daily_continue and
                            (call.daily_mode and auto_budget.metered() or call.stt_connections < call.limits.rounds) and
                            (call.daily_mode and auto_budget.metered() or call.model_turns < call.limits.rounds) and
                            time.monotonic() - call.opened < call.limits.seconds)
            self._record(call, "playback_drained", turn_id=turn_id,
                         message_id=message_id, can_continue=can_continue)
            if not can_continue:
                self.end(call_id, owner_id)
                call.final_drain = (turn_id, message_id)
            call.playback_active = False
            call.tts_reserved_requests = 0
            call.tts_dispatch_count = 0
            call.completed.pop(turn_id, None)
            return can_continue

    def interrupt(self, call_id: str, owner_id: int,
                  observation: InterruptObservation | None = None) -> dict:
        with self.lock:
            call = self._get(call_id, owner_id)
            was_generating = call.model_active is not None
            self._record(call, "button_interrupt_received",
                         turn_id=observation.turn_id if observation else call.model_active,
                         client_ms=observation.client_click_ms if observation else None,
                         phase=observation.phase if observation else None,
                         playback_started=observation.client_playback_started if observation else None)
            if observation:
                self._record(call, "client_audio_stop_reported",
                             turn_id=observation.turn_id,
                             client_ms=observation.client_audio_stop_ms)
            if was_generating:
                self._record(call, "model_cancel_requested", turn_id=call.model_active)
            if was_generating and call.daily_mode:
                call.interrupted_model_turn = call.model_active
            call.pending_attempt = None
            call.pending_transcript = None
            call.model_active = None
            call.playback_active = False
            call.tts_reserved_requests = 0
            call.tts_dispatch_count = 0
            call.completed.clear()
            if self.single_button_conversation() is not None:
                self.end(call_id, owner_id)
                self._record(call, "interrupt_resolved", can_continue=False)
                return {"cancel_generation": was_generating, "can_continue": False}
            # An interrupt may consume the final model or Scribe allowance.
            # Tell the browser before it mounts another microphone instance;
            # the next reserve still performs its own authoritative check.
            local_room = ((call.daily_mode and auto_budget.metered() or call.stt_connections < call.limits.rounds) and
                          (call.daily_mode and auto_budget.metered() or call.model_turns < call.limits.rounds) and
                          time.monotonic() - call.opened < call.limits.seconds)
            try:
                if call.daily_mode:
                    ledger_room = continuous_daily.can_continue(
                        self.connect, call.id, owner_id)
                    daily_room = self._daily_rounds(owner_id, call.session_hash)[0] > 0
                else:
                    ledger_room = continuous_batch.can_continue(
                        self.connect, call.batch_id, owner_id)
                    daily_room = scribe_budget.usage(
                        self.connect, owner_id)["available_attempts"] > 0
            except (sqlite3.Error, continuous_batch.BatchRejected,
                    continuous_daily.DailyCallRejected,
                    scribe_budget.BudgetUnavailable,
                    auto_budget.BudgetUnavailable):
                ledger_room = daily_room = False
            can_continue = bool(local_room and ledger_room and daily_room)
            self._record(call, "interrupt_resolved", can_continue=can_continue)
            return {"cancel_generation": was_generating,
                    "can_continue": can_continue}

    def end(self, call_id: str, owner_id: int) -> None:
        with self.lock:
            call = self.calls.get(call_id)
            if call and call.owner_id == owner_id and not call.ended:
                if call.daily_mode:
                    continuous_daily.end_call(self.connect, call.id, owner_id)
                else:
                    continuous_batch.end_call(self.connect, call.batch_id, owner_id,
                                              call.id)
                self._record(call, "call_ended")
                call.ended = True
                if self.by_owner.get(owner_id) == call.id:
                    self.by_owner.pop(owner_id, None)


def _local_model_path_reason() -> str | None:
    """Local-only readiness; the start route refreshes before Scribe is reserved."""
    from .. import chatgpt_plan
    if not chatgpt_plan._enabled():
        return "voice_call_model_disabled"
    try:
        account = (chatgpt_plan._read().get("account") or {})
    except (HTTPException, OSError, ValueError, TypeError, AttributeError):
        return "voice_call_model_authorization_unreadable"
    if not (account.get("access_token") and account.get("refresh_token") and
            "chatgpt.tokens.use.direct" in account.get("scopes", [])):
        return "voice_call_model_authorization_missing"
    if (account.get("expires_at", 0) <= time.time() + 60 and
            not account.get("client_id")):
        return "voice_call_model_refresh_unavailable"
    if ((CallManager.headset_conversation() is not None or
         CallManager.single_button_conversation() is not None) and
            account.get("expires_at", 0) <= time.time() + CALL_SECONDS + 60):
        return "voice_call_model_local_token_expired"
    return None


def install(app, connect):
    manager = CallManager(connect)
    router = APIRouter(prefix="/api/voice/calls", tags=["voice-continuous"])

    @router.get("/batch")
    def batch_status(request: Request):
        if not manager.enabled() or not manager.isolated():
            raise HTTPException(409, "voice_call_disabled_or_unconfirmed")
        try:
            return continuous_batch.status(
                connect, os.getenv("YOSAKI_VOICE_CONTINUOUS_BATCH_ID", ""),
                request.state.user["id"])
        except continuous_batch.BatchRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/availability")
    def availability(request: Request, character_id: int, conversation_id: int,
                     model_profile_id: int):
        try:
            available = manager.availability(request.state.user["id"], character_id,
                conversation_id, model_profile_id, request.state.user["token_hash"])
            if manager.daily():
                reason = _local_model_path_reason()
                available["model_path_ready"] = reason is None
                available["model_path_reason"] = reason
                if reason:
                    available["available_rounds"] = 0
            return available
        except (CallRejected, auto_budget.BudgetUnavailable,
                scribe_budget.BudgetUnavailable) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/settings")
    def call_settings(request: Request):
        if not manager.enabled() or not manager.daily():
            raise HTTPException(409, "voice_call_daily_disabled")
        return continuous_daily.settings(connect, request.state.user["id"])

    @router.put("/settings")
    def save_call_settings(payload: CallSettingsInput, request: Request):
        if not manager.enabled() or not manager.daily():
            raise HTTPException(409, "voice_call_daily_disabled")
        try:
            return continuous_daily.put_settings(connect, request.state.user["id"],
                continuous_daily.CallLimits(payload.rounds, payload.seconds,
                                            payload.tts_requests, payload.tts_characters),
                payload.expected_revision, payload.confirm_increase)
        except continuous_daily.DailyCallRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("")
    async def start(payload: CallStart, request: Request):
        try:
            if manager.daily():
                if not payload.acknowledge_auto_send:
                    raise CallRejected("voice_call_disabled_or_unconfirmed")
                available = manager.availability(request.state.user["id"],
                    payload.character_id, payload.conversation_id,
                    payload.model_profile_id, request.state.user.get("token_hash", ""))
                if available["conversation_incomplete"]:
                    raise CallRejected("voice_call_conversation_incomplete")
                if available["available_rounds"] < (2 if manager.headset_conversation() is not None else 1):
                    raise CallRejected("voice_call_not_ready_or_daily_exhausted")
                reason = _local_model_path_reason()
                if reason:
                    raise CallRejected(reason)
                from .. import chatgpt_plan
                if manager.acceptance():
                    await chatgpt_plan.access_token(allow_refresh=False)
                elif manager.headset_conversation() is None:
                    await chatgpt_plan.access_token()  # Normal daily renewal.
            return manager.start(request.state.user["id"], payload.character_id,
                                 payload.conversation_id, payload.model_profile_id,
                                 payload.acknowledge_auto_send,
                                 request.state.user.get("token_hash", ""))
        except CallRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{call_id}")
    def status(call_id: str, request: Request):
        try:
            with manager.lock:
                return manager.summary(manager._get(call_id, request.state.user["id"]))
        except CallRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{call_id}/timeline")
    def timeline(call_id: str, request: Request):
        try:
            return manager.timeline(call_id, request.state.user["id"])
        except CallRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/{call_id}/interrupt")
    def interrupt(call_id: str, request: Request,
                  payload: InterruptObservation | None = None):
        try:
            return manager.interrupt(call_id, request.state.user["id"], payload)
        except CallRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/{call_id}/playback-drained")
    def playback_drained(call_id: str, payload: dict, request: Request):
        if set(payload) != {"turn_id", "message_id"} or not isinstance(payload["turn_id"], str) or \
                type(payload["message_id"]) is not int:
            raise HTTPException(422, "voice_call_playback_binding_invalid")
        try:
            can_continue = manager.playback_drained(
                call_id, payload["turn_id"], request.state.user["id"], payload["message_id"])
            return {"drained": True, "can_continue": can_continue}
        except CallRejected as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/{call_id}", status_code=204)
    def end(call_id: str, request: Request):
        manager.end(call_id, request.state.user["id"])

    app.include_router(router)
    return manager
