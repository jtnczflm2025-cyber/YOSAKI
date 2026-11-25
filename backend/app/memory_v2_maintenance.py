"""Isolated A3.7 maintenance facade. Authority stays with existing field, job and index services."""
import base64
import binascii
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from . import memory_v2_jobs as jobs
from . import memory_v2_lexical as lexical
from . import memory_v2_lexical_schema as lexical_schema
from . import memory_v2_metadata as metadata
from . import memory_v2_processing as processing
from . import memory_v2_processing_schema as processing_schema
from . import memory_v2_temporal_digest as temporal
from . import memory_v2_temporal_digest_schema as temporal_schema

VERSION = 'a37-maintenance-v2'
MAX_JOBS = 50
MAX_DIGEST_NODES = 100
MAX_TOKEN = 2048
_LOCAL = {
    'local_categories_v1': (processing.cancel_category_job, processing.recover_category_job),
    'local_tags_v1': (processing.cancel_tag_job, processing.recover_tag_job),
    'local_keywords_v1': (processing.cancel_keyword_job, processing.recover_keyword_job),
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _key(secret):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError('maintenance_signing_key_required')
    return hmac.new(secret, VERSION.encode(), hashlib.sha256).digest()


def _sign(payload, secret):
    body = _json(payload).encode('utf-8')
    mac = hmac.new(_key(secret), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + mac).decode('ascii').rstrip('=')


def _request_key(plan_token, secret):
    """A stable key for exactly one signed action plan, including its scope."""
    return hmac.new(_key(secret), b'a37-plan-request-key-v1\0' +
                    plan_token.encode('ascii'), hashlib.sha256).hexdigest()


def _offer(payload, secret):
    token = _sign(payload, secret)
    return {'planToken': token, 'requestKey': _request_key(token, secret)}


def _decode(token, secret):
    if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN:
        raise ValueError('maintenance_plan_invalid')
    try:
        raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        body, mac = raw[:-32], raw[-32:]
        if len(raw) <= 32 or not hmac.compare_digest(mac, hmac.new(_key(secret), body, hashlib.sha256).digest()):
            raise ValueError()
        payload = json.loads(body)
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ValueError('maintenance_plan_invalid') from None
    if not isinstance(payload, dict) or payload.get('version') != VERSION:
        raise ValueError('maintenance_plan_invalid')
    return payload


def _schema(db):
    if not processing_schema.installed(db):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'mv3_a3_%' LIMIT 1").fetchone():
            raise ValueError('a3_schema_invalid')
        return 'absent'
    required = {
        'mv3_jobs': {'contract_version', 'scope_key', 'cancel_intent', 'read_set_json', 'write_set_json'},
        'mv3_model_chunks': {'state', 'dispatch_attempt_id', 'request_id'},
        'mv3_a3_audit': {'job_id', 'event', 'evidence_hash'},
        'mv3_a3_migration_holds': {'job_id', 'reason'},
    }
    for table, columns in required.items():
        actual = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        if not columns <= actual:
            raise ValueError('a3_schema_invalid')
    guards = {'mv3_a3_hold_guard', 'mv3_a3_child_guard', 'mv3_a3_parent_guard',
              'ix_mv3_a3_scope'}
    present = {row['name'] for row in db.execute(
        'SELECT name FROM sqlite_master WHERE name IN (?,?,?,?)', tuple(sorted(guards)))}
    if present != guards:
        raise ValueError('a3_schema_invalid')
    return 'valid'


def _has_role_blocker(db, character_id):
    if _schema(db) != 'valid':
        return False
    return bool(db.execute("""SELECT 1 FROM mv3_jobs j
        LEFT JOIN mv3_a3_migration_holds h ON h.job_id=j.id
        WHERE j.character_id=? AND (j.status='unknown' OR h.job_id IS NOT NULL) LIMIT 1""",
        (character_id,)).fetchone())


def _rebuild_guard(db, character_id):
    if _has_role_blocker(db, character_id):
        raise ValueError('maintenance_blocked_unknown')


def _index_health(db, character_id, owner_id):
    state = lexical_schema.state(db)
    if state == 'invalid':
        raise ValueError('a33_schema_invalid')
    if state == 'absent':
        return {'state': 'schema_missing'}
    deadline = time.monotonic() + lexical.MAX_OPERATION_SECONDS
    authority, details, vocabulary = lexical._authority(db, character_id, deadline)
    rows = lexical._projection_rows(lexical._projection(details, vocabulary, deadline))
    lexical._budget(deadline)
    projection_hash = lexical._hash(rows)
    active = db.execute('SELECT generation,revision,authority_hash,projection_hash,config_version '
                        'FROM mv3_lexical_state WHERE character_id=?', (character_id,)).fetchone()
    prior = list(active) if active else None
    if not active:
        orphan = db.execute('SELECT 1 FROM mv3_lexical_terms WHERE character_id=? LIMIT 1',
                            (character_id,)).fetchone()
        index_state = 'orphaned' if orphan else 'missing'
    elif active['authority_hash'] != authority or active['config_version'] != lexical.CONFIG_VERSION:
        index_state = 'stale'
    elif active['projection_hash'] != projection_hash:
        index_state = 'invalid'
    else:
        actual = [tuple(row) for row in db.execute('''SELECT entry_id,field_name,field_id,
            field_token,value_ordinal,normalized_term,match_kind,vocabulary_id,evidence_hash
            FROM mv3_lexical_terms WHERE character_id=? AND generation=?
            ORDER BY entry_id,field_name,field_id,value_ordinal,normalized_term,match_kind,vocabulary_id
            LIMIT ?''', (character_id, active['generation'], lexical.MAX_TERMS + 1))]
        index_state = 'valid' if len(actual) <= lexical.MAX_TERMS and lexical._hash(actual) == projection_hash else 'invalid'
    return {'state': index_state, 'generation': active['generation'] if active else None,
            'revision': active['revision'] if active else None,
            'authorityHash': authority, 'projectionHash': projection_hash,
            'configVersion': lexical.CONFIG_VERSION, '_prior': prior}


def _job_dependency(db, job, payload):
    if job['kind'] != 'A3_PROCESSING':
        return 'legacy'
    kind = payload.get('executionKind')
    target = payload.get('targetId')
    prior = payload.get('readSet')
    try:
        if kind in _LOCAL and payload.get('targetKind') == 'entry':
            snapshot = {
                'local_categories_v1': processing._category_snapshot,
                'local_tags_v1': processing._tag_snapshot,
                'local_keywords_v1': processing._keyword_snapshot,
            }[kind](db, target, job['character_id'])
            return 'current' if snapshot == prior else 'changed'
        if kind == 'mock_synopsis_v1':
            from . import memory_v2_synopsis as synopsis
            return 'current' if synopsis._same_inputs(db, payload, job['character_id'])[0] else 'changed'
        if kind == 'mock_narrative_v1':
            from . import memory_v2_narrative_processing as narrative
            return 'current' if narrative._same_inputs(db, payload, job['character_id'], job['id'])[0] else 'changed'
        if kind == 'mock_temporal_digest_v1':
            return 'current' if temporal._same_inputs(db, payload, job['character_id'])[0] else 'changed'
    except (ValueError, KeyError, TypeError):
        return 'changed'
    return 'unsupported'

def _job_state(db, job):
    try:
        payload = json.loads(job['payload_json'])
    except (ValueError, TypeError):
        payload = {}
    chunks = [list(row) for row in db.execute('''SELECT ordinal,state,request_id,dispatch_attempt_id,
        reserved_tokens,reserved_cost_units,used_input_tokens,used_output_tokens,used_cost_units
        FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal''', (job['id'],))]
    hold = jobs.unsettled_kind(db, job)
    dependency = _job_dependency(db, job, payload)
    snapshot = [job['id'], job['character_id'], job['kind'], job['status'], job['attempts'],
                job['lease_token'], job['lease_until'], job['cancel_intent'],
                job['result_version_id'], job['input_hash'], job['scope_key'],
                job['read_set_json'], job['write_set_json'], chunks, hold, dependency]
    return {'jobId': job['id'], 'status': job['status'], 'cancelRequested': bool(job['cancel_intent']),
            'executionKind': payload.get('executionKind') if job['kind'] == 'A3_PROCESSING' else None,
            'targetId': payload.get('targetId') if job['kind'] == 'A3_PROCESSING' else None,
            'blockReason': hold, 'dependencyState': dependency, '_token': _hash(snapshot)}


def _jobs(db, character_id):
    if _schema(db) == 'absent':
        return []
    rows = db.execute('''SELECT * FROM mv3_jobs WHERE character_id=?
        ORDER BY created_at DESC,id DESC LIMIT ?''', (character_id, MAX_JOBS + 1)).fetchall()
    if len(rows) > MAX_JOBS:
        raise ValueError('maintenance_resource_limit')
    return [_job_state(db, row) for row in rows]


def _snapshot(db, character_id, owner_id, *, include_plan, secret, now, offset_minutes):
    lexical._authorize(db, character_id, owner_id)
    a3 = _schema(db)
    index = _index_health(db, character_id, owner_id)
    digest = []
    if temporal_schema.installed(db):
        digest = temporal.maintenance_plan(db, character_id, now=now, offset_minutes=offset_minutes)
        if len(digest) > MAX_DIGEST_NODES:
            raise ValueError('maintenance_resource_limit')
    blocked = _has_role_blocker(db, character_id)
    result = {'version': VERSION, 'characterId': character_id, 'processingSchema': a3,
              'lexical': {k: v for k, v in index.items() if not k.startswith('_')},
              'temporalSchema': 'valid' if temporal_schema.installed(db) else 'absent',
              'jobs': _jobs(db, character_id), 'digestProposals': digest if include_plan else [],
              'maintenanceBlockReason': 'unknown_or_hold' if blocked else None}
    if include_plan:
        if index['state'] in ('missing', 'stale', 'invalid') and not result.get('maintenanceBlockReason'):
            offer = _offer({
                'version': VERSION, 'action': 'lexical_rebuild', 'characterId': character_id,
                'ownerId': owner_id, 'authority': index['authorityHash'],
                'projection': index['projectionHash'], 'prior': index['_prior']}, secret)
            result['lexical']['actionToken'] = offer['planToken']
            result['lexical']['requestKey'] = offer['requestKey']
        for item in result['jobs']:
            kind = item['executionKind']
            actions = {}
            if kind in (*_LOCAL, 'mock_synopsis_v1', 'mock_narrative_v1', 'mock_temporal_digest_v1'):
                if item['status'] in ('queued', 'paused', 'running', 'unknown'):
                    actions['cancel'] = _offer({
                        'version': VERSION, 'action': 'cancel', 'characterId': character_id,
                        'ownerId': owner_id, 'jobId': item['jobId'], 'state': item['_token']}, secret)
                if (kind in _LOCAL and item['status'] == 'running' and not item['blockReason']
                        and item['dependencyState'] == 'current'
                        and db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?',
                                       (item['jobId'],)).fetchone() is None):
                    actions['recover_local'] = _offer({
                        'version': VERSION, 'action': 'recover_local', 'characterId': character_id,
                        'ownerId': owner_id, 'jobId': item['jobId'], 'state': item['_token']}, secret)
            item['actions'] = actions
    for item in result['jobs']:
        item.pop('_token')
    return result


