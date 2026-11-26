"""Restore a V1/V2 .ybackup into a new, empty directory only.

This script deliberately does not import backend.app.main or open the live DB.
"""

import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.backup_v2 import BackupError, restore_to_directory  # noqa: E402
from backend.app.backup_v2_environment import validate_work_root  # noqa: E402
from backend.app.backup_v2_limits import OPERATIONAL_DATABASE_MAX, preflight_restore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True,
                        help="New directory; an existing directory is always refused")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="Protected, preferably encrypted temporary volume")
    args = parser.parse_args()
    try:
        work_root = validate_work_root(args.work_dir)
        if args.target.resolve().parent != work_root:
            raise BackupError("restore_target_outside_protected_work_root")
        preflight_restore(args.backup, work_root)
        phrase = getpass.getpass("备份密码（输入不回显）: ")
        result = restore_to_directory(args.backup, phrase, args.target, work_root,
                                      max_database_bytes=OPERATIONAL_DATABASE_MAX)
    except (BackupError, OSError) as exc:
        print("隔离恢复失败：" + (exc.code if isinstance(exc, BackupError) else "filesystem_error"), file=sys.stderr)
        return 1
    print(json.dumps({"restored_to": str(args.target.resolve()),
                      "format_version": result["format_version"],
                      "database_sha256": result["database_sha256"],
                      "table_counts": result["tables"],
                      "live_database_unchanged": True}, ensure_ascii=False))
    print("凭据与活动会话未包含在备份中；切换数据仍需单独审核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
