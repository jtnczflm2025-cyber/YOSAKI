"""Actual product application with fake-only transport and browser diagnostics.

No frontend page is created here. The browser uses the existing product build.
All records, login and provider counters are synthetic and local to --data-dir.
"""
import argparse
import asyncio
from pathlib import Path
import sys
from fastapi import HTTPException, Request

from scripts.voice_ai_chat_candidate_fixture import Fixture
from scripts.voice_ai_http_lab import BODY, MP3


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,required=True)
    args=parser.parse_args()
    fixture=Fixture(args.data_dir)
    main=fixture.main
    from backend.app.voice import manager,audio_cache
    conversation=fixture.client.post('/api/conversations',json={'character_id':1,'title':'旧缓存兼容样本'}).json()['id']
    with main.connect() as db:
        message=db.execute("INSERT INTO messages(conversation_id,role,content,model,created_at,generation_status) VALUES(?,'assistant',?,'synthetic-legacy',?,'completed')",
                           (conversation,BODY,main.now())).lastrowid
    context=manager.speech_context(main.connect,message,1)
    _,cache,attempt=audio_cache.claim(main.connect,1,'synthetic-voice',3,'ja',BODY,set())
    audio_cache.publish(main.connect,cache,attempt,MP3)
    with main.connect() as db:
        db.execute('INSERT INTO voice_message_audio(message_id,cache_id,sequence,source_sha256,profile_revision,created_at) VALUES(?,?,0,?,3,?)',
                   (message,cache,context['digest'],audio_cache.stamp()))
    alternate=fixture.client.post('/api/models',json={'name':'明确选定的规划档案 B','provider_name':'synthetic','protocol':'chatgpt_plan',
        'base_url':'https://synthetic.invalid/v1','model_name':'gpt-6.1-sol','max_tokens':2048,'enabled':True}).json()['id']

    @main.app.get('/api/synthetic/voice-stats')
    async def stats():
        with main.connect() as db:
            plans=[dict(r) for r in db.execute('SELECT id,state,revision FROM annotation_plans ORDER BY rowid')]
            audio=[dict(r) for r in db.execute('SELECT plan_id,revision,state,characters,length(audio) AS bytes FROM annotation_audio ORDER BY rowid')]
        return {**fixture.counters(),'legacy_conversation_id':conversation,'legacy_message_id':message,
                'planner_id':fixture.model_id,'alternate_planner_id':alternate,'plans':plans,'audio':audio,'real_provider_requests':0}

    @main.app.post('/api/synthetic/voice-scenario')
    async def scenario(request: Request):
        payload=await request.json()
        mode=payload.get('mode')
        if mode not in ('normal','failed','unknown','hold','late_tts'):raise HTTPException(422,'synthetic_scenario_only')
        fixture.scenario=mode
        (fixture.root/'scenario.txt').write_text(mode,encoding='utf-8')
        fixture.release.clear()
        (fixture.root/'release-audio').unlink(missing_ok=True)
        return {'scenario':mode}

    @main.app.post('/api/synthetic/voice-release')
    async def release():
        fixture.release.set();(fixture.root/'release-audio').touch()
        return {'released':True}

    def deny(event,_arguments):
        if event=='socket.connect':raise PermissionError('synthetic_server_outbound_denied')
    async def serve():
        import uvicorn
        sys.addaudithook(deny)
        await uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=18901,workers=1,access_log=False)).serve()
    try:asyncio.run(serve())
    finally:fixture.close()


if __name__=='__main__':main()
