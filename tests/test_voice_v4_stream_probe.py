"""Final one-use entry exercised only against a local fake TTD WebSocket."""

import asyncio
import base64
from contextlib import closing, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from backend.app.voice.realtime_ttd_connector import RealTTDConnector
from backend.app.voice.realtime_output import OutputRun
from scripts import voice_v4_stream_probe as probe


FAKE_VOICE = "synthetic_voice_id"
FAKE_KEY = "synthetic-key-never-log"
PCM = base64.b64encode(struct.pack("<hhhh", 0, 1000, -1000, 0)).decode()


def synthetic_database(path: Path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE voice_profiles(character_id INTEGER,provider TEXT,"
                   "voice_id TEXT,revision INTEGER,active INTEGER)")
        db.execute("CREATE TABLE voice_privacy_consents(provider TEXT,notice_version TEXT,"
                   "accepted_at TEXT)")
        db.execute("INSERT INTO voice_profiles VALUES(1,'elevenlabs',?,3,1)", (FAKE_VOICE,))
        db.execute("INSERT INTO voice_privacy_consents VALUES('elevenlabs',?,?)",
                   (probe.NOTICE_VERSION, "synthetic_timestamp"))


def synthetic_manifest():
    m = probe.load_manifest().copy()
    m["voice_id_sha256"] = hashlib.sha256(FAKE_VOICE.encode()).hexdigest()
    return m


def make_receipt(root: Path, manifest: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / f"{probe.RUN_ID}.approval.json"
    receipt.write_text(json.dumps({
        "schema_version": 1, "run_id": probe.RUN_ID,
        "manifest_sha256": probe.digest(manifest),
        "code_set_sha256": probe.code_set_sha256(),
        "voice_id_sha256": manifest["voice_id_sha256"],
        "profile_revision": 3, "notice_version": probe.NOTICE_VERSION,
        "limits": manifest["limits"], "network_proxy_url": None,
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
    }), encoding="utf-8")
    return receipt


class StreamGateTests(unittest.TestCase):
    def test_legacy_queue_guard_exceeded_by_simulated_retained_burst(self):
        # Retained r1 PCM is 441,600 bytes (9.2 s). The r1 file lacks exact
        # per-chunk sizes/times, so this is explicitly a simulated 17-frame
        # projection, not a reconstruction of the supplier schedule.
        durations = [26000 / 48000] * 16 + [25600 / 48000]
        next_start = 0.0
        first_excess = None
        for index, duration in enumerate(durations):
            arrival = index * .04
            projected_ahead = max(next_start, arrival) + duration - arrival
            if projected_ahead > 8 and first_excess is None:
                first_excess = (index + 1, projected_ahead)
            next_start = max(next_start, arrival) + duration
        self.assertEqual(sum(durations), 9.2)
        self.assertEqual(first_excess[0], 16)
        self.assertGreater(first_excess[1], 8)

    def test_dry_run_is_zero_network_without_profile_or_key(self):
        with patch.object(probe, "read_profile", side_effect=AssertionError("database read")), \
             patch.object(probe, "network_proxy", side_effect=AssertionError("proxy read")):
            out = io.StringIO()
            from contextlib import redirect_stdout
            with redirect_stdout(out):
                self.assertEqual(probe.main([]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["real_requests"], 0)
        self.assertEqual(result["limits"]["provider_ws_connections"], 1)
        self.assertEqual(result["limits"]["safe_pre_text_connect_retries"], 0)

    def test_real_connector_frozen_url_and_header_without_dispatch(self):
        url = RealTTDConnector.url()
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(urlsplit(url).hostname, "api.elevenlabs.io")
        self.assertEqual(query, {"model_id": ["eleven_v4_turbo"],
                                 "output_format": ["pcm_24000"],
                                 "language_code": ["ja"]})
        with patch("backend.app.voice.realtime_ttd_connector.connect") as factory:
            RealTTDConnector(FAKE_KEY, None)()
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(factory.call_args.kwargs["additional_headers"],
                             {"xi-api-key": FAKE_KEY})
            self.assertIsNone(factory.call_args.kwargs["proxy"])
        self.assertNotIn(FAKE_KEY, repr(RealTTDConnector(FAKE_KEY, None)))

    def test_receipt_mismatch_and_duplicate_rejected_before_network(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt = make_receipt(root, manifest)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            for field, bad in (("manifest_sha256", "old"),
                               ("code_set_sha256", "old"),
                               ("third_party_processing_confirmed_for_this_run", False),
                               ("billing_uncertainty_acknowledged", False),
                               ("voice_id_sha256", "old")):
                with self.subTest(field=field), self.assertRaises(probe.GateError):
                    probe.verify_receipt(dict(value, **{field: bad}), manifest,
                                         probe.code_set_sha256(), None)
            result = probe.consume_receipt(receipt, root, manifest,
                                           probe.code_set_sha256(), None)
            self.assertTrue((root / (receipt.name + ".used")).exists())
            self.assertTrue(result.is_dir())
            with self.assertRaises(probe.GateError):
                probe.consume_receipt(receipt, root, manifest,
                                      probe.code_set_sha256(), None)
            result.rmdir()
            with self.assertRaises(probe.GateError):
                probe.consume_receipt(receipt, root, manifest,
                                      probe.code_set_sha256(), None)

    def test_final_worker_invalid_receipt_never_constructs_provider(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "runs"
            db = Path(d) / "synthetic.sqlite3"
            synthetic_database(db)
            receipt = make_receipt(root, manifest)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["code_set_sha256"] = "stale"
            receipt.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(probe, "DATABASE", db), \
                 patch.object(probe, "load_manifest", return_value=manifest), \
                 patch.object(probe, "result_root", return_value=root), \
                 patch.object(probe, "network_proxy", return_value=None), \
                 patch.object(probe, "RealTTDConnector", side_effect=AssertionError("supplier")), \
                 patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}), \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(probe.worker(receipt), 2)
            self.assertFalse((root / (receipt.name + ".used")).exists())
            self.assertFalse((root / probe.RUN_ID).exists())

    def test_profile_gate_read_only_and_revocation(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "synthetic.sqlite3"
            synthetic_database(db)
            with patch.object(probe, "DATABASE", db):
                self.assertEqual(probe.read_profile(db, manifest), FAKE_VOICE)
                with closing(sqlite3.connect(db)) as conn, conn:
                    conn.execute("DELETE FROM voice_privacy_consents")
                with self.assertRaises(probe.GateError):
                    probe.read_profile(db, manifest)

    def test_hard_process_timeout_is_bounded_and_not_retried(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(probe, "result_root", return_value=Path(d)), \
             patch.object(probe.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(["python"], 50)) as runner:
            with redirect_stderr(io.StringIO()):
                result = probe.main(["--execute-real", "--approval-file", "unused"])
            self.assertEqual(result, 2)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(runner.call_args.kwargs["timeout"], 50)


class StreamEntryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, scenario: str):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "runs"
            db = Path(d) / "synthetic.sqlite3"
            synthetic_database(db)
            manifest = synthetic_manifest()
            receipt = make_receipt(root, manifest)
            connections = 0

            async def provider(ws):
                nonlocal connections
                connections += 1
                self.assertEqual(json.loads(await ws.recv()), {"voices": [FAKE_VOICE]})
                sent = json.loads(await ws.recv())
                self.assertEqual(sent, {"inputs": [{"text": manifest["text"],
                                                    "voice_id": FAKE_VOICE}], "flush": True})
                if scenario == "remote_close":
                    await ws.close(code=1011)
                    return
                if scenario == "abrupt":
                    ws.transport.abort()
                    return
                await ws.send(json.dumps({"audio": PCM}))
                if scenario == "cancel":
                    await asyncio.sleep(.4)
                    try: await ws.send(json.dumps({"audio": PCM}))
                    except Exception: pass
                    return
                await asyncio.sleep(.06)
                await ws.send(json.dumps({"audio": PCM}))
                await ws.send('{"is_final_audio_for_turn":true}')
                self.assertEqual(json.loads(await ws.recv()), {"close_socket": True})
                await ws.send('{"is_final":true}')

            async with serve(provider, "127.0.0.1", 0) as fake:
                port = fake.sockets[0].getsockname()[1]
                def fake_connector(_key, _proxy):
                    return lambda: connect(f"ws://127.0.0.1:{port}",
                                           open_timeout=1, close_timeout=.1,
                                           ping_interval=None)

                with patch.object(probe, "DATABASE", db), \
                     patch.object(probe, "load_manifest", return_value=manifest), \
                     patch.object(probe, "result_root", return_value=root), \
                     patch.object(probe, "network_proxy", return_value=None), \
                     patch.object(probe, "RealTTDConnector", side_effect=fake_connector), \
                     patch.dict(os.environ, {"ELEVENLABS_API_KEY": FAKE_KEY}):
                    worker_task = asyncio.create_task(asyncio.to_thread(probe.worker, receipt))
                    entry = root / probe.RUN_ID / "entry.url"
                    for _ in range(100):
                        if entry.is_file(): break
                        await asyncio.sleep(.02)
                    self.assertTrue(entry.is_file())
                    ticket = parse_qs(urlsplit(entry.read_text(encoding="utf-8")).fragment)["ticket"][0]
                    async with connect(f"ws://127.0.0.1:{probe.PORT}/ws/output",
                                       origin=probe.ORIGIN) as browser:
                        request_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
                        await browser.send(json.dumps({"type": "start", "sample_id": "frozen",
                                                       "browser_ticket": ticket,
                                                       "request_id": request_id}))
                        events = []
                        while True:
                            event = json.loads(await asyncio.wait_for(browser.recv(), 2))
                            events.append(event)
                            if event["type"] == "audio" and scenario == "cancel":
                                await browser.send(json.dumps({"type": "stop",
                                                               "request_id": request_id}))
                            if event["type"] in ("completed", "failed", "stopped"):
                                if event["type"] == "completed":
                                    for name, ms in (("first_audio_received", 100),
                                                     ("first_playback_started", 110),
                                                     ("receive_completed", 200),
                                                     ("playback_ended", 400)):
                                        await browser.send(json.dumps({
                                            "type": "metric", "name": name,
                                            "client_ms": ms, "request_id": request_id}))
                                break
                        if scenario == "completed":
                            await asyncio.sleep(.08)
                    code = await asyncio.wait_for(worker_task, 3)
                    duplicate = await asyncio.to_thread(probe.worker, receipt)
                    self.assertEqual(duplicate, 2)
                    result = json.loads((root / probe.RUN_ID / "result.json").read_text(encoding="utf-8"))
                    self.assertEqual(connections, 1)
                    self.assertEqual(result["usage"]["provider_http_requests"], 0)
                    self.assertEqual(result["usage"]["provider_ws_connections"], 1)
                    self.assertEqual(result["usage"]["provider_text_dispatches"], 1)
                    self.assertEqual(result["usage"]["tts_characters_dispatched"], 52)
                    self.assertNotIn(FAKE_KEY, json.dumps(result))
                    self.assertNotIn(FAKE_VOICE, json.dumps(result))
                    return code, result, events, receipt

    async def test_final_entry_one_connection_and_browser_metrics(self):
        code, result, events, _ = await self.exercise("completed")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "streaming_observed")
        self.assertEqual(result["streaming_observation"],
                         "playback_started_before_receive_complete")
        self.assertTrue(any(e["type"] == "audio" for e in events))
        self.assertTrue(result["audio_file"].endswith(".wav"))
        markers = result["phase_markers"]
        self.assertLess(markers["final_turn"], markers["final_protocol_event"])
        self.assertLess(markers["final_protocol_event"], markers["transport_closed"])
        self.assertLessEqual(markers["transport_closed"], markers["receive_completed"])
        kinds = [event["event"] for event in result["events"]]
        self.assertLess(kinds.index("transport_closed"), kinds.index("receive_completed"))
        self.assertIn("playback_queue_drained", markers)

    async def test_browser_playback_diagnostic_is_bounded_and_bound_to_run(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = probe.ProbeRuntime(synthetic_manifest(), Path(d), FAKE_VOICE,
                                         lambda: None)
            run = OutputRun(request_id="r", generation_id="g", segment_id="s", epoch=5,
                            connection_id="c")
            runtime.relay = SimpleNamespace(session_id="session", current=run)
            message = {"code": "playback_queue_full", "request_id": "r",
                       "session_id": "session", "generation_id": "g", "segment_id": "s",
                       "connection_id": "c", "connection_epoch": 5,
                       "pending_audio_bytes": 0, "incoming_audio_bytes": 25600,
                       "scheduled_ahead_seconds": 7.9, "scheduled_sources": 17,
                       "free_text": "must not be logged"}
            runtime.accept_playback_error(message)
            self.assertEqual(runtime.state["browser_diagnostics"][0]["code"],
                             "playback_queue_full")
            self.assertEqual(runtime.state["status"], "playback_failed")
            self.assertTrue(runtime.done.is_set())
            self.assertNotIn("must not be logged", str(runtime.state))
            runtime.accept_playback_error(dict(message, connection_epoch=4))
            runtime.accept_playback_error(dict(message, code="secret arbitrary error"))
            self.assertEqual(len(runtime.state["browser_diagnostics"]), 1)

    async def test_final_entry_abnormal_close_stops_without_retry(self):
        code, result, events, _ = await self.exercise("remote_close")
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["request_outcome"], "unknown")
        self.assertTrue(any(e["type"] == "failed" and
                            e["close_origin"] == "remote_close_frame" for e in events))

    async def test_final_entry_no_close_frame_stops_without_retry(self):
        code, result, events, _ = await self.exercise("abrupt")
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["request_outcome"], "unknown")
        self.assertTrue(any(e["type"] == "failed" and
                            e["close_origin"] == "transport_lost" for e in events))

    async def test_final_entry_cancel_preserves_partial_and_drops_late_audio(self):
        code, result, events, _ = await self.exercise("cancel")
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["request_outcome"], "unknown")
        self.assertEqual(result["usage"]["audio_bytes_received"], 8)
        self.assertEqual([e["type"] for e in events].count("audio"), 1)

    async def test_process_deadline_without_browser_does_not_connect(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runtime = probe.ProbeRuntime(manifest, root, FAKE_VOICE,
                                         lambda: (_ for _ in ()).throw(AssertionError("network")))
            result = await probe.run_server(runtime, wall_seconds=.2)
            self.assertEqual(result["status"], "process_timeout")
            self.assertEqual(result["usage"]["provider_ws_connections"], 0)
            runtime.connector = lambda: object()
            runtime.open_provider()
            with self.assertRaises(probe.GateError):
                runtime.open_provider()

    async def test_playback_after_receive_complete_is_not_called_streaming(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            runtime = probe.ProbeRuntime(manifest, Path(d), FAKE_VOICE, lambda: None)
            runtime.state["request_outcome"] = "completed"
            runtime.relay = type("Relay", (), {"current": type("Run", (), {"request_id": "r"})()})()
            for name, value in (("first_audio_received", 50),
                                ("receive_completed", 100),
                                ("first_playback_started", 200),
                                ("playback_ended", 300)):
                runtime.accept_metric({"name": name, "client_ms": value, "request_id": "r"})
            self.assertEqual(runtime.state["streaming_observation"],
                             "playback_started_after_receive_complete")
            self.assertEqual(runtime.state["status"],
                             "playback_started_after_receive_complete")
            self.assertTrue(runtime.done.is_set())

    async def test_missing_first_sample_signal_is_unverified(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            runtime = probe.ProbeRuntime(manifest, Path(d), FAKE_VOICE, lambda: None)
            runtime.state["request_outcome"] = "completed"
            runtime.relay = type("Relay", (), {"current": type("Run", (), {"request_id": "r"})()})()
            for name, value in (("first_audio_received", 100),
                                ("receive_completed", 200),
                                ("playback_ended", 300)):
                runtime.accept_metric({"name": name, "client_ms": value, "request_id": "r"})
            self.assertEqual(runtime.state["status"], "playback_timing_unverified")
            self.assertEqual(runtime.state["streaming_observation"], "unverified")

    async def test_final_entry_rejects_wrong_browser_ticket_before_provider(self):
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as d:
            runtime = probe.ProbeRuntime(manifest, Path(d), FAKE_VOICE,
                                         lambda: (_ for _ in ()).throw(AssertionError("network")))
            running = asyncio.create_task(probe.run_server(runtime, wall_seconds=1))
            entry = Path(d) / "entry.url"
            for _ in range(50):
                if entry.is_file(): break
                await asyncio.sleep(.02)
            async with connect(f"ws://127.0.0.1:{probe.PORT}/ws/output",
                               origin=probe.ORIGIN) as browser:
                await browser.send(json.dumps({"type": "start", "sample_id": "frozen",
                                               "browser_ticket": "wrong",
                                               "request_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}))
                try: await browser.recv()
                except Exception: pass
            result = await asyncio.wait_for(running, 2)
            self.assertEqual(result["status"], "browser_entry_rejected")
            self.assertEqual(result["usage"]["provider_ws_connections"], 0)

    async def test_final_entry_rejects_second_text_and_audio_over_cap(self):
        manifest = synthetic_manifest()
        manifest["limits"] = dict(manifest["limits"], max_audio_bytes=8)
        with tempfile.TemporaryDirectory() as d:
            runtime = probe.ProbeRuntime(manifest, Path(d), FAKE_VOICE, lambda: None)
            await runtime.emit({"type": "text_dispatch_attempt", "characters": 52})
            with self.assertRaises(probe.GateError):
                await runtime.emit({"type": "text_dispatch_attempt", "characters": 52})
            with self.assertRaises(probe.GateError):
                await runtime.emit({"type": "audio", "pcm_base64": base64.b64encode(b"\0" * 10).decode()})
            self.assertEqual(runtime.state["usage"]["provider_text_dispatches"], 1)
            self.assertEqual(runtime.state["usage"]["audio_bytes_received"], 0)


if __name__ == "__main__":
    unittest.main()
