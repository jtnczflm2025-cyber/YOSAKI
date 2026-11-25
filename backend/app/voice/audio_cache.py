"""V2 derived audio cache. Paths are never accepted from an HTTP client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from threading import RLock

from . import config
from .speech_planner import PLANNER_VERSION


TTL_DAYS = 7
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_OBJECT_BYTES = 16 * 1024 * 1024
_FILE = re.compile(r"[0-9a-f]{2}/[0-9a-f]{64}\.mp3\Z")
_BITRATE = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_CACHE_LOCK = RLock()


class CacheCapacityError(RuntimeError):
    """No room for a bounded response before a provider request is sent."""


def synchronized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _CACHE_LOCK:
            return function(*args, **kwargs)
    return wrapped


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root() -> Path:
    return config.data_dir() / "voice" / "audio"


def safe_path(relative: str) -> Path:
    if not _FILE.fullmatch(relative):
        raise ValueError("缓存路径无效")
    raw_base = root()
    for part in (raw_base.parent.parent, raw_base.parent, raw_base):
        if part.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(part)):
            raise ValueError("缓存路径越界")
    base = raw_base.resolve()
    path = base / relative
    for part in (base, path.parent, path):
        if part.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(part)):
            raise ValueError("缓存路径越界")
    if not path.resolve().is_relative_to(base):
        raise ValueError("缓存路径越界")
    return path


def validate_mp3(data: bytes) -> None:
    """Require complete MPEG-1 Layer III 44.1 kHz frames, allowing ID3 tags."""
    if len(data) < 800 or len(data) > MAX_OBJECT_BYTES:
        raise ValueError("MP3 大小无效")
    pos = 0
    if data.startswith(b"ID3"):
        if len(data) < 10 or any(x & 0x80 for x in data[6:10]):
            raise ValueError("ID3 头无效")
        pos = 10 + ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
        if data[5] & 0x10:
            pos += 10
    frames = 0
    while pos + 4 <= len(data):
        if len(data) - pos == 128 and data[pos:pos + 3] == b"TAG":
            pos = len(data)
            break
        head = int.from_bytes(data[pos:pos + 4], "big")
        if (head >> 21 != 0x7FF or (head >> 19) & 3 != 3 or
                (head >> 17) & 3 != 1 or (head >> 10) & 3 != 0):
            raise ValueError("MP3 帧头无效")
        rate = (head >> 12) & 15
        if rate < 1 or rate > 14:
            raise ValueError("MP3 比特率无效")
        length = (144000 * _BITRATE[rate]) // 44100 + ((head >> 9) & 1)
        if pos + length > len(data):
            raise ValueError("MP3 帧截断")
        pos += length
        frames += 1
    if pos != len(data) or frames < 2:
        raise ValueError("MP3 片段不完整")


def key(character_id: int, voice_id: str, revision: int,
        language: str, text: str, *, expression_mode: str = "none",
        expression_version: str | None = None) -> tuple[str, dict]:
    meta = {"character_id": character_id,
            "voice_hash": hashlib.sha256(voice_id.encode()).hexdigest(),
            "profile_revision": revision, "model_id": "eleven_v4",
            "output_format": "mp3_44100_128", "language_code": language,
            "text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "planner_version": PLANNER_VERSION}
    if expression_mode != "none":
        if expression_mode not in ("thoughtful", "auto") or not expression_version:
            raise ValueError("表达缓存模式或版本无效")
        meta["expression_mode"] = expression_mode
        meta["expression_version"] = expression_version
        meta["planner_version"] = PLANNER_VERSION + "+" + expression_version
    encoded = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), meta


def is_ready(connect, character_id: int, voice_id: str, revision: int,
             language: str, text: str, *, expression_mode: str = "none",
             expression_version: str | None = None) -> bool:
    """Read-only, validated cache check for a call's additional dispatch limit."""
    cache_key, _ = key(character_id, voice_id, revision, language, text,
                       expression_mode=expression_mode, expression_version=expression_version)
    with connect() as db:
        row = db.execute("""SELECT status,relative_path,audio_sha256,expires_at
            FROM voice_audio_cache WHERE cache_key=?""", (cache_key,)).fetchone()
    if not row or row["status"] != "ready" or row["expires_at"] <= stamp():
        return False
    try:
        path = safe_path(row["relative_path"])
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["audio_sha256"]
    except (OSError, ValueError, TypeError):
        return False


