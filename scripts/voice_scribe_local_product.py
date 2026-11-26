"""Temporary product UI + local-only Scribe supplier for browser QA.

No real provider endpoint or daily database is reachable through this script.
"""

from __future__ import annotations

import argparse
import asyncio
from array import array
import base64
import json
import math
import os
from pathlib import Path
import tempfile
import wave

import websockets


def fixture_similarity(reference: bytes, candidate: bytes) -> float:
    def envelope(pcm: bytes) -> list[float]:
        samples = array("h")
        samples.frombytes(pcm)
        return [sum(abs(value) for value in samples[i:i+320]) / 320
                for i in range(0, len(samples)-319, 320)]
    expected, actual = envelope(reference), envelope(candidate)
    best = 0.0
    for shift in range(-25, 26):  # up to 0.5 s capture padding
        pairs = [(expected[i], actual[i+shift]) for i in range(len(expected))
                 if 0 <= i+shift < len(actual)]
        if len(pairs) < 240:
            continue
        top = sum(left*right for left, right in pairs)
        base = math.sqrt(sum(left*left for left, _ in pairs) *
                         sum(right*right for _, right in pairs))
        if base:
            best = max(best, top/base)
    return best


async def serve(app, port: int, counts: dict, fixture_pcm: bytes) -> None:
    async def supplier(ws):
        counts["upstream_connections"] += 1
        await ws.send(json.dumps({"message_type": "session_started"}))
        audio = bytearray()
        async for raw in ws:
            item = json.loads(raw)
            if item.get("message_type") != "input_audio_chunk":
                await ws.close(code=1002)
                return
            chunk = base64.b64decode(item["audio_base_64"], validate=True)
            audio.extend(chunk)
            counts["upstream_chunks"] += 1
            if item.get("commit"):
                counts["commits"] += 1
                counts["audio_bytes"] = len(audio)
                counts["nonzero_bytes"] = sum(value != 0 for value in audio)
                counts["fixture_similarity"] = round(fixture_similarity(fixture_pcm, audio), 4)
                # A genuine browser capture of the 5.805 s fixture should
                # reach this threshold. No audio, brief audio, or silence cannot
                # get a fake success transcript.
                if (len(audio) >= 170_000 and counts["nonzero_bytes"] > 10_000
                        and counts["fixture_similarity"] >= .85):
                    await ws.send(json.dumps({"message_type": "committed_transcript",
                                              "text": "本地音频夹具已通过完整链路"}))
                else:
                    await ws.send(json.dumps({"message_type": "committed_transcript",
                                              "text": ""}))

    async with websockets.serve(supplier, "127.0.0.1", 0) as supplier_server:
        supplier_port = supplier_server.sockets[0].getsockname()[1]
        from backend.app.voice.scribe_adapter import ScribeRealtimeAdapter
        app.state.voice_scribe_draft.provider_factory = (
            lambda _owner, _character, language: ScribeRealtimeAdapter(
                language, endpoint=f"ws://127.0.0.1:{supplier_port}/v1/speech-to-text/realtime",
                key="local-fake-only", proxy=None))
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               access_log=False, log_level="warning",
                                               lifespan="on", workers=1))
        try:
            await server.serve()
        finally:
            print(json.dumps(counts), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18796)
    parser.add_argument("--frontend-port", type=int, default=15196)
    parser.add_argument("--daily", action="store_true",
                        help="exercise opt-in daily quota on an isolated database")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="yosaki-scribe-local-") as directory:
        trace_dir = Path(directory) / "trace"
        trace_dir.mkdir()
        os.environ.update({
            "YOSAKI_DATA_DIR": directory,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_SCRIBE_DRAFT_ISOLATED": "0" if args.daily else "1",
            "YOSAKI_SCRIBE_DAILY_ENABLED": "1" if args.daily else "0",
            "YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED": "0",
            "YOSAKI_SCRIBE_DRAFT_DIAGNOSTICS_DIR": str(trace_dir),
            "YOSAKI_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.frontend_port}",
            "YOSAKI_CHATGPT_PLAN_ENABLED": "0",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1" if args.daily else "0",
            "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1" if args.daily else "0",
            "ELEVENLABS_API_KEY": "local-fake-only",
        })
        import httpx
        from backend.app.voice import router
        from backend.app.voice.audio_jobs import VoiceJobs
        from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
        counts = {"upstream_connections": 0, "upstream_chunks": 0,
                  "commits": 0, "audio_bytes": 0, "nonzero_bytes": 0,
                  "fixture_similarity": None, "mock_ttd_posts": 0}
        frame = b"\xff\xfb\x90\x64" + bytes(413)
        def fake_ttd(_request):
            counts["mock_ttd_posts"] += 1
            return httpx.Response(200, content=frame * 300)
        local_transport = httpx.MockTransport(fake_ttd)
        router.VoiceJobs = lambda connect: VoiceJobs(connect, adapter_factory=lambda key:
            ElevenLabsAdapter(key, local_transport))
        from backend.app import main as app_module
        from fastapi.responses import FileResponse
        fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/voice_v1b_scribe_zh.wav"
        with wave.open(str(fixture), "rb") as wav:
            if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1,16000,2):
                raise RuntimeError("fixture_format_invalid")
            fixture_pcm = wav.readframes(wav.getnframes())

        @app_module.app.get("/api/voice/scribe-draft/fixture.wav")
        def local_fixture():
            return FileResponse(fixture, media_type="audio/wav")

        @app_module.app.get("/api/voice/scribe-draft/volume-check")
        def local_volume_check():
            page = Path(__file__).with_name("scribe_local_volume_check.html")
            return FileResponse(page, media_type="text/html")

        from backend.app.voice import auto_budget, storage
        from backend.app.voice.schemas import ProfileInput
        from fastapi.testclient import TestClient
        with TestClient(app_module.app) as client:
            response = client.post("/api/auth/setup", json={
                "username": "scribe_local_owner",
                "password": "local-only-password-2026",
            })
            if response.status_code != 201:
                raise RuntimeError("local_setup_failed")
        with app_module.connect() as db:
            if args.daily:
                from backend.app.voice import scribe_budget
                scribe_budget.init_db(db)
                auto_budget.init_db(db)
                auto_budget.init_daily_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(
                expected_revision=0, voice_id="local-fake-voice", active=True))
        print(json.dumps({"mode": "local_only_scribe_product", "port": args.port,
                          "frontend_port": args.frontend_port,
                          "database_path": str(app_module.DB.resolve()),
                          "diagnostics_dir": str(trace_dir), "supplier_requests": 0}), flush=True)
        asyncio.run(serve(app_module.app, args.port, counts, fixture_pcm))


if __name__ == "__main__":
    main()
