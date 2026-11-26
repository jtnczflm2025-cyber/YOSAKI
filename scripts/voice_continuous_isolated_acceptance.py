"""Local-only continuous call exercise; never connects to a provider.

Run with ``--check`` for actual FastAPI routes or ``--serve`` for the isolated
product UI. The script creates and later removes only its own temporary data.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import inspect
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

import httpx


PASSWORD = "SyntheticOnly-Voice-2026!"
ORIGIN = "http://127.0.0.1:5174"
PORT = 18799
BOUND_CONVERSATION_ID = 6
TEXTS = ("うん、今夜はゆっくり話そう。", "そうだな、次は静かに話そう。",
         "じゃあ、最後にもう少しだけ。")


def mp3_silence(frames: int = 95) -> bytes:
    # MPEG-1 Layer III 128 kb/s, 44.1 kHz. Synthetic silent frame payload.
    return (bytes.fromhex("fffb9064") + bytes(413)) * frames


def prepare(root: Path, *, model_delay: float = 0, tts_delay: float = 0,
            audio_frames: int = 95, daily: bool = False,
            settings_schema: bool = False, ws_output: bool = False,
            ws_audio_seconds: int = 5, daily_three_rounds: bool = False,
            two_round_acceptance: bool = False, ws_failure: bool = False,
            tts_characters: int = 450, headset_fixture: bool = False,
            ws_final_delay: float = 0, automatic_tone: bool = False):
    if not root.is_absolute() or root.name == "data" or "Temp" not in str(root):
        raise RuntimeError("isolated_data_path_required")
    os.environ.update({
        "YOSAKI_DATA_DIR": str(root),
        "YOSAKI_ALLOWED_ORIGINS": ORIGIN,
        "YOSAKI_VOICE_ENABLED": "1",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
        "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "1",
        "YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED": "1" if daily else "0",
        "YOSAKI_VOICE_CONTINUOUS_TWO_ROUND_ACCEPTANCE": "1" if two_round_acceptance else "0",
        "YOSAKI_VOICE_HEADSET_ACCEPTANCE_CONVERSATION": str(BOUND_CONVERSATION_ID) if headset_fixture else "",
        "YOSAKI_VOICE_CONTINUOUS_BATCH_ID": uuid.uuid4().hex,
        "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
        "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
        "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
        "YOSAKI_V4_DAILY_OUTPUT_ENABLED": "1" if ws_output else "0",
        "ELEVENLABS_API_KEY": "synthetic_test_key_never_sent",
        "YOSAKI_V4_REAL_PROVIDER_ENABLED": "0",
    })
    os.environ["YOSAKI_V3_EXPRESSION_ENABLED"] = str(int(automatic_tone))
    os.environ["YOSAKI_VOICE_AUTO_EXPRESSION_ENABLED"] = str(int(automatic_tone))
    if daily:
        os.environ["YOSAKI_VOICE_CONTINUOUS_ISOLATED"] = "0"
    from backend.app import main, chatgpt_plan
    from backend.app.voice import auto_budget, continuous_batch, continuous_daily, scribe_budget, storage, audio_cache
    from backend.app.voice.schemas import ProfileInput
    audio_cache.validate_mp3(mp3_silence(audio_frames))
    if settings_schema:
        if not daily:
            raise RuntimeError("settings_schema_requires_daily_mode")
        from scripts.migrate_voice_continuous_settings import migrate
        with main.connect() as db:
            continuous_daily.init_db(db)
        migrate(root / "yosaki.sqlite3", "isolated", apply=True)
    with main.connect() as db:
        auto_budget.init_db(db)
        auto_budget.init_daily_db(db)
        scribe_budget.init_db(db)
        if daily:
            continuous_daily.init_db(db)
        else:
            continuous_batch.init_db(db)
        db.execute("""INSERT INTO users(username,password_hash,created_at)
            VALUES (?,?,?)""", ("synthetic_voice_owner", main.password_hash(PASSWORD), main.now()))
        db.execute("""INSERT INTO model_profiles(id,name,provider_name,base_url,model_name,
            protocol,temperature,top_p,max_tokens,context_limit,supports_streaming,enabled,
            created_at,updated_at) VALUES
            (7,'Synthetic Plan','Synthetic','https://invalid.local','gpt-6.1-sol',
            'chatgpt_plan',0.8,1,?,32768,1,1,?,?)""",
            (2048 if two_round_acceptance and not headset_fixture else 256, main.now(), main.now()))
        db.execute("UPDATE characters SET default_model_id=7 WHERE id=1")
        db.execute("""INSERT INTO conversations(id,character_id,title,model_profile_id,
            created_at,updated_at) VALUES (5,1,'Older synthetic conversation',7,?,?)""",
            (main.now(), main.now()))
        db.execute("""INSERT INTO conversations(id,character_id,title,model_profile_id,
            created_at,updated_at) VALUES (6,1,'Bound new empty conversation',7,?,?)""",
            (main.now(), main.now()))
        db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
            VALUES (5,'user','old fixture user',?),
                   (5,'assistant','old fixture answer',?)""", (main.now(), main.now()))
        if daily:
            db.execute("""INSERT INTO memories(character_id,conversation_id,scope,
                content,enabled,created_at,updated_at)
                VALUES (1,6,'temporary','synthetic call memory',1,?,?)""",
                (main.now(), main.now()))
        storage.put_consent(db, 1)
    if not daily:
        continuous_batch.prepare(main.connect,
            os.environ["YOSAKI_VOICE_CONTINUOUS_BATCH_ID"], 1, 1,
            BOUND_CONVERSATION_ID, 7)
    with main.connect() as db:
        if automatic_tone:
            from backend.app.voice import automatic_expression, expression_product
            automatic_expression.init_db(db)
            expression_product.init_db(db)
            state = json.dumps({"emotion":{"valence":0, "arousal":.2, "tension":.2}})
            db.execute("""INSERT OR REPLACE INTO psychological_snapshots
                (character_id,revision,state_json,updated_at,rule_version) VALUES(1,1,?,?,'synthetic')""",
                (state,main.now()))
            db.commit()
        storage.put_profile(db, 1, ProfileInput(expected_revision=0,
            voice_id="synthetic_voice_never_sent", active=True,
            configuration={"expression_mode":"auto", "personality":"克制"} if automatic_tone else {}))
    if daily_three_rounds:
        continuous_daily.put_settings(main.connect, 1,
            continuous_daily.CallLimits(3, 360, 3, 450), 0, True)
        auto_budget.put_settings(main.connect, 1, auto_budget.Limits(4, 4, tts_characters), 0, True)
    attempts = 4 if daily_three_rounds else 2
    scribe_budget.put_settings(main.connect, 1, True,
        scribe_budget.Limits(attempts, attempts, attempts * 15), 0, True, True)

    class FakeScribe:
        opens = 0
        closes = 0
        feeds = 0
        commits = []

        async def open(self):
            FakeScribe.opens += 1
            self.index = FakeScribe.opens
            self.bytes = 0

        async def feed(self, pcm, *, commit):
            FakeScribe.feeds += 1
            self.bytes += len(pcm)
            if (headset_fixture or daily_three_rounds) and commit:
                FakeScribe.commits.append(self.bytes)
                utterances = ("夜の散歩について、日本語で百字ほど話してください。",
                              "少しゆっくり話してください。", "この後はどうしますか。")
                text = utterances[self.index - 1]
                return [{"type": "committed", "text": text}]
            return [{"type": "committed", "text": "今夜はゆっくり話そう。"}] if commit else []

        async def aclose(self):
            FakeScribe.closes += 1

    class FakeTTS:
        attempts = 0
        ws_opens = 0
        ws_closes = 0
        sent_http = []

        def __init__(self, _key):
            pass

        async def synthesize_http(self, _text, _voice_id, _language):
            FakeTTS.attempts += 1
            FakeTTS.sent_http.append(_text)
            if tts_delay:
                await asyncio.sleep(tts_delay)
            return mp3_silence(audio_frames)

    class FakeResponse(httpx.AsyncByteStream):
        def __init__(self, text: str):
            self.text = text

        async def __aiter__(self):
            if model_delay:
                await asyncio.sleep(model_delay)
            item = {"type": "message", "id": "msg-synthetic-final",
                    "role": "assistant", "phase": "final_answer",
                    "content": [{"type": "output_text", "text": self.text}]}
            events = [
                {"type": "response.output_item.added", "item": item},
                {"type": "response.output_text.delta", "item_id": item["id"],
                 "delta": self.text},
                {"type": "response.output_text.done", "item_id": item["id"],
                 "text": self.text},
                {"type": "response.completed", "response": {"output": [item],
                 "usage": {"input_tokens": 12, "output_tokens": 13, "total_tokens": 25}}},
            ]
            for item in events:
                yield (f"event: {item['type']}\ndata: "
                       f"{json.dumps(item, ensure_ascii=False)}\n\n").encode()

    class FakeClient(httpx.AsyncClient):
        calls = 0
        requests = []

        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(self.respond), **kwargs)

        @staticmethod
        async def respond(request):
            assert request.method == "POST" and request.url.path == "/v1/responses"
            body = json.loads(request.content)
            if two_round_acceptance:
                assert body.get("max_output_tokens") == (256 if headset_fixture else 2048)
            FakeClient.calls += 1
            if FakeClient.calls > (3 if daily_three_rounds else 2):
                raise RuntimeError("fake_model_budget_exceeded")
            FakeClient.requests.append(body)
            text = TEXTS[FakeClient.calls - 1]
            if headset_fixture and FakeClient.calls == 1:
                text = "夜の散歩は、昼間のざわめきが少しずつ遠ざかる時間です。川沿いの道では涼しい風が吹き、街灯が水に映ります。空を見上げてゆっくり歩くと、小さな音や景色にも自然と気づきます。静かな公園まで足を伸ばすのもいいですね。"
            if automatic_tone and FakeClient.calls == 2:
                text = "言葉を選んで静かに話そう。"
            return httpx.Response(200, stream=FakeResponse(text))

    async def fake_access_token(*, allow_refresh=True):
        if two_round_acceptance:
            assert not allow_refresh
        return "synthetic_not_a_real_token"

    chatgpt_plan.access_token = fake_access_token
    if daily:
        from backend.app.voice import continuous_call
        continuous_call._local_model_path_reason = lambda: None  # Fake-only credential path.
    main.httpx.AsyncClient = FakeClient
    main.app.state.voice_scribe_draft.provider_factory = lambda *_: FakeScribe()
    if ws_output:
        if not daily:
            raise RuntimeError("ws_output_requires_daily_mode")
        class FakeTTD:
            def __init__(self):
                self.queue = asyncio.Queue()
            async def __aenter__(self):
                FakeTTS.ws_opens += 1
                return self
            async def __aexit__(self, *_):
                FakeTTS.ws_closes += 1
            async def send(self, raw):
                payload = json.loads(raw)
                if "inputs" in payload:
                    if ws_failure:
                        await self.queue.put(None)
                        return
                    pcm = base64.b64encode(b"\x00\x00\x10\x27" * (12000 * ws_audio_seconds)).decode()
                    await self.queue.put(json.dumps({"audio": pcm,
                        "is_final_audio_for_turn": not ws_final_delay}))
                    self.delayed_final = bool(ws_final_delay)
                if payload.get("close_socket"):
                    await self.queue.put('{"is_final":true}')
            async def recv(self):
                if self.queue.empty() and getattr(self, "delayed_final", False):
                    self.delayed_final = False
                    await asyncio.sleep(ws_final_delay)
                    return '{"is_final_audio_for_turn":true}'
                data = await self.queue.get()
                if data is None:
                    raise ConnectionError("synthetic upstream disconnected")
                return data
        main.app.state.voice_v4_output.connector_factory = lambda _bound: FakeTTD
    from fastapi.responses import FileResponse

    @main.app.get("/api/voice/scribe-draft/fixture.wav")
    def local_fixture():
        return FileResponse(Path(__file__).resolve().parents[1] /
            "tests/fixtures/voice_v1b_scribe_zh.wav", media_type="audio/wav")

    if daily_three_rounds:
        @main.app.get("/api/voice/test/round-counts/{call_id}")
        def round_counts(call_id: str):
            with main.connect() as db:
                call = db.execute("""SELECT limit_rounds,ended_at,conversation_id FROM
                    voice_continuous_daily_calls WHERE call_id=?""", (call_id,)).fetchone()
                saved = db.execute("""SELECT count(*) FROM messages
                    WHERE conversation_id=? AND role='assistant' AND generation_status='completed'""",
                    (call["conversation_id"],)).fetchone()[0]
                used = db.execute("""SELECT synth_jobs,request_count,character_count FROM
                    voice_auto_owner_day_budget WHERE owner_id=1""").fetchone()
            return {"stored_rounds": call["limit_rounds"], "ended": call["ended_at"] is not None,
                    "scribe_opened": FakeScribe.opens, "scribe_closed": FakeScribe.closes,
                    "ttd_opened": FakeTTS.ws_opens, "ttd_closed": FakeTTS.ws_closes,
                    "http": FakeTTS.attempts, "http_tags": sum(t.startswith("[thoughtful] ") for t in FakeTTS.sent_http), "model": FakeClient.calls, "saved": saved,
                    "usage": list(used) if used else [0, 0, 0],
                    "active_output_leases": len(main.app.state.voice_v4_output.active_owners)}

    if headset_fixture or daily_three_rounds:
        @main.app.get("/api/voice/test/headset-observations")
        def headset_observations():
            with main.connect() as db:
                utterances = [row[0] for row in db.execute("""SELECT content FROM messages
                    WHERE conversation_id=? AND role='user' ORDER BY id""",
                    (BOUND_CONVERSATION_ID,))]
                limits = db.execute("""SELECT limit_rounds,limit_seconds,
                    limit_tts_requests,limit_tts_characters FROM voice_continuous_daily_calls
                    WHERE conversation_id=?""", (BOUND_CONVERSATION_ID,)).fetchone()
            return {"commit_bytes": FakeScribe.commits, "utterances": utterances,
                    "limits": list(limits), "token_caps": [body.get("max_output_tokens")
                        for body in FakeClient.requests], "length_instructions": [
                        marker in body.get("instructions", "") for body, marker in
                        zip(FakeClient.requests, ("110字以内", "50字以内"))]}
    for route in main.app.routes:
        if getattr(route, "path", "").endswith("/{message_id}/auto-speak"):
            jobs = inspect.getclosurevars(route.endpoint).nonlocals["jobs"]
            jobs.adapter_factory = FakeTTS
            break
    else:
        raise RuntimeError("auto_speak_route_missing")
    return main, FakeScribe, FakeClient, FakeTTS


