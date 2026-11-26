"""Single-factor candidate, fake wire transport, temporary authority/day ledger."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app.voice import auto_budget, storage
from backend.app.voice.annotation_listening import VoiceAuthority, expected_approval
from backend.app.voice.expression_planner import build as expression
from backend.app.voice.schemas import ProfileInput
from scripts import voice_restrained_comparison as candidate

MP3 = (b"\xff\xfb\x90\x64" + bytes(413)) * 24


class RestrainedComparison(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-restrained-")
        self.root = Path(self.temp.name)
        self.voice = "synthetic-comparison-voice"
        self.env = patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1", "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"})
        self.env.start()
        self.voice_patch = patch.object(candidate, "VOICE_SHA", hashlib.sha256(self.voice.encode()).hexdigest())
        self.voice_patch.start()
        self.manifest = candidate.build()
        (self.root / "data").mkdir()
        self.database = self.root / "data/source.sqlite3"
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            db.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users VALUES(1);"
                "CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER); INSERT INTO characters VALUES(1,1);"
                "CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER,expires_at TEXT);")
            db.execute("INSERT INTO auth_sessions VALUES(?,?,?)", ("0"*64,1,
                (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            storage.init_db(db)
            auto_budget.init_db(db)
            auto_budget.init_daily_db(db)
            db.commit()
            storage.put_profile(db,1,ProfileInput(expected_revision=0,voice_id=self.voice,active=True))
            db.execute("UPDATE voice_profiles SET revision=3")
            storage.put_consent(db,1)
            db.commit()
        self.authority = VoiceAuthority(self.database,"0"*64,self.manifest['voice_binding'],mock_only=True)
        self.receipt = self.root / "approval.json"
        self.receipt.write_text(json.dumps(expected_approval(self.manifest,self.database,"0"*64,None,mock=True)),encoding="utf-8")
        self.output = self.root / "result"
        self.reference = self.root / "historical.mp3"
        self.reference.write_bytes(MP3)
        self.calls = []

    async def asyncTearDown(self):
        self.voice_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    async def dispatch(self, handler=None, *, enabled=True):
        def good(request):
            self.calls.append(request)
            return httpx.Response(200,content=MP3)
        return await candidate.dispatch(self.manifest,self.authority,self.receipt,self.output,
            lambda:"synthetic-only",enabled=enabled,transport=httpx.MockTransport(handler or good),reference=self.reference)

    async def test_exact_single_factor_wire_parameters_and_atomic_usage(self):
        before=self.authority.check()['daily']['remaining']
        real_timeout=asyncio.timeout
        with patch.object(candidate.asyncio,'timeout',wraps=real_timeout) as timer:
            result=await self.dispatch()
        timer.assert_called_once_with(30)
        self.assertEqual(result['status'],'completed')
        self.assertEqual((result['dispatched_http'],result['dispatched_characters']),(1,28))
        self.assertEqual(len(self.calls),1)
        request=self.calls[0]
        self.assertEqual(json.loads(request.content),{'model_id':'eleven_v4',
            'inputs':[{'text':'[thoughtful] '+candidate.TEXT,'voice_id':self.voice}], 'language_code':'ja'})
        self.assertEqual(request.url.path,'/v1/text-to-dialogue')
        self.assertEqual(request.url.params['output_format'],'mp3_44100_128')
        self.assertTrue(all(v==30 for v in request.extensions['timeout'].values()))
        remaining=self.authority.check()['daily']['remaining']
        self.assertEqual({k:before[k]-remaining[k] for k in remaining},{'jobs':1,'requests':1,'characters':28})
        self.assertEqual(self.reference.read_bytes(),MP3)

    async def test_zero_annotation_reference_and_goal_remain_optional_not_applied(self):
        base=expression(candidate.TEXT,'none')
        restrained=expression(candidate.TEXT,'thoughtful')
        self.assertEqual((base.characters,restrained.characters),(15,28))
        self.assertEqual(restrained.request_text.removeprefix('[thoughtful] '),base.request_text)
        self.assertTrue(self.manifest['performance_goal_candidate']['allow_empty_annotations'])
        self.assertFalse(self.manifest['performance_goal_candidate']['require_tag_per_sentence'])
        self.assertEqual(self.manifest['budget']['chat'],0)
        self.assertEqual(self.manifest['budget']['planning'],0)

    async def test_default_off_and_bad_manifest_dispatch_nothing_and_read_no_key(self):
        with self.assertRaises(ValueError):
            await self.dispatch(enabled=False)
        self.manifest['request']['text']='[relaxed] '+candidate.TEXT
        with self.assertRaises(ValueError):
            await self.dispatch()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.receipt.with_name('approval.json.used').exists())

    async def test_missing_approval_or_revoked_permission_is_zero_dispatch(self):
        self.receipt.unlink()
        with self.assertRaises(ValueError):
            await self.dispatch()
        self.assertEqual(self.calls,[])

    async def test_revoked_before_reservation_preserves_daily_budget(self):
        before=self.authority.check()['daily']['remaining']
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('DELETE FROM auth_sessions')
            db.commit()
        with self.assertRaises(Exception):
            await self.dispatch()
        self.assertEqual(self.calls,[])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_auto_owner_day_budget').fetchone()[0],0)
        self.assertGreaterEqual(before['characters'],28)

    async def test_mock_approval_cannot_enter_real_transport(self):
        with self.assertRaises(ValueError):
            await candidate.dispatch(self.manifest,self.authority,self.receipt,self.output,
                lambda:self.fail('no key read'),enabled=True,reference=self.reference)
        self.assertEqual(self.calls,[])

    async def test_budget_exhaustion_and_bad_receipt_are_pre_dispatch(self):
        self.authority.reserve(150)
        with self.assertRaises(Exception):
            await self.dispatch()
        self.assertFalse(self.receipt.with_name('approval.json.used').exists())
        self.assertEqual(self.calls,[])
        self.receipt.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):
            await self.dispatch()
        self.assertEqual(self.calls,[])

    async def test_reference_corruption_blocks_before_credentials_and_claim(self):
        self.reference.write_bytes(b'not MP3')
        with self.assertRaises(Exception):
            await self.dispatch()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.receipt.with_name('approval.json.used').exists())

    async def test_revocation_after_response_does_not_publish_or_refund(self):
        def revoke(request):
            self.calls.append(request)
            with closing(sqlite3.connect(self.database)) as db:
                db.execute('DELETE FROM auth_sessions')
                db.commit()
            return httpx.Response(200,content=MP3)
        result=await self.dispatch(revoke)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(len(self.calls),1)
        self.assertTrue(result['reserved'])
        self.assertFalse((self.output/'restrained.mp3').exists())

    async def test_audio_byte_hard_limit_prevents_complete_publication(self):
        def oversized(request):
            self.calls.append(request)
            return httpx.Response(200,content=b'x'*(16*1024*1024+1))
        result=await self.dispatch(oversized)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(len(self.calls),1)
        self.assertFalse((self.output/'restrained.mp3').exists())

    async def test_unknown_stops_with_one_attempt_and_keeps_charge(self):
        before=self.authority.check()['daily']['remaining']
        def timeout(request):
            self.calls.append(request)
            raise httpx.ReadTimeout('not logged',request=request)
        result=await self.dispatch(timeout)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.authority.check()['daily']['remaining']['characters'],before['characters']-28)
        self.assertFalse((self.output/'restrained.mp3').exists())
        with self.assertRaises(ValueError):
            await self.dispatch()
        self.assertEqual(len(self.calls),1)

    async def test_provider_rejection_and_redirect_never_retry(self):
        def reject(request):
            self.calls.append(request)
            return httpx.Response(302,headers={'location':'https://not-called.invalid/'})
        result=await self.dispatch(reject)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.calls),1)
        self.assertFalse((self.output/'restrained.mp3').exists())

    async def test_cancel_retains_claim_and_does_not_publish(self):
        ready=asyncio.Event()
        async def wait(request):
            self.calls.append(request)
            ready.set()
            await asyncio.Event().wait()
        task=asyncio.create_task(self.dispatch(wait))
        await asyncio.wait_for(ready.wait(),2)
        task.cancel()
        result=await task
        self.assertEqual((result['status'],result['error']),('unknown','cancelled'))
        self.assertTrue(self.receipt.with_name('approval.json.used').exists())
        self.assertFalse((self.output/'restrained.mp3').exists())
        self.assertEqual(self.reference.read_bytes(),MP3)

    async def test_concurrent_repeat_and_restart_cannot_add_request(self):
        results=await asyncio.gather(self.dispatch(),self.dispatch(),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,dict) and x['status']=='completed' for x in results),1)
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(ValueError):
            await self.dispatch()
        self.assertEqual(len(self.calls),1)

    async def test_invalid_audio_is_unknown_and_never_published(self):
        def invalid(request):
            self.calls.append(request)
            return httpx.Response(200,content=b'incomplete')
        result=await self.dispatch(invalid)
        self.assertEqual(result['status'],'unknown')
        self.assertFalse((self.output/'restrained.mp3').exists())

    async def test_review_pending_success_refresh_replay_has_no_dispatch(self):
        app=candidate.review_app(self.reference,self.output,synthetic=True)
        with TestClient(app) as client:
            self.assertEqual(client.get('/audio/reference').status_code,401)
            page=client.get('/')
            self.assertIn('候选尚未生成',page.text)
            self.assertEqual(client.get('/audio/restrained').status_code,404)
        result=await self.dispatch()
        self.assertEqual(result['status'],'completed')
        with TestClient(app) as client:
            for _ in range(2):
                page=client.get('/')
                self.assertIn('本地合成数据验证',page.text)
                self.assertNotIn('候选尚未生成',page.text)
                self.assertEqual(client.get('/audio/reference').content,MP3)
                self.assertEqual(client.get('/audio/restrained').content,MP3)
            self.assertEqual(client.post('/generate').status_code,404)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':
    unittest.main()
