"""Opt-in short segmentation and the actual guarded serial entry, all synthetic."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app.voice import auto_budget
from backend.app.voice.annotation_planner import fingerprint
from backend.app.voice.segment_plan import build
from backend.app.voice.short_segment_policy import ShortSegmentPolicy
from backend.app.voice.segmented_reading import SegmentedReading
from backend.app.voice.long_http_adapter import LongHTTPAdapter,LongHTTPDiagnostics
from scripts import voice_short_serial_pair as entry
from scripts.voice_segmented_lab import bootstrap,MP3
from scripts.voice_segmented_listen import review_app
from tests.test_voice_long_http_timing import Stream,policy


def long_text():
    return ''.join(f'第{i}项记录：清晨我们沿着院子旁的小路慢慢往前走，把今天需要完成的事情按顺序写清楚，遇到不确定的部分就先停下来核对。' for i in range(1,45))


class Planning(unittest.TestCase):
    def assert_mapping(self,p):
        self.assertEqual(''.join(p['gaps'][i]+s['safe_text'] for i,s in enumerate(p['segments']))+p['gaps'][-1],p['safe_text'])
        self.assertEqual(p['characters'],sum(len(s['text']) for s in p['segments']))

    def test_normal_configurable_short_segments_and_default_compatibility(self):
        p=build(long_text(),short_policy=ShortSegmentPolicy());self.assert_mapping(p)
        self.assertGreater(p['characters'],2000);self.assertGreater(p['requests'],8)
        self.assertTrue(all(150<=s['characters']<=250 and s['text'].endswith('。') for s in p['segments']))
        changed=build(long_text(),short_policy=ShortSegmentPolicy(target=180,minimum=140,maximum=230))
        self.assertNotEqual(p['preview_sha256'],changed['preview_sha256']);self.assert_mapping(changed)
        legacy=build((entry.ROOT/'tests/fixtures/voice_step3_two_request_zh.txt').read_text(encoding='utf-8'))
        self.assertEqual([s['characters'] for s in legacy['segments']],[1084,1046])
        self.assertNotIn('segmentation_policy',legacy)

    def test_short_tail_overlong_sentence_and_unicode_fallback(self):
        p=build(('甲'*99+'。')*4+'乙'*69+'。',short_policy=ShortSegmentPolicy())
        self.assertEqual([s['characters'] for s in p['segments']],[200,200,70]);self.assert_mapping(p)
        text='甲'*139+'。'+'乙'*399+'。'
        p=build(text,short_policy=ShortSegmentPolicy());self.assertEqual(p['segments'][0]['characters'],140)
        self.assert_mapping(p)
        text='甲'*198+'👩\u200d💻'+'乙'*220+'。'
        p=build(text,short_policy=ShortSegmentPolicy());self.assert_mapping(p)
        self.assertTrue(all(not s['text'].endswith('\u200d') and not s['text'].startswith(('\u200d','💻')) for s in p['segments']))

    def test_total_and_request_budgets_not_bypassed_and_tags_not_enabled(self):
        with self.assertRaises(ValueError):build('甲'*8001,short_policy=ShortSegmentPolicy())
        with self.assertRaises(ValueError):build('甲'*4000,short_policy=ShortSegmentPolicy(target=50,minimum=50,maximum=50))
        with self.assertRaises(ValueError):ShortSegmentPolicy(maximum=251)
        with self.assertRaises(ValueError):ShortSegmentPolicy(target=True)
        with self.assertRaises(ValueError):build('一句。',annotations=[{'offset':0,'intent':'thoughtful'}],candidate_tags=True,short_policy=ShortSegmentPolicy())


class Supplier:
    def __init__(self):self.calls=[];self.fail_at=None;self.unknown=False
    async def handle(self,request):
        self.calls.append(request);trace=request.extensions['trace']
        class Post:method=b'POST'
        await trace('http11.send_request_headers.started',{'request':Post()})
        await trace('http11.send_request_body.complete',{})
        await trace('http11.receive_response_headers.started',{})
        if len(self.calls)==self.fail_at and self.unknown:raise httpx.ReadTimeout('synthetic')
        status=401 if len(self.calls)==self.fail_at else 200
        await trace('http11.receive_response_headers.complete',{'return_value':(b'HTTP/1.1',status,b'',[])})
        return httpx.Response(status,stream=Stream((.001,)))


class Serial(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-short-serial-')
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name,'ELEVENLABS_API_KEY':'fake-only'})
        self.env.start();self.root=Path(self.temp.name);self.connect=bootstrap(self.root/'ledger')
        self.fake=Supplier();self.readers=[]
        self.m=entry.build()
        self.parent=self.root/'YOSAKI/voice-runs'/self.m['run_id'];self.parent.mkdir(parents=True)
        self.receipt=self.parent/'approval.json'
        self.receipt.write_text(json.dumps(entry.expected_approval(self.m,'synthetic-session')))
        connect=self.connect
        class Authority:
            def __init__(self,*args):self.write=connect
            def check(self,**kwargs):return {'voice_id':'fake','revision':3}
        self.patches=[patch.object(entry,'VoiceAuthority',Authority),patch.object(entry,'local_proxy',return_value=self.m['proxy']),
            patch.object(entry,'LongHTTPAdapter',self.adapter)]
        for p in self.patches:p.start()
    def adapter(self,key=None,proxy_url=None,diagnostics=None):
        return LongHTTPAdapter('fake',httpx.MockTransport(self.fake.handle),diagnostics=diagnostics,policy=policy())
    async def asyncTearDown(self):
        for r in self.readers:await r.cancel()
        for p in reversed(self.patches):p.stop()
        self.env.stop();self.temp.cleanup()
    def used(self):return auto_budget.usage(self.connect,'synthetic-session',1)['daily']['used']
    def many(self,between=None):
        r=SegmentedReading(self.root/'YOSAKI/voice-runs/synthetic-many/result',self.connect,long_text(),
            lambda d:self.adapter(diagnostics=d),lambda:{'voice_id':'fake','revision':3},
            lambda n,f:auto_budget.reserve(self.connect,'synthetic-session',1,n,first_request=f),
            short_policy=ShortSegmentPolicy(),diagnostics_factory=LongHTTPDiagnostics,between=between)
        self.readers.append(r);return r
    async def run_reader(self,r):
        async with asyncio.timeout(220):
            await r.start(r.preview['preview_sha256']);await r.task

    async def test_over_2000_serial_complete_order_meter_and_actual_review_player(self):
        r=self.many();await self.run_reader(r)
        self.assertEqual(r.result['status'],'completed')
        count=r.preview['requests'];self.assertEqual(len(self.fake.calls),count)
        payload=[json.loads(c.content)['inputs'][0]['text'] for c in self.fake.calls]
        self.assertEqual(payload,[s['text'] for s in r.preview['segments']])
        self.assertEqual(self.used(),{'jobs':1,'requests':count,'characters':r.preview['characters']})
        with TestClient(review_app(r.root)) as client:
            page=client.get('/').text
            for seq in range(count):self.assertEqual(client.get('/audio/'+str(seq)).content,MP3)
        page_path=self.root/'synthetic-page.html';page_path.write_text(page,encoding='utf-8')
        result=subprocess.run(['node',str(entry.ROOT/'tests/check_serial_review_player.cjs'),str(page_path),str(count)],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['ordered_segments'],count)
        before=self.used()
        with TestClient(review_app(r.root)) as client:
            self.assertEqual(client.get('/').status_code,200)
            for seq in range(count):self.assertEqual(client.get('/audio/'+str(seq)).content,MP3)
        await r.start(r.preview['preview_sha256'])
        self.assertEqual(len(self.fake.calls),count);self.assertEqual(self.used(),before)

    async def test_midway_failure_keeps_prefix_no_next_request(self):
        self.fake.fail_at=6;r=self.many();await self.run_reader(r)
        self.assertEqual(r.result['status'],'failed');self.assertEqual(len(self.fake.calls),6)
        self.assertTrue(all(s['status']=='completed' for s in r.result['segments'][:5]))
        self.assertTrue(all(s['status']=='not_executed' for s in r.result['segments'][6:]))
        for i in range(5):self.assertTrue(r.audio(i).is_file())
        self.assertEqual(self.used()['characters'],sum(s['characters'] for s in r.preview['segments'][:6]))

    async def test_cross_segment_cancel_stops_and_late_completion_cannot_restart(self):
        entered=asyncio.Event()
        async def gap():
            if len(self.fake.calls)==3:entered.set();await asyncio.sleep(10)
        r=self.many(gap)
        await r.start(r.preview['preview_sha256']);await entered.wait();await r.cancel()
        self.assertEqual(r.result['status'],'cancelled');self.assertEqual(len(self.fake.calls),3)
        self.assertEqual(r.result['segments'][3]['status'],'not_executed')
        await r.start(r.preview['preview_sha256']);self.assertEqual(len(self.fake.calls),3)
        for i in range(3):self.assertTrue(r.audio(i).is_file())

    async def test_pair_frozen_requests_cache_replay_and_duplicate_rejection(self):
        p=self.m['preview'];self.assertEqual([s['characters'] for s in p['segments']],[182,187])
        self.assertEqual(p['characters'],369);self.assertEqual(p['segmentation_policy']['target'],200)
        r=await entry.execute(self.m,'synthetic-session',self.receipt);self.assertEqual(r['status'],'completed')
        self.assertEqual(self.used(),{'jobs':1,'requests':2,'characters':369})
        self.assertTrue(all(c.url.path=='/v1/text-to-dialogue/stream' for c in self.fake.calls))
        before=self.used()
        with TestClient(review_app(self.parent/'result')) as client:
            for _ in range(2):
                self.assertEqual(client.get('/').status_code,200)
                for seq in (0,1):self.assertEqual(client.get('/audio/'+str(seq)).content,MP3)
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(len(self.fake.calls),2);self.assertEqual(self.used(),before)

    async def test_pair_first_unknown_and_changed_policy_zero_additional_dispatch(self):
        original=self.m['preview']['segmentation_policy']['target'];self.m['preview']['segmentation_policy']['target']=180
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(self.fake.calls,[])
        self.m['preview']['segmentation_policy']['target']=original
        self.fake.fail_at=1;self.fake.unknown=True
        r=await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(r['status'],'unknown');self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(r['segments'][1]['status'],'not_executed')
        self.assertEqual(self.used(),{'jobs':1,'requests':1,'characters':182})


if __name__=='__main__':unittest.main()
