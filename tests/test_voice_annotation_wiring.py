"""Real adapters over fake byte transports and temporary authority/usage tables."""
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
from backend.app.voice.annotation_lab import PREFIX, FixtureRepository, create_app
from backend.app.voice.annotation_model import PlanningError, ProfilePlanningAdapter
from backend.app.voice.annotation_planner import Source, prepare
from backend.app.voice.annotation_listening import (VoiceAuthority, expected_approval,
    review_app, run_listening)
from backend.app.voice.schemas import ProfileInput
from scripts.voice_annotation_candidate import build

FRAME = b"\xff\xfb\x90\x64" + bytes(413)
MP3 = FRAME * 24


def model_request(adapter):
    prepared = prepare(Source(1, 1, 1, 1, "今夜は静かだ。"), model_binding=adapter.binding())
    body = {**prepared["model_input"], "input_sha256": prepared["input_sha256"]}
    instruction = body.pop("instruction")
    return {"model": prepared["model"]["model"], "model_profile_id": adapter.profile_id,
            "model_binding": adapter.binding(), "max_output_tokens": 512,
            "total_timeout_seconds": 20, "retries": 0,
            "messages": [{"role": "system", "content": instruction},
                         {"role": "user", "content": json.dumps(body, ensure_ascii=False)}]}


