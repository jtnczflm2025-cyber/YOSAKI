"""Synthetic raw SSE through the actual decoder, gate, and local shape vault."""

import asyncio
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.voice_moderation_field_review import DirectFieldVault, FieldReviewError
from scripts import voice_v4_field_capture_receipt as receipts
from scripts.voice_v4_field_capture import (
    _execute, direct_shapes, local_native_review, main, process_sse,
)


ITEM = {"type": "message", "id": "synthetic-item", "role": "assistant",
        "phase": "final_answer"}
TEXT = "Synthetic public reply."


def frame(event):
    return [f"event: {event['type']}", "data: " + json.dumps(event), ""]


def stream(*events, done=True):
    async def lines():
        for event in events:
            for line in frame(event):
                yield line
        if done:
            yield "data: [DONE]"
            yield ""
    return lines()


def normal_events(envelope):
    return (
        {"type": "response.output_item.added", "item": ITEM},
        {"type": "response.output_text.delta", "item_id": ITEM["id"], "delta": TEXT},
        {"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": envelope}},
        {"type": "response.completed", "response": {"output": [{**ITEM,
            "content": [{"type": "output_text", "text": TEXT}]}],
            "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13}}},
    )


class FieldCaptureSSETests(unittest.IsolatedAsyncioTestCase):
    async def test_default_off_restricts_but_collects_no_names_or_tts(self):
        vault = DirectFieldVault()
        result = await process_sse(stream(*normal_events({"privateFlag": {"secret": "NEVER_LOG"}})), vault)
        self.assertEqual(result["model_status"], "completed")
        self.assertEqual(result["display_action"], "restrict")
        self.assertEqual(result["capture_events"], 0)
        self.assertEqual(result["tts_dispatches"], 0)
        with self.assertRaises(FieldReviewError):
            vault.summary()
        self.assertNotIn("privateFlag", repr(result))
        self.assertNotIn("NEVER_LOG", repr(result))
        vault.close()

    async def test_actual_decoder_to_gate_to_local_per_field_reveal(self):
        vault = DirectFieldVault()
        result = await process_sse(stream(*normal_events({"auxiliaryFlag": {"secret": "NEVER_LOG"}})),
                                   vault, capture_enabled=True)
        self.assertEqual(result["model_status"], "completed")
        self.assertEqual(result["display_action"], "restrict")
        self.assertEqual(result["usage"], {"input_tokens": 9,
                         "output_tokens": 4, "total_tokens": 13})
        self.assertEqual(result["tts_dispatches"], 0)
        self.assertNotIn("auxiliaryFlag", repr(result))
        summary = vault.summary()
        self.assertNotIn("auxiliaryFlag", repr(summary))
        self.assertEqual(summary["field_count"], 1)
        digest = hashlib.sha256(b"auxiliaryFlag").hexdigest()
        self.assertEqual(summary["fields"][0]["name_sha256"], digest)
        with self.assertRaisesRegex(FieldReviewError, "manual_review_declined"):
            vault.reveal(digest, lambda _hash, _kind: False)
        self.assertEqual(vault.reveal(digest, lambda _hash, _kind: True),
                         {"name": "auxiliaryFlag", "value_type": "object"})
        vault.close()

    async def test_show_never_dispatches_tts_in_capture_mode(self):
        decision = {"final_answer_moderation": {"item_id": ITEM["id"],
            "moderated": False, "display": {"mode": "show", "version": 1,
                "trustedContacts": "skip"}}}
        vault = DirectFieldVault()
        result = await process_sse(stream(*normal_events(decision)), vault,
                                   capture_enabled=True)
        self.assertEqual(result["display_action"], "allow")
        self.assertEqual(result["tts_dispatches"], 0)
        vault.close()

    async def test_too_many_fields_and_malformed_name_release_references(self):
        for envelope, code in (({f"field{i}": {} for i in range(25)}, "field_count_exceeded"),):
            with self.subTest(code=code):
                vault = DirectFieldVault()
                result = await process_sse(stream(*normal_events(envelope)), vault,
                                           capture_enabled=True)
                self.assertEqual(result["model_status"], "failed")
                self.assertEqual(result["reason"], code)
                self.assertEqual(result["tts_dispatches"], 0)
                self.assertNotIn("bad\nname", repr(result))
                self.assertEqual(vault._shapes, ())
        with self.assertRaisesRegex(FieldReviewError, "non_string_field_name"):
            direct_shapes({"type": "response.metadata", "metadata": {
                "openai_chatgpt_moderation_metadata": {1: {}}}})

    async def test_parse_error_eof_without_terminal_and_failure_clear(self):
        metadata = normal_events({"privateFlag": {}})[:3]
        async def bad_lines():
            for event in metadata:
                for line in frame(event):
                    yield line
            yield "event: response.completed"
            yield "data: {invalid"
            yield ""
        scenarios = [bad_lines(), stream(*metadata, done=True),
                     stream(*metadata, {"type": "response.failed", "response": {}}, done=True)]
        for source in scenarios:
            vault = DirectFieldVault()
            result = await process_sse(source, vault, capture_enabled=True)
            self.assertEqual(result["display_action"], "restrict")
            self.assertEqual(result["tts_dispatches"], 0)
            self.assertEqual(vault._shapes, ())

    async def test_cancel_releases_references(self):
        metadata = normal_events({"privateFlag": {}})[:3]
        async def cancelled_lines():
            for event in metadata:
                for line in frame(event):
                    yield line
            raise asyncio.CancelledError()
        vault = DirectFieldVault()
        with self.assertRaises(asyncio.CancelledError):
            await process_sse(cancelled_lines(), vault, capture_enabled=True)
        self.assertEqual(vault._shapes, ())

    async def test_transport_drop_and_model_timeout_release(self):
        metadata = normal_events({"auxiliaryFlag": {}})[:3]
        async def broken_lines():
            for event in metadata:
                for line in frame(event):
                    yield line
            raise ConnectionResetError("private raw transport details never reported")
        vault = DirectFieldVault()
        result = await process_sse(broken_lines(), vault, capture_enabled=True)
        self.assertEqual(result["reason"], "field_review_processing_failed")
        self.assertNotIn("private raw", repr(result))
        self.assertEqual(vault._shapes, ())

        async def slow_lines(_manifest, _token):
            for event in metadata:
                for line in frame(event):
                    yield line
            await asyncio.sleep(1)
        vault = DirectFieldVault()
        with patch("scripts.voice_v4_field_capture._real_lines", slow_lines), \
                patch.dict("scripts.voice_v4_field_capture.receipts.LIMITS",
                           {"model_seconds": 0.01}):
            result = await _execute({}, "synthetic-token", vault)
        self.assertEqual(result["reason"], "model_time_budget")
        self.assertEqual(result["tts_dispatches"], 0)
        self.assertEqual(vault._shapes, ())

    async def test_sensitive_field_name_never_revealed_on_actual_sse_path(self):
        vault = DirectFieldVault()
        result = await process_sse(stream(*normal_events({"accessToken": {}})),
                                   vault, capture_enabled=True)
        self.assertEqual(result["tts_dispatches"], 0)
        summary = vault.summary()
        self.assertNotIn("accessToken", repr(summary))
        digest = hashlib.sha256(b"accessToken").hexdigest()
        with self.assertRaisesRegex(FieldReviewError, "sensitive_name_pattern"):
            vault.reveal(digest, lambda _hash, _kind: True)
        vault.close()


class FieldCaptureEntryTests(unittest.TestCase):
    def test_synthetic_ephemeral_receipt_is_single_use(self):
        request = {"provider": "chatgpt_plan", "model": "gpt-6.1-sol",
                   "owner_id": 1, "character_id": 1, "conversation_id": 5,
                   "model_profile_id": 5, "context_sha256": "0" * 64,
                   "request_body": {"model": "gpt-6.1-sol", "input": [
                       {"role": "user", "content": "synthetic only"}],
                       "max_output_tokens": 128, "stream": True, "store": False}}
        manifest = {"schema_version": 1, "mode": "local_field_shape_review",
                    "run_id": "synthetic-local-only-r1", "generation_id": "0" * 32,
                    "source_manifest_sha256": "1" * 64,
                    "context_sha256": "0" * 64, "model_request": request,
                    "model_request_sha256": receipts.digest(request),
                    "endpoint": "https://api.openai.com/v1/responses",
                    "network_proxy_url": "http://127.0.0.1:7897",
                    "limits": receipts.LIMITS}
        receipts.validate(manifest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-local-only-r1.approval.json"
            path.write_text(json.dumps({"manifest": manifest,
                "manifest_sha256": receipts.digest(manifest),
                "code_sha256": receipts.code_digest(),
                "provider_kind": "real_approved"}), encoding="utf-8")
            self.assertEqual(receipts.prepare(Path(directory), manifest["run_id"])[0],
                             manifest)
            receipts.claim(Path(directory), manifest["run_id"])
            with self.assertRaisesRegex(receipts.ReceiptError, "receipt_already_used"):
                receipts.prepare(Path(directory), manifest["run_id"])

    def test_hidden_native_window_reveals_only_after_local_confirmation(self):
        import tkinter as tk
        from tkinter import messagebox
        original_tk = tk.Tk
        observed = []
        vault = DirectFieldVault()
        vault.store([("sampleMetadata", "object")])

        def root_factory():
            root = original_tk()
            root.withdraw()  # Synthetic widget test never shows a real name.

            def exercise():
                children = [item for frame in root.winfo_children()
                            for item in ([frame] + list(frame.winfo_children()))]
                buttons = [item for item in children if isinstance(item, tk.Button)]
                self.assertEqual(len(buttons), 1)
                buttons[0].invoke()
                labels = [item for item in children if isinstance(item, tk.Label)]
                observed.extend(item.cget("text") for item in labels)
                root.destroy()

            root.after(30, exercise)
            return root

        with patch("tkinter.Tk", side_effect=root_factory), \
                patch.object(messagebox, "askyesno", return_value=True) as confirmation:
            local_native_review(vault)
        self.assertEqual(confirmation.call_count, 1)
        self.assertIn("sampleMetadata", observed)
        self.assertEqual(vault._shapes, ())

    def test_old_frozen_candidate_rejects_and_current_dry_run_stays_offline(self):
        manifest = Path(__file__).resolve().parents[1] / "docs" / \
            "VOICE_V4_FIELD_CAPTURE_CANDIDATE.json"
        run_id = "voice-v4-field-review-20261114-r1"
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.voice_v4_field_capture._read_token") as token, \
                patch("scripts.voice_v4_field_capture._real_lines") as provider, \
                redirect_stdout(io.StringIO()) as output:
            # The historical candidate is pinned to its original code bytes.
            # A later display-gate revision must reject it before any I/O.
            original = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertNotEqual(original["code_sha256"], receipts.code_digest())
            with patch.object(sys, "argv", ["capture", "--manifest", str(manifest)]):
                self.assertEqual(main(), 1)
            self.assertIn("field_capture_preflight_or_execution_failed",
                          output.getvalue())

            # Local dry-run fixture binds current source only; it is not an
            # approval receipt and cannot enable the real execution path.
            current = dict(original)
            current["code_sha256"] = receipts.code_digest()
            current_path = Path(directory) / "current-code-dry-run.json"
            current_path.write_text(json.dumps(current), encoding="utf-8")
            with patch.object(sys, "argv", ["capture", "--manifest", str(current_path)]):
                self.assertEqual(main(), 0)
            self.assertIn('"mode": "dry_run"', output.getvalue())
            self.assertIn('"model_requests": 0', output.getvalue())
            with patch.dict(os.environ, {"YOSAKI_FIELD_SHAPE_CAPTURE_ENABLED": "0"}), \
                    patch.object(sys, "argv", ["capture", "--manifest", str(current_path),
                        "--real-execute", "--allow-local-reveal", "--run-id", run_id,
                        "--receipt-dir", directory]):
                self.assertEqual(main(), 1)
            token.assert_not_called()
            provider.assert_not_called()
            with self.assertRaisesRegex(receipts.ReceiptError, "receipt_missing"):
                receipts.prepare(Path(directory), run_id)


if __name__ == "__main__":
    unittest.main()
