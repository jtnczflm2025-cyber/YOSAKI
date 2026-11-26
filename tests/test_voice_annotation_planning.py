"""Frozen model-only entry; all credentials, SSE bytes and source rows synthetic."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

from backend.app.voice import storage
from backend.app.voice.schemas import ProfileInput
from scripts import voice_annotation_planning as planning
from tests.test_voice_annotation_wiring import ByteStream, frames


class FrozenPlanning(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-frozen-planning-")
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.database = self.root / "data/source.sqlite3"
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users VALUES(1);
                CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER); INSERT INTO characters VALUES(1,1);
                CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER,model_profile_id INTEGER);
                INSERT INTO conversations VALUES(5,1,5);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER,role TEXT,content TEXT,
                    generation_status TEXT,tool_calls_json TEXT,tool_batch_status TEXT);
                CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER,expires_at TEXT);
                CREATE TABLE model_profiles(id INTEGER PRIMARY KEY,name TEXT,provider_name TEXT,base_url TEXT,
                    model_name TEXT,protocol TEXT,temperature REAL,top_p REAL,max_tokens INTEGER,
                    context_limit INTEGER,supports_streaming INTEGER,enabled INTEGER,reasoning_effort TEXT,updated_at TEXT);""")
            db.execute("INSERT INTO messages VALUES(10,5,'assistant',?,'completed',NULL,NULL)",
                       ("急がなくていい。今夜は静かだ。\n\n少し考えてから話そう。",))
            db.execute("INSERT INTO auth_sessions VALUES(?,?,?)",("0"*64,1,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
            db.execute("INSERT INTO model_profiles VALUES(5,'synthetic','synthetic','https://api.openai.com/v1',"
                "'gpt-6.1-sol','chatgpt_plan',0.8,1,2048,16000,1,1,NULL,'synthetic-version')")
            storage.init_db(db);db.commit();db.row_factory=sqlite3.Row
            storage.put_profile(db,1,ProfileInput(expected_revision=0,voice_id="synthetic-voice",active=True))
            storage.put_consent(db,1);db.commit()
        self.manifest = json.loads(json.dumps(planning.build(self.database)))
        self.directory = self.root / "run"
        self.directory.mkdir()
        self.receipt = self.directory / "approval.json"
        self.receipt.write_text(json.dumps(planning.approval_for(self.manifest,"0"*64,None,
            self.directory,mock=True,database=self.database)),encoding="utf-8")
        self.calls=[]
        self.token_calls=[]

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def token(self, selected):
        self.token_calls.append(selected)
        return "synthetic-token"

    async def run_plan(self, annotations=None, *, unknown=False, enabled=True):
        def handler(request):
            self.calls.append(json.loads(request.content))
            raw={"schema_version":1,"input_sha256":self.manifest["input_sha256"],
                 "annotations":annotations or []}
            return httpx.Response(200,stream=ByteStream(frames(json.dumps(raw),unknown=unknown)))
        return await planning.execute(self.manifest,self.receipt,"0"*64,self.token,enabled=enabled,
            database=self.database,transport=httpx.MockTransport(handler),mock_directory=self.directory)

    async def test_freeze_uses_explicit_saved_selection_and_entire_public_input(self):
        m=self.manifest
        self.assertEqual(m["identity"]["model_profile_id"],5)
        self.assertIsNone(m["expected_annotation_answer"])
        self.assertEqual(m["prepared"]["granularity"],"auto")
        self.assertEqual(m["request"]["max_output_tokens"],512)
        self.assertEqual(m["request"]["total_timeout_seconds"],20)
        self.assertEqual(m["wire_body"]["max_output_tokens"],512)
        self.assertNotIn("tools",m["wire_body"])
        self.assertEqual(m["wire_body"]["model"],"gpt-6.1-sol")
        self.assertEqual(m["saved_voice_configuration"]["personality"],"")
        self.assertEqual(m["prepared"]["model_input"]["psychology"],{})
        self.assertEqual(m["budget"]["http_tts"],0)
        self.assertEqual(self.calls,[])

    async def test_real_wire_zero_annotations_valid_no_tts_and_duplicate_refused(self):
        value=await self.run_plan()
        self.assertEqual(value["status"],"empty")
        self.assertEqual(value["preview"]["request_text"],self.manifest["prepared"]["safe_text"])
        self.assertEqual(value["tts_requests"],0)
        self.assertEqual(value["usage"]["total_tokens"],130)
        self.assertEqual(self.calls,[self.manifest["wire_body"]])
        self.assertEqual(self.token_calls,[5])
        with self.assertRaises(ValueError):await self.run_plan()
        self.assertEqual(len(self.calls),1)

    async def test_sparse_annotations_show_exact_original_sentence_and_preview(self):
        sentence=next(f for f in self.manifest["prepared"]["fragments"] if f["kind"]=="sentence" and f["start"]>0)
        value=await self.run_plan([{"fragment_id":sentence["fragment_id"],"intent":"thoughtful"}])
        self.assertEqual(value["status"],"ready")
        self.assertEqual(value["suggestions"][0]["source_text"],"今夜は静かだ。")
        self.assertEqual(len(value["preview"]["insertions"]),1)
        self.assertEqual(value["preview"]["insertions"][0]["offset"],sentence["start"])
        self.assertEqual(value["tts_requests"],0)

    async def test_unknown_stops_persistently_no_tts_or_reexecution(self):
        value=await self.run_plan(unknown=True)
        self.assertEqual(value["status"],"unknown")
        self.assertEqual(value["safe_reason"],"model_event_unknown")
        self.assertNotIn("preview",value)
        self.assertEqual(value["tts_requests"],0)
        with self.assertRaises(ValueError):await self.run_plan()
        self.assertEqual(len(self.calls),1)

    async def test_multiple_positions_remain_one_plan_and_are_preview_only(self):
        paragraphs=[f for f in self.manifest["prepared"]["fragments"] if f["kind"]=="paragraph"]
        value=await self.run_plan([{"fragment_id":p["fragment_id"],"intent":intent}
            for p,intent in zip(paragraphs,("restrained","whisper"))])
        self.assertEqual(value["status"],"ready")
        self.assertEqual(len(value["preview"]["insertions"]),2)
        self.assertEqual(value["suggestions"][1]["source_text"],"少し考えてから話そう。")
        self.assertEqual(value["tts_requests"],0)
        self.assertEqual(len(self.calls),1)

    async def test_foreign_fragment_is_invalid_with_usage_preserved(self):
        value=await self.run_plan([{"fragment_id":"foreign-id","intent":"thoughtful"}])
        self.assertEqual(value["status"],"invalid")
        self.assertEqual(value["safe_reason"],"fragment_not_owned")
        self.assertEqual(value["usage"]["total_tokens"],130)
        self.assertNotIn("preview",value)

    async def test_bad_receipt_and_expired_session_reject_before_token(self):
        self.receipt.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):await self.run_plan()
        self.receipt.write_text(json.dumps(planning.approval_for(self.manifest,"0"*64,None,
            self.directory,mock=True,database=self.database)),encoding='utf-8')
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE auth_sessions SET expires_at=?",((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),));db.commit()
        with self.assertRaises(ValueError):await self.run_plan()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.token_calls,[])

    async def test_disabled_bad_receipt_or_source_drift_zero_transport(self):
        with self.assertRaises(ValueError):await self.run_plan(enabled=False)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE messages SET content=content||'変更。' WHERE id=10");db.commit()
        with self.assertRaises(ValueError):await self.run_plan()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.token_calls,[])
        self.assertFalse((self.directory/"approval.json.used").exists())

    async def test_mock_receipt_cannot_start_real_transport(self):
        with self.assertRaises(ValueError):
            await planning.execute(self.manifest,self.receipt,"0"*64,self.token,enabled=True,
                database=self.database,transport=None,mock_directory=self.directory)
        self.assertEqual(self.token_calls,[])

    async def test_archiving_the_source_rejects_before_credentials_or_dispatch(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("ALTER TABLE conversations ADD COLUMN archive_only INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE conversations SET archive_only=1 WHERE id=5");db.commit()
        with self.assertRaises(ValueError):await self.run_plan()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.token_calls,[])

    async def test_concurrent_run_claims_only_once(self):
        values=await asyncio.gather(self.run_plan(),self.run_plan(),return_exceptions=True)
        self.assertEqual(sum(isinstance(v,dict) and v["status"]=="empty" for v in values),1)
        self.assertEqual(len(self.calls),1)


if __name__=="__main__":unittest.main()
