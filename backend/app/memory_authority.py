"""Read-only eligibility checks for Phase 5 managed memory projections.

Learning records own approved text and status. A row in ``memories`` is only a
projection when a learning record points at it; invalid projections must never
fall back to the ordinary manual-memory path.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict


logger = logging.getLogger(__name__)


def _ids(value):
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        return None
    if len(parsed) != len(set(parsed)):
        return None
    return parsed


def _diagnose(character_id, memory_id, reason, learning_id=None):
    # Identifiers and reason only: memory bodies and evidence are not logged.
    logger.warning("memory_projection_ineligible character=%s memory=%s learning=%s reason=%s",
                   character_id, memory_id, learning_id, reason)


def eligible_rows(db, character_id):
    """Return (manual rows, (projection, approved learning row) pairs).

    All checks run before callers apply their own ordering, topic filter, or
    limit. The helper never changes the database.
    """
    memories = list(db.execute("SELECT * FROM memories WHERE character_id=?", (character_id,)))
    by_memory = {row["id"]: row for row in memories}
    learning = list(db.execute("""SELECT * FROM learning_memories
        WHERE character_id=? OR memory_id IN
        (SELECT id FROM memories WHERE character_id=?)""", (character_id, character_id)))
    by_learning = {row["id"]: row for row in learning}
    links = defaultdict(list)
    for row in learning:
        if row["memory_id"] is not None:
            links[row["memory_id"]].append(row)

    for row in learning:
        if row["character_id"] == character_id and row["status"] == "accepted" and row["memory_id"] not in by_memory:
            _diagnose(character_id, row["memory_id"], "accepted_projection_missing_or_wrong_role", row["id"])

    manual, approved = [], []
    for memory in memories:
        linked = links[memory["id"]]
        if not linked:
            if memory["enabled"]:
                manual.append(memory)
            continue

        accepted = [row for row in linked if row["status"] == "accepted"]
        if len(accepted) != 1:
            _diagnose(character_id, memory["id"], "ambiguous_approved_owner")
            continue
        owner = accepted[0]
        if owner["character_id"] != character_id:
            _diagnose(character_id, memory["id"], "owner_role_mismatch", owner["id"])
            continue
        if not memory["enabled"] or memory["scope"] != "long" or memory["conversation_id"] is not None:
            _diagnose(character_id, memory["id"], "projection_not_enabled_long_global", owner["id"])
            continue
        if memory["content"] != owner["content"]:
            _diagnose(character_id, memory["id"], "approved_body_mismatch", owner["id"])
            continue

        merged_ids = _ids(owner["merged_ids_json"])
        sources = _ids(owner["source_ids_json"])
        if merged_ids is None or sources is None or owner["id"] in merged_ids:
            _diagnose(character_id, memory["id"], "invalid_merge_metadata", owner["id"])
            continue
        lineage_ok = True
        for merged_id in merged_ids:
            alias = by_learning.get(merged_id)
            alias_sources = _ids(alias["source_ids_json"]) if alias else None
            if (alias is None or alias["character_id"] != character_id or alias["status"] != "merged"
                    or alias["subject"] != owner["subject"] or alias["memory_id"] not in (None, memory["id"])
                    or alias_sources is None or not set(alias_sources).issubset(sources)):
                lineage_ok = False
                break
        if lineage_ok:
            lineage_ok = all(row["id"] == owner["id"] or
                             (row["status"] == "merged" and row["character_id"] == character_id
                              and row["id"] in merged_ids)
                             for row in linked)
        if not lineage_ok:
            _diagnose(character_id, memory["id"], "invalid_merge_lineage_or_binding", owner["id"])
            continue
        approved.append((memory, owner))
    return manual, approved


def recent_long_rows(db, character_id, limit=5):
    manual, approved = eligible_rows(db, character_id)
    rows = [row for row in manual if row["scope"] == "long" and row["conversation_id"] is None]
    rows.extend(memory for memory, _ in approved)
    return sorted(rows, key=lambda row: row["id"], reverse=True)[:limit]


def approved_learning_rows(db, character_id):
    _, approved = eligible_rows(db, character_id)
    return sorted(approved, key=lambda pair: (pair[1]["updated_at"], pair[1]["id"]), reverse=True)
