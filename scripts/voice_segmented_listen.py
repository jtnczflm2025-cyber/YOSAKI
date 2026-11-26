"""Future separately approved two-HTTP fixture. Default is pure verification.

This script never creates approval; no model/OAuth/catalog/STT/WS capability.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from backend.app.voice import auto_budget
from backend.app.voice.annotation_listening import VoiceAuthority
from backend.app.voice.annotation_planner import fingerprint
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.realtime_receipt import occupy_once
from backend.app.voice.segmented_reading import SegmentedReading
from scripts.voice_segmented_candidate import build,ROOT
from scripts.voice_segmented_lab import bootstrap
from scripts.voice_v3_fixture_probe import local_proxy


def review_app(result_dir=None):
    """Fixed read-only completed fragments, including a failed batch's good prefix."""
    import hashlib
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, Response
    allowed=Path(os.environ['LOCALAPPDATA'])/'YOSAKI/voice-runs'
    parent=(Path(result_dir) if result_dir is not None else allowed/'voice-long-step3-20261118-r1/result')
    if parent.is_symlink() or not parent.resolve().is_relative_to(allowed.resolve()):
        raise ValueError('review_directory_outside_runs')
    record=json.loads((parent/'result.json').read_text(encoding='utf-8'))
    items={str(s['sequence']):s for s in record['segments'] if s['status']=='completed'}
    if not items:raise ValueError('no_complete_audio_to_review')
    app=FastAPI()
    @app.get('/')
    def index():
        return HTMLResponse('''<!doctype html><meta charset="utf-8"><title>YOSAKI 两段试听</title>
        <style>body{max-width:860px;margin:32px auto;padding:16px;background:#e5e8ed;color:#242b34;font:17px/1.75 sans-serif}button{padding:12px;margin:8px}</style>
        <h1>已保存的分段音频</h1><p>只播放已有文件，不生成音频。暂停后可继续，停止后不会自动恢复。</p>
        <button id="play">按顺序播放</button><button id="pause">暂停</button><button id="resume">继续</button><button id="stop">停止</button><p id="state">未播放</p>
        <script>let audio=null,epoch=0;const ids=IDS;
        function stop(){epoch++;if(audio){audio.pause();audio.onended=null;audio.removeAttribute('src');audio.load();audio=null}document.querySelector('#state').textContent='已停止'}
        document.querySelector('#stop').onclick=stop;
        document.querySelector('#pause').onclick=()=>audio?.pause();
        document.querySelector('#resume').onclick=()=>audio?.play();
        document.querySelector('#play').onclick=()=>{stop();let e=epoch,i=0;
        function next(){if(e!==epoch)return;if(i===ids.length){document.querySelector('#state').textContent='已播完';return}
        audio=new Audio('/audio/'+ids[i++]);audio.onended=next;audio.onerror=()=>{stop();document.querySelector('#state').textContent='播放失败'};document.querySelector('#state').textContent='正在播放第'+i+'段';audio.play().catch(()=>stop())}next()};
        window.addEventListener('pagehide',stop);</script>'''.replace('IDS',json.dumps(list(items))))
    @app.get('/audio/{sequence}')
    def audio(sequence:str):
        if sequence not in items:raise HTTPException(404)
        item=items[sequence]
        if item['audio_file']!='segment-%02d.mp3'%(int(sequence)+1):raise HTTPException(409)
        data=(parent/item['audio_file']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=item['audio_sha256']:raise HTTPException(409)
        from backend.app.voice.audio_cache import validate_mp3
        validate_mp3(data)
        return Response(data,media_type='audio/mpeg')
    return app


def verify(manifest):
    if manifest!=build():raise ValueError('frozen_candidate_drift')


def approval(manifest,session_hash,proxy):
    return {'run_id':manifest['run_id'],'manifest_sha256':fingerprint(manifest),
        'code_sha256':manifest['code_sha256'],'session_sha256':session_hash,
        'database':str(ROOT/'data/yosaki.sqlite3'),'proxy':proxy,'voice_binding':manifest['voice_binding'],
        'budget':manifest['budget'],'third_party_processing_confirmed':True,
        'billing_uncertainty_acknowledged':True,'provider_kind':'real_approved'}


async def execute(manifest,session_hash,receipt):
    verify(manifest)
    parent=Path(os.environ['LOCALAPPDATA'])/'YOSAKI/voice-runs'/manifest['run_id']
    if receipt.resolve()!=parent/'approval.json' or receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size>16384:
        raise ValueError('approval_path_invalid')
    proxy=local_proxy()
    if json.loads(receipt.read_text(encoding='utf-8'))!=approval(manifest,session_hash,proxy):
        raise ValueError('approval_binding_invalid')
    authority=VoiceAuthority(ROOT/'data/yosaki.sqlite3',session_hash,manifest['voice_binding'])
    authority.check(jobs=1,requests=2,characters=2130)
    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    if not key:raise ValueError('key_unavailable_before_occupation')
    result_dir=parent/'result'
    if result_dir.exists():raise ValueError('run_directory_exists_no_retry')
    occupy_once(parent/'approval.json.used',{'run_id':manifest['run_id'],
        'manifest_sha256':fingerprint(manifest),'status':'occupied_outcome_unknown'})
    # Only the new isolated store is initialized. No daily main/init/migration.
    connect=bootstrap(parent/'isolated')
    def check():
        verify(manifest)
        value=authority.check()
        return {'voice_id':value['voice_id'],'revision':3}
    reserved=[0,0]
    def reserve(count,first):
        if reserved[0]>=2 or reserved[1]+count>2130:raise ValueError('batch_limit')
        # Both daily and login audit are atomically debited in this existing transaction.
        auto_budget.reserve(authority.write,session_hash,1,count,first_request=first)
        reserved[0]+=1;reserved[1]+=count
    reader=SegmentedReading(result_dir,connect,manifest['preview']['source_text'],
        lambda d:ElevenLabsAdapter(key,proxy_url=proxy,diagnostics=d),check,reserve)
    await reader.start(manifest['preview']['preview_sha256'])
    try:
        await reader.task
    except BaseException:
        await reader.cancel()
        raise
    return reader.result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',type=Path,default=ROOT/'docs/VOICE_STEP3_LISTENING_CANDIDATE.json')
    p.add_argument('--execute-real',action='store_true')
    p.add_argument('--review-only',action='store_true')
    p.add_argument('--session-hash-file',type=Path)
    p.add_argument('--approval-file',type=Path)
    a=p.parse_args()
    try:
        if a.review_only:
            if a.execute_real:raise ValueError('exclusive_mode')
            import uvicorn
            uvicorn.run(review_app(),host='127.0.0.1',port=18894,workers=1,access_log=False)
            return
        m=json.loads(a.manifest.read_text(encoding='utf-8'));verify(m)
        if not a.execute_real:
            print(json.dumps({'status':'verified_zero_dispatch','manifest_sha256':fingerprint(m),
                'code_sha256':m['code_sha256'],'credentials_read':False}));return
        f=a.session_hash_file
        if not f or f.is_symlink() or not f.is_file() or f.stat().st_size>256 or not a.approval_file:
            raise ValueError('session_or_approval_missing')
        s=f.read_text().strip()
        if len(s)!=64 or any(c not in '0123456789abcdef' for c in s):raise ValueError('session_binding_invalid')
        r=asyncio.run(execute(m,s,a.approval_file))
        print(json.dumps({k:r[k] for k in ('status','http_attempts','characters','actual_billing')}))
    except Exception:
        print(json.dumps({'status':'stopped','details':'see_preserved_result_or_occupied_marker',
                          'real_execution_requested':a.execute_real}))
        raise SystemExit(2)


if __name__=='__main__':main()
