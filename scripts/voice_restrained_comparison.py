"""One fixed restrained candidate versus an immutable historical cache.

Not installed in daily routes. Building/reviewing never loads credentials.
Dispatch defaults closed, reuses Voice authority/day accounting and one-use
receipts, and accepts no caller-supplied speech text or provider parameters.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from backend.app.voice import audio_cache
from backend.app.voice.annotation_listening import VoiceAuthority, expected_approval
from backend.app.voice.annotation_planner import PARAMETERS, fingerprint
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter, ProviderError
from backend.app.voice.expression_fixture import _check_result_dir, _write_json
from backend.app.voice.expression_planner import build as expression
from backend.app.voice.realtime_receipt import occupy_once

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "voice-tone-restrained-20261123-r1"
TEXT = "まだ眠れないなら、少し話そう。"
REFERENCE_RUN = "voice-ai-product-listen-20261122-r1"
REFERENCE_SHA = "8be6faafc160645014cba37b6737ec55a58f15e0e9477de5d73b434e3dff9529"
VOICE_SHA = "e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767"
GOAL = {
    "version": "aya-performance-candidate-1", "status": "review_candidate_not_applied",
    "target": "慵懒、低沉、克制，亲近但不过分雀跃，允许自然停顿和留白。",
    "allow_empty_annotations": True, "require_tag_per_sentence": False,
    "avoid": ["强迫每句表演", "雀跃或过分开朗", "为降低音高擅自改变Voice或参数"],
    "limitations": ["表演目标不是供应商音高参数", "thoughtful不保证降低音高",
                    "本候选不启用relaxed", "不改聊天正文或整体人格"],
    "application": "future_planner_voice_setting_candidate_only_not_sent_in_this_tts",
}
FILES = sorted({p.relative_to(ROOT).as_posix()
    for p in (ROOT / "backend/app/voice").glob("*.py")} | {
    "scripts/voice_restrained_comparison.py", "tests/test_voice_restrained_comparison.py",
    "frontend/voice-restrained-listen.html", "backend/requirements.txt"})


def build() -> dict:
    base, candidate = expression(TEXT, "none"), expression(TEXT, "thoughtful")
    files = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES}
    assert base.characters == 15 and candidate.characters == 28
    assert candidate.request_text == "[thoughtful] " + base.request_text
    return {"run_id": RUN_ID, "status": "awaiting_separate_real_approval",
        "reference": {"run_id": REFERENCE_RUN, "conversation_id": 22, "message_id": 191,
            "generation_id": "a2ea8cdea4ea3598ffc7e7ecd7e4fba4", "text": TEXT,
            "audio_sha256": REFERENCE_SHA, "file": "message-191-ai-v1.mp3",
            "kind": "historical_reference_not_same_batch_ab", "new_requests": 0},
        "voice_binding": {"expected_profile_revision": 3, "expected_voice_sha256": VOICE_SHA,
            "expected_consent_version": "2026-11-08-v1"},
        "request": {"text": candidate.request_text, "characters": 28,
            "text_sha256": hashlib.sha256(candidate.request_text.encode()).hexdigest(),
            "parameters": dict(PARAMETERS), "endpoint": "/v1/text-to-dialogue",
            "single_changed_factor": "one_controlled_thoughtful_prefix"},
        "performance_goal_candidate": json.loads(json.dumps(GOAL, ensure_ascii=False)),
        "budget": {"http_tts": 1, "synthesis_jobs": 1, "request_characters": 28,
            "chat": 0, "planning": 0, "oauth": 0, "model_directory_get": 0,
            "tts_ws": 0, "scribe_ws": 0, "retries": 0, "redirects": 0,
            "per_http_total_seconds": 30, "per_audio_max_bytes": 16 * 1024 * 1024},
        "transport": {"trust_env": False, "proxy_url": None},
        "dependencies": {k: version(k) for k in ("httpx", "httpcore", "anyio", "fastapi",
            "pydantic", "markdown-it-py", "mdurl", "cryptography")},
        "code_files": files, "code_sha256": fingerprint(files)}


def reference_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "YOSAKI/voice-runs" / REFERENCE_RUN / "message-191-ai-v1.mp3"


def reference_audio(path: Path, sha: str) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("reference_audio_unavailable")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("reference_audio_changed")
    audio_cache.validate_mp3(data)
    return data


async def dispatch(manifest, authority: VoiceAuthority, approval: Path, output: Path,
                   key_loader, *, enabled=False, transport=None, reference=None):
    if not enabled:
        raise ValueError("comparison_dispatch_disabled")
    mock = isinstance(transport, (httpx.MockTransport, httpx.ASGITransport))
    if mock != authority.mock_only or (transport is not None and not mock):
        raise ValueError("transport_authority_mismatch")
    if manifest != build():
        raise ValueError("frozen_candidate_drift")
    if authority.binding != manifest["voice_binding"]:
        raise ValueError("authority_binding_mismatch")
    ref = Path(reference) if reference is not None else reference_path()
    if not mock and ref.resolve() != reference_path().resolve():
        raise ValueError("reference_path_mismatch")
    ref_sha = manifest["reference"]["audio_sha256"]
    # Test transports use synthetic audio, while the real candidate is immutable.
    if mock:
        ref_sha = hashlib.sha256(ref.read_bytes()).hexdigest()
    reference_audio(ref, ref_sha)
    root = _check_result_dir(output, authority.database)
    expected = expected_approval(manifest, authority.database, authority.session_hash, None, mock=mock)
    if (root.is_relative_to(ROOT) or root.exists() or root.name != "result" or
            root.parent != Path(expected["run_directory"]) or approval.is_symlink() or
            approval.resolve() != root.parent / "approval.json" or not approval.is_file() or
            approval.stat().st_size > 16384 or json.loads(approval.read_text(encoding="utf-8")) != expected):
        raise ValueError("approval_or_result_binding_mismatch")
    authority.check(jobs=1, requests=1, characters=28)
    occupy_once(approval.with_name("approval.json.used"), {"run_id": RUN_ID,
        "manifest_sha256": fingerprint(manifest), "status": "occupied_outcome_unknown"})
    root.mkdir(exist_ok=False)
    record = {"run_id": RUN_ID, "status": "occupied_not_dispatched", "reserved": False,
        "dispatched_http": 0, "dispatched_characters": 0, "actual_fees": "unknown",
        "mock_only": mock, "reference_sha256": ref_sha, "human_listening": "not_tested"}
    _write_json(root / "result.json", record)
    started = time.monotonic()
    adapter = None
    try:
        key = key_loader()
        if not isinstance(key, str) or not key:
            raise ValueError("key_unavailable")
        current = authority.check(jobs=1, requests=1, characters=28)
        # Reuse original owner/day atomic pre-reservation; no refunds on unknown.
        authority.reserve(28)
        record.update(reserved=True, status="reserved_not_dispatched")
        _write_json(root / "result.json", record)
        authority.check()
        if manifest != build():
            raise ValueError("frozen_candidate_drift")
        adapter = ElevenLabsAdapter(key, transport=transport)
        record.update(status="attempted_result_unknown", dispatched_http=1, dispatched_characters=28)
        _write_json(root / "result.json", record)
        async with asyncio.timeout(30):
            audio = await adapter.synthesize_http(manifest["request"]["text"], current["voice_id"], "ja")
        if time.monotonic() - started >= 30:
            raise TimeoutError
        audio_cache.validate_mp3(audio)
        authority.check()
        if manifest != build():
            raise ValueError("frozen_candidate_drift")
        with (root / "restrained.mp3").open("xb") as stream:
            stream.write(audio)
            stream.flush()
            os.fsync(stream.fileno())
        record.update(status="completed", audio_bytes=len(audio),
            audio_sha256=hashlib.sha256(audio).hexdigest())
    except BaseException as exc:
        known_rejection = isinstance(exc, ProviderError) and not exc.unknown
        record.update(status="failed" if known_rejection or not record["dispatched_http"] else "unknown",
            error="cancelled" if isinstance(exc, asyncio.CancelledError) else
                "provider_rejected" if known_rejection else "local_preflight_or_result_unconfirmed",
            phase=adapter.synthesis_phase if adapter else "before_dispatch")
    record["elapsed_seconds"] = round(time.monotonic() - started, 4)
    _write_json(root / "result.json", record)
    return record


def review_app(reference: Path, output: Path, *, synthetic=False) -> FastAPI:
    """Read-only review. Pending candidate is not playable; no generation routes."""
    app, token = FastAPI(), secrets.token_urlsafe(32)
    ref_sha = hashlib.sha256(reference.read_bytes()).hexdigest() if synthetic else REFERENCE_SHA
    reference_audio(reference, ref_sha)
    _check_result_dir(output)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if request.url.path != "/" and not secrets.compare_digest(
                request.cookies.get("tone_review", ""), token):
            return JSONResponse({"error": "review_session_required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def page():
        html = (ROOT / "frontend/voice-restrained-listen.html").read_text(encoding="utf-8")
        record = json.loads((output / "result.json").read_text(encoding="utf-8")) if (output / "result.json").exists() else {}
        ready = record.get("status") == "completed" and record.get("run_id") == RUN_ID and record.get("mock_only") == synthetic
        html = html.replace("{{candidate_audio}}", '<audio id="restrained" controls preload="none" src="/audio/restrained"></audio>'
            if ready else '<p role="status">候选尚未生成，需另行批准；当前不能试听。</p>')
        html = html.replace("{{kind}}", "本地合成数据验证，不代表真实候选音色。" if synthetic else
            "原缓存是历史参照，并非同批严格A/B；每版一次生成不能证明标签的稳定因果效果。")
        response = HTMLResponse(html)
        response.set_cookie("tone_review", token, httponly=True, samesite="strict")
        return response

    @app.get("/theme/{name}")
    async def theme(name: str):
        if name not in ("tokens.css", "silver.css"):
            return Response(status_code=404)
        return Response((ROOT / "frontend/src/ui" / name).read_text(encoding="utf-8"), media_type="text/css")

    @app.get("/audio/{variant}")
    async def audio(variant: str):
        if variant == "reference":
            return Response(reference_audio(reference, ref_sha), media_type="audio/mpeg")
        if variant != "restrained" or not (output / "result.json").is_file():
            return Response(status_code=404)
        record = json.loads((output / "result.json").read_text(encoding="utf-8"))
        if (record.get("status") != "completed" or record.get("run_id") != RUN_ID or
                record.get("mock_only") != synthetic):
            return Response(status_code=404)
        return Response(reference_audio(output / "restrained.mp3", record["audio_sha256"]), media_type="audio/mpeg")
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = build()
    if args.output:
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": RUN_ID, "manifest_sha256": fingerprint(manifest),
        "code_sha256": manifest["code_sha256"], "code_files": len(FILES),
        "http_tts": 1, "characters": 28, "real_requests": 0, "receipt_created": False}))


if __name__ == "__main__":
    main()
