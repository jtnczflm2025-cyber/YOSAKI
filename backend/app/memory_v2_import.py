"""Explicit, resumable RAW import into archive-only conversations.

The caller must install the import schema on an isolated database first. No
model, activity, feedback or learning entrypoint is called here.
"""

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException

from . import archives
from . import memory_v2_import_preview as preview


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _begin(db):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


def _row(db, table, identity):
    return db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()


def _batch(db, batch_id, character_id):
    row = _row(db, "mv2_import_batches", batch_id)
    if (row is None or row["character_id"] != character_id or
            not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone()):
        raise HTTPException(404, "导入批次不存在")
    return row


def _file_payload(path, body, character_id, offset, container_sha):
    parsed = preview.parse_source(path, body)
    report = preview.preview_file(path, body, character_id=character_id,
                                  confirmed_offset_minutes=offset, container_sha256=container_sha,
                                  parsed_source=parsed)
    title, rows = parsed["title"], parsed["rows"]
    original_id = report["externalConversationId"]
    staged = []
    for item, evidence in zip(rows, report["messages"]):
        staged.append({"row": item, "ordinal": evidence["ordinal"],
                       "externalMessageId": evidence["externalMessageId"], "time": evidence["time"]})
    return report, title, original_id, staged


def _suggest(rows):
    """30-minute time gaps suggest boundaries; unknown times never prove a gap."""
    eligible = [item for item in rows if item["row"]["role"] in ("user", "assistant")]
    if not eligible:
        return []
    starts = [eligible[0]["ordinal"]]
    previous = None
    for item in eligible:
        instant = item["time"]["occurredAt"]
        current = datetime.fromisoformat(instant) if instant else None
        if previous is not None and current is not None and (current - previous).total_seconds() > 1800:
            starts.append(item["ordinal"])
        previous = current
    ends = [ordinal - 1 for ordinal in starts[1:]] + [eligible[-1]["ordinal"]]
    return [(start, end, f"待整理片段 {index}") for index, (start, end) in enumerate(zip(starts, ends), 1)]


def prepare(db, character_id, files, confirmed_offset_minutes=None, expected_preview_fingerprint=None, *, plan_owner=None):
    """Stage a fully validated preview; source messages remain untouched."""
    if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
        raise HTTPException(404, "角色不可访问")
    if not files or len(files) > preview.MAX_FILES:
        raise HTTPException(413, "文件数量无效或超过 200")
    staged = []
    total_bytes = 0
    seen = set()
    seen_external = set()
    for upload_name, upload_body in files:
        total_bytes += len(upload_body)
        if total_bytes > archives.MAX_TOTAL:
            raise HTTPException(413, "上传总量超过 64 MiB")
        for path, body, container_sha in preview.members(upload_name, upload_body):
            if len(staged) >= preview.MAX_FILES:
                raise HTTPException(413, "展开后文件数量超过 200")
            report, title, external_id, rows = _file_payload(path, body, character_id, confirmed_offset_minutes, container_sha)
            if not any(item["row"]["role"] in ("user", "assistant") for item in rows):
                raise HTTPException(422, "档案没有可整理的双方对话消息")
            digest = report["fileSha256"]
            if digest in seen or db.execute("SELECT 1 FROM imported_archives WHERE digest=?", (digest,)).fetchone():
                raise HTTPException(409, "文件已在本批或旧档案中导入；请检查重复来源")
            if db.execute("SELECT 1 FROM mv2_import_files WHERE character_id=? AND file_sha256=?", (character_id, digest)).fetchone():
                raise HTTPException(409, "文件已在 Chronicle 导入批次中；请恢复原批次")
            if external_id is not None:
                for item in rows:
                    message_id = item["externalMessageId"]
                    if message_id is None:
                        continue
                    key = (external_id, message_id)
                    if key in seen_external or db.execute("""SELECT 1 FROM mv2_import_identity_claims WHERE
                        character_id=? AND external_conversation_id=? AND external_message_id=?""",
                        (character_id, external_id, message_id)).fetchone() or db.execute("""SELECT 1 FROM mv2_import_receipts WHERE
                        character_id=? AND external_conversation_id=? AND external_message_id=?""",
                        (character_id, external_id, message_id)).fetchone():
                        raise HTTPException(409, "外部消息身份重复；请检查重叠导入")
                    seen_external.add(key)
            seen.add(digest)
            staged.append((report, title, external_id, rows))
    preview_fingerprint = preview.batch_preview_fingerprint([item[0] for item in staged])
    if (expected_preview_fingerprint is not None and expected_preview_fingerprint != preview_fingerprint
            or expected_preview_fingerprint is None and
            any(item[0]["format"] == "chronicle_markdown" for item in staged)):
        raise HTTPException(409, "文件、解析器或预览结果已变化；请重新预览")
    batch_id = str(uuid.uuid4())
    stamp = _now()
    _begin(db)
    try:
        db.execute("""INSERT INTO mv2_import_batches(id,character_id,status,created_at,updated_at,total)
            VALUES(?,?,'preview',?,?,?)""", (batch_id, character_id, stamp, stamp,
                                                   sum(len(rows) for _, _, _, rows in staged)))
        for report, title, external_id, rows in staged:
            file_id = str(uuid.uuid4())
            db.execute("""INSERT INTO mv2_import_files(id,batch_id,character_id,name,file_sha256,container_sha256,
                parser_version,external_conversation_id,title,staged_json,total,status,conversion_note)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'preview',?)""",
                (file_id, batch_id, character_id, report["name"], report["fileSha256"], report["containerSha256"],
                 report["parserVersion"], external_id, title, json.dumps(rows, ensure_ascii=False),
                 len(rows), report["conversionNote"]))
            for ordinal, (start, end, suggested_title) in enumerate(_suggest(rows), 1):
                db.execute("""INSERT INTO mv2_import_segments(id,batch_id,file_id,ordinal,start_ordinal,end_ordinal,title)
                    VALUES(?,?,?,?,?,?,?)""", (str(uuid.uuid4()), batch_id, file_id, ordinal,
                                               start, end, suggested_title))
            if external_id is not None:
                for item in rows:
                    if item["externalMessageId"] is not None:
                        db.execute("""INSERT INTO mv2_import_identity_claims(
                            character_id,external_conversation_id,external_message_id,file_id,content_sha256)
                            VALUES(?,?,?,?,?)""", (character_id, external_id, item["externalMessageId"],
                                                 file_id, hashlib.sha256(item["row"]["content"].encode()).hexdigest()))
        if plan_owner is not None:
            from .memory_v2_import_processing import grant
            grant(db,batch_id,character_id,plan_owner)
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "来源身份在并发预览中已被占用") from exc
    except Exception:
        db.rollback()
        raise
    return {"batchId": batch_id, "status": "preview", "total": sum(len(rows) for _, _, _, rows in staged),
            "previewFingerprint": preview_fingerprint,
            "files": [report for report, _, _, _ in staged]}


