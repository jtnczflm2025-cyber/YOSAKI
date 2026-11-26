"""Materialize one real candidate receipt only after explicit user approval.

This offline command is never called by the preview/server. It requires the
approved exact manifest and code hashes as arguments, creates one new file,
and neither imports main.py nor reads credentials or contacts providers.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backend.app.voice import realtime_dynamic_candidate_receipt as receipts
from backend.app.voice.realtime_dynamic_context import digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument("--approved-code-sha256", required=True)
    args = parser.parse_args()
    if not args.receipt_dir.is_dir() or args.receipt_dir.is_symlink() or \
            args.receipt_dir.resolve().is_relative_to(receipts.ROOT.resolve()):
        raise SystemExit("receipt_directory_missing_or_unsafe")
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest = value["manifest"]
    receipts.validate_manifest(manifest)
    manifest_hash = digest(manifest)
    code_hash = receipts.code_digest()
    if manifest_hash != args.approved_manifest_sha256 or \
            code_hash != args.approved_code_sha256 or \
            value.get("manifest_sha256") != manifest_hash or \
            value.get("code_sha256") != code_hash:
        raise SystemExit("approved_fingerprints_mismatch")
    target = args.receipt_dir / f"{manifest['run_id']}.approval.json"
    record = {"manifest": manifest, "manifest_sha256": manifest_hash,
              "code_sha256": code_hash, "provider_kind": "real_approved"}
    try:
        with target.open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise SystemExit("receipt_already_exists") from exc
    print(json.dumps({"run_id": manifest["run_id"],
        "manifest_sha256": manifest_hash, "code_sha256": code_hash,
        "receipt_created": True, "provider_requests_dispatched": 0}))


if __name__ == "__main__":
    main()
