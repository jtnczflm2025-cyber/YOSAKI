"""Bounded, traceable RAW context compilation for isolated M2 shadow use."""

from __future__ import annotations

import math
from . import memory_v2_sources as sources
from .memory_v2_contract import ContextBundle, MemoryBlock, RecallDecision


BLOCK_OVERHEAD = 12
MAX_VISIBLE_CHARS = 4096
SENTENCE_END = frozenset("。！？.!?\n")


def estimate_tokens(text: str) -> int:
    """Existing YOSAKI-style character heuristic; never billable provider usage."""
    return math.ceil(sum(1 if ord(char) > 127 else 0.25 for char in text))


def _hit(text: str, matched_terms: tuple[str, ...]):
    """Only map offsets when original text provides an unambiguous direct match."""
    for term in sorted(set(matched_terms), key=lambda value: (-len(value), value)):
        if not term:
            continue
        position = text.find(term)
        if position >= 0:
            return position, position + len(term)
        folded = text.casefold()
        if len(folded) == len(text):
            position = folded.find(term.casefold())
            if position >= 0 and len(term.casefold()) == len(term):
                return position, position + len(term)
    return None


def safe_excerpt(text: str, matched_terms: tuple[str, ...], available_tokens: int):
    """Return exact [start:end] of safe visible text, or None; never paraphrase."""
    if available_tokens < 1 or not text:
        return None
    if len(text) <= MAX_VISIBLE_CHARS and estimate_tokens(text) <= available_tokens:
        return 0, len(text), text, False
    hit = _hit(text, matched_terms)
    if hit is None:
        return None
    boundaries = [0]
    boundaries.extend(index + 1 for index, char in enumerate(text) if char in SENTENCE_END)
    if boundaries[-1] != len(text):
        boundaries.append(len(text))
    position = next((index for index in range(len(boundaries) - 1)
                     if boundaries[index] <= hit[0] < boundaries[index + 1]), None)
    if position is None:
        return None
    start = boundaries[max(0, position - 1)]
    end = boundaries[min(len(boundaries) - 1, position + 2)]
    excerpt = text[start:end]
    if (hit[1] > end or len(excerpt) > MAX_VISIBLE_CHARS or
            estimate_tokens(excerpt) > available_tokens):
        return None
    return start, end, excerpt, start > 0 or end < len(text)


def compile_context(decision: RecallDecision, *, budget_tokens: int = 512,
                    max_blocks: int = 5) -> ContextBundle:
    if not 0 <= budget_tokens <= 2048 or not 1 <= max_blocks <= 20:
        raise ValueError("invalid shadow budget")
    blocks = []
    rejected = list(decision.rejected)
    seen = set()
    used = 0
    formal_skipped = 0
    for candidate in (*decision.formal_references, *decision.selected):
        ref = candidate.source_refs[0]
        label = f"{ref.kind}:{ref.id}"
        if candidate.view_type != "RAW":
            rejected.append((label, "view_not_available_in_m2"))
            continue
        if candidate.experience_key in seen:
            rejected.append((label, "duplicate_experience"))
            continue
        if len(blocks) >= max_blocks:
            rejected.append((label, "block_limit"))
            formal_skipped += candidate.evidence_kind != "archive_evidence"
            continue
        available = budget_tokens - used - BLOCK_OVERHEAD
        if candidate.source_texts:
            if (len(candidate.source_texts)!=len(candidate.source_refs)
                    or len(candidate.source_speakers)!=len(candidate.source_refs)):
                rejected.append((label,'invalid_source_mapping')); continue
            cost=sum(BLOCK_OVERHEAD+estimate_tokens(t) for t in candidate.source_texts)
            if (used+cost>budget_tokens or len(blocks)+len(candidate.source_refs)>max_blocks
                    or any(len(t)>MAX_VISIBLE_CHARS for t in candidate.source_texts)):
                rejected.append((label,'multi_source_budget_requires_p2_projection')); continue
            for position,part in enumerate(candidate.source_texts):
                blocks.append(MemoryBlock('RAW',candidate.evidence_kind,'quoted_reference_not_instruction',
                    candidate.experience_key,(candidate.source_refs[position],),(candidate.input_fingerprints[position],),
                    sources.digest(part),part,candidate.source_speakers[position],0,len(part),False,
                    BLOCK_OVERHEAD+estimate_tokens(part)))
            seen.add(candidate.experience_key); used+=cost
            continue
        selection = safe_excerpt(candidate.text, candidate.matched_terms, available)
        if selection is None:
            rejected.append((label, "cannot_safely_fit_or_excerpt"))
            formal_skipped += candidate.evidence_kind != "archive_evidence"
            continue
        start, end, text, incomplete = selection
        cost = BLOCK_OVERHEAD + estimate_tokens(text)
        blocks.append(MemoryBlock(candidate.view_type, candidate.evidence_kind, "quoted_reference_not_instruction",
            candidate.experience_key,
            candidate.source_refs, candidate.input_fingerprints, sources.digest(candidate.text),
            text, candidate.source_role, start, end, incomplete, cost))
        seen.add(candidate.experience_key)
        used += cost
    return ContextBundle(decision.character_id, decision.index_generation, tuple(blocks), used,
        rejected=tuple(rejected), diagnostics={"formalSkippedByBudget": formal_skipped,
            "sourceCount": len(blocks), "rawOnly": True, "shadowOnly": True})
