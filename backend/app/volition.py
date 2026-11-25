"""Phase 2: read-only advice, simulated introspection, and persistent intentions.

This module never invokes MCP tools or changes drive parameters. Model output is
public structured data, not a claim about private reasoning or actual experience.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .psychology import Reflection
from . import memory_authority

router = APIRouter(prefix="/api/desires")
STAGES = ("candidate", "pending", "committed")
ACTIVE = "active"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetaDesire(Strict):
    targetDriveId: str
    stance: Literal["accept", "question", "restrain", "defer", "cultivate", "neutral"]
    explanation: str = Field(default="", max_length=300)


class ObservationResult(Strict):
    schemaVersion: Literal[2]
    selfDescription: str = Field(default="", max_length=600)
    observedDriveIds: list[str] = Field(default_factory=list, max_length=6)
    metaDesires: list[MetaDesire] = Field(default_factory=list, max_length=6)
    unresolvedQuestions: list[str] = Field(default_factory=list, max_length=4)
    reflections: list[Reflection] = Field(default_factory=list, max_length=6)


class ProposedItem(Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    source: Literal["desire", "memory", "personal_plan", "external_event", "self_generated"] = "self_generated"
    relatedDriveIds: list[str] = Field(default_factory=list, max_length=6)
    stage: Literal["candidate", "pending", "committed"] = "candidate"


class DecisionResult(Strict):
    schemaVersion: Literal[2]
    type: Literal["create_goal", "choose_goal", "maintain_goal", "defer_goal", "reject_goal", "revise_goal", "no_action", "undecided"]
    targetItemId: str | None = None
    proposedItem: ProposedItem | None = None
    reasonSummary: str = Field(default="", max_length=600)
    selfNote: str = Field(default="", max_length=600)
    reflections: list[Reflection] = Field(default_factory=list, max_length=6)


class ObservationInput(Strict):
    schemaVersion: Literal[2]
    requestId: str = Field(min_length=8, max_length=100)
    modelProfileId: int
    mode: Literal["passive", "reflective", "goal_focused"] = "passive"
    targetItemId: str | None = None
    includeMemoryContext: bool = False


class DecisionInput(Strict):
    schemaVersion: Literal[2]
    requestId: str = Field(min_length=8, max_length=100)
    modelProfileId: int
    targetItemId: str | None = None
    observationId: str | None = None
    includeMemoryContext: bool = False
    environmentSummary: str = Field(default="", max_length=1000)


class TransitionInput(Strict):
    schemaVersion: Literal[2]
    requestId: str = Field(min_length=8, max_length=100)
    expectedVersion: int = Field(ge=1)
    action: Literal["archive", "abandon", "reconsider", "complete"]
    reasonSummary: str = Field(default="", max_length=600)


def init_db(db) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS desire_advisories(
      id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      schema_version INTEGER NOT NULL,source_snapshot_id TEXT NOT NULL,source_json TEXT NOT NULL,
      fingerprint TEXT NOT NULL,report_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_advisories_character ON desire_advisories(character_id,created_at);
    CREATE TABLE IF NOT EXISTS introspection_records(
      id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      schema_version INTEGER NOT NULL,request_id TEXT NOT NULL,model_profile_id INTEGER NOT NULL,
      advisory_id TEXT NOT NULL REFERENCES desire_advisories(id),mode TEXT NOT NULL,target_item_id TEXT,
      result_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(character_id,request_id));
    CREATE TABLE IF NOT EXISTS volition_items(
      id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      schema_version INTEGER NOT NULL,title TEXT NOT NULL,title_key TEXT NOT NULL,description TEXT NOT NULL,
      source TEXT NOT NULL,related_drives_json TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,
      reason_summary TEXT NOT NULL,self_note TEXT NOT NULL,version INTEGER NOT NULL,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_active_volition_title ON volition_items(character_id,title_key) WHERE status='active';
    CREATE INDEX IF NOT EXISTS ix_volition_items_character ON volition_items(character_id,status,updated_at);
    CREATE TABLE IF NOT EXISTS volition_decisions(
      id TEXT PRIMARY KEY,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      schema_version INTEGER NOT NULL,request_id TEXT NOT NULL,model_profile_id INTEGER NOT NULL,
      advisory_id TEXT NOT NULL REFERENCES desire_advisories(id),observation_id TEXT,
      result_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(character_id,request_id));
    CREATE TABLE IF NOT EXISTS volition_item_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT,character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      item_id TEXT NOT NULL REFERENCES volition_items(id) ON DELETE CASCADE,
      request_id TEXT NOT NULL,actor TEXT NOT NULL,before_json TEXT,after_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS volition_legacy_links(
      legacy_goal_id INTEGER PRIMARY KEY REFERENCES volition_goals(id) ON DELETE CASCADE,
      item_id TEXT NOT NULL REFERENCES volition_items(id) ON DELETE CASCADE);
    """)


