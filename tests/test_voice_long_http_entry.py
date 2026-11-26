"""Final new /stream candidate gate, fake authority and HTTP transport only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from backend.app.voice import auto_budget
from backend.app.voice.long_http_adapter import LongHTTPAdapter,LongHTTPPolicy
from scripts import voice_long_http_diagnostic as entry
from scripts.voice_segmented_lab import bootstrap
from tests.test_voice_long_http_timing import Supplier,policy


class Entry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-new-http-entry-')
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name,'ELEVENLABS_API_KEY':'fake-only'})
        self.env.start()
        self.control=bootstrap(Path(self.temp.name)/'control')
        self.m=entry.build();self.supplier=Supplier()
        self.root=Path(self.temp.name)/'YOSAKI/voice-runs'/self.m['run_id'];self.root.mkdir(parents=True)
        self.receipt=self.root/'approval.json'
        self.receipt.write_text(json.dumps(entry.expected_approval(self.m,'synthetic-session')))
        connect=self.control
        class Authority:
            def __init__(self,*a):self.write=connect
            def check(self,**a):return {'voice_id':'fake'}
        self.patches=[patch.object(entry,'VoiceAuthority',Authority),
            patch.object(entry,'local_proxy',return_value=self.m['proxy']),
            patch.object(entry,'LongHTTPAdapter',lambda key,proxy_url,diagnostics:
                LongHTTPAdapter(key,httpx.MockTransport(self.supplier.handle),diagnostics=diagnostics,policy=policy()))]
        for p in self.patches:p.start()
    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.env.stop();self.temp.cleanup()

    async def test_one_actual_fake_request_meter_and_duplicate_rejection(self):
        r=await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(r['status'],'completed');self.assertEqual(len(self.supplier.calls),1)
        self.assertEqual(self.supplier.calls[0].url.path,'/v1/text-to-dialogue/stream')
        self.assertEqual(auto_budget.usage(self.control,'synthetic-session',1)['daily']['used'],
                         {'jobs':1,'requests':1,'characters':201})
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(len(self.supplier.calls),1)

    async def test_changed_manifest_or_missing_receipt_zero_dispatch(self):
        self.m['budget']['characters']=202
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.m=entry.build();self.receipt.write_text('{}')
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertFalse((self.root/'approval.json.used').exists());self.assertEqual(self.supplier.calls,[])

    async def test_unknown_keeps_charge_and_never_restarts(self):
        self.supplier.headers=.8
        r=await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(r['status'],'unknown');self.assertEqual(len(self.supplier.calls),1)
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(len(self.supplier.calls),1)
        self.assertTrue((self.root/'approval.json.used').exists())

    async def test_invalid_policy_and_pre_start_cancel_zero_dispatch(self):
        with self.assertRaises(ValueError):LongHTTPPolicy(hard=100)
        with self.assertRaises(ValueError):LongHTTPPolicy(read_idle=float('nan'))
        from backend.app.voice.segmented_reading import SegmentedReading
        reader=SegmentedReading(self.root/'prestart',self.control,self.m['preview']['source_text'],
            lambda d:None,lambda:{'voice_id':'fake','revision':3},lambda *a:None)
        await reader.start(reader.preview['preview_sha256']);await reader.cancel()
        self.assertEqual(reader.result['status'],'cancelled')
        self.assertEqual(reader.result['http_attempts'],0)


if __name__=='__main__':unittest.main()
