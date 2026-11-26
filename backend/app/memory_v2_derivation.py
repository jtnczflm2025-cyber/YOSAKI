"""M3 provenance and version lifecycle. No model calls or application hooks."""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from . import memory_v2_read, memory_v2_sources
from .memory_v2_contract import SourceRef, experience_key


VIEWS = ("CLEANED", "NARRATIVE", "DIGEST")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row(db, table, row_id):
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    return dict(row) if row else None


def live_source(db, character_id, kind, source_id, file_cache=None):
    """Only the active, current M1 generation may become an M3 input."""
    if kind not in memory_v2_sources.KINDS:
        return None
    cache = memory_v2_read.source_cache(db)
    key = (character_id, kind, source_id)
    if cache is not None and key in cache:
        live = cache[key]
        # Message-path authorization is contextual, even without any database
        # change. Recheck it on every use of a previously validated source.
        from .chat_history import source_allowed, READ_SCOPE
        if kind == 'message' and not source_allowed(db, int(source_id), int(live.stream_id)):
            return None
        if kind == 'activity_operation' and READ_SCOPE.get() is not None:
            return None
        return live
    meta = db.execute("SELECT active_generation,state FROM mv2_meta WHERE character_id=?", (character_id,)).fetchone()
    if meta is None or meta["state"] != "ready":
        return None
    indexed = db.execute("""SELECT * FROM mv2_sources WHERE character_id=? AND generation=?
        AND source_kind=? AND source_id=?""",
        (character_id, meta["active_generation"], kind, source_id)).fetchone()
    live = memory_v2_read.valid_source(db, indexed, character_id, file_cache)
    if cache is not None and live is not None:
        cache[key] = live
    return live


def source_inputs(db, character_id, refs, file_cache=None):
    if not refs or len(refs) > 500:
        raise ValueError("source_count_invalid")
    seen, items = set(), []
    if file_cache is None:
        file_cache = {}
    for ordinal, ref in enumerate(refs):
        kind, source_id = ref["kind"], str(ref["id"])
        if (kind, source_id) in seen:
            raise ValueError("duplicate_source")
        seen.add((kind, source_id))
        live = live_source(db, character_id, kind, source_id, file_cache)
        if live is None:
            raise ValueError("source_missing_or_stale")
        items.append({"ordinal": ordinal, "source_kind": kind, "source_id": source_id,
                      "character_id": character_id, "source_role": live.role,
                      "evidence_kind": live.evidence_kind, "content_hash": live.content_hash,
                      "state_hash": live.state_hash})
    return items


def _refs(db, version_id):
    return [dict(row) for row in db.execute("""SELECT * FROM mv3_source_refs
        WHERE version_id=? ORDER BY ordinal""", (version_id,))]


def _deps(db, version_id):
    return [dict(row) for row in db.execute("""SELECT * FROM mv3_dependencies
        WHERE version_id=? ORDER BY ordinal""", (version_id,))]


def create_derivation(db, character_id, view_type, refs):
    if view_type not in VIEWS:
        raise ValueError("invalid_view")
    items = source_inputs(db, character_id, refs)
    key = experience_key(character_id, tuple(SourceRef(x["source_kind"], x["source_id"]) for x in items))
    existing = db.execute("""SELECT * FROM mv3_derivations WHERE character_id=? AND view_type=?
        AND experience_key=?""", (character_id, view_type, key)).fetchone()
    if existing:
        return dict(existing)
    derivation_id = str(uuid.uuid4())
    db.execute("""INSERT INTO mv3_derivations(id,character_id,view_type,experience_key,created_at)
        VALUES(?,?,?,?,?)""", (derivation_id, character_id, view_type, key, _now()))
    return _row(db, "mv3_derivations", derivation_id)


