"""Create a sanitized, ignored local observation database from the live SQLite file.

Run only while the normal YOSAKI backend is stopped. This never modifies source.
The owner password hash remains so the same owner can log into the isolated copy.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "yosaki.sqlite3"
TARGET_DIR = ROOT / "data" / "phase65-observation"


def prepare(source: Path = SOURCE, target_dir: Path = TARGET_DIR) -> Path:
    source = source.resolve(strict=True)
    target_dir = target_dir.resolve()
    if not target_dir.is_relative_to(ROOT / "data"):
        raise ValueError("观察副本必须位于项目 data 目录内")
    if source == target_dir / "yosaki.sqlite3":
        raise ValueError("源数据库不能是目标数据库")
    if (target_dir / "yosaki.sqlite3").exists():
        raise FileExistsError("观察副本已存在；不会覆盖或重置观察进度")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "yosaki.sqlite3"
    try:
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as live:
            with sqlite3.connect(target) as copy:
                live.backup(copy)
                copy.execute("PRAGMA foreign_keys=ON")
                copy.execute("DELETE FROM auth_sessions")
                copy.execute("DELETE FROM auth_login_attempts")
                copy.execute("UPDATE model_profiles SET api_key_encrypted=NULL,enabled=CASE WHEN protocol='chatgpt_plan' THEN enabled ELSE 0 END,base_url=CASE WHEN protocol='chatgpt_plan' THEN base_url ELSE 'https://disabled.invalid/v1' END")
                copy.execute("UPDATE mcp_servers SET endpoint='http://127.0.0.1/disabled',auth_secret_encrypted=NULL,session_id=NULL,status='disconnected',enabled=0,last_error=NULL")
                copy.execute("UPDATE mcp_tools SET enabled=0")
                copy.execute("UPDATE backup_plan SET enabled=0,local_dir='',github_repo='',last_run_at=NULL,next_run_at=NULL")
                copy.execute("DELETE FROM backup_runs")
                copy.execute("UPDATE awakening_sessions SET status='paused',termination_reason='observation_copy',lease_owner=NULL,lease_until=NULL WHERE status NOT IN ('completed','cancelled','failed','paused')")
                if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("观察副本完整性检查失败")
                if copy.execute("SELECT COUNT(*) FROM model_profiles WHERE api_key_encrypted IS NOT NULL").fetchone()[0]:
                    raise ValueError("模型密钥未清除")
                if copy.execute("SELECT COUNT(*) FROM mcp_servers WHERE auth_secret_encrypted IS NOT NULL OR session_id IS NOT NULL OR enabled=1").fetchone()[0]:
                    raise ValueError("MCP 密钥或连接未清除")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--target-dir", type=Path, default=TARGET_DIR)
    args = parser.parse_args()
    print(prepare(args.source, args.target_dir))
