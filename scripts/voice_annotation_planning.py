"""One frozen, model-only expression assessment. Default: read-only verify.

No main import, approval issuer, OAuth refresh, directory query or TTS branch.
Preparing a manifest reads only the selected public rows, never credentials.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import httpx

from backend.app import chatgpt_plan
from backend.app.voice import config, storage
from backend.app.voice.annotation_model import PlanningError, ProfilePlanningAdapter
from backend.app.voice.annotation_planner import AnnotationError, Source, fingerprint, prepare, validate, render
from backend.app.voice.expression_fixture import _check_result_dir, _write_json
from backend.app.voice.realtime_receipt import occupy_once
from scripts.voice_annotation_candidate import FILES as EXISTING_FILES
from scripts.voice_v3_fixture_probe import local_proxy

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/yosaki.sqlite3"
RUN_ID = "voice-expression-ai-plan-20261118-r1"
MANIFEST = ROOT / "docs/VOICE_EXPRESSION_AI_PLANNING_CANDIDATE.json"
PROFILE_FIELDS = ("id", "name", "provider_name", "base_url", "model_name", "protocol",
                  "temperature", "top_p", "max_tokens", "context_limit", "supports_streaming",
                  "enabled", "reasoning_effort", "updated_at")
# Explicit evaluation-only snapshot of the user's stated preference, NOT a
# claim that the currently empty saved Profile personality contains this text.
PERSONALITY = "中性、慵懒、清冷、疏离；偏低沉、成熟稳重，语气随意；避免开朗活泼和过度表演，允许留白和不加标签。"
FILES = sorted(set(EXISTING_FILES) | {"scripts/voice_annotation_planning.py",
                                    "tests/test_voice_annotation_planning.py"})


def read_source(database: Path):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if not db.execute("SELECT 1 FROM users WHERE id=1").fetchone():
            raise ValueError("owner_unavailable")
        character = db.execute("SELECT enabled FROM characters WHERE id=1").fetchone()
        archive_field = "archive_only" if any(r["name"] == "archive_only" for r in
            db.execute("PRAGMA table_info(conversations)")) else "0"
        conversation = db.execute("SELECT character_id,model_profile_id," + archive_field +
                                  " AS archived FROM conversations WHERE id=5").fetchone()
        message = db.execute("""SELECT conversation_id,role,content,generation_status,
            CASE WHEN tool_calls_json IS NOT NULL AND tool_calls_json!='' THEN 1 ELSE 0 END AS has_tools,
            CASE WHEN tool_batch_status IS NOT NULL AND tool_batch_status!='' THEN 1 ELSE 0 END AS has_batch
            FROM messages WHERE id=10""").fetchone()
        profile = db.execute("SELECT " + ",".join(PROFILE_FIELDS) + " FROM model_profiles WHERE id=5").fetchone()
        voice, consent = storage.profile(db, 1), storage.consent(db, 1)
        if (not character or not character[0] or not conversation or
                tuple(conversation) != (1, 5, 0) or not message or message["conversation_id"] != 5 or
                message["role"] != "assistant" or message["generation_status"] != "completed" or
                message["has_tools"] or message["has_batch"]):
            raise ValueError("saved_source_or_explicit_model_selection_changed")
        if not profile or not profile["enabled"] or profile["protocol"] != "chatgpt_plan":
            raise ValueError("selected_planning_profile_unavailable")
        if not voice or not voice["active"] or not consent or consent["notice_version"] != config.NOTICE_VERSION:
            raise ValueError("voice_authority_unavailable")
        profile = dict(profile)
        async def no_token(_):
            raise RuntimeError("preparation_cannot_read_credentials")
        adapter = ProfilePlanningAdapter(5, lambda _: profile, no_token, enabled=True)
        source = Source(1, 1, 5, 10, message["content"], profile_revision=voice["revision"],
            voice_sha256=hashlib.sha256(voice["voice_id"].encode()).hexdigest(),
            consent_version=consent["notice_version"], personality=PERSONALITY)
        prepared = prepare(source, "auto", model_binding=adapter.binding())
        data = {**prepared["model_input"], "input_sha256": prepared["input_sha256"], "granularity": "auto"}
        instruction = data.pop("instruction")
        request = {"model": profile["model_name"], "model_profile_id": 5,
            "model_binding": adapter.binding(), "max_output_tokens": 512,
            "total_timeout_seconds": 20, "retries": 0,
            "messages": [{"role": "system", "content": instruction},
                         {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]}
        endpoint, body, _ = adapter.request(request)
        saved_voice = {k: voice["configuration"][k] for k in
            ("language", "personality", "expression_mode", "model_standard", "audio_format")}
        return profile, prepared, request, endpoint, body, saved_voice


def build(database=DATABASE):
    profile, prepared, request, endpoint, body, saved_voice = read_source(database)
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
    value = {"run_id": RUN_ID, "status": "awaiting_separate_approval", "kind": "model_only_planning",
        "identity": {"owner_id": 1, "character_id": 1, "conversation_id": 5, "message_id": 10,
                     "model_profile_id": 5}, "selected_profile_public": profile,
        "selection_basis": "saved_conversation_5_explicit_model_profile_5_no_default_fallback",
        "source": prepared["source"], "prepared": prepared,
        "saved_voice_configuration": saved_voice,
        "evaluation_voice_personality_origin": "user_stated_preference_snapshot_not_profile_write",
        "request": request, "endpoint": endpoint, "wire_body": body,
        "wire_body_sha256": fingerprint(body), "input_sha256": prepared["input_sha256"],
        "budget": {"model_generations": 1, "max_output_tokens": 512, "total_seconds": 20,
                   "retries": 0, "http_tts": 0, "tts_ws": 0, "scribe_ws": 0,
                   "oauth": 0, "model_directory_get": 0},
        "code_files": files, "code_sha256": fingerprint(files),
        "excluded": ["chat_history", "memory", "summary", "tools", "psychology_read",
                     "internal_reasoning", "tts_dispatch", "daily_profile_writes"],
        "expected_annotation_answer": None, "actual_billing": "unknown"}
    # Frozen JSON is the dispatch object; tuples from the pure Source DTO must
    # round-trip exactly when the next process reads the candidate file.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def approval_for(manifest, session_hash, proxy_url, run_directory, *, mock=False, database=DATABASE):
    return {"run_id": RUN_ID, "manifest_sha256": fingerprint(manifest),
        "code_sha256": manifest["code_sha256"], "input_sha256": manifest["input_sha256"],
        "wire_body_sha256": manifest["wire_body_sha256"], "identity": manifest["identity"],
        "source_database": str(database.resolve()),
        "session_sha256": session_hash, "proxy_url": proxy_url,
        "run_directory": str(run_directory.resolve()), "budget": manifest["budget"],
        "provider_kind": "mock_only" if mock else "real_approved",
        "processing_and_billing_confirmed": True}


def run_directory():
    return Path(os.environ["LOCALAPPDATA"]) / "YOSAKI/voice-runs" / RUN_ID


async def execute(manifest, receipt: Path, session_hash: str, token_loader, *, enabled=False,
                  database=DATABASE, transport=None, proxy_url=None, mock_directory=None):
    if not enabled:
        raise ValueError("planning_gate_disabled")
    mock = isinstance(transport, (httpx.MockTransport, httpx.ASGITransport))
    if mock != (mock_directory is not None):
        raise ValueError("transport_authority_mismatch")
    directory = Path(mock_directory).resolve() if mock else run_directory().resolve()
    if not mock and database.resolve() != DATABASE.resolve():
        raise ValueError("source_database_path_mismatch")
    _check_result_dir(directory / "result", database)
    if directory.is_relative_to(ROOT) or receipt != directory / "approval.json" or receipt.is_symlink():
        raise ValueError("fixed_receipt_path_invalid")
    if manifest != build(database):
        raise ValueError("frozen_input_or_code_drift")
    if not receipt.is_file() or receipt.stat().st_size > 16384 or \
            json.loads(receipt.read_text(encoding="utf-8")) != approval_for(
                manifest, session_hash, proxy_url, directory, mock=mock, database=database):
        raise ValueError("approval_binding_mismatch")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT user_id,expires_at FROM auth_sessions WHERE token_hash=?", (session_hash,)).fetchone()
        if not row or row[0] != 1 or datetime.fromisoformat(row[1]) <= datetime.now(timezone.utc):
            raise ValueError("owner_session_unavailable")
    output = directory / "result"
    if output.exists():
        raise ValueError("existing_result_not_reexecuted")
    occupy_once(directory / "approval.json.used", {"run_id": RUN_ID,
        "manifest_sha256": fingerprint(manifest), "status": "occupied_outcome_unknown"})
    output.mkdir()
    result = {"run_id": RUN_ID, "status": "occupied_outcome_unknown", "usage": None,
              "model_dispatch_attempts": 0, "attempt_counter_kind": "reserved_possible_dispatch_not_provider_ack",
              "actual_billing": "unknown", "tts_requests": 0}
    _write_json(output / "result.json", result)

    def resolve(selected):
        if selected != 5 or build(database) != manifest:
            raise ValueError("planning_binding_changed")
        return read_source(database)[0]

    # Only the approved credential callback runs here; no refresh callback exists.
    async def token(selected):
        value = await token_loader(selected)
        if build(database) != manifest:
            raise ValueError("planning_binding_changed")
        # Persist immediately before the adapter's single HTTP attempt. A crash
        # at this point is possibly dispatched, never capacity for a retry.
        result["model_dispatch_attempts"] = 1
        result["status"] = "attempted_outcome_unknown"
        _write_json(output / "result.json", result)
        return value

    adapter = ProfilePlanningAdapter(5, resolve, token, enabled=True,
                                    transport=transport, proxy_url=proxy_url)
    try:
        raw, usage = await adapter.generate(manifest["request"])
        result["usage"] = usage
        if build(database) != manifest:
            raise ValueError("late_result_binding_changed")
        annotations = validate(manifest["prepared"], raw)
        preview = render(manifest["prepared"], annotations, 1)
        fragments = {f["fragment_id"]: f for f in manifest["prepared"]["fragments"]}
        result.update(status="ready" if annotations else "empty", annotations=annotations,
            suggestions=[{**a, "source_text": preview["safe_text"][fragments[a["fragment_id"]]["start"]:
                                          fragments[a["fragment_id"]]["end"]]} for a in annotations],
            preview=preview, preview_only_no_tts=True)
    except PlanningError as exc:
        result.update(status=exc.outcome, safe_reason=exc.code, usage=exc.usage or result["usage"])
    except AnnotationError as exc:
        result.update(status="invalid", safe_reason=exc.code)
    except BaseException as exc:
        result.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "invalid_or_unknown",
                      safe_reason="cancelled" if isinstance(exc, asyncio.CancelledError) else "local_validation_or_binding_failure")
    _write_json(output / "result.json", result)
    return result


async def existing_plan_token(selected):
    """Only used AFTER separate approval; current preparation never calls this."""
    if selected != 5:
        raise ValueError("selected_profile_changed")
    from cryptography.fernet import Fernet
    chatgpt_plan._data_dir = DATABASE.parent
    chatgpt_plan._fernet = lambda: Fernet((DATABASE.parent / ".secret_key").read_bytes())
    # No automatic OAuth, model list or fallback provider.
    try:
        return await chatgpt_plan.access_token(allow_refresh=False)
    except Exception:
        raise PlanningError("planning_credential_unavailable_no_refresh", outcome="not_dispatched") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--session-hash-file", type=Path)
    args = parser.parse_args()
    if args.prepare and args.execute_real:
        raise ValueError("mutually_exclusive_modes")
    if args.prepare:
        candidate = build()
        if MANIFEST.exists():
            raise ValueError("candidate_already_exists_do_not_overwrite")
        MANIFEST.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        candidate = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if candidate != build():
            raise ValueError("candidate_drift")
    if not args.execute_real:
        print(json.dumps({"run_id": RUN_ID, "manifest_sha256": fingerprint(candidate),
            "code_sha256": candidate["code_sha256"], "input_sha256": candidate["input_sha256"],
            "model": candidate["request"]["model"], "cap": 512, "total_seconds": 20,
            "real_requests": 0, "credential_reads": 0, "receipt_created": False}))
        return
    session_file = args.session_hash_file
    if session_file is None or session_file.is_symlink() or session_file.stat().st_size > 256:
        raise ValueError("session_binding_file_invalid")
    session = session_file.read_text().strip()
    if len(session) != 64 or any(c not in "0123456789abcdef" for c in session):
        raise ValueError("session_binding_file_invalid")
    result = asyncio.run(execute(candidate, run_directory() / "approval.json", session,
                                existing_plan_token, enabled=True, proxy_url=local_proxy()))
    print(json.dumps({k: result[k] for k in ("status", "model_dispatch_attempts", "usage", "tts_requests", "actual_billing")}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No arbitrary provider/credential exception text or partial payload.
        print(json.dumps({"status": "stopped", "safe_reason": "local_preflight_or_gate_failure"}))
        raise SystemExit(2)
