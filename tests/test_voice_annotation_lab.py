"""Synthetic saved replies through the isolated HTTP routes. No credentials/network."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.app.voice.annotation_planner import (
    AnnotationError, Source, TAGS, fingerprint, prepare, render, validate,
)
from backend.app.voice.annotation_lab import (
    PREFIX, FIXTURES, FakeModel, FakeTTS, FixtureRepository, create_app,
)
from backend.app.voice.annotation_store import AnnotationStore


def output(value, annotations):
    return {"schema_version": 1, "input_sha256": value["input_sha256"], "annotations": annotations}


class AnnotationContracts(unittest.TestCase):
    def setUp(self):
        self.value = prepare(Source(1, 1, 1, 1, FIXTURES[103][1]))

    def annotation(self, kind, index, intent):
        fragments = [f for f in self.value["fragments"] if f["kind"] == kind]
        return {"fragment_id": fragments[index]["fragment_id"], "intent": intent}

    def assertRestorable(self, preview):
        text = preview["request_text"]
        # Remove only the recorded, trusted insertions, not a broad tag regex.
        inserted = 0
        ranges = []
        for item in preview["insertions"]:
            start = item["offset"] + inserted
            self.assertEqual(text[start:start + len(item["tag"])], item["tag"])
            ranges.append((start, start + len(item["tag"])))
            inserted += len(item["tag"])
        for lo, hi in reversed(ranges):
            text = text[:lo] + text[hi:]
        self.assertEqual(text, self.value["safe_text"])
        self.assertEqual(preview["http_requests"], 1)
        self.assertEqual(preview["segment_count"], 1)
        self.assertEqual(len(preview["request_text"]), preview["request_characters"])

    def test_empty_plan_preserves_paragraphs(self):
        p = render(self.value, [], 1)
        self.assertRestorable(p)
        self.assertEqual(p["request_text"], FIXTURES[103][1])
        self.assertEqual(p["insertions"], [])

    def test_sparse_middle_sentence(self):
        a = self.annotation("sentence", 1, "thoughtful")
        p = render(self.value, [a], 1)
        self.assertRestorable(p)
        self.assertEqual(p["request_text"], "急がなくていい。[thoughtful] 静かに話そう。\n\n無理に笑わなくてもいい。今は休もう。")

    def test_two_paragraph_intents_single_request(self):
        p = render(self.value, [self.annotation("paragraph", 0, "thoughtful"),
                               self.annotation("paragraph", 1, "resigned")], 1)
        self.assertRestorable(p)
        self.assertEqual(p["request_text"], "[thoughtful] 急がなくていい。静かに話そう。\n\n[sighs] 無理に笑わなくてもいい。今は休もう。")
        self.assertNotIn("[reset]", p["request_text"])

    def test_adjacent_sentence_intents_single_request(self):
        p = render(self.value, [self.annotation("sentence", 0, "thoughtful"),
                               self.annotation("sentence", 1, "lazy")], 1)
        self.assertRestorable(p)
        self.assertIn("[thoughtful] 急がなくていい。[relaxed] 静かに話そう。", p["request_text"])

    def test_sparse_nonoverlapping_paragraph_and_sentence(self):
        p = render(self.value, [self.annotation("paragraph", 0, "restrained"),
                               self.annotation("sentence", 3, "whisper")], 1)
        self.assertRestorable(p)

    def test_illegal_annotation_variants(self):
        valid = self.annotation("sentence", 0, "thoughtful")
        cases = [None, {"rewritten": "x"},
                 output(self.value, [{"fragment_id": "not-owned", "intent": "thoughtful"}]),
                 output(self.value, [{**valid, "intent": "[whispers]"}]),
                 output(self.value, [{**valid, "reason": "private"}]),
                 output(self.value, [valid, valid]),
                 output(self.value, [self.annotation("paragraph", 0, "thoughtful"), valid]),
                 output(self.value, [valid] * 9),
                 {**output(self.value, []), "schema_version": True},
                 {**output(self.value, []), "input_sha256": "old"},
                 {**output(self.value, []), "raw_text": "x"}]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(AnnotationError):
                validate(self.value, raw)

    def test_granularity_and_stable_codepoint_ids(self):
        source = Source(1, 1, 1, 1, "夜は静かだ。🌙少し休もう。\n\nまた話そう。")
        a, b = prepare(source, "sentence"), prepare(source, "paragraph")
        self.assertEqual(a["fragments"], b["fragments"])
        self.assertEqual(a["fragments"], prepare(source, "sentence")["fragments"])
        self.assertNotEqual(a["input_sha256"], b["input_sha256"])
        fragment = next(f for f in a["fragments"] if f["kind"] == "sentence" and f["start"] > 0)
        self.assertTrue(a["safe_text"][fragment["start"]:fragment["end"]].startswith("🌙"))
        with self.assertRaises(AnnotationError):
            validate(a, output(a, [{"fragment_id": a["fragments"][0]["fragment_id"], "intent": "lazy"}]))

    def test_filter_and_source_trace(self):
        source = Source(1, 1, 1, 1, "[whispers] 今夜は**静か**だ。\n\n```\nsecret()\n```\n\n少し休もう。")
        p = prepare(source)
        self.assertNotIn("whispers", p["safe_text"])
        self.assertNotIn("secret", p["safe_text"])
        self.assertEqual(p["source"]["body"], source.body)
        for fragment in p["fragments"]:
            origin = fragment["source_range"]
            self.assertIn(origin["precision"], ("markdown_block", "whole_message"))
            self.assertLess(origin["source_start"], origin["source_end"])

    def test_context_limits_and_qualification(self):
        for source in [Source(1, 1, 1, 1, "你好，这是中文。"),
                       Source(1, 1, 1, 1, "今夜は静かだ。", completed=False),
                       Source(1, 1, 1, 1, "今夜は静かだ。", authorized=False),
                       Source(1, 1, 1, 1, "今夜は静かだ。", display_allowed=False),
                       Source(1, 1, 1, 1, "今夜は静かだ。", personality="x" * 1001),
                       Source(1, 1, 1, 1, "今夜は静かだ。", public_context=("x",)*3)]:
            with self.subTest(source=source), self.assertRaises(AnnotationError):
                prepare(source)

    def test_tag_count_limit_and_baseline_not_reset(self):
        p = prepare(Source(1, 1, 1, 1, "あ" * 1990 + "。"))
        with self.assertRaises(AnnotationError):
            render(p, [{"fragment_id": p["fragments"][0]["fragment_id"], "intent": "thoughtful"}], 1)
        source = Source(1, 1, 1, 1, "少し考えよう。", baseline_enabled=True, baseline_applicable=True)
        self.assertTrue(render(prepare(source), [], 1)["request_text"].startswith("[thoughtful] "))
        self.assertEqual(render(prepare(source, mode="none"), [], 1)["request_text"], source.body)


class AnnotationRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-expression-test-")
        self.app = create_app(Path(self.temp.name), enabled=True)
        self.service = self.app.state.service
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        await self.client.get(PREFIX + "/session")

    async def asyncTearDown(self):
        await self.service.close()
        await self.client.aclose()
        self.temp.cleanup()

    async def start(self, message=103, operation="op-1", mode="ai_expression", granularity="auto"):
        source = (await self.client.get(PREFIX + f"/messages/{message}/expression-source",
                                       params={"mode": mode, "granularity": granularity})).json()
        response = await self.client.post(PREFIX + f"/messages/{message}/expression-plans", json={
            "operation_id": operation, "input_sha256": source["input_sha256"],
            "profile_revision": source["source"]["profile_revision"], "planning_consent": True,
            "mode": mode, "granularity": granularity})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def wait(self, identity):
        for _ in range(150):
            response = await self.client.get(PREFIX + f"/expression-plans/{identity}")
            if response.status_code != 200 or response.json()["state"] != "planning":
                return response
            await asyncio.sleep(.01)
        self.fail("local plan did not terminate")

    async def preview(self, identity, revision=None):
        response = await self.client.get(PREFIX + f"/expression-plans/{identity}/speech-preview",
                                         params={} if revision is None else {"revision": revision})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def speak(self, identity, p):
        return await self.client.post(PREFIX + f"/expression-plans/{identity}/speak", json={
            "revision": p["plan_revision"], "preview_sha256": p["preview_sha256"]})

    async def test_four_patterns_reach_fake_tts_once_each(self):
        for message, tags in [(101, 0), (102, 1), (103, 2), (104, 2)]:
            row = await self.start(message, f"sample-{message}")
            self.assertEqual((await self.wait(row["id"])).status_code, 200)
            p = await self.preview(row["id"])
            self.assertEqual(len(p["insertions"]), tags)
            self.assertEqual(p["http_requests"], 1)
            self.assertEqual((await self.speak(row["id"], p)).status_code, 200)
            self.assertEqual(self.service.tts.requests[-1]["text"], p["request_text"])
            self.assertEqual(self.service.tts.requests[-1]["parameters"], p["parameters"])
        self.assertEqual(self.service.store.counters()["mock_model_attempts"], 4)
        self.assertEqual(len(self.service.tts.requests), 4)

    async def test_concurrent_operations_deduplicate_model_and_tts(self):
        rows = await asyncio.gather(*(self.start(operation=f"parallel-{i}") for i in range(12)))
        self.assertEqual(len({r["id"] for r in rows}), 1)
        identity = rows[0]["id"]
        await self.wait(identity)
        self.assertEqual(len(self.service.model.requests), 1)
        p = await self.preview(identity)
        await asyncio.gather(*(self.speak(identity, p) for _ in range(12)))
        self.assertEqual(len(self.service.tts.requests), 1)
        again = await self.start(operation="after-refresh")
        self.assertEqual(again["id"], identity)

    async def test_version_cache_old_audio_stays_accessible(self):
        row = await self.start()
        await self.wait(row["id"])
        first = await self.preview(row["id"])
        await self.speak(row["id"], first)
        response = await self.client.patch(PREFIX + f"/expression-plans/{row['id']}", json={
            "expected_revision": 1, "annotations": []})
        self.assertEqual(response.status_code, 200)
        second = await self.preview(row["id"])
        self.assertNotEqual(first["preview_sha256"], second["preview_sha256"])
        self.assertEqual((await self.speak(row["id"], first)).status_code, 409)
        old = await self.preview(row["id"], 1)
        self.assertEqual(first["preview_sha256"], old["preview_sha256"])
        self.assertEqual(old["audio_state"], "ready")
        audio = await self.client.get(PREFIX + f"/expression-plans/{row['id']}/audio/{first['preview_sha256']}")
        self.assertEqual(audio.status_code, 200)
        self.assertTrue(audio.content.startswith(b"RIFF"))
        await self.speak(row["id"], second)
        self.assertEqual(len(self.service.tts.requests), 2)
        self.assertEqual(len(self.service.model.requests), 1)

    async def test_manual_choice_bypasses_planning_and_cache_isolation(self):
        previews = []
        for mode in ("none", "thoughtful"):
            row = await self.start(mode=mode, operation=mode)
            previews.append(await self.preview(row["id"]))
        self.assertEqual(self.service.model.requests, [])
        self.assertNotEqual(previews[0]["preview_sha256"], previews[1]["preview_sha256"])
        self.assertFalse(previews[0]["request_text"].startswith("["))
        self.assertTrue(previews[1]["request_text"].startswith("[thoughtful] "))

    async def test_model_request_contains_only_filtered_context_and_cap(self):
        old = self.service.repository.items[103]
        self.service.repository.items[103] = replace(old, public_context=("公开内容。\n\n```\nSECRET\n```",),
            psychology=(("arousal", .2),))
        row = await self.start()
        await self.wait(row["id"])
        request = self.service.model.requests[0]
        self.assertEqual(request["max_output_tokens"], 512)
        self.assertEqual(request["retries"], 0)
        self.assertNotIn("SECRET", json.dumps(request))
        self.assertEqual(len(request["messages"]), 2)
        self.assertEqual(json.loads(request["messages"][1]["content"])["psychology"], {"arousal": .2})

    async def test_denied_paths_zero_dispatch(self):
        source = (await self.client.get(PREFIX + "/messages/103/expression-source")).json()
        base = {"operation_id": "denied", "input_sha256": source["input_sha256"],
                "profile_revision": 3, "planning_consent": True}
        for change, status in [({"input_sha256": "0" * 64}, 409), ({"profile_revision": 4}, 409),
                               ({"planning_consent": False}, 403), ({"extra": "text"}, 422)]:
            response = await self.client.post(PREFIX + "/messages/103/expression-plans", json={**base, **change})
            self.assertEqual(response.status_code, status, response.text)
        self.service.enabled = False
        self.assertEqual((await self.client.get(PREFIX + "/messages/103/expression-source")).status_code, 403)
        self.assertEqual(self.service.store.counters()["mock_model_attempts"], 0)

    async def test_authentication_owner_and_origin(self):
        with self.assertRaises(AnnotationError):
            self.service.source(103, 2)
        response = await self.client.post(PREFIX + "/messages/103/expression-plans",
                                         json={}, headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        self.assertEqual((await self.client.get(PREFIX + "/messages/103/expression-source")).status_code, 401)

    async def test_interface_without_output_cap_refused_before_claim(self):
        self.service.model.supports_output_cap = False
        source = (await self.client.get(PREFIX + "/messages/103/expression-source")).json()
        response = await self.client.post(PREFIX + "/messages/103/expression-plans", json={
            "operation_id": "unsupported", "input_sha256": source["input_sha256"],
            "profile_revision": 3, "planning_consent": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.model.requests, [])
        self.assertEqual(self.service.store.counters()["mock_model_attempts"], 0)

    async def test_preflight_failure_before_attempt_is_not_provider_unknown(self):
        real_source = self.service.source
        def over_limit(*args, **kwargs):
            value = real_source(*args, **kwargs)
            # Exercise the actual final-request guard, after a valid source check.
            value["model_input"]["instruction"] = "x" * 6001
            return value
        with patch.object(self.service, "source", side_effect=over_limit):
            row = await self.start()
            final = (await self.wait(row["id"])).json()
        self.assertEqual(final["state"], "fallback")
        self.assertEqual(final["reason"], "model_input_limit")
        self.assertEqual(final["model_attempts"], 0)
        self.assertEqual(self.service.model.requests, [])

    async def test_body_consent_and_parameter_drift_reject_old_preview(self):
        from backend.app.voice.annotation_planner import PARAMETERS
        for field, value in [("body", "今夜は少し休もう。"), ("consent_version", "revoked-version")]:
            old = replace(self.service.repository.items[103], body_version=self.service.repository.items[103].body_version + 1)
            self.service.repository.items[103] = old
            row = await self.start(operation=f"drift-{field}")
            await self.wait(row["id"])
            p = await self.preview(row["id"])
            self.service.repository.items[103] = replace(old, **{field: value})
            self.assertEqual((await self.speak(row["id"], p)).status_code, 409)
            # Restore only the synthetic source, retaining all old plan claims.
            self.service.repository.items[103] = old
        old = replace(old, body_version=old.body_version + 1)
        self.service.repository.items[103] = old
        row = await self.start(operation="parameter-drift")
        await self.wait(row["id"])
        p = await self.preview(row["id"])
        with patch.dict(PARAMETERS, {"output_format": "pcm_24000"}):
            self.assertEqual((await self.speak(row["id"], p)).status_code, 409)
        self.assertEqual(self.service.tts.requests, [])

    async def test_revoke_during_audio_cannot_publish_or_play(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowAudio(FakeTTS):
            async def synthesize(self, text, parameters):
                entered.set()
                await release.wait()
                return await super().synthesize(text, parameters)
        self.service.tts = SlowAudio()
        row = await self.start()
        await self.wait(row["id"])
        p = await self.preview(row["id"])
        pending = asyncio.create_task(self.speak(row["id"], p))
        await asyncio.wait_for(entered.wait(), 2)
        self.service.repository.items[103] = replace(self.service.repository.items[103], authorized=False)
        release.set()
        self.assertEqual((await pending).status_code, 409)
        self.assertEqual(self.service.store.audio(p["preview_sha256"])["audio"], None)
        self.assertEqual(self.service.store.counters()["mock_tts_attempts"], 1)

    async def test_stale_source_and_profile_prevent_tts(self):
        row = await self.start()
        await self.wait(row["id"])
        p = await self.preview(row["id"])
        old = self.service.repository.items[103]
        self.service.repository.items[103] = replace(old, profile_revision=4)
        self.assertEqual((await self.speak(row["id"], p)).status_code, 409)
        self.assertEqual(self.service.tts.requests, [])

    async def test_bad_model_falls_back_without_usage_loss(self):
        class Bad(FakeModel):
            async def generate(self, request):
                await super().generate(request)
                return {"text": "[arbitrary]"}, {"input_tokens": 70, "output_tokens": 15}
        self.service.model = Bad()
        row = await self.start()
        ended = (await self.wait(row["id"])).json()
        self.assertEqual(ended["state"], "fallback")
        self.assertEqual(ended["usage"]["output_tokens"], 15)
        p = await self.preview(row["id"])
        self.assertEqual(p["effective_mode"], "none")
        self.assertEqual(p["request_text"], FIXTURES[103][1])

    async def test_timeout_unknown_does_not_retry(self):
        class Slow(FakeModel):
            async def generate(self, request):
                self.requests.append(request)
                await asyncio.sleep(1)
        self.service.model, self.service.deadline = Slow(), .02
        row = await self.start()
        ended = (await self.wait(row["id"])).json()
        self.assertEqual(ended["state"], "failed_unknown")
        again = await self.start(operation="new-operation-same-input")
        self.assertEqual(again["state"], "failed_unknown")
        self.assertEqual(len(self.service.model.requests), 1)
        self.assertEqual(self.service.store.counters()["mock_model_attempts"], 1)

    async def test_cancel_and_late_model_no_publish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Late(FakeModel):
            async def generate(self, request):
                value = await super().generate(request)
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
                return value
        self.service.model = Late()
        row = await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        await self.client.delete(PREFIX + f"/expression-plans/{row['id']}")
        await asyncio.sleep(.03)
        final = self.service.store.get(row["id"], 1)
        self.assertEqual(final["state"], "cancelled")
        self.assertEqual(final["versions"], [])
        self.assertEqual(final["model_attempts"], 1)
        self.assertEqual(final["usage"]["total_tokens"], 130)
        self.assertEqual(self.service.tts.requests, [])

    async def test_permission_revoked_during_planning(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Paused(FakeModel):
            async def generate(self, request):
                value = await super().generate(request)
                entered.set()
                await release.wait()
                return value
        self.service.model = Paused()
        row = await self.start()
        await asyncio.wait_for(entered.wait(), 2)
        self.service.repository.items[103] = replace(self.service.repository.items[103], authorized=False)
        release.set()
        await asyncio.sleep(.08)
        final = self.service.store.get(row["id"], 1)
        self.assertEqual(final["state"], "restricted")
        self.assertEqual(final["usage"]["total_tokens"], 130)
        self.assertEqual(final["versions"], [])

    async def test_audio_failure_keeps_attempt_no_repeat(self):
        class Broken(FakeTTS):
            async def synthesize(self, text, parameters):
                self.requests.append(text)
                raise RuntimeError("do not log arbitrary provider payload")
        self.service.tts = Broken()
        row = await self.start()
        await self.wait(row["id"])
        p = await self.preview(row["id"])
        self.assertEqual((await self.speak(row["id"], p)).status_code, 409)
        self.assertEqual((await self.speak(row["id"], p)).status_code, 409)
        self.assertEqual(len(self.service.tts.requests), 1)
        self.assertEqual(self.service.store.audio(p["preview_sha256"])["state"], "failed_unknown")

    async def test_late_audio_after_edit_cannot_publish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowAudio(FakeTTS):
            async def synthesize(self, text, parameters):
                entered.set()
                await release.wait()
                return await super().synthesize(text, parameters)
        self.service.tts = SlowAudio()
        row = await self.start()
        await self.wait(row["id"])
        p = await self.preview(row["id"])
        pending = asyncio.create_task(self.speak(row["id"], p))
        await asyncio.wait_for(entered.wait(), 2)
        await self.client.patch(PREFIX + f"/expression-plans/{row['id']}", json={"expected_revision": 1, "annotations": []})
        release.set()
        self.assertEqual((await pending).status_code, 409)
        self.assertNotEqual(self.service.store.audio(p["preview_sha256"])["state"], "ready")

    async def test_restart_preserves_pending_claims(self):
        prepared = self.service.source(103, 1)
        row, _ = self.service.store.claim(1, "crashed-before-request", prepared)
        self.service.store.model_started(row["id"])
        store = AnnotationStore(Path(self.temp.name) / "expression-lab.sqlite3")
        store.recover()
        again = await self.start(operation="after-restart")
        self.assertEqual(again["id"], row["id"])
        self.assertEqual(again["state"], "failed_unknown")
        self.assertEqual(self.service.model.requests, [])

    async def test_write_failure_retains_usage_and_claim(self):
        with patch.object(self.service.store, "finish", side_effect=sqlite3.OperationalError("full")):
            row = await self.start()
            final = (await self.wait(row["id"])).json()
        self.assertEqual(final["state"], "failed_unknown")
        self.assertEqual(final["usage"]["total_tokens"], 130)
        self.assertEqual(final["model_attempts"], 1)

    async def test_operation_binding_and_stale_edit(self):
        row = await self.start()
        await self.wait(row["id"])
        source = (await self.client.get(PREFIX + "/messages/104/expression-source")).json()
        response = await self.client.post(PREFIX + "/messages/104/expression-plans", json={
            "operation_id": "op-1", "input_sha256": source["input_sha256"],
            "profile_revision": 3, "planning_consent": True})
        self.assertEqual(response.status_code, 409)
        response = await self.client.patch(PREFIX + f"/expression-plans/{row['id']}", json={"expected_revision": 0, "annotations": []})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.service.model.requests), 1)

    async def test_reject_daily_database_and_real_provider(self):
        path = Path(self.temp.name) / "other.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE users(id INTEGER)")
        db.close()
        with self.assertRaises(ValueError):
            AnnotationStore(path)
        with self.assertRaises(ValueError):
            AnnotationStore(Path(__file__).resolve().parents[1] / "data" / "never-created.sqlite3")
        class Real:
            mock_only = False
        with self.assertRaises(ValueError):
            create_app(Path(self.temp.name), model=Real())


if __name__ == "__main__":
    unittest.main()
