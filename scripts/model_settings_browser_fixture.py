"""Synthetic browser fixture through Runtime test preflight; no real providers."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
(ROOT/'evidence').mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_chat_isolated_tests import guard
sys.addaudithook(guard)
for key in list(os.environ):
    if key.startswith(('YOSAKI_','ELEVENLABS_','OPENAI_')):os.environ.pop(key,None)
os.environ.update(YOSAKI_BACKGROUND_TASKS_ENABLED='0',YOSAKI_VOICE_ENABLED='0',YOSAKI_ALLOWED_ORIGINS='http://yosaki-settings.localhost:28187')
from yosaki_runtime import configuration,preflight,run
directory=Path(tempfile.mkdtemp(prefix='yosaki-chat-browser-'))
runtime=configuration('test',test_dir=directory,port=28186)
preflight(runtime)
os.environ['YOSAKI_DATA_DIR']=str(directory)
from backend.app import main as m
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.responses import JSONResponse

control={'reject':False,'delay_headers':False}
wire=[]
@m.app.post('/_fixture/control')
async def configure(request:Request):
    control.update(await request.json())
    return control

@m.app.middleware('http')
async def mock_rejection(request,call_next):
    if request.url.path in ('/api/chat/stream','/api/chat/regenerate'):
        if control['reject']:
            return JSONResponse({'detail':'合成 HTTP 拒绝，未接受请求'},status_code=422)
        response=await call_next(request)
        if control['delay_headers']:
            import asyncio
            await asyncio.sleep(3)
        return response
    return await call_next(request)

@m.app.post('/synthetic/v1/responses')
async def mock(request:Request):
    body=await request.json()
    wire.append({'reasoning':body.get('reasoning'),'max_output_tokens':body.get('max_output_tokens')})
    (ROOT/'evidence'/'mock-wire.json').write_text(json.dumps(wire),encoding='utf-8')
    contents=json.dumps(body.get('input',[]),ensure_ascii=False)
    answer='合成回复：已收到本轮输入。\n条件仍需确认；这不是私人资料。'
    if '翻译' in contents:answer='Synthetic translation: conditions remain unconfirmed.'
    elif '连续性摘要' in contents:answer='合成摘要：用户提出测试问题，条件未决；后续更正须保留。'
    import uuid
    rid='synthetic-'+str(uuid.uuid4())
    async def events():
        yield 'data: '+json.dumps({'type':'response.output_text.delta','delta':answer},ensure_ascii=False)+'\n\n'
        yield 'data: '+json.dumps({'type':'response.completed','response':{'id':rid,'model':'synthetic-only','usage':{'input_tokens':123,'output_tokens':25},'output':[]}})+'\n\n'
    return StreamingResponse(events(),media_type='text/event-stream')

with m.connect() as db:
    cid=db.execute('SELECT id FROM characters LIMIT 1').fetchone()[0]
    profile=db.execute("INSERT INTO model_profiles(name,base_url,model_name,protocol,created_at,updated_at) VALUES('本地合成模型','http://127.0.0.1:28186/synthetic/v1','synthetic-only','openai_responses',?,?)",(m.now(),m.now())).lastrowid
    db.execute("UPDATE characters SET name='合成测试角色',system_prompt='只用于隔离界面测试。',default_model_id=? WHERE id=?",(profile,cid))
    db.execute('DELETE FROM prompt_blocks WHERE character_id=?',(cid,))
(ROOT/'evidence'/'browser-runtime.json').write_text(json.dumps({'data_dir':str(directory),'backend':28186,'frontend':28187,'synthetic_only':True}),encoding='utf-8')
with m.connect() as db:
    db.execute("UPDATE model_profiles SET reasoning_effort='low' WHERE id=?",(profile,))
    db.execute("INSERT INTO model_profiles(name,base_url,model_name,protocol,reasoning_effort,created_at,updated_at) VALUES('第二合成档案','http://127.0.0.1:28186/synthetic/v1','synthetic-only','openai_responses','medium',?,?)",(m.now(),m.now()))
    db.execute("INSERT INTO characters(name,subtitle,system_prompt,greeting,mood,enabled,created_at,updated_at) VALUES('第二合成角色','','仅用于隔离测试','','',1,?,?)",(m.now(),m.now()))
    for title in ['合成会话一','合成会话二']:
        db.execute('INSERT INTO conversations(character_id,title,model_profile_id,created_at,updated_at) VALUES(?,?,?,?,?)',(cid,title,profile,m.now(),m.now()))
run(runtime,inherited_data_dir=str(directory))
