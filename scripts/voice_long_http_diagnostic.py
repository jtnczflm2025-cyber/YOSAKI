"""Default pure verification. A NEW separately approved single /stream request.

No model/OAuth/catalog/Scribe/WS capability and no approval issuer.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path

import httpx
from httpcore._async.http11 import AsyncHTTP11Connection
from httpcore._async.http_proxy import AsyncTunnelHTTPConnection

from backend.app.voice import auto_budget
from backend.app.voice.annotation_planner import fingerprint
from backend.app.voice.annotation_listening import VoiceAuthority
from backend.app.voice.long_http_adapter import LongHTTPAdapter,LongHTTPDiagnostics,LongHTTPPolicy
from backend.app.voice.realtime_receipt import occupy_once
from backend.app.voice.segment_plan import build as preview
from backend.app.voice.segmented_reading import SegmentedReading
from scripts.voice_segmented_lab import bootstrap
from scripts.voice_v3_fixture_probe import local_proxy

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'docs/VOICE_LONG_HTTP_DIAGNOSTIC_CANDIDATE.json'
FIXTURE='tests/fixtures/voice_long_http_stream_check_zh.txt'


def build():
    files={p.relative_to(ROOT).as_posix() for p in (ROOT/'backend/app/voice').glob('*.py')}
    files.update([FIXTURE,'scripts/voice_long_http_diagnostic.py','scripts/voice_segmented_lab.py',
        'scripts/voice_v3_fixture_probe.py','scripts/voice_provider_probe.py','backend/requirements.txt'])
    hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sorted(files)}
    source_hashes={name:hashlib.sha256(Path(inspect.getsourcefile(obj)).read_bytes()).hexdigest() for name,obj in
        [('httpx/_client.py',httpx.AsyncClient.send),
         ('httpcore/_async/http11.py',AsyncHTTP11Connection.handle_async_request),
         ('httpcore/_async/http_proxy.py',AsyncTunnelHTTPConnection.handle_async_request)]}
    p=preview((ROOT/FIXTURE).read_text(encoding='utf-8'))
    if p['requests']!=1 or p['characters']!=201:raise ValueError('new_diagnostic_sample_drift')
    return {'run_id':'voice-long-http-stream-check-20261118-r1','status':'awaiting_separate_approval',
        'code_files':hashes,'code_sha256':fingerprint(hashes),'dependency_sources':source_hashes,
        'dependency_versions':{n:importlib.metadata.version(n) for n in ('httpx','httpcore','h11')},
        'preview':p,'endpoint':'/v1/text-to-dialogue/stream','proxy':'http://127.0.0.1:7897',
        'voice_binding':{'expected_profile_revision':3,
            'expected_voice_sha256':'e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767',
            'expected_consent_version':'2026-11-08-v1'},
        'budget':{'jobs':1,'http':1,'characters':201,'max_audio_bytes':16777216,
            'timeouts':vars(LongHTTPPolicy()),'executor_cleanup_seconds':92,'job_seconds':100,
            'model':0,'oauth':0,'directory_get':0,'scribe':0,'ws':0,'retries':0,'redirects':0}}


def expected_approval(m,session_hash):
    return {'run_id':m['run_id'],'manifest_sha256':fingerprint(m),'code_sha256':m['code_sha256'],
        'session_sha256':session_hash,'database':str(ROOT/'data/yosaki.sqlite3'),
        'voice_binding':m['voice_binding'],'budget':m['budget'],'proxy':m['proxy'],
        'third_party_processing_confirmed':True,'billing_uncertainty_acknowledged':True,
        'provider_kind':'real_approved'}


async def execute(m,session_hash,receipt):
    if m!=build():raise ValueError('frozen_diagnostic_candidate_drift')
    parent=Path(os.environ['LOCALAPPDATA'])/'YOSAKI/voice-runs'/m['run_id']
    if receipt.resolve()!=parent/'approval.json' or receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size>16384:
        raise ValueError('approval_path_invalid')
    if json.loads(receipt.read_text(encoding='utf-8'))!=expected_approval(m,session_hash):
        raise ValueError('approval_binding_invalid')
    if local_proxy()!=m['proxy']:raise ValueError('proxy_drift')
    authority=VoiceAuthority(ROOT/'data/yosaki.sqlite3',session_hash,m['voice_binding'])
    authority.check(jobs=1,requests=1,characters=201)
    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    if not key or (parent/'result').exists():raise ValueError('preflight_or_existing_run')
    occupy_once(parent/'approval.json.used',{'run_id':m['run_id'],
        'manifest_sha256':fingerprint(m),'status':'occupied_outcome_unknown'})
    connect=bootstrap(parent/'isolated')
    def check():
        if m!=build() or local_proxy()!=m['proxy']:raise ValueError('frozen_binding_drift')
        current=authority.check()
        return {'voice_id':current['voice_id'],'revision':3}
    reserved=False
    def reserve(n,first):
        nonlocal reserved
        if reserved or n!=201 or not first:raise ValueError('single_request_limit')
        auto_budget.reserve(authority.write,session_hash,1,n,first_request=True)
        reserved=True
    reader=SegmentedReading(parent/'result',connect,m['preview']['source_text'],
        lambda d:LongHTTPAdapter(key,proxy_url=m['proxy'],diagnostics=d),check,reserve,
        request_timeout_seconds=92,diagnostics_factory=LongHTTPDiagnostics)
    await reader.start(m['preview']['preview_sha256'])
    try:
        async with asyncio.timeout(100):await reader.task
    except BaseException:
        await reader.cancel()
        raise
    return reader.result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--execute-real',action='store_true')
    parser.add_argument('--approval-file',type=Path)
    parser.add_argument('--session-hash-file',type=Path)
    args=parser.parse_args()
    try:
        if args.freeze:
            if args.execute_real:raise ValueError('exclusive_mode')
            m=build();MANIFEST.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'manifest_sha256':fingerprint(m),'code_sha256':m['code_sha256'],
                              'files':len(m['code_files']),'http':1,'characters':201}));return
        m=json.loads(MANIFEST.read_text(encoding='utf-8'))
        if m!=build():raise ValueError('frozen_candidate_drift')
        if not args.execute_real:
            print(json.dumps({'status':'verified_zero_dispatch','manifest_sha256':fingerprint(m),
                              'credentials_read':False}));return
        f=args.session_hash_file
        if not f or not args.approval_file or f.is_symlink() or not f.is_file() or f.stat().st_size>256:
            raise ValueError('session_binding_missing')
        session=f.read_text(encoding='utf-8').strip()
        if len(session)!=64 or any(c not in '0123456789abcdef' for c in session):raise ValueError('session_binding_invalid')
        result=asyncio.run(execute(m,session,args.approval_file))
        print(json.dumps({k:result[k] for k in ('status','http_attempts','characters','actual_billing')}))
    except Exception:
        print(json.dumps({'status':'stopped','reason':'inspect_preserved_safe_result',
                          'real_execute_requested':args.execute_real}))
        raise SystemExit(2)


if __name__=='__main__':main()