def _validity(db, version_id, character_id, visited, draft=False):
    if version_id in visited:
        return "dependency_cycle"
    version = _row(db, "mv3_versions", version_id)
    if version is None:
        return "dependency_missing"
    derivation = _row(db, "mv3_derivations", version["derivation_id"])
    if derivation is None or derivation["character_id"] != character_id:
        return "role_mismatch"
    if version["status"] != "active" and not (draft and version["status"] == "needs_review"):
        return version["status"]
    if _hash(version["body"]) != version["output_hash"]:
        return "body_changed"
    if not _refs(db, version_id):
        return "sources_missing"
    visited = visited | {version_id}
    for ref in _refs(db, version_id):
        if ref["character_id"] != character_id:
            return "role_mismatch"
        live = live_source(db, character_id, ref["source_kind"], ref["source_id"])
        if live is None:
            return "source_missing_or_stale"
        if (live.content_hash != ref["content_hash"] or live.state_hash != ref["state_hash"]
                or live.role != ref["source_role"] or live.evidence_kind != ref["evidence_kind"]):
            return "source_changed"
    if (derivation["view_type"] == "DIGEST" and version["rule_version"].startswith(
            ("digest-extractive-v1:", "model-digest-m35-v1:",
             "model-digest-a36-v1:"))):
        from . import memory_v2_time_projection as p2
        for ref in _refs(db, version_id):
            live = live_source(db, character_id, ref["source_kind"], ref["source_id"])
            evidence = (live.occurred_date if live.time_precision == "day"
                        else live.occurred_at if live.time_precision == "instant" else None)
            if p2._timestamp(evidence, 480) is None:
                return "digest_time_unverified"
    if version['rule_version'].startswith('dialogue-finite-'):
        from . import memory_v2_dialogue_refinement as finite
        reason = finite.invalid_reason(db, version, character_id)
        if reason:
            return reason
    # The rule version is a durable discriminator. Missing mapping markers on
    # a new version never fall back to the older M3 reader.
    if (derivation["view_type"] == "NARRATIVE" and
            version["rule_version"].startswith("model-narrative-a35-")):
        from . import memory_v2_narrative_processing as a35
        reason = a35.version_invalid_reason(db, version, character_id)
        if reason:
            return reason
    if (derivation["view_type"] == "DIGEST" and
            version["rule_version"].startswith("model-digest-a36-")):
        from . import memory_v2_temporal_digest as a36
        reason = a36.version_invalid_reason(db, version, character_id)
        if reason:
            return reason
    for dep in _deps(db, version_id):
        child = _row(db, "mv3_versions", dep["child_version_id"])
        if child is None or child["output_hash"] != dep["child_output_hash"]:
            return "dependency_changed"
        reason = _validity(db, child["id"], character_id, visited)
        if reason:
            return "dependency_" + reason
    if version["status"] == "active":
        if version["validation_status"] != "passed" or version["validation_binding"] != _binding(db, version):
            return "validation_binding_invalid"
        if version["review_status"] == "human_checked" and not db.execute(
                "SELECT 1 FROM mv3_reviews WHERE version_id=? AND binding=?",
                (version_id, version["validation_binding"])).fetchone():
            return "review_missing"
    return None


def validity(db, version_id, character_id):
    return _validity(db, version_id, character_id, set())