def check(main, fake_scribe, fake_model, fake_tts, *, daily: bool = False):
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        login = client.post("/api/auth/login", json={
            "username": "synthetic_voice_owner", "password": PASSWORD})
        assert login.status_code == 200, login.status_code
        if daily:
            before_start = client.get("/api/voice/calls/availability", params={
                "character_id": 1, "conversation_id": BOUND_CONVERSATION_ID,
                "model_profile_id": 7})
            assert before_start.status_code == 200, before_start.text
            assert before_start.json()["available_rounds"] == 2, before_start.json()
        started = client.post("/api/voice/calls", json={
            "character_id": 1, "conversation_id": BOUND_CONVERSATION_ID, "model_profile_id": 7,
            "acknowledge_auto_send": True})
        assert started.status_code == 200, started.text
        call_id = started.json()["call_id"]
        assert client.post("/api/voice/calls", json={"character_id": 1,
            "conversation_id": BOUND_CONVERSATION_ID, "model_profile_id": 7,
            "acknowledge_auto_send": True}).status_code == 409
        # Daily mode permits unrelated ordinary chat. The isolated acceptance
        # mode keeps its global product lock.
        if not daily:
            assert client.post("/api/chat/stream", json={"character_id": 1,
                "conversation_id": 5, "model_profile_id": 7,
                "message": "old-conversation bypass"}).status_code == 409
        else:
            available = client.get("/api/voice/calls/availability", params={
                "character_id": 1, "conversation_id": BOUND_CONVERSATION_ID,
                "model_profile_id": 7})
            assert available.status_code == 200, available.text
            assert available.json()["active_call"] is True, available.json()
            assert available.json()["available_rounds"] == 0, available.json()
        assert client.post("/api/chat/stream", json={"character_id": 1,
            "conversation_id": 5, "model_profile_id": 7,
            "message": "wrong target", "voice_call_id": call_id,
            "voice_call_turn_id": uuid.uuid4().hex}).status_code == 409
        assert fake_model.calls == 0
        completed = []
        round_links = []
        for round_no in range(2):
            attempt = uuid.uuid4().hex
            with client.websocket_connect("/api/voice/scribe-draft/ws",
                    headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "start", "character_id": 1,
                              "language_code": "auto", "attempt_id": attempt,
                              "call_id": call_id})
                assert ws.receive_json()["type"] == "ready"
                ws.send_json({"type": "audio", "pcm_base64":
                    base64.b64encode(bytes([1]) * 3200).decode()})
                ws.send_json({"type": "commit"})
                final = ws.receive_json()
                assert final["type"] == "committed", final
            request = {"character_id": 1, "conversation_id": BOUND_CONVERSATION_ID,
                       "model_profile_id": 7, "message": final["text"],
                       "voice_auto_read_requested": True,
                       "voice_call_id": call_id,
                       "voice_call_turn_id": final["call_turn_id"]}
            response = client.post("/api/chat/stream", json=request)
            assert response.status_code == 200, response.text
            events = []
            for block in response.text.split("\n\n"):
                if "data: " not in block:
                    continue
                kind = block.split("event: ", 1)[1].split("\n", 1)[0]
                data = json.loads(block.split("data: ", 1)[1].split("\n", 1)[0])
                events.append((kind, data))
            finished = [data for kind, data in events if kind == "message_complete"]
            assert len(finished) == 1 and finished[0]["voice_auto_read_eligible"]
            message_id = finished[0]["message_id"]
            preview = client.get(f"/api/voice/messages/{message_id}/speech-preview")
            assert preview.status_code == 200, preview.text
            if not daily:
                assert client.post(f"/api/voice/messages/{message_id}/speak", json={
                    "preview_sha256": preview.json()["preview_sha256"]}).status_code == 409
            speak = client.post(f"/api/voice/messages/{message_id}/auto-speak", json={
                "preview_sha256": preview.json()["preview_sha256"],
                "voice_call_id": call_id,
                "voice_call_turn_id": final["call_turn_id"]})
            assert speak.status_code == 202, speak.text
            for _ in range(50):
                job = client.get("/api/voice/jobs/" + speak.json()["job_id"]).json()
                if job["status"] not in ("queued", "generating"):
                    break
                time.sleep(0.02)
            assert job["status"] == "completed", job
            audio = client.get(job["audio"][0]["url"])
            assert audio.status_code == 200 and len(audio.content) > 800
            drained = client.post(f"/api/voice/calls/{call_id}/playback-drained",
                json={"turn_id": final["call_turn_id"], "message_id": message_id})
            assert drained.status_code == 200, drained.text
            assert drained.json()["can_continue"] is (round_no == 0)
            completed.append(message_id)
            round_links.append((attempt, final["call_turn_id"], message_id))
        assert fake_scribe.opens == 2
        assert fake_model.calls == 2
        first_request, second_request = map(
            lambda item: json.dumps(item, ensure_ascii=False), fake_model.requests)
        assert "old fixture user" not in first_request + second_request
        assert "old fixture answer" not in first_request + second_request
        assert TEXTS[0] not in first_request and TEXTS[0] in second_request
        if daily:
            assert "synthetic call memory" in first_request
        else:
            assert "synthetic call memory" not in first_request
        assert not any(request.get("tools") for request in fake_model.requests)
        assert fake_tts.attempts == 2  # Different replies need two independent HTTP jobs.
        usage = main.app.state.voice_continuous.summary(
            main.app.state.voice_continuous.calls[call_id])["used"]
        assert usage["tts_requests"] == 2 and usage["model_turns"] == 2
        with main.connect() as db:
            saved = db.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id",
                               (BOUND_CONVERSATION_ID,)).fetchall()
            assert len(saved) == 4 and [row["role"] for row in saved] == \
                ["user", "assistant", "user", "assistant"]
            assert db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=5").fetchone()[0] == 2
            table = ("voice_continuous_daily_calls" if daily else
                     "voice_continuous_batch_receipts")
            trace = json.loads(db.execute(f"SELECT claim_trace_json FROM {table}").fetchone()[0])
            assert [item["action"] for item in trace].count("stt") == 2
            assert [item["action"] for item in trace].count("model") == 2
            assert [item["action"] for item in trace].count("tts") == 2
            assert all(item.get("call_id", call_id) == call_id for item in trace)
            assert len({item[0] for item in round_links}) == 2
            assert len({item[1] for item in round_links}) == 2
            for attempt_id, turn_id, message_id in round_links:
                assert len([e for e in trace if e["action"] == "stt" and
                            e.get("attempt_id") == attempt_id and e.get("call_id", call_id) == call_id]) == 1
                assert len([e for e in trace if e["action"] == "model" and
                            e.get("turn_id") == turn_id and e.get("call_id", call_id) == call_id]) == 1
                for action in ("message_saved", "tts"):
                    assert len([e for e in trace if e["action"] == action and
                                e.get("turn_id") == turn_id and
                                e.get("message_id") == message_id and
                                e.get("call_id", call_id) == call_id]) == 1
        assert client.delete(f"/api/voice/calls/{call_id}").status_code == 204
        assert client.get(f"/api/voice/calls/{call_id}").status_code == 409
        assert client.post("/api/voice/calls", json={
            "character_id": 1, "conversation_id": BOUND_CONVERSATION_ID,
            "model_profile_id": 7, "acknowledge_auto_send": True}).status_code == 409
    return {"model_requests": fake_model.calls, "scribe_connections": fake_scribe.opens,
            "tts_requests": fake_tts.attempts, "saved_messages": len(saved),
            "completed_ids": completed, "bound_conversation": BOUND_CONVERSATION_ID,
            "batch_call_attempt_turn_message_linked": True,
            "third_start_rejected": True}


