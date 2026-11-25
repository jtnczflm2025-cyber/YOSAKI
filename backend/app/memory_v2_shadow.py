"""No-write comparison helpers. Never register on the live YOSAKI app in M2."""

from __future__ import annotations

from .memory_v2_context import compile_context
from .memory_v2_recall import recall


def compare(db, request, *, legacy_memory_ids=(), budget_tokens=512,
            cooldown=None, now=None, generators=None):
    before = db.total_changes
    decision = recall(db, request, cooldown=cooldown, now=now, generators=generators)
    bundle = compile_context(decision, budget_tokens=budget_tokens)
    if db.total_changes != before:
        raise RuntimeError("shadow comparison wrote to SQLite")
    formal = tuple(f"{item.source_refs[0].kind}:{item.source_refs[0].id}"
                   for item in decision.formal_references)
    evidence = tuple(f"{item.source_refs[0].kind}:{item.source_refs[0].id}"
                     for item in decision.selected)
    return {"status": decision.status, "trigger": decision.trigger,
            "indexGeneration": decision.index_generation,
            "formalReferenceIds": formal, "historicalEvidenceIds": evidence,
            "legacyReferenceIds": tuple(legacy_memory_ids),
            "compiledSourceIds": tuple(f"{block.source_refs[0].kind}:{block.source_refs[0].id}"
                                       for block in bundle.blocks),
            "rejected": bundle.rejected, "estimatedTokens": bundle.estimated_tokens,
            "tokenMethod": bundle.token_method, "databaseWrites": 0,
            "rawTextStored": False, "shadowOnly": True}
