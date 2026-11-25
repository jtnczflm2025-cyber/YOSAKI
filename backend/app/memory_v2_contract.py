"""M2 read-only recall contracts. M3 views use the same source identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol


VIEW_TYPES = ("RAW", "CLEANED", "NARRATIVE", "DIGEST")
PURPOSES = ("chat", "volition", "activity", "explicit")
LANES = ("passive", "entity", "deliberate")
FUTURE_GENERATORS = ("semantic_similarity", "cross_time_topic", "context_change", "goal_link")


@dataclass(frozen=True, order=True)
class SourceRef:
    kind: str
    id: str


def experience_key(character_id: int, source_refs: tuple[SourceRef, ...]) -> str:
    """Views of the same source set share one key; narration order stays separate."""
    canonical = sorted({(ref.kind, ref.id) for ref in source_refs})
    if not canonical:
        raise ValueError("experience needs a source")
    raw = json.dumps([character_id, canonical], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecallRequest:
    character_id: int
    purpose: str
    lane: str
    topic: str
    conversation_id: int | None = None
    anchor_message_id: int | None = None
    excluded_refs: tuple[SourceRef, ...] = ()
    aliases: tuple[str, ...] = ()
    max_candidates: int = 5
    expected_generation: int | None = None

    def __post_init__(self):
        if self.purpose not in PURPOSES or self.lane not in LANES:
            raise ValueError("invalid recall mode")
        if not 1 <= self.max_candidates <= 20:
            raise ValueError("invalid candidate limit")
        if len(self.topic) > 20000:
            raise ValueError("topic too long")


@dataclass(frozen=True)
class CandidateSeed:
    source_refs: tuple[SourceRef, ...]
    generator: str
    matched_terms: tuple[str, ...] = ()
    match_kind: str = "term"
    entry_id: str | None = None
    entry_version_id: str | None = None


class CandidateGenerator(Protocol):
    name: str

    def generate(self, db, request: RecallRequest, generation: int) -> list[CandidateSeed]: ...


@dataclass(frozen=True)
class RecallCandidate:
    character_id: int
    view_type: str
    source_refs: tuple[SourceRef, ...]
    input_fingerprints: tuple[str, ...]
    experience_key: str
    evidence_kind: str
    source_role: str
    source_status: str
    text: str
    matched_terms: tuple[str, ...] = ()
    selected_because: tuple[str, ...] = ()
    occurred_at: str | None = None
    recorded_at: str | None = None
    indexed_generation: int | None = None
    source_texts: tuple[str, ...] = ()
    source_speakers: tuple[str, ...] = ()

    def __post_init__(self):
        if self.view_type not in VIEW_TYPES or len(self.source_refs) != len(self.input_fingerprints):
            raise ValueError("invalid candidate provenance")


@dataclass(frozen=True)
class RecallDecision:
    character_id: int
    index_generation: int | None
    selected: tuple[RecallCandidate, ...] = ()
    formal_references: tuple[RecallCandidate, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()
    status: str = "ok"
    trigger: str = "none"
    scan_limited: bool = False
    cooldown_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryBlock:
    view_type: str
    evidence_kind: str
    trust_label: str
    experience_key: str
    source_refs: tuple[SourceRef, ...]
    input_fingerprints: tuple[str, ...]
    content_fingerprint: str
    text: str
    speaker: str
    start_offset: int
    end_offset: int
    incomplete: bool
    estimated_tokens: int


@dataclass(frozen=True)
class ContextBundle:
    character_id: int
    index_generation: int | None
    blocks: tuple[MemoryBlock, ...] = ()
    estimated_tokens: int = 0
    token_method: str = "local_heuristic_not_billable"
    rejected: tuple[tuple[str, str], ...] = ()
    diagnostics: dict[str, object] = field(default_factory=dict)
