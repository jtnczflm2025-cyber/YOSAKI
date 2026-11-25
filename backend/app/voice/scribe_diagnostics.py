"""Bounded, optional Scribe attempt counters. Never stores audio or transcripts."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


ATTEMPT = re.compile(r"^[0-9a-f]{32}$")
KINDS = frozenset({
    "browser_start", "reserved", "upstream_attempt", "upstream_connected",
    "capture_ready", "audio_browser", "audio_upstream_attempt",
    "audio_upstream_sent", "commit_browser", "commit_upstream_attempt",
    "commit_upstream_sent", "provider_event", "browser_result", "error",
    "browser_closed", "upstream_closed", "cleanup",
})


def trace_directory(directory: str) -> Path | None:
    if not directory:
        return None
    root = Path(directory)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("scribe_diagnostics_dir_invalid")
    return root


class ScribeTrace:
    def __init__(self, attempt_id: str, directory: str = "") -> None:
        if not ATTEMPT.fullmatch(attempt_id):
            raise ValueError("attempt_id_invalid")
        self.attempt_id = attempt_id
        self.started = time.monotonic()
        self.count = 0
        self.path: Path | None = None
        root = trace_directory(directory)
        if root is not None:
            self.path = root / f"{attempt_id}.jsonl"
            # A duplicate browser attempt must not erase existing evidence.
            with self.path.open("x", encoding="utf-8"):
                pass

    def emit(self, kind: str, **data: object) -> None:
        if kind not in KINDS or self.count >= 4096:
            raise ValueError("scribe_diagnostics_invalid")
        safe: dict[str, object] = {}
        for key, value in data.items():
            if key not in {"code", "source", "event", "bytes", "chunks", "sample_rate",
                           "channels", "active_samples", "close_code", "initiator"}:
                raise ValueError("scribe_diagnostics_invalid")
            if key in {"bytes", "chunks", "sample_rate", "channels", "active_samples", "close_code"}:
                if type(value) is not int or value < 0 or value > 10_000_000:
                    raise ValueError("scribe_diagnostics_invalid")
            elif not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_]{1,48}", value):
                raise ValueError("scribe_diagnostics_invalid")
            safe[key] = value
        row = {"attempt_id": self.attempt_id, "t_ms": round(
            (time.monotonic() - self.started) * 1000), "kind": kind, **safe}
        if self.path:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, separators=(",", ":")) + "\n")
                file.flush()
                os.fsync(file.fileno())
        self.count += 1
