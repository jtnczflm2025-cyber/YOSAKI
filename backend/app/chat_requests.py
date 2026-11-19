"""Durable request identity and accounting independent from displayed answers."""
import asyncio
import hashlib
import json
import time
import os
from datetime import datetime, timezone
from fastapi import HTTPException
from . import chat_history


def stamp():
    return datetime.now(timezone.utc).isoformat()


def process_identity(pid):
    """Read a process creation identity; never send a signal on Windows."""
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        dll=ctypes.WinDLL('kernel32',use_last_error=True)
        dll.OpenProcess.restype=wintypes.HANDLE
        dll.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        handle=dll.OpenProcess(0x1000,False,pid)
        if not handle:return None if ctypes.get_last_error()==87 else 'unreadable'
        try:
            values=[wintypes.FILETIME() for _ in range(4)]
            dll.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            if not dll.GetProcessTimes(handle,*map(ctypes.byref,values)):return 'unreadable'
            return str((values[0].dwHighDateTime<<32)|values[0].dwLowDateTime)
        finally:
            dll.CloseHandle.argtypes=[wintypes.HANDLE];dll.CloseHandle(handle)
    try:
        from pathlib import Path
        return Path('/proc/'+str(pid)+'/stat').read_text().split(') ',1)[1].split()[19]
    except FileNotFoundError:return None
    except OSError:return 'unreadable'


def reconcile_workers(db,cid):
    for row in db.execute("""SELECT a.id,a.dispatched,w.pid,w.process_identity FROM chat_attempts a
        JOIN chat_attempt_workers w ON w.attempt_id=a.id WHERE a.conversation_id=?
        AND a.status IN ('prepared','dispatching','streaming')""",(cid,)).fetchall():
        current=process_identity(row['pid'])
        if current!='unreadable' and current!=row['process_identity']:
            db.execute("UPDATE chat_attempts SET status=?,finished_at=?,error_kind='worker_lost' WHERE id=?",
                ('unknown' if row['dispatched'] else 'failed',stamp(),row['id']))
            db.execute("UPDATE messages SET tool_batch_status='interrupted' WHERE conversation_id=? AND tool_batch_status='continuing'",(cid,))


def dumps(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True)


def normalize_usage(raw):
    if not isinstance(raw,dict):
        return {key:None for key in ('input','output','cache_read','cache_write','reasoning')}
    def integer(value):
        return value if type(value) is int and value>=0 else None
    inp = raw.get('input_tokens',raw.get('prompt_tokens'))
    out = raw.get('output_tokens',raw.get('completion_tokens'))
    details = raw.get('input_tokens_details',raw.get('prompt_tokens_details')) or {}
    outputs = raw.get('output_tokens_details',raw.get('completion_tokens_details')) or {}
    if not isinstance(details,dict):details={}
    if not isinstance(outputs,dict):outputs={}
    return dict(input=integer(inp),output=integer(out),cache_read=integer(details.get('cached_tokens')),
        cache_write=integer(details.get('cache_write_tokens',raw.get('cache_creation_input_tokens'))),
        reasoning=integer(outputs.get('reasoning_tokens')))


def begin(db, aid, cid, character_id, profile, purpose, expected_head=None):
    chat_history.assert_idle(db,cid)
    chat_history.check_head(db,cid,expected_head)
    p=dict(profile) if profile else {}
    db.execute('''INSERT INTO chat_attempts(id,conversation_id,character_id,purpose,protocol,requested_model,started_at)
        VALUES(?,?,?,?,?,?,?)''',(aid,cid,character_id,purpose,p.get('protocol','demo'),p.get('model_name','demo'),stamp()))
    db.execute('INSERT INTO chat_attempt_workers VALUES(?,?,?)',(aid,os.getpid(),process_identity(os.getpid()) or 'unreadable'))
    branch=db.execute('SELECT root_id FROM chat_branches WHERE conversation_id=?',(cid,)).fetchone()
    db.execute('UPDATE chat_attempts SET root_id=? WHERE id=?',(branch[0] if branch else cid,aid))


