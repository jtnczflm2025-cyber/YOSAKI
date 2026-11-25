"""Strict Chronicle adapter for the bracketed, timestamped Markdown export.

Only the header and the standalone ``---`` separator are structural. Every
character between the blank line after a header and the next separator stays
in the message body, including trailing newlines and indentation.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import HTTPException


PARSER_VERSION = "chronicle-bracket-md-v1"
_HEADER = re.compile(r"\*\*\[(用户|阿夜)\]\*\* _(\d{4}-\d{2}-\d{2} \d{2}:\d{2})_")
_SIGNATURE = re.compile(r"(?m)^---\r?\n\r?\n\*\*\[")
_LEGACY_HEADER = re.compile(r"(?m)^## (?:用户|助手)(?: ·|\s*$)")


def recognizes(name: str, data: bytes) -> bool:
    if not name.lower().endswith(".md"):
        return False
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return not _LEGACY_HEADER.search(text) and bool(_SIGNATURE.search(text))


def parse(data: bytes, *, max_messages: int) -> tuple[str, list[dict], list[dict]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Chronicle Markdown 必须是 UTF-8") from exc
    if "\r" in text.replace("\r\n", "") or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise HTTPException(422, "Chronicle Markdown 换行符混用或包含独立 CR")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if not lines:
        raise HTTPException(422, "Chronicle Markdown 缺少标题")

    def value(index: int) -> str:
        line = lines[index]
        return line[:-len(newline)] if line.endswith(newline) else line

    if not value(0).startswith("# ") or not value(0)[2:] or len(value(0)[2:]) > 120:
        raise HTTPException(422, "Chronicle Markdown 标题缺失或过长")
    title = value(0)[2:]
    try:
        first_separator = next(index for index in range(1, len(lines)) if value(index) == "---")
    except StopIteration as exc:
        raise HTTPException(422, "Chronicle Markdown 缺少首个消息分隔符") from exc
    if (first_separator + 2 >= len(lines) or value(first_separator + 1) != ""
            or not value(first_separator + 2).startswith("**[")):
        raise HTTPException(422, "Chronicle Markdown 首个消息边界不完整")

    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    rows: list[dict] = []
    locations: list[dict] = []
    cursor = first_separator + 2
    while cursor < len(lines):
        header = _HEADER.fullmatch(value(cursor))
        if header is None:
            raise HTTPException(422, f"Chronicle Markdown 第 {cursor + 1} 行消息标记无效")
        try:
            datetime.fromisoformat(header.group(2))
        except ValueError as exc:
            raise HTTPException(422, f"Chronicle Markdown 第 {cursor + 1} 行时间无效") from exc
        if cursor + 1 >= len(lines) or value(cursor + 1) != "":
            raise HTTPException(422, f"Chronicle Markdown 第 {cursor + 1} 行后缺少正文分隔空行")
        body_start = cursor + 2
        boundary = next((index for index in range(body_start, len(lines)) if value(index) == "---"), len(lines))
        if boundary < len(lines) and (boundary + 2 >= len(lines) or value(boundary + 1) != ""
                                      or not value(boundary + 2).startswith("**[")):
            raise HTTPException(422, f"Chronicle Markdown 第 {boundary + 1} 行边界不明确")
        body = text[starts[body_start]:starts[boundary]]
        rows.append({"role": "user" if header.group(1) == "用户" else "assistant",
                     "created_at": header.group(2), "content": body})
        locations.append({"headerLine": cursor + 1, "bodyStartLine": body_start + 1,
                          "bodyEndLine": boundary if boundary < len(lines) else len(lines),
                          "bodyStartChar": starts[body_start], "bodyEndChar": starts[boundary]})
        if len(rows) > max_messages:
            raise HTTPException(413, "Chronicle Markdown 消息超过数量上限")
        cursor = boundary + 2 if boundary < len(lines) else len(lines)
    if not rows:
        raise HTTPException(422, "Chronicle Markdown 没有消息")
    return title, rows, locations
