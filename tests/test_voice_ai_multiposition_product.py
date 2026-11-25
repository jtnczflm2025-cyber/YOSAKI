import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from backend.app.voice.annotation_planner import AnnotationError, Source, prepare, render
from backend.app.voice.performance_preferences import CANDIDATE, apply
from scripts.voice_ai_multiposition_fixture import BODY, CHAT_INPUT, MultiFixture


class Preferences(unittest.TestCase):
    def test_candidate_builder_freezes_new_context_without_mutating_previous(self):
        from scripts.voice_ai_multiposition_candidate import build
        original={'planning':{'personality':''},'budgets':{'chat':1,'planning':1,'http_tts':1,
            'tts_characters_including_tags':120},'runtime_qualification':[],
            'code_files':{},'dependencies':{}}
        context={'messages':[{'role':'system','content':'角色规则保持原值'},
            {'role':'user','content':'旧输入'}],'body':{'model':'gpt-6.1-sol',
            'instructions':'角色规则保持原值','input':[{'role':'user','content':'旧输入'}],
            'max_output_tokens':2048,'stream':True,'store':False}}
        before=copy.deepcopy(original)
        manifest=build(original,context)
        self.assertEqual(original,before)
        self.assertEqual(manifest['chat_body']['instructions'],context['body']['instructions'])
        self.assertEqual(manifest['chat_body']['input'][0]['content'],CHAT_INPUT)
        self.assertEqual(manifest['expected_public_text'],BODY)
        self.assertEqual(manifest['planning']['performance_preferences'],CANDIDATE)
        self.assertEqual(manifest['budgets']['http_tts'],1)
        self.assertEqual(manifest['budgets']['tts_characters_including_tags'],120)

    def test_off_preserves_existing_hash_and_preferences_never_force_tags(self):
        old=prepare(Source(1,1,1,1,BODY,personality='旧声音设定'))
        original=copy.deepcopy(old)
        self.assertIs(apply(old,None),old)
        active=apply(old,CANDIDATE)
        self.assertEqual(old,original)
        self.assertNotEqual(active['input_sha256'],old['input_sha256'])
        self.assertEqual(active['source']['personality'],'旧声音设定')
        self.assertEqual(active['model_input']['allowed_intents'],old['model_input']['allowed_intents'])
        self.assertEqual(render(active,[],1)['request_text'],BODY)
        self.assertIsNone(render(active,[],1)['baseline_tag'])
        self.assertIn('different fragments',active['model_input']['instruction'])

    def test_version_fields_and_forced_baseline_are_strict(self):
        old=prepare(Source(1,1,1,1,BODY))
        for bad in ({**CANDIDATE,'version':'unknown'},{**CANDIDATE,'force_baseline':True},
                    {**CANDIDATE,'allow_empty_annotations':False},{**CANDIDATE,'extra':1},
                    {**CANDIDATE,'delivery_target':''}):
            with self.subTest(bad=bad),self.assertRaises(AnnotationError):apply(old,bad)
        changed=apply(old,{**CANDIDATE,'delivery_target':'更克制，保留留白。'})
        self.assertNotEqual(changed['input_sha256'],apply(old,CANDIDATE)['input_sha256'])


