"""Opt-in bounded HTTP stage evidence and quarantined incomplete audio.

No credentials, text, arbitrary headers, error strings or raw response frames.
Default adapter usage never constructs this class or writes diagnostic files.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import time

from .expression_fixture import _write_json

EVENTS = frozenset(("connecting", "waiting_response", "dispatch_attempt", "response_headers",
    "first_audio", "receive_progress", "receive_complete", "response_closed", "meter_reserved",
    "integrity_check", "integrity_passed", "saving", "saved", "stopped", "between_segments"))
FIELDS = frozenset(("kind", "http_status", "audio_bytes", "error_type", "cache_id", "status"))
ERRORS = frozenset(("ProviderError", "TimeoutError", "CancelledError", "ValueError", "OSError",
                   "PermissionError", "HTTPException", "DiagnosticFailure"))


class DiagnosticFailure(RuntimeError):
    pass


class SegmentDiagnostics:
    def __init__(self, root: Path, job_id: str, sequence: int, attempt_id: str):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / (attempt_id + ".json")
        self.started = time.monotonic()
        self.record = {"job_id": job_id, "sequence": sequence, "attempt_id": attempt_id,
            "events": [], "audio_bytes": 0, "dispatch_attempted": False, "complete_cache": False,
            "diagnostic_audio": None, "truncated_progress_events": 0}
        self.spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=root)
        self.closed = False
        try:
            self.flush()
        except BaseException:
            self.spool.close()
            self.closed = True
            raise

    def flush(self):
        try:
            _write_json(self.path, self.record)
        except Exception:
            raise DiagnosticFailure("http_diagnostics_write_failed") from None

    def event(self, event, **details):
        if event not in EVENTS or set(details) - FIELDS:
            raise DiagnosticFailure("http_diagnostics_field_rejected")
        if "error_type" in details and details["error_type"] not in ERRORS:
            details["error_type"] = "unclassified"
        if "kind" in details and details["kind"] not in ("tcp", "tls"):
            details["kind"] = "unclassified"
        if "status" in details and details["status"] not in ("completed", "cancelled", "failed", "unknown", "generating"):
            details["status"] = "unknown"
        entry = {"event": event, "relative_seconds": round(time.monotonic() - self.started, 6), **details}
        if event == "dispatch_attempt":
            self.record["dispatch_attempted"] = True
        if event == "saved":
            self.record["complete_cache"] = True
            self.record["cache_id"] = details.get("cache_id")
        # Reserve terminal/validation/save slots even for many tiny chunks.
        if event == "receive_progress" and len(self.record["events"]) >= 96:
            self.record["truncated_progress_events"] += 1
        elif len(self.record["events"]) < 128:
            self.record["events"].append(entry)
        else:
            raise DiagnosticFailure("http_diagnostics_terminal_capacity")
        self.flush()

    def chunk(self, payload: bytes):
        if self.closed or self.record["audio_bytes"] + len(payload) > 16 * 1024 * 1024:
            raise DiagnosticFailure("http_diagnostic_audio_hard_limit")
        self.spool.write(payload)
        self.record["audio_bytes"] += len(payload)

    def finish(self):
        if self.closed:
            return
        try:
            if self.record["audio_bytes"] and not self.record["complete_cache"]:
                self.spool.seek(0)
                data = self.spool.read()
                path = self.root / (self.record["attempt_id"] + ".audio.incomplete")
                with path.open("xb") as output:
                    output.write(data)
                self.record["diagnostic_audio"] = {"file": path.name, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "publishable": False}
            self.flush()
        finally:
            self.spool.close()
            self.closed = True
