"""V4 isolated output tests: loopback fake provider only, no main.py or database."""

import asyncio
import base64
import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from backend.app.voice.realtime_output import OutputLimits, OutputRelay


PCM = base64.b64encode(struct.pack("<hhhh", 0, 1000, -1000, 0)).decode()


class OutputRelayTests(unittest.IsolatedAsyncioTestCase):
    def test_frozen_real_execution_candidate_has_no_implicit_dispatch(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "VOICE_V4_STREAM_R2_MANIFEST.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "awaiting_single_real_approval")
        self.assertEqual(manifest["characters"], len(manifest["text"]))
        self.assertEqual(manifest["characters"], 52)
        self.assertEqual(manifest["limits"]["provider_http_requests"], 0)
        self.assertEqual(manifest["limits"]["provider_ws_connections"], 1)
        self.assertEqual(manifest["limits"]["automatic_text_retries"], 0)

    def setUp(self):
        self.events = []
        self.limits = OutputLimits(keepalive_seconds=.2, total_seconds=2,
                                   close_wait_seconds=.5, connect_budget_seconds=1,
                                   reconnect_attempts=1)

    async def emit(self, event):
        self.events.append(event)

    async def run_server(self, handler, action):
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            relay = OutputRelay(voice_id="synthetic_voice_id", emit=self.emit,
                                connector=lambda: connect(f"ws://127.0.0.1:{port}",
                                                          open_timeout=.2, close_timeout=.1,
                                                          ping_interval=None), limits=self.limits)
            await action(relay)

    async def test_normal_pcm_stream_has_distinct_ids_sequence_and_final(self):
        async def provider(ws):
            self.assertEqual(json.loads(await ws.recv()), {"voices": ["synthetic_voice_id"]})
            sent = json.loads(await ws.recv())
            self.assertEqual(sent["inputs"], [{"text": "safe test", "voice_id": "synthetic_voice_id"}])
            self.assertTrue(sent["flush"])
            await ws.send(json.dumps({"audio": PCM}))
            await ws.send(json.dumps({"audio": PCM, "is_final_audio_for_turn": True}))
            self.assertEqual(json.loads(await ws.recv()), {"close_socket": True})
            await ws.send('{"is_final":true}')

        async def action(relay):
            run = await relay.start(request_id="browser-1", text="safe test")
            await run.task
            audio = [e for e in self.events if e["type"] == "audio"]
            self.assertEqual([e["sequence"] for e in audio], [1, 2])
            self.assertIsInstance(audio[0]["text_dispatch_to_first_audio_ms"], (int, float))
            self.assertGreaterEqual(audio[0]["text_dispatch_to_first_audio_ms"], 0)
            self.assertNotIn("text_dispatch_to_first_audio_ms", audio[1])
            self.assertEqual({e["connection_id"] for e in audio}, {run.connection_id})
            self.assertEqual({e["generation_id"] for e in audio}, {run.generation_id})
            self.assertEqual({e["segment_id"] for e in audio}, {run.segment_id})
            phases = [e["type"] for e in self.events]
            self.assertLess(phases.index("final_turn"), phases.index("close_socket_sent"))
            self.assertLess(phases.index("close_socket_sent"), phases.index("final_protocol_event"))
            self.assertLess(phases.index("final_protocol_event"), phases.index("transport_closed"))
            self.assertLess(phases.index("transport_closed"), phases.index("completed"))
            self.assertEqual(self.events[-1]["type"], "completed")
            self.assertEqual(self.events[-1]["audio_bytes"], 16)
            await relay.stop()
            self.assertNotIn("stopped", [e["type"] for e in self.events])
        await self.run_server(provider, action)

    async def test_audio_hard_limit_has_explicit_reason(self):
        async def provider(ws):
            await ws.recv(); await ws.recv()
            await ws.send(json.dumps({"audio": PCM}))
            await ws.send(json.dumps({"audio": PCM}))
        async def action(relay):
            relay.limits = OutputLimits(total_seconds=2, max_audio_bytes=8,
                                        connect_budget_seconds=1, reconnect_attempts=0)
            run = await relay.start(request_id="hard-cap", text="safe test")
            await run.task
            failure = next(e for e in self.events if e["type"] == "failed")
            self.assertEqual(failure["error_category"], "audio_hard_limit")
            self.assertEqual([e["type"] for e in self.events].count("audio"), 1)
        await self.run_server(provider, action)

    async def test_keepalive_sent_while_waiting_and_then_audio(self):
        async def provider(ws):
            await ws.recv(); await ws.recv()
            self.assertEqual(json.loads(await ws.recv()), {"keep_alive": True})
            self.assertEqual(json.loads(await ws.recv()), {"keep_alive": True})
            await ws.send(json.dumps({"audio": PCM}))
            await ws.send('{"is_final_audio_for_turn":true}')
            await ws.recv()
            await ws.send('{"is_final":true}')
        async def action(relay):
            relay.limits = OutputLimits(keepalive_seconds=.05, total_seconds=1,
                                        close_wait_seconds=.3, connect_budget_seconds=.5)
            run = await relay.start(request_id="keepalive", text="safe test")
            await run.task
            self.assertEqual([e["type"] for e in self.events].count("keepalive_sent"), 2)
            self.assertEqual(self.events[-1]["type"], "completed")
        await self.run_server(provider, action)

    async def test_application_quota_error_stops_without_audio_or_retry(self):
        connections = 0
        async def provider(ws):
            nonlocal connections
            connections += 1
            await ws.recv(); await ws.recv()
            await ws.send('{"error":{"code":"quota_exceeded","message":"do not log"}}')
            await ws.close(code=1008)
        async def action(relay):
            run = await relay.start(request_id="quota", text="safe test")
            await run.task
            self.assertEqual(connections, 1)
            self.assertEqual([e["type"] for e in self.events].count("audio"), 0)
            self.assertEqual(next(e for e in self.events if e["type"] == "failed")
                             ["error_category"], "quota")
            self.assertTrue(any(e["type"] == "transport_closed" for e in self.events))
            self.assertNotIn("do not log", str(self.events))
        await self.run_server(provider, action)

    async def test_auth_quota_and_protocol_handshake_never_reconnect(self):
        for status, category in ((401, "authentication"), (429, "quota"),
                                 (400, "protocol")):
            self.events.clear()
            attempts = 0
            class Denied:
                async def __aenter__(self):
                    raise InvalidStatus(SimpleNamespace(status_code=status))
                async def __aexit__(self, *_): pass
            def connector():
                nonlocal attempts
                attempts += 1
                return Denied()
            relay = OutputRelay(voice_id="synthetic_voice_id", connector=connector,
                                emit=self.emit, limits=self.limits)
            run = await relay.start(request_id=f"denied-{status}", text="safe test")
            await run.task
            self.assertEqual(attempts, 1)
            self.assertEqual(self.events[-1]["error_category"], category)
            self.assertEqual(self.events[-1]["request_outcome"], "not_dispatched")
            self.assertNotIn("connect_retry", [e["type"] for e in self.events])

    async def test_application_auth_and_protocol_errors_never_reconnect(self):
        for code, category in (("invalid_api_key", "authentication"),
                               ("invalid_request", "protocol")):
            self.events.clear()
            connections = 0
            async def provider(ws):
                nonlocal connections
                connections += 1
                await ws.recv(); await ws.recv()
                await ws.send(json.dumps({"error": {"code": code,
                                                     "message": "private provider detail"}}))
            async def action(relay):
                run = await relay.start(request_id=code, text="safe test")
                await run.task
                self.assertEqual(connections, 1)
                self.assertEqual(next(e for e in self.events if e["type"] == "failed")
                                 ["error_category"], category)
                self.assertNotIn("private provider detail", str(self.events))
                self.assertNotIn("connect_retry", [e["type"] for e in self.events])
            await self.run_server(provider, action)

    async def test_unknown_application_event_fails_closed(self):
        async def provider(ws):
            await ws.recv(); await ws.recv()
            await ws.send('{"new_provider_field":"unrecognized"}')
        async def action(relay):
            run = await relay.start(request_id="unknown", text="safe test")
            await run.task
            self.assertEqual(self.events[-1]["type"], "failed")
            self.assertEqual(self.events[-1]["error_category"], "protocol_unknown")
            self.assertEqual(self.events[-1]["request_outcome"], "unknown")
        await self.run_server(provider, action)

    async def test_remote_close_frame_and_no_frame_disconnect_are_distinct(self):
        for abrupt in (False, True):
            self.events.clear()
            async def provider(ws):
                await ws.recv(); await ws.recv()
                if abrupt: ws.transport.abort()
                else: await ws.close(code=1011)
            async def action(relay):
                run = await relay.start(request_id="close", text="safe test")
                await run.task
                failure = self.events[-1]
                self.assertEqual(failure["type"], "failed")
                self.assertEqual(failure["close_origin"],
                                 "transport_lost" if abrupt else "remote_close_frame")
                self.assertEqual(failure["remote_close_code"], None if abrupt else 1011)
                self.assertEqual(failure["request_outcome"], "unknown")
            await self.run_server(provider, action)

    async def test_cancel_and_switch_reject_old_late_audio(self):
        first_audio = asyncio.Event()
        async def provider(ws):
            await ws.recv()
            sent = json.loads(await ws.recv())
            await ws.send(json.dumps({"audio": PCM}))
            if sent["inputs"][0]["text"] == "first":
                first_audio.set()
                await asyncio.sleep(.25)
                try: await ws.send(json.dumps({"audio": PCM}))
                except Exception: pass
            else:
                await ws.send('{"is_final_audio_for_turn":true}')
                await ws.recv()
                await ws.send('{"is_final":true}')
        async def action(relay):
            first = await relay.start(request_id="old", text="first")
            await asyncio.wait_for(first_audio.wait(), .5)
            second = await relay.start(request_id="new", text="second")
            await second.task
            self.assertNotEqual(first.generation_id, second.generation_id)
            self.assertNotEqual(first.connection_id, second.connection_id)
            marker = next(i for i, e in enumerate(self.events) if
                          e["type"] == "started" and e["request_id"] == "new")
            self.assertFalse(any(e["type"] == "audio" and e["request_id"] == "old"
                                 for e in self.events[marker:]))
            self.assertTrue(any(e["type"] == "stopped" and e["request_id"] == "old"
                                and e["request_outcome"] == "unknown" for e in self.events))
        await self.run_server(provider, action)

    async def test_connect_retry_only_before_text_and_has_attempt_limit(self):
        connections = 0
        async def provider(ws):
            await ws.recv(); await ws.recv()
            await ws.send(json.dumps({"audio": PCM}))
            await ws.send('{"is_final_audio_for_turn":true}')
            await ws.recv(); await ws.send('{"is_final":true}')
        async with serve(provider, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            class FirstFailure:
                async def __aenter__(self): raise OSError("local connect failure")
                async def __aexit__(self, *_): pass
            def connector():
                nonlocal connections
                connections += 1
                return FirstFailure() if connections == 1 else connect(
                    f"ws://127.0.0.1:{port}", open_timeout=.2, close_timeout=.1)
            relay = OutputRelay(voice_id="synthetic_voice_id", connector=connector,
                                emit=self.emit, limits=self.limits)
            run = await relay.start(request_id="retry", text="safe test")
            await run.task
            self.assertEqual(connections, 2)
            self.assertEqual([e["type"] for e in self.events].count("connect_retry"), 1)
            self.assertEqual(self.events[-1]["type"], "completed")

    async def test_after_text_disconnect_never_replays(self):
        connections = 0
        async def provider(ws):
            nonlocal connections
            connections += 1
            await ws.recv(); await ws.recv()
            ws.transport.abort()
        async def action(relay):
            run = await relay.start(request_id="no-replay", text="safe test")
            await run.task
            self.assertEqual(connections, 1)
            self.assertEqual(self.events[-1]["request_outcome"], "unknown")
            self.assertNotIn("connect_retry", [e["type"] for e in self.events])
        await self.run_server(provider, action)

    async def test_invalid_pcm_and_timeout_release_resources(self):
        for malformed in (True, False):
            self.events.clear()
            async def provider(ws):
                await ws.recv(); await ws.recv()
                if malformed: await ws.send('{"audio":"@@@"}')
                else: await asyncio.sleep(.5)
            async def action(relay):
                relay.limits = OutputLimits(keepalive_seconds=.03, total_seconds=.12,
                                            close_wait_seconds=.05, connect_budget_seconds=.2)
                run = await relay.start(request_id="bad", text="safe test")
                await run.task
                self.assertEqual(self.events[-1]["type"], "failed")
                self.assertEqual(self.events[-1]["request_outcome"], "unknown")
                await relay.stop()
                self.assertIsNone(relay.current)
            await self.run_server(provider, action)


if __name__ == "__main__":
    unittest.main()
