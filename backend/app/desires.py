"""Per-character simulated drives and bounded, manual awakening sessions."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from .autonomy import ControlInput

router = APIRouter(prefix="/api/desires")

DRIVES = (
    ("autonomy.independence", "自主与控制", "独立自主"),
    ("safety.security", "安全与稳定", "安全与稳定"),
    ("attachment.intimacy", "依恋与亲密", "亲密交流"),
    ("attachment.care", "依恋与亲密", "关怀照顾"),
    ("esteem.recognition", "自尊与社会价值", "获得认可"),
    ("esteem.self_respect", "自尊与社会价值", "维护自尊"),
    ("exploration.curiosity", "探索与认知", "好奇与探索"),
    ("exploration.mastery", "探索与认知", "掌握知识"),
    ("creation.play", "游戏与创造", "娱乐与游戏"),
    ("creation.creativity", "游戏与创造", "创造与表达"),
    ("physiological.rest", "生理与感官", "休息与低刺激"),
    ("meaning.integrity", "意义与身份", "价值一致性"),
)
DRIVE_IDS = {item[0] for item in DRIVES}
HALF_LIFE_HOURS = 72
OFFLINE_CAP_HOURS = 72
TERMINAL = {"completed", "cancelled", "failed"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegacyEmptyControl(StrictModel):
    """Older guided sessions send an empty object for pause, resume and cancel."""


class EnableInput(StrictModel):
    enabled: bool


class DriveEffect(StrictModel):
    drive_id: str
    activation_delta: float = Field(default=0, ge=-1, le=1)
    satisfaction_delta: float = Field(default=0, ge=-1, le=1)
    inhibition_delta: float = Field(default=0, ge=-1, le=1)


class EventInput(StrictModel):
    event_id: str = Field(min_length=8, max_length=100)
    source: Literal["user", "chat_confirmed", "awakening", "feedback"] = "user"
    description: str = Field(min_length=1, max_length=500)
    effects: list[DriveEffect] = Field(min_length=1, max_length=12)


class FeedbackInput(EventInput):
    activity_id: str = Field(min_length=1, max_length=100)
    completed: bool
    outcome: Literal["positive", "neutral", "negative"]
    reflection: str = Field(default="", max_length=1000)


class SimulationInput(StrictModel):
    hours: float = Field(default=0, ge=0, le=720)
    events: list[EventInput] = Field(default_factory=list, max_length=20)


class ToolPolicyInput(StrictModel):
    acknowledged_read_only: bool


class AwakeningInput(StrictModel):
    model_profile_id: int
    mode: Literal["guided", "free"] = "guided"
    request_id: str | None = Field(default=None, min_length=8, max_length=100)
    environment_id: str = "local-studio-v1"
    max_output_tokens: int = Field(default=2400, ge=128, le=16000)
    feedback_review_calls: int = Field(default=0, ge=0, le=1)
    feedback_review_tokens: int = Field(default=600, ge=128, le=1000)
    psychology_appraisal_calls: int = Field(default=0, ge=0, le=1)
    psychology_appraisal_tokens: int = Field(default=600, ge=128, le=1200)
    duration_minutes: int = Field(default=10, ge=1, le=60)
    max_model_calls: int = Field(default=3, ge=1, le=10)
    max_tool_calls: int = Field(default=2, ge=0, le=10)
    allowed_tool_ids: list[int] = Field(default_factory=list, max_length=20)


class ToolDecisionInput(StrictModel):
    approve: bool


class PreferenceDecisionInput(StrictModel):
    approve: bool


class VolitionDecision(StrictModel):
    decision: Literal["act", "defer", "reject", "rest", "none", "tool"]
    goal: str = Field(default="", max_length=300)
    targetItemId: str | None = None
    reasonSummary: str = Field(default="", max_length=600)
    relatedDrives: list[str] = Field(default_factory=list, max_length=6)
    toolId: int | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    suggestedEffects: list[DriveEffect] = Field(default_factory=list, max_length=12)


def install(app, connect, now, model_url, auth_headers, mcp_request, validate_tool_arguments):
    from . import responses_adapter

    def stamp() -> str:
        return now()

    def clamp(value: float) -> float:
        return round(max(0.0, min(1.0, value)), 6)

    def policy_fingerprint(tool, server) -> str:
        raw = json.dumps([tool["id"], tool["name"], tool["input_schema"], tool["policy_revision"], server["endpoint"], server["auth_type"], server["policy_revision"]], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def require_character(db, character_id: int):
        character = db.execute("SELECT * FROM characters WHERE id=?", (character_id,)).fetchone()
        if character is None:
            raise HTTPException(404, "角色不存在")
        return character

    def enabled(db, character_id: int):
        character = require_character(db, character_id)
        flag = db.execute("SELECT enabled FROM desire_settings WHERE character_id=?", (character_id,)).fetchone()
        if not flag or not flag[0]:
            raise HTTPException(409, "此角色尚未启用欲望系统")
        return character

    def seed(db, character_id: int):
        db.execute("INSERT OR IGNORE INTO desire_settings(character_id,enabled) VALUES(?,0)", (character_id,))
        db.executemany("INSERT OR IGNORE INTO drive_states(character_id,drive_id,baseline,activation,satisfaction,inhibition,updated_at,version) VALUES(?,?,0.5,0,0.5,0,?,1)",
                       [(character_id, drive_id, stamp()) for drive_id, _, _ in DRIVES])

    def advance(db, character_id: int, at: datetime | None = None):
        at = (at or datetime.now(timezone.utc)).replace(microsecond=0)
        rows = db.execute("SELECT * FROM drive_states WHERE character_id=?", (character_id,)).fetchall()
        for row in rows:
            previous = datetime.fromisoformat(row["updated_at"])
            hours = min(OFFLINE_CAP_HOURS, max(0.0, (at - previous).total_seconds() / 3600))
            activation = clamp(row["activation"] * 2 ** (-hours / HALF_LIFE_HOURS))
            satisfaction = clamp(0.5 + (row["satisfaction"] - 0.5) * 2 ** (-hours / (HALF_LIFE_HOURS * 2)))
            inhibition = clamp(row["inhibition"] * 2 ** (-hours / 24))
            if hours:
                db.execute("UPDATE drive_states SET activation=?,satisfaction=?,inhibition=?,updated_at=?,version=version+1 WHERE character_id=? AND drive_id=?",
                           (activation, satisfaction, inhibition, at.isoformat(timespec="seconds"), character_id, row["drive_id"]))

    def snapshot(db, character_id: int, *, update=True):
        enabled(db, character_id)
        seed(db, character_id)
        if update:
            advance(db, character_id)
        rows = db.execute("SELECT * FROM drive_states WHERE character_id=? ORDER BY drive_id", (character_id,)).fetchall()
        names = {item[0]: item[1:] for item in DRIVES}
        drives = [{**dict(row), "category": names[row["drive_id"]][0], "name": names[row["drive_id"]][1]} for row in rows]
        prominent = sorted(drives, key=lambda item: item["activation"] * (1 - item["inhibition"]), reverse=True)[:3]
        conflicts = []
        if next(x for x in drives if x["drive_id"] == "exploration.curiosity")["activation"] >= 0.6 and next(x for x in drives if x["drive_id"] == "physiological.rest")["activation"] >= 0.6:
            conflicts.append("同时存在探索兴趣和休息倾向；尚未形成行为决定。")
        return {"schemaVersion": 1, "character_id": character_id, "drives": drives,
                "advisory": {"tendencies": [{"drive_id": x["drive_id"], "name": x["name"], "activation": x["activation"], "inhibition": x["inhibition"]} for x in prominent if x["activation"] * (1 - x["inhibition"]) >= 0.25],
                             "conflicts": conflicts, "note": "模拟状态仅供参考，可以忽略；不代表真实生理需求。"}}

    def write_event(db, character_id: int, payload: EventInput, *, feedback=None):
        enabled(db, character_id)
        if any(item.drive_id not in DRIVE_IDS for item in payload.effects):
            raise HTTPException(422, "未知驱力 ID")
        existing = db.execute("SELECT id FROM desire_events WHERE character_id=? AND event_id=?", (character_id, payload.event_id)).fetchone()
        if existing:
            return False
        seed(db, character_id)
        advance(db, character_id)
        db.execute("INSERT INTO desire_events(character_id,event_id,source,description,effects_json,feedback_json,created_at) VALUES(?,?,?,?,?,?,?)",
                   (character_id, payload.event_id, payload.source, payload.description, payload.model_dump_json(include={"effects"}),
                    json.dumps(feedback, ensure_ascii=False) if feedback else None, stamp()))
        for item in payload.effects:
            row = db.execute("SELECT * FROM drive_states WHERE character_id=? AND drive_id=?", (character_id, item.drive_id)).fetchone()
            activation_delta = item.activation_delta * (0.5 + row["baseline"])
            if activation_delta > 0 and row["last_satisfied_at"]:
                since = (datetime.now(timezone.utc) - datetime.fromisoformat(row["last_satisfied_at"])).total_seconds()
                if 0 <= since < 7200:
                    activation_delta *= 0.5
            satisfied_at = stamp() if item.satisfaction_delta > 0 and feedback and feedback["outcome"] == "positive" else row["last_satisfied_at"]
            db.execute("UPDATE drive_states SET activation=?,satisfaction=?,inhibition=?,last_satisfied_at=?,updated_at=?,version=version+1 WHERE character_id=? AND drive_id=?",
                       (clamp(row["activation"] + activation_delta), clamp(row["satisfaction"] + item.satisfaction_delta), clamp(row["inhibition"] + item.inhibition_delta), satisfied_at, stamp(), character_id, item.drive_id))
        if feedback and feedback["completed"] and feedback["outcome"] == "positive":
            for item in payload.effects:
                if item.satisfaction_delta <= 0:
                    continue
                positive_evidence = []
                for event_row in db.execute("SELECT event_id,effects_json,feedback_json FROM desire_events WHERE character_id=? AND source='feedback' ORDER BY id", (character_id,)):
                    detail = json.loads(event_row["feedback_json"] or "{}")
                    effects = json.loads(event_row["effects_json"] or "{}").get("effects", [])
                    if detail.get("outcome") == "positive" and any(effect.get("drive_id") == item.drive_id and effect.get("satisfaction_delta", 0) > 0 for effect in effects):
                        positive_evidence.append(event_row["event_id"])
                count = len(positive_evidence)
                pending = db.execute("SELECT 1 FROM preference_changes WHERE character_id=? AND drive_id=? AND status='pending'", (character_id, item.drive_id)).fetchone()
                if count >= 3 and count % 3 == 0 and not pending:
                    baseline = db.execute("SELECT baseline FROM drive_states WHERE character_id=? AND drive_id=?", (character_id, item.drive_id)).fetchone()[0]
                    evidence = positive_evidence[-3:]
                    db.execute("INSERT INTO preference_changes(character_id,drive_id,before_value,after_value,evidence_count,evidence_json,status,created_at) VALUES(?,?,?,?,?,?,'pending',?)",
                               (character_id, item.drive_id, baseline, clamp(baseline + 0.02), count, json.dumps(evidence), stamp()))
        return True

    def apply_regulation(db, character_id: int, event_id: str, effects: list[dict[str, Any]], description: str):
        """Apply confirmed Phase 4 deltas through the drive boundary, without baseline learning."""
        enabled(db, character_id)
        if db.execute("SELECT 1 FROM desire_events WHERE character_id=? AND event_id=?", (character_id, event_id)).fetchone():
            return False
        seed(db, character_id)
        for effect in effects:
            if effect["drive_id"] not in DRIVE_IDS:
                raise HTTPException(422, "未知驱力 ID")
            row = db.execute("SELECT * FROM drive_states WHERE character_id=? AND drive_id=?", (character_id, effect["drive_id"])).fetchone()
            db.execute("UPDATE drive_states SET activation=?,satisfaction=?,inhibition=?,last_satisfied_at=?,updated_at=?,version=version+1 WHERE character_id=? AND drive_id=?", (
                clamp(row["activation"] + effect["activation_delta"]), clamp(row["satisfaction"] + effect["satisfaction_delta"]),
                clamp(row["inhibition"] + effect["inhibition_delta"]), stamp() if effect["satisfaction_delta"] > 0 else row["last_satisfied_at"],
                stamp(), character_id, effect["drive_id"]))
        db.execute("INSERT INTO desire_events(character_id,event_id,source,description,effects_json,created_at) VALUES(?,?,'feedback',?,?,?)",
                   (character_id, event_id, description[:500], json.dumps({"effects": effects}, ensure_ascii=False), stamp()))
        return True

    def session(db, session_id: str):
        row = db.execute("SELECT * FROM awakening_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "唤醒会话不存在")
        result = dict(row)
        result["allowed_tool_ids"] = json.loads(result.pop("allowed_tools_json"))
        decision_json = result.pop("decision_json")
        request_json = result.pop("tool_request_json")
        result["decision"] = json.loads(decision_json) if decision_json else None
        result["tool_request"] = json.loads(request_json) if request_json else None
        result["history"] = [dict(x) for x in db.execute("SELECT * FROM awakening_activity WHERE session_id=? ORDER BY id", (session_id,))]
        return result

    def active(db, session_id: str):
        item = session(db, session_id)
        if item["status"] in TERMINAL:
            raise HTTPException(409, "会话已经结束")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(item["deadline_at"]):
            db.execute("UPDATE awakening_sessions SET status='completed',ended_at=? WHERE id=?", (stamp(), session_id))
            db.commit()
            raise HTTPException(409, "会话已达到时限")
        return item

    async def model_text(profile, messages, max_output_tokens=None, return_usage=False, response_schema=None):
        protocol = profile["protocol"]
        if protocol == "chatgpt_plan":
            from . import chatgpt_plan
            body = chatgpt_plan.payload(profile, messages, [])
            body["max_output_tokens"] = min(profile["max_tokens"], max_output_tokens) if max_output_tokens is not None else profile["max_tokens"]
            if response_schema is not None:
                from .structured_output import response_format
                body["text"] = response_schema if isinstance(response_schema, dict) else response_format(response_schema)
            url = f"{chatgpt_plan.API_BASE}/responses"
            headers = {"Authorization": f"Bearer {await chatgpt_plan.access_token()}", "Content-Type": "application/json"}
        elif protocol == "openai_responses":
            body = responses_adapter.payload(profile, messages, [], False)
            if max_output_tokens is not None:
                body["max_output_tokens"] = min(profile["max_tokens"], max_output_tokens)
            url = responses_adapter.endpoint(profile["base_url"])
            headers = auth_headers(profile)
        else:
            body = {"model": profile["model_name"], "messages": messages, "stream": False, "temperature": profile["temperature"], "max_tokens": min(profile["max_tokens"], max_output_tokens or 1200)}
            url = model_url(profile["base_url"])
            headers = auth_headers(profile)
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                if protocol == "chatgpt_plan":
                    completed = None
                    stream_state = responses_adapter.StreamState()
                    text_parts = []
                    async with client.stream("POST", url, headers=headers, json=body) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data_line = line[5:].strip()
                            if not data_line or data_line == "[DONE]":
                                continue
                            event = json.loads(data_line)
                            if event.get("type") == "response.failed":
                                raise HTTPException(502, chatgpt_plan.error_message(event))
                            if event.get("type") == "response.incomplete":
                                raise HTTPException(502, "模型输出未完成，状态未更新")
                            normalized = stream_state.normalize(event)
                            part = normalized["choices"][0]["delta"].get("content")
                            if part:
                                text_parts.append(part)
                            if event.get("type") == "response.completed":
                                completed = event.get("response") or {}
                    if completed is None:
                        raise HTTPException(502, "模型未返回完整结果，状态未更新")
                    raw = responses_adapter.preview_text(completed) or "".join(text_parts)
                    if not raw.strip():
                        raise HTTPException(502, "模型未返回文本，状态未更新")
                    usage = completed.get("usage") or {}
                    output_tokens = usage.get("output_tokens")
                    cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
                    return (cleaned, output_tokens) if return_usage else cleaned
                response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            raw = responses_adapter.preview_text(data) if protocol in ("chatgpt_plan", "openai_responses") else data["choices"][0]["message"]["content"]
            cleaned = raw.strip().removeprefix("```json").removesuffix("```").strip()
            usage = data.get("usage") or {}
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
            return (cleaned, output_tokens) if return_usage else cleaned
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError, json.JSONDecodeError) as exc:
            raise HTTPException(502, f"模型调用失败，状态未更新：{str(exc)[:200]}") from exc

    async def deliberate(profile, messages):
        try:
            parsed = VolitionDecision.model_validate_json(await model_text(profile, messages, response_schema=VolitionDecision))
            if any(x not in DRIVE_IDS for x in parsed.relatedDrives) or any(x.drive_id not in DRIVE_IDS for x in parsed.suggestedEffects):
                raise ValueError("invalid drive IDs")
            if parsed.decision == "tool" and parsed.toolId is None:
                raise ValueError("invalid tool request")
            if len(json.dumps(parsed.arguments, ensure_ascii=False)) > 8000:
                raise ValueError("tool arguments too large")
            return parsed.model_dump()
        except (ValueError, PydanticValidationError) as exc:
            raise HTTPException(502, f"意愿判断失败，状态未更新：{str(exc)[:200]}") from exc

    from . import autonomy
    free_service = autonomy.install(app, connect, now, model_text, snapshot, mcp_request, validate_tool_arguments, policy_fingerprint)
    from . import feedback
    feedback.install(app, connect, now, model_text, snapshot, apply_regulation, DRIVE_IDS)
    from . import learning
    learning.install(app, connect, now, model_text)
    from . import psychology
    psychology.install(app, connect, now, model_text, DRIVE_IDS)
    from . import behavior
    behavior.install(app, connect, now)

    @router.get("/{character_id}")
    def get_state(character_id: int):
        with connect() as db:
            require_character(db, character_id)
            seed(db, character_id)
            flag = db.execute("SELECT enabled FROM desire_settings WHERE character_id=?", (character_id,)).fetchone()[0]
            return {"enabled": bool(flag), **(snapshot(db, character_id) if flag else {"schemaVersion": 1, "character_id": character_id, "drives": [], "advisory": None})}

    @router.put("/{character_id}/enabled")
    def set_enabled(character_id: int, payload: EnableInput):
        with connect() as db:
            require_character(db, character_id)
            seed(db, character_id)
            db.execute("UPDATE desire_settings SET enabled=? WHERE character_id=?", (int(payload.enabled), character_id))
        return {"enabled": payload.enabled}

    @router.post("/{character_id}/events")
    def add_event(character_id: int, payload: EventInput):
        with connect() as db:
            applied = write_event(db, character_id, payload)
            result = snapshot(db, character_id, update=False)
        return {"applied": applied, **result}

    @router.post("/{character_id}/feedback")
    def add_feedback(character_id: int, payload: FeedbackInput):
        with connect() as db:
            payload.source = "feedback"
            existing = db.execute("SELECT 1 FROM feedback_records WHERE character_id=? AND activity_id=?", (character_id, payload.activity_id)).fetchone()
            applied = False if existing else write_event(db, character_id, payload, feedback=payload.model_dump(include={"activity_id", "completed", "outcome", "reflection"}))
            if applied:
                db.execute("INSERT INTO feedback_records(character_id,activity_id,event_id,created_at) VALUES(?,?,?,?)", (character_id, payload.activity_id, payload.event_id, stamp()))
                if payload.completed:
                    db.execute("UPDATE volition_goals SET status='completed',updated_at=? WHERE character_id=? AND session_id=? AND status='chosen'", (stamp(), character_id, payload.activity_id))
            result = snapshot(db, character_id, update=False)
        return {"applied": applied, **result}

    @router.post("/{character_id}/simulate")
    def simulate(character_id: int, payload: SimulationInput):
        with connect() as db:
            result = snapshot(db, character_id, update=False)
        drives = {item["drive_id"]: dict(item) for item in result["drives"]}
        for item in drives.values():
            elapsed = min(payload.hours, OFFLINE_CAP_HOURS)
            item["activation"] = clamp(item["activation"] * 2 ** (-elapsed / HALF_LIFE_HOURS))
            item["satisfaction"] = clamp(0.5 + (item["satisfaction"] - 0.5) * 2 ** (-elapsed / (HALF_LIFE_HOURS * 2)))
            item["inhibition"] = clamp(item["inhibition"] * 2 ** (-elapsed / 24))
        for event in payload.events:
            for effect in event.effects:
                if effect.drive_id not in drives:
                    raise HTTPException(422, "未知驱力 ID")
                drives[effect.drive_id]["activation"] = clamp(drives[effect.drive_id]["activation"] + effect.activation_delta * (0.5 + drives[effect.drive_id]["baseline"]))
                drives[effect.drive_id]["satisfaction"] = clamp(drives[effect.drive_id]["satisfaction"] + effect.satisfaction_delta)
                drives[effect.drive_id]["inhibition"] = clamp(drives[effect.drive_id]["inhibition"] + effect.inhibition_delta)
        return {"schemaVersion": 1, "drives": list(drives.values()), "saved": False}

    @router.get("/{character_id}/events")
    def events(character_id: int):
        with connect() as db:
            enabled(db, character_id)
            return [dict(row) for row in db.execute("SELECT id,event_id,source,description,created_at FROM desire_events WHERE character_id=? ORDER BY id DESC LIMIT 100", (character_id,))]

    @router.post("/{character_id}/snapshots")
    def save_snapshot(character_id: int):
        with connect() as db:
            state = snapshot(db, character_id)
            snapshot_id = str(uuid.uuid4())
            db.execute("INSERT INTO desire_snapshots(id,character_id,state_json,created_at) VALUES(?,?,?,?)", (snapshot_id, character_id, json.dumps(state, ensure_ascii=False), stamp()))
        return {"id": snapshot_id, "created_at": stamp()}

    @router.get("/{character_id}/snapshots")
    def list_snapshots(character_id: int):
        with connect() as db:
            enabled(db, character_id)
            return [dict(row) for row in db.execute("SELECT id,created_at FROM desire_snapshots WHERE character_id=? ORDER BY created_at DESC LIMIT 30", (character_id,))]

    @router.post("/{character_id}/snapshots/{snapshot_id}/restore")
    def restore_snapshot(character_id: int, snapshot_id: str):
        with connect() as db:
            enabled(db, character_id)
            saved = db.execute("SELECT state_json FROM desire_snapshots WHERE id=? AND character_id=?", (snapshot_id, character_id)).fetchone()
            if not saved:
                raise HTTPException(404, "快照不存在")
            state = json.loads(saved[0])
            if state.get("schemaVersion") != 1 or {item.get("drive_id") for item in state.get("drives", [])} != DRIVE_IDS:
                raise HTTPException(422, "快照格式无效")
            for item in state["drives"]:
                if any(not isinstance(item.get(key), (int, float)) or not math.isfinite(item[key]) or not 0 <= item[key] <= 1 for key in ("baseline", "activation", "satisfaction", "inhibition")):
                    raise HTTPException(422, "快照数值无效")
                db.execute("UPDATE drive_states SET baseline=?,activation=?,satisfaction=?,inhibition=?,last_satisfied_at=NULL,updated_at=?,version=version+1 WHERE character_id=? AND drive_id=?",
                           (item["baseline"], item["activation"], item["satisfaction"], item["inhibition"], stamp(), character_id, item["drive_id"]))
            db.execute("INSERT INTO desire_events(character_id,event_id,source,description,effects_json,created_at) VALUES(?,?,'user','恢复欲望状态快照','{}',?)", (character_id, str(uuid.uuid4()), stamp()))
            return snapshot(db, character_id, update=False)

    @router.get("/{character_id}/preferences")
    def preferences(character_id: int):
        with connect() as db:
            enabled(db, character_id)
            return [dict(row) for row in db.execute("SELECT * FROM preference_changes WHERE character_id=? ORDER BY id DESC", (character_id,))]

    @router.get("/{character_id}/goals")
    def goals(character_id: int):
        with connect() as db:
            enabled(db, character_id)
            return [dict(row) for row in db.execute("SELECT * FROM volition_goals WHERE character_id=? ORDER BY id DESC LIMIT 50", (character_id,))]

    @router.post("/{character_id}/preferences/{change_id}/decision")
    def preference_decision(character_id: int, change_id: int, payload: PreferenceDecisionInput):
        with connect() as db:
            enabled(db, character_id)
            row = db.execute("SELECT * FROM preference_changes WHERE id=? AND character_id=? AND status='pending'", (change_id, character_id)).fetchone()
            if not row:
                raise HTTPException(409, "偏好建议不存在或已处理")
            if payload.approve:
                current = db.execute("SELECT baseline FROM drive_states WHERE character_id=? AND drive_id=?", (character_id, row["drive_id"])).fetchone()[0]
                if abs(current - row["before_value"]) > 1e-6:
                    raise HTTPException(409, "基线已变化，请重新审阅")
                db.execute("UPDATE drive_states SET baseline=?,version=version+1 WHERE character_id=? AND drive_id=?", (row["after_value"], character_id, row["drive_id"]))
            db.execute("UPDATE preference_changes SET status=?,decided_at=? WHERE id=?", ("applied" if payload.approve else "rejected", stamp(), change_id))
        return {"status": "applied" if payload.approve else "rejected"}

    @router.post("/{character_id}/preferences/{change_id}/rollback")
    def preference_rollback(character_id: int, change_id: int):
        with connect() as db:
            enabled(db, character_id)
            row = db.execute("SELECT * FROM preference_changes WHERE id=? AND character_id=? AND status='applied'", (change_id, character_id)).fetchone()
            if not row:
                raise HTTPException(409, "没有可撤销的变更")
            current = db.execute("SELECT baseline FROM drive_states WHERE character_id=? AND drive_id=?", (character_id, row["drive_id"])).fetchone()[0]
            if abs(current - row["after_value"]) > 1e-6:
                raise HTTPException(409, "后续偏好已变化，不能直接撤销")
            db.execute("UPDATE drive_states SET baseline=?,version=version+1 WHERE character_id=? AND drive_id=?", (row["before_value"], character_id, row["drive_id"]))
            db.execute("UPDATE preference_changes SET status='rolled_back',decided_at=? WHERE id=?", (stamp(), change_id))
        return {"status": "rolled_back"}

    @router.get("/{character_id}/tools")
    def tools(character_id: int):
        with connect() as db:
            enabled(db, character_id)
            return [dict(row) for row in db.execute("SELECT t.id,t.name,t.description,t.enabled,t.input_schema,t.read_only_acknowledged,t.updated_at,s.name AS server_name,s.status AS server_status FROM mcp_tools t JOIN mcp_servers s ON s.id=t.server_id WHERE NOT EXISTS(SELECT 1 FROM plugin_tools pt WHERE pt.mcp_tool_id=t.id) ORDER BY s.name,t.name")]

    @router.put("/{character_id}/tools/{tool_id}/policy")
    def tool_policy(character_id: int, tool_id: int, payload: ToolPolicyInput):
        with connect() as db:
            enabled(db, character_id)
            if not db.execute("UPDATE mcp_tools SET read_only_acknowledged=?,policy_revision=policy_revision+1,updated_at=? WHERE id=?", (int(payload.acknowledged_read_only), stamp(), tool_id)).rowcount:
                raise HTTPException(404, "工具不存在")
        return {"read_only_acknowledged": payload.acknowledged_read_only}

    @router.post("/{character_id}/awakening")
    async def start(character_id: int, payload: AwakeningInput):
        with connect() as db:
            character = enabled(db, character_id)
            if payload.mode == "free":
                if not payload.request_id:
                    raise HTTPException(422, "自由活动需要唯一请求 ID")
                previous = db.execute("SELECT * FROM awakening_sessions WHERE character_id=? AND request_id=?", (character_id, payload.request_id)).fetchone()
                if previous:
                    fingerprint = hashlib.sha256(payload.model_dump_json(exclude_none=True).encode()).hexdigest()
                    if previous["request_fingerprint"] != fingerprint:
                        raise HTTPException(409, "启动请求 ID 已用于不同配置")
                    return free_service.view(db, previous)
            profile = db.execute("SELECT id FROM model_profiles WHERE id=? AND enabled=1", (payload.model_profile_id,)).fetchone()
            if not profile:
                raise HTTPException(422, "请选择可用的模型档案")
            expired = db.execute("SELECT * FROM awakening_sessions WHERE character_id=? AND status NOT IN ('completed','cancelled','failed') AND deadline_at<=?", (character_id, stamp())).fetchall()
            for old in expired:
                if old["mode"] == "free":
                    free_service.finish(db, old, "time_exhausted")
                else:
                    db.execute("UPDATE awakening_sessions SET status='completed',ended_at=? WHERE id=?", (stamp(), old["id"]))
            prior = db.execute("SELECT id FROM awakening_sessions WHERE character_id=? AND status NOT IN ('completed','cancelled','failed')", (character_id,)).fetchone()
            if prior:
                raise HTTPException(409, "此角色已有未结束的唤醒会话")
            allowed = sorted(set(payload.allowed_tool_ids))
            tool_policy = {}
            for tool_id in allowed:
                tool = db.execute("SELECT * FROM mcp_tools WHERE id=?", (tool_id,)).fetchone()
                server = db.execute("SELECT * FROM mcp_servers WHERE id=?", (tool["server_id"],)).fetchone() if tool else None
                if not tool or not tool["enabled"] or not server or not server["enabled"] or server["status"] != "connected" or db.execute("SELECT 1 FROM plugin_tools WHERE mcp_tool_id=?", (tool_id,)).fetchone():
                    raise HTTPException(422, "所选工具不可用")
                if tool["read_only_acknowledged"]:
                    tool_policy[str(tool_id)] = policy_fingerprint(tool, server)
            state = snapshot(db, character_id)
            session_id = str(uuid.uuid4())
            deadline = datetime.now(timezone.utc) + timedelta(minutes=payload.duration_minutes)
            if payload.mode == "free":
                result, created = free_service.start(db, character_id, profile["id"], payload, state, allowed, tool_policy, deadline.isoformat(timespec="seconds"))
                session_id = result["id"]
            else:
                created = False
                db.execute("INSERT INTO awakening_sessions(id,character_id,model_profile_id,status,started_at,deadline_at,max_model_calls,max_tool_calls,allowed_tools_json,tool_policy_json,snapshot_json) VALUES(?,?,?,'ready',?,?,?,?,?,?,?)",
                           (session_id, character_id, profile["id"], stamp(), deadline.isoformat(timespec="seconds"), payload.max_model_calls, payload.max_tool_calls, json.dumps(allowed), json.dumps(tool_policy), json.dumps(state, ensure_ascii=False)))
                result = session(db, session_id)
        if created:
            free_service.kick(session_id)
        return result

    @router.get("/{character_id}/awakening")
    def list_sessions(character_id: int):
        with connect() as db:
            enabled(db, character_id)
            ids = [row[0] for row in db.execute("SELECT id FROM awakening_sessions WHERE character_id=? ORDER BY started_at DESC LIMIT 20", (character_id,))]
            return [session(db, item) for item in ids]

    @router.get("/{character_id}/awakening/{session_id}")
    def get_session(character_id: int, session_id: str):
        with connect() as db:
            enabled(db, character_id)
            result = session(db, session_id)
            if result["character_id"] != character_id:
                raise HTTPException(404, "唤醒会话不存在")
            if result["mode"] == "free":
                return free_service.view(db, db.execute("SELECT * FROM awakening_sessions WHERE id=?", (session_id,)).fetchone())
            return result

    @router.post("/{character_id}/awakening/{session_id}/pause")
    def pause(character_id: int, session_id: str, payload: ControlInput | LegacyEmptyControl | None = None):
        with connect() as db:
            enabled(db, character_id)
            row = db.execute("SELECT mode FROM awakening_sessions WHERE id=? AND character_id=?", (session_id, character_id)).fetchone()
            if row and row["mode"] == "free":
                if not isinstance(payload, ControlInput):
                    raise HTTPException(422, "自由活动控制需要请求 ID 和会话版本")
                return free_service.control(db, character_id, session_id, "pause", payload)[0]
            item = active(db, session_id)
            if item["character_id"] != character_id or item["status"] not in ("ready", "awaiting_confirmation"):
                raise HTTPException(409, "当前状态无法暂停")
            db.execute("UPDATE awakening_sessions SET status='paused' WHERE id=?", (session_id,))
            return session(db, session_id)

    @router.post("/{character_id}/awakening/{session_id}/resume")
    async def resume(character_id: int, session_id: str, payload: ControlInput | LegacyEmptyControl | None = None):
        with connect() as db:
            enabled(db, character_id)
            row = db.execute("SELECT mode FROM awakening_sessions WHERE id=? AND character_id=?", (session_id, character_id)).fetchone()
            if row and row["mode"] == "free":
                if not isinstance(payload, ControlInput):
                    raise HTTPException(422, "自由活动控制需要请求 ID 和会话版本")
                result, should_kick = free_service.control(db, character_id, session_id, "resume", payload)
            else:
                should_kick = False
            if not row or row["mode"] != "free":
                item = active(db, session_id)
                if item["character_id"] != character_id or item["status"] != "paused":
                    raise HTTPException(409, "当前状态无法恢复")
                target = "awaiting_confirmation" if item["tool_request"] else "ready"
                db.execute("UPDATE awakening_sessions SET status=? WHERE id=?", (target, session_id))
                result = session(db, session_id)
        if should_kick:
            free_service.kick(session_id)
        return result

    @router.post("/{character_id}/awakening/{session_id}/cancel")
    def cancel(character_id: int, session_id: str, payload: ControlInput | LegacyEmptyControl | None = None):
        with connect() as db:
            enabled(db, character_id)
            row = db.execute("SELECT mode FROM awakening_sessions WHERE id=? AND character_id=?", (session_id, character_id)).fetchone()
            if row and row["mode"] == "free":
                if not isinstance(payload, ControlInput):
                    raise HTTPException(422, "自由活动控制需要请求 ID 和会话版本")
                return free_service.control(db, character_id, session_id, "cancel", payload)[0]
            item = session(db, session_id)
            if item["character_id"] != character_id:
                raise HTTPException(404, "唤醒会话不存在")
            if item["status"] not in TERMINAL:
                db.execute("UPDATE awakening_sessions SET status='cancelled',ended_at=?,tool_request_json=NULL WHERE id=?", (stamp(), session_id))
            return session(db, session_id)

    @router.post("/{character_id}/awakening/{session_id}/step")
    async def step(character_id: int, session_id: str):
        with connect() as db:
            character = enabled(db, character_id)
            item = active(db, session_id)
            if item["mode"] == "free":
                raise HTTPException(409, "自由活动由后端自动推进")
            if item["character_id"] != character_id or item["status"] != "ready":
                raise HTTPException(409, "当前会话不能继续思考")
            if item["model_calls"] >= item["max_model_calls"]:
                db.execute("UPDATE awakening_sessions SET status='completed',ended_at=? WHERE id=?", (stamp(), session_id))
                return session(db, session_id)
            profile = db.execute("SELECT * FROM model_profiles WHERE id=? AND enabled=1", (item["model_profile_id"],)).fetchone()
            if not profile:
                raise HTTPException(409, "所选模型已不可用")
            snapshot_data = snapshot(db, character_id)
            memories = [row[0] for row in db.execute("SELECT content FROM memories WHERE character_id=? AND scope='long' AND enabled=1 ORDER BY id DESC LIMIT 5", (character_id,))]
            history = [dict(row) for row in db.execute("SELECT kind,detail FROM awakening_activity WHERE session_id=? ORDER BY id DESC LIMIT 5", (session_id,))]
            from . import volition
            intentions = [{"id": x["id"], "title": x["title"], "stage": x["stage"], "reasonSummary": x["reason_summary"], "selfNote": x["self_note"]} for x in volition.context_items(db, character_id, stamp())]
            tools = [dict(row) for row in db.execute("SELECT t.id,t.name,t.description FROM mcp_tools t JOIN mcp_servers s ON s.id=t.server_id WHERE t.enabled=1 AND s.enabled=1 AND s.status='connected' AND NOT EXISTS(SELECT 1 FROM plugin_tools pt WHERE pt.mcp_tool_id=t.id)") if row["id"] in item["allowed_tool_ids"]]
            db.execute("UPDATE awakening_sessions SET status='deliberating',model_calls=model_calls+1 WHERE id=?", (session_id,))
        messages = [{"role": "system", "content": "你是角色的公开意愿判断辅助。结合角色设定、模拟驱力、已有意愿和环境作出选择；欲望不是命令，未决意愿也不是必须完成的待办。如继续已有意愿，可填写其 targetItemId。只输出 JSON 对象：decision(act|defer|reject|rest|none|tool), goal, targetItemId(可选), reasonSummary, relatedDrives(字符串数组), toolId(仅 tool), arguments(仅 tool), suggestedEffects(可选的驱力反馈建议数组，每项含 drive_id, activation_delta, satisfaction_delta, inhibition_delta，范围 -1 到 1)。建议只供用户确认，不直接更新状态。不要输出私有推理。工具只可从可用列表选；不能假装执行。"},
                    {"role": "user", "content": json.dumps({"character": {"name": character["name"], "system_prompt": character["system_prompt"][:3000]}, "advisory": snapshot_data["advisory"], "memories": memories, "intentions": intentions, "recent_activity": history, "available_tools": tools, "remaining_tool_calls": item["max_tool_calls"] - item["tool_calls"]}, ensure_ascii=False)}]
        try:
            decision = await deliberate(profile, messages)
        except Exception:
            with connect() as db:
                db.execute("UPDATE awakening_sessions SET status='ready' WHERE id=? AND status='deliberating'", (session_id,))
            raise
        with connect() as db:
            fresh = session(db, session_id)
            if fresh["status"] != "deliberating":
                raise HTTPException(409, "会话已被中止")
            if decision.get("targetItemId"):
                target = db.execute("SELECT title FROM volition_items WHERE id=? AND character_id=? AND status='active'", (decision["targetItemId"], character_id)).fetchone()
                if not target:
                    db.execute("UPDATE awakening_sessions SET status='ready' WHERE id=?", (session_id,))
                    db.commit()
                    raise HTTPException(422, "所引用的意愿不存在或已结束")
                if not decision["goal"]:
                    decision["goal"] = target["title"]
            if datetime.now(timezone.utc) >= datetime.fromisoformat(fresh["deadline_at"]):
                db.execute("UPDATE awakening_sessions SET status='completed',ended_at=? WHERE id=?", (stamp(), session_id))
                return session(db, session_id)
            if decision["decision"] == "tool":
                tool_id = decision["toolId"]
                if tool_id not in fresh["allowed_tool_ids"] or fresh["tool_calls"] >= fresh["max_tool_calls"]:
                    db.execute("UPDATE awakening_sessions SET status='ready' WHERE id=?", (session_id,)); db.commit()
                    raise HTTPException(403, "工具不在本次授权范围或预算已用尽")
                tool = db.execute("SELECT * FROM mcp_tools WHERE id=?", (tool_id,)).fetchone()
                server = db.execute("SELECT * FROM mcp_servers WHERE id=?", (tool["server_id"],)).fetchone() if tool else None
                if not tool or not tool["enabled"] or not server or not server["enabled"] or server["status"] != "connected" or db.execute("SELECT 1 FROM plugin_tools WHERE mcp_tool_id=?", (tool_id,)).fetchone():
                    db.execute("UPDATE awakening_sessions SET status='ready' WHERE id=?", (session_id,)); db.commit()
                    raise HTTPException(403, "工具已不可用")
                try:
                    validate_tool_arguments(decision["arguments"], json.loads(tool["input_schema"] or "{}"))
                except HTTPException:
                    db.execute("UPDATE awakening_sessions SET status='ready' WHERE id=?", (session_id,)); db.commit()
                    raise
                policy = json.loads(fresh["tool_policy_json"] or "{}")
                acknowledged = bool(tool["read_only_acknowledged"]) and policy.get(str(tool_id)) == policy_fingerprint(tool, server)
                request = {"tool_id": tool_id, "name": tool["name"], "arguments": decision["arguments"], "read_only_preapproved": acknowledged, "configuration_fingerprint": policy_fingerprint(tool, server)}
                db.execute("UPDATE awakening_sessions SET status='awaiting_confirmation',decision_json=?,tool_request_json=? WHERE id=?", (json.dumps(decision, ensure_ascii=False), json.dumps(request, ensure_ascii=False), session_id))
            else:
                status = "completed" if decision["decision"] in ("rest", "none") else "ready"
                db.execute("UPDATE awakening_sessions SET status=?,decision_json=?,ended_at=? WHERE id=?", (status, json.dumps(decision, ensure_ascii=False), stamp() if status == "completed" else None, session_id))
                if decision["decision"] == "act":
                    db.execute("INSERT INTO awakening_activity(session_id,kind,detail,created_at) VALUES(?,'reflection_draft',?,?)", (session_id, decision["goal"], stamp()))
            if decision["goal"] and decision["decision"] not in ("rest", "none"):
                goal_status = {"act": "chosen", "tool": "chosen", "defer": "deferred", "reject": "rejected"}[decision["decision"]]
                goal_cursor = db.execute("INSERT INTO volition_goals(character_id,session_id,description,status,reason_summary,related_drives_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                         (character_id, session_id, decision["goal"], goal_status, decision["reasonSummary"], json.dumps(decision["relatedDrives"]), stamp(), stamp()))
                volition.link_legacy_decision(db, character_id, goal_cursor.lastrowid, decision, stamp())
            db.execute("INSERT INTO awakening_activity(session_id,kind,detail,created_at) VALUES(?,'decision',?,?)", (session_id, json.dumps(decision, ensure_ascii=False), stamp()))
            result = session(db, session_id)
        if result["tool_request"] and result["tool_request"]["read_only_preapproved"]:
            return await decide_tool(character_id, session_id, ToolDecisionInput(approve=True))
        return result

    @router.post("/{character_id}/awakening/{session_id}/tools/decision")
    async def decide_tool(character_id: int, session_id: str, payload: ToolDecisionInput):
        with connect() as db:
            enabled(db, character_id)
            item = active(db, session_id)
            if item["mode"] == "free":
                raise HTTPException(409, "请使用自由活动的操作确认接口")
            if item["character_id"] != character_id or item["status"] != "awaiting_confirmation" or not item["tool_request"]:
                raise HTTPException(409, "没有待处理工具")
            request = item["tool_request"]
            if not payload.approve:
                db.execute("UPDATE awakening_sessions SET status='ready',tool_request_json=NULL WHERE id=?", (session_id,))
                db.execute("INSERT INTO awakening_activity(session_id,kind,detail,created_at) VALUES(?,'tool_rejected',?,?)", (session_id, json.dumps(request, ensure_ascii=False), stamp()))
                return session(db, session_id)
            tool = db.execute("SELECT * FROM mcp_tools WHERE id=?", (request["tool_id"],)).fetchone()
            server = db.execute("SELECT * FROM mcp_servers WHERE id=?", (tool["server_id"],)).fetchone() if tool else None
            if not tool or not tool["enabled"] or not server or not server["enabled"] or server["status"] != "connected" or request["tool_id"] not in item["allowed_tool_ids"] or db.execute("SELECT 1 FROM plugin_tools WHERE mcp_tool_id=?", (request["tool_id"],)).fetchone():
                raise HTTPException(403, "工具授权已失效")
            if request["configuration_fingerprint"] != policy_fingerprint(tool, server):
                raise HTTPException(403, "工具配置已变化，请重新进行意愿判断")
            if request["read_only_preapproved"]:
                policy = json.loads(item["tool_policy_json"] or "{}")
                if not tool["read_only_acknowledged"] or policy.get(str(tool["id"])) != policy_fingerprint(tool, server):
                    raise HTTPException(403, "只读工具预授权已失效")
            validate_tool_arguments(request["arguments"], json.loads(tool["input_schema"] or "{}"))
            db.execute("UPDATE awakening_sessions SET status='acting',tool_calls=tool_calls+1 WHERE id=?", (session_id,))
        try:
            result, _ = await mcp_request(server, "tools/call", {"name": tool["name"], "arguments": request["arguments"]}, 3000 + item["tool_calls"])
            detail = json.dumps({"tool_id": tool["id"], "result": result}, ensure_ascii=False)[:10000]
            kind = "tool_result"
        except Exception as exc:
            detail = json.dumps({"tool_id": request["tool_id"], "error": str(exc)[:300]}, ensure_ascii=False)
            kind = "tool_error"
        with connect() as db:
            fresh = session(db, session_id)
            db.execute("INSERT INTO awakening_activity(session_id,kind,detail,created_at) VALUES(?,?,?,?)", (session_id, kind, detail, stamp()))
            if fresh["status"] == "acting":
                db.execute("UPDATE awakening_sessions SET status='ready',tool_request_json=NULL WHERE id=?", (session_id,))
            return session(db, session_id)

    app.include_router(router)
    from . import volition
    volition.install(app, connect, now, model_text, snapshot, DRIVE_IDS)


def init_db(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS desire_settings(character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,enabled INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS drive_states(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,drive_id TEXT NOT NULL,baseline REAL NOT NULL,activation REAL NOT NULL,satisfaction REAL NOT NULL,inhibition REAL NOT NULL,last_satisfied_at TEXT,updated_at TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(character_id,drive_id));
    CREATE TABLE IF NOT EXISTS desire_events(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,event_id TEXT NOT NULL,source TEXT NOT NULL,description TEXT NOT NULL,effects_json TEXT NOT NULL,feedback_json TEXT,created_at TEXT NOT NULL,UNIQUE(character_id,event_id));
    CREATE TABLE IF NOT EXISTS feedback_records(character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,activity_id TEXT NOT NULL,event_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(character_id,activity_id));
    CREATE TABLE IF NOT EXISTS desire_snapshots(id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,state_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS preference_changes(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,drive_id TEXT NOT NULL,before_value REAL NOT NULL,after_value REAL NOT NULL,evidence_count INTEGER NOT NULL,evidence_json TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL,created_at TEXT NOT NULL,decided_at TEXT);
    CREATE TABLE IF NOT EXISTS volition_goals(id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,session_id TEXT NOT NULL REFERENCES awakening_sessions(id) ON DELETE CASCADE,description TEXT NOT NULL,status TEXT NOT NULL,reason_summary TEXT NOT NULL,related_drives_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS awakening_sessions(id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,model_profile_id INTEGER NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,deadline_at TEXT NOT NULL,ended_at TEXT,max_model_calls INTEGER NOT NULL,max_tool_calls INTEGER NOT NULL,model_calls INTEGER NOT NULL DEFAULT 0,tool_calls INTEGER NOT NULL DEFAULT 0,allowed_tools_json TEXT NOT NULL,tool_policy_json TEXT NOT NULL DEFAULT '{}',snapshot_json TEXT NOT NULL,decision_json TEXT,tool_request_json TEXT);
    CREATE TABLE IF NOT EXISTS awakening_activity(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL REFERENCES awakening_sessions(id) ON DELETE CASCADE,kind TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(mcp_tools)")}
    if "read_only_acknowledged" not in columns:
        db.execute("ALTER TABLE mcp_tools ADD COLUMN read_only_acknowledged INTEGER NOT NULL DEFAULT 0")
    if "policy_revision" not in columns:
        db.execute("ALTER TABLE mcp_tools ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 0")
    server_columns = {row[1] for row in db.execute("PRAGMA table_info(mcp_servers)")}
    if "policy_revision" not in server_columns:
        db.execute("ALTER TABLE mcp_servers ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 0")
    drive_columns = {row[1] for row in db.execute("PRAGMA table_info(drive_states)")}
    if "last_satisfied_at" not in drive_columns:
        db.execute("ALTER TABLE drive_states ADD COLUMN last_satisfied_at TEXT")
    preference_columns = {row[1] for row in db.execute("PRAGMA table_info(preference_changes)")}
    if "evidence_json" not in preference_columns:
        db.execute("ALTER TABLE preference_changes ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]'")
    db.execute("UPDATE awakening_sessions SET status='paused' WHERE status IN ('deliberating','acting')")
    from . import volition
    volition.init_db(db)
    from . import autonomy
    autonomy.init_db(db)
    from . import feedback
    feedback.init_db(db)
    from . import learning
    learning.init_db(db)
    from . import psychology
    psychology.init_db(db)
    from . import behavior
    behavior.init_db(db)


def chat_advisory(db, character_id: int, message: str) -> str:
    """Return a short, relevant reference; never mutate drives during chat."""
    flag = db.execute("SELECT enabled FROM desire_settings WHERE character_id=?", (character_id,)).fetchone()
    if not flag or not flag[0] or not message:
        return ""
    topics = {
        "exploration.curiosity": ("探索", "好奇", "问题", "研究"),
        "creation.creativity": ("创作", "画", "写", "设计"),
        "physiological.rest": ("休息", "疲惫", "累", "睡"),
        "attachment.intimacy": ("亲密", "陪伴", "关系"),
        "autonomy.independence": ("自主", "独立", "选择"),
    }
    relevant = [drive_id for drive_id, words in topics.items() if any(word in message for word in words)]
    if not relevant:
        return ""
    rows = db.execute("SELECT drive_id,activation,updated_at FROM drive_states WHERE character_id=?", (character_id,)).fetchall()
    current_time = datetime.now(timezone.utc)
    values = {row["drive_id"]: clamp_chat_activation(row, current_time) for row in rows}
    names = {drive_id: name for drive_id, _, name in DRIVES}
    selected = [f"{names[drive_id]}：{values.get(drive_id, 0):.2f}" for drive_id in relevant[:2]]
    return "模拟内部倾向（仅供参考，可忽略）：" + "；".join(selected)


def clamp_chat_activation(row, at: datetime) -> float:
    hours = min(OFFLINE_CAP_HOURS, max(0.0, (at - datetime.fromisoformat(row["updated_at"])).total_seconds() / 3600))
    return max(0.0, min(1.0, row["activation"] * 2 ** (-hours / HALF_LIFE_HOURS)))