def frames(text, *, terminal="response.completed", usage=True, unknown=False):
    item = {"type": "message", "id": "synthetic-answer", "phase": "final_answer",
            "content": [{"type": "output_text", "text": text}]}
    events = [{"type": "response.output_item.added", "item": item, "output_index": 0},
              {"type": "response.output_text.delta", "item_id": item["id"], "content_index": 0, "delta": text}]
    if unknown:
        events.append({"type": "response.synthetic_unknown"})
    if terminal:
        response = {"status": terminal.split(".")[-1], "output": [item]}
        if usage:
            response["usage"] = {"input_tokens": 101, "output_tokens": 29, "total_tokens": 130}
        events.append({"type": terminal, "response": response})
    return [("event: " + event["type"] + "\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
            for event in events]


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, hold=None):
        self.chunks, self.hold, self.closed = chunks, hold, False

    async def __aiter__(self):
        for chunk in self.chunks:
            for pos in range(0, len(chunk), 7):
                yield chunk[pos:pos + 7]
        if self.hold:
            self.hold.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class PlanningWiring(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"id": 9, "enabled": 1, "protocol": "openai_responses",
            "model_name": "gpt-6.1-sol", "base_url": "https://model.test/v1",
            "max_tokens": 2048, "reasoning_effort": None, "temperature": .7, "top_p": 1}
        self.sent, self.tokens = [], []
        self.response_text = None
        self.terminal, self.unknown = "response.completed", False

    async def token(self, selected):
        self.tokens.append(selected)
        return "synthetic-token-never-a-real-credential"

    def adapter(self, handler=None, *, enabled=True):
        def default(request):
            body = json.loads(request.content)
            self.sent.append((str(request.url), body))
            user = next(m for m in body["input"] if m["role"] == "user")
            data = json.loads(user["content"])
            text = self.response_text or json.dumps({"schema_version": 1,
                "input_sha256": data["input_sha256"], "annotations": []}, ensure_ascii=False)
            return httpx.Response(200, stream=ByteStream(frames(text, terminal=self.terminal, unknown=self.unknown)))
        return ProfilePlanningAdapter(9, lambda _: self.profile, self.token,
            enabled=enabled, transport=httpx.MockTransport(handler or default))

    async def test_both_existing_payload_builders_preserve_selected_profile_and_cap(self):
        for protocol in ("openai_responses", "chatgpt_plan"):
            self.profile["protocol"] = protocol
            adapter = self.adapter()
            request = model_request(adapter)
            value, usage = await adapter.generate(request)
            self.assertEqual(value["annotations"], [])
            self.assertEqual(usage["total_tokens"], 130)
            url, body = self.sent[-1]
            self.assertEqual(body["max_output_tokens"], 512)
            self.assertEqual(body["model"], self.profile["model_name"])
            self.assertNotIn("tools", body)
            self.assertFalse(body["store"])
            self.assertEqual(self.profile["max_tokens"], 2048)
            self.assertEqual(self.tokens[-1], 9)
        self.assertEqual(len(self.sent), 2)

    async def test_invalid_caps_or_profile_block_before_token_and_transport(self):
        adapter = self.adapter()
        valid = model_request(adapter)
        for key, value in [("max_output_tokens", None), ("max_output_tokens", True),
                           ("max_output_tokens", 2048), ("total_timeout_seconds", 30),
                           ("retries", 1), ("model_profile_id", 10), ("model", "another-model")]:
            with self.subTest(key=key, value=value), self.assertRaises(PlanningError):
                await adapter.generate({**valid, key: value})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.tokens, [])

    async def test_default_off_and_profile_drift_no_dispatch(self):
        adapter = self.adapter(enabled=False)
        with self.assertRaises(PlanningError):
            await adapter.generate(model_request(adapter))
        adapter = self.adapter()
        request = model_request(adapter)
        self.profile["reasoning_effort"] = "medium"
        with self.assertRaises(PlanningError):
            await adapter.generate(request)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.tokens, [])

    async def test_strict_json_duplicates_not_accepted_and_usage_retained(self):
        self.response_text = '{"schema_version":1,"schema_version":2}'
        with self.assertRaises(PlanningError) as raised:
            await self.adapter().generate(model_request(self.adapter()))
        self.assertEqual(raised.exception.code, "planning_json_invalid")
        self.assertEqual(raised.exception.usage["total_tokens"], 130)

    async def test_failed_truncated_eof_and_unknown_are_distinct(self):
        for terminal, unknown, code in [("response.failed", False, "model_failed"),
                ("response.incomplete", False, "model_incomplete"),
                (None, False, "model_completion_missing"),
                ("response.completed", True, "model_event_unknown")]:
            self.terminal, self.unknown = terminal, unknown
            adapter = self.adapter()
            with self.subTest(code=code), self.assertRaises(PlanningError) as raised:
                await adapter.generate(model_request(adapter))
            self.assertEqual(raised.exception.code, code)
            if terminal in ("response.failed", "response.incomplete"):
                self.assertEqual(raised.exception.usage["output_tokens"], 29)

    async def test_cancel_closes_transport_and_preserves_observed_usage(self):
        hold = asyncio.Event()
        text = '{"schema_version":1,"input_sha256":"x","annotations":[]}'
        stream = ByteStream(frames(text), hold=hold)
        def handler(_request):
            self.sent.append(1)
            return httpx.Response(200, stream=stream)
        adapter = self.adapter(handler)
        task = asyncio.create_task(adapter.generate(model_request(adapter)))
        await asyncio.wait_for(hold.wait(), 2)
        task.cancel()
        with self.assertRaises(PlanningError) as raised:
            await task
        self.assertEqual(raised.exception.outcome, "cancelled")
        self.assertEqual(raised.exception.usage["total_tokens"], 130)
        self.assertTrue(stream.closed)
        self.assertEqual(len(self.sent), 1)

    async def test_real_twenty_second_total_deadline_over_fake_transport(self):
        async def handler(_request):
            self.sent.append(1)
            await asyncio.sleep(21)
            return httpx.Response(200)
        adapter = self.adapter(handler)
        started = asyncio.get_running_loop().time()
        with self.assertRaises(PlanningError) as raised:
            await adapter.generate(model_request(adapter))
        elapsed = asyncio.get_running_loop().time() - started
        self.assertEqual(raised.exception.code, "planning_total_timeout")
        self.assertGreaterEqual(elapsed, 19.5)
        self.assertLess(elapsed, 22)
        self.assertEqual(len(self.sent), 1)

    async def test_selected_profile_through_actual_lab_route_fake_transport_only(self):
        with tempfile.TemporaryDirectory(prefix="yosaki-selected-plan-") as directory:
            adapter = self.adapter()
            app = create_app(Path(directory), enabled=True, model=adapter)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                await client.get(PREFIX + "/session")
                source = (await client.get(PREFIX + "/messages/103/expression-source")).json()
                payload = {"operation_id": "selected-profile", "input_sha256": source["input_sha256"],
                    "profile_revision": 3, "planning_consent": True, "model_profile_id": 9}
                wrong = await client.post(PREFIX + "/messages/103/expression-plans", json={**payload, "model_profile_id": 10})
                self.assertEqual(wrong.status_code, 409)
                row = (await client.post(PREFIX + "/messages/103/expression-plans", json=payload)).json()
                for _ in range(100):
                    result = (await client.get(PREFIX + "/expression-plans/" + row["id"])).json()
                    if result["state"] != "planning": break
                    await asyncio.sleep(.01)
                self.assertEqual(result["state"], "empty")
                self.assertEqual(len(self.sent), 1)
                self.assertEqual(self.sent[0][1]["max_output_tokens"], 512)
            await app.state.service.close()

    async def test_actual_adapter_unknown_and_invalid_json_are_persistent_not_retried(self):
        for unknown, expected in ((True,"failed_unknown"),(False,"fallback")):
            self.unknown=unknown
            self.response_text=None if unknown else '{"schema_version":1,"schema_version":2}'
            before=len(self.sent)
            with tempfile.TemporaryDirectory(prefix="yosaki-adapter-outcome-") as directory:
                app=create_app(Path(directory),enabled=True,model=self.adapter())
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    await client.get(PREFIX+"/session")
                    source=(await client.get(PREFIX+"/messages/103/expression-source")).json()
                    payload={"operation_id":"adapter-outcome","input_sha256":source["input_sha256"],
                             "profile_revision":3,"planning_consent":True,"model_profile_id":9}
                    row=(await client.post(PREFIX+"/messages/103/expression-plans",json=payload)).json()
                    for _ in range(100):
                        value=(await client.get(PREFIX+"/expression-plans/"+row["id"])).json()
                        if value["state"]!="planning":break
                        await asyncio.sleep(.01)
                    self.assertEqual(value["state"],expected)
                    again=(await client.post(PREFIX+"/messages/103/expression-plans",json=payload)).json()
                    self.assertEqual(again["id"],row["id"])
                    self.assertEqual(len(self.sent),before+1)
                    if not unknown:self.assertEqual(value["usage"]["total_tokens"],130)
                await app.state.service.close()


