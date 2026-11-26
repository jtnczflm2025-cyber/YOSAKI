"""Prepare or run the single saved-message product WS acceptance.

`plan` and `dry-run` have no provider connection. `issue` is intentionally not
invoked without a separate, exact user approval. `serve` refuses to start
without that receipt and derives a minimal isolated database from read-only
daily records; the route alone occupies the receipt before provider dispatch.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import socket
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

import uvicorn

from backend.app.voice import config, manager, realtime_receipt, storage
from scripts.voice_provider_probe import network_proxy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "yosaki.sqlite3"
RUN_ID = "voice-v4-product-20261111-r1"
RUN_ROOT = Path(os.environ["LOCALAPPDATA"]) / "YOSAKI" / RUN_ID
MANIFEST = RUN_ROOT / "manifest.json"
RECEIPTS = RUN_ROOT / "receipts"
RUNTIME_DB = RUN_ROOT / "data" / "yosaki.sqlite3"
BACKEND_PORT = 18772
FRONTEND_PORT = 15176
ORIGIN = f"http://127.0.0.1:{FRONTEND_PORT}"
MESSAGE_ID = 10


@contextmanager
def source_db():
    with closing(sqlite3.connect(SOURCE.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        actual = db.execute("PRAGMA database_list").fetchone()[2]
        if Path(actual).resolve() != SOURCE.resolve():
            raise ValueError("source_database_path_changed")
        yield db


def selected_manifest() -> dict:
    # Planning bypasses availability checks only in this independent process;
    # it never reads the real Key, changes the service, or calls the provider.
    with patch.object(config, "enabled", return_value=True), \
            patch.object(config, "configured", return_value=True):
        context = manager.speech_context(source_db, MESSAGE_ID, 1)
    with source_db() as db:
        row = db.execute("""SELECT m.role,m.generation_status,m.conversation_id,
            c.character_id FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.id=?""", (MESSAGE_ID,)).fetchone()
        profile = storage.profile(db, 1)
        consent = storage.consent(db, 1)
        owner = db.execute("SELECT id FROM users").fetchall()
    if not row or row["role"] != "assistant" or row["generation_status"] != "completed" or \
            row["character_id"] != 1 or len(owner) != 1 or owner[0][0] != 1 or \
            not profile or not profile["active"] or profile["provider"] != "elevenlabs" or \
            profile["configuration"]["model_realtime"] != "eleven_v4_turbo" or \
            not consent or consent["notice_version"] != config.NOTICE_VERSION or \
            context["language"] != "ja" or len(context["segments"]) != 1 or \
            not 1 <= len(context["segments"][0]) <= 1000:
        raise ValueError("selected_message_no_longer_eligible")
    text = context["segments"][0]
    return {
        "schema_version": 1, "status": "awaiting_single_real_approval",
        "run_id": RUN_ID, "owner_id": 1, "character_id": 1,
        "conversation_id": row["conversation_id"], "message_id": MESSAGE_ID,
        "message_version_sha256": context["digest"],
        "request_text": text,
        "request_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "characters": len(text), "profile_revision": profile["revision"],
        "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
        "notice_version": consent["notice_version"],
        "provider": "elevenlabs", "model_id": "eleven_v4_turbo",
        "output_format": "pcm_24000", "pcm_sample_rate_hz": 24000,
        "pcm_encoding": "s16le", "pcm_channels_expected": 1,
        "language_code": "ja", "voice_settings": "omitted",
        "source_database_path": str(SOURCE.resolve()),
        "runtime_database_path": str(RUNTIME_DB.resolve()),
        "network_proxy_url": network_proxy(),
        "limits": {**realtime_receipt.EXPECTED_LIMITS,
                   "tts_characters": len(text)},
        "failure_policy": "stop_on_auth_quota_protocol_unknown_timeout_or_disconnect; never_replay_dispatched_text",
        "billing": "unknown_until_provider_account_observation",
    }


def load_frozen() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest != selected_manifest():
        raise ValueError("frozen_manifest_or_source_changed")
    return manifest


def plan() -> dict:
    manifest = selected_manifest()
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    RECEIPTS.mkdir(exist_ok=True)
    with MANIFEST.open("x", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return summary(manifest)


def summary(manifest: dict) -> dict:
    receipt_path = RECEIPTS / f"{RUN_ID}.approval.json"
    return {"run_id": RUN_ID, "manifest_sha256": realtime_receipt.digest(manifest),
            "code_set_sha256": realtime_receipt.code_set_sha256(),
            "request_text_sha256": manifest["request_text_sha256"],
            "characters": manifest["characters"],
            "receipt_exists": receipt_path.exists(),
            "receipt_used": receipt_path.with_name(receipt_path.name + ".used").exists(),
            "supplier_requests": 0}


def issue(manifest: dict, args) -> dict:
    manifest_hash = realtime_receipt.digest(manifest)
    code_hash = realtime_receipt.code_set_sha256()
    if (args.approved_run_id != RUN_ID or args.approved_manifest_sha256 != manifest_hash or
            args.approved_code_set_sha256 != code_hash or
            not args.confirm_processing or not args.ack_billing):
        raise ValueError("exact_user_approval_required")
    if RUNTIME_DB.exists():
        raise ValueError("runtime_database_already_exists")
    approval = realtime_receipt.expected_receipt(manifest, manifest_hash, code_hash)
    path = RECEIPTS / f"{RUN_ID}.approval.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(approval, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    return summary(manifest)


def _make_minimal_database(manifest: dict) -> Path:
    if "backend.app.main" in sys.modules:
        raise ValueError("main_imported_before_runtime_path")
    if RUNTIME_DB.exists() or RUNTIME_DB.parent.exists():
        raise ValueError("runtime_database_already_exists")
    RUNTIME_DB.parent.mkdir(parents=True, exist_ok=False)
    os.environ.update({
        "YOSAKI_DATA_DIR": str(RUNTIME_DB.parent),
        "YOSAKI_VOICE_ENABLED": "1",
        "YOSAKI_V4_OUTPUT_ENABLED": "1",
        "YOSAKI_V4_REAL_PROVIDER_ENABLED": "1",
        "YOSAKI_V4_OUTPUT_MAX_ATTEMPTS_PER_OWNER": "1",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_ALLOWED_ORIGINS": ORIGIN,
        "YOSAKI_V4_PRODUCT_MANIFEST_PATH": str(MANIFEST),
        "YOSAKI_V4_RECEIPT_DIR": str(RECEIPTS),
        "YOSAKI_V4_TTD_PROXY": manifest["network_proxy_url"] or "",
    })
    from backend.app import main as product  # only after data path is fixed
    from fastapi.testclient import TestClient

    temporary_password = secrets.token_urlsafe(24)
    with TestClient(product.app) as client:
        result = client.post("/api/auth/setup", json={
            "username": "v4_isolated_owner", "password": temporary_password})
        if result.status_code != 201:
            raise ValueError("isolated_owner_setup_failed")
    with source_db() as source:
        message = source.execute("SELECT content FROM messages WHERE id=?", (MESSAGE_ID,)).fetchone()
        profile = source.execute("SELECT * FROM voice_profiles WHERE character_id=1").fetchone()
        consent = source.execute("SELECT * FROM voice_privacy_consents WHERE owner_id=1").fetchone()
    if not message or not profile or not consent:
        raise ValueError("source_records_missing")
    with product.connect() as db:
        db.execute("INSERT INTO conversations(id,character_id,title,created_at,updated_at) "
                   "VALUES (?,1,'V4 受控验证','now','now')", (manifest["conversation_id"],))
        db.execute("INSERT INTO messages(id,conversation_id,role,content,generation_status,created_at) "
                   "VALUES (?,?,'assistant',?,'completed','now')",
                   (MESSAGE_ID, manifest["conversation_id"], message["content"]))
        db.execute("""INSERT INTO voice_profiles(character_id,provider,voice_id,active,revision,
            configuration_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            tuple(profile[key] for key in ("character_id", "provider", "voice_id",
                                      "active", "revision", "configuration_json",
                                      "created_at", "updated_at")))
        db.execute("""INSERT INTO voice_privacy_consents(owner_id,provider,notice_version,accepted_at)
            VALUES (?,?,?,?)""", tuple(consent[key] for key in
            ("owner_id", "provider", "notice_version", "accepted_at")))
    credential = RUN_ROOT / "isolated-login.txt"
    with credential.open("x", encoding="utf-8") as output:
        output.write("v4_isolated_owner\n" + temporary_password + "\n")
        output.flush()
        os.fsync(output.fileno())
    return credential


