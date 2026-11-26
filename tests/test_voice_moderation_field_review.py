"""Synthetic-only privacy checks for the unconnected local review helper."""

import hashlib
import unittest

from scripts.voice_moderation_field_review import (
    DirectFieldVault, FieldReviewError, field_shapes, reveal_one_name,
)


class LocalFieldReviewTests(unittest.TestCase):
    def test_direct_fields_only_and_no_values_in_shape(self):
        result = field_shapes([("sampleMetadata", "object"),
            ("safety_block", "array"), ("fieldNull", "null"),
            ("fieldTrue", "boolean"), ("fieldCount", "integer")])
        self.assertEqual(result["field_count"], 5)
        self.assertEqual({item["value_type"] for item in result["fields"]},
                         {"object", "array", "null", "boolean", "integer"})
        serialized = repr(result)
        for forbidden in ("sampleMetadata", "safety_block", "nestedSecret", "DO_NOT_PRINT_412"):
            self.assertNotIn(forbidden, serialized)

    def test_one_name_requires_confirmation_and_preserves_value_privacy(self):
        shapes = [("sampleMetadata", "object")]
        digest = hashlib.sha256(b"sampleMetadata").hexdigest()
        observed = []
        with self.assertRaisesRegex(FieldReviewError, "manual_review_declined"):
            reveal_one_name(shapes, digest, lambda h, t: observed.append((h, t)) or False)
        self.assertEqual(observed, [(digest, "object")])
        self.assertEqual(reveal_one_name(shapes, digest, lambda h, t: True),
                         {"name": "sampleMetadata", "value_type": "object"})

    def test_unsafe_or_sensitive_name_never_revealed(self):
        names = ["accessToken", "api_key", "emailAddress", "line\nsecret", "私有字段",
                 "x" * 49, "http://example.invalid", ""]
        for name in names:
            with self.subTest(name=repr(name)):
                digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
                calls = []
                with self.assertRaises(FieldReviewError):
                    reveal_one_name([(name, "string")], digest,
                                    lambda h, t: calls.append((h, t)) or True)
                self.assertEqual(calls, [])
                self.assertNotIn(name or "DO_NOT_PRINT_412", repr(field_shapes([(name, "integer")])))

    def test_count_and_non_json_inputs_fail_closed(self):
        with self.assertRaisesRegex(FieldReviewError, "field_count_exceeded"):
            field_shapes([(f"field{i}", "null") for i in range(25)])
        with self.assertRaisesRegex(FieldReviewError, "field_shape_invalid"):
            field_shapes([(1, "null")])
        with self.assertRaisesRegex(FieldReviewError, "field_shapes_invalid"):
            field_shapes({"sample": "string"})
        with self.assertRaisesRegex(FieldReviewError, "field_shape_invalid"):
            field_shapes([("sample", "unsupported")])

    def test_invalid_selector_and_missing_hash_fail_closed(self):
        with self.assertRaisesRegex(FieldReviewError, "invalid_field_selector"):
            reveal_one_name([("sample", "integer")], "bad", lambda h, t: True)
        with self.assertRaisesRegex(FieldReviewError, "field_selector_not_unique"):
            reveal_one_name([("sample", "integer")], "0" * 64, lambda h, t: True)

    def test_vault_expiry_and_reference_release(self):
        now = [100.0]
        class FakeTimer:
            daemon = True
            def __init__(self, delay, callback):
                self.callback = callback
            def start(self):
                pass
            def cancel(self):
                pass
        vault = DirectFieldVault(clock=lambda: now[0], timer_factory=FakeTimer)
        vault.store([("sampleMetadata", "object")])
        self.assertEqual(vault.summary()["field_count"], 1)
        now[0] += 61
        with self.assertRaisesRegex(FieldReviewError, "review_expired"):
            vault.summary()
        self.assertEqual(vault._shapes, ())


if __name__ == "__main__":
    unittest.main()
