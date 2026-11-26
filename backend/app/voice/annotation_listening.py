"""Fixed two-request listening gate, separate from the network-denied lab.

Reuses the HTTP adapter, Voice authority, owner/day reservations, exclusive
receipt marker and atomic result writer. No approval issuer or auto retries.
"""
from __future__ import annotations

import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

import httpx

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import audio_cache, auto_budget, config, storage
from .annotation_planner import fingerprint
from .elevenlabs_adapter import ElevenLabsAdapter, ProviderError
from .expression_fixture import _check_result_dir, _write_json
from .realtime_receipt import occupy_once

ROOT = Path(__file__).resolve().parents[3]


class ListeningError(ValueError):
    pass


class VoiceAuthority:
    """Public authority precheck is SQLite mode=ro; only reserve() writes usage."""
    def __init__(self, database: Path, session_hash: str, binding: dict, *, mock_only=False):
        self.database = database.resolve()
        self.session_hash, self.binding, self.mock_only = session_hash, binding, mock_only
        if not mock_only and self.database != ROOT / "data" / "yosaki.sqlite3":
            raise ListeningError("daily_database_path_mismatch")

    @contextmanager
    def read(self):
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve() != self.database:
                raise ListeningError("database_path_changed")
            yield db

    def check(self, *, jobs=0, requests=0, characters=0) -> dict:
        if not config.enabled():
            raise ListeningError("voice_feature_disabled")
        with self.read() as db:
            session = db.execute("SELECT user_id,expires_at FROM auth_sessions WHERE token_hash=?",
                                 (self.session_hash,)).fetchone()
            if not db.execute("SELECT 1 FROM users WHERE id=1").fetchone() or not session or session["user_id"] != 1 or \
                    datetime.fromisoformat(session["expires_at"]) <= datetime.now(timezone.utc):
                raise ListeningError("owner_session_unavailable")
            character = db.execute("SELECT enabled FROM characters WHERE id=1").fetchone()
            profile, consent = storage.profile(db, 1), storage.consent(db, 1)
            b = self.binding
            if not character or not character[0] or not profile or not profile["active"] or \
                    profile["provider"] != "elevenlabs" or profile["revision"] != b["expected_profile_revision"] or \
                    hashlib.sha256(profile["voice_id"].encode()).hexdigest() != b["expected_voice_sha256"]:
                raise ListeningError("voice_profile_binding_changed")
            if not consent or consent["notice_version"] != b["expected_consent_version"] or \
                    consent["notice_version"] != config.NOTICE_VERSION:
                raise ListeningError("voice_consent_changed")
            if profile["configuration"]["model_standard"] != "eleven_v4" or \
                    profile["configuration"]["audio_format"] != "mp3_44100_128":
                raise ListeningError("voice_parameters_changed")
        if not auto_budget.enabled() or not auto_budget.daily_enabled():
            raise ListeningError("daily_budget_gate_not_enabled")
        usage = auto_budget.usage(self.read, self.session_hash, 1)
        remaining = usage["daily"]["remaining"]
        if remaining["jobs"] < jobs or remaining["requests"] < requests or remaining["characters"] < characters:
            raise ListeningError("daily_budget_insufficient")
        return {"voice_id": profile["voice_id"], "daily": usage["daily"]}

    @contextmanager
    def write(self):
        with closing(sqlite3.connect(self.database, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def reserve(self, characters: int):
        # Existing owner/day + login audit, one task per fixed variant. No refund.
        auto_budget.reserve(self.write, self.session_hash, 1, characters, first_request=True)


def expected_approval(manifest: dict, source_database: Path, session_hash: str, proxy_url, *, mock=False):
    run_directory = (source_database.resolve().parent.parent if mock else
                     Path(os.environ["LOCALAPPDATA"]) / "YOSAKI" / "voice-runs" / manifest["run_id"])
    return {"schema_version": 1, "run_id": manifest["run_id"],
            "manifest_sha256": fingerprint(manifest), "code_sha256": manifest["code_sha256"],
            "source_database": str(source_database.resolve()), "session_sha256": session_hash,
            "network_proxy_url": proxy_url, "provider_kind": "mock_transport_only" if mock else "real_approved",
            "voice_binding": manifest["voice_binding"], "budget": manifest["budget"],
            "run_directory": str(run_directory.resolve()),
            "third_party_processing_confirmed": True, "billing_uncertainty_acknowledged": True}


def verify_manifest(manifest: dict, *, mock=False):
    from scripts.voice_annotation_candidate import build
    expected = build()
    if mock:
        expected["status"] = "mock_transport_only"
        expected["voice_binding"]["expected_voice_sha256"] = manifest["voice_binding"]["expected_voice_sha256"]
    if manifest != expected or manifest["budget"]["http_tts"] != 2 or \
            [x["characters"] for x in manifest["requests"]] != [35, 56] or \
            sum(len(x["text"]) for x in manifest["requests"]) != 91 or \
            manifest["planning_model_requests"] != 0:
        raise ListeningError("frozen_candidate_drift")


async def run_listening(manifest: dict, authority: VoiceAuthority, approval_file: Path,
                        result_dir: Path, key_loader, *, enabled=False, transport=None,
                        proxy_url=None):
    if not enabled:
        raise ListeningError("listening_dispatch_disabled")
    mock = isinstance(transport, (httpx.MockTransport, httpx.ASGITransport))
    if mock != authority.mock_only:
        raise ListeningError("provider_authority_kind_mismatch")
    verify_manifest(manifest, mock=mock)
    root = _check_result_dir(result_dir, authority.database)
    if root.is_relative_to(ROOT) or root.exists():
        raise ListeningError("result_directory_not_independent_or_exists")
    if approval_file.is_symlink():
        raise ListeningError("approval_file_invalid")
    receipt = approval_file.resolve()
    if receipt.parent != root.parent or not receipt.is_file() or receipt.stat().st_size > 16384:
        raise ListeningError("approval_file_invalid")
    expected = expected_approval(manifest, authority.database, authority.session_hash, proxy_url, mock=mock)
    if receipt.name != "approval.json" or root.name != "result" or \
            root.parent != Path(expected["run_directory"]):
        raise ListeningError("fixed_run_directory_mismatch")
    if json.loads(receipt.read_text(encoding="utf-8")) != expected:
        raise ListeningError("approval_binding_mismatch")
    authority.check(jobs=2, requests=2, characters=91)
    occupy_once(receipt.with_name(receipt.name + ".used"), {
        "run_id": manifest["run_id"], "manifest_sha256": fingerprint(manifest),
        "code_sha256": manifest["code_sha256"], "status": "occupied_outcome_unknown"})
    root.mkdir(exist_ok=False)
    record = {"run_id": manifest["run_id"], "manifest_sha256": fingerprint(manifest),
              "status": "running", "dispatched_http": 0, "dispatched_characters": 0,
              "planning_model_requests": 0, "actual_billing": "unknown", "items": [
              {"variant": r["variant"], "characters": r["characters"], "status": "not_executed"}
              for r in manifest["requests"]]}
    output = root / "result.json"
    _write_json(output, record)
    try:
        key = key_loader()
    except Exception:
        key = None
    if not isinstance(key, str) or not key:
        record["status"] = "stopped"
        record["error_code"] = "api_key_unavailable_before_dispatch"
        _write_json(output, record)
        return record
    adapter = ElevenLabsAdapter(key, transport=transport, proxy_url=proxy_url)
    for index, request in enumerate(manifest["requests"]):
        item = record["items"][index]
        try:
            verify_manifest(manifest, mock=mock)
            current = authority.check(jobs=1, requests=1, characters=request["characters"])
            authority.reserve(request["characters"])
            item.update(status="reserved_not_dispatched", budget_reserved=True)
            _write_json(output, record)
            authority.check()
        except Exception:
            item.update(status="not_dispatched", error_code="authority_or_budget_preflight_failed")
            record["status"] = "stopped"
            _write_json(output, record)
            return record
        item.update(status="attempted_result_unknown", audio_bytes=None)
        record["dispatched_http"] += 1
        record["dispatched_characters"] += request["characters"]
        _write_json(output, record)
        started = time.monotonic()
        try:
            async with asyncio.timeout(30):
                audio = await adapter.synthesize_http(request["text"], current["voice_id"], "ja")
            if time.monotonic() - started >= 30:
                raise TimeoutError
            audio_cache.validate_mp3(audio)
            authority.check()
            verify_manifest(manifest, mock=mock)
            filename = request["variant"] + ".mp3"
            path = root / filename
            with path.open("xb") as stream:
                stream.write(audio)
                stream.flush()
                os.fsync(stream.fileno())
            item.update(status="succeeded", audio_file=filename, audio_bytes=len(audio),
                        audio_sha256=hashlib.sha256(audio).hexdigest())
        except ProviderError as exc:
            item.update(status="unknown" if exc.unknown else "failed",
                        error_code="provider_unknown" if exc.unknown else "provider_rejected",
                        phase=adapter.synthesis_phase)
        except BaseException as exc:
            item.update(status="unknown", error_code="cancelled" if isinstance(exc, asyncio.CancelledError)
                        else "audio_or_storage_unknown", phase=adapter.synthesis_phase)
        item["elapsed_seconds"] = round(time.monotonic() - started, 4)
        _write_json(output, record)
        if item["status"] != "succeeded":
            record["status"] = "stopped"
            _write_json(output, record)
            return record
    record["status"] = "completed"
    _write_json(output, record)
    return record


def review_app(result_dir: Path) -> FastAPI:
    """Playback only. No generation endpoint; replay cannot dispatch anything."""
    root = _check_result_dir(result_dir)
    app, session = FastAPI(), secrets.token_urlsafe(32)
    prior = root / "playback.json"
    events = json.loads(prior.read_text(encoding="utf-8"))["events"][:128] if prior.exists() else []

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.url.path != "/" and not secrets.compare_digest(request.cookies.get("listening_review", ""), session):
            return JSONResponse({"error": "review_session_required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def page():
        html = (ROOT / "frontend" / "voice-expression-listen.html").read_text(encoding="utf-8")
        response = HTMLResponse(html)
        response.set_cookie("listening_review", session, httponly=True, samesite="strict")
        return response

    @app.get("/theme/{name}")
    async def theme(name: str):
        if name not in ("tokens.css", "silver.css"):
            return Response(status_code=404)
        return Response((ROOT / "frontend" / "src" / "ui" / name).read_text(encoding="utf-8"), media_type="text/css")

    @app.get("/audio/{variant}")
    async def audio(variant: str):
        if variant not in ("plain", "two_position_candidate"):
            return Response(status_code=404)
        record = json.loads((root / "result.json").read_text(encoding="utf-8"))
        item = next((x for x in record["items"] if x["variant"] == variant), {})
        path = root / (variant + ".mp3")
        if item.get("status") != "succeeded" or path.is_symlink() or not path.is_file() or path.stat().st_size > 16777216:
            return Response(status_code=404)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["audio_sha256"]:
            return Response(status_code=409)
        return Response(data, media_type="audio/mpeg")

    @app.post("/playback/{variant}/{event}")
    async def playback(variant: str, event: str, request: Request):
        if request.headers.get("origin") not in (None, str(request.base_url).rstrip("/")):
            return Response(status_code=403)
        if variant not in ("plain", "two_position_candidate") or event not in ("play", "pause", "ended", "error"):
            return Response(status_code=400)
        if len(events) < 128:
            events.append({"variant": variant, "event": event, "local_received_monotonic": time.monotonic()})
            _write_json(root / "playback.json", {"events": events, "kind": "browser_software_events_not_human_hearing"})
        return {"recorded": True}
    return app


