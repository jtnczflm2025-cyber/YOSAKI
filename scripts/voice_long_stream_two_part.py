"""Frozen two-part HTTP /stream reading. Pure verification unless explicitly approved.

No daily installation, credential refresh, model/catalog/STT/WS or retry path.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from backend.app.voice import auto_budget
from backend.app.voice.annotation_listening import VoiceAuthority
from backend.app.voice.annotation_planner import fingerprint
from backend.app.voice.expression_fixture import _write_json
from backend.app.voice.long_http_adapter import LongHTTPAdapter,LongHTTPDiagnostics
from backend.app.voice.realtime_receipt import occupy_once
from backend.app.voice.segment_plan import build as preview
from backend.app.voice.segmented_reading import SegmentedReading
from scripts.voice_long_http_diagnostic import build as reception_baseline,ROOT
from scripts.voice_segmented_lab import bootstrap
from scripts.voice_segmented_listen import review_app
from scripts.voice_v3_fixture_probe import local_proxy

MANIFEST=ROOT/'docs/VOICE_LONG_STREAM_TWO_PART_CANDIDATE.json'
FIXTURE='tests/fixtures/voice_long_stream_two_part_zh.txt'


def build():
    baseline=reception_baseline()
    files=dict(baseline['code_files'])
    for p in (FIXTURE,'scripts/voice_long_stream_two_part.py','scripts/voice_segmented_listen.py',
              'scripts/voice_segmented_candidate.py'):
        files[p]=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
    files=dict(sorted(files.items()))
    p=preview((ROOT/FIXTURE).read_text(encoding='utf-8'))
    if p['requests']!=2 or [s['characters'] for s in p['segments']]!=[1036,1052] or \
            p['characters']!=2088 or any(s['insertions'] for s in p['segments']):
        raise ValueError('two_part_frozen_preview_drift')
    return {'run_id':'voice-long-stream-two-part-20261118-r1',
        'status':'awaiting_separate_approval','code_files':files,'code_sha256':fingerprint(files),
        'dependency_versions':baseline['dependency_versions'],'dependency_sources':baseline['dependency_sources'],
        'endpoint':baseline['endpoint'],'proxy':baseline['proxy'],'voice_binding':baseline['voice_binding'],
        'preview':p,'budget':{**baseline['budget'],'http':2,'characters':2088,'job_seconds':220,
                             'aggregate_max_audio_bytes':33554432},
        'requests':[{'sequence':s['sequence'],'method':'POST',
            'endpoint':baseline['endpoint'],'query':{'output_format':'mp3_44100_128'},
            'body':{'model_id':'eleven_v4','language_code':'zh',
                    'inputs':[{'text':s['text'],'voice_id':'resolved_from_bound_profile'}]},
            'characters':s['characters'],'text_sha256':s['text_sha256']} for s in p['segments']]}


def expected_approval(m,session_hash):
    return {'run_id':m['run_id'],'manifest_sha256':fingerprint(m),'code_sha256':m['code_sha256'],
        'dependency_versions':m['dependency_versions'],'dependency_sources':m['dependency_sources'],
        'session_sha256':session_hash,'database':str(ROOT/'data/yosaki.sqlite3'),
        'voice_binding':m['voice_binding'],'budget':m['budget'],'proxy':m['proxy'],
        'third_party_processing_confirmed':True,'billing_uncertainty_acknowledged':True,
        'provider_kind':'real_approved'}


async def execute(m,session_hash,receipt):
    if m!=build():raise ValueError('frozen_two_part_candidate_drift')
    parent=Path(os.environ['LOCALAPPDATA'])/'YOSAKI/voice-runs'/m['run_id']
    if receipt.resolve()!=parent/'approval.json' or receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size>16384:
        raise ValueError('approval_path_invalid')
    if json.loads(receipt.read_text(encoding='utf-8'))!=expected_approval(m,session_hash):
        raise ValueError('approval_binding_invalid')
    if local_proxy()!=m['proxy']:raise ValueError('proxy_drift')
    authority=VoiceAuthority(ROOT/'data/yosaki.sqlite3',session_hash,m['voice_binding'])
    authority.check(jobs=1,requests=2,characters=2088)
    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    if not key or (parent/'result').exists():raise ValueError('preflight_or_existing_run')
    occupy_once(parent/'approval.json.used',{'run_id':m['run_id'],
        'manifest_sha256':fingerprint(m),'status':'occupied_outcome_unknown'})
    connect=bootstrap(parent/'isolated')
    def check():
        if m!=build() or local_proxy()!=m['proxy']:raise ValueError('frozen_binding_drift')
        current=authority.check()
        return {'voice_id':current['voice_id'],'revision':3}
    reserved=0
    def reserve(n,first):
        nonlocal reserved
        if reserved>=2 or n!=m['requests'][reserved]['characters'] or first!=(reserved==0):
            raise ValueError('two_part_dispatch_budget')
        auto_budget.reserve(authority.write,session_hash,1,n,first_request=first)
        reserved+=1
    reader=SegmentedReading(parent/'result',connect,m['preview']['source_text'],
        lambda d:LongHTTPAdapter(key,proxy_url=m['proxy'],diagnostics=d),check,reserve,
        request_timeout_seconds=92,diagnostics_factory=LongHTTPDiagnostics)
    deadline=None
    try:
        async with asyncio.timeout(220) as deadline:
            await reader.start(m['preview']['preview_sha256'])
            await reader.task
    except TimeoutError:
        if not deadline or not deadline.expired():raise
    except BaseException:
        await reader.cancel()
        raise
    if deadline and deadline.expired():
        await reader.cancel()
        reader.result.update(status='unknown',error_code='two_part_job_hard_deadline')
        reader.save()
    return reader.result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--execute-real',action='store_true')
    parser.add_argument('--review-only',action='store_true')
    parser.add_argument('--approval-file',type=Path)
    parser.add_argument('--session-hash-file',type=Path)
    args=parser.parse_args()
    try:
        if sum((args.freeze,args.execute_real,args.review_only))>1:raise ValueError('exclusive_modes')
        if args.freeze:
            m=build();_write_json(MANIFEST,m)
            print(json.dumps({'manifest_sha256':fingerprint(m),'code_sha256':m['code_sha256'],
                              'files':len(m['code_files']),'http':2,'characters':2088}));return
        m=json.loads(MANIFEST.read_text(encoding='utf-8'))
        if m!=build():raise ValueError('frozen_candidate_drift')
        if args.review_only:
            import uvicorn
            parent=Path(os.environ['LOCALAPPDATA'])/'YOSAKI/voice-runs'/m['run_id']/'result'
            uvicorn.run(review_app(parent),host='127.0.0.1',port=18895,workers=1,access_log=False)
            return
        if not args.execute_real:
            print(json.dumps({'status':'verified_zero_dispatch','manifest_sha256':fingerprint(m),
                              'credentials_read':False,'http':2,'characters':2088}));return
        f=args.session_hash_file
        if not f or not args.approval_file or f.is_symlink() or not f.is_file() or f.stat().st_size>256:
            raise ValueError('session_binding_missing')
        session=f.read_text(encoding='utf-8').strip()
        if len(session)!=64 or any(c not in '0123456789abcdef' for c in session):raise ValueError('session_binding_invalid')
        r=asyncio.run(execute(m,session,args.approval_file))
        print(json.dumps({k:r[k] for k in ('status','http_attempts','characters','actual_billing')}))
    except Exception:
        print(json.dumps({'status':'stopped','reason':'inspect_preserved_safe_result',
                          'real_execute_requested':args.execute_real}))
        raise SystemExit(2)


if __name__=='__main__':main()
