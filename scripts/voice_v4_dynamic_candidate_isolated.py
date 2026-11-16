"""Start the frozen dynamic candidate in a fresh, minimal isolated database.

Default mode is fake model + fake TTD. A future explicitly approved run must
provide a persistent real_approved receipt, --real-execute, and a fixed run ID.
The daily database is opened read-only only to copy the selected role/config.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY_DB = ROOT / "data" / "yosaki.sqlite3"
PROMPT = "阿夜，请只用日语说两句简短的话：先说今晚可以慢慢来，再说你会在这里听我讲。只输出正文，合计不超过 50 个日文字符；不要 Markdown 或 Audio Tags。"


class LocalFakeSupplier:
    local_fake_provider = True

    def __init__(self):
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, raw: str) -> None:
        item = json.loads(raw)
        if "inputs" in item:
            pcm = base64.b64encode(b"\x00\x00\x10\x27" * 12_000).decode()
            await self.queue.put(json.dumps({"audio": pcm,
                "is_final_audio_for_turn": True}))
        if item.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self) -> str:
        return await self.queue.get()


async def fake_model_events(_frozen):
    item = {"type": "message", "id": "msg-local-fake", "role": "assistant",
            "phase": "final_answer"}
    yield {"type": "response.output_item.added", "item": item}
    yield {"type": "response.output_text.delta", "item_id": item["id"],
           "delta": "今夜は、ゆっくりでいい。"}
    await asyncio.sleep(.3)
    yield {"type": "response.output_text.delta", "item_id": item["id"],
           "delta": "ここで、君の話を聞いている。"}
    await asyncio.sleep(.3)
    yield {"type": "response.metadata", "metadata": {
        "openai_chatgpt_moderation_metadata": {"final_answer_moderation": {
            "item_id": item["id"], "moderated": False,
            "display": {"mode": "show", "version": 1, "trustedContacts": "skip"}}}}}
    yield {"type": "response.completed", "response": {"output": [{**item,
        "content": [{"type": "output_text", "text":
            "今夜は、ゆっくりでいい。ここで、君の話を聞いている。"}]}],
        "usage": {"input_tokens": 30, "output_tokens": 20}}}


def _read_rows() -> dict[str, list[dict]]:
    connection = sqlite3.connect(DAILY_DB.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        specs = {"characters": ("id=?", (1,)),
                 "prompt_blocks": ("character_id=? AND enabled=1", (1,)),
                 "model_profiles": ("id=?", (5,)),
                 "conversations": ("id=? AND character_id=1", (5,)),
                 "voice_profiles": ("character_id=?", (1,)),
                 "voice_privacy_consents": ("owner_id=?", (1,))}
        result = {name: [dict(row) for row in connection.execute(
            f"SELECT * FROM {name} WHERE {where}", args)]
            for name, (where, args) in specs.items()}
        if any(len(result[name]) != 1 for name in specs if name != "prompt_blocks"):
            raise ValueError("daily_fixture_missing_or_ambiguous")
        if result["model_profiles"][0]["protocol"] != "chatgpt_plan":
            raise ValueError("daily_model_profile_changed")
        result["model_profiles"][0]["api_key_encrypted"] = None
        return result
    finally:
        connection.close()


def _insert(db, name: str, row: dict) -> None:
    columns = ",".join(row)
    marks = ",".join("?" for _ in row)
    db.execute(f"INSERT INTO {name} ({columns}) VALUES ({marks})", tuple(row.values()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18786)
    parser.add_argument("--frontend-port", type=int, default=15188)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--generation-id")
    parser.add_argument("--real-execute", action="store_true")
    parser.add_argument("--print-preview", action="store_true")
    parser.add_argument("--preview-output", type=Path)
    args = parser.parse_args()
    if not (1024 <= args.port <= 65535 and 1024 <= args.frontend_port <= 65535):
        raise SystemExit("invalid_port")
    if args.real_execute and (args.receipt_dir is None or not args.run_id):
        raise SystemExit("approved_receipt_location_required")
    if args.preview_output and not args.print_preview:
        raise SystemExit("preview_output_requires_print_preview")
    rows = _read_rows()
    temporary = tempfile.TemporaryDirectory(prefix="yosaki-v4-candidate-")
    base = Path(temporary.name)
    data = base / "data"
    data.mkdir()
    receipt_dir = args.receipt_dir.resolve() if args.receipt_dir else base / "receipts"
    if not args.real_execute:
        receipt_dir.mkdir()
    os.environ.update({
        "YOSAKI_DATA_DIR": str(data),
        "YOSAKI_V4_SOURCE_DATA_DIR": str(DAILY_DB.parent),
        "YOSAKI_VOICE_ENABLED": "1",
        "YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED": "1",
        "YOSAKI_V4_DYNAMIC_REAL_EXECUTE": "1" if args.real_execute else "0",
        "YOSAKI_V4_REVIEWED_DISPLAY_ENABLED": "1",
        "YOSAKI_V4_DYNAMIC_CANDIDATE_REAL": "0",
        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
        "YOSAKI_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.frontend_port}",
        "YOSAKI_CHATGPT_PLAN_ENABLED": "0",
    })
    from fastapi.testclient import TestClient
    from backend.app import main as app_module
    from backend.app.voice import realtime_dynamic_candidate_receipt as receipts
    from backend.app.voice.realtime_dynamic_candidate import network_proxy
    from backend.app.voice.realtime_dynamic_context import freeze
    from backend.app import chatgpt_plan

    with TestClient(app_module.app) as client:
        setup = client.post("/api/auth/setup", json={
            "username": "candidate_isolated_owner",
            "password": "local-candidate-only-password-2026"})
        if setup.status_code != 201:
            raise SystemExit("isolated_owner_setup_failed")
    with app_module.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM prompt_blocks WHERE character_id=1")
        db.execute("DELETE FROM characters WHERE id=1")
        for name in ("characters", "prompt_blocks", "model_profiles",
                     "conversations", "voice_profiles", "voice_privacy_consents"):
            for row in rows[name]:
                _insert(db, name, row)
        frozen = freeze(db, owner_id=1, character_id=1, conversation_id=5,
            model_profile_id=5, prompt=PROMPT, max_output_tokens=receipts.LIMITS["max_output_tokens"],
            build_messages=app_module.build_messages,
            plan_payload=chatgpt_plan.payload)
        db.commit()
    voice = rows["voice_profiles"][0]
    consent = rows["voice_privacy_consents"][0]
    prior = receipts.prepare(receipt_dir, args.run_id) if args.real_execute else None
    generation_id = (prior.manifest["generation_id"] if prior else
                     args.generation_id or uuid.uuid4().hex)
    manifest = {"schema_version": 1, "mode": "real_dynamic_candidate",
        "run_id": args.run_id or "dynamic-candidate-" + uuid.uuid4().hex[:16],
        "generation_id": generation_id,
        "owner_id": 1, "character_id": 1, "conversation_id": 5,
        "model_profile_id": 5, "context_sha256": frozen.context_sha256,
        "prompt": PROMPT, "profile_revision": voice["revision"],
        "voice_id_sha256": hashlib.sha256(voice["voice_id"].encode()).hexdigest(),
        "notice_version": consent["notice_version"],
        "chat_provider": "chatgpt_plan", "chat_model_id": "gpt-6.1-sol",
        "tts_model_id": "eleven_v4_turbo", "output_format": "pcm_24000",
        "language_code": "ja", "network_proxy_url": network_proxy(),
        "limits": receipts.LIMITS}
    if args.real_execute:
        prepared = prior
        if prepared.provider_kind != "real_approved" or prepared.manifest != manifest:
            raise SystemExit("approved_manifest_or_binding_changed")
        if not os.getenv("ELEVENLABS_API_KEY"):
            raise SystemExit("elevenlabs_key_not_configured")
    else:
        prepared = receipts.issue_mock(receipt_dir, manifest)
    gate = app_module.app.state.voice_v4_candidate
    gate.receipt_dir = receipt_dir
    gate.run_id = prepared.manifest["run_id"]
    if not args.real_execute:
        gate.model_events_factory = fake_model_events
        gate.ttd_connector_factory = lambda _voice_id: LocalFakeSupplier()
    print(json.dumps({"mode": "candidate_real_approved" if args.real_execute else "candidate_fake_only",
        "database_path": str(app_module.DB.resolve()),
        "source_database_read_only": str(DAILY_DB.resolve()),
        "run_id": gate.run_id, "manifest_sha256": prepared.manifest_sha256,
        "code_sha256": prepared.code_sha256,
        "context_sha256": frozen.context_sha256,
        "voice_id_sha256": manifest["voice_id_sha256"],
        "backend_port": args.port, "frontend_port": args.frontend_port},
        ensure_ascii=False), flush=True)
    if args.print_preview:
        preview = {"manifest": prepared.manifest,
            "model_request": frozen.public(),
            "manifest_sha256": prepared.manifest_sha256,
            "code_sha256": prepared.code_sha256,
            "provider_requests_dispatched": 0}
        if args.preview_output:
            args.preview_output.write_text(json.dumps(preview, ensure_ascii=False,
                indent=2) + "\n", encoding="utf-8")
        print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
        temporary.cleanup()
        return
    import uvicorn
    try:
        uvicorn.run(app_module.app, host="127.0.0.1", port=args.port,
                    log_level="warning", access_log=False, workers=1)
    finally:
        temporary.cleanup()


if __name__ == "__main__":
    main()
