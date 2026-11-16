"""Default-off, model-only SSE field-shape capture with local native review.

No product WebSocket, TTD connector, database migration, or audio code is
imported. The real path requires a separately approved one-use receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterable
from urllib.parse import urlsplit

from backend.app import responses_adapter
from backend.app.voice.realtime_answer_display import AnswerDisplayGate
from scripts import voice_v4_field_capture_receipt as receipts
from scripts.voice_moderation_field_review import DirectFieldVault, FieldReviewError


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    raise FieldReviewError("non_json_direct_value")


def direct_shapes(event: dict) -> list[tuple[str, str]] | None:
    """Extract only direct names/types; never pass an envelope to the vault."""
    if event.get("type") != "response.metadata":
        return None
    outer = event.get("metadata")
    if type(outer) is not dict or "openai_chatgpt_moderation_metadata" not in outer:
        return None
    envelope = outer["openai_chatgpt_moderation_metadata"]
    if type(envelope) is not dict:
        raise FieldReviewError("moderation_envelope_not_object")
    if len(envelope) > 24:
        raise FieldReviewError("field_count_exceeded")
    if any(type(name) is not str for name in envelope):
        raise FieldReviewError("non_string_field_name")
    return [(name, _json_type(value)) for name, value in envelope.items()]


async def process_sse(lines: AsyncIterable[str], vault: DirectFieldVault,
                      *, capture_enabled: bool = False) -> dict:
    """Actual SSE decoder -> shape extraction -> existing display gate.

    The only return value is a safe status summary. There is no TTS dispatch
    branch, even when the display gate would otherwise allow the answer.
    """
    gate = AnswerDisplayGate()
    state = responses_adapter.StreamState()
    public = ""
    terminal = "missing"
    usage = None
    capture_events = 0
    try:
        async for event in responses_adapter.iter_sse_events(lines):
            kind = event.get("type")
            classification = responses_adapter.classify_candidate_event(event)
            if classification == "unsupported":
                raise FieldReviewError("model_event_unsupported")
            if capture_enabled and classification == "metadata_answer_moderation":
                shapes = direct_shapes(event)
                if shapes is not None:
                    vault.store(shapes)
                    capture_events += 1
            gate.observe(event)
            if classification == "metadata_answer_moderation":
                event = None  # Release the decoded event before waiting for the next frame.
                continue
            if classification in responses_adapter.METADATA_STOPS:
                raise FieldReviewError("metadata_control_unsupported")
            if classification == "notice":
                event = None
                continue
            if kind in {"error", "response.failed", "response.incomplete"}:
                terminal = ("failed" if kind != "response.incomplete" else "incomplete")
                break
            if terminal == "completed" and kind in {
                    "response.output_text.delta", "response.output_text.done"}:
                raise FieldReviewError("model_event_after_completion")
            normalized = state.normalize(event)
            delta = normalized["choices"][0]["delta"]
            if delta.get("tool_calls"):
                raise FieldReviewError("unexpected_tool_call")
            public += delta.get("content") or ""
            if len(public) > 1_000:
                raise FieldReviewError("public_text_limit")
            if kind == "response.completed":
                terminal = "completed"
                raw_usage = (event.get("response") or {}).get("usage")
                if type(raw_usage) is dict:
                    observed = {key: raw_usage[key] for key in
                        ("input_tokens", "output_tokens", "total_tokens")
                        if type(raw_usage.get(key)) is int and raw_usage[key] >= 0}
                    usage = observed or None
                    if observed.get("output_tokens", 0) > receipts.LIMITS["max_output_tokens"]:
                        raise FieldReviewError("model_output_budget_exceeded")
            event = None
        if terminal != "completed":
            vault.close()
            return {"model_status": terminal, "display_action": "restrict",
                    "reason": "model_terminal_missing_or_failed", "capture_events": capture_events,
                    "usage": usage, "tts_dispatches": 0}
        result = gate.finish(model_completed=True, public_text=public)
        return {"model_status": "completed", "display_action": result.action,
                "reason": result.reason, "capture_events": capture_events,
                "usage": usage, "tts_dispatches": 0}
    except asyncio.CancelledError:
        vault.close()
        raise
    except (FieldReviewError, responses_adapter.ResponseSSEDecodeError) as exc:
        vault.close()
        return {"model_status": "failed", "display_action": "restrict",
                "reason": (exc.args[0] if exc.args and isinstance(exc.args[0], str) else
                           "field_review_failed"), "capture_events": capture_events,
                "usage": usage, "tts_dispatches": 0}
    except Exception:
        vault.close()
        return {"model_status": "failed", "display_action": "restrict",
                "reason": "field_review_processing_failed", "capture_events": capture_events,
                "usage": usage, "tts_dispatches": 0}


def _proxy() -> str | None:
    proxies = urllib.request.getproxies()
    http, https = proxies.get("http"), proxies.get("https")
    if http and https and http != https:
        raise ValueError("proxy_routes_disagree")
    target = https or http
    if not target:
        return None
    url = urlsplit(target if "://" in target else "http://" + target)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or \
            url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("proxy_configuration_unsupported")
    return f"http://127.0.0.1:{url.port}"


def _read_token() -> str:
    """Only invoked by the separately approved real execution path."""
    from cryptography.fernet import Fernet
    source = receipts.ROOT / "data"
    key = os.getenv("YOSAKI_SECRET_KEY")
    raw_key = key.encode("utf-8") if key else (source / ".secret_key").read_bytes()
    account = json.loads(Fernet(raw_key).decrypt((source / ".chatgpt_plan").read_bytes()))["account"]
    token = account.get("access_token")
    if type(token) is not str or not token or \
            "chatgpt.tokens.use.direct" not in account.get("scopes", []) or \
            float(account.get("expires_at") or 0) <= time.time() + 90:
        raise ValueError("credential_not_fresh_90s")
    return token


async def _real_lines(manifest: dict, token: str):
    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5),
                                 follow_redirects=False,
                                 proxy=manifest["network_proxy_url"],
                                 trust_env=False) as client:
        async with client.stream("POST", manifest["endpoint"],
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=manifest["model_request"]["request_body"]) as response:
            if response.status_code != 200:
                raise ValueError("model_http_non_200")
            async for line in response.aiter_lines():
                yield line


def local_native_review(vault: DirectFieldVault, *, deadline: float | None = None) -> None:
    """Native, non-network display; raw names only enter a local Tk label."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("YOSAKI local field review")
    root.resizable(False, False)
    tk.Label(root, text="Only local reveal. Turn off terminal transcription and screen recording.").pack()
    try:
        shapes = vault.summary()
        labels = []
        for item in shapes["fields"]:
            row = tk.Frame(root)
            row.pack(fill="x")
            digest = item["name_sha256"]
            tk.Label(row, text=f"{digest[:12]}… {item['value_type']} {item['review_policy']}").pack(side="left")
            label = tk.Label(row, text="masked")
            label.pack(side="right")
            labels.append(label)
            if item["review_policy"] == "eligible_for_manual_review":
                def reveal(*, key=digest, target=label):
                    try:
                        value = vault.reveal(key, lambda _hash, _kind:
                            messagebox.askyesno("Allow local reveal",
                                "Show this one field name locally? This is not a sensitivity approval. "
                                "Confirm screen recording and remote sharing are off. "
                                "The name stays out of terminal output, but screen capture can retain it.",
                                parent=root))
                        target.configure(text=value["name"])
                    except Exception:
                        try:
                            target.configure(text="masked")
                        except Exception:
                            pass
                tk.Button(row, text="允许本地揭示", command=reveal).pack(side="right")

        def close() -> None:
            for label in labels:
                try:
                    label.configure(text="")
                except Exception:
                    pass
            vault.close()
            try:
                root.destroy()
            except Exception:
                pass

        def poll() -> None:
            if deadline is not None and time.monotonic() >= deadline:
                close()
                return
            try:
                vault.summary()
            except Exception:
                close()
                return
            root.after(250, poll)

        root.protocol("WM_DELETE_WINDOW", close)
        root.after(250, poll)
        root.mainloop()
    finally:
        vault.close()
        try:
            root.destroy()
        except tk.TclError:
            pass