def safe_snapshot(value):
    if isinstance(value,list):
        return [safe_snapshot(x) for x in value]
    if isinstance(value,dict):
        result={}
        for k,v in value.items():
            if k in ('encrypted_content','provider_state','api_key','authorization','access_token'):
                result[k]={'omitted':'private_state','sha256':hashlib.sha256(dumps(v).encode()).hexdigest()}
            elif k in ('image_url','file_data'):
                result[k]={'omitted':'attachment_bytes','sha256':hashlib.sha256(dumps(v).encode()).hexdigest()}
            else:
                result[k]=safe_snapshot(v)
        return result
    return value


def wire(connect,aid,body,*,parameters=None,sources=None):
    frozen=dumps(body)
    with connect() as db:
        sources=dict(sources or {})
        if sources.get('budget',{}).get('enabled'):
            if len(frozen.encode('utf-8'))>sources['budget']['input_budget']:
                raise HTTPException(422,'最终协议请求超过保守预算，未派发；请减少附件或调整模型容量')
            sources['budget']['serialized_bytes']=len(frozen.encode('utf-8'))
        if 'history_refs' not in sources:
            sources['history_refs']=[{'id':mid,'sha256':chat_history.digest(row['content'] or '')} for mid in sources.get('history_ids',[]) if (row:=db.execute('SELECT content FROM messages WHERE id=?',(mid,)).fetchone())]
        prior=db.execute('SELECT * FROM chat_attempts WHERE conversation_id=(SELECT conversation_id FROM chat_attempts WHERE id=?) AND id!=? AND snapshot_json IS NOT NULL ORDER BY started_at DESC LIMIT 1',(aid,aid)).fetchone()
        if prior:
            old=json.loads(prior['snapshot_json']);new=safe_snapshot(body)
            old_parts=old.get('messages',old.get('input',[]));new_parts=new.get('messages',new.get('input',[]))
            shared=0
            for left,right in zip(old_parts,new_parts):
                if left!=right:break
                shared+=1
            sources=dict(sources or {},prefix_comparison={'compared_attempt':prior['id'],'common_items':shared,
                'instructions_changed':old.get('instructions')!=new.get('instructions'),
                'tools_changed':old.get('tools')!=new.get('tools'),
                'model_changed':old.get('model')!=new.get('model'),'cache_hit_guaranteed':False})
        db.execute('UPDATE chat_attempts SET snapshot_json=?,snapshot_sha256=?,parameters_json=?,sources_json=? WHERE id=?',
            (dumps(safe_snapshot(body)),hashlib.sha256(frozen.encode()).hexdigest(),dumps(parameters or {}),dumps(sources or {}),aid))


def dispatched(connect,aid):
    with connect() as db:
        from .chat_context import sources_current
        current=db.execute('SELECT * FROM chat_attempts WHERE id=?',(aid,)).fetchone()
        if current is None or not sources_current(db,current,json.loads(current['sources_json'] or '{}')):
            raise HTTPException(409,'请求来源已变化或撤权，未派发；原文与已有用量保留')
        if not db.execute("UPDATE chat_attempts SET dispatched=1,status='dispatching' WHERE id=? AND status='prepared'",(aid,)).rowcount:
            raise HTTPException(409,'请求身份已失效，不会重复派发')
        db.execute('UPDATE chat_attempts SET dispatched_at=? WHERE id=?',(stamp(),aid))


