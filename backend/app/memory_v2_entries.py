"""P1-A: versioned, source-bound metadata for one Chronicle experience.

RAW remains in M1 sources; detailed text remains an M3 NARRATIVE version. This
module is not installed in the application and never calls a model.
"""

import json
import re
import uuid

from . import memory_v2_derivation as m3


def _entry(db, entry_id):
    return m3._row(db, "mv3_entries", entry_id)


def _version(db, version_id):
    return m3._row(db, "mv3_entry_versions", version_id)


def _refs(db, version_id):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_entry_field_sets'").fetchone():
        snapshot=db.execute('SELECT sources_json FROM mv3_entry_field_sets WHERE id=?',(version_id,)).fetchone()
        if snapshot: return json.loads(snapshot['sources_json'])
    return [dict(row) for row in db.execute(
        "SELECT * FROM mv3_entry_sources WHERE version_id=? ORDER BY ordinal", (version_id,))]


def _begin(db):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


def _list(values, name):
    if not isinstance(values, (list, tuple)) or len(values) > 32:
        raise ValueError(name + "_invalid")
    cleaned = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError(name + "_invalid")
        item = value.strip()
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _metadata(title, keywords, tags, synopsis):
    if (not isinstance(title, str) or not title.strip() or len(title) > 200
            or not isinstance(synopsis, str) or not synopsis.strip() or len(synopsis) > 3000):
        raise ValueError("metadata_invalid")
    return title.strip(), _list(keywords, "keywords"), _list(tags, "tags"), synopsis.strip()


def _times(sources):
    reliable = [s for s in sources if s.time_precision in ("instant", "day") and
                (s.occurred_at or s.occurred_date)]
    complete_occurrence = len(reliable) == len(sources)
    date_precision = any(s.time_precision != "instant" for s in reliable)
    occurred = sorted((s.occurred_date if date_precision else s.occurred_at)
                      for s in reliable) if complete_occurrence else sorted(
                          s.occurred_at for s in reliable if s.occurred_at)
    recorded = sorted(s.recorded_at for s in sources if s.recorded_at)
    has_unverified_local = any(s.time_precision == "local_datetime_unverified" for s in sources)
    if len(occurred) == len(sources):
        basis = "occurred"
    elif len(recorded) == len(sources) and not occurred and not has_unverified_local:
        basis = "recorded"
    elif occurred or recorded:
        basis = "mixed"
    else:
        basis = "unknown"
    return (occurred[0] if occurred else None, occurred[-1] if occurred else None,
            recorded[0] if recorded else None, recorded[-1] if recorded else None, basis)