def _bound_sql(db) -> str:
    expression = db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
        AND name='voice_expression_audio'""").fetchone()
    extra = " OR EXISTS(SELECT 1 FROM voice_expression_audio e WHERE e.cache_id=c.id)" if expression else ""
    return "(EXISTS(SELECT 1 FROM voice_message_audio b WHERE b.cache_id=c.id)" + extra + ") AS bound"


@synchronized
def claim(connect, character_id: int, voice_id: str, revision: int,
          language: str, text: str, active_attempts: set[str], *,
          expression_mode: str = "none", expression_version: str | None = None
          ) -> tuple[str, int, str]:
    cache_key, meta = key(character_id, voice_id, revision, language, text,
                          expression_mode=expression_mode,
                          expression_version=expression_version)
    removed_paths: list[str] = []
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM voice_audio_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if row:
            if row["status"] == "ready" and row["expires_at"] > stamp():
                try:
                    path = safe_path(row["relative_path"])
                    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["audio_sha256"]:
                        db.execute("UPDATE voice_audio_cache SET last_accessed_at=? WHERE id=?", (stamp(), row["id"]))
                        return "ready", row["id"], row["attempt_id"]
                except (OSError, ValueError, TypeError):
                    pass
                db.execute("UPDATE voice_audio_cache SET status='unknown',audio_bytes=NULL WHERE id=?", (row["id"],))
                return "unknown", row["id"], row["attempt_id"]
            if row["status"] == "generating" and row["attempt_id"] in active_attempts:
                return "busy", row["id"], row["attempt_id"]
            if row["status"] == "generating":
                db.execute("UPDATE voice_audio_cache SET status='unknown',audio_bytes=NULL WHERE id=?", (row["id"],))
            return "unknown", row["id"], row["attempt_id"]
        try:
            free_bytes = shutil.disk_usage(config.data_dir()).free
        except OSError as exc:
            raise CacheCapacityError("无法确认缓存磁盘空间") from exc
        if free_bytes < MAX_OBJECT_BYTES:
            raise CacheCapacityError("缓存磁盘空间不足")
        rows = db.execute("""SELECT c.id,c.relative_path,c.audio_bytes,c.expires_at,c.status,"""
                          + _bound_sql(db) + """ FROM voice_audio_cache c
            ORDER BY c.last_accessed_at,c.id""").fetchall()
        now = stamp()
        occupied = sum((r["audio_bytes"] or MAX_OBJECT_BYTES) for r in rows
                       if r["status"] in ("ready", "generating"))
        for candidate in rows:
            if candidate["status"] != "ready":
                continue
            expired = candidate["expires_at"] <= now
            if not expired and (candidate["bound"] or occupied + MAX_OBJECT_BYTES <= MAX_CACHE_BYTES):
                continue
            db.execute("DELETE FROM voice_audio_cache WHERE id=?", (candidate["id"],))
            occupied -= candidate["audio_bytes"] or MAX_OBJECT_BYTES
            if candidate["relative_path"]:
                removed_paths.append(candidate["relative_path"])
        if occupied + MAX_OBJECT_BYTES > MAX_CACHE_BYTES:
            raise CacheCapacityError("缓存容量不足，已绑定音频不会被容量淘汰")
        attempt = uuid.uuid4().hex
        moment = stamp()
        expiry = (datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
        cur = db.execute("""INSERT INTO voice_audio_cache
            (character_id,cache_key,voice_hash,model_id,output_format,language_code,text_hash,
             planner_version,profile_revision,status,audio_bytes,attempt_id,created_at,expires_at,last_accessed_at)
            VALUES (?,?,?,?,?,?,?,?,?,'generating',?,?,?,?,?)""",
            (character_id, cache_key, meta["voice_hash"], meta["model_id"],
             meta["output_format"], meta["language_code"], meta["text_hash"],
            meta["planner_version"], revision, MAX_OBJECT_BYTES, attempt, moment, expiry, moment))
        result = "new", cur.lastrowid, attempt
    for relative in removed_paths:
        try:
            safe_path(relative).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    active_attempts.add(attempt)
    return result


@synchronized
def publish(connect, cache_id: int, attempt: str, data: bytes) -> None:
    validate_mp3(data)
    sha = hashlib.sha256(data).hexdigest()
    with connect() as db:
        row = db.execute("SELECT cache_key FROM voice_audio_cache WHERE id=? AND attempt_id=? AND status='generating'",
                         (cache_id, attempt)).fetchone()
    if row is None:
        raise ValueError("缓存任务已失效")
    cache_key = row["cache_key"]
    relative = cache_key[:2] + "/" + cache_key + ".mp3"
    path = safe_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with part.open("xb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(part, path)
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE voice_audio_cache
                SET status='ready',relative_path=?,audio_sha256=?,audio_bytes=?,last_accessed_at=?
                WHERE id=? AND attempt_id=? AND status='generating'""",
                (relative, sha, len(data), stamp(), cache_id, attempt)).rowcount
            if not changed:
                raise ValueError("缓存任务已失效")
    finally:
        part.unlink(missing_ok=True)


