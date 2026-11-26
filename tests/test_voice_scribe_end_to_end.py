"""Frontend PCM converter -> product WS -> real adapter -> local fake WS."""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import websockets

from backend.app.voice import scribe_draft, storage
from backend.app.voice.schemas import ProfileInput
from backend.app.voice.scribe_adapter import ScribeRealtimeAdapter

ROOT = Path(__file__).resolve().parents[1]


class ScribeAudioE2E(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-scribe-e2e-")
        root = Path(self.temp.name)
        self.pcm_path = root / "converted.pcm"
        subprocess.run(["node", str(ROOT / "tests/scribe_fixture_frames.mjs"),
                        str(ROOT / "tests/fixtures/voice_v1b_scribe_zh.wav"),
                        str(self.pcm_path)], check=True, capture_output=True)
        self.pcm = self.pcm_path.read_bytes()
        self.shape = json.loads((root / "converted.pcm.json").read_text())
        self.assertEqual(self.shape["frames"], len(self.shape["frame_bytes"]))
        self.assertGreater(self.shape["duration_seconds"], 5.79)
        self.assertLess(self.shape["duration_seconds"], 5.81)
        self.assertTrue(any(self.pcm))
        self.mode = "normal"
        self.upstream_connections = 0
        self.upstream_chunks = []
        self.upstream_commits = 0
        self.ready = threading.Event()
        self.stop = threading.Event()

        async def handler(ws):
            self.upstream_connections += 1
            await ws.send(json.dumps({"message_type": "session_started"}))
            async for raw in ws:
                item = json.loads(raw)
                self.assertEqual(item["message_type"], "input_audio_chunk")
                chunk = base64.b64decode(item["audio_base_64"], validate=True)
                self.upstream_chunks.append(chunk)
                if self.mode == "reject":
                    await ws.send(json.dumps({"message_type": "rate_limited"}))
                    continue
                if self.mode == "disconnect":
                    await ws.close(code=1011)
                    return
                if item["commit"]:
                    self.upstream_commits += 1
                    joined = b"".join(self.upstream_chunks)
                    # This provider requires the real converted fixture's full
                    # duration and non-silent content; it cannot return text for
                    # arbitrary/empty audio.
                    if self.mode == "empty":
                        await ws.send(json.dumps({"message_type": "committed_transcript",
                                                  "text": ""}))
                    elif (self.mode != "no_final" and len(joined) == len(self.pcm)
                            and joined == self.pcm and any(joined)):
                        await ws.send(json.dumps({"message_type": "committed_transcript",
                                                  "text": "本地夹具已完整接收"}))
                else:
                    await ws.send(json.dumps({"message_type": "partial_transcript",
                                              "text": "本地处理中"}))

        async def serve():
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self.ready.set()
                while not self.stop.is_set():
                    await asyncio.sleep(.02)

        self.thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
        self.thread.start()
        self.assertTrue(self.ready.wait(5))
        self.env = patch.dict(os.environ, {
            "YOSAKI_DATA_DIR": self.temp.name,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_SCRIBE_DRAFT_ISOLATED": "1",
            "YOSAKI_SCRIBE_DRAFT_DIAGNOSTICS_DIR": self.temp.name,
        })
        self.env.start()
        path = root / "yosaki.sqlite3"

        @contextmanager
        def connect():
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                yield db
                db.commit()
            finally:
                db.close()

        with connect() as db:
            db.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY);"
                "CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL);"
                "INSERT INTO users VALUES(1); INSERT INTO characters VALUES(1,1);")
            storage.init_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(
                expected_revision=0, voice_id="synthetic_voice_only", active=True))
        self.app = FastAPI()
        endpoint = f"ws://127.0.0.1:{self.port}/v1/speech-to-text/realtime"
        scribe_draft.install(self.app, connect,
            lambda ws: {"id": 1} if ws.cookies.get("isolated") == "1" else None,
            ["http://127.0.0.1:15193"],
            provider_factory=lambda *_: ScribeRealtimeAdapter("auto", endpoint=endpoint,
                                                                 key="fake-only", proxy=None))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"origin": "http://127.0.0.1:15193", "cookie": "isolated=1"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.stop.set()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def open(self, ws, attempt):
        ws.send_json({"type": "start", "character_id": 1, "language_code": "auto",
                      "attempt_id": attempt})
        ready = ws.receive_json()
        self.assertEqual(ready["type"], "ready")
        self.assertEqual(ready["attempt_id"], attempt)
        ws.send_json({"type": "capture", "sample_rate": 48000, "channels": 1})

    def audio_frames(self, ws):
        start = 0
        for length in self.shape["frame_bytes"]:
            ws.send_json({"type": "audio", "pcm_base64":
                          base64.b64encode(self.pcm[start:start + length]).decode()})
            start += length
        self.assertEqual(start, len(self.pcm))

    def trace(self, attempt):
        path = Path(self.temp.name) / f"{attempt}.jsonl"
        for _ in range(500):
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            if any(row["kind"] == "cleanup" for row in rows):
                return rows
            time.sleep(.01)
        return rows

    def test_full_fixture_order_commit_and_diagnostics(self):
        attempt = uuid.uuid4().hex
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.open(ws, attempt)
            self.audio_frames(ws)
            ws.send_json({"type": "commit"})
            events = []
            while True:
                event = ws.receive_json()
                events.append(event["type"])
                if event["type"] in ("committed", "error"):
                    self.assertEqual(event["type"], "committed", event)
                    break
        self.assertEqual(self.upstream_connections, 1)
        self.assertEqual(self.upstream_commits, 1)
        self.assertEqual(b"".join(self.upstream_chunks), self.pcm)
        self.assertEqual(len(self.upstream_chunks), self.shape["frames"])
        self.assertIn("partial", events)
        trace = self.trace(attempt)
        self.assertEqual({row["attempt_id"] for row in trace}, {attempt})
        self.assertEqual(next(row for row in trace if row["kind"] == "cleanup")["bytes"],
                         len(self.pcm))
        self.assertEqual(next(row for row in trace if row["kind"] == "cleanup")["chunks"],
                         self.shape["frames"])
        self.assertIn("commit_upstream_sent", [row["kind"] for row in trace])
        self.assertIn("committed_transcript", [row.get("event") for row in trace])
        self.assertNotIn("本地夹具", json.dumps(trace, ensure_ascii=False))

    def test_no_audio_and_silence_are_distinct(self):
        first = uuid.uuid4().hex
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.open(ws, first)
            ws.send_json({"type": "commit"})
            self.assertEqual(ws.receive_json()["code"], "empty_audio")
        self.assertIn("cleanup", [row["kind"] for row in self.trace(first)])
        second = uuid.uuid4().hex
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.open(ws, second)
            ws.send_json({"type": "audio", "pcm_base64": base64.b64encode(bytes(3200)).decode()})
            ws.send_json({"type": "commit"})
            self.assertEqual(ws.receive_json()["code"], "audio_silent")
        self.assertEqual(self.upstream_commits, 0)
        self.assertEqual(self.upstream_connections, 2)
        self.assertEqual(next(row for row in self.trace(first) if row["kind"] == "cleanup")["bytes"], 0)
        self.assertEqual(next(row for row in self.trace(second) if row["kind"] == "cleanup")["bytes"], 3200)

    def test_provider_rejection_and_disconnect_no_retry(self):
        for mode, code in (("reject", "rate_limited"),
                           ("disconnect", "provider_disconnected")):
            self.mode = mode
            attempt = uuid.uuid4().hex
            before = self.upstream_connections
            with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                               headers=self.headers) as ws:
                self.open(ws, attempt)
                self.audio_frames(ws)
                ws.send_json({"type": "commit"})
                while True:
                    event = ws.receive_json()
                    if event["type"] == "error":
                        self.assertEqual(event["code"], code)
                        break
            self.assertEqual(self.upstream_connections, before + 1)
            self.assertEqual(next(row for row in self.trace(attempt)
                                  if row["kind"] == "error")["code"], code)

    def test_missing_final_is_timeout_not_empty_transcript(self):
        self.mode = "no_final"
        attempt = uuid.uuid4().hex
        with patch("backend.app.voice.scribe_adapter.FINAL_WAIT_SECONDS", .1):
            with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                               headers=self.headers) as ws:
                self.open(ws, attempt)
                self.audio_frames(ws)
                ws.send_json({"type": "commit"})
                while True:
                    event = ws.receive_json()
                    if event["type"] == "error":
                        self.assertEqual(event["code"], "commit_timeout")
                        break
        self.assertEqual(self.upstream_connections, 1)
        self.assertEqual(self.upstream_commits, 1)
        self.assertNotIn("provider_empty_transcript", json.dumps(self.trace(attempt)))

    def test_explicit_empty_final_is_provider_empty_result(self):
        self.mode = "empty"
        attempt = uuid.uuid4().hex
        with self.client.websocket_connect("/api/voice/scribe-draft/ws",
                                           headers=self.headers) as ws:
            self.open(ws, attempt)
            self.audio_frames(ws)
            ws.send_json({"type": "commit"})
            while True:
                event = ws.receive_json()
                if event["type"] == "committed":
                    self.assertEqual(event["text"], "")
                    break
        self.assertEqual(self.upstream_connections, 1)
        self.assertEqual(self.upstream_commits, 1)
        self.assertIn("provider_empty_transcript", [row.get("code")
            for row in self.trace(attempt) if row["kind"] == "browser_result"])


if __name__ == "__main__":
    unittest.main()
