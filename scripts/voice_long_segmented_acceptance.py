"""Isolated V2 route acceptance for one reviewed long-text fixture.

Default mode previews with zero provider calls. The optional real mode is
single-use, fingerprint-bound, and must be separately authorized. It never
imports main.py or writes the daily database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import getproxies

from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from tests.test_voice_v2 import VoiceV2


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "voice_long_two_request_zh.txt"
LIVE_DB = ROOT / "data" / "yosaki.sqlite3"
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{5,79}$")
EXPECTED_VOICE_SHA256 = "e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767"
EXPECTED_NOTICE = "2026-11-08-v1"
EXPECTED_REVISION = 3
EXPECTED_LENGTHS = [1202, 1236]
DELIVERY_FIXTURE = FIXTURE.with_name("voice_delivery_two_segment_zh.txt")
DELIVERY_LENGTHS = [1135, 1163]
EXPECTED_MODEL = "eleven_v4"
EXPECTED_FORMAT = "mp3_44100_128"
EXPECTED_LANGUAGE = "zh"
EXPECTED_PROXY = "http://127.0.0.1:7897"
PLAYER_HTML = """<!doctype html>
<html lang="zh"><meta charset="utf-8"><title>YOSAKI 长文两段试听</title>
<body><h1>两段长文试听</h1><p>这只播放已保存的本地音频，不会生成或发送文字。</p>
<button id="play" type="button">播放两段</button>
<button id="stop" type="button">停止</button><p id="status" role="status">未播放</p>
<script>
const files=['segment-1.mp3','segment-2.mp3'];
const audio=new Audio(); let epoch=0, index=0;
const status=document.getElementById('status');
function stop(){epoch++; audio.pause(); audio.onended=null; audio.onerror=null;
  audio.removeAttribute('src'); audio.load();
  status.textContent='已停止';}
