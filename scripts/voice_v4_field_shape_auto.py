"""Default-off, one-use model-only audit of direct moderation field shapes.

No Tk window, product WebSocket, TTS connector, or database import. The only
persisted provider-derived data are bounded direct field names/JSON types,
with suspicious names replaced by hashes. Values and nested content are never
copied into the collector or its report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from scripts import voice_v4_field_capture as prior
from scripts.voice_moderation_field_review import FieldReviewError, field_shapes


ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = (
    "backend/app/chatgpt_plan.py",
    "backend/app/responses_adapter.py",
    "backend/app/voice/__init__.py",
    "backend/app/voice/realtime_answer_display.py",
    "scripts/voice_moderation_field_review.py",
    "scripts/voice_v4_chatgpt_oauth_isolated.py",
    "scripts/voice_v4_field_capture.py",
    "scripts/voice_v4_field_capture_receipt.py",
    "scripts/voice_v4_field_shape_auto.py",
)
LIMITS = {"model_generations": 1, "max_output_tokens": 128,
          "model_seconds": 30, "metadata_events": 4,
          "direct_fields_total": 24, "elevenlabs_http": 0,
          "elevenlabs_ws": 0, "tts_characters": 0, "tts_audio_bytes": 0}
HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
RUN = re.compile(r"[a-z0-9][a-z0-9-]{7,79}\Z", re.ASCII)
FROZEN_SOURCE_MANIFEST = "9f9410860ed2dd90e27c3dc67760947fd186180cd4831bee16244197aeafa7b9"
FROZEN_CONTEXT = "2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e"
FROZEN_MODEL_REQUEST = "ed3ca355cb46c8ff5d6d7411a64ad32e4c77b7da73cdf80cad14e3a94c915c58"
PUBLIC_NAMES = frozenset({
    "final_answer_moderation", "presentation", "generation",
    "safety_block", "safety_recourse", "openai_verification_recommendation",
})
EXTRA_SENSITIVE_NAME = re.compile(
    r"user_?name|full_?name|first_?name|last_?name|birth|date_?of_?birth|"
    r"location|geo|ip_?address|ssn|passport|card_?number|bank|account_?number|"
    r"device_?id|customer_?id|contact|payment|billing",
    re.IGNORECASE | re.ASCII,
)


class AutoReviewError(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def code_digest(root: Path = ROOT) -> str:
    lines = [f"{name} {hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
             for name in CODE_PATHS]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def validate_manifest(manifest: dict) -> None:
    exact = {"schema_version", "mode", "run_id", "generation_id",
             "source_manifest_sha256", "context_sha256", "model_request",
             "model_request_sha256", "endpoint", "network_proxy_url", "limits"}
    if type(manifest) is not dict or set(manifest) != exact or \
            manifest["schema_version"] != 1 or \
            manifest["mode"] != "automatic_direct_field_shape_audit" or \
            type(manifest["run_id"]) is not str or not RUN.fullmatch(manifest["run_id"]) or \
            type(manifest["generation_id"]) is not str or \
            not re.fullmatch(r"[0-9a-f]{32}", manifest["generation_id"]) or \
            any(type(manifest[key]) is not str or not HEX.fullmatch(manifest[key]) for key in
                ("source_manifest_sha256", "context_sha256", "model_request_sha256")) or \
            manifest["source_manifest_sha256"] != FROZEN_SOURCE_MANIFEST or \
            manifest["context_sha256"] != FROZEN_CONTEXT or \
            manifest["model_request_sha256"] != FROZEN_MODEL_REQUEST or \
            manifest["endpoint"] != "https://api.openai.com/v1/responses" or \
            manifest["network_proxy_url"] != "http://127.0.0.1:7897" or \
            manifest["limits"] != LIMITS:
        raise AutoReviewError("manifest_invalid")
    request = manifest["model_request"]
    if type(request) is not dict or digest(request) != manifest["model_request_sha256"] or \
            request.get("context_sha256") != manifest["context_sha256"] or \
            request.get("provider") != "chatgpt_plan" or request.get("model") != "gpt-6.1-sol" or \
            (request.get("owner_id"), request.get("character_id"),
             request.get("conversation_id"), request.get("model_profile_id")) != (1, 1, 5, 5):
        raise AutoReviewError("request_invalid")
    body = request.get("request_body")
    if type(body) is not dict or body.get("model") != "gpt-6.1-sol" or \
            body.get("max_output_tokens") != 128 or body.get("stream") is not True or \
            body.get("store") is not False or body.get("tools") or \
            type(body.get("input")) is not list or \
            any(type(item) is not dict or item.get("role") not in ("system", "user")
                for item in body["input"]):
        raise AutoReviewError("request_body_invalid")


class AutoCollector:
    """Store only bounded, screened names and direct JSON types."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.field_total = 0
        self.closed = False

    def store(self, shapes: list[tuple[str, str]]) -> dict:
        if self.closed:
            raise FieldReviewError("collector_closed")
        if len(self.events) >= LIMITS["metadata_events"] or \
                self.field_total + len(shapes) > LIMITS["direct_fields_total"]:
            raise FieldReviewError("field_capture_budget_exceeded")
        summary = field_shapes(shapes)
        names_by_hash = {hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest(): name
                         for name, _kind in shapes}
        fields = []
        for item in summary["fields"]:
            raw_name = names_by_hash[item["name_sha256"]]
            eligible = (item["review_policy"] == "eligible_for_manual_review"
                        and not EXTRA_SENSITIVE_NAME.search(raw_name))
            name_hash = item["name_sha256"]
            name = raw_name if eligible else None
            fields.append({"name": name, "name_sha256": name_hash if eligible else None,
                           "value_type": item["value_type"],
                           "mask_reason": (None if eligible else
                               "sensitive_name_pattern" if EXTRA_SENSITIVE_NAME.search(raw_name)
                               else item["review_policy"]),
                           "source_status": ("name_seen_in_pinned_public_source" if
                               name in PUBLIC_NAMES else "semantic_status_undetermined")})
        self.events.append({"event_number": len(self.events) + 1,
                            "field_count": len(fields), "fields": fields})
        self.field_total += len(fields)
        return {"field_count": len(fields)}

    def close(self) -> None:
        self.closed = True

    def report(self) -> dict:
        return {"moderation_events": len(self.events),
                "direct_field_entries": self.field_total,
                "events": self.events,
                "semantic_conclusion": "names_only_no_control_inference"}


