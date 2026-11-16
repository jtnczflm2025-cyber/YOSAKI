"""Loopback-only V4 browser harness. Synthetic voice/audio; never imports main.py."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import struct
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from backend.app.voice.realtime_output import OutputLimits, OutputRelay


SAMPLES = {"normal": "これは隔離テストの合成音声です。",
           "keepalive": "保活之后播放这一段合成音频。",
           "late": "停止后不应播放迟到的片段。",
           "error": "模拟供应商错误，不应播放。",
           "remote_close": "模拟远端异常关闭。",
           "disconnect": "模拟没有关闭帧的断线。"}
SYNTHETIC_VOICE = "synthetic_voice_id"
PROVIDER_PORT = 18768
APP_PORT = 18767
RETAINED_SHA256 = "6a58d9fd2def3a7f259293f9fa400e30fa9690cf08cc4a48536f7cc7094330e4"
RETAINED_WAV = (Path(os.environ.get("LOCALAPPDATA", "")) / "YOSAKI" /
                "voice-v4-stream-runs" / "voice-v4-stream-20261111-r2" / "audio.wav")
retained_pcm: bytes | None = None
counts = {"provider_connections": 0, "text_dispatches": 0, "browser_starts": 0}


def load_retained_pcm() -> bytes:
    source = Path(os.environ.get("YOSAKI_V4_ISOLATED_WAV", str(RETAINED_WAV)))
    if hashlib.sha256(source.read_bytes()).hexdigest() != RETAINED_SHA256:
        raise ValueError("retained_audio_fingerprint_mismatch")
    with wave.open(str(source), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(),
                wav.getcomptype()) != (1, 2, 24000, "NONE"):
            raise ValueError("retained_audio_format_mismatch")
        return wav.readframes(wav.getnframes())


def pcm_tone(frequency: float = 440, seconds: float = .18) -> str:
    samples = round(24000 * seconds)
    pcm = b"".join(struct.pack("<h", round(3800 * math.sin(2 * math.pi * frequency * i / 24000)))
                   for i in range(samples))
    return base64.b64encode(pcm).decode("ascii")


async def fake_provider(ws) -> None:
    counts["provider_connections"] += 1
    scenario = parse_qs(urlsplit(ws.request.path).query).get("scenario", ["normal"])[0]
    try:
        registration = json.loads(await ws.recv())
        assert registration == {"voices": [SYNTHETIC_VOICE]}
        request = json.loads(await ws.recv())
        counts["text_dispatches"] += 1
        assert request.get("flush") is True and len(request.get("inputs", [])) == 1
        if scenario == "error":
            await ws.send(json.dumps({"error": {"code": "quota_exceeded"}}))
            await ws.close(code=1008)
            return
        if scenario == "remote_close":
            await ws.close(code=1011)
            return
        if scenario == "disconnect":
            ws.transport.abort()
            return
        if scenario == "keepalive":
            for _ in range(2):
                assert json.loads(await asyncio.wait_for(ws.recv(), 1)) == {"keep_alive": True}
        chunks = ([retained_pcm[offset:offset + 26000]
                   for offset in range(0, len(retained_pcm), 26000)]
                  if retained_pcm is not None else [])
        if not chunks:
            raise ValueError("retained_audio_missing")
        await asyncio.sleep(.12)
        for index, chunk in enumerate(chunks):
            await ws.send(json.dumps({"audio": base64.b64encode(chunk).decode("ascii")}))
            if scenario == "late" and index == 0:
                await asyncio.sleep(1.2)
            else:
                await asyncio.sleep(.04)
        await ws.send('{"is_final_audio_for_turn":true}')
        while True:
            control = json.loads(await asyncio.wait_for(ws.recv(), 2))
            if control == {"close_socket": True}:
                break
            assert control == {"keep_alive": True}
        await ws.send('{"is_final":true}')
    except (AssertionError, ConnectionError, ConnectionClosed, TimeoutError):
        pass


app = FastAPI()


@app.get("/isolation/status")
def isolation_status() -> dict[str, int]:
    return dict(counts)


@app.websocket("/ws/output")
async def output_socket(browser: WebSocket) -> None:
    if browser.headers.get("origin") != "http://127.0.0.1:15174":
        await browser.close(code=1008)
        return
    await browser.accept()
    last_audio: dict | None = None
    stale_audio: dict | None = None
    async def emit(event: dict) -> None:
        nonlocal last_audio, stale_audio
        if event.get("type") == "audio":
            if last_audio is not None and last_audio.get("generation_id") != event.get("generation_id"):
                stale_audio = last_audio
            last_audio = dict(event)
        await browser.send_json(event)
    relay = OutputRelay(voice_id=SYNTHETIC_VOICE,
                        connector=lambda: connect(f"ws://127.0.0.1:{PROVIDER_PORT}/?scenario=normal",
                                                  open_timeout=1, close_timeout=.2,
                                                  ping_interval=None),
                        emit=emit,
                        limits=OutputLimits(keepalive_seconds=.12, total_seconds=3,
                                            close_wait_seconds=.5, connect_budget_seconds=1,
                                            reconnect_attempts=1))
    try:
        while True:
            message = await browser.receive_json()
            if not isinstance(message, dict):
                continue
            if message.get("type") == "start":
                sample = message.get("sample_id")
                request_id = message.get("request_id")
                if sample not in SAMPLES or not isinstance(request_id, str) or len(request_id) > 100:
                    await browser.send_json({"type": "input_rejected"})
                    continue
                counts["browser_starts"] += 1
                relay.connector = lambda sample=sample: connect(
                    f"ws://127.0.0.1:{PROVIDER_PORT}/?scenario={sample}",
                    open_timeout=1, close_timeout=.2, ping_interval=None)
                await relay.start(request_id=request_id, text=SAMPLES[sample])
            elif message.get("type") == "stop":
                if relay.current and relay.current.request_id == message.get("request_id"):
                    await relay.stop()
            elif message.get("type") == "inject_stale" and (stale_audio or last_audio):
                # Deliberately replay an old, already observed synthetic frame.
                await browser.send_json(stale_audio or last_audio)
    except WebSocketDisconnect:
        pass
    finally:
        await relay.stop()


async def main() -> None:
    global retained_pcm
    retained_pcm = load_retained_pcm()
    async with serve(fake_provider, "127.0.0.1", PROVIDER_PORT):
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=APP_PORT,
                                              log_level="warning", access_log=False))
        await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
