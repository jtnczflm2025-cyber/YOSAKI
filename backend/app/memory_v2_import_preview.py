"""Read-only, bounded preview of candidate Chronicle chat archives.

This module never imports a source or treats a parser's fallback timestamp as an
event timestamp. Keep the existing /api/archives behavior separate.
"""

import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath

from fastapi import HTTPException

from . import archives
from . import memory_v2_import_markdown as chronicle_markdown


PARSER_VERSION = "chronicle-preview-v1"
MAX_FILES = 200
MAX_RATIO = 100
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_EXTENSIONS = {".json", ".md", ".txt"}


def parse_source(name, data):
    """One versioned parser selection for both preview and staged import."""
    if len(data) > archives.MAX_FILE:
        raise HTTPException(413, "聊天档案文件超过 8 MiB")
    if chronicle_markdown.recognizes(name, data):
        title, rows, locations = chronicle_markdown.parse(data, max_messages=archives.MAX_MESSAGES)
        return {"title": title, "rows": rows, "locations": locations,
                "format": "chronicle_markdown", "parserVersion": chronicle_markdown.PARSER_VERSION,
                "conversionNote": "structural_header_and_separator_only", "bodyLossless": True}
    title, rows = archives.load_file(name, data)
    fmt = name.rsplit(".", 1)[-1].lower()
    return {"title": title, "rows": rows, "locations": [None] * len(rows),
            "format": fmt, "parserVersion": PARSER_VERSION,
            "conversionNote": "lossy_whitespace" if fmt in ("md", "txt") else "none",
            "bodyLossless": fmt == "json"}


