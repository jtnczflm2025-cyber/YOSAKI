"""Display clearance for the isolated final-answer voice path.

Contract pinned to openai/codex ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f:
codex-api/src/sse/responses.rs, protocol/src/{protocol.rs,items/display.rs},
core/src/{session/turn.rs,stream_events_utils.rs}. No raw metadata is retained.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class DisplayResult:
    seen: bool
    parsed: bool
    version: int | None
    decision: str
    action: str
    reason: str
    item_hash: str | None = None
    failure_field_path: str | None = None
    expected_type: str | None = None
    actual_type: str | None = None
    required_fields_present: dict[str, bool] | None = None
    extra_fields_sha256: str | None = None
    unknown_key_count: int | None = None
    layer_key_count: int | None = None
    unknown_key_details: list[dict[str, str]] | None = None
    unknown_keys_truncated: bool = False
    known_decision_parse_status: str | None = None
    known_decision_reason: str | None = None
    answer_binding_status: str = "not_checked"
    decision_presence: str = "metadata_absent"

    def diagnostic(self) -> dict[str, Any]:
        return {"moderation_seen": self.seen, "moderation_parsed": self.parsed,
                "display_version": (1 if self.version == 1 else
                    "unsupported" if self.version is not None else None),
                "display_decision": self.decision,
                "display_action": self.action, "display_reason": self.reason,
                "answer_item_sha256": self.item_hash,
                "failure_field_path": self.failure_field_path,
                "expected_type": self.expected_type,
                "actual_type": self.actual_type,
                "required_fields_present": self.required_fields_present,
                "extra_fields_sha256": self.extra_fields_sha256,
                "unknown_key_count": self.unknown_key_count,
                "layer_key_count": self.layer_key_count,
                "unknown_key_details": self.unknown_key_details,
                "unknown_keys_truncated": self.unknown_keys_truncated,
                "known_decision_parse_status": self.known_decision_parse_status,
                "known_decision_reason": self.known_decision_reason,
                "answer_binding_status": self.answer_binding_status,
                "decision_presence": self.decision_presence}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    return "other"


def _extra_hash(value: dict[str, Any], known: set[str]) -> str | None:
    extra = sorted(str(key) for key in value if key not in known)
    return (hashlib.sha256("\x00".join(extra).encode("utf-8", "surrogatepass")).hexdigest()
            if extra else None)


# Only names verified in the pinned public source may appear in diagnostics.
# Unknown/private names are hashed individually. Values are never retained.
_PUBLIC_METADATA_KEYS = frozenset({
    "openai_chatgpt_moderation_metadata", "openai_verification_recommendation",
    "final_answer_moderation", "presentation", "generation",
    "safety_block", "safety_recourse", "mode", "version",
    "trustedContacts", "annotation", "replacement", "reason",
})
_UNKNOWN_KEY_DETAIL_LIMIT = 24

# In pinned Codex ea1b6ad, core/session/turn.rs reads only
# final_answer_moderation for the final-answer display decision. The full
# envelope is also forwarded to the TUI, whose safety-recourse consumer reads
# safety_block/safety_recourse. These four observed object-valued categories
# are not final-answer presentation controls in that path. Keep all other
# unknown fields restricted, including the recourse fields we cannot render.
_NON_DISPLAY_OBJECT_FIELDS = frozenset({
    "prompt", "tool_call", "tool_response", "generation",
})


def _unknown_key_diagnostics(value: dict[str, Any], known: set[str],
                             layer: str) -> dict[str, Any]:
    details = []
    for key in value:
        if key in known:
            continue
        name = str(key)
        digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()
        detail = {"layer": layer, "key_sha256": digest,
                  "value_type": _type_name(value[key])}
        if name in _PUBLIC_METADATA_KEYS:
            detail["public_name"] = name
        details.append(detail)
    details.sort(key=lambda detail: detail["key_sha256"])
    return {"layer_key_count": len(value), "unknown_key_count": len(details),
            "unknown_key_details": details[:_UNKNOWN_KEY_DETAIL_LIMIT],
            "unknown_keys_truncated": len(details) > _UNKNOWN_KEY_DETAIL_LIMIT}


class AnswerDisplayGate:
    """Resolve optional final-answer moderation after completion and stream EOF."""

    def __init__(self) -> None:
        self.items: dict[str, str | None] = {}
        self.completed_text: dict[str, str] = {}
        self.text_item_ids: set[str] = set()
        self.decisions: list[tuple[str, DisplayResult]] = []
        self.optional_decisions: list[DisplayResult] = []
        self.malformed: DisplayResult | None = None
        self.terminal_tools = []

    @staticmethod
    def _restricted(seen: bool, parsed: bool, reason: str,
                    version: int | None = None, decision: str = "unknown",
                    **diagnostic: Any) -> DisplayResult:
        return DisplayResult(seen, parsed, version, decision, "restrict", reason,
                             **diagnostic)

    @staticmethod
    def _known_decision_snapshot(envelope: Any) -> DisplayResult:
        """Parse only the known decision, without granting display clearance.

        A separate gate runs the same parser on a sanitized envelope. The
        original unknown field still restricts the real gate, while the
        diagnostic can say whether a known decision was missing or valid.
        """
        if not isinstance(envelope, dict):
            return DisplayResult(True, False, None, "unknown", "restrict",
                "moderation_format_invalid",
                decision_presence="not_checked_invalid_envelope")
        safe = ({"final_answer_moderation": envelope["final_answer_moderation"]}
                if "final_answer_moderation" in envelope else {})
        probe = AnswerDisplayGate()
        result = probe.observe({"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": safe}})
        assert result is not None
        return result

    @staticmethod
    def _known_decision_fields(result: DisplayResult) -> dict[str, Any]:
        return {"decision_presence": result.decision_presence,
                "known_decision_parse_status": (
                    "absent" if result.action == "pending" else
                    "parsed" if result.parsed else "invalid_or_unsupported"),
                "known_decision_reason": result.reason,
                "item_hash": result.item_hash,
                "answer_binding_status": "not_checked_restricted"}

    def observe(self, event: dict[str, Any]) -> DisplayResult | None:
        kind = event.get("type")
        if kind in {"response.output_item.added", "response.output_item.done"}:
            self._item(event.get("item"))
        elif kind == "response.output_text.delta" or kind == "response.output_text.done":
            item_id = event.get("item_id")
            if isinstance(item_id, str) and item_id:
                self.text_item_ids.add(item_id)
            else:
                self.malformed = self._restricted(False, False, "answer_item_missing")
        elif kind == "response.completed":
            output = (event.get("response") or {}).get("output")
            if isinstance(output, list):
                self.terminal_tools = [
                    (item.get("call_id"), item.get("name"), item.get("arguments"))
                    for item in output if isinstance(item, dict) and item.get("type") == "function_call"
                ] if all(isinstance(item, dict) and item.get("type") in ("reasoning", "function_call")
                         for item in output) else []
                for item in output:
                    self._item(item)
                    if isinstance(item, dict) and item.get("type") == "message" and \
                            isinstance(item.get("id"), str) and isinstance(item.get("content"), list):
                        parts = item["content"]
                        if all(isinstance(part, dict) and part.get("type") == "output_text" and
                               isinstance(part.get("text"), str) for part in parts):
                            self.completed_text[item["id"]] = "".join(
                                part["text"] for part in parts)
        if kind != "response.metadata":
            return None
        outer = event.get("metadata")
        if not isinstance(outer, dict) or "openai_chatgpt_moderation_metadata" not in outer:
            return None
        if set(outer) != {"openai_chatgpt_moderation_metadata"}:
            known = self._known_decision_snapshot(
                outer["openai_chatgpt_moderation_metadata"])
            result = self._restricted(True, False, "mixed_metadata_unsupported",
                known.version, known.decision,
                failure_field_path="metadata", expected_type="moderation_only_object",
                actual_type="object", extra_fields_sha256=_extra_hash(outer,
                    {"openai_chatgpt_moderation_metadata"}),
                **_unknown_key_diagnostics(outer,
                    {"openai_chatgpt_moderation_metadata"}, "response_metadata"),
                **self._known_decision_fields(known))
            self.malformed = result
            return result
        envelope = outer["openai_chatgpt_moderation_metadata"]
        base = "metadata.openai_chatgpt_moderation_metadata"
        marker_present = isinstance(envelope, dict) and "final_answer_moderation" in envelope
        marker = envelope.get("final_answer_moderation") if isinstance(envelope, dict) else None
        present = {"final_answer_moderation": isinstance(envelope, dict) and
                   "final_answer_moderation" in envelope,
                   "item_id": isinstance(marker, dict) and "item_id" in marker,
                   "moderated": isinstance(marker, dict) and "moderated" in marker,
                   "display": isinstance(marker, dict) and "display" in marker,
                   "version": isinstance(marker, dict) and
                   isinstance(marker.get("display"), dict) and
                   "version" in marker["display"],
                   "mode": isinstance(marker, dict) and
                   isinstance(marker.get("display"), dict) and
                   "mode" in marker["display"]}
        extra = _extra_hash(envelope, {"final_answer_moderation"}) if \
            isinstance(envelope, dict) else None
        if isinstance(envelope, dict):
            # Only the four observed object-valued categories follow the
            # pinned Codex non-display path. Other fields remain restricted.
            known_fields = {"final_answer_moderation", "presentation"} | \
                _NON_DISPLAY_OBJECT_FIELDS
            unknown = set(envelope) - known_fields
            if unknown:
                known = self._known_decision_snapshot(envelope)
                result = self._restricted(True, False, "moderation_field_unrecognized",
                    known.version, known.decision,
                    failure_field_path=base + ".unknown_field",
                    expected_type="known_fields", actual_type="object",
                    required_fields_present=present, extra_fields_sha256=extra,
                    **_unknown_key_diagnostics(envelope,
                        known_fields,
                        "moderation_envelope"),
                    **self._known_decision_fields(known))
                self.malformed = result
                return result
            invalid_object_field = next((field for field in sorted(_NON_DISPLAY_OBJECT_FIELDS)
                if field in envelope and not isinstance(envelope[field], dict)), None)
            if invalid_object_field is not None:
                known = self._known_decision_snapshot(envelope)
                result = self._restricted(True, False, "moderation_ancillary_format_invalid",
                    known.version, known.decision,
                    failure_field_path=base + "." + invalid_object_field,
                    expected_type="object", actual_type=_type_name(envelope[invalid_object_field]),
                    required_fields_present=present, extra_fields_sha256=extra,
                    **self._known_decision_fields(known))
                self.malformed = result
                return result
            if "presentation" in envelope and envelope["presentation"] != "inline":
                known = self._known_decision_snapshot(envelope)
                result = self._restricted(True, False, "moderation_presentation_unsupported",
                    known.version, known.decision,
                    failure_field_path=base + ".presentation",
                    expected_type="literal_inline",
                    actual_type=_type_name(envelope["presentation"]),
                    required_fields_present=present, extra_fields_sha256=extra,
                    **self._known_decision_fields(known))
                self.malformed = result
                return result
            if not marker_present or marker is None:
                presence = "explicit_null" if marker_present else "field_missing"
                result = DisplayResult(True, False, None, "none", "pending",
                    "optional_decision_absent", required_fields_present=present,
                    extra_fields_sha256=extra, answer_binding_status="pending",
                    decision_presence=presence)
                self.optional_decisions.append(result)
                return result
        bad = None
        if not isinstance(envelope, dict):
            bad = (base, "object", envelope)
        elif not isinstance(marker, dict):
            bad = (base + ".final_answer_moderation", "object", marker)
        elif not isinstance(marker.get("item_id"), str) or not marker["item_id"]:
            bad = (base + ".final_answer_moderation.item_id", "nonempty_string",
                   marker.get("item_id"))
        elif type(marker.get("moderated")) is not bool:
            bad = (base + ".final_answer_moderation.moderated", "boolean",
                   marker.get("moderated"))
        if bad is not None:
            result = self._restricted(True, False, "moderation_format_invalid",
                failure_field_path=bad[0], expected_type=bad[1],
                actual_type=_type_name(bad[2]), required_fields_present=present,
                extra_fields_sha256=extra, answer_binding_status="not_checked_format_invalid",
                decision_presence="present")
            self.malformed = result
            return result
        item_id = marker["item_id"]
        item_hash = hashlib.sha256(item_id.encode("utf-8", "surrogatepass")).hexdigest()
        display = marker.get("display")
        if not isinstance(display, dict) or type(display.get("version")) is not int:
            path = base + ".final_answer_moderation.display"
            if isinstance(display, dict):
                path += ".version"
            result = self._restricted(True, False, "display_format_invalid",
                failure_field_path=path, expected_type=("integer" if
                    isinstance(display, dict) else "object"),
                actual_type=_type_name(display.get("version") if
                    isinstance(display, dict) else display))
        elif display["version"] != 1:
            result = self._restricted(True, False, "display_version_unsupported",
                display["version"], failure_field_path=base +
                ".final_answer_moderation.display.version",
                expected_type="supported_version_1", actual_type="integer")
        elif display.get("mode") == "show":
            if display.get("trustedContacts") not in ("skip", "check") or \
                    set(display) - {"mode", "version", "trustedContacts", "annotation"}:
                has_extra = bool(set(display) - {"mode", "version", "trustedContacts", "annotation"})
                result = self._restricted(True, False, "display_format_invalid", 1, "show",
                    failure_field_path=(base + ".final_answer_moderation.display." +
                        ("unknown_field" if has_extra else "trustedContacts")),
                    expected_type="known_fields" if has_extra else "skip_or_check",
                    actual_type="object" if has_extra else _type_name(display.get("trustedContacts")),
                    extra_fields_sha256=_extra_hash(display,
                        {"mode", "version", "trustedContacts", "annotation"}),
                    **(_unknown_key_diagnostics(display,
                        {"mode", "version", "trustedContacts", "annotation"},
                        "display_decision") if has_extra else {}))
            elif display.get("trustedContacts") != "skip" or \
                    display.get("annotation") is not None:
                result = self._restricted(True, True, "show_requirements_unsupported", 1, "show",
                    failure_field_path=(base + ".final_answer_moderation.display." +
                        ("trustedContacts" if display.get("trustedContacts") != "skip"
                         else "annotation")), expected_type="supported_requirement",
                    actual_type="unsupported_requirement")
            else:
                result = DisplayResult(True, True, 1, "show", "allow", "plain_show")
        elif display.get("mode") in ("hide", "block"):
            mode = display["mode"]
            detail = display.get("replacement" if mode == "hide" else "reason")
            valid = isinstance(detail, dict) and (
                detail.get("kind") == "wellbeingPromptHelpline" if mode == "hide" else
                detail.get("kind") in ("protection", "cyber"))
            result = self._restricted(True, valid,
                f"{mode}_restricted" if valid else "display_format_invalid", 1, mode,
                **({} if valid else {"failure_field_path": base +
                    ".final_answer_moderation.display." +
                    ("replacement" if mode == "hide" else "reason"),
                    "expected_type": "supported_restriction_object",
                    "actual_type": _type_name(detail)}))
        else:
            result = self._restricted(True, False, "display_mode_unsupported", 1,
                failure_field_path=base + ".final_answer_moderation.display.mode",
                expected_type="show_hide_or_block",
                actual_type=_type_name(display.get("mode")))
        result = replace(result, item_hash=item_hash,
                         required_fields_present=present,
                         extra_fields_sha256=result.extra_fields_sha256 or extra,
                         decision_presence="present")
        self.decisions.append((item_id, result))
        return result

    def matches_tool_proposal(self, calls: dict) -> bool:
        """A textless completed turn may proceed only to ordinary tool validation."""
        if (not calls or self.items or self.text_item_ids or self.malformed or self.decisions
                or len(self.optional_decisions) > 1):
            return False
        actual = [(call["id"], call["function"]["name"], call["function"]["arguments"])
                  for _, call in sorted(calls.items())]
        return actual == self.terminal_tools and len({call[0] for call in actual}) == len(actual)

    def _item(self, item: Any) -> None:
        if not isinstance(item, dict) or item.get("type") != "message":
            return
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            self.items[item_id] = item.get("phase") if isinstance(item.get("phase"), str) else None

    def finish(self, *, model_completed: bool, public_text: str) -> DisplayResult:
        if self.malformed:
            return self.malformed
        presence = (self.optional_decisions[0].decision_presence
                    if len(self.optional_decisions) == 1 else
                    "multiple_optional" if self.optional_decisions else "metadata_absent")
        if not model_completed:
            return self._restricted(bool(self.decisions or self.optional_decisions),
                                    bool(self.decisions),
                                    "model_not_completed", decision_presence=presence)
        if len(self.decisions) > 1 or len(self.optional_decisions) > 1 or \
                (self.decisions and self.optional_decisions):
            return self._restricted(True, False, "moderation_duplicate_or_conflicting",
                                    decision_presence="multiple")
        if not self.decisions:
            item_id = next(iter(self.items)) if len(self.items) == 1 else None
            if item_id is None or self.items[item_id] != "final_answer" or \
                    self.text_item_ids != {item_id} or \
                    self.completed_text.get(item_id) != public_text or not public_text:
                return self._restricted(bool(self.optional_decisions),
                    False, "answer_binding_invalid",
                    decision_presence=presence, answer_binding_status="mismatch")
            return DisplayResult(bool(self.optional_decisions), False, None, "none",
                "allow", "protocol_default_display_no_explicit_decision",
                answer_binding_status="matched", decision_presence=presence,
                required_fields_present=(self.optional_decisions[0].required_fields_present
                    if self.optional_decisions else None),
                extra_fields_sha256=(self.optional_decisions[0].extra_fields_sha256
                    if self.optional_decisions else None))
        item_id, result = self.decisions[0]
        if len(self.items) != 1 or self.items.get(item_id) != "final_answer" or \
                self.text_item_ids != {item_id} or \
                self.completed_text.get(item_id) != public_text:
            return replace(self._restricted(True, result.parsed,
                "answer_binding_invalid", result.version, result.decision),
                item_hash=result.item_hash, required_fields_present=result.required_fields_present,
                extra_fields_sha256=result.extra_fields_sha256,
                answer_binding_status="mismatch")
        return replace(result, answer_binding_status="matched")
