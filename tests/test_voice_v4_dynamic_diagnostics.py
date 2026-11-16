"""Private candidate evidence bounds; no app import or external connection."""

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.app.voice.realtime_dynamic_diagnostics import (
    DiagnosticCapacityError, DiagnosticWriteError, RunDiagnostics,
    bounded_event_type,
)


class DiagnosticBounds(unittest.TestCase):
    def test_event_type_identifier_and_hash_are_bounded(self):
        self.assertEqual(bounded_event_type("response.reasoning_summary_part.added"),
                         {"event_type": "response.reasoning_summary_part.added"})
        for unsafe in ("response.\nprivate", "x" * 97, "response.\ud800", {"private": "text"}):
            value = bounded_event_type(unsafe)
            self.assertEqual(value["event_type"], "unrecognized")
            self.assertEqual(len(value["event_type_sha256"]), 64)
            self.assertNotIn("private", json.dumps(value))
        self.assertEqual(bounded_event_type("response.\nprivate")
                         ["event_type_sha256"],
                         hashlib.sha256("response.\nprivate".encode()).hexdigest())

    def test_high_volume_cap_keeps_explicit_marker_and_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            log = RunDiagnostics(Path(root), "fake-run-001", "a" * 32)
            for _ in range(700):
                log.record("model_event", event_type="response.output_text.delta")
            log.record("model_terminal", model_status="completed",
                       usage={"output_tokens": 20})
            log.record("message_saved", message_id=2, message_status="completed")
            log.record("turn_summary", tts_characters=15, total_audio_bytes=96)
            self.assertEqual(log.status, "truncated")
            self.assertLessEqual(log.count, 512)
            log.close()
            rows = [json.loads(line) for line in log.path.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), log.count)
            self.assertEqual(sum(r["event"] == "diagnostic_limit_reached"
                                 for r in rows), 1)
            self.assertEqual(rows[-1]["event"], "turn_summary")
            self.assertTrue(any(r["event"] == "model_terminal" and
                r["usage"]["output_tokens"] == 20 for r in rows))

    def test_write_failure_is_explicit_and_does_not_claim_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            log = RunDiagnostics(Path(root), "fake-run-002", "b" * 32)
            def fail(_item):
                raise OSError("synthetic disk failure")
            log._write = fail
            with self.assertRaisesRegex(DiagnosticWriteError,
                                        "diagnostic_write_failed"):
                log.record("model_event", event_type="response.created")
            self.assertEqual(log.status, "failed")
            self.assertEqual(log.failure_code, "diagnostic_write_failed")
            self.assertFalse(log.record("model_terminal", model_status="completed"))
            log.close()
            self.assertNotIn("model_terminal", log.path.read_text(encoding="utf-8"))

    def test_total_capacity_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            log = RunDiagnostics(Path(root), "fake-run-003", "c" * 32)
            for _ in range(511):
                log.record("run_started")
            with self.assertRaisesRegex(DiagnosticCapacityError,
                                        "diagnostic_capacity_exceeded"):
                log.record("model_terminal", model_status="completed")
            self.assertEqual(log.count, 512)
            self.assertEqual(log.status, "failed")
            log.close()

    def test_unknown_key_details_reject_raw_names_and_values(self):
        with tempfile.TemporaryDirectory() as root:
            log = RunDiagnostics(Path(root), "fake-run-004", "d" * 32)
            safe = {"layer": "moderation_envelope",
                    "key_sha256": hashlib.sha256(b"private_field").hexdigest(),
                    "value_type": "object"}
            log.record("moderation_event", unknown_key_count=1,
                       layer_key_count=2, unknown_key_details=[safe],
                       unknown_keys_truncated=False)
            for unsafe in ({**safe, "private_name": "private_field"},
                           {**safe, "public_name": "private_field"},
                           {**safe, "value": "private value"}):
                with self.assertRaisesRegex(ValueError,
                                            "invalid_moderation_key_diagnostic"):
                    log.record("moderation_event", unknown_key_count=1,
                               layer_key_count=2, unknown_key_details=[unsafe],
                               unknown_keys_truncated=False)
            log.close()
            data = log.path.read_text(encoding="utf-8")
            self.assertNotIn("private_field", data)
            self.assertNotIn("private value", data)
