"""One-use whole-turn approval for an isolated dynamic model and speech run.

This module cannot issue a real approval. Only a later, explicitly authorized
operator action may place a real_approved record in the isolated receipt root.
Fake test receipts are intentionally rejected by the real connector path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .realtime_dynamic_context import digest


ROOT = Path(__file__).resolve().parents[3]
CODE_PATHS = (
    "backend/app/main.py",
    "backend/app/chatgpt_plan.py",
    "backend/app/responses_adapter.py",
    "backend/app/voice/realtime_dynamic_context.py",
    "backend/app/voice/realtime_dynamic_candidate_receipt.py",
    "backend/app/voice/realtime_dynamic_candidate.py",
    "backend/app/voice/realtime_dynamic_diagnostics.py",
    "backend/app/voice/realtime_answer_display.py",
    "backend/app/voice/realtime_chat_bridge.py",
    "backend/app/voice/realtime_output.py",
    "backend/app/voice/realtime_ttd_connector.py",
    "backend/app/voice/readable_text.py",
    "frontend/src/voice/realtimeOutput.ts",
    "frontend/src/voice/DynamicChatOutput.vue",
    "frontend/src/App.vue",
    "scripts/voice_v4_dynamic_candidate_isolated.py",
    "scripts/voice_v4_dynamic_candidate_approve.py",
)
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,79}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LIMITS = {
    "model_generations": 1, "max_output_tokens": 128,
    "model_seconds": 30, "tts_characters": 80, "tts_segments": 2,
    "tts_connections": 2, "tts_audio_bytes": 1_048_576,
    "tts_connection_seconds": 30, "tts_receive_seconds": 45,
    "browser_playback_seconds": 30, "overall_seconds": 110,
}


class ReceiptError(ValueError):
    pass


def code_digest(root: Path = ROOT) -> str:
    lines = [f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
             for name in CODE_PATHS]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def validate_manifest(value: dict) -> None:
    exact = {"schema_version", "mode", "run_id", "generation_id",
        "owner_id", "character_id", "conversation_id", "model_profile_id",
        "context_sha256", "prompt", "profile_revision", "voice_id_sha256",
        "notice_version", "chat_provider", "chat_model_id", "tts_model_id",
        "output_format", "language_code", "network_proxy_url", "limits"}
    if not isinstance(value, dict) or set(value) != exact or \
            value.get("schema_version") != 1 or value.get("mode") != "real_dynamic_candidate" or \
            not isinstance(value.get("run_id"), str) or not RUN_ID.fullmatch(value["run_id"]) or \
            not isinstance(value.get("generation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["generation_id"]) or \
            any(type(value.get(key)) is not int or value[key] < 1 for key in
                ("owner_id", "character_id", "conversation_id", "model_profile_id", "profile_revision")) or \
            any(not isinstance(value.get(key), str) or not HEX64.fullmatch(value[key]) for key in
                ("context_sha256", "voice_id_sha256")) or \
            not isinstance(value.get("prompt"), str) or not 1 <= len(value["prompt"]) <= 500 or \
            not isinstance(value.get("notice_version"), str) or not value["notice_version"] or \
            value.get("chat_provider") != "chatgpt_plan" or value.get("chat_model_id") != "gpt-6.1-sol" or \
            value.get("tts_model_id") != "eleven_v4_turbo" or \
            value.get("output_format") != "pcm_24000" or value.get("language_code") != "ja" or \
            (value.get("network_proxy_url") is not None and
             (not isinstance(value["network_proxy_url"], str) or
              not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{2,5}", value["network_proxy_url"]))) or \
            value.get("limits") != LIMITS:
        raise ReceiptError("candidate_manifest_invalid")


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ReceiptError("candidate_receipt_missing_or_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("candidate_receipt_missing_or_invalid") from exc
    if not isinstance(value, dict):
        raise ReceiptError("candidate_receipt_missing_or_invalid")
    return value


@dataclass(frozen=True)
class Prepared:
    manifest: dict
    manifest_sha256: str
    code_sha256: str
    provider_kind: str
    directory: Path

    @property
    def used_path(self) -> Path:
        return self.directory / f"{self.manifest['run_id']}.used"


def issue_mock(directory: Path, manifest: dict, *, root: Path = ROOT) -> Prepared:
    """Only for local fake model + fake TTD tests; never a real approval."""
    validate_manifest(manifest)
    if directory.is_symlink() or not directory.is_dir():
        raise ReceiptError("candidate_receipt_directory_invalid")
    directory = directory.resolve()
    prepared = Prepared(manifest, digest(manifest), code_digest(root),
                        "loopback_fake", directory)
    path = directory / f"{manifest['run_id']}.approval.json"
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"manifest": manifest, "manifest_sha256": prepared.manifest_sha256,
                       "code_sha256": prepared.code_sha256,
                       "provider_kind": "loopback_fake"}, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise ReceiptError("candidate_receipt_exists") from exc
    return prepared


def prepare(directory: Path, run_id: str, *, root: Path = ROOT) -> Prepared:
    if not RUN_ID.fullmatch(run_id) or directory.is_symlink() or not directory.is_dir():
        raise ReceiptError("candidate_receipt_invalid")
    directory = directory.resolve()
    value = _read(directory / f"{run_id}.approval.json")
    if set(value) != {"manifest", "manifest_sha256", "code_sha256", "provider_kind"} or \
            value["provider_kind"] not in ("loopback_fake", "real_approved"):
        raise ReceiptError("candidate_receipt_invalid")
    validate_manifest(value["manifest"])
    if value["manifest"]["run_id"] != run_id or digest(value["manifest"]) != value["manifest_sha256"] or \
            code_digest(root) != value["code_sha256"]:
        raise ReceiptError("candidate_receipt_or_code_changed")
    prepared = Prepared(value["manifest"], value["manifest_sha256"],
                        value["code_sha256"], value["provider_kind"], directory)
    if prepared.used_path.exists():
        raise ReceiptError("candidate_receipt_already_used")
    return prepared


def claim(prepared: Prepared, *, root: Path = ROOT) -> None:
    if prepare(prepared.directory, prepared.manifest["run_id"], root=root) != prepared:
        raise ReceiptError("candidate_receipt_changed")
    try:
        fd = os.open(prepared.used_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReceiptError("candidate_receipt_already_used") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"run_id": prepared.manifest["run_id"],
                   "status": "occupied_outcome_unknown",
                   "manifest_sha256": prepared.manifest_sha256}, output)
        output.flush()
        os.fsync(output.fileno())


class TurnBudget:
    def __init__(self, prepared: Prepared) -> None:
        self.prepared = prepared
        self.model_attempts = 0
        self.characters = 0
        self.segments = 0
        self.connections = 0
        self.audio_bytes = 0

    def reserve_model(self) -> None:
        if self.model_attempts >= LIMITS["model_generations"]:
            raise ReceiptError("model_generation_budget_exhausted")
        self.model_attempts += 1

    def reserve_segment(self, characters: int) -> None:
        if characters < 1 or self.characters + characters > LIMITS["tts_characters"] or \
                self.segments >= LIMITS["tts_segments"]:
            raise ReceiptError("tts_text_budget_exhausted")
        self.characters += characters
        self.segments += 1

    def reserve_connection(self) -> None:
        if self.connections >= LIMITS["tts_connections"]:
            raise ReceiptError("tts_connection_budget_exhausted")
        self.connections += 1

    def accept_audio(self, amount: int) -> None:
        if amount < 0 or self.audio_bytes + amount > LIMITS["tts_audio_bytes"]:
            raise ReceiptError("tts_audio_budget_exhausted")
        self.audio_bytes += amount
