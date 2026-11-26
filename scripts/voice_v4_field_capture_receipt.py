"""One-use, model-only approval receipt for isolated field-shape review."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = (
    "backend/app/responses_adapter.py",
    "backend/app/voice/__init__.py",
    "backend/app/voice/realtime_answer_display.py",
    "scripts/voice_moderation_field_review.py",
    "scripts/voice_v4_field_capture.py",
    "scripts/voice_v4_field_capture_receipt.py",
    "scripts/voice_v4_field_capture_approve.py",
)
LIMITS = {"model_generations": 1, "max_output_tokens": 128,
          "model_seconds": 30, "review_seconds": 60, "process_seconds": 95,
          "elevenlabs_http": 0, "elevenlabs_ws": 0,
          "tts_characters": 0, "tts_audio_bytes": 0}
HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
RUN = re.compile(r"[a-z0-9][a-z0-9-]{7,79}\Z", re.ASCII)


class ReceiptError(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def code_digest(root: Path = ROOT) -> str:
    lines = [f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
             for name in CODE_PATHS]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def validate(manifest: dict) -> None:
    exact = {"schema_version", "mode", "run_id", "generation_id",
             "source_manifest_sha256", "context_sha256", "model_request",
             "model_request_sha256", "endpoint", "network_proxy_url", "limits"}
    if type(manifest) is not dict or set(manifest) != exact or \
            manifest["schema_version"] != 1 or manifest["mode"] != "local_field_shape_review" or \
            type(manifest["run_id"]) is not str or not RUN.fullmatch(manifest["run_id"]) or \
            type(manifest["generation_id"]) is not str or \
            not re.fullmatch(r"[0-9a-f]{32}", manifest["generation_id"]) or \
            any(type(manifest[key]) is not str or not HEX.fullmatch(manifest[key]) for key in
                ("source_manifest_sha256", "context_sha256", "model_request_sha256")) or \
            manifest["endpoint"] != "https://api.openai.com/v1/responses" or \
            manifest["network_proxy_url"] != "http://127.0.0.1:7897" or \
            manifest["limits"] != LIMITS:
        raise ReceiptError("field_capture_manifest_invalid")
    request = manifest["model_request"]
    if type(request) is not dict or request.get("context_sha256") != manifest["context_sha256"] or \
            request.get("provider") != "chatgpt_plan" or request.get("model") != "gpt-6.1-sol" or \
            request.get("owner_id") != 1 or request.get("character_id") != 1 or \
            request.get("conversation_id") != 5 or request.get("model_profile_id") != 5 or \
            digest(request) != manifest["model_request_sha256"]:
        raise ReceiptError("field_capture_request_invalid")
    body = request.get("request_body")
    if type(body) is not dict or body.get("model") != "gpt-6.1-sol" or \
            body.get("max_output_tokens") != 128 or body.get("stream") is not True or \
            body.get("store") is not False or body.get("tools") or \
            type(body.get("input")) is not list or \
            any(type(item) is not dict or item.get("role") not in ("system", "user")
                for item in body["input"]):
        raise ReceiptError("field_capture_request_body_invalid")


def prepare(directory: Path, run_id: str) -> tuple[dict, str, str, Path]:
    if not RUN.fullmatch(run_id) or directory.is_symlink() or not directory.is_dir():
        raise ReceiptError("receipt_location_invalid")
    directory = directory.resolve()
    path = directory / f"{run_id}.approval.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ReceiptError("receipt_missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReceiptError("receipt_invalid") from exc
    if type(value) is not dict or set(value) != {"manifest", "manifest_sha256",
            "code_sha256", "provider_kind"} or value["provider_kind"] != "real_approved":
        raise ReceiptError("receipt_invalid")
    manifest = value["manifest"]
    validate(manifest)
    if manifest["run_id"] != run_id or digest(manifest) != value["manifest_sha256"] or \
            code_digest() != value["code_sha256"]:
        raise ReceiptError("receipt_binding_changed")
    if (directory / f"{run_id}.used").exists():
        raise ReceiptError("receipt_already_used")
    return manifest, value["manifest_sha256"], value["code_sha256"], directory


def claim(directory: Path, run_id: str) -> tuple[dict, str, str]:
    manifest, manifest_hash, code_hash, directory = prepare(directory, run_id)
    try:
        fd = os.open(directory / f"{run_id}.used",
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReceiptError("receipt_already_used") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"run_id": run_id, "status": "occupied_outcome_unknown",
                   "manifest_sha256": manifest_hash}, output)
        output.flush()
        os.fsync(output.fileno())
    return manifest, manifest_hash, code_hash
