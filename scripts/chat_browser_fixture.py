"""Synthetic browser fixture through Runtime test preflight; no real providers."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_chat_isolated_tests import guard
sys.addaudithook(guard)
for key in list(os.environ):
    if key.startswith(('YOSAKI_','ELEVENLABS_','OPENAI_')):os.environ.pop(key,None)
os.environ.update(YOSAKI_BACKGROUND_TASKS_ENABLED='0',YOSAKI_VOICE_ENABLED='0',YOSAKI_ALLOWED_ORIGINS='http://127.0.0.1:28183')
from yosaki_runtime import configuration,preflight,run
directory=Path(tempfile.mkdtemp(prefix='yosaki-chat-browser-'))
runtime=configuration('test',test_dir=directory,port=28182)
preflight(runtime)
os.environ['YOSAKI_DATA_DIR']=str(directory)
from backend.app import main as m
from fastapi import Request
from fastapi.responses import StreamingResponse

@m.app.post('/synthetic/v1/responses')
async def mock(request:Request):
    body=await request.json()
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
    profile=db.execute("INSERT INTO model_profiles(name,base_url,model_name,protocol,created_at,updated_at) VALUES('本地合成模型','http://127.0.0.1:28182/synthetic/v1','synthetic-only','openai_responses',?,?)",(m.now(),m.now())).lastrowid
    db.execute("UPDATE characters SET name='合成测试角色',system_prompt='只用于隔离界面测试。',default_model_id=? WHERE id=?",(profile,cid))
    db.execute('DELETE FROM prompt_blocks WHERE character_id=?',(cid,))
(ROOT/'evidence'/'browser-runtime.json').write_text(json.dumps({'data_dir':str(directory),'backend':28182,'frontend':28183,'synthetic_only':True}),encoding='utf-8')
run(runtime,inherited_data_dir=str(directory))
