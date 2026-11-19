"""Additive schema rehearsal on a NEW copy; never migrate the input database."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app import chat_schema


def prepare(source,destination):
    source=Path(source).resolve(strict=True);destination=Path(destination).resolve()
    protected=(Path(__file__).resolve().parents[1]/'data').resolve()
    if destination.exists() or destination==source or destination.parent==protected or protected in destination.parents:
        raise ValueError('必须指定新建、非活动 data 目录内的目标文件；不会覆盖现有数据库')
    if not destination.parent.is_dir():raise ValueError('请先建立明确的隔离目标目录')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    original=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    original.execute('PRAGMA query_only=ON')
    target=sqlite3.connect(destination)
    try:
        original.backup(target)
        target.execute('PRAGMA foreign_keys=ON')
        chat_schema.install(target);target.commit()
        chat_schema.install(target);target.commit()
        integrity=target.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=target.execute('PRAGMA foreign_key_check').fetchall()
        if integrity!='ok' or foreign:raise ValueError('隔离迁移未通过一致性校验')
    finally:target.close();original.close()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=before:raise ValueError('来源在演练期间发生变化；不使用此副本')
    return {'source_unchanged':True,'source_sha256':before,'candidate_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
        'schema_idempotent':True,'integrity':'ok','foreign_keys':'ok','daily_migrated':False,'service_switched':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source');parser.add_argument('new_destination')
    args=parser.parse_args();print(json.dumps(prepare(args.source,args.new_destination),indent=2))