def plan(connect, character_id, owner_id, *, signing_secret, now=None, offset_minutes=480):
    _key(signing_secret)
    moment = now or datetime.now(timezone.utc)
    with connect() as db:
        db.execute('BEGIN')
        return _snapshot(db, character_id, owner_id, include_plan=True, secret=signing_secret,
                         now=moment, offset_minutes=offset_minutes)


def status(connect, character_id, owner_id, *, now=None, offset_minutes=480):
    moment = now or datetime.now(timezone.utc)
    with connect() as db:
        db.execute('BEGIN')
        return _snapshot(db, character_id, owner_id, include_plan=False, secret=None,
                         now=moment, offset_minutes=offset_minutes)


def act(connect, character_id, owner_id, *, action, target_id, plan_token, request_key,
        signing_secret):
    if action not in ('lexical_rebuild', 'cancel', 'recover_local'):
        raise ValueError('maintenance_action_invalid')
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
        raise ValueError('maintenance_request_key_invalid')
    with connect() as db:
        db.execute('BEGIN')
        lexical._authorize(db, character_id, owner_id)
    proof = _decode(plan_token, signing_secret)
    if (proof.get('action') != action or proof.get('characterId') != character_id
            or proof.get('ownerId') != owner_id):
        raise ValueError('maintenance_plan_invalid')
    if not hmac.compare_digest(request_key, _request_key(plan_token, signing_secret)):
        raise ValueError('maintenance_request_key_invalid')
    if action == 'lexical_rebuild':
        if target_id is not None or 'jobId' in proof:
            raise ValueError('maintenance_target_invalid')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            lexical._authorize(db, character_id, owner_id)
            if _has_role_blocker(db, character_id):
                raise ValueError('maintenance_blocked_unknown')
            current = _index_health(db, character_id, owner_id)
            if current['authorityHash'] != proof.get('authority') or current['projectionHash'] != proof.get('projection'):
                raise ValueError('maintenance_plan_stale')
            if current['state'] == 'valid':
                return {'action': action, 'reused': True, 'generation': current['generation']}
            if current['state'] not in ('missing', 'stale', 'invalid') or current['_prior'] != proof.get('prior'):
                raise ValueError('maintenance_plan_stale')
        result = lexical.rebuild(connect, character_id, owner_id,
                                 expected_authority=proof['authority'],
                                 expected_projection=proof['projection'],
                                 expected_state=proof['prior'], reuse_if_current=True,
                                 publish_guard=_rebuild_guard)
        return {'action': action, **result}
    if not isinstance(target_id, str) or target_id != proof.get('jobId'):
        raise ValueError('maintenance_target_invalid')
    event = 'maintenance_' + action
    action_hash = _hash([VERSION, character_id, owner_id, target_id, action, plan_token, request_key])
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        lexical._authorize(db, character_id, owner_id)
        if _schema(db) != 'valid':
            raise ValueError('a3_migration_required')
        job = jobs.owned(db, target_id, character_id)
        if db.execute('SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event=? AND evidence_hash=?',
                      (target_id, event, action_hash)).fetchone():
            return {'action': action, 'status': job['status'], 'reused': True}
        current = _job_state(db, job)
        if current['_token'] != proof.get('state'):
            raise ValueError('maintenance_plan_stale')
        kind = current['executionKind']
        if kind in _LOCAL:
            cancel_fn, recover_fn = _LOCAL[kind]
        elif kind == 'mock_synopsis_v1':
            from . import memory_v2_synopsis as synopsis
            cancel_fn, recover_fn = synopsis.cancel_job, None
        elif kind == 'mock_narrative_v1':
            from . import memory_v2_narrative_processing as narrative
            cancel_fn, recover_fn = narrative.cancel_job, None
        elif kind == 'mock_temporal_digest_v1':
            cancel_fn, recover_fn = temporal.cancel_job, None
        else:
            raise ValueError('maintenance_action_not_supported')
        if action == 'cancel':
            state = cancel_fn(db, target_id, character_id, owner_id)
        else:
            if recover_fn is None or current['blockReason'] or current['dependencyState'] != 'current':
                raise ValueError('local_recovery_not_proven')
            if db.execute('SELECT 1 FROM mv3_model_chunks WHERE job_id=?',
                          (target_id,)).fetchone():
                raise ValueError('local_recovery_not_proven')
            state = recover_fn(db, target_id, character_id, owner_id)
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,?,?,?,datetime('now'))''', (target_id, event, action_hash, job['status']))
        return {'action': action, 'status': state, 'reused': False}