class MultiProduct(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-multi-product-')
        self.f=MultiFixture(Path(self.temp.name))
        self.f.enable()

    def tearDown(self):
        self.f.close();self.temp.cleanup()

    def wait(self,identity):
        for _ in range(200):
            r=self.f.client.get('/api/voice/ai-http/plans/'+identity)
            self.assertEqual(r.status_code,200,r.text)
            value=r.json()
            if value['bridge_state'] not in ('planning','synthesizing'):return value
            time.sleep(.01)
        self.fail('pipeline did not finish')

    def start(self,message):
        r=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={})
        self.assertEqual(r.status_code,200,r.text)
        return self.wait(r.json()['id'])

    def test_real_decode_receipt_sparse_distinct_intents_and_single_http(self):
        source=self.f.chat();value=self.start(source['message_id'])
        self.assertEqual(value['bridge_state'],'completed',value)
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,1))
        self.assertEqual(self.f.plan_requests[0]['max_output_tokens'],512)
        model=json.loads(self.f.plan_requests[0]['input'][0]['content'])
        self.assertEqual(model['performance_preferences'],CANDIDATE)
        self.assertEqual(model['personality'],'')
        sentences=[f for f in value['fragments'] if f['kind']=='sentence']
        self.assertEqual(len(sentences),5)
        marks=value['versions'][0]['annotations']
        self.assertEqual([a['intent'] for a in marks],['resigned','thoughtful'])
        self.assertEqual([i['offset'] for i in value['preview']['insertions']],[sentences[0]['start'],sentences[2]['start']])
        text=value['preview']['request_text']
        self.assertEqual(text.replace('[sighs] ','').replace('[thoughtful] ',''),BODY)
        self.assertEqual(self.f.tts_requests[0]['inputs'][0]['text'],text)
        self.assertEqual(value['preview']['request_characters'],len(text))
        self.assertEqual(value['preview']['http_requests'],1)
        self.assertEqual(value['intent_tags']['restrained'],'[thoughtful] ')
        with self.f.main.connect() as db:
            proof=db.execute('SELECT generation_id FROM voice_ai_completion_receipts WHERE message_id=?',(source['message_id'],)).fetchone()
            self.assertEqual(proof[0],source['generation_id'])

    def test_edit_blank_remove_all_versions_and_cache_do_not_replan(self):
        message=self.f.chat()['message_id'];first=self.start(message);identity=first['id']
        url1=first['audio'][0]['url']
        sentences=[f for f in first['fragments'] if f['kind']=='sentence']
        new=[{'fragment_id':sentences[1]['fragment_id'],'intent':'restrained'},
             {'fragment_id':sentences[3]['fragment_id'],'intent':'resigned'}]
        r=self.f.client.patch('/api/voice/ai-http/plans/'+identity,json={'expected_revision':1,'annotations':new})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))
        expired=self.f.client.patch('/api/voice/ai-http/plans/'+identity,json={'expected_revision':1,'annotations':[]})
        self.assertEqual(expired.status_code,409)
        second=self.start(message)
        self.assertEqual(second['revision'],2)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,2))
        self.assertNotEqual(second['preview']['preview_sha256'],first['preview']['preview_sha256'])
        self.assertEqual(self.f.client.get(url1).status_code,200)
        self.assertEqual(self.f.client.patch('/api/voice/ai-http/plans/'+identity,json={'expected_revision':2,'annotations':[]}).status_code,200)
        third=self.start(message)
        self.assertEqual(third['preview']['request_text'],BODY)
        self.assertEqual(third['revision'],3)
        for _ in range(2):
            self.assertEqual(self.f.client.get(f'/api/voice/ai-http/messages/{message}/state').json()['plan']['revision'],3)
            for cached in third['cached_versions']:
                self.assertEqual(self.f.client.get(cached['url']).status_code,200)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,3))

    def test_empty_plan_remains_untagged_and_invalid_fragment_edit_is_rejected(self):
        self.f.annotation_selector=lambda content:[]
        message=self.f.chat()['message_id'];first=self.start(message)
        self.assertEqual(first['state'],'empty')
        self.assertEqual(first['preview']['request_text'],BODY)
        r=self.f.client.patch('/api/voice/ai-http/plans/'+first['id'],json={
            'expected_revision':1,'annotations':[{'fragment_id':'invented','intent':'thoughtful'}]})
        self.assertEqual(r.status_code,422)
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))

    def test_preferences_change_new_input_old_audio_still_accessible(self):
        message=self.f.chat()['message_id'];first=self.start(message)
        original=first['audio'][0]['url']
        self.f.runtime.plans.performance_preferences={**CANDIDATE,'delivery_target':'更克制，保留留白。'}
        state=self.f.client.get(f'/api/voice/ai-http/messages/{message}/state').json()
        self.assertIsNone(state['plan'])
        self.assertEqual(self.f.client.get(original).status_code,200)
        second=self.start(message)
        self.assertNotEqual(second['id'],first['id'])
        self.assertEqual(self.f.client.get(original).status_code,200)

    def test_cancel_with_late_audio_does_not_publish_or_dispatch_again(self):
        message=self.f.chat()['message_id'];self.f.scenario='late_tts'
        response=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={})
        identity=response.json()['id'];self.assertTrue(self.f.tts_entered.wait(2))
        self.assertEqual(self.f.client.delete('/api/voice/ai-http/plans/'+identity).status_code,200)
        (self.f.root/'release-audio').touch()
        value=self.wait(identity)
        self.assertEqual(value['bridge_state'],'cancelled')
        self.assertEqual(value['cached_versions'],[])
        self.assertEqual((self.f.plan_count,self.f.tts_count),(1,1))

    def guarded_batch(self):
        from scripts.voice_ai_product_listening import BatchGate,attach,freeze_context,digest
        import httpx
        context=freeze_context(self.f.main,CHAT_INPUT)
        profile=dict(self.f.main._voice_ai_profile(5))
        fields=('id','protocol','model_name','enabled','max_tokens','updated_at','reasoning_effort')
        directory=Path(self.temp.name)/'one-batch';directory.mkdir()
        manifest={'run_id':'synthetic-multiposition','owner_id':1,'character_id':1,'code_sha256':'synthetic',
            'model':{k:profile.get(k) for k in fields},'model_endpoint':context['url'],
            'chat_body_sha256':digest(context['body']),'chat_input':CHAT_INPUT,'expected_public_text':BODY,
            'performance_preferences':CANDIDATE,'input_byte_limits':{'chat':10000,'planning':24000},
            'tts_endpoint':'https://api.elevenlabs.io/v1/text-to-dialogue?output_format=mp3_44100_128',
            'voice':{'revision':3,'voice_id_sha256':hashlib.sha256(b'synthetic-voice').hexdigest(),
                     'notice_version':'2026-11-08-v1'}}
        (directory/'approval.json').write_text(json.dumps({'run_id':manifest['run_id'],
            'manifest_sha256':digest(manifest),'code_sha256':'synthetic','mock_only':True}))
        gate=BatchGate(manifest,directory,mock_only=True)
        restore=attach(self.f.main,gate,transport_factory=lambda:httpx.MockTransport(self.f.handle),base_client=self.f.original_client)
        conv=self.f.client.post('/api/conversations',json={'character_id':1,'title':'fixed sample'}).json()['id']
        gate.bind_conversation(conv)
        return directory,conv,restore

    def test_final_batch_guard_uses_fixed_new_input_and_same_product_path(self):
        directory,conv,restore=self.guarded_batch()
        try:
            response=self.f.client.post('/api/chat/stream',json={'character_id':1,'conversation_id':conv,
                'model_profile_id':5,'message':CHAT_INPUT})
            self.assertEqual(response.status_code,200,response.text)
            with self.f.main.connect() as db:
                message=db.execute('SELECT message_id FROM voice_ai_completion_receipts WHERE conversation_id=?',(conv,)).fetchone()[0]
            value=self.start(message)
            self.assertEqual(value['bridge_state'],'completed',value)
            self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,1))
            self.assertLessEqual(value['preview']['request_characters'],120)
            self.assertEqual(json.loads((directory/'chat-attempt.json').read_text())['max_output_tokens'],2048)
            self.assertEqual(json.loads((directory/'planning-attempt.json').read_text())['max_output_tokens'],512)
        finally:restore()

    def test_final_batch_wrong_reply_stops_before_planning_and_tts(self):
        directory,conv,restore=self.guarded_batch()
        self.f.public_reply='違う内容だ。'
        try:
            self.f.client.post('/api/chat/stream',json={'character_id':1,'conversation_id':conv,
                'model_profile_id':5,'message':CHAT_INPUT})
            with self.f.main.connect() as db:
                message=db.execute('SELECT message_id FROM voice_ai_completion_receipts WHERE conversation_id=?',(conv,)).fetchone()[0]
            self.start(message)
            self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,0,0))
            self.assertFalse((directory/'planning-attempt.json').exists())
            self.assertFalse((directory/'tts-attempt.json').exists())
        finally:restore()


if __name__=='__main__':unittest.main()
