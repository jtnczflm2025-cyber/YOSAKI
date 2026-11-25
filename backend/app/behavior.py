"""Phase 6.5 observation diagnostics. Metrics never alter character state."""
from __future__ import annotations

import csv
import io
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from . import memory_authority

router = APIRouter(prefix="/api/desires")
# Hong Kong uses UTC+08:00; fixed offset avoids an undeclared tzdata dependency on Windows.
LOCAL_ZONE = timezone(timedelta(hours=8))
DAILY_CAP = 4


def local_day(stamp: str | None = None) -> str:
    instant = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else datetime.now(timezone.utc)
    return instant.astimezone(LOCAL_ZONE).date().isoformat()


def init_db(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS behavior_model_calls(
      id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      session_id TEXT, source_id TEXT NOT NULL, purpose TEXT NOT NULL, local_day TEXT NOT NULL,
      reserved_output_tokens INTEGER NOT NULL, actual_output_tokens INTEGER,
      elapsed_ms INTEGER, status TEXT NOT NULL, created_at TEXT NOT NULL,
      CHECK(reserved_output_tokens>=0));
    CREATE INDEX IF NOT EXISTS idx_behavior_calls_day ON behavior_model_calls(character_id,local_day);
    CREATE TABLE IF NOT EXISTS behavior_resource_samples(
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      session_id TEXT NOT NULL, local_day TEXT NOT NULL, sampled_at TEXT NOT NULL,
      database_bytes INTEGER NOT NULL, row_counts_json TEXT NOT NULL,
      PRIMARY KEY(character_id,session_id));
    """)
    db.execute("UPDATE behavior_model_calls SET status='interrupted' WHERE status='running'")


def resource_sample(db, character_id: int, session_id: str, stamp: str):
    counts = {}
    for table in ("psychological_events", "activity_timeline", "activity_operations", "feedback_activities_v4", "learning_evidence", "volition_decisions"):
        counts[table] = db.execute(f"SELECT COUNT(*) FROM {table} WHERE character_id=?", (character_id,)).fetchone()[0]
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute("""INSERT INTO behavior_resource_samples VALUES(?,?,?,?,?,?)
               ON CONFLICT(character_id,session_id) DO UPDATE SET
               local_day=excluded.local_day,sampled_at=excluded.sampled_at,
               database_bytes=excluded.database_bytes,row_counts_json=excluded.row_counts_json""",
               (character_id, session_id, local_day(stamp), stamp, pages * page_size, json.dumps(counts)))


def fraction(numerator: int, denominator: int, minimum: int = 5):
    return {"numerator": numerator, "denominator": denominator,
            "value": round(numerator / denominator, 4) if denominator else None,
            "insufficientSample": denominator < minimum}


def report(db, character_id: int, days: int):
    if not 1 <= days <= 31:
        raise HTTPException(422, "报告范围须为 1～31 天")
    if not db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone():
        raise HTTPException(404, "角色不存在")
    today = datetime.now(LOCAL_ZONE).date()
    start = (today - timedelta(days=days - 1)).isoformat()
    since = datetime.fromisoformat(start).replace(tzinfo=LOCAL_ZONE).astimezone(timezone.utc).isoformat()
    sessions = [dict(x) for x in db.execute("SELECT id,started_at,ended_at,status,termination_reason,model_calls,tool_calls FROM awakening_sessions WHERE character_id=? AND mode='free' AND started_at>=? ORDER BY started_at,id", (character_id, since))]
    ids = {x["id"] for x in sessions}
    timeline = [dict(x) for x in db.execute("SELECT session_id,kind,detail_json,created_at FROM activity_timeline WHERE character_id=? AND created_at>=? ORDER BY id", (character_id, since)) if x["session_id"] in ids]
    decisions = []
    for row in timeline:
        if row["kind"] != "decision": continue
        try: detail = json.loads(row["detail_json"])
        except (ValueError, TypeError): continue
        decisions.append({"sessionId": row["session_id"], "at": row["created_at"], "choice": detail.get("choice"),
                          "actionId": detail.get("actionId"), "targetItemId": detail.get("targetItemId"),
                          "hasGoal": bool(detail.get("targetItemId")), "contextSignals": detail.get("contextSignals") or {}})
    calls = [dict(x) for x in db.execute("SELECT id,session_id,source_id,purpose,local_day,reserved_output_tokens,actual_output_tokens,elapsed_ms,status,created_at FROM behavior_model_calls WHERE character_id=? AND local_day>=? ORDER BY created_at,id", (character_id, start))]
    samples = [dict(x) for x in db.execute("SELECT * FROM behavior_resource_samples WHERE character_id=? AND local_day>=? ORDER BY sampled_at", (character_id, start))]
    for sample in samples: sample["rowCounts"] = json.loads(sample.pop("row_counts_json"))
    activity = [x for x in decisions if x["choice"] in ("environment", "tool")]
    repeat = sum(a.get("actionId") and a["actionId"] == b.get("actionId") for a, b in zip(activity, activity[1:]))
    by_choice = dict(Counter(x["choice"] for x in decisions if x["choice"]))
    volition_rows = list(db.execute("SELECT result_json FROM volition_decisions WHERE character_id=? AND created_at>=?", (character_id, since)))
    abandon = sum(x["choice"] == "reject" for x in decisions) + sum(json.loads(x[0]).get("type") == "reject_goal" for x in volition_rows)
    invalid = sum(x["status"] == "invalid_output" for x in calls)
    prior_targets = {}
    continued = 0
    for decision in decisions:
        target = decision["targetItemId"]
        if target and target in prior_targets and prior_targets[target] != decision["sessionId"]: continued += 1
        if target: prior_targets[target] = decision["sessionId"]
    conflict_choices = dict(Counter(x[0] for x in db.execute("SELECT choice FROM psychological_conflict_decisions WHERE character_id=? AND created_at>=?", (character_id, since))))
    metrics = {"goalContinuity": fraction(continued, len(decisions)),
               "activityRepetition": fraction(repeat, max(0, len(activity) - 1)),
               "explicitAbandonment": fraction(abandon, len(decisions) + len(volition_rows)),
               "invalidModelOutput": fraction(invalid, len(calls)),
               "existingGoalLinkage": fraction(sum(bool(x["targetItemId"]) for x in activity), len(activity)),
               "choiceDistribution": by_choice, "conflictChoiceDistribution": conflict_choices}
    flags = []
    if len(activity) >= 4 and len({x.get("actionId") for x in activity[-4:]}) == 1 and len({json.dumps(x["contextSignals"], sort_keys=True) for x in activity[-4:]}) >= 2:
        flags.append({"code": "repeated_activity", "evidence": [x["sessionId"] for x in activity[-4:]]})
    if len(decisions) >= 5 and all(x["choice"] in ("rest", "no_action") and x["contextSignals"].get("energyBand") == "high" for x in decisions[-5:]):
        flags.append({"code": "repeated_rest", "evidence": [x["sessionId"] for x in decisions[-5:]]})
    if len(calls) >= 5 and invalid / len(calls) >= .2:
        flags.append({"code": "invalid_output_rising", "evidence": [x["id"] for x in calls if x["status"] == "invalid_output"]})
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        event_growth = last["rowCounts"]["psychological_events"] - first["rowCounts"]["psychological_events"]
        if last["database_bytes"] - first["database_bytes"] > 1_048_576 and event_growth < 10:
            flags.append({"code": "database_growth_without_events", "evidence": [first["session_id"], last["session_id"]]})
    daily = []
    for offset in range(days):
        day = (today - timedelta(days=days - 1 - offset)).isoformat()
        day_sessions = [x["id"] for x in sessions if local_day(x["started_at"]) == day]
        day_calls = [x for x in calls if x["local_day"] == day]
        daily.append({"day": day, "sessionIds": day_sessions, "modelCalls": len(day_calls),
                      "reservedOutputTokens": sum(x["reserved_output_tokens"] for x in day_calls),
                      "knownActualOutputTokens": sum(x["actual_output_tokens"] or 0 for x in day_calls),
                      "actualUsageComplete": all(x["actual_output_tokens"] is not None for x in day_calls),
                      "gap": None if day_sessions else "no_manual_session"})
    return {"characterId": character_id, "days": days, "fromDay": start, "toDay": today.isoformat(),
            "daily": daily, "sessions": sessions, "decisions": decisions, "modelCalls": calls, "resourceSamples": samples,
            "metrics": metrics, "diagnosticFlags": flags, "interpretation": "诊断参考；样本不足时不形成行为结论。"}


def install(app, connect, now):
    def mark_invalid(character_id, source_id, purpose):
        with connect() as db:
            row = db.execute("SELECT id FROM behavior_model_calls WHERE character_id=? AND source_id=? AND purpose=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (character_id, source_id, purpose)).fetchone()
            if row: db.execute("UPDATE behavior_model_calls SET status='invalid_output' WHERE id=?", (row[0],))

    async def measured_call(character_id, session_id, source_id, purpose, reserved, invoke):
        call_id = str(uuid.uuid4())
        day = local_day()
        with connect() as db:
            if os.getenv("YOSAKI_OBSERVATION_MODE") == "1":
                used = db.execute("SELECT COUNT(*) FROM behavior_model_calls WHERE character_id=? AND local_day=?", (character_id, day)).fetchone()[0]
                if used >= DAILY_CAP: raise HTTPException(429, "隔离观察实例当天模型调用已达 4 次")
            db.execute("INSERT INTO behavior_model_calls VALUES(?,?,?,?,?,?,?,NULL,NULL,'running',?)",
                       (call_id, character_id, session_id, source_id, purpose, day, max(0, int(reserved)), now()))
        started = time.monotonic()
        try:
            result = await invoke()
        except BaseException as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            with connect() as db:
                db.execute("UPDATE behavior_model_calls SET status=?,elapsed_ms=? WHERE id=?",
                           ("invalid_output" if isinstance(exc, (ValueError,)) else "failed", elapsed, call_id))
            raise
        usage = result[1] if isinstance(result, tuple) and len(result) > 1 else None
        with connect() as db:
            db.execute("UPDATE behavior_model_calls SET status='completed',elapsed_ms=?,actual_output_tokens=? WHERE id=?",
                       (round((time.monotonic() - started) * 1000), usage if isinstance(usage, int) and usage >= 0 else None, call_id))
        return result

    app.state.behavior_model_call = measured_call
    app.state.behavior_mark_invalid = mark_invalid
    app.state.behavior_resource_sample = resource_sample

    @router.get("/{character_id}/behavior/report")
    def get_report(character_id: int, days: int = 7):
        with connect() as db: return report(db, character_id, days)

    @router.get("/{character_id}/behavior/context-preview")
    def context_preview(character_id: int, topic: str = "", mode: str = "free", includeMemoryContext: bool = False):
        if mode not in ("free", "volition"): raise HTTPException(422, "未知上下文模式")
        with connect() as db:
            if not db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone(): raise HTTPException(404, "角色不存在")
            max_length = 500 if mode == "free" else 1000
            rows = memory_authority.recent_long_rows(db, character_id) if mode == "free" or includeMemoryContext else []
            learning = app.state.learning_context(db, character_id, topic[:200], {x["id"] for x in rows})
            psychology = app.state.psychology_context(db, character_id, topic[:200])
            entries = [{"sourceId": f"memory:{x['id']}", "selectedBecause": "最近启用的长期记忆", "length": min(len(x["content"]), max_length)} for x in rows]
            entries += [{"sourceId": f"learning:{x['sourceId']}", "selectedBecause": "主题相关且已批准", "length": len(x["content"])} for x in learning["memories"]]
            entries += [{"sourceId": f"preference:{x['sourceId']}", "selectedBecause": "主题相关且已批准", "length": len(json.dumps(x, ensure_ascii=False))} for x in learning["preferences"]]
            entries += [{"sourceId": f"strategy:{x['id']}", "selectedBecause": "条件相关且已批准", "length": len(json.dumps(x, ensure_ascii=False))} for x in learning["strategies"]]
            entries += [{"sourceId": f"goal-note:{x['sourceId']}", "selectedBecause": "相关目标的已批准进度", "length": len(x["summary"])} for x in learning["goalNotes"]]
            entries.append({"sourceId": f"psychology:snapshot:{psychology['ruleVersion']}", "selectedBecause": "当前有界模拟参考", "length": len(json.dumps(psychology, ensure_ascii=False))})
            entries += [{"sourceId": f"drive:{x[0]}", "selectedBecause": "当前驱力参谋输入", "length": 1} for x in db.execute("SELECT drive_id FROM drive_states WHERE character_id=? ORDER BY drive_id", (character_id,))]
            seen, duplicates = set(), []
            for entry in entries:
                if entry["sourceId"] in seen: duplicates.append(entry["sourceId"])
                seen.add(entry["sourceId"])
            return {"mode": mode, "topic": topic[:200], "includeMemoryContext": includeMemoryContext,
                    "entries": entries, "learning": learning, "psychologicalReference": psychology,
                    "duplicateSourceIds": duplicates}

    @router.get("/{character_id}/behavior/export")
    def export(character_id: int, days: int = 7, format: str = "json"):
        with connect() as db: data = report(db, character_id, days)
        if format == "json":
            return Response(json.dumps(data, ensure_ascii=False), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="yosaki-behavior.json"'})
        if format != "csv": raise HTTPException(422, "仅支持 json 或 csv")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["kind", "day", "session_id", "purpose_or_choice", "status", "reserved_tokens", "actual_tokens", "elapsed_ms"])
        safe = lambda value: "'" + str(value) if str(value).startswith(("=", "+", "-", "@")) else value
        for item in data["modelCalls"]:
            writer.writerow(["model", item["local_day"], safe(item["session_id"] or ""), item["purpose"], item["status"], item["reserved_output_tokens"], item["actual_output_tokens"], item["elapsed_ms"]])
        for item in data["decisions"]:
            writer.writerow(["decision", local_day(item["at"]), safe(item["sessionId"]), safe(item["choice"]), "", "", "", ""])
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="yosaki-behavior.csv"'})

    app.include_router(router)