def _staged_preview_fingerprint(db, batch_id):
    reports = []
    for file in db.execute("""SELECT character_id,name,title,conversion_note,file_sha256,parser_version,staged_json FROM mv2_import_files
        WHERE batch_id=? ORDER BY rowid""", (batch_id,)):
        if file["staged_json"] is None:
            return None
        if (file["parser_version"].startswith("chronicle-bracket-md-")
                and file["parser_version"] != preview.chronicle_markdown.PARSER_VERSION):
            raise HTTPException(409, "Chronicle Markdown 解析器版本已变化；请重新预览")
        try:
            items = json.loads(file["staged_json"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(409, "预览暂存内容损坏；请重新预览") from exc
        reports.append({"characterId": file["character_id"], "name": file["name"],
                        "title": file["title"], "conversionNote": file["conversion_note"],
                        "fileSha256": file["file_sha256"],
                        "parserVersion": file["parser_version"],
                        "previewFingerprint": preview.snapshot_fingerprint(
                            file["parser_version"], file["file_sha256"], items)})
    return preview.batch_preview_fingerprint(reports)


def confirm(db, batch_id, character_id, expected_version=None, expected_preview_fingerprint=None):
    _begin(db)
    row = _batch(db, batch_id, character_id)
    if expected_version is not None and row["plan_version"] != expected_version:
        raise HTTPException(409, "边界版本已变化，请刷新确认")
    if row["status"] == "confirmed":
        return status(db, batch_id, character_id)
    if row["status"] != "preview":
        raise HTTPException(409, "批次已开始或不能再次确认")
    requires_fingerprint = bool(db.execute("""SELECT 1 FROM mv2_import_files WHERE batch_id=?
        AND parser_version LIKE 'chronicle-bracket-md-%'""", (batch_id,)).fetchone())
    current_fingerprint = _staged_preview_fingerprint(db, batch_id)
    if (requires_fingerprint and (expected_preview_fingerprint is None or expected_version is None) or
            expected_preview_fingerprint is not None and expected_preview_fingerprint != current_fingerprint):
        raise HTTPException(409, "预览快照已变化；请重新预览")
    db.execute("UPDATE mv2_import_batches SET status='confirmed',updated_at=? WHERE id=? AND status='preview'",
               (_now(), batch_id))
    db.execute("UPDATE mv2_import_files SET status='confirmed' WHERE batch_id=?", (batch_id,))
    db.commit()
    return status(db, batch_id, character_id)


def status(db, batch_id, character_id):
    row = _batch(db, batch_id, character_id)
    files = db.execute("""SELECT f.id,f.name,f.file_sha256,f.cursor,f.total,f.status,f.conversation_id,f.conversion_note,
        EXISTS(SELECT 1 FROM mv2_import_boundary_confirmations b WHERE b.file_id=f.id
               AND b.confirmation_origin='local-dialogue-reading-v1' AND b.confirmed_by_user_id IS NULL) AS reading_ranges_pending
        FROM mv2_import_files f WHERE f.batch_id=? ORDER BY f.rowid""", (batch_id,)).fetchall()
    segments = db.execute("""SELECT s.id,s.file_id,s.ordinal,s.start_ordinal,s.end_ordinal,s.title,s.entry_id
        FROM mv2_import_segments s WHERE s.batch_id=? ORDER BY s.file_id,s.ordinal""", (batch_id,)).fetchall()
    fingerprint = _staged_preview_fingerprint(db, batch_id) if row["status"] == "preview" else None
    return {"batchId": batch_id, "characterId": character_id, "status": row["status"],
            "processed": row["processed"], "total": row["total"], "errorCode": row["error_code"],
            "files": [dict(item) for item in files], "segments": [dict(item) for item in segments],
            "planVersion": row["plan_version"], "previewFingerprint": fingerprint}


def list_batches(db, character_id):
    if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
        raise HTTPException(404, "角色不可访问")
    return [{"batchId": row["id"], "status": row["status"], "processed": row["processed"],
             "total": row["total"], "updatedAt": row["updated_at"]} for row in db.execute("""SELECT *
        FROM mv2_import_batches WHERE character_id=? ORDER BY created_at DESC,id DESC LIMIT 50""",
        (character_id,))]


def raw_files(db, character_id, from_date=None, to_date=None):
    list_batches(db, character_id)
    try:
        if from_date: date.fromisoformat(from_date)
        if to_date: date.fromisoformat(to_date)
    except ValueError as exc:
        raise HTTPException(422, "日期格式无效") from exc
    result = []
    for row in db.execute("""SELECT f.id,f.batch_id,f.name,f.title,f.status,f.cursor,f.total,f.character_id,
        f.conversation_id,MIN(r.occurred_date) AS first_date,MAX(r.occurred_date) AS last_date,
        SUM(CASE WHEN r.time_precision='local_datetime_unverified' THEN 1 ELSE 0 END) AS unverified_local_count
        FROM mv2_import_files f JOIN mv2_import_batches b ON b.id=f.batch_id
        LEFT JOIN mv2_import_receipts r ON r.file_id=f.id
        WHERE b.character_id=? AND f.conversation_id IS NOT NULL
        GROUP BY f.id ORDER BY b.created_at DESC,f.rowid DESC LIMIT 200""", (character_id,)):
        if from_date and (row["last_date"] is None or row["last_date"] < from_date):
            continue
        if to_date and (row["first_date"] is None or row["first_date"] > to_date):
            continue
        item = dict(row)
        if not valid_raw_file(db, row, character_id):
            item.update(title=None, first_date=None, last_date=None, sourceStatus="source_stale")
        else:
            item["sourceStatus"] = "valid"
        result.append(item)
    return result


def valid_raw_file(db, file, character_id):
    """Check every written source before exposing a file title or RAW page."""
    if file["character_id"] != character_id:
        return False
    conversation = db.execute("SELECT character_id,archive_only FROM conversations WHERE id=?",
                              (file["conversation_id"],)).fetchone()
    if not conversation or conversation["character_id"] != character_id or not conversation["archive_only"]:
        return False
    receipts = db.execute("""SELECT r.message_id,r.content_sha256,r.character_id AS receipt_character,
        m.content,m.conversation_id,
        c.character_id AS owner_character FROM mv2_import_receipts r
        LEFT JOIN messages m ON m.id=r.message_id
        LEFT JOIN conversations c ON c.id=m.conversation_id
        WHERE r.file_id=? ORDER BY r.ordinal""", (file["id"],)).fetchall()
    if len(receipts) != file["cursor"]:
        return False
    return all(row["content"] is not None and row["owner_character"] == character_id
               and row["receipt_character"] == character_id
               and row["conversation_id"] == file["conversation_id"]
               and hashlib.sha256(row["content"].encode()).hexdigest() == row["content_sha256"]
               for row in receipts)


def search_raw(db, character_id, query, after_id=0, limit=30, snapshot=None):
    """Search only current, validated M1 terms from archive-only messages."""
    from . import memory_v2_sources as adapters
    from . import memory_v2_read as reader
    list_batches(db, character_id)
    terms = adapters.query_terms(query)
    if not terms or len(terms) > 16 or not 0 <= after_id or not 1 <= limit <= 50:
        raise HTTPException(422, "检索词或分页范围无效")
    meta = db.execute("SELECT active_generation,state,schema_version FROM mv2_meta WHERE character_id=?", (character_id,)).fetchone()
    if not meta or meta["state"] != "ready" or meta["schema_version"] != 2:
        return {"items": [], "nextAfter": None, "indexState": "needs_rebuild", "snapshot": None}
    generation = str(meta["active_generation"])
    if snapshot is not None and snapshot != generation:
        raise HTTPException(409, "索引代已变化，请重新检索")
    items = []
    scan_after = after_id
    file_cache = {}
    while len(items) <= limit:
        candidates = db.execute(f"""SELECT s.*,m.id AS numeric_id,r.ordinal,r.occurred_date,r.time_precision,
            f.id AS file_id,f.batch_id,f.name AS source_path,f.title AS conversation_title,
            f.cursor AS file_cursor,f.conversation_id AS archive_conversation_id,
            f.character_id AS file_character_id
            FROM mv2_sources s JOIN messages m ON s.source_kind='message' AND s.source_id=CAST(m.id AS TEXT)
            JOIN conversations c ON c.id=m.conversation_id AND c.archive_only=1
            JOIN mv2_import_receipts r ON r.message_id=m.id
            JOIN mv2_import_files f ON f.id=r.file_id
            JOIN mv2_terms t ON t.generation=s.generation AND t.source_kind=s.source_kind AND t.source_id=s.source_id
            WHERE s.character_id=? AND s.generation=? AND c.character_id=? AND m.id>?
              AND t.term IN ({','.join('?' for _ in terms)})
            GROUP BY s.source_kind,s.source_id HAVING COUNT(DISTINCT t.term)=?
            ORDER BY m.id LIMIT 500""", (character_id, meta["active_generation"], character_id,
                                          scan_after, *sorted(terms), len(terms))).fetchall()
        if not candidates:
            break
        for candidate in candidates:
            scan_after = candidate["numeric_id"]
            file_id = candidate["file_id"]
            if file_id not in file_cache:
                file_cache[file_id] = valid_raw_file(db, {"id": file_id, "cursor": candidate["file_cursor"],
                    "conversation_id": candidate["archive_conversation_id"],
                    "character_id": candidate["file_character_id"]}, character_id)
            live = reader.valid_source(db, candidate, character_id)
            if live is None:
                continue
            items.append({"messageId": candidate["numeric_id"], "ordinal": candidate["ordinal"],
                          "fileId": file_id, "batchId": candidate["batch_id"],
                          "sourcePath": candidate["source_path"],
                          "conversationTitle": candidate["conversation_title"] if file_cache[file_id] else None,
                          "role": live.role, "text": live.text[:300], "occurredAt": live.occurred_at,
                          "occurredDate": candidate["occurred_date"], "timePrecision": candidate["time_precision"]})
            if len(items) > limit:
                break
        if len(candidates) < 500:
            break
    return {"items": items[:limit], "nextAfter": items[limit - 1]["messageId"] if len(items) > limit else None,
            "indexState": "ready", "snapshot": generation}


def discard_preview(db, batch_id, character_id):
    row = _batch(db, batch_id, character_id)
    if row["status"] != "preview":
        raise HTTPException(409, "只能清理尚未确认的暂存批次")
    db.execute("DELETE FROM mv2_import_batches WHERE id=? AND status='preview'", (batch_id,))
    db.commit()
    return {"discarded": True, "userFilesChanged": False}


def pause_unfinished_on_start(db):
    """Explicit post-restore/restart reconciliation; never resumes an import."""
    db.execute("""UPDATE mv2_import_batches SET status='paused',updated_at=?
        WHERE status IN ('confirmed','running')""", (_now(),))
    db.commit()


def purge_stale_previews(db, *, now=None, days=7):
    """Discard only old unconfirmed staging; never delete source messages/files."""
    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(days=days)).isoformat(timespec="seconds")
    removed = db.execute("DELETE FROM mv2_import_batches WHERE status='preview' AND created_at<?",
                         (cutoff,)).rowcount
    db.commit()
    return removed


def replace_segments(db, batch_id, character_id, file_id, segments, expected_version):
    _begin(db)
    batch = _batch(db, batch_id, character_id)
    if batch["status"] != "preview" or batch["plan_version"] != expected_version:
        raise HTTPException(409, "边界版本已变化或批次已确认")
    file = db.execute("SELECT * FROM mv2_import_files WHERE id=? AND batch_id=?", (file_id, batch_id)).fetchone()
    if file is None or file["staged_json"] is None:
        raise HTTPException(404, "待整理文件不存在")
    eligible = {item["ordinal"] for item in json.loads(file["staged_json"])
                if item["row"]["role"] in ("user", "assistant")}
    if not isinstance(segments, list) or len(segments) > 500 or not eligible:
        raise HTTPException(422, "边界数量无效")
    covered = []
    for segment in segments:
        if (not isinstance(segment, dict) or type(segment.get("start")) is not int
                or type(segment.get("end")) is not int or segment["start"] > segment["end"]
                or not isinstance(segment.get("title"), str) or not 1 <= len(segment["title"].strip()) <= 200):
            raise HTTPException(422, "边界或标题无效")
        covered.extend(sorted(x for x in eligible if segment["start"] <= x <= segment["end"]))
    if covered != sorted(eligible) or len(set(covered)) != len(covered):
        raise HTTPException(422, "边界必须按原始顺序完整且不重叠地覆盖消息")
    db.execute("DELETE FROM mv2_import_segments WHERE batch_id=? AND file_id=?", (batch_id, file_id))
    for ordinal, segment in enumerate(segments, 1):
        db.execute("""INSERT INTO mv2_import_segments(id,batch_id,file_id,ordinal,start_ordinal,end_ordinal,title)
            VALUES(?,?,?,?,?,?,?)""", (str(uuid.uuid4()), batch_id, file_id, ordinal,
                                       segment["start"], segment["end"], segment["title"].strip()))
    db.execute("UPDATE mv2_import_batches SET plan_version=plan_version+1,updated_at=? WHERE id=?",
               (_now(), batch_id))
    db.commit()
    return status(db, batch_id, character_id)


def create_draft_entries(db, batch_id, character_id):
    """After explicit M1 indexing, map each confirmed segment to one P1-A draft."""
    from . import memory_v2_entries as entries
    from . import memory_v2_derivation as m3
    _begin(db)
    batch = _batch(db, batch_id, character_id)
    if batch["status"] != "raw_complete":
        raise HTTPException(409, "RAW 尚未完整导入")
    segments = db.execute("SELECT * FROM mv2_import_segments WHERE batch_id=? ORDER BY file_id,ordinal",
                          (batch_id,)).fetchall()
    result = []
    try:
        for segment in segments:
            refs = [{"kind": "message", "id": str(row["message_id"])} for row in db.execute("""SELECT r.message_id
                FROM mv2_import_receipts r WHERE r.file_id=? AND r.ordinal BETWEEN ? AND ?
                AND EXISTS(SELECT 1 FROM messages m WHERE m.id=r.message_id AND m.role IN ('user','assistant'))
                ORDER BY r.ordinal""", (segment["file_id"], segment["start_ordinal"], segment["end_ordinal"]))]
            if not refs or len(refs) > 500:
                raise HTTPException(409, "片段无可用来源或超过 500 条，请先拆分")
            try:
                m3.source_inputs(db, character_id, refs)
            except ValueError as exc:
                raise HTTPException(409, "来源尚未索引或已失效") from exc
            entry_id = segment["entry_id"]
            if entry_id is None:
                entry_id = entries.create_entry(db, character_id)["id"]
                db.execute("UPDATE mv2_import_segments SET entry_id=? WHERE id=? AND entry_id IS NULL",
                           (entry_id, segment["id"]))
            else:
                existing = entries._entry(db, entry_id)
                if existing is None or existing["character_id"] != character_id or existing["status"] not in ("draft", "active"):
                    raise HTTPException(409, "片段关联的经历条目已失效")
            result.append({"segmentId": segment["id"], "entryId": entry_id, "titleSuggestion": segment["title"],
                           "sourceRefs": refs, "status": "draft_needs_metadata"})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def confirmed_file_snapshot(db, batch_id, character_id, file_id, expected_sha, expected_parser, *, general=False):
    """H3.2 source identity proof; never reads the external archive file."""
    from . import memory_v2_sources as adapters
    from . import memory_v2_derivation as m3
    batch = _batch(db, batch_id, character_id)
    file = db.execute("""SELECT * FROM mv2_import_files WHERE id=? AND batch_id=?
        AND character_id=?""", (file_id, batch_id, character_id)).fetchone()
    if (batch["status"] != "raw_complete" or file is None or file["status"] != "raw_complete"
            or file["total"] < 1 or (not general and file["total"] != 130) or file["cursor"] != file["total"]
            or file["file_sha256"].lower() != expected_sha.lower()
            or file["parser_version"] != expected_parser
            or not adapters.verified_archive_file(db, file_id)):
        raise ValueError("h32_file_manifest_changed")
    count = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                       (file["conversation_id"],)).fetchone()[0]
    if count != file["total"]:
        raise ValueError("h32_conversation_count_changed")
    rows = db.execute("""SELECT r.*,m.role,m.conversation_id,m.generation_status
        FROM mv2_import_receipts r JOIN messages m ON m.id=r.message_id
        WHERE r.file_id=? ORDER BY r.ordinal""", (file_id,)).fetchall()
    if len(rows) != file["total"]:
        raise ValueError("h32_receipt_count_changed")
    records = []
    cache = {}
    for ordinal, row in enumerate(rows, 1):
        if (row["ordinal"] != ordinal or row["conversation_id"] != file["conversation_id"]
                or row["generation_status"] != "imported"
                or row["role"] not in ("user", "assistant")
                or (not general and (row["occurred_at"] is not None
                or row["time_precision"] != "local_datetime_unverified"))):
            raise ValueError("h32_source_identity_changed")
        live = adapters.source(db, "message", str(row["message_id"]), cache)
        indexed = m3.live_source(db, character_id, "message", str(row["message_id"]))
        if (live is None or indexed is None or live.content_hash != row["content_sha256"]
                or indexed.content_hash != live.content_hash or indexed.state_hash != live.state_hash):
            raise ValueError("h32_source_missing_or_stale")
        records.append({"ordinal": ordinal, "messageId": str(row["message_id"]),
                        "role": row["role"], "contentHash": live.content_hash,
                        "stateHash": live.state_hash, "originalTime": row["original_time_text"],
                        "timePrecision": row["time_precision"]})
    if not general and (sum(row["role"] == "user" for row in records) != 65 or sum(
            row["role"] == "assistant" for row in records) != 65):
        raise ValueError("h32_role_count_changed")
    payload = [character_id, file_id, file["conversation_id"], file["file_sha256"],
               file["parser_version"], records]
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return file, records, fingerprint


