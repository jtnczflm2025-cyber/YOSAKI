"""Read-only, conservative adapters from YOSAKI evidence to M1 safe text."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

KINDS = ("message", "activity_operation")
LOCAL_ACTIONS = {"inspect:map": "查看地图", "inspect:notes": "查看笔记", "compare:map-notes": "对照地图笔记"}
STATUS_LABELS = {"succeeded": "成功", "failed": "失败", "unknown": "结果未知", "prepared": "准备中",
                 "approved": "已批准", "pending_confirmation": "待确认", "running": "执行中",
                 "rejected": "已拒绝", "cancelled": "已取消"}


@dataclass(frozen=True)
class Source:
    kind: str
    source_id: str
    character_id: int
    stream_kind: str
    stream_id: str
    role: str
    status: str
    evidence_kind: str
    text: str
    occurred_at: str | None
    recorded_at: str | None
    outcome_recorded_at: str | None
    time_basis: str
    content_hash: str
    state_hash: str
    occurred_date: str | None = None
    time_precision: str = "unknown"
    archive_file_id: str | None = None
    archive_ordinal: int | None = None


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized(text):
    return unicodedata.normalize("NFKC", text).casefold()


def _cjk(char):
    code = ord(char)
    return (0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or
            0x3040 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF)


def terms(text):
    text = normalized(text)
    found = set()
    run, kind = "", None
    def flush():
        if not run:
            return
        if kind == "cjk":
            found.update(run[i:i + 2] for i in range(len(run) - 1))
            if len(run) == 1:
                found.add(run)
        else:
            found.add(run)
    for char in text + " ":
        new_kind = "cjk" if _cjk(char) else "word" if (char.isascii() and char.isalnum()) else None
        if new_kind != kind:
            flush()
            run, kind = "", new_kind
        if new_kind:
            run += char
    return found


def query_terms(text):
    clean = normalized(text.strip())
    if len("".join(c for c in clean if c.isalnum())) < 2:
        return set()
    return terms(clean)


def verified_archive_file(db, file_id, cache=None):
    """Validate the whole completed H2 file, not just one receipt."""
    if cache is not None and file_id in cache:
        return cache[file_id]
    file = db.execute("""SELECT f.*,b.character_id AS batch_character,b.total AS batch_total,
        b.processed AS batch_processed,b.status AS batch_status,b.plan_version AS batch_plan_version,
        (SELECT COALESCE(SUM(total),0) FROM mv2_import_files WHERE batch_id=b.id) AS manifest_total
        FROM mv2_import_files f JOIN mv2_import_batches b ON b.id=f.batch_id
        WHERE f.id=?""", (file_id,)).fetchone()
    valid = False
    if (file is not None and file["status"] == "raw_complete" and file["cursor"] == file["total"]
            and file["total"] > 0 and file["character_id"] == file["batch_character"]
            and file["batch_status"] in ("running", "paused", "raw_complete")
            and file["batch_plan_version"] >= 1 and file["batch_total"] == file["manifest_total"]
            and file["batch_processed"] >= file["cursor"]
            and file["conversation_id"] is not None and len(file["file_sha256"]) == 64
            and bool(file["parser_version"])):
        aggregate = db.execute("""SELECT COUNT(*) AS n,COUNT(DISTINCT r.ordinal) AS distinct_n,
            MIN(r.ordinal) AS first_ordinal,MAX(r.ordinal) AS last_ordinal,
            SUM(CASE WHEN r.character_id<>? OR r.file_sha256<>? OR r.parser_version<>?
                OR r.external_conversation_id IS NOT f.external_conversation_id
                OR m.id IS NULL OR m.conversation_id<>f.conversation_id
                OR c.character_id<>f.character_id OR c.archive_only<>1 THEN 1 ELSE 0 END) AS mismatches
            FROM mv2_import_files f JOIN mv2_import_receipts r ON r.file_id=f.id
            LEFT JOIN messages m ON m.id=r.message_id
            LEFT JOIN conversations c ON c.id=m.conversation_id WHERE f.id=?""",
            (file["character_id"], file["file_sha256"], file["parser_version"], file_id)).fetchone()
        valid = (aggregate["n"] == file["total"] and aggregate["distinct_n"] == file["total"]
                 and aggregate["first_ordinal"] == 1 and aggregate["last_ordinal"] == file["total"]
                 and aggregate["mismatches"] == 0)
    if cache is not None:
        cache[file_id] = valid
    return valid


def source(db, kind, source_id, file_cache=None):
    if kind == "message":
        try:
            numeric_id = int(source_id)
            if str(numeric_id) != source_id:
                return None
        except (TypeError, ValueError):
            return None
        has_import = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv2_import_receipts'").fetchone())
        has_archive_column = any(column[1] == "archive_only" for column in db.execute("PRAGMA table_info(conversations)"))
        receipt_join = """LEFT JOIN mv2_import_receipts r ON r.message_id=m.id
            LEFT JOIN mv2_import_files f ON f.id=r.file_id""" if has_import else ""
        receipt_columns = ",r.occurred_at AS receipt_occurred,r.recorded_at AS receipt_recorded,r.content_sha256 AS receipt_content_hash,r.time_precision AS receipt_precision,r.occurred_date AS receipt_date,r.character_id AS receipt_character,r.file_id AS receipt_file_id,r.ordinal AS receipt_ordinal,r.file_sha256 AS receipt_file_sha,r.parser_version AS receipt_parser,r.original_time_text AS receipt_raw_time,r.timezone_basis AS receipt_timezone_basis,f.status AS import_file_status,f.character_id AS import_file_character,f.conversation_id AS import_file_conversation,f.file_sha256 AS import_file_sha,f.parser_version AS import_file_parser,f.cursor AS import_file_cursor,f.total AS import_file_total,f.batch_id AS import_batch_id" if has_import else ""
        archive_column = ",c.archive_only AS archive_only" if has_archive_column else ""
        row = db.execute(f"""SELECT m.*,c.character_id,ch.enabled AS character_enabled,a.imported_at
            {receipt_columns}{archive_column}
            FROM messages m JOIN conversations c ON c.id=m.conversation_id
            JOIN characters ch ON ch.id=c.character_id
            LEFT JOIN imported_archives a ON a.conversation_id=c.id
            {receipt_join} WHERE m.id=?""", (numeric_id,)).fetchone()
        from .chat_history import source_allowed
        if row is not None and not source_allowed(db,numeric_id,row["conversation_id"]):
            return None
        if row is None or not row["character_enabled"] or row["role"] not in ("user", "assistant"):
            return None
        imported = row["generation_status"] == "imported"
        archive_only = has_archive_column and bool(row["archive_only"])
        if archive_only:
            if (not imported or not has_import or row["import_file_status"] != "raw_complete"
                    or row["receipt_character"] != row["character_id"]
                    or row["import_file_character"] != row["character_id"]
                    or row["import_file_conversation"] != row["conversation_id"]
                    or row["receipt_file_sha"] != row["import_file_sha"]
                    or row["receipt_parser"] != row["import_file_parser"]
                    or type(row["receipt_ordinal"]) is not int or row["receipt_ordinal"] < 1
                    or not verified_archive_file(db, row["receipt_file_id"], file_cache)
                    or row["receipt_content_hash"] != digest(row["content"])):
                return None
            recorded, occurred = row["receipt_recorded"], row["receipt_occurred"]
        else:
            recorded = row["imported_at"] if imported else row["created_at"]
            occurred = None if imported else row["created_at"]
        payload = dict(row)
        if not archive_only:
            # Installing the optional import schema must not invalidate M1's
            # existing native-message state hashes with newly joined NULLs.
            for field in ("receipt_occurred", "receipt_recorded", "receipt_content_hash",
                          "receipt_precision", "receipt_date", "receipt_character", "receipt_file_id",
                          "receipt_ordinal", "receipt_file_sha", "receipt_parser", "receipt_raw_time",
                          "receipt_timezone_basis", "import_file_status", "import_file_character",
                          "import_file_conversation", "import_file_sha", "import_file_parser",
                          "import_file_cursor", "import_file_total", "import_batch_id",
                          "archive_only"):
                payload.pop(field, None)
        return Source(kind, source_id, row["character_id"], "conversation", str(row["conversation_id"]),
                      row["role"], row["generation_status"] or "completed",
                      "user_attested" if row["role"] == "user" else "model_generated", row["content"],
                      occurred, recorded, None, "occurred" if occurred else "recorded" if recorded else "unknown",
                      digest(row["content"]), digest(payload),
                      row["receipt_date"] if archive_only else occurred[:10] if occurred else None,
                       row["receipt_precision"] if archive_only else "instant" if occurred else "unknown",
                       row["receipt_file_id"] if archive_only else None,
                       row["receipt_ordinal"] if archive_only else None)
    if kind == "activity_operation":
        from .chat_history import READ_SCOPE
        if READ_SCOPE.get() is not None:
            # Legacy autonomous operation records have no message-path coverage
            # proof. Do not infer shared status for branch recall.
            return None
        row = db.execute("""SELECT o.*,s.character_id AS session_character_id,
            s.environment_id AS session_environment_id,c.enabled AS character_enabled
            FROM activity_operations o JOIN awakening_sessions s ON s.id=o.session_id
            JOIN characters c ON c.id=o.character_id WHERE o.id=?""", (source_id,)).fetchone()
        if row is None or not row["character_enabled"] or row["character_id"] != row["session_character_id"]:
            return None
        safe_action = LOCAL_ACTIONS.get(row["action_id"]) if row["kind"] == "environment" and row["session_environment_id"] == "local-studio-v1" else None
        label = "本地环境操作" if row["kind"] == "environment" else "工具操作"
        text = " · ".join(part for part in (label, safe_action, STATUS_LABELS.get(row["status"], "状态未知")) if part)
        return Source(kind, source_id, row["character_id"], "activity_session", row["session_id"],
                      "activity", row["status"], "operation_record", text, None, row["created_at"],
                      row["updated_at"], "recorded" if row["created_at"] else "unknown",
                      digest(text), digest(dict(row)))
    return None
