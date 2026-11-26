"""Offline by default. A separate, bounded r2 idle-WS and Scribe follow-up probe."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from websockets.exceptions import InvalidStatus

if __package__:
    from . import voice_provider_probe as base
else:
    import voice_provider_probe as base


MANIFEST = Path(__file__).with_name("voice_v1b_ws_supplement_manifest.json")
SOURCE_AUDIO = {"v4_zh": "v4_zh.pcm", "v4_ja": "v4_ja.mp3",
                "turbo_zh": "turbo_zh.pcm", "turbo_ja": "turbo_ja.pcm"}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path = MANIFEST) -> dict:
    full = base.load_manifest()
    expected = {
        "schema_version": 1, "manifest_id": "yosaki-v1b-r2-ws-supplement-2026-11-10",
        "character_id": 1, "provider": "elevenlabs",
        "source_run_id": "v1b-20261110-r2",
        "source_manifest_sha256": base.digest(full),
        "source_result_sha256": "a7b1b6e84259fc0d3a94ed05ca3757e5fb0a09b8ce373c2d0d290f94ae823a9c",
        "source_approval_sha256": "5fe361d7a78ed92a297e9a99ade6d265e1f5f39988a31458897493a7b3081f0d",
        "source_used_sha256": "4be459f65750ab8dce07ce4682ceffed84711d87c096b0ef62e83dfcadcf6256",
        "model_realtime": "eleven_v4_turbo", "model_stt": "scribe_v2_realtime",
        "ttd_idle_ws": full["ttd_idle_ws"], "scribe_ws": full["scribe_ws"],
        "limits": {"http_requests": 0, "ws_connections": 2, "tts_characters": 0,
                   "stt_audio_seconds": 5.805, "ws_deadline_seconds": 50,
                   "wall_seconds": 50},
    }
    candidate = json.loads(path.read_text(encoding="utf-8"))
    if candidate != expected:
        raise base.ProbeError("supplement_manifest_scope_mismatch")
    return candidate


def verify_source(source_dir: Path, manifest: dict, voice_id: str, revision: int,
                  proxy_url: str | None) -> None:
    if source_dir.name != manifest["source_run_id"] or not source_dir.is_dir():
        raise base.ProbeError("source_run_invalid")
    result_file = source_dir / "result.json"
    used_file = source_dir.parent / (manifest["source_run_id"] + ".approval.json.used")
    approval_file = source_dir.parent / (manifest["source_run_id"] + ".approval.json")
    if (file_hash(result_file) != manifest["source_result_sha256"] or
            file_hash(approval_file) != manifest["source_approval_sha256"] or
            file_hash(used_file) != manifest["source_used_sha256"]):
        raise base.ProbeError("source_hash_mismatch")
    old_approval = json.loads(approval_file.read_text(encoding="utf-8"))
    voice_hash = hashlib.sha256(voice_id.encode("utf-8")).hexdigest()
    if (old_approval.get("run_id") != manifest["source_run_id"] or
            old_approval.get("voice_id_sha256") != voice_hash or
            old_approval.get("profile_revision") != revision or
            old_approval.get("network_proxy_url") != proxy_url):
        raise base.ProbeError("source_profile_or_proxy_changed")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    usage = result.get("usage", {})
    if (result.get("run_id") != manifest["source_run_id"] or
            result.get("manifest_sha256") != manifest["source_manifest_sha256"] or
            result.get("status") != "unknown" or
            result.get("error_code") != "ttd_idle_unexpected_event" or
            tuple(usage.get(k) for k in ("http_requests", "ws_connections",
                                          "tts_characters_dispatched",
                                          "stt_audio_seconds_dispatched")) != (4, 2, 78, 0.0)):
        raise base.ProbeError("source_result_mismatch")
    audio = result.get("audio", [])
    if (len(audio) != 4 or {entry.get("item"): entry.get("file") for entry in audio} != SOURCE_AUDIO or
            not all(entry.get("complete") is True for entry in audio)):
        raise base.ProbeError("source_audio_mismatch")
    for entry in audio:
        sound = source_dir / entry["file"]
        if file_hash(sound) != entry.get("sha256") or sound.stat().st_size != entry.get("bytes"):
            raise base.ProbeError("source_audio_mismatch")


def verify_approval(approval: dict, manifest: dict, run_id: str, voice_id: str,
                    revision: int, proxy_url: str | None) -> None:
    if not base.RUN_ID.fullmatch(run_id) or run_id == manifest["source_run_id"]:
        raise base.ProbeError("run_id_invalid")
    required = {
        "schema_version": 1, "run_id": run_id,
        "manifest_sha256": base.digest(manifest),
        "supplement_script_sha256": file_hash(Path(__file__)),
        "base_probe_sha256": file_hash(Path(base.__file__)),
        "source_run_id": manifest["source_run_id"],
        "source_result_sha256": manifest["source_result_sha256"],
        "source_approval_sha256": manifest["source_approval_sha256"],
        "source_used_sha256": manifest["source_used_sha256"],
        "voice_id_sha256": hashlib.sha256(voice_id.encode("utf-8")).hexdigest(),
        "character_id": 1, "profile_revision": revision,
        "limits": manifest["limits"],
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
        "network_proxy_url": proxy_url,
    }
    if not isinstance(approval, dict) or approval != required:
        raise base.ProbeError("approval_mismatch")


class SupplementRunner(base.Runner):
    def _http_budget(self, seconds: int, characters: int = 0) -> None:
        raise base.ProbeError("supplement_http_forbidden")

    def _ws_budget(self, seconds: int) -> None:
        usage = self.r.state["usage"]
        if usage["ws_connections"] >= 2 or self.ws_deadlines + seconds > 50:
            raise base.ProbeError("dispatch_budget_exceeded")
        usage["ws_connections"] += 1
        self.ws_deadlines += seconds
        self.r.save()

    async def ttd_ws(self) -> None:
        raise base.ProbeError("supplement_tts_forbidden")

    async def run_supplement(self, pcm: bytes) -> dict:
        if len(pcm) != 185760:
            raise base.ProbeError("fixture_size_mismatch")
        try:
            async with asyncio.timeout(50):
                await self.idle_ws()
                await self.scribe_ws(pcm)
            usage = self.r.state["usage"]
            if tuple(usage[k] for k in ("http_requests", "ws_connections",
                                        "tts_characters_dispatched",
                                        "stt_audio_seconds_dispatched")) != (0, 2, 0, 5.805):
                raise base.ProbeError("actual_usage_mismatch", unknown=True)
            self.r.state["status"] = "completed"
        except BaseException as exc:
            if isinstance(exc, base.ProbeError):
                error = exc
            elif isinstance(exc, InvalidStatus):
                status = getattr(getattr(exc, "response", None), "status_code", None)
                error = base.ProbeError("ws_http_" + str(status) if status in (401, 403, 429)
                                        else "ws_handshake_unknown", unknown=status not in (401, 403, 429))
            else:
                error = base.ProbeError("transport_or_protocol_unknown", unknown=True)
            self.r.state["status"] = "unknown" if error.unknown else "failed"
            self.r.state["error_code"] = error.code
            self.r.save()
            raise error from None
        self.r.save()
        return self.r.state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V1-B r2 WS-only follow-up; dry-run by default")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--source-run-dir", type=Path)
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if not args.execute_real:
            print(json.dumps({"mode": "dry-run", "manifest_id": manifest["manifest_id"],
                              "manifest_sha256": base.digest(manifest), "real_requests": 0,
                              "http_requests_limit": 0, "ws_connections_limit": 2,
                              "tts_characters_limit": 0, "stt_audio_seconds_limit": 5.805,
                              "wall_seconds_limit": 50, "provider_billing": "unverified",
                              "status": "no_network"}, ensure_ascii=False))
            return 0
        if not all((args.run_id, args.approval_file, args.database,
                    args.source_run_dir, args.results_dir)):
            raise base.ProbeError("real_arguments_missing")
        voice_id, revision = base.read_profile(args.database.resolve())
        proxy_url = base.network_proxy()
        verify_source(args.source_run_dir.resolve(), manifest, voice_id, revision, proxy_url)
        approval = json.loads(args.approval_file.read_text(encoding="utf-8"))
        verify_approval(approval, manifest, args.run_id, voice_id, revision, proxy_url)
        pcm = base.fixture_pcm(base.load_manifest())
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not key:
            raise base.ProbeError("api_key_not_configured")
        if args.results_dir.exists():
            raise base.ProbeError("results_directory_exists")
        used = args.approval_file.with_name(args.approval_file.name + ".used")
        try:
            with used.open("x", encoding="utf-8") as fp:
                fp.write(args.run_id + "\n")
        except FileExistsError as exc:
            raise base.ProbeError("approval_already_used") from exc
        record = base.Recorder(args.results_dir, args.run_id, manifest, simulated=False)
        record.state["source_run_id"] = manifest["source_run_id"]
        record.state["source_result_sha256"] = manifest["source_result_sha256"]
        record.save()
        asyncio.run(SupplementRunner(base.load_manifest(), record, key=key, voice_id=voice_id,
                                     dispatch_granted=True, proxy_url=proxy_url).run_supplement(pcm))
        print(json.dumps({"mode": "real", "run_id": args.run_id, "status": "completed",
                          "results": str(args.results_dir / "result.json")}, ensure_ascii=False))
        return 0
    except (base.ProbeError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, base.ProbeError) else "local_setup_failed"
        print(json.dumps({"mode": "real" if args.execute_real else "dry-run",
                          "status": "stopped", "error_code": code}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
