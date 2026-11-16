"""Synthetic SSE, fake credentials, and a local one-use receipt only."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import voice_v4_field_shape_auto as auto
from scripts import voice_v4_field_capture as prior


ITEM = {"type": "message", "id": "synthetic-item", "role": "assistant",
        "phase": "final_answer"}
TEXT = "Synthetic public reply."


def events(envelope):
    return [
        {"type": "response.output_item.added", "item": ITEM},
        {"type": "response.output_text.delta", "item_id": ITEM["id"], "delta": TEXT},
        {"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": envelope}},
        {"type": "response.completed", "response": {"output": [{**ITEM,
            "content": [{"type": "output_text", "text": TEXT}]}],
            "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13}}},
    ]


def lines_factory(sequence, *, done=True, delay=0):
    async def lines(_manifest=None, _token=None):
        for event in sequence:
            yield f"event: {event['type']}"
            yield "data: " + json.dumps(event)
            yield ""
        if delay:
            await asyncio.sleep(delay)
        if done:
            yield "data: [DONE]"
            yield ""
    return lines


def synthetic_manifest():
    old = json.loads((auto.ROOT / "docs/VOICE_V4_FIELD_CAPTURE_CANDIDATE.json")
                     .read_text(encoding="utf-8"))["manifest"]
    value = {key: old[key] for key in ("source_manifest_sha256", "context_sha256",
             "model_request", "model_request_sha256", "endpoint", "network_proxy_url")}
    value.update(schema_version=1, mode="automatic_direct_field_shape_audit",
                 run_id="synthetic-field-auto-r1", generation_id="0" * 32,
                 limits=auto.LIMITS)
    auto.validate_manifest(value)
    return value


class AutoCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_sse_to_screened_report_preserves_restriction(self):
        collector = auto.AutoCollector()
        secret = "NEVER_STORE_NESTED_VALUE"
        envelope = {"auxiliaryFlag": {"nested": secret},
                    "accessToken": secret, "userName": "private", "bad\nfield": 7}
        result = await prior.process_sse(lines_factory(events(envelope))(),
                                         collector, capture_enabled=True)
        report = collector.report()
        rendered = json.dumps(report)
        self.assertEqual(result["model_status"], "completed")
        self.assertEqual(result["display_action"], "restrict")
        self.assertEqual(result["tts_dispatches"], 0)
        self.assertEqual(report["direct_field_entries"], 4)
        self.assertIn("auxiliaryFlag", rendered)
        for forbidden in ("accessToken", "userName", "bad\nfield", secret,
                          "nested", TEXT):
            self.assertNotIn(forbidden, rendered)
        masked = [row for row in report["events"][0]["fields"] if row["name"] is None]
        self.assertEqual(len(masked), 3)
        self.assertTrue(all(row["name_sha256"] is None for row in masked))
        self.assertEqual({row["value_type"] for row in masked},
                         {"string", "integer"})
        self.assertEqual(report["semantic_conclusion"],
                         "names_only_no_control_inference")

    async def test_explicit_show_still_zero_tts(self):
        decision = {"final_answer_moderation": {"item_id": ITEM["id"],
            "moderated": False, "display": {"mode": "show", "version": 1,
                "trustedContacts": "skip"}}}
        collector = auto.AutoCollector()
        result = await prior.process_sse(lines_factory(events(decision))(),
                                         collector, capture_enabled=True)
        self.assertEqual(result["display_action"], "allow")
        self.assertEqual(result["tts_dispatches"], 0)
        self.assertEqual(collector.report()["events"][0]["fields"][0]["name"],
                         "final_answer_moderation")

    async def test_observed_four_names_follow_bound_default_only(self):
        collector = auto.AutoCollector()
        observed_names = {"prompt": {}, "tool_call": {}, "tool_response": {},
                          "generation": {}}
        result = await prior.process_sse(lines_factory(events(observed_names))(),
                                         collector, capture_enabled=True)
        self.assertEqual(result["model_status"], "completed")
        self.assertEqual(result["display_action"], "allow")
        self.assertEqual(result["reason"],
                         "protocol_default_display_no_explicit_decision")
        self.assertEqual(result["tts_dispatches"], 0)
        self.assertEqual({row["name"] for row in collector.report()["events"][0]["fields"]},
                         set(observed_names))

        # Recognized non-display field names alone do not grant clearance.
        incomplete = await prior.process_sse(
            lines_factory(events(observed_names)[:-1])(), auto.AutoCollector(),
            capture_enabled=True)
        self.assertEqual(incomplete["display_action"], "restrict")
        self.assertEqual(incomplete["reason"], "model_terminal_missing_or_failed")
        self.assertEqual(incomplete["tts_dispatches"], 0)

        changed_body = events(observed_names)
        changed_body[-1]["response"]["output"][0]["content"][0]["text"] = "other"
        mismatched = await prior.process_sse(
            lines_factory(changed_body)(), auto.AutoCollector(), capture_enabled=True)
        self.assertEqual(mismatched["model_status"], "completed")
        self.assertEqual(mismatched["display_action"], "restrict")
        self.assertEqual(mismatched["reason"], "answer_binding_invalid")
        self.assertEqual(mismatched["tts_dispatches"], 0)

    async def test_field_budget_fails_closed_and_keeps_safe_partial(self):
        collector = auto.AutoCollector()
        sequence = events({"auxiliaryFlag": {}})[:3]
        sequence += [{"type": "response.metadata", "metadata": {
            "openai_chatgpt_moderation_metadata": {f"field{i}": {} for i in range(25)}}}]
        result = await prior.process_sse(lines_factory(sequence)(), collector,
                                         capture_enabled=True)
        self.assertEqual(result["model_status"], "failed")
        self.assertEqual(result["reason"], "field_count_exceeded")
        self.assertEqual(result["tts_dispatches"], 0)
        self.assertEqual(collector.report()["direct_field_entries"], 1)
        self.assertTrue(collector.closed)


class AutoEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(asyncio.to_thread, self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.manifest = synthetic_manifest()
        candidate = self.directory / "candidate.json"
        candidate.write_text(json.dumps({"manifest": self.manifest,
            "manifest_sha256": auto.digest(self.manifest),
            "code_sha256": auto.code_digest()}), encoding="utf-8")
        auto._approve(candidate, self.directory, auto.digest(self.manifest),
                      auto.code_digest())

    def enabled(self):
        return patch.dict(os.environ, {"YOSAKI_FIELD_SHAPE_AUTO_ENABLED": "1"})

    async def test_frozen_request_drift_is_rejected_before_execution(self):
        changed = json.loads(json.dumps(self.manifest))
        changed["model_request"]["request_body"]["max_output_tokens"] = 129
        changed["model_request_sha256"] = auto.digest(changed["model_request"])
        with self.assertRaisesRegex(auto.AutoReviewError, "manifest_invalid"):
            auto.validate_manifest(changed)
        self.assertFalse((self.directory / f"{self.manifest['run_id']}.used").exists())

    async def test_default_off_denies_before_token_or_stream(self):
        def forbidden():
            self.fail("credential must not be read")
        with self.assertRaisesRegex(auto.AutoReviewError, "auto_capture_disabled"):
            await auto.run_once(self.manifest, self.directory, forbidden,
                                lambda *_: None, enabled=False)
        self.assertFalse((self.directory / f"{self.manifest['run_id']}.used").exists())

    async def test_final_entry_writes_safe_report_and_reuse_is_rejected(self):
        name = "auxiliaryFlag"
        source = lines_factory(events({name: {"nested": "NEVER_LOG"}}))
        with self.enabled(), patch.object(prior, "_proxy", return_value=
                                          self.manifest["network_proxy_url"]):
            report = await auto.run_once(self.manifest, self.directory,
                                         lambda: "synthetic-token", source, enabled=True)
            with self.assertRaisesRegex(auto.AutoReviewError, "receipt_already_used"):
                await auto.run_once(self.manifest, self.directory,
                                    lambda: "synthetic-token", source, enabled=True)
        persisted = (self.directory / f"{self.manifest['run_id']}.field-shapes.json").read_text()
        self.assertEqual(report["model_status"], "completed")
        self.assertEqual(report["display_action"], "restrict")
        self.assertEqual(report["tts_dispatches"], 0)
        self.assertEqual(report["usage"], {"input_tokens": 9, "output_tokens": 4,
                                           "total_tokens": 13})
        self.assertIn(name, persisted)
        for forbidden in ("NEVER_LOG", "synthetic-token", TEXT, "nested"):
            self.assertNotIn(forbidden, persisted)

    async def test_timeout_leaves_safe_failure_report(self):
        original_seconds = auto.LIMITS["model_seconds"]
        try:
            auto.LIMITS["model_seconds"] = 0.01
            self.manifest["limits"]["model_seconds"] = 0.01
            self.manifest["run_id"] = "synthetic-field-auto-r2"
            candidate = self.directory / "candidate2.json"
            candidate.write_text(json.dumps({"manifest": self.manifest,
                "manifest_sha256": auto.digest(self.manifest),
                "code_sha256": auto.code_digest()}), encoding="utf-8")
            auto._approve(candidate, self.directory, auto.digest(self.manifest),
                          auto.code_digest())
            source = lines_factory(events({"safeField": {}})[:3], delay=1)
            with self.enabled(), patch.object(prior, "_proxy", return_value=
                                              self.manifest["network_proxy_url"]):
                report = await auto.run_once(self.manifest, self.directory,
                    lambda: "synthetic-token", source, enabled=True)
            self.assertEqual(report["reason"], "model_time_budget")
            self.assertEqual(report["tts_dispatches"], 0)
            self.assertEqual(report["direct_field_entries"], 1)
        finally:
            auto.LIMITS["model_seconds"] = original_seconds

    async def test_malformed_sse_reports_safe_failure_and_no_tts(self):
        async def malformed(_manifest, _token):
            for event in events({"auxiliaryFlag": {"nested": "NEVER_LOG"}})[:3]:
                yield f"event: {event['type']}"
                yield "data: " + json.dumps(event)
                yield ""
            yield "event: response.completed"
            yield "data: {invalid"
            yield ""
        with self.enabled(), patch.object(prior, "_proxy", return_value=
                                          self.manifest["network_proxy_url"]):
            report = await auto.run_once(self.manifest, self.directory,
                lambda: "synthetic-token", malformed, enabled=True)
        self.assertEqual(report["model_status"], "failed")
        self.assertEqual(report["display_action"], "restrict")
        self.assertEqual(report["tts_dispatches"], 0)
        rendered = (self.directory / f"{self.manifest['run_id']}.field-shapes.json").read_text()
        self.assertIn("auxiliaryFlag", rendered)
        self.assertNotIn("NEVER_LOG", rendered)

    async def test_cancellation_marks_receipt_used_and_saves_partial_report(self):
        async def cancelled(_manifest, _token):
            for event in events({"auxiliaryFlag": {}})[:3]:
                yield f"event: {event['type']}"
                yield "data: " + json.dumps(event)
                yield ""
            raise asyncio.CancelledError()
        with self.enabled(), patch.object(prior, "_proxy", return_value=
                                          self.manifest["network_proxy_url"]):
            with self.assertRaises(asyncio.CancelledError):
                await auto.run_once(self.manifest, self.directory,
                    lambda: "synthetic-token", cancelled, enabled=True)
        self.assertTrue((self.directory / f"{self.manifest['run_id']}.used").exists())
        report = json.loads((self.directory / f"{self.manifest['run_id']}.field-shapes.json")
                            .read_text())
        self.assertEqual(report["model_status"], "cancelled")
        self.assertEqual(report["tts_dispatches"], 0)


if __name__ == "__main__":
    unittest.main()
