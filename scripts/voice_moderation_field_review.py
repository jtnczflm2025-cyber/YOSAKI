"""In-memory review of pre-extracted direct moderation-envelope field shapes.

This module is deliberately not wired into any provider or product route.
It receives only (field name, JSON type) pairs, never an envelope or values.
The native local viewer is hosted by a separate, default-off capture entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Sequence


MAX_FIELDS = 24
MAX_NAME_LENGTH = 48
SAFE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z", re.ASCII)
SENSITIVE_NAME = re.compile(
    r"password|passwd|secret|token|credential|authorization|authkey|"
    r"apikey|api_key|cookie|session|bearer|private|email|phone|address|"
    r"personal|identity|accesskey|refreshkey",
    re.IGNORECASE | re.ASCII,
)
SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
JSON_TYPES = frozenset({"null", "object", "array", "boolean", "integer", "number", "string"})
RETAIN_SECONDS = 60


class FieldReviewError(ValueError):
    """Only fixed error codes are exposed; never include an input key/value."""


def _digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()


def _reveal_policy(name: str, value_type: str) -> str:
    if value_type == "unsupported":
        return "non_json_value"
    if not (1 <= len(name) <= MAX_NAME_LENGTH) or not SAFE_NAME.fullmatch(name):
        return "unsafe_name_shape"
    if SENSITIVE_NAME.search(name):
        return "sensitive_name_pattern"
    return "eligible_for_manual_review"


def _checked_fields(shapes: Sequence[tuple[str, str]]) -> list[tuple[str, str, str, str]]:
    if type(shapes) not in (list, tuple):
        raise FieldReviewError("field_shapes_invalid")
    if len(shapes) > MAX_FIELDS:
        raise FieldReviewError("field_count_exceeded")
    fields = []
    seen = set()
    for item in shapes:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str or \
                type(item[1]) is not str or item[1] not in JSON_TYPES:
            raise FieldReviewError("field_shape_invalid")
        name, kind = item
        if name in seen:
            raise FieldReviewError("duplicate_field_name")
        seen.add(name)
        fields.append((name, _digest(name), kind, _reveal_policy(name, kind)))
    return sorted(fields, key=lambda item: item[1])


def field_shapes(shapes: Sequence[tuple[str, str]]) -> dict:
    """Return only direct-field hashes, JSON types, and fixed policy codes."""
    fields = _checked_fields(shapes)
    return {
        "field_count": len(fields),
        "fields": [
            {"name_sha256": digest, "value_type": kind, "review_policy": policy}
            for _name, digest, kind, policy in fields
        ],
    }


def reveal_one_name(
    shapes: Sequence[tuple[str, str]], name_sha256: str,
    confirm: Callable[[str, str], bool],
) -> dict[str, str]:
    """Reveal one conservatively shaped name only after per-field confirmation.

    `confirm` receives just the hash and JSON type. The result must stay in a
    local, non-recorded human review session; callers must never log it.
    """
    if type(name_sha256) is not str or not SHA256.fullmatch(name_sha256):
        raise FieldReviewError("invalid_field_selector")
    fields = _checked_fields(shapes)
    matches = [item for item in fields if item[1] == name_sha256]
    if len(matches) != 1:
        raise FieldReviewError("field_selector_not_unique")
    name, digest, kind, policy = matches[0]
    if policy != "eligible_for_manual_review":
        raise FieldReviewError(policy)
    if confirm(digest, kind) is not True:
        raise FieldReviewError("manual_review_declined")
    return {"name": name, "value_type": kind}


class DirectFieldVault:
    """Hold only direct shapes, with timed expiry and explicit reference release."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 timer_factory: Callable = threading.Timer) -> None:
        self._clock = clock
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._shapes: tuple[tuple[str, str], ...] = ()
        self._expires_at = 0.0
        self._closed = False
        self._timer = None

    def store(self, shapes: Sequence[tuple[str, str]]) -> dict:
        checked = _checked_fields(shapes)
        with self._lock:
            if self._closed:
                raise FieldReviewError("review_closed")
            if self._timer is not None:
                self._timer.cancel()
            self._shapes = tuple((name, kind) for name, _hash, kind, _policy in checked)
            self._expires_at = self._clock() + RETAIN_SECONDS
            self._timer = self._timer_factory(RETAIN_SECONDS, self.close)
            self._timer.daemon = True
            self._timer.start()
            return field_shapes(self._shapes)

    def _current(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            if self._closed or self._clock() >= self._expires_at:
                self.close()
                raise FieldReviewError("review_expired")
            return self._shapes

    def summary(self) -> dict:
        return field_shapes(self._current())

    def reveal(self, name_sha256: str,
               confirm: Callable[[str, str], bool]) -> dict[str, str]:
        return reveal_one_name(self._current(), name_sha256, confirm)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._shapes = ()
            self._expires_at = 0.0
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline synthetic field review only")
    parser.add_argument("--synthetic-demo", action="store_true")
    args = parser.parse_args()
    if not args.synthetic_demo:
        parser.error("no live source is installed; use --synthetic-demo")
    synthetic = [("sampleMetadata", "object"), ("accessToken", "string")]
    print(json.dumps(field_shapes(synthetic), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