async function next(current){
  if(current!==epoch)return;
  if(index>=files.length){status.textContent='两段已播完';return;}
  audio.src=files[index++]; status.textContent=`正在播放第 ${index} 段／共 2 段`;
  audio.onended=()=>{void next(current);};
  audio.onerror=()=>{if(current===epoch)status.textContent='音频读取失败';};
  try{await audio.play();}catch{if(current===epoch)status.textContent='播放失败';}
}
document.getElementById('play').addEventListener('click',()=>{
  stop(); index=0; const current=epoch; void next(current);
});
document.getElementById('stop').addEventListener('click',stop);
</script></body></html>"""
CODE_FILES = """backend/app/main.py
backend/app/chatgpt_plan.py
backend/app/responses_adapter.py
backend/app/voice/config.py
backend/app/voice/storage.py
backend/app/voice/manager.py
backend/app/voice/continuous_call.py
backend/app/voice/continuous_daily.py
backend/app/voice/continuous_batch.py
backend/app/voice/scribe_budget.py
backend/app/voice/scribe_draft.py
backend/app/voice/scribe_adapter.py
backend/app/voice/auto_budget.py
backend/app/voice/speech_planner.py
backend/app/voice/readable_text.py
backend/app/voice/audio_jobs.py
backend/app/voice/realtime_product.py
backend/app/voice/realtime_output.py
backend/app/voice/realtime_ttd_connector.py
frontend/src/App.vue
frontend/src/voice/MessageAudioPlayer.vue
frontend/src/voice/MessageRealtimeOutput.vue
frontend/src/voice/ScribeDraftInput.vue
frontend/src/voice/localBargeIn.ts
frontend/src/voice/localBargeDetector.ts
frontend/src/voice/scribeCapture.ts
frontend/src/voice/realtimeOutput.ts
frontend/vite.config.ts
scripts/yosaki_runtime.py
scripts/start_yosaki_voice_daily.ps1
scripts/start_yosaki_voice_daily_frontend.ps1
scripts/voice_long_segmented_acceptance.py
tests/test_voice_v2.py""".splitlines()
CODE_FILES = sorted(set(CODE_FILES) | {
    path.relative_to(ROOT).as_posix()
    for path in (ROOT / "backend" / "app" / "voice").rglob("*.py")
})


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_collection_sha256() -> str:
    lines = [path + "\x00" + sha256((ROOT / path).read_bytes()) + "\n"
             for path in sorted(CODE_FILES)]
    return sha256("".join(lines).encode("utf-8"))


def checked_proxy() -> str:
    """Freeze the already reviewed local proxy; never silently take another route."""
    proxies = getproxies()
    http, https = proxies.get("http"), proxies.get("https")
    if http and https and http != https:
        raise ValueError("proxy_routes_disagree")
    value = https or http
    if not value:
        raise ValueError("proxy_missing")
    url = urlsplit(value if "://" in value else "http://" + value)
    normalized = f"{url.scheme}://{url.hostname}:{url.port}"
    if (normalized != EXPECTED_PROXY or url.username or url.password or
            url.path or url.query or url.fragment):
        raise ValueError("proxy_binding_changed")
    return normalized


def profile_from_live() -> tuple[str, int, str, int]:
    db = sqlite3.connect(LIVE_DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        profile = db.execute("""SELECT voice_id,revision,configuration_json,active
            FROM voice_profiles WHERE character_id=1""").fetchone()
        notice = db.execute("""SELECT notice_version FROM voice_privacy_consents
            WHERE owner_id=1 AND provider='elevenlabs'""").fetchone()
    finally:
        db.close()
    if (profile is None or not profile[3] or profile[1] != EXPECTED_REVISION or
            sha256(profile[0].encode()) != EXPECTED_VOICE_SHA256 or
            notice is None or notice[0] != EXPECTED_NOTICE):
        raise ValueError("voice_profile_or_notice_changed")
    return profile


def prepare(fixture: Path = FIXTURE) -> tuple[VoiceV2, dict]:
    profile = profile_from_live()
    lengths = fixture_lengths(fixture)
    body = fixture.read_text(encoding="utf-8")
    voice = VoiceV2("test_gates_and_preview_have_zero_provider_calls")
    voice.setUp()
    try:
        with voice.connect() as db:
            db.execute("UPDATE messages SET content=? WHERE id=10", (body,))
            db.execute("""UPDATE voice_profiles SET voice_id=?,revision=?,
                configuration_json=?,active=? WHERE character_id=1""", profile)
        response = voice.client.get("/api/voice/messages/10/speech-preview")
        if response.status_code != 200:
            raise ValueError("long_preview_rejected")
        preview = response.json()
        if (preview["profile_revision"] != EXPECTED_REVISION or
                preview["characters"] != sum(lengths) or
                preview["requests"] != 2 or
                [len(part) for part in preview["segments"]] != lengths or
                preview["model_id"] != EXPECTED_MODEL or
                preview["output_format"] != EXPECTED_FORMAT or
                preview["language_code"] != EXPECTED_LANGUAGE or voice.calls):
            raise ValueError("long_preview_binding_changed")
        return voice, preview
    except BaseException:
        voice.tearDown()
        raise


def fixture_lengths(fixture: Path) -> list[int]:
    if fixture == FIXTURE:
        return EXPECTED_LENGTHS
    if fixture == DELIVERY_FIXTURE:
        return DELIVERY_LENGTHS
    raise ValueError("long_fixture_not_supported")


def execute(voice: VoiceV2, preview: dict, run_id: str,
            expected_preview: str, expected_fixture: str,
            expected_code: str, *, transport=None,
            run_root: Path | None = None,
            provider_key: str | None = None, fixture: Path = FIXTURE) -> dict:
    character_limit = sum(fixture_lengths(fixture))
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("run_id_invalid")
    if (preview["preview_sha256"] != expected_preview or
            sha256(fixture.read_bytes()) != expected_fixture or
            code_collection_sha256() != expected_code):
        raise ValueError("approved_binding_changed")
    profile_from_live()  # Reject a revision, Voice ID, or notice change before claiming a run.
    proxy = checked_proxy()
    key = (provider_key if provider_key is not None else
           os.getenv("ELEVENLABS_API_KEY", "")).strip()
    if not key:
        raise ValueError("provider_key_missing")
    if run_root is None:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise ValueError("run_directory_unavailable")
        root = Path(local_app_data) / "YOSAKI" / "voice-runs"
    else:
        root = run_root
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / run_id
    run_dir.mkdir(mode=0o700, exist_ok=False)  # Atomic one-use claim; never retry a used run ID.
    receipt = {"run_id": run_id, "state": "occupied_unknown",
               "preview_sha256": expected_preview, "fixture_sha256": expected_fixture,
               "code_collection_sha256": expected_code,
               "voice_id_sha256": EXPECTED_VOICE_SHA256,
               "profile_revision": EXPECTED_REVISION,
               "notice_version": EXPECTED_NOTICE,
               "proxy_url": proxy,
               "http_limit": 2, "character_limit": character_limit,
               "per_request_seconds": 30, "per_segment_bytes": 16 * 1024 * 1024}
    (run_dir / "receipt.json").write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    attempts: list[int] = []

    class CountedAdapter(ElevenLabsAdapter):
        async def synthesize_http(self, text: str, voice_id: str, language_code: str) -> bytes:
            if len(attempts) >= 2 or sum(attempts) + len(text) > character_limit:
                raise ValueError("long_provider_budget_exhausted")
            attempts.append(len(text))  # Attempted send is never refunded.
            return await super().synthesize_http(text, voice_id, language_code)

    voice.jobs.adapter_factory = lambda supplied_key: CountedAdapter(
        supplied_key, transport=transport,
        proxy_url=proxy if transport is None else None)
    previous_key = os.environ.get("ELEVENLABS_API_KEY")
    os.environ["ELEVENLABS_API_KEY"] = key
    report: dict = {"run_id": run_id, "status": "unknown", "http_attempts": 0,
                    "attempted_characters": 0, "audio": []}
    try:
        response = voice.client.post("/api/voice/messages/10/speak",
                                     json={"preview_sha256": expected_preview})
        if response.status_code != 202:
            report["status"] = "dispatch_rejected"
            report["http_status"] = response.status_code
            return report
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 270
        while time.monotonic() < deadline:
            result = voice.client.get(f"/api/voice/jobs/{job_id}")
            if result.status_code != 200:
                report["status"] = "job_status_unavailable"
                return report
            job = result.json()
            if job["status"] not in ("queued", "generating"):
                report["status"] = job["status"]
                report["failure_phase"] = job.get("failure_phase")
                report["failed_segment"] = job.get("failed_segment")
                if job["status"] == "completed":
                    audio = job["audio"]
                    if len(audio) != 2:
                        report["status"] = "audio_segment_count_unknown"
                        return report
                    for number, item in enumerate(audio, 1):
                        file_response = voice.client.get(item["url"])
                        if file_response.status_code != 200:
                            report["status"] = "audio_read_failed"
                            return report
                        filename = f"segment-{number}.mp3"
                        payload = file_response.content
                        (run_dir / filename).write_bytes(payload)
                        report["audio"].append({"file": filename, "bytes": len(payload),
                                                "sha256": sha256(payload)})
                    (run_dir / "player.html").write_text(PLAYER_HTML, encoding="utf-8")
                    report["player"] = "player.html"
                return report
            time.sleep(0.1)
        report["status"] = "job_timeout_unknown"
        return report
    finally:
        if previous_key is None:
            os.environ.pop("ELEVENLABS_API_KEY", None)
        else:
            os.environ["ELEVENLABS_API_KEY"] = previous_key
        report["http_attempts"] = len(attempts)
        report["attempted_characters"] = sum(attempts)
        (run_dir / "report.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        if report["status"] == "completed":
            receipt["state"] = "consumed"
            (run_dir / "receipt.json").write_text(
                json.dumps(receipt, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-execute", action="store_true")
    parser.add_argument("--delivery-fixture", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--approved-preview-sha256", default="")
    parser.add_argument("--approved-fixture-sha256", default="")
    parser.add_argument("--approved-code-sha256", default="")
    args = parser.parse_args()
    if args.real_execute and not all((args.run_id, args.approved_preview_sha256,
                                      args.approved_fixture_sha256,
                                      args.approved_code_sha256)):
        parser.error("real execution requires approved run and all fingerprints")
    provider_key = os.getenv("ELEVENLABS_API_KEY", "").strip() if args.real_execute else None
    fixture = DELIVERY_FIXTURE if args.delivery_fixture else FIXTURE
    voice, preview = prepare(fixture)
    try:
        if not args.real_execute:
            print(json.dumps({"mode": "dry_run", "message_id": 10,
                              "segments": fixture_lengths(fixture),
                              "fixture_sha256": sha256(fixture.read_bytes()), "characters": preview["characters"],
                              "requests": preview["requests"],
                              "preview_sha256": preview["preview_sha256"],
                              "provider_requests": len(voice.calls),
                              "code_sha256": code_collection_sha256()}))
        else:
            print(json.dumps(execute(voice, preview, args.run_id,
                                     args.approved_preview_sha256,
                                     args.approved_fixture_sha256,
                                     args.approved_code_sha256,
                                     provider_key=provider_key, fixture=fixture)))
    finally:
        voice.tearDown()


if __name__ == "__main__":
    main()
