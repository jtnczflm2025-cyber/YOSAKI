"""Plain, deterministic V2 HTTP SpeechPlan. No LLM or psychology calls."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .readable_text import public_paragraphs


PLANNER_VERSION = "v2a-plain-2"
MAX_SEGMENT = 2_000
MAX_TOTAL = 8_000
MAX_SEGMENTS = 8
_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fd-\u30ff]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
# These simplified forms are not ordinary Japanese kanji. Han-only text without
# one of them stays unforced, since Chinese and Japanese can share characters.
_ZH_MARKERS = frozenset("你这话说让听们没过时发为请间应汉语吗书见对还给经长问开关样总")
_SENTENCE_END = frozenset("。！？!?")
_SOFT_END = frozenset("，、；;,:：")
_CLOSERS = frozenset('”"』）')


class SpeechPlanError(ValueError):
    pass


def _safe_cut(text: str, limit: int) -> int:
    """Do not split a combining sequence or a joined emoji at the hard limit."""
    end = min(limit, len(text))
    while end > 0 and end < len(text) and (
        unicodedata.combining(text[end]) or text[end] in "\ufe0e\ufe0f\u200d" or
        text[end - 1] == "\u200d"
    ):
        end -= 1
    if end == 0:
        raise SpeechPlanError("朗读文字无法安全拆分")
    return end


def _preferred_cut(text: str, limit: int) -> int:
    """Split a long paragraph at a late sentence, phrase, or safe hard edge."""
    end = _safe_cut(text, limit)
    start = max(1, end // 2)
    for punctuation in (_SENTENCE_END, _SOFT_END):
        candidates = []
        for index in range(start - 1, end):
            if text[index] not in punctuation:
                continue
            cut = index + 1
            while cut < end and text[cut] in _CLOSERS:
                cut += 1
            candidates.append(cut)
        if candidates:
            return max(candidates)
    spaces = [index + 1 for index in range(start - 1, end) if text[index].isspace()]
    return max(spaces) if spaces else end


def plan(body: str, skipped_reasons: set[str] | None = None) -> list[str]:
    paragraphs = public_paragraphs(body, skipped_reasons)
    segments: list[str] = []
    pending = ""
    for paragraph in paragraphs:
        while paragraph:
            separator = "\n\n" if pending else ""
            if len(pending) + len(separator) + len(paragraph) <= MAX_SEGMENT:
                pending += separator + paragraph
                break
            if pending:
                segments.append(pending)
                pending = ""
                continue
            cut = _preferred_cut(paragraph, MAX_SEGMENT)
            segments.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip()
    if pending:
        segments.append(pending)
    if not segments:
        raise SpeechPlanError("没有可安全朗读的公开正文")
    if len(segments) > MAX_SEGMENTS or sum(map(len, segments)) > MAX_TOTAL:
        raise SpeechPlanError("朗读内容超过单次上限")
    return segments


def request_language(segments: list[str]) -> tuple[str, str]:
    """Return the effective request hint, independent of the clone's source language.

    ``auto`` means omit the optional provider language_code. Shared Han-only text,
    mixed scripts and unsupported languages do not get a guessed forced language.
    """
    text = "\n".join(segments)
    has_kana = bool(_KANA.search(text))
    has_zh_marker = any(char in _ZH_MARKERS for char in text)
    if _LATIN_WORD.search(text) and (has_kana or has_zh_marker):
        return "auto", "mixed_or_ambiguous"
    if has_kana and has_zh_marker:
        return "auto", "mixed_or_ambiguous"
    if has_kana:
        return "ja", "japanese_kana"
    if has_zh_marker:
        return "zh", "simplified_chinese"
    return "auto", "undetermined"


def digest(message_id: int, body: str, status: str | None, revision: int,
           segments: list[str], *, voice_id: str, model_id: str,
           output_format: str, language_code: str,
           reference_language: str | None = None) -> str:
    value = {"message_id": message_id, "body": body, "status": status,
             "revision": revision, "segments": segments, "planner": PLANNER_VERSION,
             "voice_id_sha256": hashlib.sha256(voice_id.encode("utf-8")).hexdigest(),
             "model_id": model_id, "output_format": output_format,
             "language_code": language_code,
             "reference_language": reference_language}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
