"""Browser rehearsal of the final V4 entry with a loopback fake provider only."""

import asyncio
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from scripts import voice_v4_stream_probe as probe


FAKE_VOICE = "synthetic_voice_id"
SCENARIO = "normal"
RETAINED_SHA256 = "785ac803e4811122b0293edc79bd8d384d55bd1cfda09369e8e53cc9c3e8d599"


def retained_pcm() -> bytes:
    path = (Path(os.environ["LOCALAPPDATA"]) / "YOSAKI" / "voice-v4-stream-runs" /
            "voice-v4-stream-20261111-r1" / "audio.pcm.part")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != RETAINED_SHA256 or len(data) != 441600:
        raise ValueError("retained_pcm_changed")
    return data


def tone() -> str:
    raw = b"".join(struct.pack("<h", round(4500 * math.sin(2 * math.pi * 330 * i / 24000)))
                   for i in range(12000))
    return base64.b64encode(raw).decode("ascii")


async def fake_provider(ws) -> None:
    assert json.loads(await ws.recv()) == {"voices": [FAKE_VOICE]}
    payload = json.loads(await ws.recv())
    assert payload == {"inputs": [{"text": probe.FROZEN_TEXT,
                                   "voice_id": FAKE_VOICE}], "flush": True}
    if SCENARIO in ("retained-burst", "retained-slow"):
        data = retained_pcm()
        chunks = [data[offset:offset + 26000] for offset in range(0, len(data), 26000)]
        assert len(chunks) == 17
        interval = .04 if SCENARIO == "retained-burst" else .6
        for chunk in chunks:
            await ws.send(json.dumps({"audio": base64.b64encode(chunk).decode("ascii")}))
            await asyncio.sleep(interval)
        await ws.send('{"is_final_audio_for_turn":true}')
        assert json.loads(await ws.recv()) == {"close_socket": True}
        await ws.send('{"is_final":true}')
        return
    await ws.send(json.dumps({"audio": tone()}))
    if SCENARIO == "late":
        await asyncio.sleep(5)
    elif SCENARIO == "normal":
        await asyncio.sleep(.45)
    try:
        await ws.send(json.dumps({"audio": tone()}))
    except Exception:
        return
    await ws.send('{"is_final_audio_for_turn":true}')
    assert json.loads(await ws.recv()) == {"close_socket": True}
    await ws.send('{"is_final":true}')


async def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix="yosaki-v4-browser-sim-"))
    manifest = probe.load_manifest().copy()
    manifest["voice_id_sha256"] = hashlib.sha256(FAKE_VOICE.encode()).hexdigest()
    async with serve(fake_provider, "127.0.0.1", 0) as supplier:
        port = supplier.sockets[0].getsockname()[1]
        runtime = probe.ProbeRuntime(manifest, directory, FAKE_VOICE,
                                     lambda: connect(f"ws://127.0.0.1:{port}",
                                                     open_timeout=1, close_timeout=.2,
                                                     ping_interval=None),
                                     connection_seconds=20 if SCENARIO == "retained-slow" else 15)
        if SCENARIO == "invalid-client-audio":
            forward = runtime.emit
            async def invalid_sample_rate(event):
                await forward({**event, "sample_rate": 48000} if event["type"] == "audio"
                              else event)
            runtime.emit = invalid_sample_rate
        # The isolated browser may be opened manually after the fake listener
        # starts; this rehearsal wait is not the frozen real-run process cap.
        running = asyncio.create_task(probe.run_server(runtime, wall_seconds=60))
        entry = directory / "entry.url"
        while not entry.exists() and not running.done():
            await asyncio.sleep(.02)
        print("entry_file=" + str(entry), flush=True)
        print("result_file=" + str(directory / "result.json"), flush=True)
        await running
        print("status=" + runtime.state["status"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("normal", "late", "fast",
                                               "retained-burst", "retained-slow",
                                               "invalid-client-audio"), default="normal")
    SCENARIO = parser.parse_args().scenario
    asyncio.run(main())