def add_version(db, derivation_id, refs, body, *, author_kind="user", rule_version="manual-v1",
                prompt_version=None, model_provider=None, model_id=None, child_version_ids=(), mapping=()):
    derivation = _row(db, "mv3_derivations", derivation_id)
    if derivation is None:
        raise ValueError("derivation_missing")
    max_body_chars = 200_000
    if author_kind == 'rule' and derivation['view_type'] == 'NARRATIVE':
        from . import memory_v2_dialogue_refinement as finite
        if rule_version == finite.RULE:
            max_body_chars = finite.MAX_BODY_CHARS
    if author_kind not in ("rule", "model", "user") or not isinstance(body, str) or not body.strip() or len(body) > max_body_chars:
        raise ValueError("invalid_version")
    character_id = derivation["character_id"]
    items = source_inputs(db, character_id, refs)
    key = experience_key(character_id, tuple(SourceRef(x["source_kind"], x["source_id"]) for x in items))
    if key != derivation["experience_key"]:
        raise ValueError("source_set_mismatch")
    dependencies = []
    if len(set(child_version_ids)) != len(child_version_ids):
        raise ValueError("duplicate_dependency")
    for child_id in child_version_ids:
        child = _row(db, "mv3_versions", child_id)
        owner = _row(db, "mv3_derivations", child["derivation_id"]) if child else None
        if owner is None or owner["character_id"] != character_id:
            raise ValueError("invalid_dependency")
        if owner['id'] == derivation_id:
            # Different temporal levels may cover exactly the same original sources.
            # Only a strictly lower, existing DIGEST version may be a child.
            import re
            parent_level = re.match(r'^(?:digest-extractive-v1|model-digest-m35-v1|model-digest-a36-v1):L([1-4]):', rule_version)
            child_level = re.match(r'^(?:digest-extractive-v1|model-digest-m35-v1|model-digest-a36-v1):L([1-4]):', child['rule_version'])
            if (derivation['view_type'] != 'DIGEST' or not parent_level or not child_level
                    or int(child_level[1]) >= int(parent_level[1])):
                raise ValueError('invalid_dependency')
        if validity(db, child_id, character_id):
            raise ValueError("dependency_not_active")
        child_sources = {(x["source_kind"], x["source_id"]) for x in _refs(db, child_id)}
        if not child_sources.issubset({(x["source_kind"], x["source_id"]) for x in items}):
            raise ValueError("dependency_outside_sources")
        dependencies.append((child_id, child["output_hash"]))
    input_hash = _hash([[(x["source_kind"], x["source_id"], x["content_hash"], x["state_hash"],
                          x["source_role"], x["evidence_kind"]) for x in items], dependencies,
                        rule_version, prompt_version, model_provider, model_id, mapping,
                        _hash(body) if author_kind == "model" else None])
    if author_kind in ("rule", "model"):
        prior = db.execute("""SELECT * FROM mv3_versions WHERE derivation_id=? AND input_hash=?
            AND author_kind=?""", (derivation_id, input_hash, author_kind)).fetchone()
        if prior:
            return dict(prior)
    version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM mv3_versions WHERE derivation_id=?",
                         (derivation_id,)).fetchone()[0]
    version_id = str(uuid.uuid4())
    db.execute("""INSERT INTO mv3_versions(id,derivation_id,version,body,status,review_status,
        author_kind,input_hash,output_hash,rule_version,prompt_version,model_provider,model_id,created_at)
        VALUES(?,?,?,?,'needs_review','unreviewed',?,?,?,?,?,?,?,?)""",
        (version_id, derivation_id, version, body, author_kind, input_hash, _hash(body), rule_version,
         prompt_version, model_provider, model_id, _now()))
    db.executemany("""INSERT INTO mv3_source_refs(version_id,ordinal,source_kind,source_id,
        character_id,source_role,evidence_kind,content_hash,state_hash) VALUES(?,?,?,?,?,?,?,?,?)""",
        [(version_id, x["ordinal"], x["source_kind"], x["source_id"], character_id,
          x["source_role"], x["evidence_kind"], x["content_hash"], x["state_hash"]) for x in items])
    db.executemany("""INSERT INTO mv3_dependencies(version_id,ordinal,child_version_id,child_output_hash)
        VALUES(?,?,?,?)""", [(version_id, i, cid, h) for i, (cid, h) in enumerate(dependencies)])
    db.execute('UPDATE mv3_versions SET mapping_json=? WHERE id=?',
               (json.dumps(mapping, ensure_ascii=False, sort_keys=True), version_id))
    return _row(db, "mv3_versions", version_id)


def _binding(db, version):
    return _hash([version["id"], version["input_hash"], _hash(version["body"]),
                  version["rule_version"], version["prompt_version"], version["model_provider"],
                  version["model_id"], version['author_kind'], version['mapping_json'],
                  _refs(db, version["id"]), _deps(db, version["id"])])


