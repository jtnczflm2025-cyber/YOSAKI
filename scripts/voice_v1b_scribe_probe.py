"""Scribe-only V1-B follow-up. Default invocation is offline dry-run."""

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


MANIFEST = Path(__file__).with_name("voice_v1b_scribe_manifest.json")
UNUSED_VOICE_ID = "not_sent_by_scribe_probe"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path = MANIFEST) -> dict:
    full = base.load_manifest()
    expected = {
        "schema_version": 1, "manifest_id": "yosaki-v1b-scribe-only-2026-11-10",
        "proposed_run_id": "v1b-20261110-scribe-r4", "provider": "elevenlabs",
        "source_run_id": "v1b-20261110-ws-r3",
        "source_result_sha256": "a72e84236b828e7ae93650bb39975d05769e56c98bfb59510fdd7aa1e356149c",
        "source_approval_sha256": "629d535d3d94e8c9d66318a8577802411cbfe1180fc9aabdfedcd10ed122be53",
        "source_used_sha256": "2b92b4d6672f28dbf10bfa0c89545229bf681bd02fe31d9ee8a70cbfb42cafba",
        "model_id": "scribe_v2_realtime", "endpoint": "/v1/speech-to-text/realtime",
        "scribe_ws": {**full["scribe_ws"], "commit_strategy": "manual", "language_code": "zh"},
        "limits": {"http_requests": 0, "ws_connections": 1, "tts_characters": 0,
                   "stt_audio_seconds": 5.805, "wall_seconds": 20},
    }
    candidate = json.loads(path.read_text(encoding="utf-8"))
    if candidate != expected:
        raise base.ProbeError("scribe_manifest_scope_mismatch")
    return candidate


def verify_source(source_dir: Path, manifest: dict) -> None:
    if source_dir.name != manifest["source_run_id"] or not source_dir.is_dir():
        raise base.ProbeError("source_run_invalid")
    result_file = source_dir / "result.json"
    approval_file = source_dir.parent / (manifest["source_run_id"] + ".approval.json")
    used_file = source_dir.parent / (manifest["source_run_id"] + ".approval.json.used")
    if (file_hash(result_file) != manifest["source_result_sha256"] or
            file_hash(approval_file) != manifest["source_approval_sha256"] or
            file_hash(used_file) != manifest["source_used_sha256"]):
        raise base.ProbeError("source_hash_mismatch")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    usage = result.get("usage", {})
    if (result.get("run_id") != manifest["source_run_id"] or
            result.get("status") != "unknown" or
            result.get("error_code") != "ttd_idle_unexpected_event" or
            tuple(usage.get(k) for k in ("http_requests", "ws_connections",
                                          "tts_characters_dispatched",
                                          "stt_audio_seconds_dispatched")) != (0, 1, 0, 0.0)):
        raise base.ProbeError("source_result_mismatch")


def verify_approval(approval: dict, manifest: dict, run_id: str,
                    proxy_url: str | None) -> None:
    if run_id != manifest["proposed_run_id"] or not base.RUN_ID.fullmatch(run_id):
        raise base.ProbeError("run_id_invalid")
    required = {
        "schema_version": 1, "run_id": run_id,
        "manifest_sha256": base.digest(manifest),
        "scribe_script_sha256": file_hash(Path(__file__)),
        "base_probe_sha256": file_hash(Path(base.__file__)),
        "source_run_id": manifest["source_run_id"],
        "source_result_sha256": manifest["source_result_sha256"],
        "source_approval_sha256": manifest["source_approval_sha256"],
        "source_used_sha256": manifest["source_used_sha256"],
        "fixture_sha256": manifest["scribe_ws"]["fixture_sha256"],
        "limits": manifest["limits"],
        "third_party_processing_confirmed_for_this_run": True,
        "billing_uncertainty_acknowledged": True,
        "network_proxy_url": proxy_url,
    }
    if not isinstance(approval, dict) or approval != required:
        raise base.ProbeError("approval_mismatch")


class ScribeRunner(base.Runner):
    def _http_budget(self, seconds: int, characters: int = 0) -> None:
        raise base.ProbeError("scribe_http_forbidden")

    def _ws_budget(self, seconds: int) -> None:
        usage = self.r.state["usage"]
        if usage["ws_connections"] >= 1 or self.ws_deadlines + seconds > 20:
            raise base.ProbeError("dispatch_budget_exceeded")
        usage["ws_connections"] += 1
        self.ws_deadlines += seconds
        self.r.save()

    async def idle_ws(self) -> None:
        raise base.ProbeError("scribe_ttd_forbidden")

    async def ttd_ws(self) -> None:
        raise base.ProbeError("scribe_ttd_forbidden")

    async def run_scribe(self, pcm: bytes) -> dict:
        if len(pcm) != 185760:
            raise base.ProbeError("fixture_size_mismatch")
        try:
            async with asyncio.timeout(20):
                await self.scribe_ws(pcm)
            usage = self.r.state["usage"]
            if tuple(usage[k] for k in ("http_requests", "ws_connections",
                                        "tts_characters_dispatched",
                                        "stt_audio_seconds_dispatched")) != (0, 1, 0, 5.805):
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
    parser = argparse.ArgumentParser(description="Scribe-only V1-B probe; dry-run by default")
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--source-run-dir", type=Path)
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if not args.execute_real:
            print(json.dumps({"mode": "dry-run", "manifest_id": manifest["manifest_id"],
                              "manifest_sha256": base.digest(manifest), "real_requests": 0,
                              "http_requests_limit": 0, "ws_connections_limit": 1,
                              "tts_characters_limit": 0, "stt_audio_seconds_limit": 5.805,
                              "wall_seconds_limit": 20, "provider_billing": "unverified",
                              "status": "no_network"}, ensure_ascii=False))
            return 0
        if not all((args.run_id, args.approval_file, args.source_run_dir,
                    args.results_dir)):
            raise base.ProbeError("real_arguments_missing")
        proxy_url = base.network_proxy()
        verify_source(args.source_run_dir.resolve(), manifest)
        approval = json.loads(args.approval_file.read_text(encoding="utf-8"))
        verify_approval(approval, manifest, args.run_id, proxy_url)
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
        asyncio.run(ScribeRunner(base.load_manifest(), record, key=key,
                                 voice_id=UNUSED_VOICE_ID, dispatch_granted=True,
                                 proxy_url=proxy_url).run_scribe(pcm))
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
