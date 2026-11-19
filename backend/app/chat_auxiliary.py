"""Explicit auxiliary requests use the same durable identity and raw usage observer."""
import asyncio
import json
import secrets
import time
import httpx
from fastapi import HTTPException
from . import chat_requests as ledger, model_capabilities, responses_adapter, chatgpt_plan


async def generate(m,cid,profile_id,purpose,prompt,source_ids):
    aid=secrets.token_hex(16);started=time.monotonic();status='unknown';text='';terminal=None
    with m.connect() as db:
        conv=m.require_live_conversation(db,cid)
        character_id=db.execute('SELECT character_id FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        profile=db.execute('SELECT * FROM model_profiles WHERE id=? AND enabled=1',(profile_id,)).fetchone()
        if profile is None:raise HTTPException(422,'请选择可用的辅助任务模型')
        profile=model_capabilities.effective(profile,{})
        ledger.begin(db,aid,cid,character_id,profile,purpose)
    p=dict(profile);protocol=p['protocol'];messages=[{'role':'user','content':prompt}]
    try:
        if protocol=='chatgpt_plan':
            body=chatgpt_plan.payload(p,messages,[]);url=chatgpt_plan.API_BASE+'/responses'
        elif protocol=='openai_responses':
            body=responses_adapter.payload(p,messages,[],True);url=responses_adapter.endpoint(p['base_url'])
        else:
            body={'model':p['model_name'],'messages':messages,'max_tokens':p['max_tokens'],'temperature':p['temperature'],'top_p':p['top_p'],'stream':True,'stream_options':{'include_usage':True}}
            if p.get('reasoning_effort'):body['reasoning_effort']=p['reasoning_effort']
            url=m.model_url(p['base_url'])
        from .chat_context import estimate
        if estimate(body)+p['max_tokens']>p['context_limit']:
            raise HTTPException(422,'辅助请求超过所选模型的保守容量预算，未派发')
        ledger.wire(m.connect,aid,body,parameters=model_capabilities.effects(p,body),sources={'history_ids':source_ids})
        headers={'Authorization':'Bearer '+await chatgpt_plan.access_token(allow_refresh=False)} if protocol=='chatgpt_plan' else m.auth_headers(p)
        state=responses_adapter.StreamState() if protocol!='openai_compatible' else None
        ledger.dispatched(m.connect,aid)
        async with httpx.AsyncClient(timeout=httpx.Timeout(120,connect=15)) as client:
            async with client.stream('POST',url,json=body,headers=headers) as response:
                if response.status_code>=400:raise HTTPException(502,'辅助模型返回 HTTP '+str(response.status_code))
                async for line in response.aiter_lines():
                    if not line.startswith('data:'):continue
                    raw=line[5:].strip()
                    if raw=='[DONE]':break
                    try:event=json.loads(raw)
                    except ValueError:continue
                    ledger.observe(m.connect,aid,event,(time.monotonic()-started)*1000)
                    normalized=state.normalize(event) if state else event
                    choice=(normalized.get('choices') or [{}])[0]
                    text+=(choice.get('delta') or {}).get('content') or ''
                    terminal=choice.get('finish_reason') or terminal
                    if len(text)>80000:raise HTTPException(422,'辅助回复超过本地保存容量；已发生用量仍保留')
        if terminal!='stop' or not text.strip():raise HTTPException(502,'辅助回复不完整，原内容保留；未自动重试')
        status='completed'
        return text,aid
    except asyncio.CancelledError:
        status='cancelled';raise
    finally:
        with m.connect() as db:
            row=db.execute('SELECT dispatched,terminal FROM chat_attempts WHERE id=?',(aid,)).fetchone()
            if status=='unknown' and (not row['dispatched'] or row['terminal']):status='failed'
            db.execute('UPDATE chat_attempts SET status=?,finished_at=?,elapsed_ms=? WHERE id=?',(status,m.now(),(time.monotonic()-started)*1000,aid))
