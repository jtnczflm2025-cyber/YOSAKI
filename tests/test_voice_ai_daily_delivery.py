"""Daily delivery changes only: synthetic product routes, no providers."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.voice_ai_chat_candidate_fixture import Fixture
from backend.app.voice.performance_preferences import CANDIDATE


class DailyDelivery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-daily-expression-')
        self.f=Fixture(Path(self.temp.name))

    def tearDown(self):
        self.f.close();self.temp.cleanup()

    def enable(self,revision=0,enabled=True):
        return self.f.client.put('/api/voice/ai-http/settings',json={
            'enabled':enabled,'model_profile_id':self.f.model_id,
            'expected_revision':revision,'confirm_planning':enabled})

    def generate(self):
        self.assertEqual(self.enable().status_code,200)
        message=self.f.chat()['message_id']
        response=self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={})
        self.assertEqual(response.status_code,200,response.text)
        identity=response.json()['id']
        for _ in range(200):
            value=self.f.client.get('/api/voice/ai-http/plans/'+identity).json()
            if value['bridge_state'] not in ('planning','synthesizing'):break
            time.sleep(.01)
        self.assertEqual(value['bridge_state'],'completed',value)
        return message,value

    def test_daily_preferences_default_off_no_user_setting_write(self):
        settings=self.f.client.get('/api/voice/ai-http/settings').json()
        self.assertFalse(settings['enabled']);self.assertIsNone(settings['model_profile_id'])
        self.assertEqual(settings['performance_preferences'],CANDIDATE)
        with self.f.main.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM voice_ai_owner_settings').fetchone()[0],0)
        self.assertEqual(self.f.counters()['planning'],0)

    def test_opt_in_request_gets_advisory_preferences_once(self):
        message,value=self.generate()
        payload=json.loads(self.f.plan_requests[0]['input'][0]['content'])
        self.assertEqual(payload['performance_preferences'],CANDIDATE)
        self.assertEqual(value['performance_preferences'],CANDIDATE)
        self.assertEqual(self.f.client.get(f'/api/voice/ai-http/messages/{message}/state').json()['plan']['id'],value['id'])
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,1))
        self.assertEqual(self.enable(0).status_code,409)

    def test_off_keeps_read_only_saved_mapping_audio_without_dispatch(self):
        message,value=self.generate()
        self.assertEqual(self.enable(1,False).status_code,200)
        state=self.f.client.get(f'/api/voice/ai-http/messages/{message}/state').json()
        self.assertFalse(state['eligible'])
        before=self.f.counters()
        for _ in range(2):
            response=self.f.client.get(f'/api/voice/ai-http/messages/{message}/saved-audio')
            self.assertEqual(response.status_code,200,response.text)
            saved=response.json()['plan']
            self.assertTrue(saved['cache_read_only'])
            self.assertEqual(saved['preview']['request_text'],value['preview']['request_text'])
            self.assertEqual(saved['versions'],value['versions'])
            self.assertEqual(self.f.client.get(saved['audio'][0]['url']).status_code,200)
        self.assertEqual(self.f.counters(),before)
        self.assertEqual(self.f.client.post(f'/api/voice/ai-http/messages/{message}/read',json={}).status_code,403)
        with self.f.main.connect() as db:db.execute('UPDATE voice_profiles SET revision=revision+1')
        self.assertIsNone(self.f.client.get(f'/api/voice/ai-http/messages/{message}/saved-audio').json()['plan'])
        self.assertEqual(self.f.client.get(value['audio'][0]['url']).status_code,409)

    def test_once_only_fallback_audio_is_also_readable_after_disabling(self):
        self.f.scenario='failed'
        message,value=self.generate()
        self.assertEqual(value['state'],'fallback')
        self.assertEqual(self.enable(1,False).status_code,200)
        response=self.f.client.get(f'/api/voice/ai-http/messages/{message}/saved-audio')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['plan']['preview']['request_text'],value['preview']['request_text'])
        self.assertEqual((self.f.chat_count,self.f.plan_count,self.f.tts_count),(1,1,1))


if __name__=='__main__':unittest.main()
