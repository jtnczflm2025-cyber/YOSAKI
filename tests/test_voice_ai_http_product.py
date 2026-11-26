"""New Step 4 wiring only: saved reply/display proof -> raw fake SSE -> HTTP/cache.

All databases, credentials and responses are synthetic. No main import or network.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.app.voice import audio_cache, auto_budget, manager
from backend.app.voice.annotation_http_product import HTTPReplyBridge
from backend.app.voice.annotation_lab import EditRequest
from backend.app.voice.annotation_planner import AnnotationError
from backend.app.voice.annotation_store import AnnotationStore
from scripts.voice_ai_http_lab import BODY, Fixture, MP3

P = '/api/voice-ai-http-lab'


class Product(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='yosaki-ai-http-product-')
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.fixture = Fixture(Path(self.temp.name), enabled=True)
        self.bridge = self.fixture.bridge
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.fixture.app), base_url='http://isolated')
        await self.client.get(P + '/session')

    async def asyncTearDown(self):
        await self.bridge.close()
        await self.client.aclose()
        self.env.stop()
        self.temp.cleanup()

    async def payload(self, mode='ai_expression', operation='once'):
        response = await self.client.get(P + '/messages/501/source', params={'mode': mode})
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()
        return dict(operation_id=operation, input_sha256=source['input_sha256'], profile_revision=3,
                    planning_consent=mode=='ai_expression', mode=mode,
                    model_profile_id=9 if mode=='ai_expression' else None)

    async def start(self, mode='ai_expression', operation='once'):
        response = await self.client.post(P + '/messages/501/read', json=await self.payload(mode, operation))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    async def finish(self, identity):
        task = self.bridge.tasks.get(identity)
        if task:
            await task
        response = await self.client.get(P + '/plans/' + identity)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_complete_reply_raw_model_sse_to_existing_http_and_cached_playback(self):
        identity = await self.start()
        result = await self.finish(identity)
        self.assertEqual(result['bridge_state'], 'completed')
        self.assertEqual(result['preview']['http_requests'], 1)
        self.assertEqual(result['preview']['request_text'], '[thoughtful] ' + BODY)
        self.assertEqual(result['preview']['request_characters'], len('[thoughtful] ' + BODY))
        model, tts = self.fixture.model_requests[0], self.fixture.tts_requests[0]
        self.assertEqual((len(self.fixture.model_requests), len(self.fixture.tts_requests)), (1, 1))
        self.assertEqual(model['max_output_tokens'], 512)
        self.assertEqual(tts, {'model_id':'eleven_v4','inputs':[{'text':'[thoughtful] ' + BODY,
                            'voice_id':'synthetic-voice'}], 'language_code':'ja'})
        for _ in range(2):
            audio = await self.client.get(result['audio'][0]['url'])
            self.assertEqual(audio.headers['content-type'], 'audio/mpeg')
            self.assertEqual(audio.content, MP3)
        await self.start(operation='refresh')
        await self.finish(identity)
        self.assertEqual((len(self.fixture.model_requests), len(self.fixture.tts_requests)), (1, 1))
        daily = auto_budget.usage(self.fixture.connect, 'synthetic-session', 1)['daily']['used']
        self.assertEqual(daily, {'jobs':1,'requests':1,'characters':len('[thoughtful] ' + BODY)})
        with self.fixture.connect() as db:
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=501').fetchone()[0], BODY)

    async def test_default_off_and_no_real_transport_or_client_body_bypass(self):
        self.bridge.plans.enabled = False
        response = await self.client.get(P + '/messages/501/source')
        self.assertEqual(response.status_code, 403)
        self.bridge.plans.enabled = True
        data = await self.payload()
        response = await self.client.post(P + '/messages/501/read', json={**data, 'body':'forged'})
        self.assertEqual(response.status_code, 422)
        response = await self.client.post(P + '/messages/501/read', json={**data, 'model_profile_id':10})
        self.assertEqual(response.status_code, 409)
        with self.assertRaises(ValueError):
            HTTPReplyBridge(Path(self.temp.name)/'other', self.bridge.plans.repository,
                self.bridge.plans.model, None, lambda *a:None, enabled=True)
        self.assertEqual(self.fixture.model_requests, [])
        self.assertEqual(self.fixture.tts_requests, [])

    async def test_full_completion_display_binding_before_any_planning(self):
        original = dict(self.fixture.proof)
        for field, value in [('display_action','hide'), ('model_completed',False), ('stream_ended',False),
                             ('message_id',999), ('body_sha256','bad'), ('generation_id','')]:
            self.fixture.proof = {**original, field:value}
            response = await self.client.get(P+'/messages/501/source', params={'mode':'ai_expression'})
            self.assertEqual(response.status_code, 409, field)
        self.fixture.proof = original
        with self.fixture.connect() as db:
            db.execute("UPDATE messages SET generation_status='generating' WHERE id=501")
        self.assertEqual((await self.client.get(P+'/messages/501/source')).status_code, 409)
        self.assertEqual(self.fixture.model_requests, [])
        self.assertEqual(self.fixture.tts_requests, [])

    async def test_manual_choice_has_priority_and_does_not_resolve_model(self):
        self.fixture.profile['enabled'] = 0
        identity = await self.start('thoughtful')
        result = await self.finish(identity)
        self.assertEqual(result['bridge_state'], 'completed')
        self.assertEqual(result['preview']['effective_mode'], 'thoughtful')
        self.assertEqual(len(self.fixture.model_requests), 0)
        identity = await self.start('none', 'plain')
        plain = await self.finish(identity)
        self.assertEqual(plain['preview']['request_text'], BODY)
        self.assertEqual(len(self.fixture.tts_requests), 2)

    async def test_invalid_advice_known_failure_fallback_once_but_unknown_stops(self):
        for scenario, state, tts_calls in [('invalid','completed',1), ('model_failed','completed',1),
                                           ('model_unknown','failed_unknown',0), ('empty','completed',1)]:
            with tempfile.TemporaryDirectory(prefix='yosaki-ai-fallback-') as temp:
                fixture = Fixture(Path(temp), enabled=True)
                fixture.scenario = scenario
                bridge = fixture.bridge
                try:
                    prepared = bridge.source(501, 1, 'ai_expression')
                    from backend.app.voice.annotation_lab import PlanRequest
                    payload = PlanRequest(operation_id='same', input_sha256=prepared['input_sha256'],
                        profile_revision=3, planning_consent=True, model_profile_id=9)
                    identity = bridge.start(501, 1, payload)['id']
                    await bridge.tasks[identity]
                    result = bridge.status(identity, 1)
                    self.assertEqual(result['bridge_state'], state, scenario)
                    self.assertEqual(len(fixture.model_requests), 1)
                    self.assertEqual(len(fixture.tts_requests), tts_calls)
                    if tts_calls:
                        self.assertEqual(result['preview']['request_text'], BODY)
                    bridge.start(501, 1, payload)
                    if identity in bridge.tasks:
                        await bridge.tasks[identity]
                    self.assertEqual(len(fixture.tts_requests), tts_calls)
                finally:
                    await bridge.close()

    async def test_concurrent_repeat_and_restart_do_not_replan_or_resynthesize(self):
        self.fixture.model_hold = asyncio.Event()
        payload = await self.payload()
        replies = await asyncio.gather(*(self.client.post(P+'/messages/501/read', json=payload) for _ in range(4)))
        self.assertTrue(all(r.status_code==200 for r in replies))
        identities = {r.json()['id'] for r in replies}
        self.assertEqual(len(identities), 1)
        await self.fixture.model_entered.wait()
        self.fixture.model_hold.set()
        identity = identities.pop()
        result = await self.finish(identity)
        # Reopen persistent store, as a single-worker restart; no claim is released.
        self.bridge.plans.store = AnnotationStore(self.bridge.plans.store.path)
        self.bridge.plans.store.recover()
        await self.start(operation='restart')
        await self.finish(identity)
        self.assertEqual((len(self.fixture.model_requests),len(self.fixture.tts_requests)),(1,1))
        self.assertEqual((await self.client.get(result['audio'][0]['url'])).content, MP3)

    async def test_old_binding_and_expression_versions_remain_accessible(self):
        context = manager.speech_context(self.fixture.connect, 501, 1)
        state, cache_id, attempt = audio_cache.claim(self.fixture.connect,1,'synthetic-voice',3,'ja',BODY,set())
        audio_cache.publish(self.fixture.connect, cache_id, attempt, MP3)
        with self.fixture.connect() as db:
            db.execute('INSERT INTO voice_message_audio(message_id,cache_id,sequence,source_sha256,profile_revision,created_at) VALUES(501,?,0,?,3,?)',
                       (cache_id, context['digest'], audio_cache.stamp()))
            before = tuple(db.execute('SELECT * FROM voice_message_audio').fetchone())
        identity = await self.start()
        first = await self.finish(identity)
        row = self.bridge.plans.store.get(identity,1)
        self.bridge.plans.edit(identity,1,EditRequest(expected_revision=1,annotations=[]))
        await self.start(operation='edited')
        second = await self.finish(identity)
        self.assertNotEqual(first['preview']['preview_sha256'],second['preview']['preview_sha256'])
        self.assertEqual((len(self.fixture.model_requests),len(self.fixture.tts_requests)),(1,2))
        self.assertEqual((await self.client.get(first['audio'][0]['url'])).content,MP3)
        self.assertEqual((await self.client.get(second['audio'][0]['url'])).content,MP3)
        with self.fixture.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM voice_message_audio').fetchone()),before)

    async def test_cancel_late_audio_and_permission_drift_cannot_publish(self):
        self.fixture.scenario = 'late_tts'
        self.fixture.tts_hold = asyncio.Event()
        identity = await self.start()
        await self.fixture.tts_entered.wait()
        response = await self.client.delete(P+'/plans/'+identity)
        self.assertEqual(response.status_code,200)
        row = self.bridge.plans.store.get(identity,1)
        self.assertEqual(row['state'],'cancelled')
        with self.bridge.plans.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM annotation_audio WHERE state='ready'").fetchone()[0],0)
        self.assertEqual(len(self.fixture.tts_requests),1)

    async def test_profile_drift_while_planning_stops_tts_and_keeps_text(self):
        self.fixture.model_hold = asyncio.Event()
        identity = await self.start()
        await self.fixture.model_entered.wait()
        with self.fixture.connect() as db:
            db.execute('UPDATE voice_profiles SET revision=4')
        self.fixture.model_hold.set()
        await self.bridge.tasks[identity]
        self.assertEqual(self.bridge.plans.store.get(identity,1)['state'],'stale')
        self.assertEqual(self.fixture.tts_requests,[])
        with self.fixture.connect() as db:
            self.assertEqual(db.execute('SELECT content FROM messages WHERE id=501').fetchone()[0],BODY)

    async def test_tts_unknown_no_plain_fallback_or_new_charge_on_repeat(self):
        self.fixture.scenario = 'tts_unknown'
        identity = await self.start()
        result = await self.finish(identity)
        self.assertEqual(result['bridge_state'],'unknown')
        await self.start(operation='again')
        await self.finish(identity)
        self.assertEqual((len(self.fixture.model_requests),len(self.fixture.tts_requests)),(1,1))
        self.assertEqual(auto_budget.usage(self.fixture.connect,'synthetic-session',1)['daily']['used']['requests'],1)

    async def test_shutdown_late_audio_cannot_publish(self):
        self.fixture.scenario = 'late_tts'
        self.fixture.tts_hold = asyncio.Event()
        identity = await self.start()
        await self.fixture.tts_entered.wait()
        await self.bridge.close()
        self.assertEqual(self.bridge.plans.store.get(identity,1)['state'],'cancelled')
        with self.bridge.plans.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM annotation_audio WHERE state='ready'").fetchone()[0],0)

    async def test_revoked_consent_while_planning(self):
        self.fixture.model_hold = asyncio.Event()
        identity = await self.start()
        await self.fixture.model_entered.wait()
        with self.fixture.connect() as db:
            db.execute('DELETE FROM voice_privacy_consents')
        self.fixture.model_hold.set()
        await self.bridge.tasks[identity]
        self.assertEqual(self.bridge.plans.store.get(identity,1)['state'],'restricted')
        self.assertEqual(self.fixture.tts_requests,[])

    async def test_other_languages_and_multiple_http_segments_are_not_enabled(self):
        for body in ['请你稍微休息一下。', '今夜は静かだ。'*400]:
            with self.fixture.connect() as db:
                db.execute('UPDATE messages SET content=? WHERE id=501',(body,))
            self.fixture.proof['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
            response = await self.client.get(P+'/messages/501/source',params={'mode':'ai_expression'})
            self.assertEqual(response.status_code,422)
        self.assertEqual(self.fixture.model_requests,[])
        self.assertEqual(self.fixture.tts_requests,[])

    async def test_budget_refusal_is_known_zero_tts_and_never_retried(self):
        def deny(*args):
            raise auto_budget.BudgetExhausted('synthetic')
        self.bridge.plans.tts.reserve = deny
        identity = await self.start()
        result = await self.finish(identity)
        self.assertEqual(result['bridge_state'],'failed')
        self.assertEqual(result['error'],'auto_budget_exhausted')
        await self.start(operation='repeat-budget-denial')
        await self.finish(identity)
        self.assertEqual(len(self.fixture.model_requests),1)
        self.assertEqual(self.fixture.tts_requests,[])
        self.assertEqual(auto_budget.usage(self.fixture.connect,'synthetic-session',1)['daily']['used']['requests'],0)


if __name__ == '__main__':
    unittest.main()
