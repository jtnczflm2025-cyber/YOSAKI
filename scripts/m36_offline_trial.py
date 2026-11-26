"""Inspect the synthetic M3.6 manifest only. This script has no network or key path."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("m36_gold", ROOT / "tests" / "m36_gold.py")
gold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gold)


def main():
    gold.validate_gold()
    requests = 0
    rows = []
    for case in gold.GOLD:
        count, current = 0, 0
        for _, content in case["turns"]:
            if current and current + len(content) > 6000:
                count += 1
                current = 0
            current += len(content)
        if current:
            count += 1
        requests += count * 2
        rows.append({"case": case["id"], "kind": case["kind"], "batchesPerPrompt": count})
    if requests != 18:
        raise RuntimeError("synthetic_manifest_request_count_changed")
    print(json.dumps({"syntheticOnly": True, "generationRequestsProposed": requests,
                      "providerCallsMade": 0, "cases": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
