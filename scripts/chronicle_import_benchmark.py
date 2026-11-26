"""Synthetic-only import benchmark. Creates and removes an isolated SQLite DB."""

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=5000)
    args = parser.parse_args()
    if not 100 <= args.messages <= 20000:
        raise SystemExit("synthetic message count must be 100..20000")
    with tempfile.TemporaryDirectory(prefix="yosaki-chronicle-benchmark-") as directory:
        root = Path(directory).resolve()
        project = Path(__file__).resolve().parents[1]
        if root == project or project in root.parents or root in project.parents:
            raise RuntimeError("benchmark isolation failed")
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        os.environ["YOSAKI_CHATGPT_PLAN_ENABLED"] = "0"
        for key in ("OPENAI_API_KEY", "YOSAKI_M36_TEST_API_KEY", "YOSAKI_SECRET_KEY",
                    "YOSAKI_BACKUP_PASSWORD", "YOSAKI_BACKUP_GITHUB_TOKEN"):
            os.environ.pop(key, None)
        if "backend.app.main" in sys.modules:
            raise RuntimeError("backend imported before temporary data directory")
        sys.path.insert(0, str(project))
        from backend.app import main as backend
        from backend.app import memory_v2_import as importer
        from backend.app import memory_v2_import_schema as schema
        from backend.app import memory_v2_index as indexer

        with backend.connect() as db:
            schema.init_db(db)
            character = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]
        base = datetime(2021, 1, 1, tzinfo=timezone.utc)
        messages = [{"id": f"synthetic-{i}", "role": "user" if i % 2 else "assistant",
                     "content": f"合成历史 美咲 阿夜 第{i}轮。" + ("旧对话正文。" * 18),
                     "created_at": (base + timedelta(minutes=i)).isoformat()} for i in range(args.messages)]
        payload = json.dumps({"format": "yosaki-conversation", "version": 2,
                              "conversation": {"id": "synthetic-benchmark"}, "messages": messages},
                             ensure_ascii=False).encode()
        if len(payload) > 8 * 1024 * 1024:
            raise RuntimeError("synthetic file exceeds importer limit")
        tracemalloc.start()
        started = time.perf_counter()
        with backend.connect() as db:
            batch = importer.prepare(db, character, [("benchmark.json", payload)])["batchId"]
            importer.confirm(db, batch, character)
        prepared_seconds = time.perf_counter() - started
        raw_started = time.perf_counter()
        paused_once = False
        while True:
            with backend.connect() as db:
                state = importer.step(db, batch, character)
                if not paused_once and state["processed"] >= args.messages // 2 and state["status"] != "raw_complete":
                    importer.set_paused(db, batch, character, True)
                    paused_once = True
            if paused_once:
                with backend.connect() as db:
                    if importer.status(db, batch, character)["status"] == "paused":
                        importer.set_paused(db, batch, character, False)
            if state["status"] == "raw_complete":
                break
        raw_seconds = time.perf_counter() - raw_started
        with backend.connect() as db:
            job = indexer.start_job(db, character, "rebuild")
        index_started = time.perf_counter()
        for _ in range(args.messages // 100 + 100):
            with backend.connect() as db:
                result = indexer.step_job(db, job["job_id"])
            if result["status"] == "completed":
                break
        else:
            raise RuntimeError("synthetic M1 indexing failed to finish")
        index_seconds = time.perf_counter() - index_started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with backend.connect() as db:
            raw_count = db.execute("SELECT COUNT(*) FROM mv2_import_receipts").fetchone()[0]
            indexed_count = db.execute("SELECT COUNT(*) FROM mv2_sources WHERE source_kind='message'").fetchone()[0]
            events = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("learning_evidence", "feedback_activities_v4", "psychological_events")
                      if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()}
        print(json.dumps({"syntheticMessages": args.messages, "fileBytes": len(payload),
                          "prepareSeconds": round(prepared_seconds, 3), "rawSeconds": round(raw_seconds, 3),
                          "indexSeconds": round(index_seconds, 3), "peakPythonMiB": round(peak / 1048576, 2),
                          "sqliteBytes": backend.DB.stat().st_size, "rawReceipts": raw_count,
                          "indexedSources": indexed_count, "businessEventCounts": events,
                          "pausedAndResumed": paused_once}, ensure_ascii=False))


if __name__ == "__main__":
    main()