async def _serve_bounded(app) -> None:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=BACKEND_PORT,
                                           log_level="warning", access_log=False))
    task = asyncio.create_task(server.serve())
    # Reserve five seconds inside the 180-second local process budget for
    # graceful shutdown after the operator window expires.
    done, _ = await asyncio.wait({task}, timeout=175)
    if not done:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
    else:
        await task


def serve(manifest: dict) -> dict:
    receipt_path = RECEIPTS / f"{RUN_ID}.approval.json"
    if not receipt_path.is_file() or receipt_path.with_name(receipt_path.name + ".used").exists():
        raise ValueError("unused_receipt_required")
    if json.loads(receipt_path.read_text(encoding="utf-8")) != \
            realtime_receipt.expected_receipt(manifest, realtime_receipt.digest(manifest),
                                              realtime_receipt.code_set_sha256()):
        raise ValueError("receipt_or_code_mismatch")
    if not os.getenv("ELEVENLABS_API_KEY", "").strip():
        raise ValueError("api_key_not_configured")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", BACKEND_PORT))
        except OSError as exc:
            raise ValueError("isolated_backend_port_busy") from exc
    credential = _make_minimal_database(manifest)
    try:
        from backend.app import main as product
        asyncio.run(_serve_bounded(product.app))
    finally:
        credential.unlink(missing_ok=True)
    return {"run_id": RUN_ID, "service_ended": True,
            "receipt_used": receipt_path.with_name(receipt_path.name + ".used").exists()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Saved-message product V4; dry-run by default")
    parser.add_argument("action", nargs="?", choices=("dry-run", "plan", "issue", "serve"),
                        default="dry-run")
    parser.add_argument("--approved-run-id")
    parser.add_argument("--approved-manifest-sha256")
    parser.add_argument("--approved-code-set-sha256")
    parser.add_argument("--confirm-processing", action="store_true")
    parser.add_argument("--ack-billing", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            value = plan()
        else:
            manifest = load_frozen()
            if args.action == "issue":
                value = issue(manifest, args)
            elif args.action == "serve":
                value = serve(manifest)
            else:
                value = summary(manifest)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        # Controlled local error code only; no request text or Key in output.
        print(json.dumps({"status": "stopped", "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
