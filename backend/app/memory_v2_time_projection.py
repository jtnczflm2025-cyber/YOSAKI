"""P2 Chronicle time tree and bounded projections. Not installed in live chat.

All public read functions rebuild the view from current M1/M3/P1-A evidence. The
only write here is an explicit binding for an extractive, reviewed M3 DIGEST.
"""

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from types import SimpleNamespace

from . import memory_v2_derivation as m3
from . import memory_v2_digest as digest
from . import memory_v2_entries as entries
from .memory_v2_context import estimate_tokens


MAX_PAGE = 50
MAX_RAW_PAGE = 4096
MAX_BUDGET = 4096


def init_db(db):
    """Explicit isolated migration; GET paths never call this function."""
    db.execute("""CREATE TABLE IF NOT EXISTS mv3_time_digest_bindings (
        id TEXT PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        node_id TEXT NOT NULL,
        level TEXT NOT NULL CHECK(level IN ('L1','L2','L3','L4')),
        period TEXT NOT NULL,
        time_basis TEXT NOT NULL CHECK(time_basis IN ('occurred_at','recorded_at')),
        input_profile TEXT NOT NULL CHECK(input_profile='raw_sources_v1'),
        scope_hash TEXT NOT NULL,
        digest_version_id TEXT NOT NULL REFERENCES mv3_versions(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        UNIQUE(character_id,node_id,scope_hash,digest_version_id)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_mv3_time_digest_node ON mv3_time_digest_bindings(character_id,node_id)")


def _checked_view(now, offset_minutes):
    if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
            or type(offset_minutes) is not int or not -840 <= offset_minutes <= 840):
        raise ValueError("invalid_time_view")
    return now.astimezone(timezone.utc)


def _timestamp(value, offset_minutes):
    if not value:
        return None
    try:
        if len(value) == 10 and date.fromisoformat(value):
            return datetime.combine(date.fromisoformat(value), datetime.min.time(),
                                    timezone(timedelta(minutes=offset_minutes)))
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _period(level, timestamp, offset_minutes):
    day = timestamp.astimezone(timezone(timedelta(minutes=offset_minutes)))
    iso = day.isocalendar()
    return {"L0": day.strftime("%Y-%m-%d"), "L1": f"{iso.year}-W{iso.week:02d}",
            "L2": day.strftime("%Y-%m"), "L3": f"{day.year}-Q{(day.month - 1)//3 + 1}",
            "L4": f"{day.year}-H{(day.month - 1)//6 + 1}", "L5": str(day.year)}[level]


@dataclass(frozen=True)
class Member:
    entry_id: str
    version_id: str
    detail: dict
    refs: tuple[dict, ...]
    timestamp: datetime | None
    level: str | None
    period: str | None
    time_basis: str
    future_timestamp: bool


@dataclass(frozen=True)
class Node:
    id: str
    level: str | None
    period: str | None
    time_basis: str
    members: tuple[Member, ...]
    children: tuple["Node", ...]
    scope_hash: str
    offset_minutes: int


def _members(db, character_id, now, offset_minutes):
    if not _table_exists(db, "mv3_entries"):
        return []
    found = []
    for row in db.execute("SELECT id FROM mv3_entries WHERE character_id=? AND status='active' ORDER BY id",
                          (character_id,)):
        try:
            detail = entries.read_entry(db, row["id"], character_id)
        except ValueError:
            continue  # Invalid source/version metadata must disappear before ranking or pagination.
        basis = detail['timeBasis']
        # A storage timestamp never determines an event's age or calendar bucket.
        value = detail['occurredAt'][0] if basis == 'occurred' else None
        timestamp = _timestamp(value, offset_minutes)
        day_precision = basis == "occurred" and isinstance(value, str) and len(value) == 10
        if day_precision and timestamp is not None:
            # End of the known day is only a conservative age bound, not an event timestamp.
            timestamp += timedelta(days=1, microseconds=-1)
        if timestamp is None:
            level = period = None
            projection_basis = ("recorded_only" if basis == "recorded" else
                                "unknown" if basis == "unknown" else "mixed_or_unparseable")
            future = False
        else:
            source = SimpleNamespace(occurred_at=timestamp.isoformat(),
                                     recorded_at=None,
                                     source_id=detail["entryId"])
            projected = digest.projection(source, now, offset_minutes)
            level, period = projected["level"], projected["period"]
            projection_basis, future = ("occurred_day" if day_precision else projected["timeBasis"]), projected["futureTimestamp"]
        found.append(Member(detail["entryId"], detail["versionId"], detail,
                            tuple(entries._refs(db, detail["versionId"])), timestamp,
                            level, period, projection_basis, future))
    return found


def _ordered(members):
    return tuple(sorted(members, key=lambda m: (m.timestamp or datetime.min.replace(tzinfo=timezone.utc), m.entry_id)))


def _source_refs(members):
    seen, refs = set(), []
    for member in members:
        for ref in member.refs:
            key = (ref["source_kind"], ref["source_id"])
            if key not in seen:
                seen.add(key)
                refs.append({"kind": key[0], "id": key[1]})
    return refs


def _node(level, period, time_basis, members, offset_minutes):
    members = _ordered(members)
    children = []
    if level in ("L2", "L3", "L4", "L5"):
        lower = f"L{int(level[1]) - 1}"
        groups = {}
        for member in members:
            key = _period(lower, member.timestamp, offset_minutes)
            groups.setdefault(key, []).append(member)
        children = [_node(lower, key, time_basis, group, offset_minutes)
                    for key, group in sorted(groups.items())]
    member_ids = [m.entry_id for m in members]
    node_id = ("E:" + member_ids[0] if level in (None, "L0") else
               f"{level}:{period}:{time_basis}@{m3._hash(member_ids)[:16]}")
    # The current extractive DIGEST is made from RAW and child DIGEST versions.
    # Title/keywords/synopsis/narrative edits do not change this RAW scope.
    scope = m3._hash(["p2-raw-extractive-v1", level, period, time_basis,
                      [(m.entry_id, [(r["source_kind"], r["source_id"], r["content_hash"],
                                      r["state_hash"]) for r in m.refs]) for m in members],
                      [(c.id, c.scope_hash) for c in children]])
    return Node(node_id, level, period, time_basis, members, tuple(children), scope,
                offset_minutes)


def _tree(db, character_id, now, offset_minutes):
    _checked_view(now, offset_minutes)
    members = _members(db, character_id, now, offset_minutes)
    groups = {}
    for member in members:
        key = ((member.level, member.period, member.time_basis)
               if member.level not in (None, "L0") else
               (member.level, member.period, member.time_basis, member.entry_id))
        groups.setdefault(key, []).append(member)
    roots = [_node(key[0], key[1], key[2], group, offset_minutes) for key, group in groups.items()]
    roots.sort(key=lambda n: (max((m.timestamp for m in n.members if m.timestamp), default=datetime.min.replace(tzinfo=timezone.utc)),
                              n.id), reverse=True)
    return roots


def _find(nodes, node_id):
    for node in nodes:
        if node.id == node_id:
            return node
        found = _find(node.children, node_id)
        if found:
            return found
    return None


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _digest(db, character_id, node):
    if node.level not in ("L1", "L2", "L3", "L4"):
        return None, "not_applicable"
    from . import memory_v2_temporal_digest_schema as a36_schema
    if a36_schema.installed(db):
        rows = db.execute("""SELECT * FROM mv3_time_digest_bindings_v2
            WHERE character_id=? AND node_id=? ORDER BY created_at DESC,id DESC""",
            (character_id, node.id)).fetchall()
        for binding in rows:
            if (binding["scope_hash"] != node.scope_hash or binding["level"] != node.level
                    or binding["period"] != node.period or binding["time_basis"] != node.time_basis
                    or binding["offset_minutes"] != node.offset_minutes):
                continue
            version = m3._row(db, "mv3_versions", binding["digest_version_id"])
            if (version is not None and version["review_status"] == "human_checked"
                    and m3.validity(db, version["id"], character_id) is None):
                return version, "available"
    if not _table_exists(db, "mv3_time_digest_bindings"):
        return None, "not_applicable" if node.level not in ("L1", "L2", "L3", "L4") else "missing"
    children = []
    for child in node.children:
        version, _ = _digest(db, character_id, child)
        if version is None:
            return None, "child_digest_missing"
        children.append(version["id"])
    rows = db.execute("""SELECT * FROM mv3_time_digest_bindings WHERE character_id=? AND node_id=?
        ORDER BY created_at DESC,id DESC""", (character_id, node.id)).fetchall()
    if not rows:
        return None, "stale_or_unreviewed" if a36_schema.installed(db) and db.execute(
            "SELECT 1 FROM mv3_time_digest_bindings_v2 WHERE character_id=? AND node_id=?",
            (character_id, node.id)).fetchone() else "missing"
    for binding in rows:
        if (binding["scope_hash"] != node.scope_hash or binding["input_profile"] != "raw_sources_v1"
                or binding["level"] != node.level or binding["period"] != node.period
                or binding["time_basis"] != node.time_basis):
            continue
        version = m3._row(db, "mv3_versions", binding["digest_version_id"])
        owner = m3._row(db, "mv3_derivations", version["derivation_id"]) if version else None
        if (owner is None or owner["character_id"] != character_id or owner["view_type"] != "DIGEST"
                or version["review_status"] != "human_checked"
                or version["rule_version"] not in (f"digest-extractive-v1:{node.level}:{node.period}",
                                                   f"model-digest-m35-v1:{node.level}:{node.period}")
                or m3.validity(db, version["id"], character_id)):
            continue
        if [(r["source_kind"], r["source_id"]) for r in m3._refs(db, version["id"])] != [
                (r["kind"], r["id"]) for r in _source_refs(node.members)]:
            continue
        if [d["child_version_id"] for d in m3._deps(db, version["id"])] != children:
            continue
        return version, "available"
    return None, "stale_or_unreviewed"


def _task_status(db, character_id, node):
    if not _table_exists(db, "mv3_jobs") or node.level not in ("L1", "L2", "L3", "L4"):
        return None
    expected = [(r["kind"], r["id"]) for r in _source_refs(node.members)]
    for job in db.execute("SELECT id,status,input_hash,payload_json FROM mv3_jobs WHERE character_id=? AND kind='DIGEST' ORDER BY created_at DESC",
                          (character_id,)):
        try:
            payload = json.loads(job["payload_json"])
            refs = [(r["kind"], str(r["id"])) for r in payload["refs"]]
        except (ValueError, KeyError, TypeError):
            continue
        if payload.get("level") == node.level and payload.get("period") == node.period and refs == expected:
            from . import memory_v2_jobs
            try:
                if memory_v2_jobs.snapshot(db, character_id, payload) != job["input_hash"]:
                    return "stale"
            except ValueError:
                return "stale"
            if _table_exists(db, "mv3_model_chunks") and db.execute("""SELECT 1 FROM mv3_model_chunks
                WHERE job_id=? AND state IN ('possibly_dispatched','unknown')""", (job["id"],)).fetchone():
                return "unknown"
            return job["status"]
    return None


def _view(db, character_id, node, *, preview_chars=2048, member_offset=0, member_limit=MAX_PAGE):
    version, status = _digest(db, character_id, node)
    first = node.members[0]
    shown = node.members[member_offset:member_offset + member_limit]
    next_member = member_offset + len(shown)
    result = {"nodeId": node.id, "level": node.level, "period": node.period,
              "timeBasis": node.time_basis, "entryIds": [m.entry_id for m in shown],
              "entryLabels": [{"entryId": m.entry_id, "title": m.detail["title"]} for m in shown],
              "sourceRefs": _source_refs(shown), "memberCount": len(node.members),
              "nextMemberOffset": next_member if next_member < len(node.members) else None,
              "sourceRefsIncomplete": next_member < len(node.members),
              "childNodeIds": [c.id for c in node.children],
              "summaryStatus": status, "taskStatus": _task_status(db, character_id, node),
              "digestVersionId": version["id"] if version else None,
              "futureTimestamp": any(m.future_timestamp for m in node.members),
              "title": first.detail["title"] if len(node.members) == 1 else node.period or "时间未知",
              "entrySynopsis": first.detail["synopsis"] if len(node.members) == 1 else None,
              "expandable": True}
    if version:
        from .memory_v2_metadata_contract import digest_token
        from . import memory_v2_temporal_digest_schema as a36_schema
        v2 = (db.execute("SELECT input_profile,input_token FROM mv3_time_digest_bindings_v2 "
                         "WHERE digest_version_id=?", (version['id'],)).fetchone()
              if a36_schema.installed(db) else None)
        if v2:
            result['digestInputProfile'] = v2['input_profile']
            result['digestToken'] = m3._hash(['digest-v2', v2['input_token'],
                version['id'], version['output_hash'], version['validation_binding']])
        else:
            result['digestInputProfile']='raw_sources_v1'
            result['digestToken']=digest_token('raw_sources_v1',node.scope_hash,
                [m.entry_id for m in node.members], [dict(d) for d in m3._deps(db,version['id'])],
                digest_revision=[version['id'],version['output_hash'],version['validation_binding']])
        result["summaryPreview"] = version["body"][:preview_chars]
        result["summaryIncomplete"] = len(version["body"]) > preview_chars
    return result


def _generation(db, character_id):
    if not _table_exists(db, "mv2_meta"):
        return None
    row = db.execute("SELECT active_generation,state,schema_version FROM mv2_meta WHERE character_id=?", (character_id,)).fetchone()
    return row["active_generation"] if row and row["state"] == "ready" and row["schema_version"] == 2 else None


def _cursor_encode(value):
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def _cursor_decode(value):
    try:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError()
        return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        raise ValueError("invalid_cursor") from None


def create_extractive_digest(db, character_id, node_id, *, now, offset_minutes=480):
    """Explicit local task output; binds its exact RAW scope before human review.

    This does not create a model task or approve the M3 version. M3 activation
    and source/dependency checks remain mandatory before browse can use it.
    """
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    if not _table_exists(db, "mv3_time_digest_bindings"):
        raise ValueError("p2_schema_missing")
    node = _find(_tree(db, character_id, now, offset_minutes), node_id)
    if node is None or node.level not in ("L1", "L2", "L3", "L4"):
        raise ValueError("digest_node_unavailable")
    children = []
    for child in node.children:
        child_version, _ = _digest(db, character_id, child)
        if child_version is None:
            raise ValueError("child_digest_missing")
        children.append(child_version["id"])
    refs = _source_refs(node.members)
    version = digest.create_digest(db, character_id, refs, level=node.level, period=node.period,
                                   child_version_ids=children)
    _insert_binding(db, character_id, node, version["id"])
    return version


def _insert_binding(db, character_id, node, version_id):
    db.execute("""INSERT OR IGNORE INTO mv3_time_digest_bindings(id,character_id,node_id,level,
        period,time_basis,input_profile,scope_hash,digest_version_id,created_at)
        VALUES(?,?,?,?,?,?,'raw_sources_v1',?,?,?)""",
        (str(uuid.uuid4()), character_id, node.id, node.level, node.period, node.time_basis,
         node.scope_hash, version_id, m3._now()))


def bind_reviewed_digest(db, character_id, node_id, version_id, *, now, offset_minutes=480):
    """Explicitly attach an already human-approved M3 digest to its current scope.

    Used for an M3.5 model version only after its existing M3 review/activation.
    It neither generates text nor promotes an unreviewed draft.
    """
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    if not _table_exists(db, "mv3_time_digest_bindings"):
        raise ValueError("p2_schema_missing")
    node = _find(_tree(db, character_id, now, offset_minutes), node_id)
    if node is None or node.level not in ("L1", "L2", "L3", "L4"):
        raise ValueError("digest_node_unavailable")
    version = m3._row(db, "mv3_versions", version_id)
    owner = m3._row(db, "mv3_derivations", version["derivation_id"]) if version else None
    if (owner is None or owner["character_id"] != character_id or owner["view_type"] != "DIGEST"
            or version["review_status"] != "human_checked"
            or version["rule_version"] not in (f"digest-extractive-v1:{node.level}:{node.period}",
                                               f"model-digest-m35-v1:{node.level}:{node.period}")
            or m3.validity(db, version_id, character_id)):
        raise ValueError("digest_not_reviewed_or_current")
    if [(r["source_kind"], r["source_id"]) for r in m3._refs(db, version_id)] != [
            (r["kind"], r["id"]) for r in _source_refs(node.members)]:
        raise ValueError("digest_source_scope_mismatch")
    children = []
    for child in node.children:
        selected, _ = _digest(db, character_id, child)
        if selected is None:
            raise ValueError("child_digest_missing")
        children.append(selected["id"])
    if [d["child_version_id"] for d in m3._deps(db, version_id)] != children:
        raise ValueError("digest_dependency_mismatch")
    _insert_binding(db, character_id, node, version_id)
    return {"digestVersionId": version_id, "nodeId": node.id, "scopeHash": node.scope_hash}


def browse(db, character_id, *, now, offset_minutes=480, levels=None, limit=20, cursor=None):
    """Read-only time tree roots; asOf is frozen across all cursor pages."""
    _checked_view(now, offset_minutes)
    if not 1 <= limit <= MAX_PAGE or levels is not None and not set(levels).issubset(
            {None, "L0", "L1", "L2", "L3", "L4", "L5"}):
        raise ValueError("invalid_browse_filter")
    roots = _tree(db, character_id, now, offset_minutes)
    if levels is not None:
        roots = [node for node in roots if node.level in levels]
    views = [_view(db, character_id, node) for node in roots]
    snapshot = m3._hash([(node.id, node.scope_hash, [(m.entry_id, m.version_id) for m in node.members],
                          view["summaryStatus"], view["digestVersionId"])
                         for node, view in zip(roots, views)])
    binding = {"characterId": character_id, "asOf": now.isoformat(), "offsetMinutes": offset_minutes,
               "levels": sorted(levels, key=str) if levels is not None else None,
               "generation": _generation(db, character_id), "snapshot": snapshot}
    offset = 0
    if cursor:
        parsed = _cursor_decode(cursor)
        if not isinstance(parsed, dict) or any(parsed.get(key) != value for key, value in binding.items()):
            raise ValueError("cursor_view_changed")
        offset = parsed.get("offset")
        if type(offset) is not int or not 0 <= offset < len(views):
            raise ValueError("invalid_cursor")
    page = views[offset:offset + limit]
    next_offset = offset + len(page)
    return {"asOf": now.isoformat(), "offsetMinutes": offset_minutes,
            "indexGeneration": binding["generation"],
            "state": "ready" if _table_exists(db, "mv3_entries") else "needs_schema",
            "nodes": page, "total": len(views),
            "nextCursor": _cursor_encode({**binding, "offset": next_offset}) if next_offset < len(views) else None}


def expand_node(db, character_id, node_id, *, now, offset_minutes=480, offset=0, limit=MAX_RAW_PAGE,
                member_limit=MAX_PAGE, member_cursor=None, expected_digest_version_id=None):
    """Open a node one level at a time; a valid digest body is paged independently."""
    if (type(offset) is not int or offset < 0 or not 1 <= limit <= MAX_RAW_PAGE
            or type(member_limit) is not int or not 1 <= member_limit <= MAX_PAGE):
        raise ValueError("invalid_page")
    node = _find(_tree(db, character_id, now, offset_minutes), node_id)
    if node is None:
        raise ValueError("node_missing_or_stale")
    binding = {"characterId": character_id, "asOf": now.isoformat(), "offsetMinutes": offset_minutes,
               "nodeId": node_id, "generation": _generation(db, character_id),
               "memberSnapshot": m3._hash([(m.entry_id, m.version_id) for m in node.members])}
    member_offset = 0
    if member_cursor:
        parsed = _cursor_decode(member_cursor)
        if not isinstance(parsed, dict) or any(parsed.get(key) != value for key, value in binding.items()):
            raise ValueError("cursor_view_changed")
        member_offset = parsed.get("offset")
        if type(member_offset) is not int or not 0 <= member_offset < len(node.members):
            raise ValueError("invalid_cursor")
    result = _view(db, character_id, node, preview_chars=0, member_offset=member_offset,
                   member_limit=member_limit)
    result["nextMemberCursor"] = (_cursor_encode({**binding, "offset": result["nextMemberOffset"]})
                                   if result["nextMemberOffset"] is not None else None)
    result.pop("summaryPreview", None)
    result.pop("summaryIncomplete", None)
    result["children"] = [_view(db, character_id, child, preview_chars=0) for child in node.children]
    version, _ = _digest(db, character_id, node)
    if offset and (version is None or expected_digest_version_id != version["id"]):
        raise ValueError("digest_page_changed")
    if version:
        result["summaryPage"] = version["body"][offset:offset + limit]
        result["nextOffset"] = offset + limit if offset + limit < len(version["body"]) else None
    else:
        result["summaryPage"] = None
        result["nextOffset"] = None
    return result


def unfold_raw(db, character_id, entry_id, source_ordinal, *, offset=0, limit=MAX_RAW_PAGE,
               expected_content_hash=None):
    """Explicit full RAW lookup by stable entry/source identity, independent of age."""
    if type(source_ordinal) is not int or type(offset) is not int or source_ordinal < 0 or offset < 0 or not 1 <= limit <= MAX_RAW_PAGE:
        raise ValueError("invalid_page")
    detail = entries.read_entry(db, entry_id, character_id)
    refs = entries._refs(db, detail["versionId"])
    if source_ordinal >= len(refs):
        raise ValueError("source_ordinal_missing")
    ref = refs[source_ordinal]
    source = m3.live_source(db, character_id, ref["source_kind"], ref["source_id"])
    if (source is None or source.content_hash != ref["content_hash"] or source.state_hash != ref["state_hash"]):
        raise ValueError("source_missing_or_stale")
    if expected_content_hash is not None and expected_content_hash != source.content_hash:
        raise ValueError("source_page_changed")
    text = source.text
    return {"entryId": entry_id, "sourceOrdinal": source_ordinal,
            "sourceRef": {"kind": ref["source_kind"], "id": ref["source_id"]},
            "sourceRole": source.role, "sourceStatus": source.status,
            "evidenceKind": source.evidence_kind, "contentHash": source.content_hash,
            "text": text[offset:offset + limit], "offset": offset, "totalLength": len(text),
            "nextOffset": offset + limit if offset + limit < len(text) else None}


def context_projection(db, character_id, selected_entry_ids, *, now, offset_minutes=480,
                       budget_tokens=512, max_blocks=5):
    """Project already selected experiences; never retrieve candidates or write cooldown."""
    _checked_view(now, offset_minutes)
    if (not isinstance(selected_entry_ids, (list, tuple)) or len(selected_entry_ids) > 50
            or any(not isinstance(entry_id, str) for entry_id in selected_entry_ids)
            or type(budget_tokens) is not int or not 0 <= budget_tokens <= MAX_BUDGET
            or type(max_blocks) is not int or not 1 <= max_blocks <= 20):
        raise ValueError("invalid_projection_request")
    roots = _tree(db, character_id, now, offset_minutes)
    all_members = {m.entry_id: (m, node) for node in roots for m in node.members}
    selected = set(selected_entry_ids)
    blocks, rejected, used, seen_nodes, seen_experiences = [], [], 0, set(), set()
    for entry_id in selected_entry_ids:
        if entry_id not in all_members:
            rejected.append({"entryId": entry_id, "reason": "entry_missing_or_stale"})
            continue
        member, node = all_members[entry_id]
        experience = m3._hash(sorted((r["source_kind"], r["source_id"]) for r in member.refs))
        if experience in seen_experiences:
            rejected.append({"entryId": entry_id, "reason": "duplicate_experience"})
            continue
        seen_experiences.add(experience)
        if len(blocks) >= max_blocks:
            rejected.append({"entryId": entry_id, "reason": "block_limit"})
            continue
        level = member.level
        version, digest_status = _digest(db, character_id, node)
        use_group = (level in ("L1", "L2", "L3", "L4") and
                     set(m.entry_id for m in node.members).issubset(selected) and version is not None)
        if use_group:
            if node.id in seen_nodes:
                continue
            candidates = [("period_digest", "DIGEST", version["body"], version["id"],
                           [m.entry_id for m in node.members], _source_refs(node.members))]
        elif level == "L0":
            try:
                narrative = m3.read_version(db, member.detail["narrativeVersionId"], character_id)
                candidates = [("detailed_narrative", "NARRATIVE", narrative["body"], narrative["id"],
                               [entry_id], _source_refs((member,)))]
            except ValueError:
                rejected.append({"entryId": entry_id, "reason": "narrative_stale"})
                continue
        else:
            candidates = []
        if level != "L5":
            candidates.append(("entry_synopsis", None, member.detail["synopsis"], None,
                               [entry_id], _source_refs((member,))))
        candidates.append(("title_index", None, member.detail["title"], None,
                           [entry_id], _source_refs((member,))))
        chosen = None
        for kind, view_type, text, version_id, entry_ids, refs in candidates:
            if not text:
                continue
            cost = estimate_tokens(text) + 12
            if text and used + cost <= budget_tokens:
                chosen = {"projectionKind": kind, "viewType": view_type, "level": level,
                          "period": member.period, "timeBasis": member.time_basis,
                          "entryIds": entry_ids, "sourceRefs": refs, "text": text,
                          "digestVersionId": version_id if kind == "period_digest" else None,
                          "narrativeVersionId": version_id if kind == "detailed_narrative" else None,
                          "summaryStatus": digest_status, "estimatedTokens": cost,
                          "authority": "system_derived_text", "eligibleForFormalRecall": False}
                break
        if chosen is None:
            rejected.append({"entryId": entry_id, "reason": "budget_exhausted"})
            continue
        if chosen["projectionKind"] == "period_digest":
            seen_nodes.add(node.id)
        blocks.append(chosen)
        used += chosen["estimatedTokens"]
    return {"characterId": character_id, "asOf": now.isoformat(),
            "indexGeneration": _generation(db, character_id), "blocks": blocks,
            "estimatedTokens": used, "rejected": rejected,
            "shadowOnly": True, "noModelCalls": True}
