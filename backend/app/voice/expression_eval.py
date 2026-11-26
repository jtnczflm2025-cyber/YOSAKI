"""Isolated V3 expression evaluation; deliberately not installed in main.py.

The source database is only read through the existing saved-message authority
check.  Evaluation receipts and audio live in an explicit separate directory;
no voice_message_audio binding or formal cache row is created or replaced.
There is no default provider factory and dispatch is disabled by default.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from fastapi import HTTPException

from . import audio_cache, config, manager
from .elevenlabs_adapter import ProviderError
from .expression_planner import EXPRESSION_PLANNER_VERSION, ExpressionMode, build
from .speech_planner import SpeechPlanError


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RESULTS = 4
_MODEL = "eleven_v4"
_FORMAT = "mp3_44100_128"
_ENDPOINT = "/v1/text-to-dialogue"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class IsolatedExpressionStore:
    """A bounded, separate receipt and MP3 store, never the YOSAKI data dir."""

    def __init__(self, root: Path):
        raw = Path(root).expanduser()
        if not raw.is_absolute():
            raise ValueError("表达评测目录必须为绝对路径")
        daily = config.data_dir().resolve()
        resolved = raw.resolve()
        if resolved == daily or resolved.is_relative_to(daily) or daily.is_relative_to(resolved):
            raise ValueError("表达评测目录不得包含或位于 YOSAKI 数据目录内")
        for part in (raw, *raw.parents):
            if part.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(part)):
                raise ValueError("表达评测目录不得经过链接")
        resolved.mkdir(parents=True, exist_ok=True)
        self.root = resolved
        self.db_path = resolved / "expression_eval.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS samples (
                cache_key TEXT PRIMARY KEY, preview_sha256 TEXT NOT NULL,
                message_id INTEGER NOT NULL, mode TEXT NOT NULL,
                planner_version TEXT NOT NULL, profile_revision INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('generating','ready','unknown','failed')),
                attempt_id TEXT NOT NULL, audio_sha256 TEXT, audio_bytes INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def audio_path(self, cache_key: str) -> Path:
        if not _HEX64.fullmatch(cache_key):
            raise ValueError("表达缓存键无效")
        path = self.root / "audio" / cache_key[:2] / (cache_key + ".mp3")
        for part in (self.root, self.root / "audio", path.parent, path):
            if part.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(part)):
                raise ValueError("表达音频路径越界")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("表达音频路径越界")
        return path

    def record(self, cache_key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM samples WHERE cache_key=?", (cache_key,)).fetchone()
        return dict(row) if row else None

    def ready(self, cache_key: str) -> dict | None:
        row = self.record(cache_key)
        if row is None or row["status"] != "ready":
            return None
        try:
            data = self.audio_path(cache_key).read_bytes()
            audio_cache.validate_mp3(data)
        except (OSError, ValueError):
            return None
        if (len(data) != row["audio_bytes"] or
                hashlib.sha256(data).hexdigest() != row["audio_sha256"]):
            return None
        return row

    def claim(self, preview: dict) -> tuple[str, str | None]:
        cache_key = preview["cache_key"]
        if not _HEX64.fullmatch(cache_key):
            raise ValueError("表达缓存键无效")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM samples WHERE cache_key=?", (cache_key,)).fetchone()
            if row is not None:
                return row["status"], None
            count = db.execute("SELECT count(*) FROM samples").fetchone()[0]
            if count >= _MAX_RESULTS:
                raise HTTPException(409, "隔离评测样本已达四条上限")
            attempt = uuid.uuid4().hex
            stamp = _now()
            db.execute("""INSERT INTO samples
                (cache_key,preview_sha256,message_id,mode,planner_version,
                 profile_revision,status,attempt_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'generating',?,?,?)""",
                (cache_key, preview["preview_sha256"], preview["message_id"],
                 preview["mode"], EXPRESSION_PLANNER_VERSION,
                 preview["profile_revision"], attempt, stamp, stamp))
            return "new", attempt

    def finish(self, cache_key: str, attempt: str, status: str,
               audio: bytes | None = None) -> dict:
        if status not in ("ready", "unknown", "failed"):
            raise ValueError("表达评测终态无效")
        sha = None
        size = None
        published_path = None
        if status == "ready":
            if audio is None:
                raise ValueError("缺少完整音频")
            audio_cache.validate_mp3(audio)
            sha, size = hashlib.sha256(audio).hexdigest(), len(audio)
            path = self.audio_path(cache_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise ValueError("表达音频已存在，拒绝覆盖")
            part = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
            try:
                with part.open("xb") as fp:
                    fp.write(audio)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(part, path)
                published_path = path
            finally:
                part.unlink(missing_ok=True)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute("""UPDATE samples SET status=?,audio_sha256=?,audio_bytes=?,updated_at=?
                    WHERE cache_key=? AND attempt_id=? AND status='generating'""",
                    (status, sha, size, _now(), cache_key, attempt)).rowcount
                if changed != 1:
                    raise ValueError("表达评测尝试已失效")
        except BaseException:
            if published_path is not None:
                published_path.unlink(missing_ok=True)
            raise
        return self.record(cache_key)


class ExpressionEvaluator:
    """Previewable, one-request experiment with an explicit simulated gate."""

    def __init__(self, connect, store: IsolatedExpressionStore,
                 *, adapter_factory=None, simulated_execution_enabled: bool = False,
                 timeout_seconds: float = 30):
        if not 0 < timeout_seconds <= 30:
            raise ValueError("表达评测请求时限无效")
        self.connect = connect
        self.store = store
        self.adapter_factory = adapter_factory
        self.simulated_execution_enabled = simulated_execution_enabled
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def preview(self, message_id: int, owner_id: int, mode: ExpressionMode) -> dict:
        context = manager.speech_context(self.connect, message_id, owner_id)
        try:
            planned = build(context["body"], mode)
        except SpeechPlanError as exc:
            raise HTTPException(422, str(exc)) from exc
        if [planned.safe_text] != context["segments"]:
            raise HTTPException(409, "公开正文规划已变化")
        if planned.language != "ja":
            raise HTTPException(409, "首版表达评测仅支持确定的日语正文")
        voice_hash = hashlib.sha256(context["voice_id"].encode("utf-8")).hexdigest()
        effective = {
            "method": "POST", "endpoint": _ENDPOINT,
            "model_id": _MODEL, "output_format": _FORMAT,
            "language_code": "ja", "settings": "omitted", "seed": "omitted",
            "audio_effects": "omitted", "previous_text": "omitted",
            "future_text": "omitted", "inputs_count": 1,
        }
        basis = {"message_id": message_id, "owner_id": owner_id,
                 "source_digest": context["digest"],
                 "profile_revision": context["revision"], "voice_hash": voice_hash,
                 "mode": planned.mode, "tag": planned.tag,
                 "safe_text": planned.safe_text, "request_text": planned.request_text,
                 "expression_planner_version": EXPRESSION_PLANNER_VERSION,
                 "v2_planner_version": manager.PLANNER_VERSION,
                 "effective_parameters": effective}
        preview_sha = _digest(basis)
        cache_key = _digest({"kind": "isolated-expression-audio-v1",
                             "preview_sha256": preview_sha,
                             "mode": planned.mode,
                             "expression_planner_version": EXPRESSION_PLANNER_VERSION})
        ready = self.store.ready(cache_key)
        row = self.store.record(cache_key)
        return {"message_id": message_id, "character_id": context["character_id"],
                "source_digest": context["digest"],
                "mode": planned.mode, "tag": planned.tag,
                "safe_text": planned.safe_text,
                "request_text": planned.request_text,
                "characters": planned.characters,
                "non_whitespace_characters": sum(not c.isspace() for c in planned.request_text),
                "segments": 1, "requests": 1,
                "skipped_reasons": list(planned.skipped_reasons),
                "effective_parameters": effective,
                "voice_id_masked": "***" + context["voice_id"][-3:],
                "voice_id_sha256": voice_hash,
                "profile_revision": context["revision"],
                "v2_planner_version": manager.PLANNER_VERSION,
                "expression_planner_version": EXPRESSION_PLANNER_VERSION,
                "preview_sha256": preview_sha, "cache_key": cache_key,
                "cache_status": "ready" if ready else (row["status"] if row else "miss")}

    async def execute(self, message_id: int, owner_id: int, mode: ExpressionMode,
                      preview_sha256: str) -> dict:
        if not self.simulated_execution_enabled or self.adapter_factory is None:
            raise HTTPException(403, "表达评测执行门控关闭")
        async with self._lock:
            preview = self.preview(message_id, owner_id, mode)
            if preview["preview_sha256"] != preview_sha256:
                raise HTTPException(409, "表达预览已变化，请重新预览")
            key = preview["cache_key"]
            existing = self.store.ready(key)
            if existing:
                return {"status": "completed", "cache_hit": True,
                        "preview_sha256": preview_sha256, "cache_key": key,
                        "audio_sha256": existing["audio_sha256"]}
            state, attempt = self.store.claim(preview)
            if state != "new" or attempt is None:
                raise HTTPException(409, "表达结果已存在或结果未知，不自动重试")
            try:
                key_value = os.getenv("ELEVENLABS_API_KEY", "").strip()
                if not key_value:
                    raise ValueError("密钥状态已变化")
                source = manager.speech_context(self.connect, message_id, owner_id)
                if source["digest"] != preview["source_digest"] or source["revision"] != preview["profile_revision"]:
                    raise ValueError("表达来源在派发前变化")
                adapter = self.adapter_factory(key_value)
                deadline = time.monotonic() + self.timeout_seconds
                async with asyncio.timeout(self.timeout_seconds):
                    audio = await adapter.synthesize_http(preview["request_text"],
                                                          source["voice_id"], "ja")
                if time.monotonic() >= deadline:
                    raise TimeoutError("表达评测超过总时限")
                latest = self.preview(message_id, owner_id, mode)
                if latest["preview_sha256"] != preview_sha256:
                    raise ValueError("表达预览在生成中失效")
                row = self.store.finish(key, attempt, "ready", audio)
                return {"status": "completed", "cache_hit": False,
                        "preview_sha256": preview_sha256, "cache_key": key,
                        "audio_sha256": row["audio_sha256"],
                        "audio_bytes": row["audio_bytes"]}
            except ProviderError as exc:
                status = "unknown" if exc.unknown else "failed"
                self.store.finish(key, attempt, status)
                return {"status": status, "cache_hit": False,
                        "error_code": "provider_unknown" if exc.unknown else "provider_rejected"}
            except asyncio.CancelledError:
                self.store.finish(key, attempt, "unknown")
                raise
            except (TimeoutError, ValueError, OSError):
                self.store.finish(key, attempt, "unknown")
                return {"status": "unknown", "cache_hit": False,
                        "error_code": "result_unknown"}
            except Exception:
                self.store.finish(key, attempt, "unknown")
                return {"status": "unknown", "cache_hit": False,
                        "error_code": "result_unknown"}

