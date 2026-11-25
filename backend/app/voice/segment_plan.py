"""Trusted long HTTP preview, reusing the existing plain segment boundaries.

Tags are inserted AFTER public extraction, never re-parsed as chat markup.
No automatic continuation/reset tag is invented across request boundaries.
"""
from __future__ import annotations

import hashlib
import re

from .annotation_planner import TAGS, fingerprint
from .readable_text import public_paragraphs
from .speech_planner import (MAX_SEGMENT, MAX_SEGMENTS, MAX_TOTAL, PLANNER_VERSION,
                             _preferred_cut, plan, request_language)

VERSION = "long-http-plan-1"


def build(body: str, annotations=(), *, candidate_tags=False, short_policy=None):
    safe = "\n\n".join(public_paragraphs(body))
    plain = plan(body)
    if short_policy is not None:
        if annotations:
            raise ValueError('short_policy_tags_not_validated')
        from .short_segment_policy import split
        plain = split(safe,short_policy)
    legal = set()
    cursor = 0
    for paragraph in public_paragraphs(body):
        legal.add(cursor)
        for match in re.finditer(r'.+?(?:[。！？!?]+[」』”）]*|$)', paragraph):
            legal.add(cursor + match.start() + len(match.group()) - len(match.group().lstrip()))
        cursor += len(paragraph) + 2
    if annotations and not candidate_tags:
        raise ValueError("long_candidate_tags_disabled")
    if len(annotations) > 8:
        raise ValueError("long_annotation_limit")
    insertions = []
    used = set()
    for a in annotations:
        if (set(a) != {"offset", "intent"} or type(a["offset"]) is not int or
                a["offset"] not in legal or a["offset"] in used or a["intent"] not in TAGS):
            raise ValueError("long_annotation_not_trusted")
        used.add(a["offset"])
        if TAGS[a["intent"]]:
            insertions.append({**a, "tag": TAGS[a["intent"]]})
    insertions.sort(key=lambda a: a["offset"])

    def rendered(lo, hi):
        parts, at = [], lo
        selected = [a for a in insertions if lo <= a["offset"] < hi]
        for a in selected:
            parts.extend((safe[at:a["offset"]], a["tag"]))
            at = a["offset"]
        return "".join(parts) + safe[at:hi], selected

    segments = []
    cursor = 0
    for base in plain:
        lo = safe.find(base, cursor)
        if lo < cursor or safe[cursor:lo].strip():
            raise ValueError("long_source_mapping_failed")
        hi = lo + len(base)
        while lo < hi:
            text, tags = rendered(lo, hi)
            end = hi
            if len(text) > MAX_SEGMENT:
                # Account for actual tag bytes as Unicode characters before
                # selecting an existing punctuation/safe Unicode boundary.
                available = MAX_SEGMENT - sum(len(a["tag"]) for a in tags)
                if available < 1:
                    raise ValueError("long_tag_capacity")
                end = lo + _preferred_cut(safe[lo:hi], available)
                while end > lo and safe[end - 1].isspace():
                    end -= 1
                text, tags = rendered(lo, end)
            if not text or len(text) > MAX_SEGMENT:
                raise ValueError("long_tagged_request_limit")
            segments.append({"sequence": len(segments), "source_start": lo, "source_end": end,
                "safe_text": safe[lo:end], "text": text, "characters": len(text),
                "insertions": tags, "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
            lo = end
            while lo < hi and safe[lo].isspace():
                lo += 1
        cursor = hi
    maximum_parts=short_policy.max_segments if short_policy is not None else MAX_SEGMENTS
    if safe[cursor:].strip() or len(segments) > maximum_parts or sum(s["characters"] for s in segments) > MAX_TOTAL:
        raise ValueError("long_total_or_mapping_limit")
    # Full exact ranges + explicit whitespace gaps make reconstruction testable.
    gaps, cursor = [], 0
    for s in segments:
        gap = safe[cursor:s["source_start"]]
        if gap.strip():
            raise ValueError("long_nonwhitespace_omission")
        gaps.append(gap)
        cursor = s["source_end"]
    gaps.append(safe[cursor:])
    rebuilt = "".join(gaps[i] + s["safe_text"] for i, s in enumerate(segments)) + gaps[-1]
    if rebuilt != safe:
        raise ValueError("long_reconstruction_failed")
    value = {"version": VERSION, "plain_planner_version": PLANNER_VERSION,
        "source_text": body, "safe_text": safe, "safe_sha256": fingerprint(safe),
        "segments": segments, "gaps": gaps, "characters": sum(s["characters"] for s in segments),
        "requests": len(segments), "language_code": request_language([s["safe_text"] for s in segments])[0],
        "parameters": {"model_id": "eleven_v4", "output_format": "mp3_44100_128", "settings": "omitted"},
        "tag_continuation": "not_reapplied_or_reset_acoustic_scope_unverified" if insertions else "none"}
    if short_policy is not None:
        value['segmentation_policy']=short_policy.describe()
        value['version']=VERSION+'+'+value['segmentation_policy']['version']
    value["preview_sha256"] = fingerprint(value)
    return value
