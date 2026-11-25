"""Pure-read M2 candidate generation and authority-preserving selection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping, Sequence

from . import memory_authority, memory_v2_read, memory_v2_sources as sources
from .memory_v2_contract import (CandidateSeed, RecallCandidate, RecallDecision,
                                 RecallRequest, SourceRef, experience_key)


MAX_SEEDS = 1000
RECALL_CUES = ("还记得", "以前", "上次", "那次", "之前", "回忆", "覚えて", "remember", "last time")
COMMON_TERMS = {"阿夜", "我们", "你们", "他们", "什么", "怎么", "这个", "那个"}
PENDING_ACTIVITY = {"prepared", "approved", "pending_confirmation", "running"}


def _key(character_id, conversation_id, candidate):
    payload = [character_id, conversation_id, candidate.experience_key, list(candidate.input_fingerprints)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _time_rank(candidate):
    stamp = candidate.occurred_at or candidate.recorded_at
    if not stamp:
        return 0.0
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _term_seeds(db, request, generation, terms, generator):
    terms = sorted(set(terms))[:64]
    if not terms:
        return []
    placeholders = ",".join("?" for _ in terms)
    rows = db.execute(f"""SELECT s.source_kind,s.source_id FROM mv2_sources s
        JOIN mv2_terms t ON t.generation=s.generation AND t.source_kind=s.source_kind
            AND t.source_id=s.source_id
        WHERE s.character_id=? AND s.generation=? AND t.term IN ({placeholders})
        GROUP BY s.source_kind,s.source_id ORDER BY s.source_kind,s.source_id LIMIT ?""",
        (request.character_id, generation, *terms, MAX_SEEDS + 1)).fetchall()
    return [CandidateSeed((SourceRef(row["source_kind"], row["source_id"]),), generator,
                          tuple(terms)) for row in rows]


def _cue_terms(topic):
    normalized = sources.normalized(topic[:500])
    found = False
    for cue in RECALL_CUES:
        if cue in normalized:
            normalized = normalized.replace(cue, " ")
            found = True
    return found, sources.query_terms(normalized)


def _entity_terms(db, request, generation):
    terms = set()
    normalized_topic = sources.normalized(request.topic[:500])
    for alias in request.aliases[:20]:
        alias = sources.normalized(alias[:100].strip())
        if alias and alias in normalized_topic:
            terms.update(sources.query_terms(alias))
    count = db.execute("SELECT COUNT(*) FROM mv2_sources WHERE character_id=? AND generation=?",
                       (request.character_id, generation)).fetchone()[0]
    # Passive retrieval treats only a standalone two-character term as a
    # heuristic name. Longer questions need an explicit alias or recall cue.
    passive_bare_name = len(normalized_topic.strip(" ，。！？!?\"'「」“”")) == 2
    if count >= 20 and (request.lane != "passive" or passive_bare_name):
        role = db.execute("SELECT name FROM characters WHERE id=?", (request.character_id,)).fetchone()
        role_terms = sources.terms(role["name"]) if role else set()
        for term in sorted(sources.query_terms(request.topic[:200]))[:64]:
            if term in role_terms or term in COMMON_TERMS or len(term) < 2:
                continue
            frequency = db.execute("""SELECT COUNT(*) FROM mv2_terms t JOIN mv2_sources s
                ON s.generation=t.generation AND s.source_kind=t.source_kind AND s.source_id=t.source_id
                WHERE s.character_id=? AND s.generation=? AND t.term=?""",
                (request.character_id, generation, term)).fetchone()[0]
            if 0 < frequency <= 5:
                terms.add(term)
    return terms


class LexicalGenerator:
    name = "lexical"

    def generate(self, db, request, generation):
        has_cue, terms = _cue_terms(request.topic)
        return _term_seeds(db, request, generation, terms, self.name) if has_cue else []


class EntityGenerator:
    name = "entity"

    def generate(self, db, request, generation):
        return _term_seeds(db, request, generation, _entity_terms(db, request, generation), self.name)


class DeliberateGenerator:
    name = "deliberate"

    def generate(self, db, request, generation):
        return _term_seeds(db, request, generation, sources.query_terms(request.topic[:500]), self.name)


def _formal(db, request):
    manual, approved = memory_authority.eligible_rows(db, request.character_id)
    selected = []
    for row in manual:
        if row["scope"] != "long" and not (row["scope"] == "temporary" and row["conversation_id"] == request.conversation_id):
            continue
        if request.purpose in ("volition", "activity") and (row["scope"] != "long" or row["conversation_id"] is not None):
            continue
        ref = SourceRef("memory", str(row["id"]))
        fingerprint = sources.digest([row["character_id"], row["scope"], row["conversation_id"],
                                      row["enabled"], row["content"]])
        selected.append(RecallCandidate(request.character_id, "RAW", (ref,), (fingerprint,),
            experience_key(request.character_id, (ref,)), "manual_reference", "owner", "enabled",
            row["content"], selected_because=("existing_manual_memory",)))
    for projection, owner in approved:
        ref = SourceRef("memory", str(projection["id"]))
        fingerprint = sources.digest([projection["character_id"], projection["enabled"],
            projection["content"], owner["id"], owner["status"], owner["merged_ids_json"]])
        selected.append(RecallCandidate(request.character_id, "RAW", (ref,), (fingerprint,),
            experience_key(request.character_id, (ref,)), "approved_learning", "phase5", "accepted",
            projection["content"], selected_because=("phase5_approved_projection",)))
    return tuple(selected)


def recall(db, request: RecallRequest, *, cooldown: Mapping[str, datetime] | None = None,
           now: datetime | None = None, generators: Sequence | None = None) -> RecallDecision:
    """No writes, no model calls. Cooldown is caller-supplied and never updated here."""
    role = db.execute("SELECT enabled FROM characters WHERE id=?", (request.character_id,)).fetchone()
    if role is None or not role["enabled"]:
        return RecallDecision(request.character_id, None, status="character_unavailable")
    if request.conversation_id is not None and not db.execute(
            "SELECT 1 FROM conversations WHERE id=? AND character_id=?",
            (request.conversation_id, request.character_id)).fetchone():
        return RecallDecision(request.character_id, None, status="conversation_mismatch")
    settings = db.execute("SELECT memory_enabled FROM context_settings WHERE character_id=?",
                          (request.character_id,)).fetchone()
    if settings is not None and not settings["memory_enabled"]:
        return RecallDecision(request.character_id, None, status="memory_disabled")
    formal = _formal(db, request)
    meta = db.execute("SELECT active_generation,state,schema_version FROM mv2_meta WHERE character_id=?",
                      (request.character_id,)).fetchone()
    if meta is None or meta["state"] != "ready" or meta["schema_version"] != 2:
        return RecallDecision(request.character_id, None, formal_references=formal, status="index_unready")
    generation = meta["active_generation"]
    if request.expected_generation is not None and request.expected_generation != generation:
        return RecallDecision(request.character_id, generation, formal_references=formal, status="generation_changed")
    if generators is None:
        generators = ((DeliberateGenerator(),) if request.lane == "deliberate" else
                      (EntityGenerator(),) if request.lane == "entity" else
                      (LexicalGenerator(), EntityGenerator()))
    seeds = []
    for generator in generators:
        seeds.extend(generator.generate(db, request, generation))
    if not seeds:
        return RecallDecision(request.character_id, generation, formal_references=formal,
                              status="trigger_skipped", trigger=request.lane)
    excluded = set(request.excluded_refs)
    if request.anchor_message_id is not None:
        excluded.add(SourceRef("message", str(request.anchor_message_id)))
    rejected = []
    candidates = {}
    scan_limited = len(seeds) > MAX_SEEDS
    now = now or datetime.now(timezone.utc)
    for seed in seeds[:MAX_SEEDS]:
        if seed.entry_id is not None:
            from . import memory_v2_metadata as metadata
            from . import memory_v2_metadata_search as metadata_search
            try:
                detail=metadata.read_draft(db,seed.entry_id,request.character_id)
                fresh=metadata_search.search(db,request.character_id,request.topic,purpose='candidate',limit=50)
                hit=next((h for h in fresh if h['entryId']==seed.entry_id),None)
                expected=tuple(SourceRef(r['source_kind'],r['source_id']) for r in detail['sourceRefs'])
                if (not detail['qualifications']['candidateRecall'] or hit is None
                        or detail['versionId']!=seed.entry_version_id or expected!=seed.source_refs
                        or any(r in excluded for r in expected)):
                    raise ValueError('entry_candidate_invalid')
                live_sources=[]
                for ref in expected:
                    indexed=db.execute('SELECT * FROM mv2_sources WHERE generation=? AND character_id=? AND source_kind=? AND source_id=?',
                        (generation,request.character_id,ref.kind,ref.id)).fetchone()
                    live=memory_v2_read.valid_source(db,indexed,request.character_id)
                    if live is None or (ref.kind=='message' and live.status not in ('completed','imported')) or (
                        ref.kind=='activity_operation' and live.status in PENDING_ACTIVITY):
                        raise ValueError('source_incomplete_or_stale')
                    live_sources.append(live)
                candidate=RecallCandidate(request.character_id,'RAW',expected,
                    tuple(s.state_hash for s in live_sources),experience_key(request.character_id,expected),
                    'archive_evidence','mixed','validated', '\n'.join(s.text for s in live_sources),
                    tuple(sorted({m['term'] for m in hit['matches']})),(seed.generator,'metadata'),
                    indexed_generation=generation,source_texts=tuple(s.text for s in live_sources),
                    source_speakers=tuple(s.role for s in live_sources))
                expiry=(cooldown or {}).get(_key(request.character_id,request.conversation_id,candidate))
                if request.lane=='passive' and expiry is not None and expiry>now:
                    raise ValueError('passive_cooldown')
                candidates.setdefault(candidate.experience_key,candidate)
            except ValueError as exc:
                rejected.append((seed.entry_id,str(exc)))
            continue
        if len(seed.source_refs) != 1:
            rejected.append(("multi_source", "unsupported_in_m2"))
            continue
        ref = seed.source_refs[0]
        if ref in excluded or ref.kind not in sources.KINDS:
            rejected.append((f"{ref.kind}:{ref.id}", "excluded_or_unsupported"))
            continue
        indexed = db.execute("""SELECT * FROM mv2_sources WHERE generation=? AND character_id=?
            AND source_kind=? AND source_id=?""", (generation, request.character_id, ref.kind, ref.id)).fetchone()
        live = memory_v2_read.valid_source(db, indexed, request.character_id)
        if live is None:
            rejected.append((f"{ref.kind}:{ref.id}", "source_stale_or_unauthorized"))
            continue
        if (ref.kind == "message" and live.role == "assistant" and live.status not in ("completed", "imported")) or (
                ref.kind == "activity_operation" and live.status in PENDING_ACTIVITY):
            rejected.append((f"{ref.kind}:{ref.id}", "source_incomplete"))
            continue
        matched = tuple(sorted(set(seed.matched_terms) & sources.terms(live.text)))
        if not matched:
            rejected.append((f"{ref.kind}:{ref.id}", "term_no_longer_matches"))
            continue
        candidate = RecallCandidate(request.character_id, "RAW", (ref,), (live.state_hash,),
            experience_key(request.character_id, (ref,)), "archive_evidence", live.role, live.status,
            live.text, matched, (seed.generator, seed.match_kind), live.occurred_at, live.recorded_at, generation)
        key = _key(request.character_id, request.conversation_id, candidate)
        expiry = (cooldown or {}).get(key)
        if request.lane == "passive" and expiry is not None and expiry > now:
            rejected.append((f"{ref.kind}:{ref.id}", "passive_cooldown"))
            continue
        previous = candidates.get(candidate.experience_key)
        if previous is None or len(candidate.matched_terms) > len(previous.matched_terms):
            candidates[candidate.experience_key] = candidate
    ranked = sorted(candidates.values(), key=lambda item: (-len(item.matched_terms),
        0 if item.source_role == "user" else 1 if item.source_role == "activity" else 2,
        -_time_rank(item), item.source_refs[0].kind,
        item.source_refs[0].id))
    chosen = tuple(ranked[:request.max_candidates])
    for item in ranked[request.max_candidates:]:
        rejected.append((f"{item.source_refs[0].kind}:{item.source_refs[0].id}", "candidate_limit"))
    return RecallDecision(request.character_id, generation, chosen, formal, tuple(rejected),
                          "ok", request.lane, scan_limited,
                          tuple(_key(request.character_id, request.conversation_id, item) for item in chosen)
                          if request.lane == "passive" else ())