def observe(connect,aid,data,elapsed_ms=None):
    response=data.get('response') or {}
    raw=response.get('usage') if 'usage' in response else data.get('usage')
    terminal=data.get('type') if data.get('type') in ('response.completed','response.incomplete','response.failed') else None
    choices=data.get('choices') or []
    if choices and choices[0].get('finish_reason'):
        terminal=choices[0]['finish_reason']
    text=data.get('type')=='response.output_text.delta' or bool(choices and (choices[0].get('delta') or {}).get('content'))
    with connect() as db:
        previous=db.execute('SELECT * FROM chat_attempts WHERE id=?',(aid,)).fetchone()
        if previous is None:return
        rid=response.get('id') or data.get('id')
        if previous['response_id'] and rid and rid!=previous['response_id']:
            if isinstance(raw,dict):
                fingerprint=hashlib.sha256(dumps({'response_id':rid,'usage':raw}).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO chat_usage_receipts VALUES(?,?,?,?,?)',(aid,fingerprint,rid,dumps(raw),'response_identity_rejected'))
            return
        if isinstance(raw,dict):
            usage=normalize_usage(raw)
            old_usage=json.loads(previous['normalized_json']) if previous['normalized_json'] else normalize_usage(None)
            merged={key:usage[key] if usage[key] is not None else old_usage.get(key) for key in usage}
            reliable='provided' if merged['input'] is not None and merged['output'] is not None else 'partial'
            if not terminal and not previous['terminal']:reliable='provisional'
            fingerprint=hashlib.sha256(dumps({'response_id':rid or previous['response_id'],'usage':raw}).encode()).hexdigest()
            conflict=previous['reliability']=='conflict' or previous['reliability']=='provided' and any(usage[key] is not None and old_usage.get(key) is not None and usage[key]!=old_usage[key] for key in usage)
            inserted=db.execute('INSERT OR IGNORE INTO chat_usage_receipts VALUES(?,?,?,?,?)',(aid,fingerprint,rid,dumps(raw),'conflict' if conflict else 'accepted')).rowcount
            if inserted or previous['reliability']=='provisional' and reliable!='provisional':
                if conflict:
                    # Preserve both receipts, never silently replace settled usage.
                    db.execute("UPDATE chat_attempts SET reliability='conflict' WHERE id=?",(aid,))
                else:db.execute('UPDATE chat_attempts SET usage_json=?,normalized_json=?,reliability=? WHERE id=?',(dumps(raw),dumps(merged),reliable,aid))
        if terminal:
            db.execute('UPDATE chat_attempts SET terminal=? WHERE id=?',(terminal,aid))
        model=response.get('model') or data.get('model')
        if model: db.execute('UPDATE chat_attempts SET returned_model=? WHERE id=?',(model,aid))
        if rid: db.execute('UPDATE chat_attempts SET response_id=? WHERE id=?',(rid,aid))
        if text and elapsed_ms is not None:
            db.execute('UPDATE chat_attempts SET first_text_ms=COALESCE(first_text_ms,?),last_text_ms=? WHERE id=?',(elapsed_ms,elapsed_ms,aid))


async def tracked(iterator,connect,aid):
    started=time.monotonic(); state='unknown'; message_id=None; error=None;public=[];reasoning=[];monologue=[]
    with connect() as db:
        initial=db.execute('SELECT * FROM chat_attempts WHERE id=?',(aid,)).fetchone()
        before=db.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE conversation_id=?',(initial['conversation_id'],)).fetchone()[0]
    try:
        async for event in iterator:
            try:
                name=event.split('\n',1)[0].removeprefix('event: ')
                data=json.loads(event.split('data: ',1)[1].strip())
            except (ValueError,IndexError):
                yield event
                continue
            if name=='text_delta':
                public.append(data.get('text',''))
                with connect() as db:
                    db.execute('UPDATE chat_attempts SET first_public_ms=COALESCE(first_public_ms,?) WHERE id=?',((time.monotonic()-started)*1000,aid))
            if name=='reasoning_delta':reasoning.append(data.get('text',''))
            if name=='monologue_delta':monologue.append(data.get('text',''))
            if name in ('message_complete','tool_batch_waiting'):
                state='completed' if data.get('generation_status','completed')=='completed' else 'incomplete'
                message_id=data.get('message_id') or data.get('assistant_message_id')
            if name=='error':
                error='stream_error'
            if name=='usage': observe(connect,aid,{'usage':data})
            if name=='message_start':
                data['attempt_id']=aid
                event=f'event: {name}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'
            yield event
    except (asyncio.CancelledError,GeneratorExit):
        state='cancelled'
        raise
    except Exception as exc:
        error=type(exc).__name__
        message=exc.detail if isinstance(exc,HTTPException) else '请求处理失败；已发生用量与原消息保留'
        yield 'event: error\ndata: '+json.dumps({'message':message},ensure_ascii=False)+'\n\n'
    finally:
        await iterator.aclose()
        with connect() as db:
            row=db.execute('SELECT * FROM chat_attempts WHERE id=?',(aid,)).fetchone()
            if row:
                if state=='unknown' and not row['dispatched']: state='failed'
                if state=='unknown' and row['terminal']: state='failed'
                if message_id is None:
                    saved=db.execute("SELECT id FROM messages WHERE conversation_id=? AND id>? AND role='assistant' AND generation_status='stopped' ORDER BY id DESC LIMIT 1",(row['conversation_id'],before)).fetchone()
                    if saved:message_id=saved[0]
                    elif public or reasoning or monologue:
                        message_id=db.execute("""INSERT INTO messages(conversation_id,role,content,model,created_at,generation_status,reasoning_content,monologue_content,usage_json)
                            VALUES(?,'assistant',?,?,?,?,?,?,?)""",(row['conversation_id'],''.join(public),row['requested_model'],stamp(),'stopped' if state=='cancelled' else 'incomplete',''.join(reasoning),''.join(monologue),row['usage_json'])).lastrowid
                db.execute('UPDATE chat_attempts SET status=?,assistant_message_id=?,finished_at=?,elapsed_ms=?,error_kind=? WHERE id=?',
                    (state,message_id,stamp(),(time.monotonic()-started)*1000,error,aid))
                if message_id:
                    db.execute('UPDATE messages SET usage_json=COALESCE(usage_json,?) WHERE id=?',(row['usage_json'],message_id))
                    db.execute('''INSERT INTO chat_message_state(message_id,attempt_id,changed_at) VALUES(?,?,?)
                        ON CONFLICT(message_id) DO UPDATE SET attempt_id=excluded.attempt_id''',(message_id,aid,stamp()))


def totals(db,cid):
    branch=db.execute('SELECT root_id FROM chat_branches WHERE conversation_id=?',(cid,)).fetchone()
    root=branch['root_id'] if branch else cid
    ids=[root,*[r[0] for r in db.execute('SELECT conversation_id FROM chat_branches WHERE root_id=?',(root,))]]
    attempts=[dict(r) for r in db.execute('SELECT * FROM chat_attempts WHERE root_id=? OR conversation_id IN ('+','.join('?' for _ in ids)+')',[root,*ids])]
    fields=('input','output','cache_read','cache_write','reasoning')
    result={k:{'known':0,'provided':0,'unknown':0} for k in fields}
    for row in attempts:
        if not row['dispatched']:continue
        normalized=json.loads(row['normalized_json']) if row['normalized_json'] and row['reliability'] not in ('conflict','provisional') else normalize_usage(None)
        for k,v in normalized.items():
            if v is None: result[k]['unknown']+=1
            else: result[k]['known']+=v;result[k]['provided']+=1
    historical=[r[0] for r in db.execute("SELECT m.usage_json FROM messages m WHERE m.conversation_id IN ("+','.join('?' for _ in ids)+") AND m.usage_json IS NOT NULL AND COALESCE(m.generation_status,'')!='imported' AND NOT EXISTS(SELECT 1 FROM chat_attempts a WHERE a.assistant_message_id=m.id)",ids)]
    def subtotal(items):
        value={'attempts':len(items),'dispatched':sum(bool(r['dispatched']) for r in items),'input_known':0,'output_known':0,'input_unknown':0,'output_unknown':0}
        for item in items:
            if not item['dispatched']:continue
            usage=json.loads(item['normalized_json']) if item['normalized_json'] and item['reliability'] not in ('conflict','provisional') else normalize_usage(None)
            for key in ('input','output'):
                if usage[key] is None:value[key+'_unknown']+=1
                else:value[key+'_known']+=usage[key]
        return value
    return {'attempts':len(attempts),'tokens':result,'scope':'root_and_branches','includes_deleted_candidates':True,'cost':None,
        'dispatched_attempts':sum(bool(r['dispatched']) for r in attempts),
        'current_branch':subtotal([r for r in attempts if r['conversation_id']==cid]),
        'by_purpose':{purpose:subtotal([r for r in attempts if r['purpose']==purpose]) for purpose in sorted({r['purpose'] for r in attempts})},
        'mapping_version':'chat-usage-v1','conflicting_receipts':sum(r['reliability']=='conflict' for r in attempts),
        'historical_message_usage':{'records':len(historical),'usage':[normalize_usage(json.loads(raw)) for raw in historical],'included_in_attempt_totals':False},
        'external_ledgers':{'memory':'原有 Memory 任务账本；此处不重复计入','voice':'原有 Voice 账本；此处不重复计入'}}
