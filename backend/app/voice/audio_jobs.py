"""Bounded, explicit V2 HTTP synthesis jobs. No startup dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
from threading import RLock
import uuid
from typing import Callable

from fastapi import HTTPException

from . import audio_cache, auto_budget, manager
from .elevenlabs_adapter import ElevenLabsAdapter, ProviderError

HTTP_TTD_MAX_SECONDS = 30


class DispatchGuardRejected(ValueError):
    """A local pre-dispatch check stopped the request before provider I/O."""


class LocalPreflightRejected(ValueError):
    """Provider I/O is known not to have started."""


@dataclass
class Job:
    id: str
    owner_id: int
    context: dict
    auto_session_hash: str | None = None
    auto_reserved: bool = False
    dispatch_guard: Callable[[int], None] | None = field(default=None, repr=False)
    batch_claim: dict | None = field(default=None, repr=False)
    status: str = "queued"
    error: str | None = None
    failure_phase: str | None = None
    failed_segment: int | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    cancel_requested: bool = field(default=False, repr=False)
    lock: RLock = field(default_factory=RLock, repr=False)


class VoiceJobs:
    def __init__(self, connect, adapter_factory=ElevenLabsAdapter):
        self.connect = connect
        self.adapter_factory = adapter_factory
        self.jobs: dict[str, Job] = {}
        self.active_attempts: set[str] = set()

    def submit(self, context: dict, owner_id: int, *,
               auto_session_hash: str | None = None,
               dispatch_guard: Callable[[int], None] | None = None,
               batch_claim: dict | None = None) -> dict:
        for existing in self.jobs.values():
            if existing.owner_id == owner_id and existing.status in ("queued", "generating"):
                if (existing.context["message_id"] == context["message_id"] and
                        existing.context["digest"] == context["digest"]):
                    return self.public(existing)
                raise HTTPException(409, "已有朗读生成任务，请先等待或停止")
        audio_cache.prune(self.connect)
        job = Job(uuid.uuid4().hex, owner_id, context, auto_session_hash,
                  dispatch_guard=dispatch_guard, batch_claim=batch_claim)
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job))
        return self.public(job)

    def public(self, job: Job) -> dict:
        return {"job_id": job.id, "message_id": job.context["message_id"],
                "mode": job.context.get("mode", "none"),
                "status": job.status, "error_code": job.error,
                "failure_phase": job.failure_phase, "failed_segment": job.failed_segment,
                "audio": self.bindings(job.context) if job.status == "completed" else []}

    def get(self, job_id: str, owner_id: int) -> dict:
        job = self.jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            raise HTTPException(404, "朗读任务不存在")
        return self.public(job)

    def cancel(self, job_id: str, owner_id: int) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.owner_id != owner_id:
            raise HTTPException(404, "朗读任务不存在")
        self._request_cancel(job)

    @staticmethod
    def _request_cancel(job: Job) -> None:
        with job.lock:
            if job.status not in ("queued", "generating"):
                return
            job.cancel_requested = True
            if job.task and not job.task.done():
                job.task.get_loop().call_soon_threadsafe(job.task.cancel)

    def cancel_owner(self, owner_id: int, character_id: int | None = None) -> None:
        for job in self.jobs.values():
            if (job.owner_id == owner_id and (character_id is None or
                    job.context["character_id"] == character_id) and
                    job.task and not job.task.done()):
                self._request_cancel(job)

    async def shutdown(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task and not job.task.done()]
        for job in self.jobs.values():
            if job.task in tasks:
                self._request_cancel(job)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job: Job) -> None:
        current: tuple[int, str] | None = None
        current_unknown = True
        ids: list[int] = []
        try:
            with job.lock:
                if job.cancel_requested:
                    raise asyncio.CancelledError
                job.status = "generating"
            if not os.getenv("ELEVENLABS_API_KEY", "").strip():
                raise LocalPreflightRejected("key_missing")
            async with asyncio.timeout(260):
                for text in job.context["segments"]:
                    if job.cancel_requested:
                        raise asyncio.CancelledError
                    latest = manager.speech_context(self.connect, job.context["message_id"],
                                                    job.owner_id, job.context.get("mode", "none"))
                    if latest["digest"] != job.context["digest"]:
                        raise ValueError("source_changed")
                    state, cache_id, attempt = audio_cache.claim(
                        self.connect, job.context["character_id"], job.context["voice_id"],
                        job.context["revision"], job.context["language"], text,
                        self.active_attempts,
                        expression_mode=job.context.get("mode", "none"),
                        expression_version=job.context.get("expression_planner_version"))
                    if state == "unknown":
                        raise ValueError("cache_unknown")
                    if state == "busy":
                        for _ in range(300):
                            await asyncio.sleep(0.1)
                            with self.connect() as db:
                                row = db.execute("SELECT status FROM voice_audio_cache WHERE id=?", (cache_id,)).fetchone()
                            if row and row["status"] == "ready":
                                state = "ready"
                                break
                            if not row or row["status"] != "generating":
                                raise ValueError("cache_unknown")
                        else:
                            raise ValueError("cache_unknown")
                    if state == "new":
                        current = cache_id, attempt
                        if job.cancel_requested:
                            raise asyncio.CancelledError
                        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
                        if not key:
                            current_unknown = False
                            audio_cache.discard_unattempted(self.connect, cache_id, attempt)
                            self.active_attempts.discard(attempt)
                            current = None
                            raise LocalPreflightRejected("key_missing")
                        adapter = self.adapter_factory(key)
                        if job.dispatch_guard is not None:
                            try:
                                job.dispatch_guard(len(text))
                            except Exception as exc:
                                current_unknown = False
                                audio_cache.discard_unattempted(self.connect, cache_id, attempt)
                                self.active_attempts.discard(attempt)
                                current = None
                                raise DispatchGuardRejected from exc
                        if job.auto_session_hash is not None:
                            try:
                                auto_budget.reserve(self.connect, job.auto_session_hash,
                                                    job.owner_id, len(text),
                                                    first_request=not job.auto_reserved,
                                                    batch_claim=job.batch_claim)
                                job.auto_reserved = True
                            except (auto_budget.BudgetExhausted,
                                    auto_budget.BudgetUnavailable):
                                current_unknown = False
                                audio_cache.discard_unattempted(self.connect, cache_id, attempt)
                                self.active_attempts.discard(attempt)
                                current = None
                                raise
                        try:
                            async with asyncio.timeout(HTTP_TTD_MAX_SECONDS):
                                audio = await adapter.synthesize_http(text, job.context["voice_id"],
                                                                       job.context["language"])
                        except (ProviderError, TimeoutError):
                            job.failure_phase = getattr(adapter, "synthesis_phase", None)
                            job.failed_segment = len(ids) + 1
                            raise
                        with job.lock:
                            if job.cancel_requested:
                                raise asyncio.CancelledError
                            audio_cache.publish(self.connect, cache_id, attempt, audio)
                        self.active_attempts.discard(attempt)
                        current = None
                    ids.append(cache_id)
                latest = manager.speech_context(self.connect, job.context["message_id"],
                                                job.owner_id, job.context.get("mode", "none"))
                if latest["digest"] != job.context["digest"]:
                    raise ValueError("source_changed")
                with job.lock:
                    if job.cancel_requested:
                        raise asyncio.CancelledError
                    with self.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        mode = job.context.get("mode", "none")
                        table = "voice_message_audio" if mode == "none" else "voice_expression_audio"
                        if mode == "none":
                            db.execute("DELETE FROM voice_message_audio WHERE message_id=?",
                                       (job.context["message_id"],))
                        else:
                            db.execute("DELETE FROM voice_expression_audio WHERE message_id=? AND mode=?",
                                       (job.context["message_id"], mode))
                        for seq, cache_id in enumerate(ids):
                            if mode == "none":
                                db.execute("""INSERT INTO voice_message_audio
                                    (message_id,cache_id,sequence,source_sha256,profile_revision,created_at)
                                    VALUES (?,?,?,?,?,?)""",
                                    (job.context["message_id"], cache_id, seq,
                                     job.context["digest"], job.context["revision"], audio_cache.stamp()))
                            else:
                                db.execute("""INSERT INTO voice_expression_audio
                                    (message_id,cache_id,mode,sequence,source_sha256,profile_revision,created_at)
                                    VALUES (?,?,?,?,?,?,?)""",
                                    (job.context["message_id"], cache_id, mode, seq,
                                     job.context["digest"], job.context["revision"], audio_cache.stamp()))
                    audio_cache.prune(self.connect, protected_ids=set(ids))
                    job.status = "completed"
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error = "possibly_billed"
        except ProviderError as exc:
            job.status = "unknown" if exc.unknown else "failed"
            job.error = "provider_unknown" if exc.unknown else "provider_rejected"
            current_unknown = exc.unknown
        except audio_cache.CacheCapacityError:
            job.status = "failed"
            job.error = "cache_capacity"
        except auto_budget.BudgetExhausted:
            job.status = "failed"
            job.error = "auto_budget_exhausted"
        except auto_budget.BudgetUnavailable:
            job.status = "failed"
            job.error = "auto_budget_unavailable"
        except DispatchGuardRejected:
            job.status = "failed"
            job.error = "voice_call_disallowed"
        except LocalPreflightRejected:
            job.status = "failed"
            job.error = "key_missing"
        except (HTTPException, ValueError, TimeoutError, OSError):
            job.status = "unknown"
            job.error = "generation_unknown"
        except Exception:
            job.status = "unknown"
            job.error = "generation_unknown"
        finally:
            if current:
                audio_cache.mark(self.connect, current[0], current[1], unknown=current_unknown)
                self.active_attempts.discard(current[1])

    def bindings(self, context: dict) -> list[dict]:
        mode = context.get("mode", "none")
        table = "voice_message_audio" if mode == "none" else "voice_expression_audio"
        condition = "AND b.mode=?" if mode != "none" else ""
        with self.connect() as db:
            rows = db.execute(f"""SELECT b.id,b.sequence,c.relative_path,c.audio_sha256,c.expires_at
                FROM {table} b JOIN voice_audio_cache c ON c.id=b.cache_id
                WHERE b.message_id=? AND b.source_sha256=? AND b.profile_revision=?
                {condition} AND c.status='ready' ORDER BY b.sequence""",
                (context["message_id"], context["digest"], context["revision"],
                 *((mode,) if mode != "none" else ()))).fetchall()
        if len(rows) != len(context["segments"]):
            return []
        import hashlib
        result = []
        for row in rows:
            if row["expires_at"] <= audio_cache.stamp():
                return []
            try:
                path = audio_cache.safe_path(row["relative_path"])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["audio_sha256"]:
                    return []
            except (ValueError, OSError, TypeError):
                return []
            endpoint = "audio" if mode == "none" else "expression-audio"
            result.append({"binding_id": row["id"], "sequence": row["sequence"],
                           "url": f"/api/voice/{endpoint}/{row['id']}"})
        return result

    def audio_path(self, binding_id: int, owner_id: int, *, expression: bool = False):
        table = "voice_expression_audio" if expression else "voice_message_audio"
        mode_column = "b.mode" if expression else "'none' AS mode"
        with self.connect() as db:
            row = db.execute(f"""SELECT b.message_id,b.source_sha256,b.profile_revision,{mode_column},
                c.relative_path,c.audio_sha256,c.expires_at,c.status
                FROM {table} b JOIN voice_audio_cache c ON c.id=b.cache_id
                WHERE b.id=?""", (binding_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "音频绑定不存在")
        context = manager.speech_context(self.connect, row["message_id"], owner_id, row["mode"])
        if (context["digest"] != row["source_sha256"] or
                context["revision"] != row["profile_revision"] or
                row["status"] != "ready" or row["expires_at"] <= audio_cache.stamp()):
            raise HTTPException(410, "音频绑定已失效")
        try:
            path = audio_cache.safe_path(row["relative_path"])
            import hashlib
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["audio_sha256"]:
                raise ValueError
        except (ValueError, OSError, TypeError):
            raise HTTPException(410, "音频缓存已失效") from None
        return path
