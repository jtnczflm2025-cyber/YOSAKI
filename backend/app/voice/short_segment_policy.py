"""Opt-in short serial policy. Public filtering/total cap remain shared.

Sentence boundaries first; exceptional short groups preserve complete sentences.
An overlong sentence falls back to an existing phrase/Unicode-safe cut. This is
an engineering strategy, not a diagnosis of upstream long-request timeouts.
"""
from dataclasses import asdict,dataclass
from .speech_planner import _preferred_cut,_safe_cut,_SENTENCE_END,_CLOSERS,SpeechPlanError,MAX_TOTAL

VERSION='short-sentence-1'
CLOSERS=_CLOSERS|frozenset('」’\')】］〉》')


@dataclass(frozen=True)
class ShortSegmentPolicy:
    target: int=200
    minimum: int=150
    maximum: int=250
    max_segments: int=64

    def __post_init__(self):
        if any(type(v) is not int for v in asdict(self).values()) or not \
                50<=self.minimum<=self.target<=self.maximum<=250 or not 1<=self.max_segments<=64:
            raise SpeechPlanError('short_policy_out_of_range')

    def describe(self):return {'version':VERSION,**asdict(self),
        'fallback':'complete_short_sentence_then_phrase_then_unicode_safe_target',
        'total_character_limit':MAX_TOTAL}


def split(safe_text,policy):
    if not isinstance(policy,ShortSegmentPolicy):raise SpeechPlanError('short_policy_type')
    segments=[];text=safe_text
    while text:
        if len(text)<=policy.maximum:
            cut=len(text)
        else:
            ends=[];i=0
            while i<min(len(text),policy.maximum):
                if text[i] in _SENTENCE_END:
                    end=i+1
                    while end<len(text) and (text[end] in _SENTENCE_END or text[end] in CLOSERS):end+=1
                    if end<=policy.maximum:ends.append(end)
                    i=end
                else:i+=1
            normal=[e for e in ends if e>=policy.minimum]
            if normal:cut=min(normal,key=lambda e:(abs(e-policy.target),e))
            elif ends:cut=max(ends)  # preserve the short sentence before an overlong one
            else:
                cut=_preferred_cut(text,policy.target)
                if cut<policy.minimum:cut=_safe_cut(text,policy.target)
        value=text[:cut].rstrip()
        if not value:raise SpeechPlanError('short_empty_segment')
        segments.append(value)
        text=text[cut:].lstrip()
        if len(segments)>policy.max_segments:raise SpeechPlanError('short_request_count_limit')
    return segments
