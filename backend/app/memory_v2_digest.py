"""Chronicle time projections; extractive digests never infer inner experience."""
from datetime import datetime, timedelta, timezone
import re
from . import memory_v2_derivation as core

BOUNDARIES = (7, 30, 90, 180, 365)
RULE = 'digest-extractive-v1'


def parse_time(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo is not None else None
    except (AttributeError, ValueError):
        return None


def projection(source, now, offset_minutes=480):
    if now.tzinfo is None or not -840 <= offset_minutes <= 840:
        raise ValueError('invalid_projection_time')
    occurred = parse_time(source.occurred_at)
    # recorded_at documents storage, not event occurrence.  Keep its provenance
    # visible without assigning an age bucket to an unverified event.
    timestamp = occurred
    basis = 'occurred_at' if occurred else 'recorded_at' if parse_time(source.recorded_at) else 'unknown'
    if timestamp is None:
        return {'level': None, 'period': None, 'timeBasis': basis, 'sourceId': source.source_id}
    age = max(0, (now - timestamp).total_seconds() / 86400)
    level = next((i for i, b in enumerate(BOUNDARIES) if age < b), 5)
    day = timestamp.astimezone(timezone(timedelta(minutes=offset_minutes)))
    iso = day.isocalendar()
    periods = (day.strftime('%Y-%m-%d'), f'{iso.year}-W{iso.week:02}', day.strftime('%Y-%m'),
               f'{day.year}-Q{(day.month - 1) // 3 + 1}', f'{day.year}-H{(day.month - 1) // 6 + 1}', str(day.year))
    return {'level': f'L{level}', 'period': periods[level], 'timeBasis': basis,
            'sourceId': source.source_id, 'futureTimestamp': timestamp > now}


def plan(db, character_id, refs, now, offset_minutes=480):
    core.source_inputs(db, character_id, refs)
    groups = {}
    for ref in refs:
        source = core.live_source(db, character_id, ref['kind'], str(ref['id']))
        item = projection(source, now, offset_minutes)
        # Unknown/event/record times never silently merge into the same time bucket.
        key = (item['level'], item['period'], item['timeBasis'])
        groups.setdefault(key, []).append({'kind': source.kind, 'id': source.source_id})
    return [{'level': key[0], 'period': key[1], 'timeBasis': key[2], 'sourceRefs': members,
             'projectionOnly': key[0] in (None, 'L0', 'L5'),
             'fingerprint': core._hash([key, core.source_inputs(db, character_id, members), offset_minutes])}
            for key, members in groups.items()]


def create_digest(db, character_id, refs, *, level, period, child_version_ids=(), max_chars=200000):
    patterns = {'L1': r'\d{4}-W(0[1-9]|[1-4]\d|5[0-3])', 'L2': r'\d{4}-(0[1-9]|1[0-2])',
                'L3': r'\d{4}-Q[1-4]', 'L4': r'\d{4}-H[12]'}
    if level not in patterns or not isinstance(period, str) or not re.fullmatch(patterns[level], period):
        raise ValueError('invalid_digest_period')
    if not isinstance(max_chars, int) or not 1 <= max_chars <= 200000:
        raise ValueError('invalid_digest_budget')
    core.source_inputs(db, character_id, refs)
    parts = [f'摘录式摘要 {level} / {period}（系统派生；未作语义概括）']
    mapping = []
    for ordinal, ref in enumerate(refs):
        source = core.live_source(db, character_id, ref['kind'], str(ref['id']))
        prefix = f'[{source.kind}:{source.source_id} | {source.role} | {source.status}]「'
        offset = len('\n\n'.join(parts)) + 2 + len(prefix)
        parts.append(prefix + source.text + '」')
        mapping.append({'ordinal': ordinal, 'sourceKind': source.kind, 'sourceId': source.source_id,
            'rawStart': 0, 'rawEnd': len(source.text), 'outputStart': offset,
            'outputEnd': offset + len(source.text), 'contentHash': source.content_hash})
    body = '\n\n'.join(parts)
    if len(body) > max_chars:
        raise ValueError('digest_budget_exceeded')
    item = core.create_derivation(db, character_id, 'DIGEST', refs)
    return core.add_version(db, item['id'], refs, body, author_kind='rule',
        rule_version=f'{RULE}:{level}:{period}', child_version_ids=child_version_ids, mapping=mapping)


def unfold(db, version_id, character_id):
    """Every node revalidates before returning a body or original source."""
    version = core.read_version(db, version_id, character_id)
    return {'versionId': version_id, 'body': version['body'], 'authority': 'system_derived_text',
        'children': [unfold(db, d['child_version_id'], character_id) for d in core._deps(db, version_id)],
        'sources': [{'ref': ref, 'text': core.live_source(db, character_id, ref['source_kind'], ref['source_id']).text}
                    for ref in core._refs(db, version_id)]}
