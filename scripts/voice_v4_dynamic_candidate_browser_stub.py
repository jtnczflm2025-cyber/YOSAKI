"""Isolated browser rehearsal of real_approved UI with zero outbound capability.

The product route, receipt checks, and Vue component are real. Model, token,
and TTD connectors are replaced inside this process before the server starts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import httpx


TEXT = "今夜は、ゆっくりでいい。ここで、君の話を聞いている。"
PROMPT = "阿夜、今夜はゆっくりでいいと短く答えて。"


class FakeSupplier:
    local_fake_provider = True

    def __init__(self, scenario: str = "normal"):
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.scenario = scenario

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, raw: str) -> None:
        import base64
        value = json.loads(raw)
        if "inputs" in value:
            if self.scenario == "voice_failure":
                await self.queue.put('{"error":{"code":"quota_exceeded"}}')
                return
            pcm = base64.b64encode(b"\0\0\x10\x27" * 1200).decode()
            await self.queue.put(json.dumps({"audio": pcm,
                "is_final_audio_for_turn": True}))
        if value.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self) -> str:
        if self.scenario == "unknown_event":
            await asyncio.sleep(.4)
        return await self.queue.get()


class NoNetworkConnector:
    scenario = "normal"
    def __init__(self, _key: str, _proxy: str | None):
        pass

    def __call__(self) -> FakeSupplier:
        return FakeSupplier(self.scenario)


class ChunkedSSE(httpx.AsyncByteStream):
    """Synthetic wire bytes; every third byte may split a UTF-8 character."""

    def __init__(self, frames: list[bytes], pause_after: int | None):
        self.frames = frames
        self.pause_after = pause_after

    async def __aiter__(self):
        for index, frame in enumerate(self.frames):
            for offset in range(0, len(frame), 3):
                yield frame[offset:offset + 3]
            if index == self.pause_after:
                await asyncio.sleep(.12)


def sse(event: dict) -> bytes:
    return (f"event: {event['type']}\n"
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n").encode("utf-8")


async def fake_model(_frozen):
    from backend.app import responses_adapter

    item = {"type": "message", "id": "msg-browser-fixture",
            "role": "assistant", "phase": "final_answer"}
    frames = [sse({"type": "response.created"}),
              sse({"type": "response.output_item.added", "item": item}),
              sse({"type": "response.output_text.delta", "item_id": item["id"],
                   "delta": TEXT[:13]})]
    scenario = NoNetworkConnector.scenario
    if scenario == "unknown_event":
        frames.append(sse({"type": "response.future_notice",
                           "payload": "private synthetic field"}))
    else:
        if scenario in ("missing", "ancillary_missing", "observed_optional"):
            moderation = {"presentation": "inline"}
        elif scenario == "explicit_null":
            moderation = {"final_answer_moderation": None, "presentation": "inline"}
        elif scenario == "invalid":
            moderation = {"final_answer_moderation": []}
        else:
            display = ({"mode": "hide", "version": 1,
                        "replacement": {"kind": "wellbeingPromptHelpline"}}
                       if scenario in ("hide", "ancillary_hide", "observed_hide") else
                       {"mode": "block", "version": 1, "reason": {"kind": "cyber"}}
                       if scenario == "block" else
                       {"mode": "show", "version": 1,
                        "trustedContacts": "skip"})
            moderation = {"final_answer_moderation": {
                "item_id": item["id"], "moderated": True, "display": display}}
        if scenario in ("ancillary_missing", "ancillary_hide"):
            moderation["generation"] = {"private": "synthetic classifier detail"}
        if scenario in ("observed_optional", "observed_hide"):
            moderation.update({name: {"private": "synthetic detail"} for name in
                               ("prompt", "tool_call", "tool_response", "generation")})
        frames.extend([
            sse({"type": "response.metadata", "metadata": {}}),
            sse({"type": "response.reasoning_summary_part.added",
                 "part": {"type": "summary_text", "text": "private synthetic reasoning"}}),
            sse({"type": "response.output_text.delta", "item_id": item["id"],
                 "delta": TEXT[13:]}),
            sse({"type": "response.metadata", "metadata": {
                "openai_chatgpt_moderation_metadata": moderation}}),
            sse({"type": "response.completed", "response": {"output": [{**item,
                "content": [{"type": "output_text", "text": TEXT}]}],
                "usage": {"input_tokens": 15, "output_tokens": 15}}}),
            b"data: [DONE]\n\n",
        ])
    response = httpx.Response(200, stream=ChunkedSSE(frames, 1),
                              request=httpx.Request("POST", "http://synthetic.invalid/responses"))
    try:
        async for event in responses_adapter.iter_sse_events(response.aiter_lines()):
            yield event
    finally:
        await response.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18788)
    parser.add_argument("--frontend-port", type=int, default=15189)
    parser.add_argument("--scenario", choices=("normal", "missing", "explicit_null",
                        "invalid", "hide", "block", "ancillary_missing", "ancillary_hide",
                        "voice_failure", "unknown_event", "observed_optional",
                        "observed_hide"),
                        default="normal")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    NoNetworkConnector.scenario = args.scenario
    temporary = tempfile.TemporaryDirectory(prefix="yosaki-v4-real-ui-stub-")
    root = Path(temporary.name)
    receipts_dir = root / "receipts"
    receipts_dir.mkdir()
    os.environ.update({
        "YOSAKI_DATA_DIR": str(root / "data"),
        "YOSAKI_V4_SOURCE_DATA_DIR": str(root),
        "YOSAKI_VOICE_ENABLED": "1",
        "YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED": "1",
        "YOSAKI_V4_DYNAMIC_REAL_EXECUTE": "1",
        "YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.frontend_port}",
        "YOSAKI_CHATGPT_PLAN_ENABLED": "0",
        "ELEVENLABS_API_KEY": "synthetic-local-only",
    })
    from fastapi.testclient import TestClient
    from backend.app import main as app_module
    from backend.app import chatgpt_plan
    from backend.app.voice import realtime_dynamic_candidate as candidate
    from backend.app.voice import realtime_dynamic_candidate_receipt as receipts
    from backend.app.voice import storage
    from backend.app.voice.realtime_dynamic_context import freeze
    from backend.app.voice.schemas import ProfileInput, VoiceConfiguration

    with TestClient(app_module.app) as client:
        setup = client.post("/api/auth/setup", json={
            "username": "browser_stub_owner",
            "password": "synthetic-browser-only-2026"})
        if setup.status_code != 201:
            raise SystemExit("synthetic_setup_failed")
    with app_module.connect() as db:
        db.execute("INSERT INTO model_profiles(id,name,provider_name,base_url,model_name,"
            "protocol,temperature,top_p,max_tokens,context_limit,supports_streaming,enabled,"
            "created_at,updated_at) VALUES (5,'browser stub','synthetic',"
            "'https://api.openai.com/v1','gpt-6.1-sol','chatgpt_plan',0.8,1.0,2048,"
            "128000,1,1,'now','now')")
        db.execute("INSERT INTO conversations(id,character_id,title,model_profile_id,"
            "created_at,updated_at) VALUES (5,1,'browser stub',5,'now','now')")
        storage.put_consent(db, 1)
        db.commit()
        storage.put_profile(db, 1, ProfileInput(expected_revision=0,
            voice_id="synthetic_browser_voice", active=True,
            configuration=VoiceConfiguration(language="ja", audio_format="pcm_24000")))
        frozen = freeze(db, owner_id=1, character_id=1, conversation_id=5,
            model_profile_id=5, prompt=PROMPT,
            max_output_tokens=receipts.LIMITS["max_output_tokens"],
            build_messages=app_module.build_messages,
            plan_payload=chatgpt_plan.payload)
        profile = storage.profile(db, 1)
        consent = storage.consent(db, 1)
    run_id = "browser-real-stub-" + uuid.uuid4().hex[:16]
    manifest = {"schema_version": 1, "mode": "real_dynamic_candidate",
        "run_id": run_id, "generation_id": uuid.uuid4().hex,
        "owner_id": 1, "character_id": 1, "conversation_id": 5,
        "model_profile_id": 5, "context_sha256": frozen.context_sha256,
        "prompt": PROMPT, "profile_revision": profile["revision"],
        "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
        "notice_version": consent["notice_version"],
        "chat_provider": "chatgpt_plan", "chat_model_id": "gpt-6.1-sol",
        "tts_model_id": "eleven_v4_turbo", "output_format": "pcm_24000",
        "language_code": "ja", "network_proxy_url": candidate.network_proxy(),
        "limits": receipts.LIMITS}
    receipts.issue_mock(receipts_dir, manifest)
    path = receipts_dir / f"{run_id}.approval.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["provider_kind"] = "real_approved"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    gate = app_module.app.state.voice_v4_candidate
    gate.receipt_dir = receipts_dir
    gate.run_id = run_id
    gate.model_events_factory = None
    gate.ttd_connector_factory = None
    gate.real_model_events = fake_model
    candidate._read_only_plan_token = lambda _path: "synthetic-not-sent"
    candidate.RealTTDConnector = NoNetworkConnector
    print(json.dumps({"backend_port": args.port,
        "frontend_port": args.frontend_port, "run_id": run_id,
        "scenario": args.scenario, "receipt_dir": str(receipts_dir),
        "isolated_database": str(app_module.DB.resolve()),
        "provider_network_capability": False}), flush=True)
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1",
        port=args.port, log_level="warning", access_log=False, workers=1))

    @app_module.app.post("/__test/shutdown")
    async def shutdown():
        # This test-only process binds loopback and has no provider capability.
        server.should_exit = True
        return {"stopping": True}

    try:
        server.run()
    finally:
        if args.evidence_dir:
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            path = receipts_dir / f"{run_id}.diagnostic.jsonl"
            if path.exists():
                shutil.copy2(path, args.evidence_dir /
                    f"{args.scenario}.diagnostic.jsonl")
        temporary.cleanup()


if __name__ == "__main__":
    main()
