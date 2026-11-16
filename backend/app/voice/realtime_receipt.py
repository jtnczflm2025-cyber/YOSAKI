"""Persistent, one-use approval for the saved-message product WS path.

The same exclusive-create used marker as the standalone probes is retained
after every outcome, including a crash before provider dispatch. This module
never issues an approval and never opens a provider connection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, manager, storage


ROOT = Path(__file__).resolve().parents[3]
CODE_PATHS = (
    "backend/app/main.py",
    "backend/app/voice/config.py",
    "backend/app/voice/manager.py",
    "backend/app/voice/storage.py",
    "backend/app/voice/schemas.py",
    "backend/app/voice/expression_product.py",
    "backend/app/voice/speech_planner.py",
    "backend/app/voice/readable_text.py",
    "backend/app/voice/realtime_output.py",
    "backend/app/voice/realtime_ttd_connector.py",
    "backend/app/voice/realtime_product.py",
    "backend/app/voice/realtime_receipt.py",
    "frontend/src/App.vue",
    "frontend/src/voice/MessageRealtimeOutput.vue",
    "frontend/src/voice/realtimeOutput.ts",
    "frontend/vite.config.ts",
    "scripts/voice_provider_probe.py",
    "scripts/voice_v4_product_runtime.py",
)
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,79}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_LIMITS = {
    "provider_http_requests": 0,
    "provider_ws_connections": 1,
    "provider_text_dispatches": 1,
    "automatic_retries": 0,
    "connection_seconds": 45,
    "dispatch_wall_seconds": 50,
    "max_audio_bytes": 8 * 1024 * 1024,
}


class ApprovalError(ValueError):
    """A local scope or authorization mismatch; never dispatch."""


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def code_set_sha256(root: Path = ROOT) -> str:
    lines = [f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
             for name in CODE_PATHS]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ApprovalError("approval_file_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalError("approval_file_invalid") from exc
    if not isinstance(value, dict):
        raise ApprovalError("approval_file_invalid")
    return value


@contextmanager
def _read_only(path: Path):
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        try:
            actual = db.execute("PRAGMA database_list").fetchone()[2]
            if Path(actual).resolve() != path:
                raise ApprovalError("source_database_path_changed")
            yield db
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise ApprovalError("source_database_unavailable") from exc


def _source_matches(path: Path, expected: dict) -> bool:
    def connect():
        return _read_only(path)
    with connect() as db:
        row = db.execute("""SELECT m.role,m.generation_status,m.conversation_id,
            c.character_id FROM messages m JOIN conversations c ON c.id=m.conversation_id
            WHERE m.id=?""", (expected["message_id"],)).fetchone()
        profile = storage.profile(db, expected["character_id"])
        consent = storage.consent(db, expected["owner_id"])
    if not row or row["role"] != "assistant" or row["generation_status"] != "completed" or \
            row["conversation_id"] != expected["conversation_id"] or \
            row["character_id"] != expected["character_id"] or \
            not profile or not profile["active"] or profile["revision"] != expected["profile_revision"] or \
            hashlib.sha256(profile["voice_id"].encode()).hexdigest() != expected["voice_id_sha256"] or \
            not consent or consent["notice_version"] != expected["notice_version"]:
        return False
    context = manager.speech_context(connect, expected["message_id"], expected["owner_id"])
    return (context["digest"] == expected["message_version_sha256"] and
            context["language"] == expected["language_code"] and
            context["segments"] == [expected["request_text"]])


@dataclass(frozen=True)
class PreparedApproval:
    manifest: dict
    manifest_sha256: str
    code_set_sha256: str
    manifest_path: Path
    receipt_path: Path
    used_path: Path
    observation_path: Path


def expected_receipt(manifest: dict, manifest_hash: str, code_hash: str) -> dict:
    return {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest_hash,
        "code_set_sha256": code_hash,
        "owner_id": manifest["owner_id"],
        "character_id": manifest["character_id"],
        "conversation_id": manifest["conversation_id"],
        "message_id": manifest["message_id"],
        "message_version_sha256": manifest["message_version_sha256"],
        "request_text_sha256": manifest["request_text_sha256"],
        "profile_revision": manifest["profile_revision"],
        "voice_id_sha256": manifest["voice_id_sha256"],
        "notice_version": manifest["notice_version"],
        "network_proxy_url": manifest["network_proxy_url"],
        "limits": manifest["limits"],
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
    }


def prepare(connect, bound, manifest_path: Path | None = None,
            receipt_root: Path | None = None) -> PreparedApproval:
    manifest_path = manifest_path or Path(os.getenv("YOSAKI_V4_PRODUCT_MANIFEST_PATH", ""))
    receipt_root = receipt_root or Path(os.getenv("YOSAKI_V4_RECEIPT_DIR", ""))
    if not str(manifest_path) or not str(receipt_root) or \
            str(manifest_path) == "." or str(receipt_root) == ".":
        raise ApprovalError("approval_not_configured")
    manifest = _read_json(manifest_path)
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ApprovalError("run_id_invalid")
    if receipt_root.is_symlink() or not receipt_root.is_dir() or \
            receipt_root.resolve() != receipt_root.absolute():
        raise ApprovalError("receipt_directory_invalid")
    receipt_path = receipt_root / f"{run_id}.approval.json"
    used_path = receipt_root / f"{run_id}.approval.json.used"
    observation_path = receipt_root / f"{run_id}.observation.json"
    if used_path.exists():
        raise ApprovalError("approval_already_used")
    with connect() as db:
        actual = db.execute("PRAGMA database_list").fetchone()[2]
    expected = {
        "schema_version": 1, "status": "awaiting_single_real_approval",
        "run_id": run_id, "owner_id": bound.owner_id,
        "character_id": bound.character_id,
        "conversation_id": bound.conversation_id,
        "message_id": bound.message_id,
        "message_version_sha256": bound.digest,
        "request_text": bound.text,
        "request_text_sha256": hashlib.sha256(bound.text.encode("utf-8")).hexdigest(),
        "characters": len(bound.text),
        "profile_revision": bound.profile_revision,
        "voice_id_sha256": hashlib.sha256(bound.voice_id.encode()).hexdigest(),
        "notice_version": config.NOTICE_VERSION,
        "provider": "elevenlabs", "model_id": "eleven_v4_turbo",
        "output_format": "pcm_24000", "pcm_sample_rate_hz": 24000,
        "pcm_encoding": "s16le", "pcm_channels_expected": 1,
        "language_code": "ja", "voice_settings": "omitted",
        "source_database_path": manifest.get("source_database_path"),
        "runtime_database_path": str(Path(actual).resolve()),
        "network_proxy_url": os.getenv("YOSAKI_V4_TTD_PROXY") or None,
        "limits": {**EXPECTED_LIMITS, "tts_characters": len(bound.text)},
        "failure_policy": "stop_on_auth_quota_protocol_unknown_timeout_or_disconnect; never_replay_dispatched_text",
        "billing": "unknown_until_provider_account_observation",
    }
    if manifest != expected:
        raise ApprovalError("manifest_or_binding_changed")
    source = Path(manifest["source_database_path"]).resolve()
    if not source.is_file() or not _source_matches(source, expected):
        raise ApprovalError("source_binding_changed")
    manifest_hash = digest(manifest)
    code_hash = code_set_sha256()
    if _read_json(receipt_path) != expected_receipt(manifest, manifest_hash, code_hash):
        raise ApprovalError("approval_mismatch")
    return PreparedApproval(manifest, manifest_hash, code_hash, manifest_path,
                            receipt_path, used_path, observation_path)


def claim(prepared: PreparedApproval) -> None:
    # Re-read before the exclusive marker. After O_EXCL succeeds, never remove
    # it, including on pre-dispatch failure or crash.
    if digest(_read_json(prepared.manifest_path)) != prepared.manifest_sha256 or \
            code_set_sha256() != prepared.code_set_sha256 or \
            _read_json(prepared.receipt_path) != expected_receipt(
                prepared.manifest, prepared.manifest_sha256, prepared.code_set_sha256):
        raise ApprovalError("approval_changed")
    try:
        fd = os.open(prepared.used_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ApprovalError("approval_already_used") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"run_id": prepared.manifest["run_id"],
                   "manifest_sha256": prepared.manifest_sha256,
                   "status": "occupied_outcome_unknown"}, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())


def still_matches(prepared: PreparedApproval) -> bool:
    """Fail closed if the frozen source or code changes after occupation."""
    try:
        return (digest(_read_json(prepared.manifest_path)) == prepared.manifest_sha256 and
                code_set_sha256() == prepared.code_set_sha256 and
                _source_matches(Path(prepared.manifest["source_database_path"]).resolve(),
                                prepared.manifest))
    except Exception:
        # The owner/consent/planner recheck may raise an HTTPException. Any
        # such failure must stop a claimed run before provider dispatch.
        return False


def observe(prepared: PreparedApproval, *, provider_status: str,
            text_dispatched: bool, audio_bytes: int,
            browser_playback_ended: bool) -> None:
    # The used marker remains authoritative. A missing observation after a
    # process crash means unknown, never an unused receipt.
    record: dict[str, Any] = {
        "run_id": prepared.manifest["run_id"],
        "manifest_sha256": prepared.manifest_sha256,
        "provider_status": provider_status,
        "text_dispatch_attempted": text_dispatched,
        "audio_bytes_observed": audio_bytes,
        "browser_playback_ended_reported": browser_playback_ended,
        "request_outcome": "completed" if provider_status == "completed" else
                           "unknown" if text_dispatched else "not_dispatched",
        "actual_billing": "unknown",
    }
    try:
        with prepared.observation_path.open("x", encoding="utf-8") as output:
            json.dump(record, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        pass
