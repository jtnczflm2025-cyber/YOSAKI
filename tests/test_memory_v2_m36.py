"""M3.6 zero-payment acceptance: all HTTP calls use in-process MockTransport."""
import json
import threading
import concurrent.futures
import unittest
from types import SimpleNamespace

import httpx

from test_memory_v2_derivation import M3Base, app_module, derived
from backend.app import memory_v2_m36_trial as trial
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_model_adapter as local_model
from m36_gold import GOLD, validate_gold


PRICE = {"provider": "openai_responses", "model": "gpt-6.1-sol", "mode": "standard",
         "inputUsdPerMillion": "2", "outputUsdPerMillion": "10",
         "checkedAt": "synthetic-2026-10-29", "sourceUrl": "synthetic-official-price-snapshot"}


class FakeProvider:
    def __init__(self, *, status="completed", usage=True, on_generate=None,
                 service_tier="default", response_model=None):
        self.status = status
        self.usage = usage
        self.on_generate = on_generate
        self.service_tier = service_tier
        self.response_model = response_model
        self.calls = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append((request.url.path, body))
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 500})
        if self.on_generate:
            self.on_generate()
        if self.status == "timeout":
            raise httpx.ReadTimeout("synthetic_timeout")
        messages = json.loads(body["input"][0]["content"])
        if body["text"]["format"]["type"] == "json_schema":
            text = json.dumps({"items": [{"sourceKind": m["sourceKind"], "sourceId": m["sourceId"],
                "speaker": m["speaker"], "text": ("我说：" if m["speaker"] == "assistant" else "她说：") + m["content"]}
                for m in messages]}, ensure_ascii=False)
        else:
            text = "她说……我说：……（合成旧提示词评测输出）"
        part = {"type": "refusal", "refusal": "synthetic"} if self.status == "refused" else {"type": "output_text", "text": text}
        return httpx.Response(200, json={"id": "resp_synthetic", "model": self.response_model or body["model"],
            "status": "completed" if self.status == "refused" else self.status,
            "service_tier": self.service_tier,
            "output": [{"type": "message", "content": [part]}],
            "usage": (self.usage if isinstance(self.usage, dict) else
                      {"input_tokens": 500, "output_tokens": 100} if self.usage else None)})


def adapter(fake):
    return responses.ResponsesAdapter(model="gpt-6.1-sol",
                                      transport=httpx.MockTransport(fake))


