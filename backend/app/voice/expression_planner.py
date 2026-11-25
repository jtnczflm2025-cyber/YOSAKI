"""Isolated V3 expression planning after the V2 public-text safety boundary.

This module has no provider, database or psychology dependency.  A source
bracket sequence never becomes an instruction: only an explicitly selected
mode may add one of the fixed, reviewed tags after safe extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .speech_planner import MAX_SEGMENT, SpeechPlanError, plan, request_language


EXPRESSION_PLANNER_VERSION = "v3-expression-isolated-1"
ExpressionMode = Literal["none", "thoughtful", "relaxed"]
TAGS: dict[str, str] = {
    "none": "",
    "thoughtful": "[thoughtful] ",
    "relaxed": "[relaxed] ",
}


@dataclass(frozen=True)
class ExpressionPlan:
    mode: ExpressionMode
    tag: str | None
    safe_text: str
    request_text: str
    skipped_reasons: tuple[str, ...]
    language: str
    language_reason: str

    @property
    def characters(self) -> int:
        return len(self.request_text)


def build(body: str, mode: ExpressionMode) -> ExpressionPlan:
    """Extract the saved public body first, then add one controlled prefix.

    The first isolated exercise is one HTTP TTD input only.  Long messages and
    multi-segment expression planning belong to a later, separately reviewed
    scope; rejecting them avoids silently changing V2 chunk behavior.
    """
    if mode not in TAGS:
        raise SpeechPlanError("不支持的表达模式")
    skipped: set[str] = set()
    segments = plan(body, skipped)
    if len(segments) != 1:
        raise SpeechPlanError("首版表达评测仅支持单片段正文")
    safe_text = segments[0]
    tag = TAGS[mode]
    request_text = tag + safe_text
    if len(request_text) > MAX_SEGMENT:
        raise SpeechPlanError("表达标签使请求超过单片段上限")
    language, reason = request_language([safe_text])
    return ExpressionPlan(mode, tag.strip() or None, safe_text, request_text,
                          tuple(sorted(skipped)), language, reason)