def confirm_h32_drafts(db, batch_id, character_id, file_id, segments, *,
                       expected_sha, expected_parser, expected_snapshot,
                       h31_snapshot_hash, editor_id, confirmation_origin):
    """Original 13/130 specimen contract, unchanged."""
    return _confirm_drafts(db,batch_id,character_id,file_id,segments,expected_sha=expected_sha,
        expected_parser=expected_parser,expected_snapshot=expected_snapshot,
        h31_snapshot_hash=h31_snapshot_hash,editor_id=editor_id,confirmation_origin=confirmation_origin)


def confirm_dialogue_drafts(db, batch_id, character_id, file_id, segments, *,
                            expected_sha, expected_parser, expected_snapshot, editor_id):
    """Owner explicitly confirms this entire source file's ordered partition.

    The legacy h31_snapshot_hash slot is a stable NOT-APPLICABLE marker for this
    new origin; it is not an invented H3.1 review or content-quality approval.
    """
    if not isinstance(segments,list) or not 1 <= len(segments) <= 500:
        raise ValueError('dialogue_boundary_count_invalid')
    file, records, snapshot=confirmed_file_snapshot(db,batch_id,character_id,file_id,expected_sha,expected_parser,general=True)
    return _confirm_drafts(db,batch_id,character_id,file_id,segments,expected_sha=expected_sha,
        expected_parser=expected_parser,expected_snapshot=expected_snapshot,
        h31_snapshot_hash=hashlib.sha256(b'not-applicable:owner-dialogue-boundary-v1').hexdigest(),
        editor_id=editor_id,confirmation_origin='owner-dialogue-boundary-v1',
        required_count=len(segments),expected_total=len(records),generic=True)