class M36Trial(M3Base):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            trial.init_db(db)

    def created(self, *, max_microusd=5_000_000):
        with app_module.connect() as db:
            return trial.create_trial(db, self.cid, "gpt-6.1-sol", PRICE,
                                      max_microusd=max_microusd)

    def prepare(self, fake=None, *, max_microusd=5_000_000):
        tid = self.created(max_microusd=max_microusd)
        fake = fake or FakeProvider()
        result = trial.prepare_trial(app_module.connect, tid,
            [{"kind": "NARRATIVE", "refs": self.refs()}], adapter(fake))
        with app_module.connect() as db:
            rows = db.execute("SELECT id,payload_json FROM mv3_jobs WHERE character_id=?", (self.cid,)).fetchall()
            jobs_by_variant = {p["prompt_variant"]: r["id"] for r in rows
                               if (p := json.loads(r["payload_json"]))["trial_id"] == tid}
        return tid, jobs_by_variant, fake, result

    def test_official_wire_shape_and_no_implicit_network(self):
        with self.assertRaisesRegex(ValueError, "network_and_credentials_not_approved"):
            responses.ResponsesAdapter(model="gpt-6.1-sol")
        fake = FakeProvider()
        client = adapter(fake)
        messages = [{"sourceKind": "message", "sourceId": "1", "speaker": "user",
                     "time": None, "content": "もし雨なら、行かない。"}]
        payload = responses.request_payload("gpt-6.1-sol", "NARRATIVE", responses.M35_VARIANT, messages, 2048)
        self.assertEqual((payload["store"], payload["background"], payload["stream"], payload["truncation"]),
                         (False, False, False, "disabled"))
        self.assertEqual(payload["service_tier"], "default")
        self.assertEqual(payload["reasoning"], {"mode": "standard", "effort": "medium"})
        self.assertNotIn("tools", payload)
        self.assertNotIn("conversation", payload)
        self.assertNotIn("previous_response_id", payload)
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(client.count_input(payload), 500)
        result = client.generate(payload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["modelId"], "gpt-6.1-sol")
        self.assertEqual(result["serviceTier"], "default")
        self.assertEqual([x[0] for x in fake.calls], ["/v1/responses/input_tokens", "/v1/responses"])
        self.assertEqual(fake.calls[0][1], responses.count_payload(payload))
        self.assertNotIn("service_tier", fake.calls[0][1])
        self.assertEqual(fake.calls[1][1]["max_output_tokens"], 2048)

    def test_trial_schema_preflight_and_two_variants(self):
        with app_module.connect() as db:
            trial.init_db(db)
        tid, jobs_by_variant, fake, report = self.prepare()
        self.assertEqual(report["generationRequests"], 2)
        self.assertEqual(report["countRequests"], 2)
        self.assertEqual(sum(path.endswith("/input_tokens") for path, _ in fake.calls), 2)
        with app_module.connect() as db:
            status = trial.trial_status(db, tid, self.cid)
            self.assertEqual(status["status"], "prepared")
            self.assertGreater(status["reserved_microusd"], 0)
            row = db.execute("SELECT input_json FROM mv3_model_chunks WHERE trial_id=? LIMIT 1", (tid,)).fetchone()
            self.assertNotIn("今天累了", row[0])
        first = trial.dispatch_trial_chunk(app_module.connect, tid, jobs_by_variant[responses.PLUGIN_VARIANT], self.cid, adapter(fake))
        self.assertIsNone(first["versionId"])
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_versions").fetchone()[0], 0)
        second = trial.dispatch_trial_chunk(app_module.connect, tid, jobs_by_variant[responses.M35_VARIANT], self.cid, adapter(fake))
        self.assertIsNotNone(second["versionId"])
        with app_module.connect() as db:
            version = derived._row(db, "mv3_versions", second["versionId"])
            self.assertEqual((version["status"], version["review_status"], version["model_provider"]),
                             ("needs_review", "unreviewed", "openai_responses"))
            self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "completed")
            self.assertEqual(trial.trial_status(db, tid, self.cid)["reserved_microusd"], 0)
            self.assertGreater(trial.trial_status(db, tid, self.cid)["spent_estimate_microusd"], 0)
            with self.assertRaisesRegex(ValueError, "valid_review_required"):
                derived.activate_version(db, version["id"], self.cid)
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 2)

    def test_budget_rejection_before_generation(self):
        tid = self.created(max_microusd=10)
        fake = FakeProvider()
        with self.assertRaisesRegex(ValueError, "trial_budget_exceeded"):
            trial.prepare_trial(app_module.connect, tid, [{"kind": "NARRATIVE", "refs": self.refs()}], adapter(fake))
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 0)
        with app_module.connect() as db:
            self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "stopped")

    def test_http_error_is_not_retried(self):
        calls = []
        def failed(request):
            calls.append(request.url.path)
            return httpx.Response(503, json={"error": "synthetic_unavailable"})
        client = responses.ResponsesAdapter(model="gpt-6.1-sol", transport=httpx.MockTransport(failed))
        payload = responses.request_payload("gpt-6.1-sol", "NARRATIVE", responses.M35_VARIANT,
            [{"sourceKind": "message", "sourceId": "1", "speaker": "user", "time": None,
              "content": "合成文本"}], 100)
        with self.assertRaises(httpx.HTTPStatusError):
            client.generate(payload)
        self.assertEqual(calls, ["/v1/responses"])

    def test_timeout_and_usage_missing_stop_without_retry(self):
        for kwargs in ({"status": "timeout"}, {"usage": False}):
            with self.subTest(kwargs=kwargs):
                fake = FakeProvider(**kwargs)
                tid, by_variant, _, _ = self.prepare(fake)
                jid = by_variant[responses.PLUGIN_VARIANT]
                with self.assertRaises(ValueError):
                    trial.dispatch_trial_chunk(app_module.connect, tid, jid, self.cid, adapter(fake))
                with app_module.connect() as db:
                    self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "stopped")
                    row = db.execute("SELECT state,provider_response_id FROM mv3_model_chunks WHERE job_id=?", (jid,)).fetchone()
                    self.assertEqual(row["state"], "unknown")
                    self.assertEqual(row["provider_response_id"], "resp_synthetic" if kwargs.get("usage") is False else None)
                with self.assertRaises(ValueError):
                    trial.dispatch_trial_chunk(app_module.connect, tid, jid, self.cid, adapter(fake))
                self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 1)

    def test_expired_lease_blocks_generation_before_send(self):
        tid, by_variant, fake, _ = self.prepare()
        jid = by_variant[responses.PLUGIN_VARIANT]
        with app_module.connect() as db:
            jobs.claim(db, jid, self.cid, now=100)
        with self.assertRaisesRegex(ValueError, "trial_source_changed"):
            trial.dispatch_trial_chunk(app_module.connect, tid, jid, self.cid, adapter(fake))
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 0)

    def test_refused_incomplete_failed_cancelled_settle_and_stop(self):
        for state in ("refused", "incomplete", "failed", "cancelled"):
            with self.subTest(state=state):
                fake = FakeProvider(status=state)
                tid, by_variant, _, _ = self.prepare(fake)
                result = trial.dispatch_trial_chunk(app_module.connect, tid, by_variant[responses.PLUGIN_VARIANT], self.cid, adapter(fake))
                self.assertEqual(result["status"], "stopped")
                with app_module.connect() as db:
                    self.assertGreater(trial.trial_status(db, tid, self.cid)["spent_estimate_microusd"], 0)
                    self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "stopped")

    def test_nonstandard_service_tier_stops_before_second_request(self):
        fake = FakeProvider(service_tier="priority")
        tid, by_variant, _, _ = self.prepare(fake)
        result = trial.dispatch_trial_chunk(app_module.connect, tid,
            by_variant[responses.PLUGIN_VARIANT], self.cid, adapter(fake))
        self.assertEqual(result["error"], "provider_service_tier_mismatch")
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 1)
        with app_module.connect() as db:
            self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "stopped")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_versions").fetchone()[0], 0)

    def test_provider_model_mismatch_stops_before_second_request(self):
        fake = FakeProvider(response_model="synthetic-wrong-model")
        tid, by_variant, _, _ = self.prepare(fake)
        result = trial.dispatch_trial_chunk(app_module.connect, tid,
            by_variant[responses.PLUGIN_VARIANT], self.cid, adapter(fake))
        self.assertEqual(result["error"], "provider_model_mismatch")
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 1)
        with app_module.connect() as db:
            self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "stopped")

    def test_source_changed_during_request_settles_but_no_version(self):
        def change():
            with app_module.connect() as db:
                db.execute("UPDATE messages SET content='edited synthetic' WHERE id=?", (self.user,))
        fake = FakeProvider(on_generate=change)
        tid, by_variant, _, _ = self.prepare(fake)
        result = trial.dispatch_trial_chunk(app_module.connect, tid, by_variant[responses.M35_VARIANT], self.cid, adapter(fake))
        self.assertEqual(result["error"], "source_changed_or_lease_expired")
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_versions").fetchone()[0], 0)
            self.assertGreater(trial.trial_status(db, tid, self.cid)["spent_estimate_microusd"], 0)

    def test_usage_above_reservation_stops_and_records_provider_usage(self):
        fake = FakeProvider(usage={"input_tokens": 500, "output_tokens": 100_000})
        tid, by_variant, _, _ = self.prepare(fake)
        result = trial.dispatch_trial_chunk(app_module.connect, tid, by_variant[responses.PLUGIN_VARIANT], self.cid, adapter(fake))
        self.assertEqual(result["error"], "provider_usage_exceeds_reservation")
        with app_module.connect() as db:
            row = db.execute("SELECT state,provider_usage_json,usage_estimated_usd_micros FROM mv3_model_chunks WHERE job_id=?",
                             (by_variant[responses.PLUGIN_VARIANT],)).fetchone()
            self.assertEqual(row["state"], "rejected")
            self.assertEqual(json.loads(row["provider_usage_json"])["output_tokens"], 100_000)
            self.assertGreater(row["usage_estimated_usd_micros"], 0)
            self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "stopped")

    def test_serial_dispatch_rejects_concurrent_second_request(self):
        entered, release = threading.Event(), threading.Event()
        def hold():
            entered.set()
            self.assertTrue(release.wait(5))
        fake = FakeProvider(on_generate=hold)
        tid, by_variant, _, _ = self.prepare(fake)
        first_id = by_variant[responses.PLUGIN_VARIANT]
        second_id = by_variant[responses.M35_VARIANT]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(trial.dispatch_trial_chunk, app_module.connect, tid, first_id, self.cid, adapter(fake))
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(ValueError, "trial_unsettled_request"):
                trial.dispatch_trial_chunk(app_module.connect, tid, second_id, self.cid, adapter(fake))
            release.set()
            self.assertEqual(first.result(timeout=5)["status"], "recorded")
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 1)

    def test_synthetic_gold_manifest_is_fixed(self):
        self.assertTrue(validate_gold())
        self.assertEqual([case["id"] for case in GOLD],
                         ["N1", "N2", "N3", "N4", "N5", "N6", "D1", "D2"])
        self.assertEqual(responses.quote_microusd(500, 2048, "2", "10"), 25776)
        self.assertEqual(responses.usage_microusd({"input_tokens": 500, "output_tokens": 100}, "2", "10"), 2000)
        class Bytes:
            def encode(self, value):
                return value.encode("utf-8")
        calls = 0
        for case in GOLD:
            sources = [SimpleNamespace(kind="message", source_id=str(i), role=role,
                occurred_at=None, status="completed", content_hash="c", state_hash="s", text=content)
                for i, (role, content) in enumerate(case["turns"])]
            calls += 2 * len(local_model.batches(sources, Bytes(), kind=case["kind"],
                                                input_cap=1_000_000))
        self.assertEqual(calls, 18)

    def test_all_eight_synthetic_cases_preflight_eighteen_without_generation(self):
        cases = []
        with app_module.connect() as db:
            for case in GOLD:
                conversation = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                    VALUES(?,'m36-synthetic','2026-10-29','2026-10-29')""", (self.cid,)).lastrowid
                refs = []
                for role, content in case["turns"]:
                    mid = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                        VALUES(?,?,?,'2026-10-29')""", (conversation, role, content)).lastrowid
                    refs.append({"kind": "message", "id": str(mid)})
                item = {"kind": case["kind"], "refs": refs}
                if case["kind"] == "DIGEST":
                    item.update(level=case["level"], period=case["period"])
                cases.append(item)
            self._index(db)
        tid = self.created()
        fake = FakeProvider()
        report = trial.prepare_trial(app_module.connect, tid, cases, adapter(fake))
        self.assertEqual((report["countRequests"], report["generationRequests"]), (18, 18))
        self.assertEqual(sum(path == "/v1/responses" for path, _ in fake.calls), 0)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_model_chunks WHERE trial_id=?", (tid,)).fetchone()[0], 18)
            self.assertEqual(trial.trial_status(db, tid, self.cid)["status"], "prepared")

    def test_different_model_text_creates_new_unreviewed_version(self):
        with app_module.connect() as db:
            item = derived.create_derivation(db, self.cid, "NARRATIVE", self.refs())
            opts = {"author_kind": "model", "rule_version": "model-narrative-m35-v1",
                    "prompt_version": "ssly-faithful-m35-v1", "model_provider": "openai_responses",
                    "model_id": "gpt-6.1-sol"}
            first = derived.add_version(db, item["id"], self.refs(), "我说：先休息。", **opts)
            again = derived.add_version(db, item["id"], self.refs(), "我说：今天先休息。", **opts)
            repeat = derived.add_version(db, item["id"], self.refs(), "我说：先休息。", **opts)
            self.assertNotEqual(first["id"], again["id"])
            self.assertEqual(first["id"], repeat["id"])
            self.assertEqual((first["status"], again["status"]), ("needs_review", "needs_review"))


if __name__ == "__main__":
    unittest.main()
