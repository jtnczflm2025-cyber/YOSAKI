"""Run the fake-only V4 dynamic product bridge on a fresh temporary database."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path


class LocalFakeSupplier:
    local_fake_provider = True

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        if "inputs" in request:
            # 1 second of synthetic, non-silent 24 kHz mono s16le PCM.
            pcm = base64.b64encode(b"\x00\x00\x10\x27" * 12_000).decode()
            await self.queue.put(json.dumps({"audio": pcm,
                "is_final_audio_for_turn": True}))
        if request.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self) -> str:
        return await self.queue.get()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18785)
    parser.add_argument("--frontend-port", type=int, default=15187)
    args = parser.parse_args()
    if not (1024 <= args.port <= 65535 and 1024 <= args.frontend_port <= 65535):
        raise SystemExit("invalid_port")
    base = Path(tempfile.mkdtemp(prefix="yosaki-v4-dynamic-browser-"))
    data = base / "data"
    data.mkdir()
    receipts = base / "receipts"
    receipts.mkdir()
    os.environ.update({
        "YOSAKI_DATA_DIR": str(data),
        "YOSAKI_VOICE_ENABLED": "1",
        "YOSAKI_V4_DYNAMIC_CHAT_ISOLATED": "1",
        "YOSAKI_V4_DYNAMIC_FAKE_ONLY": "1",
        "YOSAKI_V4_REAL_PROVIDER_ENABLED": "0",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.frontend_port}",
        "ELEVENLABS_API_KEY": "",
    })
    from fastapi.testclient import TestClient
    from backend.app import main as app_module
    from backend.app.voice import realtime_dynamic_receipt, storage
    from backend.app.voice.schemas import ProfileInput, VoiceConfiguration

    with TestClient(app_module.app) as client:
        setup = client.post("/api/auth/setup", json={
            "username": "bridge_browser_owner",
            "password": "bridge-test-local-only-2026"})
        if setup.status_code != 201:
            raise SystemExit("isolated_setup_failed")
    with app_module.connect() as db:
        db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) "
                   "VALUES (1,'dynamic bridge fixture','now','now')")
        conversation_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        storage.put_consent(db, 1)
        db.commit()
        storage.put_profile(db, 1, ProfileInput(expected_revision=0,
            voice_id="synthetic_ivc_voice", active=True,
            configuration=VoiceConfiguration(language="ja", audio_format="pcm_24000")))
        profile = storage.profile(db, 1)
        consent = storage.consent(db, 1)
    manifest = {
        "schema_version": 1, "mode": "mock_only",
        "run_id": "dynamic-browser-" + uuid.uuid4().hex[:16],
        "generation_id": uuid.uuid4().hex,
        "owner_id": 1, "character_id": 1,
        "conversation_id": conversation_id,
        "profile_revision": profile["revision"],
        "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
        "notice_version": consent["notice_version"],
        "chat_model_id": "isolated_mock_chat_v1",
        "tts_model_id": "eleven_v4_turbo", "output_format": "pcm_24000",
        "language_code": "ja",
        "limits": {"max_characters": 400, "max_segments": 8,
                   "max_connections": 8, "max_audio_bytes": 1_000_000,
                   "max_turn_seconds": 15},
    }
    realtime_dynamic_receipt.issue_mock(receipts, manifest)
    gate = app_module.app.state.voice_v4_dynamic
    gate.run_id = manifest["run_id"]
    gate.receipt_dir = receipts
    gate.connector_factory = lambda _bound: LocalFakeSupplier()
    print(json.dumps({"mode": "isolated_fake_only", "database_path":
        str(app_module.DB.resolve()), "run_id": manifest["run_id"],
        "backend_port": args.port, "frontend_port": args.frontend_port},
        ensure_ascii=False), flush=True)
    import uvicorn
    uvicorn.run(app_module.app, host="127.0.0.1", port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