async def _execute(manifest: dict, token: str, vault: DirectFieldVault) -> dict:
    try:
        async with asyncio.timeout(receipts.LIMITS["model_seconds"]):
            return await process_sse(_real_lines(manifest, token), vault,
                                     capture_enabled=True)
    except TimeoutError:
        vault.close()
        return {"model_status": "failed", "display_action": "restrict",
                "reason": "model_time_budget", "capture_events": 0,
                "usage": None, "tts_dispatches": 0}


def main() -> int:
    process_deadline = time.monotonic() + receipts.LIMITS["process_seconds"]
    parser = argparse.ArgumentParser(description="Default-off local field-shape capture")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--real-execute", action="store_true")
    parser.add_argument("--allow-local-reveal", action="store_true")
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    vault = DirectFieldVault()
    try:
        value = json.loads(args.manifest.read_text(encoding="utf-8"))
        manifest = value["manifest"]
        receipts.validate(manifest)
        if value.get("manifest_sha256") != receipts.digest(manifest) or \
                value.get("code_sha256") != receipts.code_digest():
            raise ValueError("frozen_candidate_changed")
        if not args.real_execute:
            print(json.dumps({"mode": "dry_run", "run_id": manifest["run_id"],
                              "model_requests": 0, "tts_dispatches": 0}))
            return 0
        if not args.allow_local_reveal or args.receipt_dir is None or \
                args.run_id != manifest["run_id"] or \
                os.getenv("YOSAKI_FIELD_SHAPE_CAPTURE_ENABLED", "0") != "1" or \
                _proxy() != manifest["network_proxy_url"]:
            raise ValueError("field_capture_not_authorized_or_proxy_changed")
        approved, manifest_hash, code_hash, _directory = receipts.prepare(
            args.receipt_dir, args.run_id)
        if approved != manifest or manifest_hash != value["manifest_sha256"] or \
                code_hash != value["code_sha256"]:
            raise ValueError("approval_binding_changed")
        token = _read_token()
        receipts.claim(args.receipt_dir, args.run_id)
        result = asyncio.run(_execute(manifest, token, vault))
        del token
        if time.monotonic() >= process_deadline:
            vault.close()
            raise ValueError("process_time_budget")
        print(json.dumps({"run_id": manifest["run_id"],
                          "model_status": result["model_status"],
                          "display_action": result["display_action"],
                          "reason": result["reason"], "usage": result["usage"],
                          "tts_dispatches": 0}, sort_keys=True))
        if result["model_status"] == "completed" and result["capture_events"]:
            local_native_review(vault, deadline=process_deadline)
        return 0 if result["model_status"] == "completed" else 1
    except (Exception, KeyboardInterrupt):
        print("field_capture_preflight_or_execution_failed")
        return 1
    finally:
        vault.close()  # Reference release, not verifiable memory erasure.


if __name__ == "__main__":
    raise SystemExit(main())
