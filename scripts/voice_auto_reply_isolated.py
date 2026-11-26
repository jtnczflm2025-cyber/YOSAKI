"""Local product check for confirmed-send chat and post-completion auto-read.

The database and owner are temporary. Chat uses the existing demo generator;
ElevenLabs HTTP is replaced before main.py is imported. No provider can be
reached from this process through the voice jobs adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import httpx


class LocalFakeScribe:
    async def feed(self, pcm: bytes, *, commit: bool) -> list[dict]:
        if commit:
            return [{"type": "committed", "text": "隔离测试转写"}]
        return [{"type": "partial", "text": "隔离测试"}] if pcm else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18793)
    parser.add_argument("--frontend-port", type=int, default=15193)
    args = parser.parse_args()
    frame = b"\xff\xfb\x90\x64" + bytes(413)
    audio = frame * 300
    with tempfile.TemporaryDirectory(prefix="yosaki-auto-read-") as location:
        os.environ.update({
            "YOSAKI_DATA_DIR": location,
            "YOSAKI_VOICE_ENABLED": "1",
            "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
            "YOSAKI_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.frontend_port}",
            "YOSAKI_CHATGPT_PLAN_ENABLED": "0",
            "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
            "YOSAKI_SCRIBE_DRAFT_ISOLATED": "1",
            "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
            "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
            "ELEVENLABS_API_KEY": "isolation-only-fake-key",
        })
        from backend.app.voice import auto_budget, router, storage
        from backend.app.voice.audio_jobs import VoiceJobs
        from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter

        count = {"mock_ttd_posts": 0}

        def fake_ttd(_request: httpx.Request) -> httpx.Response:
            count["mock_ttd_posts"] += 1
            return httpx.Response(200, content=audio)

        transport = httpx.MockTransport(fake_ttd)
        router.VoiceJobs = lambda connect: VoiceJobs(
            connect, adapter_factory=lambda key: ElevenLabsAdapter(key, transport))

        from backend.app import main as app_module
        from backend.app import chatgpt_plan
        from backend.app.voice.schemas import ProfileInput
        from fastapi.testclient import TestClient

        # Both provider paths are locally intercepted; the fake Plan option
        # exercises the ordinary chat route's answer-display gate in a browser.
        real_async_client = httpx.AsyncClient
        model_calls = {"count": 0}
        spoken = "今夜はゆっくりでいいよ。"
        item = {"type": "message", "id": "msg-isolated-final", "role": "assistant",
                "phase": "final_answer", "content": [{"type": "output_text", "text": spoken}]}

        def sse(event):
            return ("event: " + event["type"] + "\n" +
                    "data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()

        def fake_plan(_request):
            model_calls["count"] += 1
            events = [
                {"type": "response.output_item.added", "item": item},
                {"type": "response.output_text.delta", "item_id": item["id"],
                 "delta": spoken},
                {"type": "response.metadata", "metadata": {
                    "openai_chatgpt_moderation_metadata": {
                        "final_answer_moderation": {"item_id": item["id"],
                            "moderated": True, "display": {"version": 1,
                                "mode": "show", "trustedContacts": "skip"}}}}},
                {"type": "response.completed", "response": {"output": [item],
                    "usage": {"input_tokens": 4, "output_tokens": 8,
                              "total_tokens": 12}}},
            ]
            return httpx.Response(200, content=b"".join(sse(event) for event in events),
                                  headers={"content-type": "text/event-stream"})

        def local_async_client(*client_args, **client_kwargs):
            client_kwargs.setdefault("transport", httpx.MockTransport(fake_plan))
            return real_async_client(*client_args, **client_kwargs)

        async def fake_access_token():
            return "isolated-fake-token"

        httpx.AsyncClient = local_async_client
        chatgpt_plan.access_token = fake_access_token

        with TestClient(app_module.app) as client:
            response = client.post("/api/auth/setup", json={
                "username": "auto_read_isolated_owner",
                "password": "isolation-only-password-2026",
            })
            if response.status_code != 201:
                raise SystemExit("isolated_owner_setup_failed")
        with app_module.connect() as db:
            auto_budget.init_db(db)
            auto_budget.init_daily_db(db)
            storage.put_consent(db, 1)
            db.commit()
            storage.put_profile(db, 1, ProfileInput(
                expected_revision=0, voice_id="synthetic_voice_only", active=True))
            db.execute("INSERT INTO model_profiles(name,provider_name,base_url,model_name,"
                "protocol,temperature,top_p,max_tokens,context_limit,supports_streaming,"
                "enabled,created_at,updated_at) VALUES "
                "('本地假模型','OpenAI','https://api.openai.com/v1','gpt-fake',"
                "'chatgpt_plan',0.8,1,128,10000,1,1,'now','now')")
        app_module.app.state.voice_scribe_draft.provider_factory = (
            lambda _owner, _character, _language: LocalFakeScribe())
        print(json.dumps({
            "mode": "fake_chat_fake_ttd", "database_path": str(app_module.DB.resolve()),
            "backend_port": args.port, "frontend_port": args.frontend_port,
            "provider_requests": 0, "mock_posts_before_browser": count["mock_ttd_posts"],
            "mock_model_calls_before_browser": model_calls["count"],
        }), flush=True)
        import uvicorn
        uvicorn.run(app_module.app, host="127.0.0.1", port=args.port,
                    log_level="warning", access_log=False, workers=1)
        print(json.dumps({"mock_ttd_posts": count["mock_ttd_posts"],
                          "mock_model_calls": model_calls["count"],
                          "provider_requests": 0}), flush=True)


if __name__ == "__main__":
    main()
