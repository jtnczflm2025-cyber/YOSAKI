"""Freeze the controlled listening candidate. This builder never dispatches.

The two fixed variants measure rendering/acoustics, not a real AI judgment.
No real-execute flag, credential loading, receipt creation or daily DB access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.app.voice.annotation_planner import Source, fingerprint, prepare, render
from backend.app.voice.annotation_lab import FIXTURES

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "backend/app/voice/annotation_planner.py", "backend/app/voice/annotation_store.py",
    "backend/app/voice/annotation_lab.py", "backend/app/voice/readable_text.py",
    "backend/app/voice/speech_planner.py", "frontend/src/voice/ExpressionLab.vue",
    "frontend/src/voice/expressionLabMain.ts", "frontend/voice-expression-lab.html",
    "frontend/vite.expression-lab.config.ts", "scripts/voice_annotation_lab.py",
    "scripts/voice_annotation_candidate.py", "tests/test_voice_annotation_lab.py",
    "frontend/src/ui/GlassSurface.vue", "frontend/src/ui/glassRefraction.ts",
    "frontend/src/ui/ThemeToggle.vue", "frontend/src/ui/theme.ts",
    "frontend/src/ui/tokens.css", "frontend/src/ui/silver.css",
    "backend/app/voice/elevenlabs_adapter.py", "backend/requirements.txt", "package-lock.json",
    "backend/app/chatgpt_plan.py", "backend/app/responses_adapter.py",
    "frontend/voice-expression-listen.html", "scripts/voice_annotation_listen.py",
    "scripts/voice_v3_fixture_probe.py", "tests/test_voice_annotation_wiring.py",
]
FILES = sorted(set(FILES) | {p.relative_to(ROOT).as_posix()
                            for p in (ROOT / "backend/app/voice").glob("*.py")})


def build() -> dict:
    source = Source(1, 1, 1, 103, FIXTURES[103][1])
    plain = prepare(source, "paragraph", "none")
    annotated = prepare(source, "paragraph")
    paragraphs = [f for f in annotated["fragments"] if f["kind"] == "paragraph"]
    annotations = [{"fragment_id": p["fragment_id"], "intent": intent}
                   for p, intent in zip(paragraphs, ("thoughtful", "resigned"))]
    previews = [render(plain, [], 1), render(annotated, annotations, 1)]
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
    return {
        "run_id": "voice-expression-multiposition-20261118-r2",
        "status": "awaiting_single_real_approval", "source_kind": "explicit_frozen_synthetic_fixture",
        "sample_id": "jp-two-position-annotation-1",
        "source_text": source.body, "planning_model_requests": 0,
        "annotation_origin": "frozen_candidate_not_real_ai_output",
        "budget": {"http_tts": 2, "request_characters": sum(p["request_characters"] for p in previews),
                   "tts_ws": 0, "scribe_ws": 0, "oauth": 0, "model_directory_get": 0,
                   "per_http_total_seconds": 30, "per_audio_max_bytes": 16 * 1024 * 1024,
                   "retries": 0, "serial": True},
        "stop_rules": ["binding_drift", "failure", "timeout", "unknown_result", "budget_exhausted"],
        "voice_binding": {"expected_profile_revision": 3,
            "expected_voice_sha256": "e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767",
            "expected_consent_version": "2026-11-08-v1", "read_only_recheck_required": True,
            "verified_in_this_batch": False},
        "requests": [{"variant": label, "text": p["request_text"],
                      "characters": p["request_characters"], "parameters": p["parameters"],
                      "annotations": p["annotations"], "insertions": p["insertions"],
                      "text_sha256": hashlib.sha256(p["request_text"].encode("utf-8")).hexdigest()}
                     for label, p in zip(("plain", "two_position_candidate"), previews)],
        "audio_storage": "separate_evaluation_directory_not_v2_bindings",
        "code_files": files, "code_sha256": fingerprint(files),
        "limitations": ["planning_model_not_part_of_listening_batch", "no_real_receipt_created",
            "controlled_gate_default_closed", "acoustic_scope_not_verified",
            "no_invented_reset_tag", "single_generation_per_variant_not_stability_proof",
            "actual_billing_unknown"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = build()
    sha = fingerprint(manifest)
    if args.output:
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest_sha256": sha, "code_sha256": manifest["code_sha256"],
                      "http_tts": 2, "characters": manifest["budget"]["request_characters"],
                      "real_requests": 0, "approved": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
