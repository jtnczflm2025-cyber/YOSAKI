"""Explicit M3.6 trial ledger. No application route, scheduler or credential lookup."""
from __future__ import annotations

import json
import time
import uuid

from .owner_billing import metered
from . import memory_v2_derivation as core
from . import memory_v2_fidelity as fidelity
from . import memory_v2_jobs as jobs
from . import memory_v2_model_adapter as local_model
from . import memory_v2_responses_adapter as responses


MAX_PILOT_CALLS = 18
MAX_PILOT_MICROUSD = 5_000_000


class _ByteTokenizer:
    def encode(self, text):
        return text.encode("utf-8")


def init_db(db):
    jobs.init_db(db)
    db.execute("""CREATE TABLE IF NOT EXISTS mv3_trial_budgets (
        id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, model TEXT NOT NULL, price_json TEXT NOT NULL,
        max_microusd INTEGER NOT NULL, max_requests INTEGER NOT NULL,
        reserved_microusd INTEGER NOT NULL DEFAULT 0, spent_estimate_microusd INTEGER NOT NULL DEFAULT 0,
        dispatched_requests INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL CHECK(status IN ('created','counting','prepared','running','completed','stopped')),
        error_code TEXT, created_at TEXT NOT NULL)""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(mv3_model_chunks)")}
    for name, definition in (
        ("trial_id", "TEXT"), ("prompt_variant", "TEXT"),
        ("provider_response_id", "TEXT"), ("provider_status", "TEXT"),
        ("provider_usage_json", "TEXT"), ("preflight_input_tokens", "INTEGER"),
        ("reserved_usd_micros", "INTEGER NOT NULL DEFAULT 0"),
        ("usage_estimated_usd_micros", "INTEGER"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE mv3_model_chunks ADD COLUMN {name} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS ix_mv3_model_trial ON mv3_model_chunks(trial_id,state)")


def create_trial(db, character_id: int, model: str, price: dict, *,
                 max_microusd: int = MAX_PILOT_MICROUSD,
                 max_requests: int = MAX_PILOT_CALLS) -> str:
    if not db.execute("SELECT 1 FROM characters WHERE id=? AND enabled=1", (character_id,)).fetchone():
        raise ValueError("trial_character_invalid")
    if (not model or type(max_microusd) is not int or max_microusd <= 0 or (not metered() and max_microusd > MAX_PILOT_MICROUSD)
            or type(max_requests) is not int or max_requests <= 0 or (not metered() and max_requests > MAX_PILOT_CALLS)
            or price.get("provider") not in ("openai_responses", "openai_embeddings") or price.get("model") != model
            or price.get("mode") != "standard" or not price.get("checkedAt")
            or not price.get("sourceUrl")):
        raise ValueError("trial_manifest_invalid")
    responses.quote_microusd(1, 1, price["inputUsdPerMillion"], price["outputUsdPerMillion"])
    trial_id = str(uuid.uuid4())
    db.execute("""INSERT INTO mv3_trial_budgets(id,character_id,provider,model,price_json,
        max_microusd,max_requests,status,created_at)
        VALUES(?,?,?,?,?,?,?,'created',?)""",
        (trial_id, character_id, price['provider'], model, json.dumps(price, sort_keys=True),
         max_microusd, max_requests, core._now()))
    return trial_id


def trial_status(db, trial_id: str, character_id: int) -> dict:
    row = db.execute("SELECT * FROM mv3_trial_budgets WHERE id=? AND character_id=?",
                     (trial_id, character_id)).fetchone()
    if not row:
        raise ValueError("trial_not_accessible")
    result = dict(row)
    result.pop("price_json")
    return result


def stop_interrupted_count(db, trial_id: str, character_id: int) -> bool:
    """A crashed token preflight is never silently restarted."""
    trial_status(db, trial_id, character_id)
    return db.execute("""UPDATE mv3_trial_budgets SET status='stopped',error_code='count_preflight_interrupted'
        WHERE id=? AND character_id=? AND status='counting'""",
        (trial_id, character_id)).rowcount == 1


def _trial(db, trial_id):
    row = db.execute("SELECT * FROM mv3_trial_budgets WHERE id=?", (trial_id,)).fetchone()
    if not row:
        raise ValueError("trial_missing")
    return dict(row)


def _request(model, kind, variant, group, output_cap, level):
    return responses.request_payload(model, kind, variant, group, output_cap, level=level)


def prepare_trial(connect, trial_id: str, cases: list[dict], counter: responses.ResponsesAdapter):
    """Count every full payload before any generation; only then atomically reserve all calls."""
    if not isinstance(counter, responses.ResponsesAdapter) or not cases:
        raise ValueError("trial_counter_required")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        trial = _trial(db, trial_id)
        if trial["status"] != "created" or counter.model != trial["model"]:
            raise ValueError("trial_not_preparable")
        price = json.loads(trial["price_json"])
        prepared = []
        for case in cases:
            kind = case["kind"]
            refs = case["refs"]
            sources = [core.live_source(db, trial["character_id"], r["kind"], str(r["id"])) for r in refs]
            if any(source is None for source in sources):
                raise ValueError("source_missing_or_stale")
            target = case.get("targetChars", 6000)
            groups = local_model.batches(sources, _ByteTokenizer(), kind=kind,
                                         input_cap=1_000_000, target_chars=target)
            source_snapshot = core.source_inputs(db, trial["character_id"], refs)
            for variant in (responses.PLUGIN_VARIANT, responses.M35_VARIANT):
                for ordinal, group in enumerate(groups):
                    payload = _request(trial["model"], kind, variant, group,
                                       case.get("outputCap", 2048), case.get("level"))
                    prepared.append({"case": case, "variant": variant, "ordinal": ordinal,
                                     "group": group, "request": payload, "sourceSnapshot": source_snapshot,
                                     "requestHash": core._hash(payload)})
        db.execute("UPDATE mv3_trial_budgets SET status='counting' WHERE id=? AND status='created'", (trial_id,))
    try:
        if len(prepared) > trial["max_requests"]:
            raise ValueError("trial_request_limit")
        for item in prepared:
            count = counter.count_input(item["request"])
            if count > item["case"].get("inputCap", 8192):
                raise ValueError("provider_input_token_limit")
            item["inputTokens"] = count
            item["reservedMicros"] = responses.quote_microusd(
                count, item["request"]["max_output_tokens"],
                price["inputUsdPerMillion"], price["outputUsdPerMillion"])
        total = sum(item["reservedMicros"] for item in prepared)
        if total > trial["max_microusd"]:
            raise ValueError("trial_budget_exceeded")
    except Exception as exc:
        with connect() as db:
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=? AND status='counting'",
                       (str(exc)[:120], trial_id))
        raise
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        trial = _trial(db, trial_id)
        if trial["status"] != "counting":
            raise ValueError("trial_prepare_conflict")
        for item in prepared:
            case = item["case"]
            if core.source_inputs(db, trial["character_id"], case["refs"]) != item["sourceSnapshot"]:
                raise ValueError("trial_source_changed_during_count")
            args = {"model_mode": True, "trial_id": trial_id,
                    "prompt_variant": item["variant"], "provider": trial["provider"],
                    "model": trial["model"], "price_snapshot": trial["price_json"],
                    "input_cap": case.get("inputCap", 8192),
                    "output_cap": case.get("outputCap", 2048),
                    "total_token_cap": 1_000_000, "max_cost_units": 0,
                    "target_chars": case.get("targetChars", 6000)}
            if case["kind"] == "DIGEST":
                args.update(level=case["level"], period=case["period"], children=case.get("children", []))
            job = jobs.enqueue(db, trial["character_id"], case["kind"], case["refs"], **args)
            group = item["group"]
            references = [{k: v for k, v in message.items() if k != "content"} for message in group]
            db.execute("""INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,
                state,reserved_tokens,reserved_cost_units,trial_id,prompt_variant,preflight_input_tokens,
                reserved_usd_micros,provider_status)
                VALUES(?,?,?,?,?,'prepared',?,0,?,?,?,?, 'prepared')""",
                (job["id"], item["ordinal"], str(uuid.uuid4()), item["requestHash"],
                 json.dumps(references, ensure_ascii=False, separators=(",", ":")),
                 item["inputTokens"] + item["request"]["max_output_tokens"],
                 trial_id, item["variant"], item["inputTokens"], item["reservedMicros"]))
        db.execute("UPDATE mv3_trial_budgets SET status='prepared',reserved_microusd=? WHERE id=?",
                   (total, trial_id))
    return {"trialId": trial_id, "generationRequests": len(prepared),
            "reservedMicrousd": total, "countRequests": len(prepared)}


def _mark_unknown(connect, trial_id, job_id, ordinal, character_id, request_id, attempt_id, code, result=None):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        jobs.settle_dispatch(db,job_id,character_id,ordinal,request_id,attempt_id,state='unknown')
        db.execute("""UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,provider_usage_json=?
            WHERE job_id=? AND ordinal=?""",
            (code, result.get("responseId") if result else None,
             json.dumps(result["usage"], sort_keys=True) if result and result.get("usage") else None,
             job_id, ordinal))
        db.execute("UPDATE mv3_jobs SET error_code=? WHERE id=?",(code,job_id))
        db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                   (code, trial_id))


def dispatch_trial_chunk(connect, trial_id: str, job_id: str, character_id: int,
                         adapter: responses.ResponsesAdapter):
    """One serial generation. The paid request must already have a durable reservation."""
    if not isinstance(adapter, responses.ResponsesAdapter):
        raise ValueError("responses_adapter_required")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        trial = _trial(db, trial_id)
        job = jobs.owned(db, job_id, character_id)
        payload = json.loads(job["payload_json"])
        if (trial["character_id"] != character_id
                or payload.get("trial_id") != trial_id or adapter.model != trial["model"]):
            raise ValueError("trial_not_dispatchable")
        jobs.require_dispatch_settled(db,job,request_error='trial_unsettled_request',trial_id=trial_id)
        if trial["status"] not in ("prepared", "running"):
            raise ValueError("trial_not_dispatchable")
        if trial["dispatched_requests"] >= trial["max_requests"]:
            raise ValueError("trial_request_limit")
        if db.execute("SELECT 1 FROM mv3_model_chunks WHERE trial_id=? AND state IN ('possibly_dispatched','unknown')",
                      (trial_id,)).fetchone():
            raise ValueError("trial_unsettled_request")
        row = db.execute("SELECT * FROM mv3_model_chunks WHERE job_id=? AND state='prepared' ORDER BY ordinal LIMIT 1",
                         (job_id,)).fetchone()
        if not row or row["trial_id"] != trial_id:
            raise ValueError("trial_chunk_not_prepared")
        if job["status"] == "queued":
            jobs.claim(db, job_id, character_id)
            job = jobs.owned(db, job_id, character_id)
        if (job["status"] != "running" or job["lease_until"] is None or
                job["lease_until"] <= time.time() or
                jobs.snapshot(db, character_id, payload) != job["input_hash"]):
            raise ValueError("trial_source_changed")
        inputs = []
        for ref in json.loads(row["input_json"]):
            live = core.live_source(db, character_id, ref["sourceKind"], ref["sourceId"])
            if live is None or live.content_hash != ref["contentHash"] or live.state_hash != ref["stateHash"]:
                raise ValueError("trial_source_changed")
            inputs.append({**ref, "content": live.text})
        request = _request(trial["model"], job["kind"], row["prompt_variant"], inputs,
                           payload["output_cap"], payload.get("level"))
        if core._hash(request) != row["input_hash"]:
            raise ValueError("trial_request_changed")
        if (trial["reserved_microusd"] < row["reserved_usd_micros"] or
                trial["spent_estimate_microusd"] + trial["reserved_microusd"] > trial["max_microusd"]):
            raise ValueError("trial_budget_exceeded")
        attempt_id=jobs.begin_dispatch(db,job_id,character_id,row['ordinal'],
            expected_request_id=row['request_id'],expected_input_hash=row['input_hash'])
        db.execute("UPDATE mv3_model_chunks SET provider_status='possibly_dispatched' WHERE job_id=? AND ordinal=?",
                   (job_id,row['ordinal']))
        db.execute("UPDATE mv3_trial_budgets SET status='running',dispatched_requests=dispatched_requests+1 WHERE id=?",
                   (trial_id,))
        db.execute("UPDATE mv3_jobs SET lease_until=? WHERE id=?", (time.time() + 120, job_id))
        ordinal = row["ordinal"]
    try:
        result = adapter.generate(request)
    except Exception:
        _mark_unknown(connect, trial_id, job_id, ordinal, character_id,row['request_id'],attempt_id,"provider_result_unknown")
        raise ValueError("provider_result_unknown") from None
    price = json.loads(trial["price_json"])
    try:
        estimated = responses.usage_microusd(result["usage"], price["inputUsdPerMillion"],
                                             price["outputUsdPerMillion"])
    except (KeyError, ValueError):
        _mark_unknown(connect, trial_id, job_id, ordinal, character_id,row['request_id'],attempt_id,"provider_usage_missing", result)
        raise ValueError("provider_usage_missing") from None
    try:
        if result["modelId"] != trial["model"]:
            raise ValueError("provider_model_mismatch")
        if result["serviceTier"] != "default":
            raise ValueError("provider_service_tier_mismatch")
        if result["status"] != "completed" or not result["text"]:
            raise ValueError("provider_" + result["status"])
        if row["prompt_variant"] == responses.M35_VARIANT:
            items, warnings = fidelity.validate_chunk(inputs, {"finishReason": "stop", "output": result["text"]}, kind=job["kind"])
            output = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        else:
            warnings = []
            output = json.dumps({"legacyText": result["text"]}, ensure_ascii=False)
        failure = None
    except (ValueError, KeyError) as exc:
        failure = str(exc)
        output, warnings = None, []
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current_job = jobs.owned(db, job_id, character_id)
        current_trial = _trial(db, trial_id)
        if current_trial["status"] != "running":
            raise ValueError("trial_status_changed_after_dispatch")
        if estimated > row["reserved_usd_micros"]:
            failure = "provider_usage_exceeds_reservation"
            output, warnings = None, []
        try:
            unchanged = jobs.snapshot(db, character_id, payload) == current_job["input_hash"]
        except ValueError:
            unchanged = False
        if not unchanged or current_job["status"] != ("unknown" if jobs._a3_installed(db) else "running") or ("cancel_intent" in current_job.keys() and current_job['cancel_intent']):
            failure = "source_changed_or_lease_expired"
        state=jobs.settle_dispatch(db,job_id,character_id,ordinal,row['request_id'],attempt_id,
            state='rejected' if failure else 'completed',
            used_input=result['usage']['input_tokens'],used_output=result['usage']['output_tokens'],
            result_hash=core._hash([result['responseId'],result['status'],result['usage'],output]),
            trial_id=trial_id,usd_estimate=estimated)
        db.execute("""UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
            provider_usage_json=?,usage_estimated_usd_micros=?,output_json=?,warning_json=?
            WHERE job_id=? AND ordinal=?""",
            (result['status'],result['responseId'],json.dumps(result['usage'],sort_keys=True),
             estimated,output,json.dumps(warnings),job_id,ordinal))
        if failure:
            jobs.recompute_parent(db,job_id,character_id,error=failure)
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                       (failure, trial_id))
            return {"status": "stopped", "error": failure}
        remaining = db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state!='completed'",
                               (job_id,)).fetchone()
        version_id = None
        if not remaining:
            if row["prompt_variant"] == responses.M35_VARIANT:
                db.execute("SAVEPOINT m36_finish")
                try:
                    version = jobs._finish_model(db, current_job, payload, provider="openai_responses",
                                                 model=trial["model"],
                                                 prompt_version=responses.prompt_version(responses.M35_VARIANT))
                    version_id = version["versionId"]
                    db.execute("RELEASE m36_finish")
                except Exception as exc:
                    db.execute("ROLLBACK TO m36_finish")
                    db.execute("RELEASE m36_finish")
                    failure = str(exc)
            else:
                jobs.recompute_parent(db,job_id,character_id,output_ready=True)
        if failure:
            jobs.recompute_parent(db,job_id,character_id,error=failure)
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                       (failure, trial_id))
            return {"status": "stopped", "error": failure}
        if not db.execute("SELECT 1 FROM mv3_model_chunks WHERE trial_id=? AND state!='completed'",
                          (trial_id,)).fetchone():
            db.execute("UPDATE mv3_trial_budgets SET status='completed' WHERE id=?", (trial_id,))
        return {"status": "completed" if version_id else "recorded", "versionId": version_id,
                "usageEstimatedMicrousd": estimated}
