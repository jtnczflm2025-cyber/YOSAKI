"""Run the M5-A0 read-only calendar against disposable synthetic SQLite.

Usage: .venv/Scripts/python.exe scripts/chronicle_a0_demo.py --port 8765
The process owns its temporary data directory and removes it on exit.
"""

import argparse
import os
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
import uvicorn


def _approve_derivation(db, m3, version, character_id):
    m3.review_version(db, version["id"], character_id, 1, version["output_hash"], version["input_hash"])
    m3.activate_version(db, version["id"], character_id)


def seed(connect, main, schema, indexer, m3, entries, p2, jobs):
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    with connect() as db:
        schema.init_db(db)
        p2.init_db(db)
        jobs.init_db(db)
        db.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                   ("chronicle-demo", main.password_hash("A0SyntheticOnly_2026!"), stamp.isoformat()))
        character_id = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]
        conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
            VALUES(?,'合成演示对话',?,?)""", (character_id, stamp.isoformat(), stamp.isoformat())).lastrowid
        cases = [
            (0, "雨夜的约定", "她说今晚若下雨就改走近路。我说：うん、無理はしない。\n她又说，不是明天，是今天。\n我说：那就今天，先确认天气。",
             ["雨夜", "约定", "日语"], ["对话", "条件"], "她提出下雨时改走近路，并纠正了日期；我同意先确认天气。"),
            (7, "去图书馆的路", "她说先去图书馆，再去车站。我说：顺序记住了。", ["图书馆"], ["出行"], "她安排了图书馆与车站的先后顺序。"),
            (8, "车站前的咖啡", "她说车站前先买咖啡。我说：好，之后再进站。", ["咖啡", "车站"], ["出行"], "她提出先买咖啡，再进入车站。"),
            (14, "散步时的树影", "她说树影很长。我说：那我们走慢一点。", ["散步"], ["日常"], "她提到路上的树影，我们放慢脚步。"),
            (21, "未审核的清理草稿", "她说先别定结论。我说：等你确认。", ["等待"], ["对话"], "双方决定等待进一步确认。"),
            (30, "秋末的地图", "她说地图上还有一条小路。我说：先核对路况。", ["地图"], ["计划"], "她指出小路，我建议先核对路况。"),
            (90, "夏日书单", "她列出三本想读的书。我说：先借第一本。", ["书单"], ["阅读"], "她列出书单，我们约定先借第一本。"),
            (180, "春天的车票", "她说车票还没买。我说：先看时刻表。", ["车票"], ["出行"], "车票尚未购买，我们先查时刻。"),
            (365, "去年的旧信", "她说这封信要留着。我说：会按原样保存。", ["旧信"], ["保存"], "她提出保留旧信，我答应按原样保存。"),
        ]
        source_rows = []
        for age, title, narrative, keywords, tags, synopsis in cases:
            at = (stamp - timedelta(days=age, hours=1)).isoformat()
            user_text = f"{title}：" + ("今晚若下雨就改走近路。不是明天，是今天。" if age == 0 else synopsis)
            if age == 0:
                user_text += "\n" + "这是合成来源的分页段落。" * 450
            user_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user',?,?)""", (conv, user_text, at)).lastrowid
            assistant_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'assistant',?,?)""", (conv, "うん、無理はしない。" if age == 0 else "我说：先核对原始记录。", at)).lastrowid
            source_rows.append((age, title, narrative, keywords, tags, synopsis,
                                [{"kind": "message", "id": str(user_id)},
                                 {"kind": "message", "id": str(assistant_id)}]))
        imported_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at,generation_status)
            VALUES(?,'user','发生时间未核实的合成导入。',?,'imported')""",
            (conv, stamp.isoformat())).lastrowid
        stale_id = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
            VALUES(?,'user','将变更的合成来源。',?)""", (conv, stamp.isoformat())).lastrowid
        job = indexer.start_job(db, character_id, "rebuild")
        for _ in range(30):
            if indexer.step_job(db, job["job_id"])["status"] == "completed":
                break
        else:
            raise RuntimeError("synthetic index did not finish")

        def make_entry(title, narrative, keywords, tags, synopsis, refs):
            derivation = m3.create_derivation(db, character_id, "NARRATIVE", refs)
            version = m3.add_version(db, derivation["id"], refs, narrative)
            _approve_derivation(db, m3, version, character_id)
            item = entries.create_entry(db, character_id)
            meta = entries.add_version(db, item["id"], refs, title, keywords, tags,
                                       synopsis, version["id"], 1)
            entries.review_version(db, meta["id"], character_id, 1,
                                   meta["metadata_hash"], meta["source_hash"])
            entries.activate_version(db, meta["id"], character_id)
            return item["id"]

        ids = {}
        for age, title, narrative, keywords, tags, synopsis, refs in source_rows:
            ids[age] = make_entry(title, narrative, keywords, tags, synopsis, refs)
            if age == 0:
                clean = m3.create_derivation(db, character_id, "CLEANED", refs)
                version = m3.add_version(db, clean["id"], refs,
                    "今晚若下雨就改走近路。不是明天，是今天。\nうん、無理はしない。",
                    author_kind="user")
                _approve_derivation(db, m3, version, character_id)
            if age == 21:
                clean = m3.create_derivation(db, character_id, "CLEANED", refs)
                m3.add_version(db, clean["id"], refs, "尚未审核的合成清理草稿。")
        make_entry("时间未核实的导入", "她说：发生时间未核实。", ["导入"], ["待核时"],
                   "这条导入没有可核实的发生时间。", [{"kind": "message", "id": str(imported_id)}])
        make_entry("待失效来源", "她说：来源随后发生变化。", ["失效"], ["测试"],
                   "这条经历用于展示来源失效过滤。", [{"kind": "message", "id": str(stale_id)}])

        def approve_node(node):
            for child in node.children:
                approve_node(child)
            if node.level in ("L1", "L2", "L3", "L4") and ids.get(14) not in [m.entry_id for m in node.members] and ids.get(21) not in [m.entry_id for m in node.members]:
                version = p2.create_extractive_digest(db, character_id, node.id, now=stamp)
                _approve_derivation(db, m3, version, character_id)

        for node in p2._tree(db, character_id, stamp, 480):
            approve_node(node)
        draft_node = next(n for n in p2._tree(db, character_id, stamp, 480)
                          if ids[21] in [m.entry_id for m in n.members])
        p2.create_extractive_digest(db, character_id, draft_node.id, now=stamp)
        missing_node = next(n for n in p2._tree(db, character_id, stamp, 480)
                            if ids[14] in [m.entry_id for m in n.members])
        missing_refs = p2._source_refs(missing_node.members)
        task = jobs.enqueue(db, character_id, "DIGEST", missing_refs,
                            level=missing_node.level, period=missing_node.period)
        db.execute("UPDATE mv3_jobs SET status='failed' WHERE id=?", (task["id"],))
        db.execute("UPDATE messages SET content='来源已经修订，旧正文不得再展示。' WHERE id=?", (stale_id,))
        return {"characterId": character_id, "entryIds": ids}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--frontend-origin", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("invalid port")
    if not args.frontend_origin.startswith("http://127.0.0.1:"):
        raise SystemExit("frontend origin must be loopback")
    with tempfile.TemporaryDirectory(prefix="yosaki-chronicle-a0-") as temporary:
        root = Path(temporary).resolve()
        project_data = (Path(__file__).resolve().parents[1] / "data").resolve()
        if root == project_data or project_data in root.parents or root in project_data.parents:
            raise RuntimeError("demo data directory is not isolated")
        os.environ["YOSAKI_DATA_DIR"] = str(root)
        os.environ["YOSAKI_CHATGPT_PLAN_ENABLED"] = "0"
        for name in ("YOSAKI_SECRET_KEY", "YOSAKI_BACKUP_PASSWORD", "YOSAKI_BACKUP_GITHUB_TOKEN",
                     "YOSAKI_M36_TEST_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        if "backend.app.main" in sys.modules:
            raise RuntimeError("backend imported before isolated data directory")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from backend.app import main as backend
        from backend.app import memory_v2_derivation_schema as schema
        from backend.app import memory_v2_index as indexer
        from backend.app import memory_v2_derivation as m3
        from backend.app import memory_v2_entries as entries
        from backend.app import memory_v2_time_projection as p2
        from backend.app import memory_v2_jobs as jobs
        from backend.app import memory_v2_time_api as time_api
        from backend.app import memory_v2_calendar_api as calendar_api
        from backend.app import memory_v2_import_schema as import_schema
        from backend.app import memory_v2_import_api as import_api
        from backend.app import memory_v2_entry_api as entry_api
        from backend.app import memory_v2_metadata as metadata
        from backend.app import memory_v2_metadata_jobs as metadata_jobs
        from backend.app import memory_v2_metadata_search as metadata_search
        from backend.app import memory_v2_metadata_api as metadata_api
        seed(backend.connect, backend, schema, indexer, m3, entries, p2, jobs)
        with backend.connect() as db:
            import_schema.init_db(db)
            metadata.init_db(db)
            metadata_jobs.init_db(db)
            metadata_search.init_db(db)
            from backend.app import memory_v2_import as importer
            importer.pause_unfinished_on_start(db)
            importer.purge_stale_previews(db)
        app = FastAPI(title="Chronicle A0 synthetic read-only demo")
        app.add_api_route("/api/auth/status", backend.auth_status, methods=["GET"])
        app.add_api_route("/api/auth/login", backend.auth_login, methods=["POST"])
        time_api.install(app, backend.connect, backend.current_session)
        calendar_api.install(app, backend.connect, backend.current_session)
        import_api.install(app, backend.connect, backend.current_session,
                           allowed_origins=(args.frontend_origin,))
        entry_api.install(app, backend.connect, backend.current_session,
                          allowed_origins=(args.frontend_origin,))
        metadata_api.install(app, backend.connect, backend.current_session,
                             allowed_origins=(args.frontend_origin,))
        print(f"Synthetic Chronicle A0 API: http://127.0.0.1:{args.port}")
        print("Demo login: chronicle-demo / A0SyntheticOnly_2026! (temporary synthetic data)")
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