def item_dict(row) -> dict[str, Any]:
    value = dict(row)
    value["relatedDriveIds"] = json.loads(value.pop("related_drives_json"))
    return value


def normalize_title(title: str) -> str:
    return " ".join(title.strip().casefold().split())


def sync_legacy(db, character_id: int, stamp: str) -> None:
    """Map V3 rows once. The old table remains an immutable compatibility log."""
    rows = db.execute("""SELECT g.* FROM volition_goals g LEFT JOIN volition_legacy_links l
                         ON l.legacy_goal_id=g.id WHERE g.character_id=? AND l.legacy_goal_id IS NULL
                         ORDER BY g.id""", (character_id,)).fetchall()
    for row in rows:
        title = row["description"].strip()
        if not title:
            continue
        key = normalize_title(title)
        existing = db.execute("SELECT id FROM volition_items WHERE character_id=? AND title_key=? AND status='active'", (character_id, key)).fetchone()
        if existing:
            item_id = existing[0]
        else:
            item_id = str(uuid.uuid4())
            stage = "pending" if row["status"] == "deferred" else "candidate"
            status = {"rejected": "rejected", "completed": "completed"}.get(row["status"], ACTIVE)
            db.execute("""INSERT INTO volition_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (item_id, character_id, 2, title, key, "", "self_generated", row["related_drives_json"],
                        stage, status, row["reason_summary"], "", 1, row["created_at"], row["updated_at"]))
            after = item_dict(db.execute("SELECT * FROM volition_items WHERE id=?", (item_id,)).fetchone())
            db.execute("INSERT INTO volition_item_history(character_id,item_id,request_id,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)",
                       (character_id, item_id, f"legacy:{row['id']}", "legacy", None, json.dumps(after, ensure_ascii=False), stamp))
        db.execute("INSERT INTO volition_legacy_links(legacy_goal_id,item_id) VALUES(?,?)", (row["id"], item_id))
    completed = db.execute("""SELECT DISTINCT i.id FROM volition_items i JOIN volition_legacy_links l ON l.item_id=i.id
                              JOIN volition_goals g ON g.id=l.legacy_goal_id
                              WHERE i.character_id=? AND i.status='active' AND g.status='completed'""", (character_id,)).fetchall()
    for linked in completed:
        before_row = db.execute("SELECT * FROM volition_items WHERE id=?", (linked[0],)).fetchone()
        before = item_dict(before_row)
        db.execute("UPDATE volition_items SET status='completed',version=version+1,updated_at=? WHERE id=?", (stamp, linked[0]))
        after = item_dict(db.execute("SELECT * FROM volition_items WHERE id=?", (linked[0],)).fetchone())
        db.execute("INSERT INTO volition_item_history(character_id,item_id,request_id,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)",
                   (character_id, linked[0], f"legacy-complete:{linked[0]}", "legacy", json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), stamp))


def context_items(db, character_id: int, stamp: str) -> list[dict[str, Any]]:
    sync_legacy(db, character_id, stamp)
    rows = db.execute("""SELECT * FROM volition_items WHERE character_id=? AND status='active'
                         ORDER BY CASE stage WHEN 'committed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,updated_at DESC LIMIT 20""",
                      (character_id,)).fetchall()
    return [item_dict(row) for row in rows]


