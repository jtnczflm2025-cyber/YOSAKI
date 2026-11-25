"""Versioned advisory delivery preferences, separate from character identity.

No settings/database/network access. Opt-in injection only; None is byte-for-
byte compatible with existing prepared plans. Never installs a baseline tag.
"""
from copy import deepcopy

from .annotation_planner import AnnotationError, fingerprint

VERSION = "aya-performance-preferences-1"
CANDIDATE = {
    "version": VERSION,
    "delivery_target": "慵懒、低沉、克制，亲近但不过分雀跃，允许自然停顿和留白。",
    "semantic_policy": "依据整段语义，对需要表演的位置稀疏标注；不同句子可以选择不同意图。",
    "allow_empty_annotations": True,
    "force_baseline": False,
}
GUIDANCE = (" Delivery preferences are advisory voice performance goals, not character "
            "identity or pitch controls. Interpret the complete reply and each fragment's "
            "meaning; different fragments may need different intents. Leave ordinary "
            "fragments unannotated. Empty annotations are valid. Do not add a blanket "
            "thoughtful baseline, force one tag per sentence, invent reset tags, or "
            "rewrite the public reply. Preferences never expand allowed_intents.")


def apply(prepared: dict, preferences: dict | None) -> dict:
    if preferences is None:
        return prepared
    if (not isinstance(preferences, dict) or set(preferences) != set(CANDIDATE)
            or preferences.get("version") != VERSION
            or preferences.get("allow_empty_annotations") is not True
            or preferences.get("force_baseline") is not False
            or any(not isinstance(preferences.get(key), str) or not 1 <= len(preferences[key]) <= 300
                   for key in ("delivery_target", "semantic_policy"))):
        raise AnnotationError("performance_preferences_invalid")
    value = deepcopy(prepared)
    value.pop("input_sha256")
    value["performance_preferences"] = deepcopy(preferences)
    value["model_input"]["performance_preferences"] = deepcopy(preferences)
    value["model_input"]["instruction"] += GUIDANCE
    value["input_sha256"] = fingerprint(value)
    return value