def _read_receipt(directory: Path, run_id: str) -> tuple[dict, str, str, Path]:
    if type(run_id) is not str or not RUN.fullmatch(run_id) or \
            directory.is_symlink() or not directory.is_dir() or \
            directory.resolve().is_relative_to(ROOT.resolve()):
        raise AutoReviewError("receipt_location_invalid")
    directory = directory.resolve()
    path = directory / f"{run_id}.approval.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise AutoReviewError("receipt_missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise AutoReviewError("receipt_invalid") from exc
    if type(value) is not dict or set(value) != {"manifest", "manifest_sha256",
            "code_sha256", "provider_kind"} or value["provider_kind"] != "real_approved":
        raise AutoReviewError("receipt_invalid")
    manifest = value["manifest"]
    validate_manifest(manifest)
    if manifest["run_id"] != run_id or digest(manifest) != value["manifest_sha256"] or \
            code_digest() != value["code_sha256"]:
        raise AutoReviewError("receipt_binding_changed")
    if (directory / f"{run_id}.used").exists():
        raise AutoReviewError("receipt_already_used")
    return manifest, value["manifest_sha256"], value["code_sha256"], directory


def _claim(directory: Path, run_id: str) -> tuple[dict, str, str]:
    manifest, manifest_hash, code_hash, directory = _read_receipt(directory, run_id)
    try:
        fd = os.open(directory / f"{run_id}.used",
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AutoReviewError("receipt_already_used") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"run_id": run_id, "status": "occupied_outcome_unknown",
                   "manifest_sha256": manifest_hash}, output)
        output.flush()
        os.fsync(output.fileno())
    return manifest, manifest_hash, code_hash


def _approve(manifest_path: Path, directory: Path, approved_manifest: str,
             approved_code: str) -> dict:
    if directory.is_symlink() or not directory.is_dir() or \
            directory.resolve().is_relative_to(ROOT.resolve()):
        raise AutoReviewError("receipt_location_invalid")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = value["manifest"]
    validate_manifest(manifest)
    manifest_hash, code_hash = digest(manifest), code_digest()
    if value.get("manifest_sha256") != manifest_hash or \
            value.get("code_sha256") != code_hash or \
            (approved_manifest, approved_code) != (manifest_hash, code_hash):
        raise AutoReviewError("approved_fingerprints_mismatch")
    path = directory / f"{manifest['run_id']}.approval.json"
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"manifest": manifest, "manifest_sha256": manifest_hash,
                       "code_sha256": code_hash, "provider_kind": "real_approved"},
                      output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise AutoReviewError("receipt_already_exists") from exc
    return {"run_id": manifest["run_id"], "receipt_created": True,
            "provider_requests_dispatched": 0}


