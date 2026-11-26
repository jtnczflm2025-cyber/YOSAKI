"""Synthetic transport only; no daily database, credentials or sockets."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import httpx

from backend.app.voice import auto_budget
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.http_diagnostics import SegmentDiagnostics
from backend.app.voice.segment_plan import build
from backend.app.voice.segmented_reading import SegmentedReading
from scripts.voice_segmented_lab import bootstrap, FakeSupplier, ROOT


class Planning(unittest.TestCase):
    def test_frozen_two_request_reconstruction(self):
        text = (ROOT/'tests/fixtures/voice_step3_two_request_zh.txt').read_text(encoding='utf-8')
        p=build(text)
        self.assertEqual([s['characters'] for s in p['segments']], [1084,1046])
        self.assertEqual(p['characters'],2130)
        self.assertEqual(p['requests'],2)
        self.assertEqual(p['language_code'],'zh')
        self.assertEqual(''.join(p['gaps'][i]+s['safe_text'] for i,s in enumerate(p['segments']))+p['gaps'][-1],text)

    def test_tag_capacity_and_cross_request_boundaries(self):
        text=('甲乙丙丁。'*398)+'\n\n'+('中文下一句。'*180)
        p=build(text,[{'offset':0,'intent':'thoughtful'},
            {'offset':1992,'intent':'restrained'}],candidate_tags=True)
        self.assertTrue(all(s['characters']<=2000 for s in p['segments']))
        self.assertEqual(sum(s['text'].count('[thoughtful] ') for s in p['segments']),2)
        self.assertEqual(''.join(p['gaps'][i]+s['safe_text'] for i,s in enumerate(p['segments']))+p['gaps'][-1],text)
        self.assertEqual(sum(len(s['text']) for s in p['segments']),p['characters'])
        with self.assertRaises(ValueError): build(text,[{'offset':1,'intent':'thoughtful'}],candidate_tags=True)
        with self.assertRaises(ValueError): build(text,[{'offset':0,'intent':'thoughtful'}])


class Routes(unittest.TestCase):
    def test_loopback_route_rejects_stale_unknown_and_incomplete_audio(self):
        from scripts.voice_segmented_lab import make_app
        with tempfile.TemporaryDirectory(prefix='yosaki-segment-api-') as root, patch.dict(os.environ,{},clear=False):
            with TestClient(make_app(root)) as client:
                self.assertEqual(client.get('/api/voice-segmented-lab/arbitrary').status_code,404)
                self.assertEqual(client.post('/api/voice-segmented-lab/normal/start',
                    json={'preview_sha256':'0'*64}).status_code,409)
                self.assertEqual(client.get('/api/voice-segmented-lab/normal/audio/0').status_code,404)
                self.assertEqual(client.get('/api/voice-segmented-lab/normal').json()['fake_http'],0)
                self.assertEqual(client.post('/api/voice-segmented-lab/normal/start',
                    headers={'Origin':'https://other.test'},json={}).status_code,403)


class Reading(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-long-http-')
        self.env=patch.dict(os.environ,{},clear=False);self.env.start()
        self.root=Path(self.temp.name)
        self.connect=bootstrap(self.root)
        self.text=(ROOT/'tests/fixtures/voice_step3_two_request_zh.txt').read_text(encoding='utf-8')
        self.allowed=True
        self.readings=[]

    async def asyncTearDown(self):
        for r in self.readings: await r.cancel()
        self.env.stop();self.temp.cleanup()

    def make(self,scenario='normal',body=None,between=None):
        fake=FakeSupplier(scenario)
        def authority():
            if not self.allowed: raise PermissionError('revoked')
            return {'voice_id':'synthetic_'+scenario,'revision':3}
        def reserve(count,first):
            auto_budget.reserve(self.connect,'synthetic-session',1,count,first_request=first)
        r=SegmentedReading(self.root/'run',self.connect,body or self.text,
            lambda d: ElevenLabsAdapter('fake',httpx.MockTransport(fake.handle),diagnostics=d),
            authority,reserve,between=between)
        self.readings.append(r)
        return r,fake

    async def execute(self,r):
        await r.start(r.preview['preview_sha256']); await r.task

    def used(self):
        return auto_budget.usage(self.connect,'synthetic-session',1)['daily']['used']

    async def test_normal_two_exact_payloads_replay_and_restart(self):
        r,f=self.make();await self.execute(r)
        self.assertEqual(r.result['status'],'completed')
        self.assertEqual([c['inputs'][0]['text'] for c in f.calls], [s['text'] for s in r.preview['segments']])
        self.assertEqual(self.used(),{'jobs':1,'requests':2,'characters':2130})
        self.assertEqual(json.loads((r.root/'preview.json').read_text(encoding='utf-8'))['source_text'],self.text)
        for i in range(2): self.assertTrue(r.audio(i).is_file())
        await r.start(r.preview['preview_sha256']);self.assertEqual(len(f.calls),2)
        reboot=SegmentedReading(r.root,self.connect,self.text,r.adapter_factory,r.authority,r.reserve)
        await reboot.start(r.preview['preview_sha256']);self.assertIsNone(reboot.task)
        self.assertEqual(len(f.calls),2)

    async def test_three_segments_order(self):
        text='\n\n'.join(('段落'+str(i)+'。')*260 for i in range(3))
        r,f=self.make(body=text);await self.execute(r)
        self.assertEqual(r.result['status'],'completed')
        self.assertEqual(len(f.calls),3)
        self.assertEqual([c['inputs'][0]['text'] for c in f.calls],[s['text'] for s in r.preview['segments']])

    async def test_first_rejected_no_second(self):
        r,f=self.make('first_rejected');await self.execute(r)
        self.assertEqual(r.result['status'],'failed');self.assertEqual(len(f.calls),1)
        self.assertEqual(self.used()['requests'],1)
        self.assertEqual(r.result['segments'][1]['status'],'not_executed')

    async def test_first_unknown_preserved_no_retry(self):
        r,f=self.make('first_unknown');await self.execute(r)
        self.assertEqual(r.result['status'],'unknown');await r.start(r.preview['preview_sha256'])
        self.assertEqual(len(f.calls),1)
        with self.assertRaises(ValueError):r.audio(0)

    async def test_partial_quarantined_not_media(self):
        r,f=self.make('partial_unknown');await self.execute(r)
        self.assertEqual(len(f.calls),1)
        records=[json.loads(p.read_text()) for p in (r.root/'diagnostics').glob('*.json')]
        self.assertGreater(records[0]['audio_bytes'],0)
        self.assertFalse(records[0]['diagnostic_audio']['publishable'])
        self.assertEqual(len(list((r.root/'diagnostics').glob('*.incomplete'))),1)
        self.assertEqual(list(r.root.glob('*.mp3')),[])
        with self.assertRaises(ValueError):r.audio(0)

    async def test_later_failure_keeps_first(self):
        r,f=self.make('later_failed');await self.execute(r)
        self.assertEqual(r.result['status'],'failed');self.assertEqual(len(f.calls),2)
        self.assertTrue(r.audio(0).is_file())
        self.assertEqual(r.preview['source_text'],self.text)
        with self.assertRaises(ValueError):r.audio(1)

    async def test_cancel_between_prevents_second_and_preserves_first(self):
        entered=asyncio.Event()
        async def gap(): entered.set();await asyncio.sleep(20)
        r,f=self.make(between=gap)
        await r.start(r.preview['preview_sha256']);await entered.wait();await r.cancel()
        self.assertEqual(len(f.calls),1);self.assertTrue(r.audio(0).is_file())
        self.assertEqual(r.result['status'],'cancelled')
        await r.start(r.preview['preview_sha256']);self.assertEqual(len(f.calls),1)

    async def test_concurrent_duplicate_and_stale_preview(self):
        r,f=self.make()
        with self.assertRaises(ValueError): await r.start('0'*64)
        self.assertEqual(f.calls,[])
        await asyncio.gather(*[r.start(r.preview['preview_sha256']) for _ in range(8)])
        await r.task;self.assertEqual(len(f.calls),2)

    async def test_revocation_between_and_media_block(self):
        async def gap():self.allowed=False
        r,f=self.make(between=gap);await self.execute(r)
        self.assertEqual(len(f.calls),1)
        with self.assertRaises(PermissionError):r.audio(0)

    async def test_budget_rejected_before_first_no_charge(self):
        with self.connect() as db:db.execute('UPDATE voice_auto_owner_settings SET character_count=100')
        r,f=self.make();await self.execute(r)
        self.assertEqual(f.calls,[]);self.assertEqual(self.used()['requests'],0)

    async def test_diagnostics_bounded_and_terminal_kept(self):
        d=SegmentDiagnostics(self.root/'diag','job',0,'attempt')
        for _ in range(600):d.event('receive_progress',audio_bytes=1)
        d.event('stopped',status='unknown',error_type='secret_error_text')
        d.finish()
        self.assertLessEqual(len(d.record['events']),128)
        self.assertEqual(d.record['events'][-1]['event'],'stopped')
        self.assertNotIn('secret_error_text',d.path.read_text())

    async def test_diagnostic_write_failure_no_following_dispatch(self):
        r,f=self.make()
        with patch.object(SegmentDiagnostics,'flush',side_effect=OSError('synthetic')):
            await self.execute(r)
        self.assertEqual(f.calls,[])

    async def test_late_audio_after_cancel_cannot_publish(self):
        r,f=self.make()
        entered=asyncio.Event()
        class LateAdapter:
            async def synthesize_http(self,*args):
                entered.set()
                try:await asyncio.sleep(20)
                except asyncio.CancelledError:pass
                from scripts.voice_segmented_lab import MP3
                return MP3
        r.adapter_factory=lambda d:LateAdapter()
        await r.start(r.preview['preview_sha256']);await entered.wait();await r.cancel()
        self.assertEqual(r.result['status'],'cancelled')
        self.assertEqual(list(r.root.glob('*.mp3')),[])
        self.assertEqual(r.result['http_attempts'],1)
        self.assertEqual(r.result['segments'][1]['status'],'not_executed')

    async def test_integrity_failure_quarantined(self):
        r,f=self.make()
        async def invalid(request):return httpx.Response(200,content=b'not-complete-mp3')
        r.adapter_factory=lambda d:ElevenLabsAdapter('fake',httpx.MockTransport(invalid),diagnostics=d)
        await self.execute(r)
        self.assertEqual(r.result['status'],'unknown')
        self.assertEqual(r.result['segments'][0]['failure_phase'],'integrity_check')
        self.assertEqual(list(r.root.glob('*.mp3')),[])
        self.assertEqual(len(list((r.root/'diagnostics').glob('*.incomplete'))),1)

    async def test_expired_fragment_not_served_or_resynthesized(self):
        r,f=self.make();await self.execute(r)
        path=r.audio(0);os.utime(path,(0,0))
        with self.assertRaisesRegex(ValueError,'expired'):r.audio(0)
        await r.start(r.preview['preview_sha256']);self.assertEqual(len(f.calls),2)

    async def test_second_budget_rejection_keeps_first_without_extra_debit(self):
        with self.connect() as db:db.execute('UPDATE voice_auto_owner_settings SET character_count=1200')
        r,f=self.make();await self.execute(r)
        self.assertEqual(len(f.calls),1);self.assertTrue(r.audio(0).is_file())
        self.assertEqual(self.used(),{'jobs':1,'requests':1,'characters':1084})
        self.assertFalse(r.result['segments'][1].get('budget_reserved',False))

    async def test_tagged_capacity_reaches_actual_fake_wire(self):
        r,f=self.make()
        text=('甲乙丙丁。'*398)+'\n\n'+('中文下一句。'*180)
        tagged=SegmentedReading(self.root/'tagged',self.connect,text,r.adapter_factory,r.authority,r.reserve,
            annotations=[{'offset':0,'intent':'thoughtful'},{'offset':1992,'intent':'restrained'}],
            candidate_tags=True)
        self.readings.append(tagged);await self.execute(tagged)
        self.assertEqual(tagged.result['status'],'completed')
        payloads=[c['inputs'][0]['text'] for c in f.calls]
        self.assertEqual(payloads,[s['text'] for s in tagged.preview['segments']])
        self.assertTrue(all(len(t)<=2000 for t in payloads))
        self.assertEqual(sum(t.count('[thoughtful] ') for t in payloads),2)


if __name__=='__main__':unittest.main()
