"""Controlled-delay local product-route interrupt check; no provider access."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import tempfile
import uuid

from fastapi.testclient import TestClient

from scripts.voice_continuous_isolated_acceptance import (
    BOUND_CONVERSATION_ID, ORIGIN, PASSWORD, prepare,
)


def transcribe(client: TestClient, call_id: str) -> dict:
    with client.websocket_connect("/api/voice/scribe-draft/ws",
                                  headers={"origin": ORIGIN}) as ws:
        ws.send_json({"type": "start", "character_id": 1,
                      "language_code": "auto", "attempt_id": uuid.uuid4().hex,
                      "call_id": call_id})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "audio", "pcm_base64":
            base64.b64encode(bytes([1]) * 3200).decode()})
        ws.send_json({"type": "commit"})
        result = ws.receive_json()
        assert result["type"] == "committed", result
        return result


def request(call_id: str, result: dict) -> dict:
    return {"character_id": 1, "conversation_id": BOUND_CONVERSATION_ID,
            "model_profile_id": 7, "message": result["text"],
            "voice_auto_read_requested": True, "voice_call_id": call_id,
            "voice_call_turn_id": result["call_turn_id"]}


async def cancel_during_model(main, client, fake_model, call_id: str, result: dict) -> None:
    response = await main.chat_stream(main.ChatInput(**request(call_id, result)))
    stream = response.body_iterator
    first = await anext(stream)
    assert "message_start" in first
    pending = asyncio.create_task(anext(stream))
    for _ in range(100):
        if fake_model.calls:
            break
        await asyncio.sleep(0.01)
    assert fake_model.calls == 1, "delayed fake model was not entered"
    interrupted = client.post(f"/api/voice/calls/{call_id}/interrupt", json={
        "phase": "waiting", "client_click_ms": 123.0,
        "client_audio_stop_ms": 123.3,
        "turn_id": result["call_turn_id"]})
    assert interrupted.status_code == 200, interrupted.text
    assert interrupted.json()["cancel_generation"] is True
    pending.cancel()
    try:
        await pending
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("model stream cancellation was not observed")
    await stream.aclose()


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="yosaki-interrupt-", dir=os.getenv("TEMP")) as temp:
        main, fake_scribe, fake_model, fake_tts = prepare(
            Path(temp), model_delay=1, tts_delay=1, daily=True,
            settings_schema=True)
        with TestClient(main.app) as client:
            assert client.post("/api/auth/login", json={
                "username": "synthetic_voice_owner", "password": PASSWORD}).status_code == 200
            started = client.post("/api/voice/calls", json={
                "character_id": 1, "conversation_id": BOUND_CONVERSATION_ID,
                "model_profile_id": 7, "acknowledge_auto_send": True})
            assert started.status_code == 200, started.text
            call_id = started.json()["call_id"]
            first = transcribe(client, call_id)
            asyncio.run(cancel_during_model(main, client, fake_model, call_id, first))
            with main.connect() as db:
                stopped = db.execute("SELECT id,generation_status FROM messages "
                    "WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                    (BOUND_CONVERSATION_ID,)).fetchone()
                assert stopped["generation_status"] == "stopped"
            second = transcribe(client, call_id)
            response = client.post("/api/chat/stream", json=request(call_id, second))
            assert response.status_code == 200, response.text
            completed = [json.loads(block.split("data: ", 1)[1].split("\n", 1)[0])
                for block in response.text.split("\n\n")
                if "event: message_complete" in block]
            assert len(completed) == 1, response.text
            message_id = completed[0]["message_id"]
            preview = client.get(f"/api/voice/messages/{message_id}/speech-preview")
            assert preview.status_code == 200
            speak = client.post(f"/api/voice/messages/{message_id}/auto-speak", json={
                "preview_sha256": preview.json()["preview_sha256"],
                "voice_call_id": call_id, "voice_call_turn_id": second["call_turn_id"]})
            assert speak.status_code == 202, speak.text
            # Simulate the playback button during delayed synthesis. Its old
            # result must never become current playback after interruption.
            interrupted = client.post(f"/api/voice/calls/{call_id}/interrupt", json={
                "phase": "playing", "client_click_ms": 456.0,
                "client_audio_stop_ms": 456.2,
                "turn_id": second["call_turn_id"]})
            assert interrupted.status_code == 200, interrupted.text
            assert interrupted.json()["cancel_generation"] is False
            assert main.app.state.voice_continuous.valid_tts(
                call_id, second["call_turn_id"], 1, message_id) is False
            assert fake_scribe.opens == 2 and fake_model.calls == 2
            assert fake_tts.attempts <= 1
            timeline = client.get(f"/api/voice/calls/{call_id}/timeline")
            assert timeline.status_code == 200, timeline.text
            events = timeline.json()["events"]
            names = [event["event"] for event in events]
            assert names.count("button_interrupt_received") == 2
            assert names.count("client_audio_stop_reported") == 2
            assert "model_cancel_requested" in names
            assert "model_stream_cancel_observed" in names
            assert names.count("stt_attempt_reserved") == 2
            assert names.index("model_stream_cancel_observed") < names.index("stt_attempt_reserved", 2)
            assert names.index("model_completed_saved") < names.index("tts_bound")
            assert names.index("tts_bound") < names.index("client_audio_stop_reported", names.index("tts_bound"))
            with main.connect() as db:
                rows = db.execute("SELECT role,generation_status FROM messages "
                    "WHERE conversation_id=? ORDER BY id", (BOUND_CONVERSATION_ID,)).fetchall()
                assert len(rows) == 4
                assert rows[1]["generation_status"] == "stopped"
                assert rows[3]["generation_status"] == "completed"
            assert client.delete(f"/api/voice/calls/{call_id}").status_code == 204
            return {"first_stopped_message": stopped["id"],
                    "second_completed_message": message_id,
                    "scribe_connections": fake_scribe.opens,
                    "model_requests": fake_model.calls,
                    "late_audio_rejected": True,
                    "timeline_event_count": len(events),
                    "interrupt_phases": [event["phase"] for event in events
                                         if event["event"] == "button_interrupt_received"]}


if __name__ == "__main__":
    print(json.dumps(run()))