def link_legacy_decision(db, character_id: int, legacy_id: int, decision: dict, stamp: str) -> None:
    """Connect a new manual awakening decision to a continuing intention."""
    if decision.get("targetItemId"):
        target = db.execute("SELECT id FROM volition_items WHERE id=? AND character_id=? AND status='active'", (decision["targetItemId"], character_id)).fetchone()
        if not target:
            raise HTTPException(422, "所引用的意愿不存在或已结束")
        db.execute("INSERT INTO volition_legacy_links(legacy_goal_id,item_id) VALUES(?,?)", (legacy_id, target["id"]))
    sync_legacy(db, character_id, stamp)
    linked = db.execute("SELECT item_id FROM volition_legacy_links WHERE legacy_goal_id=?", (legacy_id,)).fetchone()
    if not linked:
        return
    row = db.execute("SELECT * FROM volition_items WHERE id=? AND character_id=?", (linked[0], character_id)).fetchone()
    if not row or row["status"] != ACTIVE:
        return
    before = item_dict(row)
    stage = ("pending" if row["stage"] != "committed" else "committed") if decision["decision"] == "defer" else ("candidate" if row["stage"] == "pending" and decision["decision"] in ("act", "tool") else row["stage"])
    status = "rejected" if decision["decision"] == "reject" else row["status"]
    if stage == row["stage"] and status == row["status"]:
        return
    db.execute("UPDATE volition_items SET stage=?,status=?,reason_summary=?,version=version+1,updated_at=? WHERE id=?",
               (stage, status, decision["reasonSummary"], stamp, row["id"]))
    after = item_dict(db.execute("SELECT * FROM volition_items WHERE id=?", (row["id"],)).fetchone())
    db.execute("INSERT INTO volition_item_history(character_id,item_id,request_id,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)",
               (character_id, row["id"], f"awakening:{legacy_id}", "character", json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), stamp))