def review_version(db, version_id, character_id, reviewer_id, expected_output_hash, expected_input_hash):
    """Server-only operation. reviewer_id comes from authenticated session, never request JSON."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    if db.execute("SELECT 1 FROM users WHERE id=?", (reviewer_id,)).fetchone() is None:
        raise ValueError("authenticated_reviewer_required")
    reason = _validity(db, version_id, character_id, set(), draft=True)
    if reason:
        raise ValueError(reason)
    version = _row(db, "mv3_versions", version_id)
    if version["output_hash"] != expected_output_hash or version["input_hash"] != expected_input_hash:
        raise ValueError("review_snapshot_changed")
    binding = _binding(db, version)
    db.execute("""INSERT OR IGNORE INTO mv3_reviews(id,version_id,reviewer_id,binding,created_at)
        VALUES(?,?,?,?,?)""", (str(uuid.uuid4()), version_id, reviewer_id, binding, _now()))
    db.execute("UPDATE mv3_versions SET review_status='human_checked' WHERE id=?", (version_id,))
    return {"versionId": version_id, "binding": binding, "authority": "system_derived_text"}


def activate_version(db, version_id, character_id):
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    version = _row(db, "mv3_versions", version_id)
    if version is None:
        raise ValueError("version_missing")
    derivation = _row(db, "mv3_derivations", version["derivation_id"])
    if derivation["character_id"] != character_id:
        raise ValueError("role_mismatch")
    if version["status"] != "needs_review":
        raise ValueError("version_not_activatable")
    reason = _validity(db, version_id, character_id, set(), draft=True)
    if reason:
        raise ValueError(reason)
    binding = _binding(db, version)
    reviewed = db.execute("""SELECT 1 FROM mv3_reviews r JOIN users u ON u.id=r.reviewer_id
        WHERE r.version_id=? AND r.binding=?""", (version_id, binding)).fetchone()
    from .memory_v2_cleaning import verified_body
    sources = [live_source(db, character_id, r["source_kind"], r["source_id"]) for r in _refs(db, version_id)]
    texts = [source.text for source in sources]
    automatic = (derivation["view_type"] == "CLEANED" and version["author_kind"] == "rule"
                 and ((version["rule_version"] == "identity-v1" and version["body"] == "\n\n".join(texts))
                      or verified_body(sources, version["rule_version"], version["body"])))
    if not automatic and not reviewed:
        raise ValueError("valid_review_required")
    changed = db.execute("""UPDATE mv3_versions SET status='active',validation_status='passed',
        validation_binding=?,review_status=? WHERE id=? AND status='needs_review' AND output_hash=?""",
        (binding, "human_checked" if reviewed else "unreviewed", version_id, version["output_hash"])).rowcount
    if changed != 1:
        raise ValueError("activation_conflict")
    db.execute("UPDATE mv3_derivations SET active_version=? WHERE id=?",
               (version["version"], derivation["id"]))
    return _row(db, "mv3_versions", version_id)


def read_version(db, version_id, character_id):
    reason = validity(db, version_id, character_id)
    if reason:
        raise ValueError(reason)
    version = _row(db, "mv3_versions", version_id)
    if version["validation_status"] != "passed" or version["validation_binding"] != _binding(db, version):
        raise ValueError("validation_binding_invalid")
    return {**version, "evidence_kind": "system_derived_text", "formal_learning_approved": False}


def invalidate_stale(db, character_id):
    rows = db.execute("""SELECT v.id,v.derivation_id,v.version FROM mv3_versions v
        JOIN mv3_derivations d ON d.id=v.derivation_id
        WHERE d.character_id=? AND v.status='active'""", (character_id,)).fetchall()
    stale = []
    for row in rows:
        reason = validity(db, row["id"], character_id)
        if reason:
            stale.append((row, reason))
    for row, reason in stale:
        db.execute("UPDATE mv3_versions SET status='stale',stale_reason=? WHERE id=?", (reason, row["id"]))
        db.execute("""UPDATE mv3_derivations SET active_version=NULL WHERE id=? AND active_version=?""",
                   (row["derivation_id"], row["version"]))
    return len(stale)
