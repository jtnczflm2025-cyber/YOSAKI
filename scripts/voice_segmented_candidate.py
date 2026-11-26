"""Pure freeze builder; never opens a database or loads a credential."""
import hashlib
import json
from pathlib import Path
from backend.app.voice.annotation_planner import fingerprint
from backend.app.voice.segment_plan import build as preview

ROOT=Path(__file__).resolve().parents[1]
FIXTURE='tests/fixtures/voice_step3_two_request_zh.txt'

def build():
    paths={p.relative_to(ROOT).as_posix() for p in (ROOT/'backend/app/voice').glob('*.py')}
    paths.update(['scripts/voice_segmented_candidate.py','scripts/voice_segmented_listen.py',
        'scripts/voice_segmented_lab.py','scripts/voice_v3_fixture_probe.py',
        'scripts/voice_provider_probe.py',FIXTURE,'backend/requirements.txt','package-lock.json',
        'frontend/package.json','frontend/vite.segmented-lab.config.ts',
        'frontend/voice-segmented-lab.html','frontend/src/voice/SegmentedLab.vue',
        'frontend/src/voice/segmentedLabMain.ts','frontend/src/voice/voiceAudioPlayer.ts'])
    paths.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'frontend/src/ui').rglob('*') if p.is_file())
    files={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sorted(paths)}
    p=preview((ROOT/FIXTURE).read_text(encoding='utf-8'))
    if p['requests']!=2 or p['characters']!=2130 or any(s['insertions'] for s in p['segments']):
        raise ValueError('frozen_segment_plan_drift')
    return {'run_id':'voice-long-step3-20261118-r1','status':'awaiting_separate_approval',
        'code_files':files,'code_sha256':fingerprint(files),'preview':p,
        'voice_binding':{'expected_profile_revision':3,
            'expected_voice_sha256':'e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767',
            'expected_consent_version':'2026-11-08-v1'},
        'budget':{'model':0,'oauth':0,'directory_get':0,'scribe':0,'ws':0,'http':2,
            'characters':2130,'jobs':1,'per_http_seconds':30,'per_http_audio_bytes':16777216,
            'job_seconds':260,'retries':0},
        'requests':[{'method':'POST','path':'/v1/text-to-dialogue',
            'query':{'output_format':'mp3_44100_128'},
            'body':{'model_id':'eleven_v4','language_code':'zh',
                'inputs':[{'text':s['text'],'voice_id':'resolved_from_bound_profile'}]},
            'characters':s['characters']} for s in p['segments']]}

if __name__=='__main__':
    value=build()
    (ROOT/'docs/VOICE_STEP3_LISTENING_CANDIDATE.json').write_text(
        json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'manifest_sha256':fingerprint(value),'code_sha256':value['code_sha256'],
                      'files':len(value['code_files']),'http':2,'characters':2130}))