class ListeningWiring(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-listening-wiring-")
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1", "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1"})
        self.env.start()
        self.manifest = build()
        self.manifest["status"] = "mock_transport_only"
        self.voice_id = "synthetic-listening-voice"
        self.manifest["voice_binding"]["expected_voice_sha256"] = hashlib.sha256(self.voice_id.encode()).hexdigest()
        (self.root / "data").mkdir()
        self.database = self.root / "data" / "source.sqlite3"
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
            storage.put_profile(db,1,ProfileInput(expected_revision=0,voice_id=self.voice_id,active=True))
            db.execute("UPDATE voice_profiles SET revision=3")
            storage.put_consent(db,1)
            db.commit()
        self.authority = VoiceAuthority(self.database, "0"*64,self.manifest["voice_binding"],mock_only=True)
        self.receipt = self.root / "approval.json"
        self.receipt.write_text(json.dumps(expected_approval(self.manifest,self.database,"0"*64,None,mock=True)),encoding="utf-8")
        self.output = self.root / "result"
        self.calls=[]

    async def asyncTearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def run_batch(self, handler=None, *, enabled=True):
        def normal(request):
            self.calls.append(request)
            return httpx.Response(200, content=MP3)
        return await run_listening(self.manifest,self.authority,self.receipt,self.output,
            lambda:"synthetic-key",enabled=enabled,transport=httpx.MockTransport(handler or normal))

    async def test_exact_serial_wire_requests_usage_and_playback_reads(self):
        result = await self.run_batch()
        self.assertEqual(result["status"],"completed")
        self.assertEqual((result["dispatched_http"],result["dispatched_characters"]),(2,91))
        for sent, planned in zip(self.calls,self.manifest["requests"]):
            body=json.loads(sent.content)
            self.assertEqual(body["inputs"],[{"text":planned["text"],"voice_id":self.voice_id}])
            self.assertEqual(body["model_id"],"eleven_v4")
            self.assertEqual(body["language_code"],"ja")
            self.assertNotIn("settings",body)
            self.assertEqual(sent.url.params["output_format"],"mp3_44100_128")
        remaining=self.authority.check()["daily"]["remaining"]
        self.assertEqual(remaining,{"jobs":0,"requests":0,"characters":69})
        app=review_app(self.output)
        with TestClient(app) as client:
            self.assertEqual(client.get("/audio/plain").status_code,401)
            self.assertEqual(client.get("/").status_code,200)
            for _ in range(3):
                self.assertEqual(client.get("/audio/plain").content,MP3)
                self.assertEqual(client.post("/playback/plain/play").status_code,200)
            self.assertEqual(client.get("/audio/two_position_candidate").content,MP3)
            self.assertEqual(client.get("/audio/unknown").status_code,404)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.authority.check()["daily"]["remaining"],remaining)

    async def test_first_rejection_or_unknown_stops_second_and_reservation_survives(self):
        def reject(request):
            self.calls.append(request)
            return httpx.Response(429)
        result=await self.run_batch(reject)
        self.assertEqual(result["items"][0]["status"],"failed")
        self.assertEqual(result["items"][1]["status"],"not_executed")
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.authority.check()["daily"]["remaining"]["characters"],125)
        with self.assertRaises(Exception):await self.run_batch(reject)
        self.assertEqual(len(self.calls),1)

    async def test_transport_unknown_does_not_repeat(self):
        def unknown(request):
            self.calls.append(request)
            raise httpx.ReadTimeout("do not store free text",request=request)
        result=await self.run_batch(unknown)
        self.assertEqual(result["items"][0]["status"],"unknown")
        self.assertEqual(result["items"][1]["status"],"not_executed")
        self.assertEqual(result["dispatched_http"],1)
        self.assertEqual(result["items"][0]["audio_bytes"],None)

    async def test_cancel_after_attempt_stops_second_and_keeps_consumption(self):
        attempted=asyncio.Event()
        async def waiting(request):
            self.calls.append(request)
            attempted.set()
            await asyncio.Event().wait()
        task=asyncio.create_task(self.run_batch(waiting))
        await asyncio.wait_for(attempted.wait(),2)
        task.cancel()
        result=await task
        self.assertEqual(result["items"][0]["error_code"],"cancelled")
        self.assertEqual(result["items"][1]["status"],"not_executed")
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.authority.check()["daily"]["remaining"]["characters"],125)

    async def test_each_http_has_real_thirty_second_total_deadline(self):
        async def waiting(request):
            self.calls.append(request)
            await asyncio.sleep(31)
            return httpx.Response(200,content=MP3)
        started=asyncio.get_running_loop().time()
        result=await self.run_batch(waiting)
        elapsed=asyncio.get_running_loop().time()-started
        self.assertGreaterEqual(elapsed,29.5)
        self.assertLess(elapsed,32)
        self.assertEqual(result["items"][0]["status"],"unknown")
        self.assertEqual(result["items"][1]["status"],"not_executed")
        self.assertEqual(len(self.calls),1)

    async def test_duplicate_concurrent_and_restart_cannot_dispatch_again(self):
        outcomes=await asyncio.gather(self.run_batch(),self.run_batch(),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,dict) and x["status"]=="completed" for x in outcomes),1)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(self.receipt.with_name(self.receipt.name+".used").exists())
        with self.assertRaises(Exception):await self.run_batch()
        self.assertEqual(len(self.calls),2)

    async def test_missing_bad_or_mock_receipt_cannot_use_real_transport(self):
        with self.assertRaises(Exception):
            await run_listening(self.manifest,self.authority,self.receipt,self.output,
                lambda:self.fail("no credentials should be read"),enabled=True,transport=None)
        self.receipt.write_text('{}',encoding="utf-8")
        with self.assertRaises(Exception):await self.run_batch()
        self.assertEqual(self.calls,[])

    async def test_copy_or_rename_receipt_does_not_create_new_run_capacity(self):
        renamed=self.receipt.with_name("second-approval.json")
        renamed.write_bytes(self.receipt.read_bytes())
        with self.assertRaises(Exception):
            await run_listening(self.manifest,self.authority,renamed,self.output,
                lambda:self.fail("no credential read"),enabled=True,transport=httpx.MockTransport(lambda r:self.fail()))
        with self.assertRaises(Exception):
            await run_listening(self.manifest,self.authority,self.receipt,self.root/"another-result",
                lambda:self.fail("no credential read"),enabled=True,transport=httpx.MockTransport(lambda r:self.fail()))
        self.assertEqual(self.calls,[])

    async def test_disabled_gate_and_drift_zero_dispatch_zero_usage(self):
        with self.assertRaises(Exception):await self.run_batch(enabled=False)
        self.manifest["requests"][0]["text"]+='改'
        with self.assertRaises(Exception):await self.run_batch()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.authority.check()["daily"]["remaining"]["requests"],2)

    async def test_revoked_consent_or_low_daily_balance_rejects_before_claim(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM voice_privacy_consents")
            db.commit()
        with self.assertRaises(Exception):await self.run_batch()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.receipt.with_name(self.receipt.name+".used").exists())

    async def test_low_daily_balance_rejects_before_claim(self):
        self.authority.reserve(80)
        with self.assertRaises(Exception):await self.run_batch()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.receipt.with_name(self.receipt.name+".used").exists())

    async def test_serial_transport_has_only_one_active_request(self):
        active=0
        async def delayed(request):
            nonlocal active
            active+=1
            self.assertEqual(active,1)
            self.calls.append(request)
            await asyncio.sleep(.02)
            active-=1
            return httpx.Response(200,content=MP3)
        result=await self.run_batch(delayed)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(len(self.calls),2)

    async def test_crash_marker_and_daily_budget_remain_used(self):
        result=await self.run_batch()
        marker=self.receipt.with_name(self.receipt.name+".used")
        before=marker.read_bytes()
        # A fresh authority instance cannot turn the used marker into capacity.
        self.authority=VoiceAuthority(self.database,"0"*64,self.manifest["voice_binding"],mock_only=True)
        with self.assertRaises(Exception):await self.run_batch()
        self.assertEqual(marker.read_bytes(),before)
        self.assertEqual(result["dispatched_http"],2)


if __name__=="__main__":unittest.main()
