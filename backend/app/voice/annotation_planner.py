"""Trusted, multi-position expression annotations. Pure code; no providers or DB.

Offsets are Unicode code points in the existing SpeechPlan's safe text. Tags
are an isolated listening candidate, never an acoustic scope/reset guarantee.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re

from markdown_it import MarkdownIt

from .readable_text import public_paragraphs
from .speech_planner import MAX_SEGMENT, PLANNER_VERSION, plan, request_language

VERSION = "expression-annotation-2"
FRAGMENT_VERSION = "safe-fragments-1"
PROMPT_VERSION = "whole-reply-intents-1"
TAGS = {"inherit": "", "thoughtful": "[thoughtful] ",
        "restrained": "[thoughtful] ", "lazy": "[relaxed] ",
        "dry_humor": "[sarcastic] ", "resigned": "[sighs] ",
        "whisper": "[whispers] ", "chuckle": "[chuckles] "}
PARAMETERS = {"path": "HTTP", "model_id": "eleven_v4",
              "output_format": "mp3_44100_128", "language_code": "ja",
              "settings": "omitted"}
INSTRUCTION = ("Return only JSON schema_version=1, input_sha256, annotations. "
               "Analyze the complete public reply once. Use supplied fragment IDs "
               "and allowed intents only. Sparse or empty annotations are valid. "
               "Never rewrite text, emit tags, explanations or private reasoning.")


class AnnotationError(ValueError):
    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code, self.status = code, status


def fingerprint(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Source:
    owner_id: int
    character_id: int
    conversation_id: int
    message_id: int
    body: str
    body_version: int = 1
    profile_revision: int = 3
    voice_sha256: str = "isolated-synthetic-voice"
    consent_version: str = "isolated-consent-1"
    completed: bool = True
    display_allowed: bool = True
    authorized: bool = True
    personality: str = "中性、克制、清冷；允许留白，不强制表演。"
    public_context: tuple[str, ...] = ()
    psychology: tuple[tuple[str, float], ...] = ()
    baseline_enabled: bool = False
    baseline_applicable: bool = False


def _paragraph_origins(body: str, paragraphs: list[str]) -> list[dict]:
    """Map safe paragraphs to original Markdown blocks, not invented char offsets.

    The existing filter can decode entities/remove syntax, so an exact character
    map is not always possible. Keep original block ranges and explicitly label
    their precision; annotation offsets always refer to the exact safe text.
    """
    lines = body.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    origins = []
    for token in MarkdownIt("commonmark", {"html": True}).parse(body):
        if token.type != "inline" or token.map is None:
            continue
        lo, hi = token.map
        block = body[offsets[lo]:offsets[min(hi, len(lines))]]
        extracted = public_paragraphs(block)
        for value in extracted:
            origins.append({"text": value, "source_start": offsets[lo],
                            "source_end": offsets[min(hi, len(lines))],
                            "precision": "markdown_block"})
    # Never guess a source range when a Markdown construct differs on reparse.
    if [o["text"] for o in origins] != paragraphs:
        return [{"text": text, "source_start": 0, "source_end": len(body),
                 "precision": "whole_message"} for text in paragraphs]
    return origins


def prepare(source: Source, granularity: str = "auto", mode: str = "ai_expression", *, model_binding=None) -> dict:
    if not source.authorized:
        raise AnnotationError("permission_revoked", 403)
    if not source.completed or not source.display_allowed:
        raise AnnotationError("source_not_displayable", 409)
    if granularity not in ("auto", "paragraph", "sentence") or mode not in (
            "ai_expression", "none", "thoughtful"):
        raise AnnotationError("selection_invalid")
    try:
        segments = plan(source.body)
    except ValueError as exc:
        raise AnnotationError("source_not_readable") from exc
    if len(segments) != 1 or request_language(segments)[0] != "ja":
        raise AnnotationError("only_short_japanese_single_http")
    safe = segments[0]
    paragraphs = public_paragraphs(source.body)
    origins = _paragraph_origins(source.body, paragraphs)
    fragments = []
    safe_sha = fingerprint(safe)

    def add(kind, start, end, parent, origin):
        identity = fingerprint([safe_sha, FRAGMENT_VERSION, kind, start, end])[:24]
        item = {"fragment_id": kind[0] + "_" + identity, "kind": kind,
                "start": start, "end": end, "parent_id": parent,
                "source_range": {k: v for k, v in origin.items() if k != "text"}}
        fragments.append(item)
        return item["fragment_id"]

    cursor = 0
    for paragraph, origin in zip(paragraphs, origins):
        parent = add("paragraph", cursor, cursor + len(paragraph), None, origin)
        for match in re.finditer(r'.+?(?:[。！？!?]+[」』”）]*|$)', paragraph):
            raw = match.group()
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            if right > left:
                add("sentence", cursor + match.start() + left,
                    cursor + match.start() + right, parent, origin)
        cursor += len(paragraph) + 2
    if len(fragments) > 32:
        raise AnnotationError("fragment_limit")
    if len(source.public_context) > 2 or sum(map(len, source.public_context)) > 1000:
        raise AnnotationError("context_limit")
    if len(source.personality) > 1000:
        raise AnnotationError("personality_limit")
    context = ["\n\n".join(public_paragraphs(text)) for text in source.public_context]
    allowed_psychology = {"arousal", "tension", "valence"}
    if any(k not in allowed_psychology or type(v) not in (int, float) or
           not -1 <= v <= 1 for k, v in source.psychology):
        raise AnnotationError("psychology_snapshot_invalid")
    value = {"source": asdict(source), "safe_text": safe, "safe_text_sha256": safe_sha,
             "fragments": fragments, "version": VERSION, "fragment_version": FRAGMENT_VERSION,
             "filter_version": PLANNER_VERSION, "prompt_version": PROMPT_VERSION,
             "granularity": granularity, "mode": mode,
             "model": model_binding or {"provider": "local_fake", "model": "whole-reply-fixture-v1",
                       "max_output_tokens": 512}, "parameters": PARAMETERS}
    # Only filtered public context is sent to the planner, not the source record.
    value["model_input"] = {"instruction": INSTRUCTION, "public_reply": safe,
                            "fragments": fragments, "public_context": context,
                            "personality": source.personality,
                            "psychology": dict(source.psychology),
                            "allowed_intents": list(TAGS)}
    if len(json.dumps(value["model_input"], ensure_ascii=False)) > 6000:
        raise AnnotationError("model_input_limit")
    value["input_sha256"] = fingerprint(value)
    return value


def validate(prepared: dict, raw: object) -> list[dict]:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "input_sha256", "annotations"}:
        raise AnnotationError("model_structure_invalid")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise AnnotationError("schema_version_invalid")
    if raw["input_sha256"] != prepared["input_sha256"]:
        raise AnnotationError("model_input_mismatch")
    annotations = raw["annotations"]
    if not isinstance(annotations, list) or len(annotations) > 8:
        raise AnnotationError("annotation_limit_or_type")
    fragments = {f["fragment_id"]: f for f in prepared["fragments"]}
    found = []
    for annotation in annotations:
        if not isinstance(annotation, dict) or set(annotation) != {"fragment_id", "intent"}:
            raise AnnotationError("annotation_structure_invalid")
        identity, intent = annotation["fragment_id"], annotation["intent"]
        if not isinstance(identity, str) or identity not in fragments:
            raise AnnotationError("fragment_not_owned")
        if not isinstance(intent, str) or intent not in TAGS:
            raise AnnotationError("intent_not_allowlisted")
        fragment = fragments[identity]
        if prepared["granularity"] != "auto" and fragment["kind"] != prepared["granularity"]:
            raise AnnotationError("granularity_mismatch")
        found.append({**annotation, "start": fragment["start"], "end": fragment["end"]})
    found.sort(key=lambda item: (item["start"], item["end"]))
    if any(a["end"] > b["start"] for a, b in zip(found, found[1:])):
        raise AnnotationError("annotation_duplicate_or_overlap")
    return [{k: a[k] for k in ("fragment_id", "intent")} for a in found]


def render(prepared: dict, annotations: list[dict], revision: int) -> dict:
    if prepared["mode"] != "ai_expression" and annotations:
        raise AnnotationError("manual_mode_cannot_add_ai_annotations")
    annotations = validate(prepared, {"schema_version": 1,
                           "input_sha256": prepared["input_sha256"], "annotations": annotations})
    source = prepared["source"]
    baseline = "[thoughtful] " if (prepared["mode"] != "none" and
                source["baseline_enabled"] and source["baseline_applicable"]) else ""
    if prepared["mode"] == "thoughtful":
        baseline = "[thoughtful] "
    fragments = {f["fragment_id"]: f for f in prepared["fragments"]}
    insertions = [{"offset": fragments[a["fragment_id"]]["start"], "tag": TAGS[a["intent"]],
                   "fragment_id": a["fragment_id"], "intent": a["intent"]}
                  for a in annotations if TAGS[a["intent"]]]
    # No invented reset tags. A baseline applies only at offset zero.
    if baseline and not any(i["offset"] == 0 for i in insertions):
        insertions.insert(0, {"offset": 0, "tag": baseline, "intent": "baseline",
                              "fragment_id": None})
    text, cursor, parts = prepared["safe_text"], 0, []
    for item in insertions:
        parts.extend([text[cursor:item["offset"]], item["tag"]])
        cursor = item["offset"]
    parts.append(text[cursor:])
    request_text = "".join(parts)
    if len(request_text) > MAX_SEGMENT:
        raise AnnotationError("tagged_request_limit")
    value = {"safe_text": text, "request_text": request_text,
             "request_characters": len(request_text), "segment_count": 1,
             "http_requests": 1, "insertions": insertions,
             "annotations": annotations, "parameters": dict(PARAMETERS),
             "input_sha256": prepared["input_sha256"], "plan_revision": revision,
             "version": VERSION, "requested_mode": prepared["mode"],
             "baseline_tag": baseline or None, "acoustic_scope": "awaiting_listening",
             "daily_enabled": False}
    value["preview_sha256"] = fingerprint(value)
    return value
