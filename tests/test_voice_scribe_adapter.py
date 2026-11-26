"""Real transport protocol against a local WebSocket; no provider network."""

import asyncio
import json
import unittest

import websockets

from backend.app.voice.scribe_adapter import ScribeProtocolError, ScribeRealtimeAdapter


class ScribeAdapterLocal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.kind = "normal"

        async def handler(ws):
            await ws.send(json.dumps({"message_type": "session_started", "config": {}}))
            if self.kind == "disconnect":
                await ws.close(code=1011)
                return
            async for raw in ws:
                message = json.loads(raw)
                self.received.append(message)
                if self.kind == "error":
                    await ws.send(json.dumps({"message_type": "rate_limited", "error": "secret omitted"}))
                    continue
                if message["commit"]:
                    await ws.send(json.dumps({"message_type": "committed_transcript", "text": "最终文本"}))
                else:
                    await ws.send(json.dumps({"message_type": "partial_transcript", "text": "临时"}))

        self.server = await websockets.serve(handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v1/speech-to-text/realtime"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_manual_commit_and_final_only(self):
        adapter = ScribeRealtimeAdapter("ja", endpoint=self.url, key="fake", proxy=None)
        await adapter.open()
        self.assertEqual(await adapter.feed(bytes(3200), commit=False), [])
        early = await adapter.feed(bytes(3200), commit=False)
        final = await adapter.feed(b"", commit=True)
        self.assertEqual((early + final)[-1], {"type": "committed", "text": "最终文本"})
        await adapter.aclose()
        self.assertEqual(len(self.received), 2)
        self.assertTrue(self.received[-1]["commit"])
        self.assertFalse(self.received[0]["commit"])
        self.assertEqual(adapter.pending, b"")

    async def test_error_stops_without_retry(self):
        self.kind = "error"
        adapter = ScribeRealtimeAdapter("auto", endpoint=self.url, key="fake", proxy=None)
        await adapter.open()
        await adapter.feed(bytes(3200), commit=False)
        with self.assertRaisesRegex(ScribeProtocolError, "rate_limited"):
            await adapter.feed(b"", commit=True)
        await adapter.aclose()
        self.assertEqual(len(self.received), 1)

    async def test_disconnect_and_cancel_release(self):
        self.kind = "disconnect"
        adapter = ScribeRealtimeAdapter("zh", endpoint=self.url, key="fake", proxy=None)
        await adapter.open()
        await adapter.feed(bytes(3200), commit=False)
        with self.assertRaises(Exception):
            await adapter.feed(b"", commit=True)
        await adapter.aclose()
        self.assertIsNone(adapter.socket)
        self.assertEqual(len(self.received), 0)