def main_cli():
    args = argparse.ArgumentParser()
    args.add_argument("--check", action="store_true")
    args.add_argument("--serve", action="store_true")
    args.add_argument("--daily", action="store_true")
    args.add_argument("--settings-schema", action="store_true")
    args.add_argument("--model-delay", type=float, default=0)
    args.add_argument("--tts-delay", type=float, default=0)
    args.add_argument("--audio-frames", type=int, default=95)
    args.add_argument("--ws-output", action="store_true")
    args.add_argument("--ws-audio-seconds", type=int, default=5)
    args.add_argument("--automatic-tone", action="store_true")
    args.add_argument("--ws-final-delay", type=float, choices=(0,8), default=0)
    args.add_argument("--daily-three-rounds", action="store_true")
    args.add_argument("--two-round-acceptance", action="store_true")
    args.add_argument("--ws-failure", action="store_true")
    args.add_argument("--headset-fixture", action="store_true")
    args.add_argument("--tts-characters", type=int, choices=(1, 450), default=450)
    mode = args.parse_args()
    if mode.check == mode.serve:
        raise SystemExit("choose --check or --serve")
    if mode.two_round_acceptance and not mode.daily_three_rounds:
        args.error("--two-round-acceptance requires --daily-three-rounds")
    if mode.daily_three_rounds and not (mode.serve and mode.daily and mode.settings_schema):
        args.error("--daily-three-rounds requires --serve --daily --settings-schema")
    if (mode.ws_failure or mode.tts_characters == 1) and not (mode.daily_three_rounds and mode.ws_output):
        args.error("failure fixtures require --daily-three-rounds --ws-output")
    if mode.headset_fixture and not (mode.two_round_acceptance and mode.ws_output):
        args.error("--headset-fixture requires the two-round WS acceptance")
    if not (0 <= mode.model_delay <= 15 and 0 <= mode.tts_delay <= 15 and
            95 <= mode.audio_frames <= 380 and 1 <= mode.ws_audio_seconds <= 15):
        raise SystemExit("synthetic_delay_or_audio_out_of_bounds")
    with tempfile.TemporaryDirectory(prefix="yosaki-continuous-", dir=os.getenv("TEMP")) as temp:
        main, fake_scribe, fake_model, fake_tts = prepare(Path(temp),
            model_delay=mode.model_delay, tts_delay=mode.tts_delay,
            audio_frames=mode.audio_frames, daily=mode.daily,
            settings_schema=mode.settings_schema, ws_output=mode.ws_output,
            ws_audio_seconds=mode.ws_audio_seconds, daily_three_rounds=mode.daily_three_rounds,
            two_round_acceptance=mode.two_round_acceptance, ws_failure=mode.ws_failure,
            tts_characters=mode.tts_characters, headset_fixture=mode.headset_fixture,
            ws_final_delay=mode.ws_final_delay, automatic_tone=mode.automatic_tone)
        if mode.check:
            print(json.dumps(check(main, fake_scribe, fake_model, fake_tts,
                                   daily=mode.daily)))
        else:
            import uvicorn
            print(f"isolated_fake_server=http://127.0.0.1:{PORT}", flush=True)
            try:
                uvicorn.run(main.app, host="127.0.0.1", port=PORT, log_level="warning")
            finally:
                with main.connect() as db:
                    table = ("voice_continuous_daily_calls" if mode.daily else
                             "voice_continuous_batch_receipts")
                    batch = db.execute(f"""SELECT stt_connections,model_turns,tts_requests,
                        claim_trace_json FROM {table}""").fetchone()
                    old_count = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=5").fetchone()[0]
                    bound_count = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=6").fetchone()[0]
                print(json.dumps({"fake_scribe": fake_scribe.opens,
                    "fake_scribe_closed": fake_scribe.closes,
                    "fake_ttd_ws": fake_tts.ws_opens,
                    "fake_ttd_ws_closed": fake_tts.ws_closes,
                    "fake_model": fake_model.calls, "fake_tts": fake_tts.attempts,
                    "batch_stt": batch["stt_connections"] if batch else 0,
                    "batch_model": batch["model_turns"] if batch else 0,
                    "batch_tts": batch["tts_requests"] if batch else 0,
                    "old_messages": old_count, "bound_messages": bound_count,
                    "trace_actions": [item["action"] for item in
                                      json.loads(batch["claim_trace_json"])] if batch else []}), flush=True)


if __name__ == "__main__":
    main_cli()