def mark(connect, cache_id: int, attempt: str, *, unknown: bool) -> None:
    with connect() as db:
        db.execute("UPDATE voice_audio_cache SET status=?,audio_bytes=NULL WHERE id=? AND attempt_id=? AND status='generating'",
                   ("unknown" if unknown else "failed", cache_id, attempt))


@synchronized
def discard_unattempted(connect, cache_id: int, attempt: str) -> None:
    """Release only a new placeholder that never reached a provider call."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""DELETE FROM voice_audio_cache
            WHERE id=? AND attempt_id=? AND status='generating' AND relative_path IS NULL""",
            (cache_id, attempt))


@synchronized
def clear(connect, character_id: int | None = None) -> int:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        where = " WHERE character_id=?" if character_id is not None else ""
        args = (character_id,) if character_id is not None else ()
        paths = [row[0] for row in db.execute("SELECT relative_path FROM voice_audio_cache" + where, args)
                 if row[0]]
        count = db.execute("DELETE FROM voice_audio_cache" + where, args).rowcount
    for relative in paths:
        try:
            safe_path(relative).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    sweep_orphans(connect)
    return count


@synchronized
def prune(connect, protected_ids: set[int] | None = None) -> int:
    """Prune expired/LRU objects. Never delete an active generation."""
    protected_ids = protected_ids or set()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("""SELECT c.id,c.relative_path,c.audio_bytes,c.expires_at,c.status,"""
                          + _bound_sql(db) + """ FROM voice_audio_cache c
            ORDER BY c.last_accessed_at,c.id""").fetchall()
        now = stamp()
        ready_size = sum(row["audio_bytes"] or 0 for row in rows if row["status"] == "ready")
        remove = []
        for row in rows:
            if row["id"] in protected_ids or row["status"] == "generating":
                continue
            if row["expires_at"] <= now or (row["status"] == "ready" and not row["bound"]
                                             and ready_size > MAX_CACHE_BYTES):
                remove.append(row)
                if row["status"] == "ready":
                    ready_size -= row["audio_bytes"] or 0
        for row in remove:
            db.execute("DELETE FROM voice_audio_cache WHERE id=?", (row["id"],))
    for row in remove:
        if row["relative_path"]:
            try:
                safe_path(row["relative_path"]).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
    sweep_orphans(connect)
    return len(remove)


@synchronized
def sweep_orphans(connect, grace_seconds: int = 3600) -> int:
    """Only inspect generated names inside this data directory; do not follow links."""
    import time
    base = root()
    if not base.is_dir() or base.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(base)):
        return 0
    with connect() as db:
        known = {row[0] for row in db.execute("SELECT relative_path FROM voice_audio_cache WHERE relative_path IS NOT NULL")}
    removed = 0
    for folder in base.iterdir():
        if not re.fullmatch(r"[0-9a-f]{2}", folder.name) or not folder.is_dir() or folder.is_symlink():
            continue
        if hasattr(os.path, "isjunction") and os.path.isjunction(folder):
            continue
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if not re.fullmatch(r"[0-9a-f]{64}\.mp3(?:\.[0-9a-f]{32}\.part)?", path.name):
                continue
            relative = folder.name + "/" + path.name
            if relative in known or time.time() - path.stat().st_mtime < grace_seconds:
                continue
            path.unlink()
            removed += 1
    return removed