def _write_report(directory: Path, run_id: str, report: dict) -> Path:
    path = directory / f"{run_id}.field-shapes.json"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return path


async def run_once(manifest: dict, directory: Path,
                   token_provider: Callable[[], str],
                   lines_factory: Callable, *, enabled: bool) -> dict:
    """Final path; tests inject fake token/lines, real mode uses frozen connector."""
    if not enabled or os.getenv("YOSAKI_FIELD_SHAPE_AUTO_ENABLED", "0") != "1":
        raise AutoReviewError("auto_capture_disabled")
    validate_manifest(manifest)
    approved, _manifest_hash, _code_hash, _ = _read_receipt(directory,
                                                              manifest["run_id"])
    if approved != manifest or prior._proxy() != manifest["network_proxy_url"]:
        raise AutoReviewError("binding_or_proxy_changed")
    token = token_provider()  # Freshness check before occupying the receipt.
    _claim(directory, manifest["run_id"])
    collector = AutoCollector()
    cancelled = False
    try:
        async with asyncio.timeout(LIMITS["model_seconds"]):
            result = await prior.process_sse(lines_factory(manifest, token),
                                             collector, capture_enabled=True)
    except TimeoutError:
        collector.close()
        result = {"model_status": "failed", "display_action": "restrict",
                  "reason": "model_time_budget", "usage": None,
                  "tts_dispatches": 0}
    except asyncio.CancelledError:
        result = {"model_status": "cancelled", "display_action": "restrict",
                  "reason": "local_cancel", "usage": None,
                  "tts_dispatches": 0}
        cancelled = True
    except Exception:
        result = {"model_status": "failed", "display_action": "restrict",
                  "reason": "field_shape_execution_failed", "usage": None,
                  "tts_dispatches": 0}
    finally:
        del token
    collector.close()
    report = {"schema_version": 1, "run_id": manifest["run_id"],
              "generation_id": manifest["generation_id"],
              "model_status": result["model_status"],
              "display_action": result["display_action"],
              "reason": result["reason"], "usage": result.get("usage"),
              "model_requests_claimed": 1, "tts_dispatches": 0,
              **collector.report()}
    _write_report(directory, manifest["run_id"], report)
    if cancelled:
        raise asyncio.CancelledError()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatic direct field-shape audit")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--real-execute", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--approved-manifest-sha256")
    parser.add_argument("--approved-code-sha256")
    args = parser.parse_args()
    try:
        value = json.loads(args.manifest.read_text(encoding="utf-8"))
        manifest = value["manifest"]
        validate_manifest(manifest)
        if (digest(manifest), code_digest()) != \
                (value.get("manifest_sha256"), value.get("code_sha256")):
            raise AutoReviewError("candidate_fingerprint_changed")
        if args.approve:
            if not args.receipt_dir or not args.approved_manifest_sha256 or \
                    not args.approved_code_sha256 or args.real_execute:
                raise AutoReviewError("approval_arguments_missing")
            print(json.dumps(_approve(args.manifest, args.receipt_dir,
                args.approved_manifest_sha256, args.approved_code_sha256)))
            return 0
        if not args.real_execute:
            print(json.dumps({"mode": "dry_run", "run_id": manifest["run_id"],
                              "model_requests": 0, "tts_dispatches": 0}))
            return 0
        if not args.receipt_dir or args.run_id != manifest["run_id"]:
            raise AutoReviewError("real_execution_arguments_missing")
        report = asyncio.run(run_once(manifest, args.receipt_dir,
            prior._read_token, prior._real_lines, enabled=True))
        print(json.dumps({"run_id": manifest["run_id"],
                          "model_status": report["model_status"],
                          "moderation_events": report["moderation_events"],
                          "direct_field_entries": report["direct_field_entries"],
                          "tts_dispatches": 0}, sort_keys=True))
        return 0 if report["model_status"] == "completed" else 1
    except (Exception, KeyboardInterrupt):
        print("field_shape_auto_preflight_or_execution_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
