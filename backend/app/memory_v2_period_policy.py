"""Bounded standing consent for Chronicle period summaries. Amounts are micro-USD."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

POLICY = 'chronicle-period-clock-v2'
FILE = 'chronicle-period-authority.json'
SCOPE = 'published-checked-title-synopsis-dates'
ZONE = timezone(timedelta(hours=8))


def read(path):
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    tmp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def valid(g, now):
    limits = [g.get(k) for k in ('perTask', 'perDay', 'total')]
    if (g.get('policy') != POLICY or g.get('scope') != SCOPE or not g.get('approvalId')
            or not all(type(limit) is int and limit > 0 for limit in limits)
            or not limits[0] <= limits[1] <= limits[2]):
        return False
    try:
        start = datetime.fromisoformat(g['startsAt'])
        end = datetime.fromisoformat(g['endsAt'])
        return start <= now < end
    except (KeyError, TypeError, ValueError):
        return False


def permits(g, members):
    return g.get('futurePublished') is True or all(
        identity[0] in g.get('entryIds', []) for identity, _ in members)


def balance(db, cid, g, day):
    if not g.get('approvalId'):
        return {'spent': 0, 'reserved': 0, 'today': 0}
    row = db.execute("""SELECT coalesce(sum(b.spent_estimate_microusd),0),
        coalesce(sum(b.reserved_microusd),0),
        coalesce(sum(CASE WHEN json_extract(j.read_set_json,'$.budgetDay')=?
        THEN b.spent_estimate_microusd+b.reserved_microusd ELSE 0 END),0)
        FROM mv3_jobs j JOIN mv3_trial_budgets b ON b.id=json_extract(j.read_set_json,'$.realConfig.trialId')
        WHERE j.character_id=? AND json_extract(j.read_set_json,'$.approvalId')=?""",
        (day, cid, g['approvalId'])).fetchone()
    return dict(zip(('spent', 'reserved', 'today'), row))


def decision(db, cid, grant, day, reserve):
    used = balance(db, cid, grant, day)
    if used['spent'] + used['reserved'] + reserve > grant['total']:
        return 'total_budget'
    if used['today'] + reserve > grant['perDay']:
        return 'daily_budget'
    return None


def authority_for(db):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    try:
        value=read(Path(path).parent / FILE) if path else {}
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}
