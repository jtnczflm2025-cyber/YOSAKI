"""Freeze one multi-sentence product batch; no credentials, DB, or network.

Uses the prior explicitly reviewed mandatory system context. The next actual
product preflight must reconstruct the same request or stop, never rewrite it.
"""
import argparse
from copy import deepcopy
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

from backend.app.voice.annotation_planner import Source, prepare, fingerprint, INSTRUCTION
from backend.app.voice.performance_preferences import CANDIDATE, GUIDANCE
from scripts.voice_ai_multiposition_fixture import BODY, CHAT_INPUT

ROOT=Path(__file__).resolve().parents[1]
RUN_ID='voice-ai-multiposition-product-20261124-r1'
EXTRA=('backend/app/voice/performance_preferences.py','scripts/voice_ai_multiposition_fixture.py',
       'scripts/voice_ai_multiposition_candidate.py','tests/test_voice_ai_multiposition_product.py')


def build(previous:dict,context:dict)->dict:
    value=deepcopy(previous)
    value.update(run_id=RUN_ID,status='awaiting_separate_approval',chat_input=CHAT_INPUT,
        expected_public_text=BODY,performance_preferences=deepcopy(CANDIDATE),
        no_approved_receipt_created=True,previous_batch_finished=True)
    messages=deepcopy(context['messages']);assert messages[-1]['role']=='user'
    messages[-1]['content']=CHAT_INPUT
    body=deepcopy(context['body']);assert body['model']=='gpt-6.1-sol' and body['max_output_tokens']==2048
    body['input']=[{'role':'user','content':CHAT_INPUT}]
    value.update(context=messages,chat_body=body,chat_body_sha256=fingerprint(body))
    value['budgets'].update(oauth=0,directory_get=0)
    value['account_requests_if_separately_approved']={'same_account_oauth_max':1,'directory_get':0,
        'only_if_credential_under_90_seconds':True,'retries':0,'not_yet_authorized':True}
    # These fragment IDs depend on safe text/offsets, not fabricated saved IDs.
    template=prepare(Source(1,1,0,0,BODY,personality=previous['planning']['personality']))
    model_input=deepcopy(template['model_input']);model_input.pop('instruction')
    model_input.update(performance_preferences=deepcopy(CANDIDATE),granularity='auto',
        input_sha256='<actual_saved_message_and_generation_bound_input_sha256>')
    value['planning'].update(system_instruction=INSTRUCTION+GUIDANCE,
        performance_preferences=deepcopy(CANDIDATE),fragments=template['fragments'],
        full_input_source='complete display-approved fixed reply; trusted fragment IDs; independent performance preferences; no baseline')
    value['planning']['request_body_template']={'model':'gpt-6.1-sol','store':False,'stream':True,
        'max_output_tokens':512,'instructions':INSTRUCTION+GUIDANCE,
        'input':[{'role':'user','content':json.dumps(model_input,ensure_ascii=False)}]}
    value['runtime_qualification']+=['public_reply_exactly_matches_frozen_multisentence_text',
        'performance_preferences_exactly_match_frozen_candidate','annotations_may_be_empty_no_replanning']
    value['temporary_settings']='isolated process explicit model5 and frozen performance preferences only; persistent user settings unchanged'
    value['billing']={'actual_fees':'unknown','budgets_are_call_limits_not_invoice_cap':True,
        'old_usd_0_103_reference_not_reused_for_changed_input':True}
    names=sorted(set(previous['code_files'])|set(EXTRA))
    value['code_files']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in names}
    value['code_sha256']=fingerprint(value['code_files'])
    value['changes_from_previous_files']=[p for p,h in previous['code_files'].items() if value['code_files'][p]!=h]
    value['new_files']=list(EXTRA)
    value['dependencies']={k:version(k) for k in previous['dependencies']}
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path,required=True)
    p.add_argument('--context',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    value=build(json.loads(args.previous.read_text(encoding='utf-8')),json.loads(args.context.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'run_id':RUN_ID,'manifest_sha256':fingerprint(value),
        'code_sha256':value['code_sha256'],'code_files':len(value['code_files']),
        'source_characters':len(BODY),'http_budget':1,'tts_character_budget':120,
        'real_requests':0,'real_receipt_created':False}))
