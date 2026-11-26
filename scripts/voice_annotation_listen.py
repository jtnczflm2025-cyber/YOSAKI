"""Default dry-run; separately approved two-HTTP listening batch or playback only.

Never creates an approval. No ChatGPT/OAuth/STT/WS calls in any branch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from backend.app.voice.annotation_listening import VoiceAuthority, review_app, run_listening, verify_manifest
from backend.app.voice.annotation_planner import fingerprint
from scripts.voice_v3_fixture_probe import local_proxy

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "docs/VOICE_EXPRESSION_LISTENING_CANDIDATE.json")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--review-only", action="store_true")
    parser.add_argument("--database", type=Path, default=ROOT / "data/yosaki.sqlite3")
    parser.add_argument("--session-hash-file", type=Path)
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--result-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.execute_real and args.review_only:
            raise ValueError("mutually_exclusive_modes")
        if args.review_only:
            if args.result_dir is None:
                raise ValueError("result_directory_required")
            import uvicorn
            uvicorn.run(review_app(args.result_dir), host="127.0.0.1", port=18890,
                        workers=1, access_log=False)
            return 0
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        verify_manifest(manifest)
        if not args.execute_real:
            print(json.dumps({"mode": "dry_run", "run_id": manifest["run_id"],
                "manifest_sha256": fingerprint(manifest), "code_sha256": manifest["code_sha256"],
                "limits": manifest["budget"], "real_requests": 0,
                "credentials_read": False, "source_database_read": False}))
            return 0
        if args.approval_file is None or args.result_dir is None or args.session_hash_file is None:
            raise ValueError("approval_session_and_result_required")
        if not args.session_hash_file.is_file() or args.session_hash_file.is_symlink() or \
                args.session_hash_file.stat().st_size > 256:
            raise ValueError("session_binding_file_invalid")
        session = args.session_hash_file.read_text(encoding="utf-8").strip()
        if len(session) != 64 or any(c not in "0123456789abcdef" for c in session):
            raise ValueError("session_binding_file_invalid")
        authority = VoiceAuthority(args.database, session, manifest["voice_binding"])
        result = asyncio.run(run_listening(manifest, authority, args.approval_file, args.result_dir,
            lambda: os.environ.get("ELEVENLABS_API_KEY", "").strip(),
            enabled=True, proxy_url=local_proxy()))
        print(json.dumps({key: result[key] for key in ("status", "dispatched_http",
            "dispatched_characters", "planning_model_requests", "actual_billing")}))
        return 0 if result["status"] == "completed" else 2
    except Exception:
        # Specific safe failure remains in result.json; never print arbitrary
        # credential/HTTP exception text or incorrectly report zero after claim.
        print(json.dumps({"status": "stopped", "error": "local_binding_or_gate_failure",
                          "requests": "see_occupied_marker_and_result_if_present" if args.execute_real else 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