def install(app, connect, now, model_text, snapshot, drive_ids):
    def require(db, character_id):
        if not db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone():
            raise HTTPException(404, "角色不存在")
        flag = db.execute("SELECT enabled FROM desire_settings WHERE character_id=?", (character_id,)).fetchone()
        if not flag or not flag[0]:
            raise HTTPException(409, "此角色尚未启用欲望系统")

    def item(db, character_id, item_id):
        row = db.execute("SELECT * FROM volition_items WHERE id=? AND character_id=?", (item_id, character_id)).fetchone()
        if not row:
            raise HTTPException(404, "意愿不存在")
        return row

    def model(db, profile_id):
        row = db.execute("SELECT * FROM model_profiles WHERE id=? AND enabled=1", (profile_id,)).fetchone()
        if not row:
            raise HTTPException(422, "请选择可用的模型档案")
        return dict(row)

    def advisory(db, character_id):
        state = snapshot(db, character_id)
        source = {"drives": state["drives"], "schemaVersion": 1}
        values = {x["drive_id"]: x for x in state["drives"]}
        evidence = [dict(x) for x in db.execute("SELECT event_id,created_at,effects_json FROM desire_events WHERE character_id=? ORDER BY id DESC LIMIT 30", (character_id,))]
        evidence_ids = {drive_id: [] for drive_id in drive_ids}
        for event in evidence:
            for effect in json.loads(event["effects_json"] or "{}").get("effects", []):
                if effect.get("drive_id") in evidence_ids:
                    evidence_ids[effect["drive_id"]].append(event["event_id"])
        signature = json.dumps({"values": {k: [round(v[field], 2) for field in ("activation", "satisfaction", "inhibition", "baseline")] for k, v in values.items()},
                                "events": [e["event_id"] for e in evidence]}, sort_keys=True)
        fingerprint = hashlib.sha256(signature.encode()).hexdigest()
        prior = db.execute("SELECT * FROM desire_advisories WHERE character_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (character_id,)).fetchone()
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(prior["created_at"])).total_seconds() if prior else 0
        if prior and prior["fingerprint"] == fingerprint and elapsed < 300:
            return json.loads(prior["report_json"])
        previous_values = {x["drive_id"]: x for x in json.loads(prior["source_json"])["drives"]} if prior else {}
        tendencies = []
        for drive in state["drives"]:
            old = previous_values.get(drive["drive_id"])
            delta = drive["activation"] - old["activation"] if old else 0
            trend = "unknown" if not old or elapsed < 300 else ("rising" if delta >= .05 else "falling" if delta <= -.05 else "stable")
            tendencies.append({"driveId": drive["drive_id"], "name": drive["name"], "activation": drive["activation"],
                               "satisfaction": drive["satisfaction"], "trend": trend,
                               "evidenceEventIds": evidence_ids[drive["drive_id"]][:5]})
        tensions = []
        for a, b, label in [("exploration.curiosity", "physiological.rest", "探索与休息"),
                            ("exploration.curiosity", "safety.security", "探索与安全"),
                            ("attachment.intimacy", "autonomy.independence", "亲密与独处")]:
            if values[a]["activation"] >= .6 and values[b]["activation"] >= .6:
                tensions.append({"driveIds": [a, b], "kind": "potential_tension", "description": f"{label}倾向同时较强；是否冲突取决于具体情境。"})
        report = {"schemaVersion": 2, "id": str(uuid.uuid4()), "sourceSnapshotId": str(uuid.uuid4()),
                  "createdAt": now(), "tendencies": tendencies, "conflicts": tensions,
                  "uncertainty": ["驱力数值是模拟参考，不证明主观体验。"] + (["缺少足够的历史间隔，趋势暂不可判断。"] if not prior or elapsed < 300 else [])}
        db.execute("INSERT INTO desire_advisories VALUES(?,?,?,?,?,?,?,?)",
                   (report["id"], character_id, 2, report["sourceSnapshotId"], json.dumps(source, ensure_ascii=False),
                    fingerprint, json.dumps(report, ensure_ascii=False), report["createdAt"]))
        return report

    def put_history(db, character_id, row_before, row_after, request_id, actor):
        db.execute("INSERT INTO volition_item_history(character_id,item_id,request_id,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)",
                   (character_id, row_after["id"], request_id, actor,
                    json.dumps(row_before, ensure_ascii=False) if row_before else None,
                    json.dumps(row_after, ensure_ascii=False), now()))

    def validate_drives(ids):
        if len(ids) != len(set(ids)) or any(value not in drive_ids for value in ids):
            raise HTTPException(422, "未知或重复的驱力 ID")

    @router.get("/{character_id}/advisory")
    def get_advisory(character_id: int):
        with connect() as db:
            require(db, character_id)
            return advisory(db, character_id)

    @router.get("/{character_id}/advisories")
    def list_advisories(character_id: int):
        with connect() as db:
            require(db, character_id)
            return [json.loads(row[0]) for row in db.execute("SELECT report_json FROM desire_advisories WHERE character_id=? ORDER BY created_at DESC,rowid DESC LIMIT 30", (character_id,))]

    @router.get("/{character_id}/advisories/{advisory_id}/source")
    def advisory_source(character_id: int, advisory_id: str):
        with connect() as db:
            require(db, character_id)
            row = db.execute("SELECT source_snapshot_id,source_json FROM desire_advisories WHERE id=? AND character_id=?", (advisory_id, character_id)).fetchone()
            if not row:
                raise HTTPException(404, "参谋报告不存在")
            return {"sourceSnapshotId": row["source_snapshot_id"], **json.loads(row["source_json"])}

    @router.get("/{character_id}/observations")
    def observations(character_id: int):
        with connect() as db:
            require(db, character_id)
            return [{**dict(row), "result": json.loads(row["result_json"])} for row in db.execute("SELECT * FROM introspection_records WHERE character_id=? ORDER BY created_at DESC LIMIT 30", (character_id,))]

    @router.post("/{character_id}/observations")
    async def observe(character_id: int, payload: ObservationInput):
        with connect() as db:
            require(db, character_id)
            prior = db.execute("SELECT * FROM introspection_records WHERE character_id=? AND request_id=?", (character_id, payload.requestId)).fetchone()
            if prior:
                return {**dict(prior), "result": json.loads(prior["result_json"])}
            profile = model(db, payload.modelProfileId)
            target = item_dict(item(db, character_id, payload.targetItemId)) if payload.targetItemId else None
            if payload.mode == "goal_focused" and not target:
                raise HTTPException(422, "目标导向观察需要指定意愿")
            report = advisory(db, character_id)
            character = dict(db.execute("SELECT name,system_prompt FROM characters WHERE id=?", (character_id,)).fetchone())
            character["system_prompt"] = character["system_prompt"][:3000]
            memory_rows = memory_authority.recent_long_rows(db, character_id) if payload.includeMemoryContext else []
            memories = [r["content"][:1000] for r in memory_rows]
        messages = [{"role": "system", "content": "根据当前角色设定，输出一份简短的第一人称模拟自我观察。不要声称真实主观体验，不作诊断，不提出行动命令，不调用工具；允许空描述。仅输出 JSON：schemaVersion=2,selfDescription,observedDriveIds,metaDesires(含 targetDriveId,stance,explanation),unresolvedQuestions,reflections(可选数组，每项含 targetType,targetId,attitude,strength,confidence,reasonSummary,relatedGoalIds)。二阶倾向可互相矛盾，不要因为一次失败就生成强烈抵触。"},
                    {"role": "user", "content": json.dumps({"character": character, "mode": payload.mode, "advisory": report, "target": target, "memories": memories}, ensure_ascii=False)}]
        raw = await app.state.behavior_model_call(character_id, None, f"observation:{payload.requestId}",
            "observation", profile["max_tokens"], lambda: model_text(profile, messages, response_schema=ObservationResult))
        try:
            result = ObservationResult.model_validate_json(raw)
            from .psychology import Reflection
            [Reflection.model_validate(x) for x in result.reflections]
            validate_drives(result.observedDriveIds)
            if any(x.targetDriveId not in drive_ids for x in result.metaDesires):
                raise ValueError("unknown reflected drive")
            if len({(x.targetDriveId, x.stance) for x in result.metaDesires}) != len(result.metaDesires):
                raise ValueError("duplicate reflected attitude")
            if any(len(x) > 300 for x in result.unresolvedQuestions):
                raise ValueError("question too long")
        except (ValidationError, ValueError, HTTPException) as exc:
            app.state.behavior_mark_invalid(character_id, f"observation:{payload.requestId}", "observation")
            raise HTTPException(502, f"自我观察格式无效，状态未更新：{str(exc)[:200]}") from exc
        with connect() as db:
            require(db, character_id)
            prior = db.execute("SELECT * FROM introspection_records WHERE character_id=? AND request_id=?", (character_id, payload.requestId)).fetchone()
            if prior:
                return {**dict(prior), "result": json.loads(prior["result_json"])}
            record_id = str(uuid.uuid4())
            db.execute("INSERT INTO introspection_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (record_id, character_id, 2, payload.requestId, payload.modelProfileId, report["id"], payload.mode,
                        payload.targetItemId, result.model_dump_json(), now()))
            legacy_reflections = [{"targetType": "drive", "targetId": x.targetDriveId,
                                   "attitude": {"accept": "endorse", "question": "uncertain", "restrain": "resist", "defer": "uncertain", "cultivate": "cultivate", "neutral": "uncertain"}[x.stance],
                                   "strength": .4, "confidence": .45, "reasonSummary": x.explanation, "relatedGoalIds": []}
                                  for x in result.metaDesires if x.stance != "neutral"]
            try:
                app.state.psychology_service.record_reflections(db, character_id, f"observation:{record_id}", legacy_reflections + result.reflections)
            except (ValueError, ValidationError) as exc:
                raise HTTPException(502, f"反思性倾向无效，观察未保存：{str(exc)[:150]}") from exc
            row = db.execute("SELECT * FROM introspection_records WHERE id=?", (record_id,)).fetchone()
            return {**dict(row), "result": result.model_dump()}

    @router.get("/{character_id}/intentions")
    def intentions(character_id: int):
        with connect() as db:
            require(db, character_id)
            sync_legacy(db, character_id, now())
            return [item_dict(row) for row in db.execute("SELECT * FROM volition_items WHERE character_id=? ORDER BY status='active' DESC,updated_at DESC LIMIT 200", (character_id,))]

    @router.get("/{character_id}/intentions/{item_id}/history")
    def intention_history(character_id: int, item_id: str):
        with connect() as db:
            require(db, character_id)
            item(db, character_id, item_id)
            return [dict(row) for row in db.execute("SELECT * FROM volition_item_history WHERE character_id=? AND item_id=? ORDER BY id", (character_id, item_id))]

    @router.get("/{character_id}/decisions")
    def decisions(character_id: int):
        with connect() as db:
            require(db, character_id)
            return [{**dict(row), "result": json.loads(row["result_json"])} for row in db.execute("SELECT * FROM volition_decisions WHERE character_id=? ORDER BY created_at DESC LIMIT 30", (character_id,))]

    @router.post("/{character_id}/decisions")
    async def decide(character_id: int, payload: DecisionInput):
        with connect() as db:
            require(db, character_id)
            prior = db.execute("SELECT * FROM volition_decisions WHERE character_id=? AND request_id=?", (character_id, payload.requestId)).fetchone()
            if prior:
                return {**dict(prior), "result": json.loads(prior["result_json"])}
            profile = model(db, payload.modelProfileId)
            items = context_items(db, character_id, now())
            if payload.targetItemId:
                focused = item_dict(item(db, character_id, payload.targetItemId))
                if focused["id"] not in {x["id"] for x in items}:
                    items.append(focused)
            observation = None
            if payload.observationId:
                obs = db.execute("SELECT result_json FROM introspection_records WHERE id=? AND character_id=?", (payload.observationId, character_id)).fetchone()
                if not obs:
                    raise HTTPException(404, "观察记录不存在")
                observation = json.loads(obs[0])
            report = advisory(db, character_id)
            character = dict(db.execute("SELECT name,system_prompt FROM characters WHERE id=?", (character_id,)).fetchone())
            character["system_prompt"] = character["system_prompt"][:3000]
            memory_rows = memory_authority.recent_long_rows(db, character_id) if payload.includeMemoryContext else []
            memories = [r["content"][:1000] for r in memory_rows]
            versions = {x["id"]: x["version"] for x in items}
            model_items = [{"id": x["id"], "title": x["title"], "stage": x["stage"], "source": x["source"],
                            "relatedDriveIds": x["relatedDriveIds"], "reasonSummary": x["reason_summary"][:300],
                            "selfNote": x["self_note"][:300]} for x in items]
            interest_evidence = [dict(x) for x in db.execute("SELECT description,expectation_gap FROM interest_evidence_v4 WHERE character_id=? AND status='candidate' ORDER BY created_at DESC LIMIT 5", (character_id,))]
            learning_context = app.state.learning_context(db, character_id, (next((x["title"] for x in items if x["id"] == payload.targetItemId), "") or payload.environmentSummary), {r["id"] for r in memory_rows})
            psychological_context = app.state.psychology_context(db, character_id, payload.environmentSummary)
        messages = [{"role": "system", "content": "你为当前角色形成公开意愿记录。欲望与模拟心理状态仅供参考，不按最高数值决定。可创建候选兴趣、明确推迟为未决意愿、明确承诺长期目标，也可选择、维持、修改、拒绝、不行动或暂不决定。未决项不产生自动期限或负罪感。二阶倾向不自动高于一阶欲望，允许矛盾保留。不要假装执行活动，不调用工具。仅输出 JSON：schemaVersion=2,type(create_goal|choose_goal|maintain_goal|defer_goal|reject_goal|revise_goal|no_action|undecided),targetItemId(已有目标时),proposedItem(新建或修改时，含 title,description,source,relatedDriveIds,stage),reasonSummary,selfNote,reflections(可选数组；字段 targetType,targetId,attitude,strength,confidence,reasonSummary,relatedGoalIds)。"},
                    {"role": "user", "content": json.dumps({"character": character, "advisory": report, "observation": observation,
                        "intentions": model_items, "interestEvidence": interest_evidence, "targetItemId": payload.targetItemId, "memories": memories,
                        "environmentSummary": payload.environmentSummary, "longTermLearning": learning_context,
                        "psychologicalReference": psychological_context}, ensure_ascii=False)}]
        raw = await app.state.behavior_model_call(character_id, None, f"volition:{payload.requestId}",
            "volition_decision", profile["max_tokens"], lambda: model_text(profile, messages, response_schema=DecisionResult))
        try:
            result = DecisionResult.model_validate_json(raw)
            from .psychology import Reflection
            [Reflection.model_validate(x) for x in result.reflections]
            if result.proposedItem:
                validate_drives(result.proposedItem.relatedDriveIds)
            needs_target = result.type in {"choose_goal", "maintain_goal", "defer_goal", "reject_goal", "revise_goal"}
            if needs_target != bool(result.targetItemId) or (result.type in {"create_goal", "revise_goal"}) != bool(result.proposedItem):
                raise ValueError("invalid target or proposed item")
            if payload.targetItemId and result.targetItemId != payload.targetItemId:
                raise ValueError("target mismatch")
            if result.type in {"no_action", "undecided"} and (result.targetItemId or result.proposedItem):
                raise ValueError("unexpected item")
        except (ValidationError, ValueError, HTTPException) as exc:
            app.state.behavior_mark_invalid(character_id, f"volition:{payload.requestId}", "volition_decision")
            raise HTTPException(502, f"意愿输出格式无效，状态未更新：{str(exc)[:200]}") from exc
        with connect() as db:
            require(db, character_id)
            prior = db.execute("SELECT * FROM volition_decisions WHERE character_id=? AND request_id=?", (character_id, payload.requestId)).fetchone()
            if prior:
                return {**dict(prior), "result": json.loads(prior["result_json"])}
            if result.targetItemId:
                row = item(db, character_id, result.targetItemId)
                if row["status"] != ACTIVE or versions.get(row["id"]) != row["version"]:
                    raise HTTPException(409, "意愿已变化，请重新判断")
                before = item_dict(row)
                proposed = result.proposedItem
                title = proposed.title.strip() if proposed else row["title"]
                provided = proposed.model_fields_set if proposed else set()
                stage = proposed.stage if proposed and "stage" in provided else ("pending" if result.type == "defer_goal" and row["stage"] != "committed" else row["stage"])
                status = "rejected" if result.type == "reject_goal" else ACTIVE
                if result.type == "maintain_goal" and row["stage"] != "committed":
                    raise HTTPException(422, "只有长期目标可以维持承诺")
                try:
                    db.execute("""UPDATE volition_items SET title=?,title_key=?,description=?,source=?,related_drives_json=?,stage=?,status=?,reason_summary=?,self_note=?,version=version+1,updated_at=? WHERE id=?""",
                               (title, normalize_title(title), proposed.description if proposed and "description" in provided else row["description"],
                                proposed.source if proposed and "source" in provided else row["source"],
                                json.dumps(proposed.relatedDriveIds if proposed and "relatedDriveIds" in provided else json.loads(row["related_drives_json"])), stage, status,
                                result.reasonSummary, result.selfNote, now(), row["id"]))
                except Exception as exc:
                    if "UNIQUE constraint failed" in str(exc):
                        raise HTTPException(409, "已有同名的活动意愿") from exc
                    raise
                changed = item_dict(item(db, character_id, row["id"]))
                put_history(db, character_id, before, changed, payload.requestId, "character")
            elif result.proposedItem:
                proposed = result.proposedItem
                key = normalize_title(proposed.title)
                if db.execute("SELECT 1 FROM volition_items WHERE character_id=? AND title_key=? AND status='active'", (character_id, key)).fetchone():
                    raise HTTPException(409, "已有同名的活动意愿")
                item_id = str(uuid.uuid4())
                db.execute("INSERT INTO volition_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (item_id, character_id, 2, proposed.title.strip(), key, proposed.description, proposed.source,
                            json.dumps(proposed.relatedDriveIds), proposed.stage, ACTIVE, result.reasonSummary, result.selfNote, 1, now(), now()))
                put_history(db, character_id, None, item_dict(item(db, character_id, item_id)), payload.requestId, "character")
            decision_id = str(uuid.uuid4())
            db.execute("INSERT INTO volition_decisions VALUES(?,?,?,?,?,?,?,?,?)",
                       (decision_id, character_id, 2, payload.requestId, payload.modelProfileId, report["id"],
                        payload.observationId, result.model_dump_json(), now()))
            try:
                app.state.psychology_service.record_reflections(db, character_id, f"decision:{decision_id}", result.reflections)
            except (ValueError, ValidationError) as exc:
                raise HTTPException(502, f"反思性倾向无效，意愿未保存：{str(exc)[:150]}") from exc
            app.state.psychology_service.record_decision(db, character_id, f"volition:{decision_id}", result.type)
            row = db.execute("SELECT * FROM volition_decisions WHERE id=?", (decision_id,)).fetchone()
            return {**dict(row), "result": result.model_dump()}

    @router.post("/{character_id}/intentions/{item_id}/transition")
    def transition(character_id: int, item_id: str, payload: TransitionInput):
        with connect() as db:
            require(db, character_id)
            sync_legacy(db, character_id, now())
            prior = db.execute("SELECT after_json FROM volition_item_history WHERE character_id=? AND request_id=?", (character_id, payload.requestId)).fetchone()
            if prior:
                saved = json.loads(prior[0])
                if saved["id"] != item_id:
                    raise HTTPException(409, "请求 ID 已用于其他意愿")
                return saved
            row = item(db, character_id, item_id)
            if row["version"] != payload.expectedVersion:
                raise HTTPException(409, "意愿版本已变化")
            before = item_dict(row)
            if payload.action == "reconsider":
                if row["status"] not in ("archived", "rejected"):
                    raise HTTPException(409, "只有归档或放弃的意愿可重新考虑")
                stage, status = "candidate", ACTIVE
            else:
                if row["status"] != ACTIVE:
                    raise HTTPException(409, "当前意愿状态不能执行此操作")
                stage = row["stage"]
                status = {"archive": "archived", "abandon": "rejected", "complete": "completed"}[payload.action]
            db.execute("UPDATE volition_items SET stage=?,status=?,reason_summary=?,version=version+1,updated_at=? WHERE id=?",
                       (stage, status, payload.reasonSummary or row["reason_summary"], now(), item_id))
            after = item_dict(item(db, character_id, item_id))
            put_history(db, character_id, before, after, payload.requestId, "user")
            return after

    app.include_router(router)
