"""12/30 choice through actual chat, synthetic RAW and MockTransport only."""
import asyncio
import hashlib
import json
import time
import unittest
from threading import Event
from unittest.mock import patch

from test_memory_v2_chronicle_chat_supplement import ChronicleChatSupplement
from test_memory_v2_entries import app_module
from backend.app import memory_v2_chronicle_semantic as semantic


class WaitChoice(ChronicleChatSupplement):
    def setUp(self):
        super().setUp()
        self.delay=0;self.generation_started=Event();self.release=None;self.revoke=False
        self.outputs=[]

    def wire(self,request):
        if request.url.path.endswith('/responses'):
            self.generation_started.set()
            if self.release is not None:self.release.wait(5)
            if self.delay:time.sleep(self.delay)
            if self.revoke:
                with app_module.connect() as db:db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
        answer=super().wire(request)
        if request.url.path.endswith('/responses'):self.outputs.append(hashlib.sha256(answer.content).hexdigest())
        return answer

    def payload(self,seconds=None,approved=True):
        result={'character_id':self.cid,'message':self.query,'model_profile_id':self.model}
        if approved:result['chronicle_supplement_max_microusd']=37000
        if seconds is not None:result['chronicle_supplement_wait_seconds']=seconds
        return result

    def send_choice(self,seconds=None,approved=True):
        response=self.client.post('/api/chat/stream',json=self.payload(seconds,approved))
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('event: message_complete',response.text)
        return self.synopsis in str(self.chat[-1])

    def settled(self,expected='rejected'):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            with app_module.connect() as db:
                row=db.execute("""SELECT c.state,b.reserved_microusd,b.spent_estimate_microusd
                    FROM mv3_model_chunks c JOIN mv3_trial_budgets b ON c.trial_id=b.id
                    JOIN mv3_jobs j ON j.id=c.job_id
                    WHERE b.provider='openai_responses' AND c.state=? AND j.lease_token IS NULL""",(expected,)).fetchone()
            if row:return tuple(row)
            time.sleep(.03)
        self.fail('worker did not finish reliable settlement')

    def test_old_omitted_default_and_explicit_30_bound_to_request_and_readset(self):
        self.assertTrue(self.send_choice())
        self.assertTrue(self.send_choice(30))
        with app_module.connect() as db:
            sets=[json.loads(r[0]) for r in db.execute("SELECT read_set_json FROM mv3_jobs WHERE json_extract(read_set_json,'$.phase')='judge'")]
            self.assertEqual([r['chatWait']['seconds'] for r in sets],[12,30])
            self.assertTrue(all(r['chatWait']['version']==semantic.CHAT_WAIT_POLICY for r in sets))
            parameters=[json.loads(r[0]) for r in db.execute("SELECT parameters_json FROM chat_attempts WHERE purpose='chat'")]
            self.assertEqual([x['chronicleSupplement']['waitSeconds'] for x in parameters],[12,30])
            self.assertTrue(all(x['chronicleSupplement']['maxMicrousd']==37000 for x in parameters))

    def test_invalid_wait_values_are_422_without_tasks(self):
        with app_module.connect() as db:before=db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0]
        for invalid in (0,13,29,31,12.0,'30',True,None):
            payload=self.payload();payload['chronicle_supplement_wait_seconds']=invalid
            response=self.client.post('/api/chat/stream',json=payload)
            self.assertEqual(response.status_code,422,(invalid,response.text))
        with app_module.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0],before)
        self.assertEqual(self.calls,[]);self.assertEqual(self.chat,[])

    def test_30_alone_is_not_consent_and_expired_rates_still_block(self):
        self.assertFalse(self.send_choice(30,False));self.assertEqual(self.calls,[])
        import os
        with patch.dict(os.environ,{'YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE':'2000-01-01'}):
            status=self.client.get('/api/chat/chronicle-supplement-status').json()
            self.assertFalse(status['enabled']);self.assertEqual(status['reason'],'chronicle_daily_rates_verification_required')
            self.assertFalse(self.send_choice(30))
        self.assertEqual(self.calls,[])

    def test_same_response_is_late_at_12_and_accepted_at_30(self):
        self.delay=12.6
        started=time.monotonic();self.assertFalse(self.send_choice(12));elapsed12=time.monotonic()-started
        self.assertLess(elapsed12,13.2)
        self.assertEqual(self.settled(),('rejected',0,1650))
        self.assertTrue(self.send_choice(30))
        self.assertEqual(self.outputs[0],self.outputs[1])

    def test_explicit_30_timeout_continues_and_late_cost_settles(self):
        self.delay=30.6
        started=time.monotonic();self.assertFalse(self.send_choice(30));elapsed=time.monotonic()-started
        self.assertGreaterEqual(elapsed,29.7);self.assertLess(elapsed,31.2)
        self.assertEqual(self.settled(),('rejected',0,1650))

    def test_search_construction_does_not_get_a_fresh_outer_window(self):
        self.mode='slow_count'
        original=semantic.ChatSearch.__init__
        def slow_init(obj,*a,**kw):
            original(obj,*a,**kw);time.sleep(.18)
        original_wait=asyncio.wait_for;seen=[]
        async def observed_wait(future,timeout):
            if getattr(getattr(future,'cr_code',None),'co_name',None)=='to_thread':seen.append(timeout)
            return await original_wait(future,timeout)
        with patch.object(semantic,'chat_deadline',side_effect=lambda seconds:time.monotonic()+.24),patch.object(semantic.ChatSearch,'__init__',slow_init),patch.object(app_module.asyncio,'wait_for',observed_wait):
            started=time.monotonic();self.assertFalse(self.send_choice(30));elapsed=time.monotonic()-started
        self.assertEqual(len(seen),1);self.assertLess(seen[0],.10)
        self.assertLess(elapsed,.60)
        self.late_stage_finished.wait(2);time.sleep(.06)
        self.assertNotIn('/v1/responses',self.calls)

    def test_30_unknown_keeps_reserve_and_does_not_redispatch(self):
        self.mode='transport_failure'
        self.assertFalse(self.send_choice(30))
        with app_module.connect() as db:
            hold=db.execute("SELECT b.reserved_microusd FROM mv3_trial_budgets b JOIN mv3_model_chunks c ON c.trial_id=b.id WHERE c.state='unknown'").fetchone()
            self.assertGreater(hold[0],0)
        before=list(self.calls);self.assertFalse(self.send_choice(30));self.assertEqual(self.calls,before)

    def test_revoked_role_cannot_use_late_result_but_usage_settles(self):
        self.revoke=True
        from fastapi.testclient import TestClient
        response=TestClient(app_module.app,raise_server_exceptions=False).post('/api/chat/stream',json=self.payload(30))
        self.assertFalse(self.chat)
        self.assertEqual(self.settled(),('rejected',0,1650))
        self.assertNotIn(self.synopsis,response.text)

    def test_disconnect_during_supplement_never_resumes_chat(self):
        self.release=Event()
        async def exercise():
            disconnected=asyncio.Event();body=json.dumps(self.payload(30)).encode();delivered=False;sent=[]
            async def receive():
                nonlocal delivered
                if not delivered:delivered=True;return {'type':'http.request','body':body,'more_body':False}
                await disconnected.wait();return {'type':'http.disconnect'}
            async def send(event):sent.append(event)
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':'/api/chat/stream','raw_path':b'/api/chat/stream','query_string':b'',
                'root_path':'','headers':[(b'host',b'127.0.0.1'),(b'content-type',b'application/json')],
                'client':('127.0.0.1',12345),'server':('127.0.0.1',8000)}
            task=asyncio.create_task(app_module.app(scope,receive,send))
            try:
                ready=await asyncio.to_thread(self.generation_started.wait,3);self.assertTrue(ready)
                disconnected.set();await asyncio.wait_for(task,2)
            finally:self.release.set()
        asyncio.run(exercise())
        self.assertEqual(self.settled(),('rejected',0,1650))
        self.assertEqual(self.chat,[])
        with app_module.connect() as db:
            row=db.execute("SELECT status,parameters_json FROM chat_attempts ORDER BY started_at DESC LIMIT 1").fetchone()
            self.assertEqual(row['status'],'cancelled')
            self.assertEqual(json.loads(row['parameters_json'])['chronicleSupplement']['waitSeconds'],30)


def load_tests(loader,tests,pattern):
    # Reuse the fixture without counting/rerunning its inherited original tests.
    return unittest.TestSuite(WaitChoice(name) for name in WaitChoice.__dict__ if name.startswith('test_'))
