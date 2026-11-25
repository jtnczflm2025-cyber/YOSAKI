"""Frozen, standalone V3 benchmark input. Never installed in the product router.

This module reads the daily database in SQLite read-only mode. It never creates
messages, profiles, consents, V2 cache rows, or V2 audio bindings. Dispatch is
only possible through an explicitly supplied adapter and matching run receipt.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from . import audio_cache, config, storage
from .elevenlabs_adapter import ProviderError
from .expression_planner import EXPRESSION_PLANNER_VERSION, build
from .speech_planner import PLANNER_VERSION


ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "scripts" / "voice_v3_fixture_manifest.json"
EXPECTED_SAMPLES = (
    ("V3-JA-RESTRAINED", "大丈夫。今できることを一つずつ確認しよう。", ("none", "thoughtful")),
    ("V3-JA-RELAXED", "まあ、少し休んでから始めよう。", ("none", "relaxed")),
)
CODE_FILES = (
    "backend/app/voice/expression_fixture.py",
    "backend/app/voice/expression_planner.py",
    "backend/app/voice/speech_planner.py",
    "backend/app/voice/readable_text.py",
    "backend/app/voice/elevenlabs_adapter.py",
    "backend/app/voice/audio_cache.py",
    "backend/app/voice/storage.py",
    "backend/app/voice/config.py",
    "scripts/voice_v3_fixture_probe.py",
)


class FixtureError(Exception):
    def __init__(self, code: str, *, unknown: bool = False):
        super().__init__(code)
        self.code = code
        self.unknown = unknown


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_fingerprint() -> str:
    return digest({name: file_sha256(ROOT / name) for name in CODE_FILES})


def manifest() -> dict:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1, "manifest_id": "yosaki-v3-ja-pairs-2026-11-11",
        "character_id": 1, "provider": "elevenlabs",
        "voice_id_sha256": "e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767",
        "profile_revision": 3, "model_id": "eleven_v4",
        "output_format": "mp3_44100_128", "language_code": "ja",
        "samples": [{"sample_id": sample_id, "body": body, "variants": list(variants)}
                    for sample_id, body, variants in EXPECTED_SAMPLES],
        "limits": {"http_requests": 4, "request_characters": 95,
                   "timeout_seconds_each": 30, "max_audio_bytes_each": 16 * 1024 * 1024,
                   "automatic_retries": 0},
    }
    if value != expected:
        raise FixtureError("manifest_scope_mismatch")
    planned = [build(body, mode) for _, body, modes in EXPECTED_SAMPLES for mode in modes]
    bodies = [body for _, body, modes in EXPECTED_SAMPLES for _ in modes]
    if ([p.characters for p in planned] != [21, 34, 15, 25] or
            sum(p.characters for p in planned) != 95 or
            any(p.safe_text != body or p.language != "ja"
                for p, body in zip(planned, bodies))):
        raise FixtureError("fixture_planner_drift")
    return value


def _check_result_dir(path: Path, database: Path | None = None) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise FixtureError("result_dir_not_absolute")
    resolved = raw.resolve()
    protected = {config.data_dir().resolve(), (ROOT / "data").resolve()}
    if database is not None:
        protected.add(Path(database).resolve().parent)
    for data_dir in protected:
        if (resolved == data_dir or resolved.is_relative_to(data_dir)
                or data_dir.is_relative_to(resolved)):
            raise FixtureError("result_dir_overlaps_data")
    for part in (raw, *raw.parents):
        if part.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(part)):
            raise FixtureError("result_dir_linked")
    return resolved


def _write_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with temp.open("x", encoding="utf-8") as fp:
            json.dump(value, fp, ensure_ascii=False, sort_keys=True, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class FixtureEvaluator:
    """Two sample IDs, four variants, one sequential batch, no message IDs."""

    def __init__(self, database: Path, *, adapter_factory=None):
        self.database = Path(database).expanduser().resolve()
        if not self.database.is_file():
            raise FixtureError("database_not_found")
        self.spec = manifest()
        self.adapter_factory = adapter_factory

    def authority(self, owner_id: int = 1) -> dict:
        if owner_id != 1:
            raise FixtureError("owner_not_authorized")
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                actual = Path(db.execute("PRAGMA database_list").fetchone()[2]).resolve()
                if actual != self.database:
                    raise FixtureError("database_path_mismatch")
                if not db.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone():
                    raise FixtureError("owner_not_authorized")
                character = db.execute("SELECT enabled FROM characters WHERE id=1").fetchone()
                if not character or not character[0]:
                    raise FixtureError("character_unavailable")
                profile = storage.profile(db, 1)
                consent = storage.consent(db, owner_id)
        except sqlite3.Error as exc:
            raise FixtureError("database_read_failed") from exc
        if not profile or profile["provider"] != "elevenlabs" or not profile["active"]:
            raise FixtureError("profile_unavailable")
        if (profile["revision"] != self.spec["profile_revision"] or
                hashlib.sha256(profile["voice_id"].encode("utf-8")).hexdigest()
                != self.spec["voice_id_sha256"]):
            raise FixtureError("profile_changed")
        settings = profile["configuration"]
        if (settings["model_standard"] != self.spec["model_id"] or
                settings["audio_format"] != self.spec["output_format"]):
            raise FixtureError("profile_parameters_changed")
        if not consent or consent["notice_version"] != config.NOTICE_VERSION:
            raise FixtureError("third_party_consent_missing")
        return {"voice_id": profile["voice_id"], "voice_id_sha256": self.spec["voice_id_sha256"],
                "profile_revision": profile["revision"], "owner_id": owner_id,
                "api_key_configured": config.configured()}

    def previews(self, owner_id: int = 1) -> list[dict]:
        authority = self.authority(owner_id)
        effective = {"method": "POST", "endpoint": "/v1/text-to-dialogue",
                     "model_id": "eleven_v4", "output_format": "mp3_44100_128",
                     "language_code": "ja", "inputs_count": 1,
                     "settings": "omitted", "seed": "omitted",
                     "audio_effects": "omitted", "previous_text": "omitted",
                     "future_text": "omitted"}
        result = []
        for sample in self.spec["samples"]:
            for mode in sample["variants"]:
                planned = build(sample["body"], mode)
                if planned.safe_text != sample["body"] or planned.language != "ja":
                    raise FixtureError("fixture_planner_drift")
                basis = {"manifest_sha256": digest(self.spec),
                         "sample_id": sample["sample_id"], "mode": mode,
                         "request_text": planned.request_text,
                         "voice_id_sha256": authority["voice_id_sha256"],
                         "profile_revision": authority["profile_revision"],
                         "expression_planner_version": EXPRESSION_PLANNER_VERSION,
                         "v2_planner_version": PLANNER_VERSION,
                         "effective_parameters": effective}
                result.append({"sample_id": sample["sample_id"], "mode": mode,
                               "safe_text": planned.safe_text, "tag": planned.tag,
                               "request_text": planned.request_text,
                               "characters": planned.characters,
                               "input_sha256": hashlib.sha256(planned.request_text.encode("utf-8")).hexdigest(),
                               "preview_sha256": digest(basis),
                               "effective_parameters": effective,
                               "profile_revision": authority["profile_revision"],
                               "voice_id_sha256": authority["voice_id_sha256"],
                               "voice_id_masked": "***" + authority["voice_id"][-3:],
                               "expression_planner_version": EXPRESSION_PLANNER_VERSION,
                               "v2_planner_version": PLANNER_VERSION})
        if len(result) != 4 or sum(x["characters"] for x in result) != 95:
            raise FixtureError("fixture_scope_mismatch")
        return result

    def expected_approval(self, run_id: str, proxy_url: str | None = None) -> dict:
        if run_id != "v3-20261111-ab-r1":
            raise FixtureError("run_id_mismatch")
        current = self.authority()
        previews = self.previews()
        return {"schema_version": 1, "run_id": run_id,
                "manifest_sha256": digest(self.spec),
                "code_fingerprint": code_fingerprint(),
                "input_sha256": [p["input_sha256"] for p in previews],
                "preview_sha256": [p["preview_sha256"] for p in previews],
                "owner_id": 1, "character_id": 1,
                "voice_id_sha256": current["voice_id_sha256"],
                "profile_revision": current["profile_revision"],
                "limits": self.spec["limits"],
                "network_proxy_url": proxy_url,
                "third_party_processing_confirmed_for_this_run": True,
                "billing_uncertainty_acknowledged": True}

    async def run_batch(self, run_id: str, approval: dict, result_dir: Path,
                        *, proxy_url: str | None = None) -> dict:
        if self.adapter_factory is None:
            raise FixtureError("dispatch_gate_closed")
        if approval != self.expected_approval(run_id, proxy_url):
            raise FixtureError("approval_mismatch")
        if not self.authority()["api_key_configured"]:
            raise FixtureError("api_key_missing")
        previews = self.previews()
        root = _check_result_dir(result_dir, self.database)
        root.mkdir(parents=True, exist_ok=False)
        record = {"run_id": run_id, "manifest_sha256": digest(self.spec),
                  "code_fingerprint": code_fingerprint(), "status": "running",
                  "items": [{"sample_id": p["sample_id"], "mode": p["mode"],
                             "preview_sha256": p["preview_sha256"],
                             "input_sha256": p["input_sha256"],
                             "characters": p["characters"], "status": "pending"}
                            for p in previews], "dispatched_http": 0,
                  "dispatched_characters": 0, "actual_billing": "unknown"}
        output = root / "result.json"
        _write_json(output, record)
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        for index, preview in enumerate(previews):
            # Re-check all frozen inputs and permissions immediately before dispatch.
            try:
                valid = (self.previews()[index]["preview_sha256"] == preview["preview_sha256"]
                         and code_fingerprint() == approval["code_fingerprint"])
            except Exception:
                valid = False
            if not valid:
                record["status"] = "stopped"
                record["items"][index]["status"] = "failed_preflight"
                _write_json(output, record)
                return record
            item = record["items"][index]
            item["status"] = "dispatched"
            record["dispatched_http"] += 1
            record["dispatched_characters"] += preview["characters"]
            _write_json(output, record)
            started = time.monotonic()
            try:
                adapter = self.adapter_factory(key)
                async with asyncio.timeout(30):
                    audio = await adapter.synthesize_http(preview["request_text"],
                        self.authority()["voice_id"], "ja")
                if time.monotonic() - started >= 30:
                    raise TimeoutError("request_deadline")
                audio_cache.validate_mp3(audio)
                if (self.previews()[index]["preview_sha256"] != preview["preview_sha256"] or
                        code_fingerprint() != approval["code_fingerprint"]):
                    raise FixtureError("source_changed_after_dispatch", unknown=True)
                audio_path = root / f"{index + 1:02d}-{preview['sample_id']}-{preview['mode']}.mp3"
                part = audio_path.with_name(audio_path.name + "." + uuid.uuid4().hex + ".part")
                try:
                    with part.open("xb") as fp:
                        fp.write(audio)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(part, audio_path)
                finally:
                    part.unlink(missing_ok=True)
                item.update(status="succeeded", audio_file=audio_path.name,
                            audio_sha256=hashlib.sha256(audio).hexdigest(),
                            audio_bytes=len(audio))
            except asyncio.CancelledError:
                item["status"] = "unknown"
                record["status"] = "stopped"
                _write_json(output, record)
                raise
            except ProviderError as exc:
                item["status"] = "unknown" if exc.unknown else "failed"
                item["error_code"] = "provider_unknown" if exc.unknown else "provider_rejected"
            except Exception:
                item["status"] = "unknown"
                item["error_code"] = "result_unknown"
            _write_json(output, record)
            if item["status"] != "succeeded":
                record["status"] = "stopped"
                _write_json(output, record)
                return record
        record["status"] = "completed"
        _write_json(output, record)
        return record