def _confirm_drafts(db, batch_id, character_id, file_id, segments, *,
                       expected_sha, expected_parser, expected_snapshot,
                       h31_snapshot_hash, editor_id, confirmation_origin, required_count=13, expected_total=130, generic=False,
                       prepare_reading=False):
    """Isolated post-RAW H3.2 confirmation and 13 P1-A2 drafts, in one transaction."""
    from . import memory_v2_metadata as metadata
    if db.in_transaction:
        raise ValueError("h32_requires_own_transaction")
    if (not isinstance(segments, list) or len(segments) != required_count
            or not isinstance(confirmation_origin, str) or not confirmation_origin
            or not isinstance(h31_snapshot_hash, str) or len(h31_snapshot_hash) != 64):
        raise ValueError("h32_plan_invalid")
    db.execute("BEGIN IMMEDIATE")
    try:
        metadata._owner(db, editor_id)
        file, records, snapshot = confirmed_file_snapshot(
            db, batch_id, character_id, file_id, expected_sha, expected_parser, general=generic)
        if snapshot != expected_snapshot:
            raise ValueError("h32_source_snapshot_changed")
        ordered = []
        next_ordinal = 1
        for segment in segments:
            if (not isinstance(segment, dict) or set(segment) != {"start", "end", "title"}
                    or type(segment["start"]) is not int or type(segment["end"]) is not int
                    or segment["start"] != next_ordinal or segment["end"] < segment["start"]
                    or segment["end"] > expected_total or not isinstance(segment["title"], str)
                    or not 1 <= len(segment["title"].strip()) <= 200):
                raise ValueError("h32_boundary_invalid")
            ordered.append({"start": segment["start"], "end": segment["end"],
                            "title": segment["title"]})
            next_ordinal = segment["end"] + 1
        if next_ordinal != expected_total + 1:
            raise ValueError("h32_coverage_invalid")
        plan_payload = [character_id, file_id, expected_sha.lower(), expected_parser,
                        snapshot, h31_snapshot_hash, ordered]
        plan_hash = hashlib.sha256(json.dumps(plan_payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        prior = db.execute("SELECT * FROM mv2_import_boundary_confirmations WHERE file_id=?",
                           (file_id,)).fetchone()
        if prior:
            confirm_existing = (generic and confirmation_origin == 'owner-dialogue-boundary-v1'
                                and prior["confirmation_origin"] == 'local-dialogue-reading-v1'
                                and prior["confirmed_by_user_id"] is None and json.loads(prior["plan_json"]) == ordered)
            if ((prior["plan_hash"] != plan_hash and not confirm_existing) or prior["source_snapshot_hash"] != snapshot
                    or prior["batch_id"] != batch_id or prior["character_id"] != character_id):
                raise ValueError("h32_confirmation_conflict")
            existing = db.execute("""SELECT * FROM mv2_import_segments WHERE batch_id=? AND file_id=?
                ORDER BY ordinal""", (batch_id, file_id)).fetchall()
            if len(existing) != required_count or any(
                    row["ordinal"] != i or row["start_ordinal"] != item["start"]
                    or row["end_ordinal"] != item["end"] or row["title"] != item["title"]
                    or row["entry_id"] is None for i, (row, item) in enumerate(zip(existing, ordered), 1)):
                raise ValueError("h32_entry_binding_changed")
            result = []
            for row in existing:
                detail = metadata.read_draft(db, row["entry_id"], character_id)
                refs = [r["messageId"] for r in records[row["start_ordinal"]-1:row["end_ordinal"]]]
                if ([(r["source_kind"], r["source_id"]) for r in detail["sourceRefs"]]
                        != [("message", source_id) for source_id in refs]
                        or (not generic and detail["fields"]["title"]["value"] != row["title"])):
                    raise ValueError("h32_entry_binding_changed")
                result.append({"entryId": row["entry_id"], "start": row["start_ordinal"],
                               "end": row["end_ordinal"], "title": detail["fields"]["title"]["value"]})
            if confirm_existing:
                db.execute("""UPDATE mv2_import_boundary_confirmations SET plan_hash=?,h31_snapshot_hash=?,
                    confirmation_origin=?,confirmed_by_user_id=?,confirmed_at=? WHERE file_id=?""",
                    (plan_hash,h31_snapshot_hash,confirmation_origin,editor_id,_now(),file_id))
            db.commit()
            return {"planHash": plan_hash, "sourceSnapshot": snapshot,
                    "planVersion": prior["plan_version"], "items": result, "reused": True}
        batch = _batch(db, batch_id, character_id)
        old = db.execute("SELECT entry_id FROM mv2_import_segments WHERE batch_id=? AND file_id=?",
                         (batch_id, file_id)).fetchall()
        if any(row["entry_id"] is not None for row in old):
            raise ValueError("h32_existing_entry_conflict")
        version = batch["plan_version"] + 1
        db.execute("DELETE FROM mv2_import_segments WHERE batch_id=? AND file_id=?", (batch_id, file_id))
        db.execute("""INSERT INTO mv2_import_boundary_confirmations
            (file_id,batch_id,character_id,plan_version,plan_hash,source_snapshot_hash,
             h31_snapshot_hash,plan_json,confirmation_origin,confirmed_by_user_id,confirmed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (file_id, batch_id, character_id, version, plan_hash, snapshot,
             h31_snapshot_hash, json.dumps(ordered, ensure_ascii=False),
             confirmation_origin, None if prepare_reading else editor_id, _now()))
        result = []
        for i, item in enumerate(ordered, 1):
            refs = [{"kind": "message", "id": r["messageId"]}
                    for r in records[item["start"]-1:item["end"]]]
            request_key = "h32:" + plan_hash + ":" + str(i)
            draft = metadata.create_draft(db, character_id, refs, item["title"], editor_id,
                                          request_key, title_origin="h31_system_suggested")
            if prepare_reading:
                from . import memory_v2_dialogue_refinement as finite
                finite.create(db, character_id, editor_id, draft['entryId'], draft['versionId'])
            segment_id = str(uuid.uuid4())
            db.execute("""INSERT INTO mv2_import_segments
                (id,batch_id,file_id,ordinal,start_ordinal,end_ordinal,title,entry_id)
                VALUES(?,?,?,?,?,?,?,?)""",
                (segment_id, batch_id, file_id, i, item["start"], item["end"],
                 item["title"], draft["entryId"]))
            result.append({"entryId": draft["entryId"], **item})
        db.execute("UPDATE mv2_import_batches SET plan_version=?,updated_at=? WHERE id=?",
                   (version, _now(), batch_id))
        db.commit()
        return {"planHash": plan_hash, "sourceSnapshot": snapshot,
                "planVersion": version, "items": result, "reused": False}
    except Exception:
        db.rollback()
        raise


def set_paused(db, batch_id, character_id, paused):
    _begin(db)
    row = _batch(db, batch_id, character_id)
    allowed = ("confirmed", "running", "failed") if paused else ("paused",)
    if row["status"] not in allowed:
        raise HTTPException(409, "批次状态不允许暂停或恢复")
    db.execute("UPDATE mv2_import_batches SET status=?,updated_at=?,error_code=NULL WHERE id=?",
               ("paused" if paused else "confirmed", _now(), batch_id))
    db.commit()
    return status(db, batch_id, character_id)


def step(db, batch_id, character_id, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "单批上限为 100 条")
    _begin(db)
    try:
        batch = _batch(db, batch_id, character_id)
        if batch["status"] not in ("confirmed", "running"):
            raise HTTPException(409, "批次尚未确认、已暂停或已经完成")
        file = db.execute("""SELECT * FROM mv2_import_files WHERE batch_id=? AND cursor<total
            ORDER BY rowid LIMIT 1""", (batch_id,)).fetchone()
        if file is None:
            db.execute("UPDATE mv2_import_batches SET status='raw_complete',updated_at=? WHERE id=?", (_now(), batch_id))
            db.commit()
            return status(db, batch_id, character_id)
        if file["staged_json"] is None:
            raise HTTPException(409, "待导入文件暂存内容缺失")
        rows = json.loads(file["staged_json"])
        if len(rows) != file["total"]:
            raise HTTPException(409, "待导入文件数量已变化")
        stamp = _now()
        conversation_id = file["conversation_id"]
        if conversation_id is None:
            conversation_id = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at,archive_only)
                VALUES(?,?,?,?,1)""", (character_id, file["title"], stamp, stamp)).lastrowid
            db.execute("UPDATE mv2_import_files SET conversation_id=? WHERE id=?", (conversation_id, file["id"]))
        elif not db.execute("SELECT 1 FROM conversations WHERE id=? AND character_id=? AND archive_only=1",
                            (conversation_id, character_id)).fetchone():
            raise HTTPException(409, "归档对话已失效或归属改变")
        end = min(file["cursor"] + limit, file["total"])
        for item in rows[file["cursor"]:end]:
            row, when = item["row"], item["time"]
            occurrence = when["occurredAt"]
            content_sha = hashlib.sha256(row["content"].encode()).hexdigest()
            message_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at,model,
                tool_calls_json,tool_call_id,tool_name,reasoning_content,monologue_content,
                reasoning_source,generation_status,imported_tool_runs_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'imported',?)""",
                (conversation_id, row["role"], row["content"], occurrence or stamp, row.get("model"),
                 row.get("tool_calls_json"), row.get("tool_call_id"), row.get("tool_name"),
                 row.get("reasoning_content"), row.get("monologue_content"), row.get("reasoning_source"),
                 json.dumps(row.get("tool_runs"), ensure_ascii=False) if row.get("tool_runs") else None)).lastrowid
            db.execute("""INSERT INTO mv2_import_receipts(message_id,file_id,character_id,ordinal,
                external_message_id,external_conversation_id,original_time_text,occurred_at,occurred_date,
                time_precision,timezone_basis,recorded_at,file_sha256,container_sha256,parser_version,source_path,
                conversion_note,content_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, file["id"], character_id, item["ordinal"], item["externalMessageId"],
                 file["external_conversation_id"], when["original"], occurrence, when["occurredDate"],
                 when["precision"], when["timezoneBasis"], stamp, file["file_sha256"], file["container_sha256"],
                 file["parser_version"], file["name"], file["conversion_note"], content_sha))
        complete = end == file["total"]
        db.execute("""UPDATE mv2_import_files SET cursor=?,status=?,staged_json=? WHERE id=?""",
                   (end, "raw_complete" if complete else "running", None if complete else file["staged_json"], file["id"]))
        db.execute("UPDATE mv2_import_batches SET processed=processed+?,status='running',updated_at=? WHERE id=?",
                   (end - file["cursor"], stamp, batch_id))
        if not db.execute("SELECT 1 FROM mv2_import_files WHERE batch_id=? AND cursor<total", (batch_id,)).fetchone():
            db.execute("UPDATE mv2_import_batches SET status='raw_complete' WHERE id=?", (batch_id,))
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        db.execute("UPDATE mv2_import_batches SET status='failed',error_code='source_identity_conflict',updated_at=? WHERE id=?",
                   (_now(), batch_id))
        db.commit()
        raise HTTPException(409, "来源身份冲突；批次已停止，未提交本批消息") from exc
    except HTTPException:
        db.rollback()
        raise
    return status(db, batch_id, character_id)