def _check_title_date(title, sources, time_basis):
    """A recognized date in a title must be supported by an occurrence, not import time."""
    dates = set(re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", title))
    dates.update(f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in
                 re.findall(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日", title))
    if dates and (time_basis != "occurred" or not dates.issubset(
            {source.occurred_date or source.occurred_at[:10] for source in sources if source.occurred_date or source.occurred_at})):
        raise ValueError("title_date_unverified")


def _narrative(db, character_id, narrative_version_id, refs):
    if narrative_version_id is None:
        return None
    version = m3._row(db, "mv3_versions", narrative_version_id)
    owner = m3._row(db, "mv3_derivations", version["derivation_id"]) if version else None
    if (owner is None or owner["character_id"] != character_id or owner["view_type"] != "NARRATIVE"
            or owner["active_version"] != version["version"]
            or m3.validity(db, narrative_version_id, character_id)):
        raise ValueError("narrative_not_current")
    narrative_refs = [(r["source_kind"], r["source_id"]) for r in m3._refs(db, narrative_version_id)]
    if narrative_refs != [(r["source_kind"], r["source_id"]) for r in refs]:
        raise ValueError("narrative_source_order_mismatch")
    return version


def create_entry(db, character_id):
    _begin(db)
    if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
        raise ValueError("role_unavailable")
    entry_id = str(uuid.uuid4())
    db.execute("INSERT INTO mv3_entries(id,character_id,status,created_at) VALUES(?,?,'draft',?)",
               (entry_id, character_id, m3._now()))
    return _entry(db, entry_id)


def add_version(db, entry_id, source_refs, title, keywords, tags, synopsis,
                narrative_version_id=None, edited_by=None):
    _begin(db)
    entry = _entry(db, entry_id)
    if entry is None or entry["status"] in ("archived", "superseded"):
        raise ValueError("entry_unavailable")
    title, keywords, tags, synopsis = _metadata(title, keywords, tags, synopsis)
    refs = m3.source_inputs(db, entry["character_id"], source_refs)
    first = db.execute("SELECT id FROM mv3_entry_versions WHERE entry_id=? ORDER BY version LIMIT 1",
                       (entry_id,)).fetchone()
    if first and {(r["source_kind"], r["source_id"]) for r in _refs(db, first["id"])} != {
            (r["source_kind"], r["source_id"]) for r in refs}:
        raise ValueError("source_set_requires_restructure")
    sources = [m3.live_source(db, entry["character_id"], r["source_kind"], r["source_id"]) for r in refs]
    narrative = _narrative(db, entry["character_id"], narrative_version_id, refs)
    if edited_by is not None and not db.execute("SELECT 1 FROM users WHERE id=?", (edited_by,)).fetchone():
        raise ValueError("editor_unavailable")
    times = _times(sources)
    _check_title_date(title, sources, times[-1])
    source_hash = m3._hash([(r["source_kind"], r["source_id"], r["content_hash"],
                             r["state_hash"], r["source_role"], r["evidence_kind"]) for r in refs])
    metadata_hash = m3._hash([title, keywords, tags, synopsis, times, narrative_version_id,
                              narrative["output_hash"] if narrative else None])
    number = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM mv3_entry_versions WHERE entry_id=?",
                        (entry_id,)).fetchone()[0]
    version_id = str(uuid.uuid4())
    db.execute("""INSERT INTO mv3_entry_versions(id,entry_id,version,title,keywords_json,tags_json,
        synopsis,narrative_version_id,occurred_start,occurred_end,recorded_start,recorded_end,
        time_basis,source_hash,metadata_hash,status,review_status,edited_by,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft','unreviewed',?,?)""",
        (version_id, entry_id, number, title, json.dumps(keywords, ensure_ascii=False),
         json.dumps(tags, ensure_ascii=False), synopsis, narrative_version_id, *times,
         source_hash, metadata_hash, edited_by, m3._now()))
    db.executemany("""INSERT INTO mv3_entry_sources(version_id,ordinal,source_kind,source_id,
        character_id,source_role,evidence_kind,content_hash,state_hash) VALUES(?,?,?,?,?,?,?,?,?)""",
        [(version_id, r["ordinal"], r["source_kind"], r["source_id"], entry["character_id"],
          r["source_role"], r["evidence_kind"], r["content_hash"], r["state_hash"]) for r in refs])
    return _version(db, version_id)


def _binding(db, version):
    return m3._hash([version["id"], version["entry_id"], version["metadata_hash"],
                     version["source_hash"], _refs(db, version["id"]),
                     version["narrative_version_id"]])


def validity(db, version_id, character_id, *, preview=False):
    version = _version(db, version_id)
    if version is None:
        return "entry_version_missing"
    entry = _entry(db, version["entry_id"])
    if entry is None or entry["character_id"] != character_id:
        return "role_mismatch"
    if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
        return "role_unavailable"
    if not preview and (entry["status"] != "active" or entry["active_version_id"] != version_id
                        or version["status"] != "active"):
        return "entry_not_active"
    if preview and version["status"] not in ("draft", "active"):
        return "entry_version_stale"
    if (version["metadata_hash"] != m3._hash([version["title"], json.loads(version["keywords_json"]),
            json.loads(version["tags_json"]), version["synopsis"],
            (version["occurred_start"], version["occurred_end"], version["recorded_start"],
             version["recorded_end"], version["time_basis"]), version["narrative_version_id"],
            _narrative_hash(db, version["narrative_version_id"])])):
        return "metadata_changed"
    refs = _refs(db, version_id)
    if not refs:
        return "sources_missing"
    if version["source_hash"] != m3._hash([(r["source_kind"], r["source_id"], r["content_hash"],
            r["state_hash"], r["source_role"], r["evidence_kind"]) for r in refs]):
        return "source_binding_changed"
    for ref in refs:
        if ref["character_id"] != character_id:
            return "role_mismatch"
        live = m3.live_source(db, character_id, ref["source_kind"], ref["source_id"])
        if live is None:
            return "source_missing_or_stale"
        if (live.content_hash != ref["content_hash"] or live.state_hash != ref["state_hash"]
                or live.role != ref["source_role"] or live.evidence_kind != ref["evidence_kind"]):
            return "source_changed"
    if version["narrative_version_id"]:
        try:
            _narrative(db, character_id, version["narrative_version_id"], refs)
        except ValueError as exc:
            return str(exc)
    elif not preview:
        return "narrative_missing"
    if version["status"] == "active":
        if version["review_status"] != "human_checked" or version["review_binding"] != _binding(db, version):
            return "review_binding_invalid"
        if not db.execute("""SELECT 1 FROM mv3_entry_reviews r JOIN users u ON u.id=r.reviewer_id
            WHERE r.version_id=? AND r.binding=?""", (version_id, version["review_binding"])).fetchone():
            return "review_missing"
    return None


def _narrative_hash(db, version_id):
    row = m3._row(db, "mv3_versions", version_id) if version_id else None
    return row["output_hash"] if row else None


def review_version(db, version_id, character_id, reviewer_id, expected_metadata_hash,
                   expected_source_hash):
    _begin(db)
    if not db.execute("SELECT 1 FROM users WHERE id=?", (reviewer_id,)).fetchone():
        raise ValueError("authenticated_reviewer_required")
    version = _version(db, version_id)
    if version is None or version["status"] != "draft":
        raise ValueError("entry_version_not_reviewable")
    reason = validity(db, version_id, character_id, preview=True)
    if reason:
        raise ValueError(reason)
    if (version["metadata_hash"] != expected_metadata_hash
            or version["source_hash"] != expected_source_hash):
        raise ValueError("review_snapshot_changed")
    if not version["narrative_version_id"]:
        raise ValueError("narrative_missing")
    binding = _binding(db, version)
    db.execute("""INSERT OR IGNORE INTO mv3_entry_reviews(id,version_id,reviewer_id,binding,created_at)
        VALUES(?,?,?,?,?)""", (str(uuid.uuid4()), version_id, reviewer_id, binding, m3._now()))
    db.execute("UPDATE mv3_entry_versions SET review_status='human_checked',review_binding=? WHERE id=?",
               (binding, version_id))
    return {"versionId": version_id, "reviewStatus": "human_checked", "authority": "system_derived_text"}


def activate_version(db, version_id, character_id):
    _begin(db)
    version = _version(db, version_id)
    if version is None or version["status"] != "draft":
        raise ValueError("entry_version_not_activatable")
    reason = validity(db, version_id, character_id, preview=True)
    if reason:
        raise ValueError(reason)
    binding = _binding(db, version)
    if version["review_binding"] != binding or not db.execute(
            "SELECT 1 FROM mv3_entry_reviews WHERE version_id=? AND binding=?",
            (version_id, binding)).fetchone():
        raise ValueError("valid_review_required")
    entry = _entry(db, version["entry_id"])
    if entry["status"] in ("archived", "superseded"):
        raise ValueError("entry_unavailable")
    old = entry["active_version_id"]
    if old:
        db.execute("UPDATE mv3_entry_versions SET status='stale' WHERE id=? AND status='active'", (old,))
    if db.execute("UPDATE mv3_entry_versions SET status='active' WHERE id=? AND status='draft'",
                  (version_id,)).rowcount != 1:
        raise ValueError("activation_conflict")
    db.execute("UPDATE mv3_entries SET status='active',active_version_id=? WHERE id=?",
               (version_id, entry["id"]))
    return _version(db, version_id)


def read_entry(db, entry_id, character_id):
    entry = _entry(db, entry_id)
    if entry is None or entry["character_id"] != character_id:
        raise ValueError("entry_unavailable")
    if entry.get('contract_version') == 2:
        from . import memory_v2_metadata
        return memory_v2_metadata.read_active(db,entry_id,character_id)
    version_id = entry["active_version_id"]
    if version_id is None:
        raise ValueError("entry_not_active")
    if (_version(db, version_id) or {}).get("entry_id") != entry_id:
        raise ValueError("entry_version_owner_mismatch")
    reason = validity(db, version_id, character_id)
    if reason:
        raise ValueError(reason)
    version = _version(db, version_id)
    return {"entryId": entry_id, "versionId": version_id, "title": version["title"],
            "keywords": json.loads(version["keywords_json"]), "tags": json.loads(version["tags_json"]),
            "synopsis": version["synopsis"], "narrativeVersionId": version["narrative_version_id"],
            "occurredAt": [version["occurred_start"], version["occurred_end"]],
            "recordedAt": [version["recorded_start"], version["recorded_end"]],
            "timeBasis": version["time_basis"],
            "sourceRefs": [{"kind": r["source_kind"], "id": r["source_id"]} for r in _refs(db, version_id)],
            "authority": "system_derived_text"}


def dependency_tokens(db, version_id, character_id):
    """Explicit aspect hashes for later indexes/digests; no downstream writes in P1-A."""
    reason = validity(db, version_id, character_id)
    if reason:
        raise ValueError(reason)
    v = _version(db, version_id)
    return {name: m3._hash(value) for name, value in {
        "source": v["source_hash"], "title": v["title"], "keywords": v["keywords_json"],
        "tags": v["tags_json"], "synopsis": v["synopsis"],
        "narrative": [v["narrative_version_id"], _narrative_hash(db, v["narrative_version_id"])],
        "time": [v["occurred_start"], v["occurred_end"], v["recorded_start"],
                 v["recorded_end"], v["time_basis"]],
    }.items()}


def propose_restructure(db, parent_ids, child_specs, operation, character_id):
    """Stage a split/merge. Parents remain active until every child is reviewed and active."""
    _begin(db)
    if operation not in ("split", "merge") or len(set(parent_ids)) != len(parent_ids):
        raise ValueError("invalid_restructure")
    if (operation == "split" and (len(parent_ids) != 1 or len(child_specs) < 2)
            or operation == "merge" and (len(parent_ids) < 2 or len(child_specs) != 1)):
        raise ValueError("invalid_restructure")
    if any(db.execute("SELECT 1 FROM mv3_entry_lineage WHERE parent_id=? AND status='proposed'",
                      (parent,)).fetchone() for parent in parent_ids):
        raise ValueError("restructure_already_proposed")
    parents = [read_entry(db, parent, character_id) for parent in parent_ids]
    original = [(r["kind"], r["id"]) for parent in parents for r in parent["sourceRefs"]]
    proposed = [(r["kind"], str(r["id"])) for spec in child_specs for r in spec["sourceRefs"]]
    if len(original) != len(set(original)) or len(proposed) != len(set(proposed)) or set(original) != set(proposed):
        raise ValueError("restructure_source_partition_invalid")
    if operation == "merge" and proposed != original:
        raise ValueError("restructure_order_invalid")
    if operation == "split":
        positions = [original.index(ref) for ref in proposed]
        if positions != sorted(positions):
            raise ValueError("restructure_order_invalid")
    children = []
    for spec in child_specs:
        child = create_entry(db, character_id)
        add_version(db, child["id"], spec["sourceRefs"], spec["title"], spec["keywords"],
                    spec["tags"], spec["synopsis"], spec.get("narrativeVersionId"), spec.get("editedBy"))
        children.append(child["id"])
    for parent in parent_ids:
        for child in children:
            db.execute("""INSERT INTO mv3_entry_lineage(parent_id,child_id,operation,created_at)
                VALUES(?,?,?,?)""", (parent, child, operation, m3._now()))
    return children


def finalize_restructure(db, parent_ids, child_ids, character_id):
    _begin(db)
    if not parent_ids or not child_ids or len(set(parent_ids)) != len(parent_ids) or len(set(child_ids)) != len(child_ids):
        raise ValueError("lineage_incomplete")
    for parent in parent_ids:
        read_entry(db, parent, character_id)
    for child in child_ids:
        read_entry(db, child, character_id)
    for parent in parent_ids:
        linked = {r[0] for r in db.execute("SELECT child_id FROM mv3_entry_lineage WHERE parent_id=? AND status='proposed'", (parent,))}
        if linked != set(child_ids):
            raise ValueError("lineage_incomplete")
    for child in child_ids:
        linked = {r[0] for r in db.execute("SELECT parent_id FROM mv3_entry_lineage WHERE child_id=? AND status='proposed'", (child,))}
        if linked != set(parent_ids):
            raise ValueError("lineage_incomplete")
    for parent in parent_ids:
        db.execute("UPDATE mv3_entries SET status='superseded' WHERE id=?", (parent,))
    db.executemany("UPDATE mv3_entry_lineage SET status='finalized' WHERE parent_id=? AND child_id=?",
                   [(parent, child) for parent in parent_ids for child in child_ids])
    return {"superseded": list(parent_ids), "activeChildren": list(child_ids)}


def cancel_restructure(db, parent_ids, child_ids, character_id):
    """Cancel only an unfinalized proposal; retain lineage and draft metadata for audit."""
    _begin(db)
    for parent in parent_ids:
        read_entry(db, parent, character_id)
    for child in child_ids:
        entry = _entry(db, child)
        if entry is None or entry["character_id"] != character_id or entry["status"] != "draft":
            raise ValueError("restructure_not_cancellable")
    for parent in parent_ids:
        linked = {r[0] for r in db.execute("SELECT child_id FROM mv3_entry_lineage WHERE parent_id=? AND status='proposed'", (parent,))}
        if linked != set(child_ids):
            raise ValueError("lineage_incomplete")
    for child in child_ids:
        linked = {r[0] for r in db.execute("SELECT parent_id FROM mv3_entry_lineage WHERE child_id=? AND status='proposed'", (child,))}
        if linked != set(parent_ids):
            raise ValueError("lineage_incomplete")
    db.executemany("UPDATE mv3_entry_lineage SET status='cancelled' WHERE parent_id=? AND child_id=?",
                   [(parent, child) for parent in parent_ids for child in child_ids])
    db.executemany("UPDATE mv3_entries SET status='archived' WHERE id=?", [(child,) for child in child_ids])
    return {"retainedParents": list(parent_ids), "archivedDraftChildren": list(child_ids)}
