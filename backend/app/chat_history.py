"""Immutable message bodies and ordered branch references; no derived memory creation."""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from fastapi import HTTPException
from . import chat_schema

READ_SCOPE = ContextVar('yosaki_chat_read_scope', default=None)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def rows(db, conversation_id):
    if not chat_schema.installed(db):
        return list(db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id', (conversation_id,)))
    inherited = [dict(r) for r in db.execute('''SELECT m.* FROM chat_branch_members b JOIN messages m ON m.id=b.message_id
        WHERE b.conversation_id=? AND NOT EXISTS (SELECT 1 FROM chat_path_exclusions x
        WHERE x.conversation_id=b.conversation_id AND x.message_id=b.message_id) ORDER BY b.position''', (conversation_id,))]
    local = [dict(r) for r in db.execute('''SELECT m.* FROM messages m LEFT JOIN chat_message_state s ON s.message_id=m.id
        WHERE m.conversation_id=? AND COALESCE(s.hidden,0)=0 ORDER BY m.id''', (conversation_id,))]
    # A fork preserves evidence, never executable old calls or provider-private state.
    for row in inherited:
        row['inherited'] = True
        row['provider_state_json'] = None
        if row['role'] == 'tool':
            row['generation_status']='imported'
            continue
        if row.get('tool_calls_json'):
            row['inherited_tool_calls_json']=row['tool_calls_json']
            row['tool_calls_json'] = None
            row['tool_batch_status'] = 'historical'
    return inherited + local


def visible(db, conversation_id, message_id):
    return next((r for r in rows(db, conversation_id) if r['id'] == message_id), None)


def assert_idle(db, conversation_id):
    if chat_schema.installed(db):
        from .chat_requests import reconcile_workers
        reconcile_workers(db, conversation_id)
    if chat_schema.installed(db) and db.execute("SELECT 1 FROM chat_attempts WHERE conversation_id=? AND status IN ('prepared','dispatching','streaming')", (conversation_id,)).fetchone():
        raise HTTPException(409, '此分支正在生成，请停止并等待保存结束')
    if db.execute("SELECT 1 FROM messages WHERE conversation_id=? AND tool_batch_status='continuing'", (conversation_id,)).fetchone():
        raise HTTPException(409, '此分支正在继续工具回合')
    if db.execute("SELECT 1 FROM tool_runs WHERE conversation_id=? AND status='running'",(conversation_id,)).fetchone():
        raise HTTPException(409,'此分支的工具正在执行，请等待结果')


def check_head(db, conversation_id, expected):
    history = rows(db, conversation_id)
    head = history[-1]['id'] if history else 0
    if expected is not None and expected != head:
        raise HTTPException(409, '分支已有新内容，请刷新后操作')
    return history


def hide(db, ids, stamp, *, group=None, reason='regenerate'):
    for mid in ids:
        db.execute('''INSERT INTO chat_message_state(message_id,hidden,group_id,reason,changed_at) VALUES(?,1,?,?,?)
            ON CONFLICT(message_id) DO UPDATE SET hidden=1,group_id=excluded.group_id,reason=excluded.reason,changed_at=excluded.changed_at''', (mid, group, reason, stamp))
        # Retain result and approval evidence; prevent unexecuted old proposals firing later.
        db.execute("UPDATE tool_runs SET status='revoked',error='消息已退出当前路径' WHERE assistant_message_id=? AND status='pending_confirmation'", (mid,))


def fork(db, conversation_id, message_id, stamp, *, before=False):
    assert_idle(db, conversation_id)
    conv = db.execute('SELECT * FROM conversations WHERE id=?', (conversation_id,)).fetchone()
    if not conv or dict(conv).get('archive_only'):
        raise HTTPException(409, '归档不可建立活动分支')
    history = rows(db, conversation_id)
    pos = next((i for i,r in enumerate(history) if r['id']==message_id), None)
    if pos is None:
        raise HTTPException(404, '消息不在当前分支')
    parent = db.execute('SELECT * FROM chat_branches WHERE conversation_id=?', (conversation_id,)).fetchone()
    cid = db.execute('INSERT INTO conversations(character_id,title,model_profile_id,created_at,updated_at) VALUES(?,?,?,?,?)',
        (conv['character_id'], conv['title'][:140]+' · 分支', conv['model_profile_id'], stamp, stamp)).lastrowid
    db.execute('INSERT INTO chat_branches VALUES(?,?,?,?,?)', (cid, parent['root_id'] if parent else conversation_id, conversation_id, message_id, stamp))
    selected = history[:pos if before else pos+1]
    db.executemany('INSERT INTO chat_branch_members VALUES(?,?,?)', [(cid,i,r['id']) for i,r in enumerate(selected)])
    # No copy of conversation_summaries, temporary memories, usage or learning state.
    return cid


def scope(db, conversation_id):
    if conversation_id is None or not chat_schema.installed(db):
        return None
    branch = db.execute('SELECT * FROM chat_branches WHERE conversation_id=?', (conversation_id,)).fetchone()
    hidden = db.execute('SELECT 1 FROM chat_message_state s JOIN messages m ON m.id=s.message_id WHERE m.conversation_id=? AND s.hidden=1', (conversation_id,)).fetchone()
    related=db.execute('SELECT 1 FROM chat_branches WHERE root_id=?',(conversation_id,)).fetchone()
    if not branch and not hidden and not related:
        return None
    history = rows(db, conversation_id)
    root = branch['root_id'] if branch else conversation_id
    family = {root, *[r[0] for r in db.execute('SELECT conversation_id FROM chat_branches WHERE root_id=?', (root,))]}
    return {'conversation_id':conversation_id, 'allowed':{r['id'] for r in history}, 'family':family,'path_changed':bool(branch or hidden)}


@contextmanager
def reading(db, conversation_id):
    token = READ_SCOPE.set(scope(db, conversation_id))
    try:
        yield
    finally:
        READ_SCOPE.reset(token)


def source_allowed(db, message_id, conversation_id):
    current = READ_SCOPE.get()
    return current is None or conversation_id not in current['family'] or int(message_id) in current['allowed']


def memory_allowed(db, memory, conversation_id):
    current = scope(db, conversation_id)
    if not chat_schema.installed(db):
        return memory['scope']!='temporary' or memory['conversation_id']==conversation_id
    bound = db.execute('SELECT * FROM chat_memory_scope WHERE memory_id=?', (memory['id'],)).fetchone()
    if bound:
        if bound['shared']:
            return True
        refs = json.loads(bound['source_ids_json'])
        allowed = {r['id'] for r in rows(db,conversation_id)}
        return (bool(refs) or bound['conversation_id']==conversation_id) and set(refs).issubset(allowed)
    if memory['scope']=='temporary':
        # Old manually written temporary notes have no coverage proof. They
        # remain visible in management, but cannot bypass a changed path.
        return memory['conversation_id']==conversation_id and (current is None or not current['path_changed'])
    # Explicit manual role-level long memories remain shared. A conversation-bound
    # long row is not silently promoted to a role-wide fact after branching.
    if memory['conversation_id'] is not None:
        return memory['conversation_id']==conversation_id
    return True


def message_available(db,mid):
    row=db.execute('SELECT conversation_id FROM messages WHERE id=?',(mid,)).fetchone()
    if not row:return False
    if visible(db,row[0],mid):return True
    return any(visible(db,r[0],mid) for r in db.execute('SELECT conversation_id FROM chat_branch_members WHERE message_id=?',(mid,)))


def bind_manual_memory(db,mid,cid,scope_name):
    if scope_name=='temporary':
        db.execute('INSERT OR REPLACE INTO chat_memory_scope VALUES(?,?,?,0)',(mid,cid,json.dumps([r['id'] for r in rows(db,cid)])))
    else:db.execute('DELETE FROM chat_memory_scope WHERE memory_id=?',(mid,))
