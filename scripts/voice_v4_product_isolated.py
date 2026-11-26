"""Temporary product-route browser rehearsal. Never uses the daily database or provider."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import wave
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient


PORT = 18769
ORIGIN = "http://127.0.0.1:15175"
RETAINED_SHA256 = "6a58d9fd2def3a7f259293f9fa400e30fa9690cf08cc4a48536f7cc7094330e4"
RETAINED_WAV = (Path(os.environ.get("LOCALAPPDATA", "")) / "YOSAKI" /
                "voice-v4-stream-runs" / "voice-v4-stream-20261111-r2" / "audio.wav")


def pcm_chunks() -> list[str]:
    source = RETAINED_WAV
    if hashlib.sha256(source.read_bytes()).hexdigest() != RETAINED_SHA256:
        raise RuntimeError("retained_audio_fingerprint_mismatch")
    with wave.open(str(source), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 24000, "NONE"):
            raise RuntimeError("retained_audio_format_mismatch")
        pcm = wav.readframes(wav.getnframes())
    return [base64.b64encode(pcm[i:i + 26000]).decode("ascii")
            for i in range(0, len(pcm), 26000)]


class LocalSupplier:
    def __init__(self, chunks: list[str], counters: dict[str, int]) -> None:
        self.chunks = chunks
        self.counters = counters
        self.pending: list[str] = []
        self.finished = False

    async def __aenter__(self):
        self.counters["provider_connections"] += 1
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, raw: str):
        data = json.loads(raw)
        if "inputs" in data:
            self.counters["text_dispatches"] += 1
            self.pending = list(self.chunks)
        if data.get("close_socket"):
            self.finished = True

    async def recv(self):
        await asyncio.sleep(.04)
        if self.pending:
            chunk = self.pending.pop(0)
            return json.dumps({"audio": chunk,
                               "is_final_audio_for_turn": not self.pending})
        if self.finished:
            return '{"is_final":true}'
        await asyncio.sleep(1)
        return '{"keep_alive":true}'


def main() -> None:
    chunks = pcm_chunks()
    temp = tempfile.TemporaryDirectory(prefix="yosaki-v4-product-browser-")
    os.environ.update({
        "YOSAKI_DATA_DIR": temp.name,
        "YOSAKI_VOICE_ENABLED": "1",
        "YOSAKI_V4_OUTPUT_ENABLED": "1",
        "YOSAKI_V4_REAL_PROVIDER_ENABLED": "0",
        "YOSAKI_V4_TTD_PROXY": "",
        "YOSAKI_V4_OUTPUT_MAX_ATTEMPTS_PER_OWNER": "5",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_ALLOWED_ORIGINS": ORIGIN,
        "ELEVENLABS_API_KEY": "fake-browser-only",
    })
    # The main module is imported only after the temporary data directory is fixed.
    from backend.app import main as product
    from backend.app.voice import manager, realtime_receipt, storage
    from backend.app.voice.schemas import ProfileInput

    with TestClient(product.app) as client:
        setup = client.post("/api/auth/setup", json={
            "username": "isolated_voice_owner", "password": "isolated-browser-password-123"})
        if setup.status_code != 201:
            raise RuntimeError("temporary_owner_setup_failed")
    with product.connect() as db:
        storage.put_consent(db, 1)
        db.commit()
        storage.put_profile(db, 1, ProfileInput(expected_revision=0,
            voice_id="synthetic_ivc_voice", active=True))
        db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) "
                   "VALUES (1,'V4 隔离播放','now','now')")
        conversation = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        first_conversation = conversation
        db.execute("INSERT INTO messages(conversation_id,role,content,generation_status,created_at) "
                   "VALUES (?,'assistant','[whispers] 今日は静かな夜です。','completed','now')",
                   (conversation,))
        message_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) "
                   "VALUES (1,'V4 切换验证','now','now')")
        conversation = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO messages(conversation_id,role,content,generation_status,created_at) "
                   "VALUES (?,'assistant','別の会話です。','completed','now')",
                   (conversation,))

    context = manager.speech_context(product.connect, message_id, 1)
    with product.connect() as db:
        profile = storage.profile(db, 1)
        consent = storage.consent(db, 1)
        runtime_db = db.execute("PRAGMA database_list").fetchone()[2]
    manifest = {
        "schema_version": 1, "status": "awaiting_single_real_approval",
        "run_id": "voice-v4-product-browser-test", "owner_id": 1,
        "character_id": 1, "conversation_id": first_conversation, "message_id": message_id,
        "message_version_sha256": context["digest"],
        "request_text": context["segments"][0],
        "request_text_sha256": hashlib.sha256(context["segments"][0].encode()).hexdigest(),
        "characters": len(context["segments"][0]),
        "profile_revision": profile["revision"],
        "voice_id_sha256": hashlib.sha256(profile["voice_id"].encode()).hexdigest(),
        "notice_version": consent["notice_version"],
        "provider": "elevenlabs", "model_id": "eleven_v4_turbo",
        "output_format": "pcm_24000", "pcm_sample_rate_hz": 24000,
        "pcm_encoding": "s16le", "pcm_channels_expected": 1,
        "language_code": "ja", "voice_settings": "omitted",
        "source_database_path": str(Path(runtime_db).resolve()),
        "runtime_database_path": str(Path(runtime_db).resolve()),
        "network_proxy_url": None,
        "limits": {**realtime_receipt.EXPECTED_LIMITS,
                   "tts_characters": len(context["segments"][0])},
        "failure_policy": "stop_on_auth_quota_protocol_unknown_timeout_or_disconnect; never_replay_dispatched_text",
        "billing": "unknown_until_provider_account_observation",
    }
    manifest_path = Path(temp.name) / "manifest.json"
    receipt_root = Path(temp.name) / "receipts"
    receipt_root.mkdir()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    receipt = realtime_receipt.expected_receipt(manifest,
        realtime_receipt.digest(manifest), realtime_receipt.code_set_sha256())
    (receipt_root / f'{manifest["run_id"]}.approval.json').write_text(
        json.dumps(receipt), encoding="utf-8")
    os.environ["YOSAKI_V4_PRODUCT_MANIFEST_PATH"] = str(manifest_path)
    os.environ["YOSAKI_V4_RECEIPT_DIR"] = str(receipt_root)

    counters = {"provider_connections": 0, "text_dispatches": 0}
    product.app.state.voice_v4_output.connector_factory = (
        lambda _bound: (lambda: LocalSupplier(chunks, counters)))

    @product.app.get("/isolation/v4-status")
    def status():
        return dict(counters)

    print(f"Isolated V4 product: 127.0.0.1:{PORT}; temporary database: {temp.name}", flush=True)
    try:
        uvicorn.run(product.app, host="127.0.0.1", port=PORT,
                    log_level="warning", access_log=False)
    finally:
        temp.cleanup()


if __name__ == "__main__":
    main()
