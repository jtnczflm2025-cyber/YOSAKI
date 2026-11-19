"""Offline branch-aware read-only fallback. No app import, network or database writes."""
import argparse
import html
import json
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app import chat_history, chat_requests

def export(source,destination):
    source=Path(source).resolve(strict=True);destination=Path(destination).resolve()
    if destination.exists():raise ValueError('输出目录必须为新目录')
    db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    try:
        conversations=[]
        for row in db.execute('SELECT id,title FROM conversations ORDER BY id'):
            conversations.append({'id':row['id'],'title':row['title'],'messages':[dict(m) for m in chat_history.rows(db,row['id'])], 'statistics':chat_requests.totals(db,row['id'])})
        destination.mkdir(parents=True)
        (destination/'paths.json').write_text(json.dumps(conversations,ensure_ascii=False,indent=2),encoding='utf-8')
        text=['<!doctype html><meta charset="utf-8"><title>维护期间只读对话</title><style>body{font:17px/1.75 sans-serif;max-width:860px;margin:24px auto;padding:16px;background:#F8F9FB;color:#242B34}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>维护期间只读对话</h1><p>离线导出；保留当前分支可见路径。全部候选与账目仍在原数据库。</p>']
        for conv in conversations:
            text.append('<h2>'+html.escape(str(conv['id'])+' · '+conv['title'])+'</h2>')
            for msg in conv['messages']:text.append('<p>'+html.escape(msg['role'])+'</p><pre>'+html.escape(msg['content'] or '')+'</pre>')
        (destination/'index.html').write_text(''.join(text),encoding='utf-8')
        return {'conversations':len(conversations),'database_changes':db.total_changes,'network_calls':0,'output':str(destination)}
    finally:db.close()
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('new_directory');a=p.parse_args();print(json.dumps(export(a.source,a.new_directory),ensure_ascii=False))
