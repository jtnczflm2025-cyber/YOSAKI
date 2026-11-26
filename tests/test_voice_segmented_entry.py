"""Final approval/CLI execution path, injected fake authority and HTTP transport."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from backend.app.voice import auto_budget
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from scripts import voice_segmented_listen as entry
from scripts.voice_segmented_candidate import build
from scripts.voice_segmented_lab import bootstrap,FakeSupplier


class Entry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-long-final-entry-')
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name,'ELEVENLABS_API_KEY':'synthetic-only'})
        self.env.start()
        self.control=bootstrap(Path(self.temp.name)/'control')
        self.m=build();self.fake=FakeSupplier()
        self.parent=Path(self.temp.name)/'YOSAKI/voice-runs'/self.m['run_id']
        self.parent.mkdir(parents=True)
        self.receipt=self.parent/'approval.json'
        self.receipt.write_text(json.dumps(entry.approval(self.m,'synthetic-session',None)))
        connect=self.control
        class Authority:
            def __init__(self,*args):self.write=connect
            def check(self,**kwargs):return {'voice_id':'synthetic_ivc'}
        self.patches=[patch.object(entry,'VoiceAuthority',Authority),patch.object(entry,'local_proxy',return_value=None),
            patch.object(entry,'ElevenLabsAdapter',lambda key,proxy_url,diagnostics:
                ElevenLabsAdapter(key,httpx.MockTransport(self.fake.handle),diagnostics=diagnostics))]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.env.stop();self.temp.cleanup()

    async def test_final_entry_two_attempts_receipt_and_atomic_day_meter(self):
        r=await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(r['status'],'completed');self.assertEqual(len(self.fake.calls),2)
        self.assertTrue((self.parent/'approval.json.used').exists())
        self.assertEqual(auto_budget.usage(self.control,'synthetic-session',1)['daily']['used'],
                         {'jobs':1,'requests':2,'characters':2130})
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(len(self.fake.calls),2)

    async def test_missing_or_changed_receipt_zero_connection(self):
        self.receipt.write_text('{}')
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(self.fake.calls,[])
        self.assertFalse((self.parent/'approval.json.used').exists())

    async def test_changed_manifest_zero_connection(self):
        self.m['budget']['characters']+=1
        with self.assertRaises(ValueError):await entry.execute(self.m,'synthetic-session',self.receipt)
        self.assertEqual(self.fake.calls,[])


if __name__=='__main__':unittest.main()
