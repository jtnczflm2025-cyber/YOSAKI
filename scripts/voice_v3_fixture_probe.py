"""Standalone frozen V3 benchmark probe. Default mode is read-only dry-run.

No backend.app.main import, product route, chat call, or schema initialization.
The real branch requires a separately reviewed, one-use approval receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.expression_fixture import (
    FixtureError, FixtureEvaluator, _check_result_dir, code_fingerprint, digest,
)


def local_proxy() -> str | None:
    proxies = urllib.request.getproxies()
    http, https = proxies.get("http"), proxies.get("https")
    if http and https and http != https:
        raise FixtureError("proxy_routes_disagree")
    target = https or http
    if not target:
        return None
    url = urlsplit(target if "://" in target else "http://" + target)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or
            url.username or url.password or url.path or url.query or url.fragment):
        raise FixtureError("proxy_configuration_unsupported")
    return f"http://127.0.0.1:{url.port}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen V3 Japanese A/B probe")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", default="v3-20261111-ab-r1")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--result-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        evaluator = FixtureEvaluator(args.database)
        previews = evaluator.previews()
        proxy = local_proxy()
        expected = evaluator.expected_approval(args.run_id, proxy)
        if not args.execute_real:
            print(json.dumps({"mode": "dry-run", "run_id": args.run_id,
                "manifest_sha256": digest(evaluator.spec),
                "code_fingerprint": code_fingerprint(),
                "api_key_configured": evaluator.authority()["api_key_configured"],
                "request_count": len(previews),
                "request_characters": sum(p["characters"] for p in previews),
                "real_requests": 0, "billing": "unknown",
                "previews": previews}, ensure_ascii=True, sort_keys=True))
            return 0
        if args.approval_file is None or args.result_dir is None:
            raise FixtureError("real_approval_and_result_dir_required")
        if evaluator.database != (ROOT / "data" / "yosaki.sqlite3").resolve():
            raise FixtureError("real_database_path_mismatch")
        result_root = _check_result_dir(args.result_dir, evaluator.database)
        if result_root.exists():
            raise FixtureError("result_dir_already_exists")
        receipt = args.approval_file.resolve()
        if receipt.parent != result_root.parent:
            raise FixtureError("approval_path_outside_result_parent")
        if not receipt.is_file():
            raise FixtureError("approval_file_missing")
        approval = json.loads(receipt.read_text(encoding="utf-8"))
        if approval != expected:
            raise FixtureError("approval_mismatch")
        marker = receipt.with_name(receipt.name + ".used")
        with marker.open("x", encoding="utf-8") as fp:
            fp.write(json.dumps({"run_id": args.run_id,
                                 "approval_sha256": digest(approval)}, sort_keys=True))
        evaluator.adapter_factory = lambda key: ElevenLabsAdapter(key, proxy_url=proxy)
        result = asyncio.run(evaluator.run_batch(args.run_id, approval, result_root,
                                                proxy_url=proxy))
        print(json.dumps({"mode": "real", "run_id": args.run_id,
            "status": result["status"], "result_file": str(result_root / "result.json"),
            "dispatched_http": result["dispatched_http"],
            "dispatched_characters": result["dispatched_characters"],
            "actual_billing": "unknown"}, ensure_ascii=True, sort_keys=True))
        return 0 if result["status"] == "completed" else 2
    except (FixtureError, FileExistsError, ValueError, OSError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, FixtureError) else "local_preflight_or_result_error"
        print(json.dumps({"mode": "real" if args.execute_real else "dry-run",
            "status": "stopped", "error_code": code,
            "real_requests": 0 if not args.execute_real else "see_result_file_if_present",
            "actual_billing": "unknown"}, ensure_ascii=True, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
