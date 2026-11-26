"""Only the new two-part /stream wiring; fake authority/credentials/HTTP."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from backend.app.voice import auto_budget
from backend.app.voice.long_http_adapter import LongHTTPAdapter
from backend.app.voice.segmented_reading import SegmentedReading
from scripts import voice_long_stream_two_part as entry
from scripts.voice_segmented_lab import bootstrap,MP3
from tests.test_voice_long_http_timing import policy,Stream


class Fake:
    def __init__(self,scenario='normal'):self.calls=[];self.scenario=scenario
    async def handle(self,request):
        self.calls.append(request)
        trace=request.extensions['trace']
        class Post:method=b'POST'
        await trace('http11.send_request_headers.started',{'request':Post()})
        await trace('http11.send_request_body.complete',{})
        await trace('http11.receive_response_headers.started',{})
        if self.scenario=='first_unknown':raise httpx.ReadTimeout('synthetic-only')
        if self.scenario=='slow':await asyncio.sleep(.5)
        status=503 if self.scenario=='later_failed' and len(self.calls)==2 else 200
        await trace('http11.receive_response_headers.complete',{'return_value':(b'HTTP/1.1',status,b'',[])})
        return httpx.Response(status,stream=Stream((.01,)))


class Wiring(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-two-stream-')
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name,'ELEVENLABS_API_KEY':'synthetic-only'})
        self.env.start()
        self.connect=bootstrap(Path(self.temp.name)/'ledger')
        self.m=entry.build();self.fake=Fake()
        self.parent=Path(self.temp.name)/'YOSAKI/voice-runs'/self.m['run_id'];self.parent.mkdir(parents=True)
        self.receipt=self.parent/'approval.json'
        self.receipt.write_text(json.dumps(entry.expected_approval(self.m,'synthetic-session')))
        connect=self.connect
        class Authority:
            def __init__(self,*a):self.write=connect
            def check(self,**a):return {'voice_id':'synthetic_ivc'}
        self.patches=[patch.object(entry,'VoiceAuthority',Authority),
            patch.object(entry,'local_proxy',return_value=self.m['proxy']),
            patch.object(entry,'LongHTTPAdapter',lambda key,proxy_url,diagnostics:
                LongHTTPAdapter(key,httpx.MockTransport(self.fake.handle),diagnostics=diagnostics,policy=policy()))]
        for p in self.patches:p.start()
    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.env.stop();self.temp.cleanup()
    async def execute(self):return await entry.execute(self.m,'synthetic-session',self.receipt)
    def used(self):return auto_budget.usage(self.connect,'synthetic-session',1)['daily']['used']

    async def test_two_stream_requests_exact_preview_meter_and_diagnostics(self):
        self.assertEqual(self.m['budget']['job_seconds'],220)
        self.assertEqual(self.m['budget']['timeouts'],dict(connect=10,first_response=60,read_idle=30,hard=90,write=10,pool=5))
        p=self.m['preview'];self.assertEqual([s['characters'] for s in p['segments']],[1036,1052])
        rebuilt=''.join(p['gaps'][i]+s['safe_text'] for i,s in enumerate(p['segments']))+p['gaps'][-1]
        self.assertEqual(rebuilt,p['source_text']);self.assertTrue(2001<=p['characters']<=2200)
        r=await self.execute();self.assertEqual(r['status'],'completed')
        self.assertEqual(len(self.fake.calls),2)
        payloads=[json.loads(c.content) for c in self.fake.calls]
        self.assertEqual([b['inputs'][0]['text'] for b in payloads],[s['text'] for s in p['segments']])
        self.assertTrue(all(c.url.path=='/v1/text-to-dialogue/stream' for c in self.fake.calls))
        self.assertTrue(all(b['model_id']=='eleven_v4' and b['language_code']=='zh' and 'settings' not in b for b in payloads))
        self.assertEqual(self.used(),{'jobs':1,'requests':2,'characters':2088})
        records=[json.loads(f.read_text()) for f in (self.parent/'result/diagnostics').glob('*.json')]
        self.assertEqual(len(records),2)
        for d in records:
            self.assertTrue(d['complete_cache'])
            self.assertTrue(any(e['event']=='transport_headers_complete' for e in d['events']))
            self.assertTrue(any(e['event']=='saved' for e in d['events']))

    async def test_first_unknown_stops_second_and_retains_usage(self):
        self.fake.scenario='first_unknown'
        r=await self.execute();self.assertEqual(r['status'],'unknown')
        self.assertEqual(len(self.fake.calls),1);self.assertEqual(r['segments'][1]['status'],'not_executed')
        self.assertEqual(self.used(),{'jobs':1,'requests':1,'characters':1036})
        with self.assertRaises(ValueError):entry.review_app(self.parent/'result')

    async def test_later_failure_keeps_complete_first_and_text(self):
        self.fake.scenario='later_failed'
        r=await self.execute();self.assertEqual(r['status'],'unknown')
        self.assertEqual(len(self.fake.calls),2)
        self.assertEqual(r['segments'][0]['status'],'completed')
        self.assertEqual(r['segments'][1]['status'],'unknown')
        preview=json.loads((self.parent/'result/preview.json').read_text(encoding='utf-8'))
        self.assertEqual(preview['source_text'],self.m['preview']['source_text'])
        with TestClient(entry.review_app(self.parent/'result')) as client:
            self.assertEqual(client.get('/audio/0').content,MP3)
            self.assertEqual(client.get('/audio/1').status_code,404)

    async def test_between_segment_cancel_preserves_first_and_no_second(self):
        entered=asyncio.Event();readers=[]
        async def gap():entered.set();await asyncio.sleep(5)
        def reader(*a,**kw):
            value=SegmentedReading(*a,**kw,between=gap);readers.append(value);return value
        with patch.object(entry,'SegmentedReading',reader):
            task=asyncio.create_task(self.execute());await entered.wait();await readers[0].cancel();r=await task
        self.assertEqual(r['status'],'cancelled');self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(r['segments'][0]['status'],'completed')
        self.assertEqual(r['segments'][1]['status'],'not_executed')

    async def test_review_reload_and_audio_replay_never_synthesize(self):
        await self.execute();before=self.used()
        with TestClient(entry.review_app(self.parent/'result')) as client:
            for _ in range(2):
                page=client.get('/');self.assertEqual(page.status_code,200)
                self.assertIn('const ids=["0", "1"]',page.text)
                for seq in (0,1):self.assertEqual(client.get('/audio/'+str(seq)).content,MP3)
            self.assertEqual(client.post('/start').status_code,404)
        self.assertEqual(len(self.fake.calls),2);self.assertEqual(self.used(),before)

    async def test_duplicate_concurrent_batch_has_only_two_dispatches(self):
        results=await asyncio.gather(self.execute(),self.execute(),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) and r['status']=='completed' for r in results),1)
        self.assertEqual(len(self.fake.calls),2)
        with self.assertRaises(ValueError):await self.execute()
        self.assertEqual(len(self.fake.calls),2)

    async def test_changed_binding_zero_dispatch(self):
        self.m['budget']['http']=3
        with self.assertRaises(ValueError):await self.execute()
        self.assertEqual(self.fake.calls,[])
        self.assertFalse((self.parent/'approval.json.used').exists())

    async def test_whole_job_deadline_does_not_look_like_user_cancel(self):
        self.fake.scenario='slow'
        limits=[]
        def scaled(seconds):limits.append(seconds);return asyncio.timeout(.08 if seconds==220 else seconds)
        with patch.object(entry,'asyncio',SimpleNamespace(timeout=scaled)):
            r=await self.execute()
        self.assertEqual(limits,[220]);self.assertEqual(r['status'],'unknown')
        self.assertEqual(r['error_code'],'two_part_job_hard_deadline')
        self.assertLessEqual(len(self.fake.calls),1)
        self.assertEqual(r['segments'][1]['status'],'not_executed')


if __name__=='__main__':unittest.main()
