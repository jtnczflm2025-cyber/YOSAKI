"""Bounded, private evidence for an isolated dynamic voice candidate run.

Only callers choose event names and fields. Never pass raw provider frames,
credentials, model reasoning, or audio data to this writer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path


EVENTS = frozenset({
    "run_started", "model_request_attempt", "model_event", "model_usage",
    "model_terminal", "model_failure", "message_saved", "message_save_failed",
    "segment_pre_send", "connection_attempt", "connection_ready",
    "text_send_attempt", "text_send_return", "first_audio", "audio_count",
    "voice_terminal", "cancel", "turn_summary", "run_failure",
    "diagnostic_limit_reached",
    "moderation_event", "display_terminal",
})
FIELDS = frozenset({
    "stage", "event_type", "error_code", "exception_type", "cancel_source",
    "timeout_type", "model_status", "finish_reason", "usage", "message_id",
    "message_status", "sequence", "source_start", "source_end", "characters",
    "spoken_text", "spoken_text_sha256", "source_sha256", "connection_id",
    "connection_epoch", "attempt", "audio_bytes", "total_audio_bytes",
    "tts_characters", "tts_segments", "tts_connections", "model_generations",
    "request_outcome", "close_origin", "remote_close_code", "voice_status",
    "public_characters", "public_sha256", "browser_playback_ended",
    "event_layer", "event_type_sha256", "parse_branch", "error_category",
    "moderation_seen", "moderation_parsed", "display_version",
    "display_decision", "display_action", "display_reason",
    "answer_item_sha256",
    "failure_field_path", "expected_type", "actual_type",
    "required_fields_present", "extra_fields_sha256", "answer_binding_status",
    "decision_presence", "unknown_key_count", "layer_key_count",
    "unknown_key_details", "unknown_keys_truncated",
    "known_decision_parse_status", "known_decision_reason",
})
MAX_RECORDS = 512
REGULAR_RECORDS = 480
HIGH_VOLUME = frozenset({"model_event", "audio_count"})
SAFE_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z", re.ASCII)
PUBLIC_MODERATION_KEYS = frozenset({
    "openai_chatgpt_moderation_metadata", "openai_verification_recommendation",
    "final_answer_moderation", "presentation", "generation",
    "safety_block", "safety_recourse", "mode", "version",
    "trustedContacts", "annotation", "replacement", "reason",
})
MODERATION_LAYERS = frozenset({
    "response_metadata", "moderation_envelope", "display_decision",
})
JSON_VALUE_TYPES = frozenset({
    "null", "object", "array", "boolean", "integer", "number", "string", "other",
})


def bounded_event_type(value: object) -> dict[str, str]:
    """Keep a short protocol identifier, or only a hash of an unsafe value."""
    if isinstance(value, str) and 1 <= len(value) <= 96 and SAFE_EVENT_TYPE.fullmatch(value):
        return {"event_type": value}
    raw = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str)
    return {"event_type": "unrecognized",
            "event_type_sha256": hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()}


class DiagnosticWriteError(OSError):
    pass


class DiagnosticCapacityError(RuntimeError):
    pass


class RunDiagnostics:
    def __init__(self, directory: Path, run_id: str, generation_id: str) -> None:
        if not run_id or not generation_id or any(c not in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
            raise ValueError("invalid_diagnostic_identity")
        self.path = directory / f"{run_id}.diagnostic.jsonl"
        self.run_id = run_id
        self.generation_id = generation_id
        self.started = time.monotonic_ns()
        self.lock = threading.Lock()
        self.count = 0
        self.truncated = False
        self.failed = False
        self.failure_code: str | None = None
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.file = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        try:
            self.record("run_started")
        except Exception:
            self.file.close()
            raise

    @property
    def status(self) -> str:
        return "failed" if self.failed else "truncated" if self.truncated else "complete"

    def _write(self, item: dict) -> None:
        self.file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def record(self, event: str, **fields: object) -> bool:
        if event not in EVENTS or set(fields) - FIELDS:
            raise ValueError("invalid_diagnostic_event")
        details = fields.get("unknown_key_details")
        if details is not None:
            count = fields.get("unknown_key_count")
            layer_count = fields.get("layer_key_count")
            truncated = fields.get("unknown_keys_truncated")
            if not isinstance(details, list) or len(details) > 24 or \
                    type(count) is not int or type(layer_count) is not int or \
                    type(truncated) is not bool or count < len(details) or \
                    layer_count < count or truncated != (count > len(details)):
                raise ValueError("invalid_moderation_key_diagnostic")
            for item in details:
                if not isinstance(item, dict) or set(item) not in (
                        {"layer", "key_sha256", "value_type"},
                        {"layer", "key_sha256", "value_type", "public_name"}) or \
                        not isinstance(item["layer"], str) or \
                        item["layer"] not in MODERATION_LAYERS or \
                        not isinstance(item["value_type"], str) or \
                        item["value_type"] not in JSON_VALUE_TYPES or \
                        not isinstance(item["key_sha256"], str) or \
                        not re.fullmatch(r"[0-9a-f]{64}", item["key_sha256"]):
                    raise ValueError("invalid_moderation_key_diagnostic")
                public_name = item.get("public_name")
                if public_name is not None and (not isinstance(public_name, str) or
                        public_name not in PUBLIC_MODERATION_KEYS or
                        hashlib.sha256(public_name.encode()).hexdigest() !=
                        item["key_sha256"]):
                    raise ValueError("invalid_moderation_key_diagnostic")
        with self.lock:
            if self.failed:
                return False
            if self.count >= MAX_RECORDS:
                self.failed = True
                self.failure_code = "diagnostic_capacity_exceeded"
                raise DiagnosticCapacityError(self.failure_code)
            if event in HIGH_VOLUME and self.count >= REGULAR_RECORDS:
                if not self.truncated:
                    self.truncated = True
                    self._append("diagnostic_limit_reached",
                                 error_code="high_volume_events_truncated")
                return False
            self._append(event, **fields)
            return True

    def _append(self, event: str, **fields: object) -> None:
            item = {"schema_version": 1, "run_id": self.run_id,
                    "generation_id": self.generation_id,
                    "elapsed_ms": round((time.monotonic_ns() - self.started) / 1e6, 3),
                    "event": event, **fields}
            try:
                self._write(item)
            except (OSError, UnicodeError) as exc:
                self.failed = True
                self.failure_code = "diagnostic_write_failed"
                raise DiagnosticWriteError(self.failure_code) from exc
            self.count += 1

    def close(self) -> None:
        try:
            self.file.close()
        except OSError as exc:
            self.failed = True
            self.failure_code = "diagnostic_close_failed"
            raise DiagnosticWriteError(self.failure_code) from exc
