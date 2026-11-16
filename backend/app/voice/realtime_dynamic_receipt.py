"""One-use, bounded dynamic-turn receipt for isolated fake-provider runs only.

No real-provider approval format or issuer exists in this module. The marker
is never removed after claim, including a crash with an unknown outcome.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CODE_PATHS = (
    "backend/app/main.py",
    "backend/app/voice/realtime_chat_bridge.py",
    "backend/app/voice/realtime_dynamic_receipt.py",
    "backend/app/voice/realtime_dynamic_product.py",
    "backend/app/voice/realtime_output.py",
    "backend/app/voice/readable_text.py",
    "frontend/src/voice/realtimeOutput.ts",
    "frontend/src/voice/DynamicChatOutput.vue",
    "frontend/src/App.vue",
    "scripts/voice_v4_dynamic_isolated.py",
)
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,79}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ReceiptError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def code_digest(root: Path = ROOT) -> str:
    lines = [f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
             for name in CODE_PATHS]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ReceiptError("receipt_file_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, OSError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt_file_invalid") from exc
    if not isinstance(value, dict):
        raise ReceiptError("receipt_file_invalid")
    return value


def validate_manifest(value: dict) -> None:
    limits = value.get("limits")
    exact = {"schema_version", "mode", "run_id", "generation_id", "owner_id",
             "character_id", "conversation_id", "profile_revision",
             "voice_id_sha256", "notice_version", "chat_model_id",
             "tts_model_id", "output_format", "language_code", "limits"}
    if (set(value) != exact or value.get("schema_version") != 1 or
            value.get("mode") != "mock_only" or
            not isinstance(value.get("run_id"), str) or
            not RUN_ID.fullmatch(value["run_id"]) or
            not isinstance(value.get("generation_id"), str) or
            not re.fullmatch(r"[0-9a-f]{32}", value["generation_id"]) or
            any(type(value.get(key)) is not int or value[key] < 1 for key in
                ("owner_id", "character_id", "conversation_id", "profile_revision")) or
            not isinstance(value.get("voice_id_sha256"), str) or
            not HEX64.fullmatch(value["voice_id_sha256"]) or
            any(not isinstance(value.get(key), str) or not value[key] for key in
                ("notice_version", "chat_model_id", "tts_model_id",
                 "output_format", "language_code")) or
            not isinstance(limits, dict) or set(limits) != {
                "max_characters", "max_segments", "max_connections",
                "max_audio_bytes", "max_turn_seconds"} or
            any(type(limits[key]) is not int or limits[key] < 1 for key in limits) or
            not (24 <= limits["max_characters"] <= 1600 and
                 limits["max_segments"] <= limits["max_connections"] <= 8 and
                 limits["max_audio_bytes"] <= 8 * 1024 * 1024 and
                 limits["max_turn_seconds"] <= 60)):
        raise ReceiptError("manifest_invalid")


@dataclass(frozen=True)
class Prepared:
    manifest: dict
    manifest_sha256: str
    code_sha256: str
    directory: Path

    @property
    def used_path(self) -> Path:
        return self.directory / f"{self.manifest['run_id']}.used"


def issue_mock(directory: Path, manifest: dict, *, root: Path = ROOT) -> Prepared:
    """Test/runtime setup only. This cannot issue a real supplier approval."""
    validate_manifest(manifest)
    if directory.is_symlink():
        raise ReceiptError("receipt_directory_invalid")
    directory = directory.resolve()
    if not directory.is_dir():
        raise ReceiptError("receipt_directory_invalid")
    prepared = Prepared(manifest, digest(manifest), code_digest(root), directory)
    path = directory / f"{manifest['run_id']}.approval.json"
    record = {"manifest": manifest, "manifest_sha256": prepared.manifest_sha256,
              "code_sha256": prepared.code_sha256, "provider_kind": "loopback_fake"}
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise ReceiptError("receipt_already_exists") from exc
    return prepared


def prepare(directory: Path, run_id: str, *, root: Path = ROOT) -> Prepared:
    if not RUN_ID.fullmatch(run_id):
        raise ReceiptError("run_id_invalid")
    if directory.is_symlink():
        raise ReceiptError("receipt_directory_invalid")
    directory = directory.resolve()
    if not directory.is_dir():
        raise ReceiptError("receipt_directory_invalid")
    record = _read(directory / f"{run_id}.approval.json")
    if set(record) != {"manifest", "manifest_sha256", "code_sha256", "provider_kind"} or \
            record["provider_kind"] != "loopback_fake":
        raise ReceiptError("receipt_not_mock_only")
    manifest = record["manifest"]
    if not isinstance(manifest, dict):
        raise ReceiptError("manifest_invalid")
    validate_manifest(manifest)
    if (manifest["run_id"] != run_id or digest(manifest) != record["manifest_sha256"] or
            code_digest(root) != record["code_sha256"]):
        raise ReceiptError("receipt_or_code_changed")
    prepared = Prepared(manifest, record["manifest_sha256"], record["code_sha256"], directory)
    if prepared.used_path.exists():
        raise ReceiptError("receipt_already_used")
    return prepared


def claim(prepared: Prepared, *, root: Path = ROOT) -> None:
    latest = prepare(prepared.directory, prepared.manifest["run_id"], root=root)
    if latest != prepared:
        raise ReceiptError("receipt_changed")
    try:
        fd = os.open(prepared.used_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReceiptError("receipt_already_used") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"run_id": prepared.manifest["run_id"],
                   "status": "occupied_outcome_unknown",
                   "manifest_sha256": prepared.manifest_sha256}, output)
        output.flush()
        os.fsync(output.fileno())


class TurnBudget:
    def __init__(self, prepared: Prepared) -> None:
        self.prepared = prepared
        self.characters = 0
        self.segments = 0
        self.connections = 0
        self.audio_bytes = 0

    def reserve_segment(self, characters: int) -> None:
        limits = self.prepared.manifest["limits"]
        if characters < 1 or (self.characters + characters > limits["max_characters"] or
                             self.segments + 1 > limits["max_segments"]):
            raise ReceiptError("turn_text_budget_exhausted")
        self.characters += characters
        self.segments += 1

    def reserve_connection(self) -> None:
        if self.connections + 1 > self.prepared.manifest["limits"]["max_connections"]:
            raise ReceiptError("turn_connection_budget_exhausted")
        self.connections += 1

    def accept_audio(self, amount: int) -> None:
        if amount < 0 or self.audio_bytes + amount > self.prepared.manifest["limits"]["max_audio_bytes"]:
            raise ReceiptError("turn_audio_budget_exhausted")
        self.audio_bytes += amount
