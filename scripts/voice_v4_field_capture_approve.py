"""Offline receipt creation, only after a separate exact-hash user approval."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts import voice_v4_field_capture_receipt as receipts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument("--approved-code-sha256", required=True)
    args = parser.parse_args()
    if not args.receipt_dir.is_dir() or args.receipt_dir.is_symlink() or \
            args.receipt_dir.resolve().is_relative_to(receipts.ROOT.resolve()):
        raise SystemExit("receipt_directory_unsafe")
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest = value.get("manifest")
    receipts.validate(manifest)
    manifest_hash = receipts.digest(manifest)
    code_hash = receipts.code_digest()
    if value.get("manifest_sha256") != manifest_hash or \
            value.get("code_sha256") != code_hash or \
            manifest_hash != args.approved_manifest_sha256 or \
            code_hash != args.approved_code_sha256:
        raise SystemExit("approved_fingerprints_mismatch")
    path = args.receipt_dir / f"{manifest['run_id']}.approval.json"
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"manifest": manifest, "manifest_sha256": manifest_hash,
                       "code_sha256": code_hash, "provider_kind": "real_approved"},
                      output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise SystemExit("receipt_already_exists") from exc
    print(json.dumps({"run_id": manifest["run_id"], "receipt_created": True,
                      "provider_requests_dispatched": 0}))


if __name__ == "__main__":
    main()
