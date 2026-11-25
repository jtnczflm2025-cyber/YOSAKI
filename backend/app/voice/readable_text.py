"""Conservative, static extraction of public speech from a saved Markdown body."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt


_PARSER = MarkdownIt("commonmark", {"html": True})
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CONTROL = re.compile(r"</?(?:inner|say)\b", re.IGNORECASE)
_JSON_LINE = re.compile(r"^\s*(?:\{.*\}|\[\s*\{.*\}\s*\])\s*$", re.DOTALL)
_TOOL_ARGS = re.compile(r"\b(?:arguments|tool_calls|function_call)\b\s*[:=]\s*[\[{]", re.IGNORECASE)
_SOURCE_AUDIO_TAG = re.compile(
    r"\[(?:whispers|whispering|laughs|laughing|chuckles|sighs|shouts|shouting|"
    r"cries|crying|sobbing|gasps|clears throat)\]", re.IGNORECASE,
)


def public_paragraphs(body: str, skipped_reasons: set[str] | None = None) -> list[str]:
    """Return only text tokens proven safe to send for speech.

    A paragraph containing raw HTML or an internal delimiter is dropped in
    full: keeping its surrounding text could disclose content inside markup.
    """
    if not body or len(body) > 200_000:
        return []
    output: list[str] = []
    try:
        tokens = _PARSER.parse(body)
    except Exception:
        if skipped_reasons is not None:
            skipped_reasons.add("无法确认的 Markdown 结构")
        return []
    for token in tokens:
        if token.type in ("fence", "code_block", "html_block"):
            if skipped_reasons is not None:
                skipped_reasons.add("代码或 HTML")
            continue
        if token.type != "inline" or not token.children:
            continue
        children = token.children
        if any(child.type == "html_inline" for child in children):
            if skipped_reasons is not None:
                skipped_reasons.add("HTML 或控制标记")
            continue
        if (_CONTROL.search(token.content) or _JSON_LINE.fullmatch(token.content) or
                _TOOL_ARGS.search(token.content)):
            if skipped_reasons is not None:
                skipped_reasons.add("非正文结构")
            continue
        parts: list[str] = []
        for child in children:
            if child.type == "text":
                parts.append(child.content)
            elif child.type in ("softbreak", "hardbreak"):
                parts.append(" ")
            elif skipped_reasons is not None and child.type in ("code_inline", "image", "link_open"):
                skipped_reasons.add("代码、图片或链接地址")
            # code_inline, image, link targets and HTML are never copied.
        visible = "".join(parts)
        without_tags = _SOURCE_AUDIO_TAG.sub("", visible)
        if skipped_reasons is not None and without_tags != visible:
            skipped_reasons.add("来源 Audio Tags")
        without_brackets = without_tags.replace("[", "").replace("]", "")
        if skipped_reasons is not None and without_brackets != without_tags:
            skipped_reasons.add("方括号标记")
        text = _URL.sub("", without_brackets)
        if skipped_reasons is not None and text != without_brackets:
            skipped_reasons.add("网址")
        text = re.sub(r"\s+", " ", text).strip()
        if text and not _CONTROL.search(text):
            output.append(text)
    return output