def snapshot_fingerprint(parser_version, file_sha, items):
    payload = json.dumps({"parserVersion": parser_version, "fileSha256": file_sha, "items": items},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def batch_preview_fingerprint(reports):
    payload = [{"characterId": report["characterId"], "name": report["name"],
                "title": report["title"], "conversionNote": report["conversionNote"],
                "fileSha256": report["fileSha256"],
                "parserVersion": report["parserVersion"], "previewFingerprint": report["previewFingerprint"]}
               for report in reports]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _time(value, confirmed_offset_minutes=None):
    result = {"original": value, "precision": "unknown", "occurredAt": None,
              "occurredDate": None, "timezoneBasis": "none"}
    if not value:
        return result
    if not isinstance(value, str):
        raise ValueError("消息时间必须是文本")
    try:
        if _DATE.fullmatch(value):
            result["occurredDate"] = date.fromisoformat(value).isoformat()
            result["precision"] = "day"
            return result
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("消息时间格式错误") from exc
    if parsed.tzinfo is None:
        result["occurredDate"] = parsed.date().isoformat()
        result["precision"] = "local_datetime_unverified"
        if confirmed_offset_minutes is None:
            return result
        if type(confirmed_offset_minutes) is not int or not -840 <= confirmed_offset_minutes <= 840:
            raise ValueError("确认的时区偏移无效")
        parsed = parsed.replace(tzinfo=timezone(timedelta(minutes=confirmed_offset_minutes)))
        result["timezoneBasis"] = "user_confirmed_offset"
    else:
        result["timezoneBasis"] = "source_offset"
    result["precision"] = "instant"
    result["occurredAt"] = parsed.isoformat()
    result["occurredDate"] = parsed.date().isoformat()
    return result


def _metadata(name, data):
    if not name.lower().endswith(".json"):
        return None, None, None
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, None, None  # load_file supplies the public format error.
    if not isinstance(document, dict):
        return None, None, None
    conversation = document.get("conversation")
    conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
    if conversation_id is not None and (isinstance(conversation_id, bool) or
                                        not isinstance(conversation_id, (str, int)) or
                                        len(str(conversation_id)) > 200):
        raise HTTPException(422, "外部会话 ID 格式错误或过长")
    return (str(conversation_id) if conversation_id is not None else None,
            document.get("character"), document.get("messages"))


def preview_file(name, data, db=None, character_id=None, confirmed_offset_minutes=None,
                 container_sha256=None, parsed_source=None):
    """Return parser evidence and bounded samples, without writing to SQLite."""
    if confirmed_offset_minutes is not None and (type(confirmed_offset_minutes) is not int
                                                 or not -840 <= confirmed_offset_minutes <= 840):
        raise HTTPException(422, "确认的时区偏移无效")
    parsed = parsed_source if parsed_source is not None else parse_source(name, data)
    title, rows = parsed["title"], parsed["rows"]
    fmt = parsed["format"]
    external_conversation_id, source_character, original_rows = _metadata(name, data)
    warnings = []
    if fmt in ("md", "txt"):
        warnings.append("有损格式：原解析器会整理正文边缘空白，且不包含稳定消息 ID")
    if fmt == "chronicle_markdown":
        warnings.append("Chronicle Markdown：仅移除独立标题、消息标记、标记后的空行及 --- 分隔行；正文区间的首尾空白与换行原样保留；没有外部消息 ID")
    if source_character is not None:
        warnings.append("档案角色名称仅供核对，不自动映射到当前角色")
    messages = []
    for ordinal, row in enumerate(rows, 1):
        original = original_rows[ordinal - 1] if isinstance(original_rows, list) and len(original_rows) == len(rows) else None
        external_id = original.get("id") if isinstance(original, dict) else None
        if external_id is not None and (isinstance(external_id, bool) or not isinstance(external_id, (str, int))
                                        or len(str(external_id)) > 200):
            raise HTTPException(422, f"第 {ordinal} 条消息来源 ID 格式错误")
        try:
            time = _time(row.get("created_at"), confirmed_offset_minutes)
        except ValueError as exc:
            raise HTTPException(422, f"第 {ordinal} 条：{exc}") from exc
        if row.get("generation_status") == "imported":
            # A legacy export may serialize the old import fallback as created_at.
            time = {"original": row.get("created_at"), "precision": "unknown", "occurredAt": None,
                    "occurredDate": None, "timezoneBasis": "legacy_import_unverified"}
            warnings.append("旧导入消息的 created_at 可能是导入时刻，不作为发生时间")
        if time["precision"] in ("day", "local_datetime_unverified", "unknown"):
            warnings.append("部分消息没有可确认的精确发生时刻；不会用于 30 分钟自动切段")
        messages.append({"ordinal": ordinal, "role": row["role"], "externalMessageId": str(external_id) if external_id is not None else None,
                         "contentSha256": hashlib.sha256(row["content"].encode()).hexdigest(),
                         "time": time, "indexedTextEligible": row["role"] in ("user", "assistant"),
                         "contentPreview": row["content"][:240] if ordinal <= 20 and row["role"] in ("user", "assistant") else None,
                         "sourceLocation": parsed["locations"][ordinal - 1]})
    file_sha = hashlib.sha256(data).hexdigest()
    staged_items = [{"row": row, "ordinal": item["ordinal"],
                     "externalMessageId": item["externalMessageId"], "time": item["time"]}
                    for row, item in zip(rows, messages)]
    fingerprint = snapshot_fingerprint(parsed["parserVersion"], file_sha, staged_items)
    legacy_duplicate = False
    chronicle_duplicate = False
    external_conflicts = []
    if db is not None:
        legacy_duplicate = bool(db.execute("SELECT 1 FROM imported_archives WHERE digest=?", (file_sha,)).fetchone())
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv2_import_files'").fetchone():
            chronicle_duplicate = bool(db.execute("SELECT 1 FROM mv2_import_files WHERE character_id=? AND file_sha256=?",
                                                  (character_id, file_sha)).fetchone())
            if external_conversation_id is not None:
                candidates = [m["externalMessageId"] for m in messages if m["externalMessageId"] is not None]
                for external_id in candidates:
                    if db.execute("""SELECT 1 FROM mv2_import_identity_claims WHERE character_id=?
                        AND external_conversation_id=? AND external_message_id=?""",
                        (character_id, external_conversation_id, external_id)).fetchone() or db.execute("""SELECT 1 FROM mv2_import_receipts WHERE character_id=?
                        AND external_conversation_id=? AND external_message_id=?""",
                        (character_id, external_conversation_id, external_id)).fetchone():
                        external_conflicts.append(external_id)
    return {"name": name, "format": fmt, "title": title, "parserVersion": parsed["parserVersion"],
            "fileSha256": file_sha, "externalConversationId": external_conversation_id,
            "containerSha256": container_sha256,
            "sourceCharacter": source_character, "characterId": character_id,
            "messageCount": len(rows), "messages": messages, "warnings": sorted(set(warnings)),
            "legacyDuplicate": legacy_duplicate, "chronicleDuplicate": chronicle_duplicate,
            "externalIdentityConflicts": external_conflicts[:20], "lossless": fmt == "json",
            "bodyLossless": parsed["bodyLossless"], "conversionNote": parsed["conversionNote"],
            "previewFingerprint": fingerprint}


def members(name, data):
    """Yield validated (source path, bytes) without extracting files to disk."""
    if not name.lower().endswith(".zip"):
        if len(data) > archives.MAX_FILE:
            raise HTTPException(413, "聊天档案文件超过 8 MiB")
        return [(name, data, None)]
    if len(data) > archives.MAX_TOTAL:
        raise HTTPException(413, "ZIP 上传超过 64 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as packed:
            infos = packed.infolist()
            if not infos or len(infos) > MAX_FILES:
                raise HTTPException(413, "ZIP 文件数量无效或超过 200")
            names = set()
            total = 0
            for item in infos:
                path = PurePosixPath(item.filename.replace("\\", "/"))
                if (item.is_dir() or item.filename.startswith(("/", "\\")) or ".." in path.parts
                        or ":" in item.filename or path.suffix.lower() not in _EXTENSIONS
                        or item.flag_bits & 0x1 or item.file_size > archives.MAX_FILE
                        or item.compress_size == 0 and item.file_size > 0
                        or item.file_size > 64 * 1024 and item.file_size > item.compress_size * MAX_RATIO):
                    raise HTTPException(422, "ZIP 包含不安全、不支持或超限的成员")
                folded = str(path).casefold()
                if folded in names:
                    raise HTTPException(422, "ZIP 包含重复路径")
                names.add(folded)
                total += item.file_size
                if total > archives.MAX_TOTAL:
                    raise HTTPException(413, "ZIP 解压后超过 64 MiB")
            result = []
            for item in infos:
                with packed.open(item) as member:
                    body = member.read(archives.MAX_FILE + 1)
                if len(body) != item.file_size or len(body) > archives.MAX_FILE:
                    raise HTTPException(422, "ZIP 成员大小与声明不一致")
                result.append((f"{name}!{item.filename}", body, hashlib.sha256(data).hexdigest()))
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise HTTPException(422, "ZIP 结构或校验错误") from exc


def preview_upload(name, data, db=None, character_id=None, confirmed_offset_minutes=None):
    return [preview_file(path, body, db, character_id, confirmed_offset_minutes, container_sha)
            for path, body, container_sha in members(name, data)]
