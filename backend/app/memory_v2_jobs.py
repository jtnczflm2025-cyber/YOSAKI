"""Explicit local derivation jobs. No scheduler, network, paid provider or auto-retry."""
import json
import time
import uuid
import sqlite3
from dataclasses import dataclass
from weakref import WeakValueDictionary, WeakKeyDictionary
from . import memory_v2_derivation as core
from . import memory_v2_cleaning, memory_v2_narrative, memory_v2_digest
from . import memory_v2_model_adapter as model_adapter, memory_v2_fidelity as fidelity


def init_db(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_jobs (
        id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, input_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','paused','completed','failed','cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
        result_version_id TEXT REFERENCES mv3_versions(id) ON DELETE SET NULL,
        error_code TEXT, created_at TEXT NOT NULL,
        UNIQUE(character_id,kind,input_hash))''')
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_model_chunks (
        job_id TEXT NOT NULL REFERENCES mv3_jobs(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, request_id TEXT NOT NULL UNIQUE,
        input_hash TEXT NOT NULL, input_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('prepared','possibly_dispatched','completed','rejected','unknown')),
        reserved_tokens INTEGER NOT NULL, used_input_tokens INTEGER, used_output_tokens INTEGER,
        reserved_cost_units INTEGER NOT NULL DEFAULT 0, used_cost_units INTEGER,
        output_json TEXT, warning_json TEXT, PRIMARY KEY(job_id,ordinal))''')
    columns={r[1] for r in db.execute('PRAGMA table_info(mv3_model_chunks)')}
    for name in ('reserved_cost_units','used_cost_units'):
        if name not in columns:
            db.execute(f'ALTER TABLE mv3_model_chunks ADD COLUMN {name} INTEGER')


def snapshot(db, character_id, payload):
    sources = core.source_inputs(db, character_id, payload['refs'])
    children = []
    for version_id in payload.get('children', []):
        child = core.read_version(db, version_id, character_id)
        children.append((version_id, child['output_hash'], child['validation_binding']))
    if 'target_entry' in payload:
        from . import memory_v2_metadata_jobs
        return core._hash([sources, children, payload, 'metadata-jobs-v1',
                          memory_v2_metadata_jobs.input_state(db, character_id, payload)])
    return core._hash([sources, children, payload, 'local-jobs-v1'])


def enqueue(db, character_id, kind, refs, **parameters):
    if kind not in ('CLEANED', 'NARRATIVE', 'DIGEST'):
        raise ValueError('unsupported_local_job')
    allowed = {'translation', 'line_endings'} if kind == 'CLEANED' else {'level', 'period', 'children', 'max_chars'} if kind == 'DIGEST' else set()
    if kind in ('NARRATIVE', 'DIGEST'):
        allowed |= {'model_mode', 'input_cap', 'output_cap', 'total_token_cap', 'max_cost_units', 'target_chars'}
        allowed |= {'trial_id', 'prompt_variant', 'prompt_version', 'provider', 'model', 'price_snapshot'}
    if set(parameters) - allowed:
        raise ValueError('unsupported_job_parameter')
    if parameters.get('model_mode') is not True and any(k in parameters for k in ('input_cap','output_cap','total_token_cap','max_cost_units','target_chars')):
        raise ValueError('model_budget_without_mode')
    payload = {'refs': refs, **parameters}
    fingerprint = snapshot(db, character_id, payload)
    prior = db.execute('SELECT * FROM mv3_jobs WHERE character_id=? AND kind=? AND input_hash=?',
                       (character_id, kind, fingerprint)).fetchone()
    if prior:
        return dict(prior)
    job_id = str(uuid.uuid4())
    db.execute('''INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at)
                  VALUES(?,?,?,?,?,'queued',?)''',
               (job_id, character_id, kind, fingerprint, json.dumps(payload, ensure_ascii=False), core._now()))
    return core._row(db, 'mv3_jobs', job_id)


def owned(db, job_id, character_id):
    job = core._row(db, 'mv3_jobs', job_id)
    if job is None or job['character_id'] != character_id or not db.execute(
            'SELECT 1 FROM characters WHERE id=? AND enabled=1', (character_id,)).fetchone():
        raise ValueError('job_not_accessible')
    return job


def _a3_installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv3_a3_audit'").fetchone())


def unsettled_kind(db, job, *, trial_id=None):
    """Call after scope authorization; never infer a Provider request from a hold."""
    job=owned(db,job['id'],job['character_id'])
    if trial_id is None:
        pending=db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')",(job['id'],)).fetchone()
    else:
        pending=db.execute("SELECT 1 FROM mv3_model_chunks c JOIN mv3_jobs j ON j.id=c.job_id WHERE c.trial_id=? AND j.character_id=? AND c.state IN ('possibly_dispatched','unknown')",(trial_id,job['character_id'])).fetchone()
    if pending: return 'request'
    if _a3_installed(db) and db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_a3_migration_holds'").fetchone():
        if db.execute('SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=?',(job['id'],)).fetchone():
            return 'migration_hold'
    return 'unresolved_parent' if job['status']=='unknown' else None


def require_dispatch_settled(db, job, *, request_error='model_result_unknown', trial_id=None):
    reason=unsettled_kind(db,job,trial_id=trial_id)
    if reason=='request': raise ValueError(request_error)
    if reason: raise ValueError('legacy_reconciliation_required')


def require_settled_for_resume(db, job):
    # Preserve the established resume contract, including conservative legacy holds.
    if unsettled_kind(db,job): raise ValueError('model_result_unknown')


@dataclass(frozen=True, eq=False, repr=False)
class DispatchProof:
    """Process-local sender capability; never serialized or reconstructed from IDs."""
    attempt_id: str
    request_id: str
    job_id: str
    character_id: int
    ordinal: int
    database: object
    input_hash: str
    owner_id: int | None = None


_issued_dispatches = WeakValueDictionary()
_legacy_settlements = WeakKeyDictionary()


def _valid_request_identity(value):
    # Existing dispatchers generate canonical UUIDs. Do not normalize invalid IDs.
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _database_identity(db):
    path = db.execute('PRAGMA database_list').fetchone()[2]
    return path if path else db


def _dispatch_owner_hash(owner_id, job_id, character_id, ordinal, request_id,
                         attempt_id, input_hash):
    return core._hash(['a3-dispatch-owner-v1', owner_id, job_id, character_id,
                       ordinal, request_id, attempt_id, input_hash])


_OWNER_BOUND_EXECUTIONS = ('responses_synopsis_v1', 'chronicle_embedding_v1',
                           'chronicle_semantic_supplement_v1', 'responses_temporal_digest_v1')


def persist_dispatch_identity(db, job_id, character_id, ordinal, *, original_owner_id=None):
    """Trusted dispatch coordinator only, inside its authorized write transaction."""
    row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',
                   (job_id,ordinal)).fetchone()
    if row is None or row['state']!='prepared' or not _valid_request_identity(row['request_id']):
        raise ValueError('dispatch_identity_mismatch')
    owner_id = None
    if _a3_installed(db):
        job = core._row(db, 'mv3_jobs', job_id)
        if job is None or job['character_id'] != character_id:
            raise ValueError('dispatch_identity_mismatch')
        payload = json.loads(job['payload_json']) if job['kind'] == 'A3_PROCESSING' else {}
        if payload.get('executionKind') in _OWNER_BOUND_EXECUTIONS:
            bound_owner = payload.get('readSet', {}).get('realConfig', {}).get('ownerId')
            if (type(original_owner_id) is not int or bound_owner != original_owner_id
                    or not db.execute('SELECT 1 FROM users WHERE id=?',
                                      (original_owner_id,)).fetchone()):
                raise ValueError('dispatch_owner_binding_required')
            owner_id = original_owner_id
        elif original_owner_id is not None:
            raise ValueError('dispatch_owner_binding_not_supported')
    elif original_owner_id is not None:
        raise ValueError('a3_migration_required')
    attempt=str(uuid.uuid4())
    proof=DispatchProof(attempt,row['request_id'],job_id,character_id,ordinal,
                        _database_identity(db),row['input_hash'],owner_id)
    if _a3_installed(db):
        db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?",(job_id,))
        changed=db.execute("UPDATE mv3_model_chunks SET state='possibly_dispatched',dispatch_attempt_id=? WHERE job_id=? AND ordinal=? AND state='prepared'",(attempt,job_id,ordinal)).rowcount
    else:
        changed=db.execute("UPDATE mv3_model_chunks SET state='possibly_dispatched' WHERE job_id=? AND ordinal=? AND state='prepared'",(job_id,ordinal)).rowcount
    if changed!=1: raise ValueError('dispatch_conflict')
    if owner_id is not None:
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,'dispatch_owner_binding_v1',?,?,?)''',
            (job_id, _dispatch_owner_hash(owner_id, job_id, character_id, ordinal,
                                          row['request_id'], attempt, row['input_hash']),
             'unknown', core._now()))
    _issued_dispatches[attempt]=proof
    return proof


def _identity_row(db, job_id, character_id, ordinal, request_id, proof):
    if (type(proof) is not DispatchProof or not _valid_request_identity(request_id)
            or not _valid_request_identity(proof.attempt_id)
            or _issued_dispatches.get(proof.attempt_id) is not proof
            or (proof.job_id,proof.character_id,proof.ordinal,proof.request_id,proof.database)
                != (job_id,character_id,ordinal,request_id,_database_identity(db))):
        raise ValueError('dispatch_identity_mismatch')
    row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=? AND request_id=?',
                   (job_id,ordinal,request_id)).fetchone()
    if row is None or row['input_hash']!=proof.input_hash:
        raise ValueError('dispatch_identity_mismatch')
    if _a3_installed(db) and (not _valid_request_identity(row['dispatch_attempt_id'])
                              or row['dispatch_attempt_id']!=proof.attempt_id):
        raise ValueError('dispatch_identity_mismatch')
    return row


def validate_dispatch_identity(db, job_id, character_id, ordinal, request_id, proof):
    owned(db,job_id,character_id)
    return _identity_row(db,job_id,character_id,ordinal,request_id,proof)


def _revoked_settlement_job(db,job_id,character_id,ordinal,request_id,proof,owner_id):
    """Only an original in-process sender with a durable owner binding may settle."""
    if (not _a3_installed(db) or type(owner_id) is not int or
            db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',
                       (character_id,)).fetchone() or
            not db.execute('SELECT 1 FROM users WHERE id=?', (owner_id,)).fetchone()):
        raise ValueError('revoked_settlement_not_authorized')
    job=core._row(db,'mv3_jobs',job_id)
    if (job is None or job['character_id']!=character_id or
            job['contract_version'] is None or type(proof) is not DispatchProof or
            proof.owner_id!=owner_id):
        raise ValueError('revoked_settlement_not_authorized')
    payload = json.loads(job['payload_json'])
    if (payload.get('executionKind') not in _OWNER_BOUND_EXECUTIONS or
            payload.get('readSet', {}).get('realConfig', {}).get('ownerId') != owner_id):
        raise ValueError('revoked_settlement_not_authorized')
    row=_identity_row(db,job_id,character_id,ordinal,request_id,proof)
    evidence=_dispatch_owner_hash(owner_id,job_id,character_id,ordinal,request_id,
                                  proof.attempt_id,proof.input_hash)
    if not db.execute('''SELECT 1 FROM mv3_a3_audit WHERE job_id=?
        AND event='dispatch_owner_binding_v1' AND evidence_hash=?''',
        (job_id,evidence)).fetchone():
        raise ValueError('revoked_settlement_not_authorized')
    return job,row


def begin_dispatch(db, job_id, character_id, ordinal, *, expected_request_id, expected_input_hash):
    """Caller commits this transaction before invoking any adapter."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    job=owned(db,job_id,character_id)
    require_dispatch_settled(db,job,request_error='unsettled_request')
    if job['status']!='running' or job['lease_until'] is None or job['lease_until']<=time.time():
        raise ValueError('invalid_job_lease')
    if 'cancel_intent' in job.keys() and job['cancel_intent']:
        raise ValueError('cancel_requested')
    if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')",(job_id,)).fetchone():
        raise ValueError('unsettled_request')
    if _a3_installed(db) and db.execute("SELECT 1 FROM mv3_jobs WHERE character_id=? AND id!=? AND status='unknown'",(character_id,job_id)).fetchone():
        raise ValueError('semantic_scope_unknown')
    row=db.execute("SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=? AND state='prepared'",(job_id,ordinal)).fetchone()
    if row is None or row['request_id']!=expected_request_id or row['input_hash']!=expected_input_hash or row['reserved_tokens']<=0 or (row['reserved_cost_units'] or 0)<0:
        raise ValueError('request_reservation_invalid')
    if snapshot(db,character_id,json.loads(job['payload_json']))!=job['input_hash']:
        raise ValueError('job_input_changed')
    return persist_dispatch_identity(db,job_id,character_id,ordinal)


def recompute_parent(db, job_id, character_id, *, output_ready=False, error=None, result_version_id=None, safe_pause=False):
    """Only lifecycle coordinator chooses final status. Unknown outranks cancel."""
    job=owned(db,job_id,character_id)
    return _recompute_parent(db,job,output_ready=output_ready,error=error,
                             result_version_id=result_version_id,safe_pause=safe_pause)


def _recompute_parent(db,job,*,output_ready=False,error=None,result_version_id=None,safe_pause=False):
    job_id=job['id']
    states=[r[0] for r in db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',(job_id,))]
    held=bool(_a3_installed(db) and db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_a3_migration_holds'").fetchone()
              and db.execute('SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=?',(job_id,)).fetchone())
    if held or any(s in ('possibly_dispatched','unknown') for s in states):
        state='unknown' if _a3_installed(db) else 'paused'
    elif ('cancel_intent' in job.keys() and job['cancel_intent']) or job['status']=='cancelled':
        state='cancelled'
    elif error or 'rejected' in states:
        state='failed'
    elif output_ready:
        if any(s!='completed' for s in states): raise ValueError('batches_incomplete')
        state='completed'
    elif job['status']=='completed':
        state='completed'
    elif safe_pause:
        state='paused'
    else:
        state='running'
    db.execute('UPDATE mv3_jobs SET status=?,error_code=COALESCE(?,error_code) WHERE id=?',(state,error,job_id))
    if state in ('completed','failed','cancelled'):
        db.execute('UPDATE mv3_jobs SET lease_token=NULL,lease_until=NULL WHERE id=?',(job_id,))
    if state=='completed' and result_version_id:
        db.execute('UPDATE mv3_jobs SET result_version_id=? WHERE id=?',(result_version_id,job_id))
    return state


def business_result_allowed(db,job_id,character_id):
    return recompute_parent(db,job_id,character_id)=='running'


def settle_dispatch(db,job_id,character_id,ordinal,request_id,attempt_id,*,state,
                    used_input=None,used_output=None,used_cost=None,result_hash=None,
                    trial_id=None,usd_estimate=None,original_owner_id=None):
    """Settle a definite result in the caller's transaction; money ledger joins it."""
    if state not in ('completed','rejected','unknown'):
        raise ValueError('invalid_settlement')
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    if original_owner_id is None:
        job=owned(db,job_id,character_id)
        row=validate_dispatch_identity(db,job_id,character_id,ordinal,request_id,attempt_id)
    else:
        if state=='completed':
            raise ValueError('revoked_result_cannot_complete')
        job,row=_revoked_settlement_job(db,job_id,character_id,ordinal,request_id,
                                        attempt_id,original_owner_id)
    binding=core._hash([state,used_input,used_output,used_cost,result_hash,trial_id,usd_estimate])
    if row['state'] in ('completed','rejected'):
        if _a3_installed(db) and row['settlement_hash']==binding and row['state']==state:
            return job['status']
        if not _a3_installed(db) and _legacy_settlements.get(attempt_id)==binding and row['state']==state:
            return job['status']
        raise ValueError('conflicting_settlement')
    if row['state'] not in ('possibly_dispatched','unknown'):
        raise ValueError('request_not_dispatched')
    if state=='unknown':
        if row['state']=='unknown':
            return 'unknown'
        db.execute("UPDATE mv3_model_chunks SET state='unknown' WHERE job_id=? AND ordinal=?",(job_id,ordinal))
        if original_owner_id is not None:
            db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
                VALUES(?,'revoked_result_unknown_v1',?,?,?)''',
                (job_id,binding,job['status'],core._now()))
        # Legacy schema cannot store unknown. Preserve its paused parent while
        # retaining the durable child and the resume guard. No implicit migration.
        if not _a3_installed(db):
            db.execute("UPDATE mv3_jobs SET status='paused' WHERE id=?",(job_id,))
        return 'unknown'
    if type(used_input) is not int or type(used_output) is not int or min(used_input,used_output)<0:
        raise ValueError('usage_missing')
    if trial_id is not None:
        if row['trial_id']!=trial_id or type(usd_estimate) is not int or usd_estimate<0:
            raise ValueError('trial_settlement_invalid')
        changed=db.execute('''UPDATE mv3_trial_budgets SET reserved_microusd=reserved_microusd-?,
            spent_estimate_microusd=spent_estimate_microusd+?
            WHERE id=? AND character_id=? AND reserved_microusd>=?''',
            (row['reserved_usd_micros'],usd_estimate,trial_id,character_id,row['reserved_usd_micros'])).rowcount
        if changed!=1: raise ValueError('trial_reservation_invalid')
    if _a3_installed(db):
        db.execute('''UPDATE mv3_model_chunks SET state=?,used_input_tokens=?,used_output_tokens=?,
            used_cost_units=?,settlement_hash=? WHERE job_id=? AND ordinal=?''',
            (state,used_input,used_output,used_cost,binding,job_id,ordinal))
    else:
        db.execute('''UPDATE mv3_model_chunks SET state=?,used_input_tokens=?,used_output_tokens=?,
            used_cost_units=? WHERE job_id=? AND ordinal=?''',
            (state,used_input,used_output,used_cost,job_id,ordinal))
        _legacy_settlements[attempt_id]=binding
    if original_owner_id is not None:
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,'revoked_usage_settled_v1',?,?,?)''',
            (job_id,binding,job['status'],core._now()))
    return _recompute_parent(db,job)


def release_never_dispatched_trial_requests(db, job_id, character_id, trial_id, reason):
    """Terminally release only provably unsent prepared requests in one write tx.

    This is an internal coordinator operation, not Provider settlement. It
    preserves every usage/attempt field as NULL and never changes an unknown
    or possibly dispatched request. A concurrent dispatch uses the same
    SQLite writer lock and prepared-state compare-and-swap.
    """
    if not db.in_transaction:
        raise ValueError('prepared_release_transaction_required')
    if not _a3_installed(db):
        raise ValueError('a3_migration_required')
    job = core._row(db, 'mv3_jobs', job_id)
    trial = core._row(db, 'mv3_trial_budgets', trial_id)
    if (job is None or trial is None or job['character_id'] != character_id or
            trial['character_id'] != character_id or trial['status'] != 'stopped' or
            job['kind'] != 'A3_PROCESSING' or
            db.execute('SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=?',
                       (job_id,)).fetchone()):
        raise ValueError('prepared_release_not_authorized')
    try:
        payload = json.loads(job['payload_json'])
        config = payload['readSet']['realConfig']
        owner_id = config['ownerId']
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('prepared_release_not_authorized') from exc
    if (payload.get('executionKind') != 'responses_synopsis_v1' or
            config.get('trialId') != trial_id or type(owner_id) is not int or
            not db.execute('SELECT 1 FROM users WHERE id=?', (owner_id,)).fetchone() or
            not isinstance(reason, str) or not 0 < len(reason) <= 120):
        raise ValueError('prepared_release_not_authorized')
    released = 0
    for row in db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND trial_id=? '
                          'ORDER BY ordinal', (job_id, trial_id)).fetchall():
        if row['state'] != 'prepared':
            continue
        if (row['provider_status'] != 'prepared' or
                not _valid_request_identity(row['request_id']) or
                row['dispatch_attempt_id'] is not None or
                row['provider_response_id'] is not None or
                row['provider_usage_json'] is not None or
                row['settlement_hash'] is not None or
                row['output_json'] is not None or
                row['warning_json'] is not None or
                row['used_input_tokens'] is not None or
                row['used_output_tokens'] is not None or
                row['used_cost_units'] is not None or
                row['usage_estimated_usd_micros'] is not None or
                type(row['reserved_usd_micros']) is not int or
                row['reserved_usd_micros'] < 0):
            raise ValueError('prepared_release_identity_unproven')
        record = {'kind': 'prepared_request_released_v1',
                  'requestId': row['request_id'], 'ordinal': row['ordinal'],
                  'releasedMicrousd': row['reserved_usd_micros'],
                  'reason': reason}
        changed = db.execute('''UPDATE mv3_model_chunks
            SET state='rejected',provider_status='not_dispatched',warning_json=?
            WHERE job_id=? AND ordinal=? AND trial_id=? AND state='prepared'
            AND provider_status='prepared' AND dispatch_attempt_id IS NULL''',
            (json.dumps(record, ensure_ascii=False, sort_keys=True),
             job_id, row['ordinal'], trial_id)).rowcount
        if changed != 1:
            raise ValueError('prepared_release_dispatch_conflict')
        changed = db.execute('''UPDATE mv3_trial_budgets
            SET reserved_microusd=reserved_microusd-?
            WHERE id=? AND character_id=? AND reserved_microusd>=?''',
            (row['reserved_usd_micros'], trial_id, character_id,
             row['reserved_usd_micros'])).rowcount
        if changed != 1:
            raise ValueError('prepared_release_reservation_conflict')
        db.execute('''INSERT INTO mv3_a3_audit
            (job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,'prepared_request_released_v1',?,?,?)''',
            (job_id, core._hash([record, trial_id, character_id]),
             job['status'], core._now()))
        released += row['reserved_usd_micros']
    return released


def claim(db, job_id, character_id, *, now=None):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    job = owned(db, job_id, character_id)
    if 'contract_version' in job.keys() and job['contract_version']:
        raise ValueError('a3_executor_required')
    require_dispatch_settled(db,job)
    if job['status'] != 'queued' or job['attempts'] >= 3:
        raise ValueError('job_not_claimable')
    if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')", (job_id,)).fetchone():
        raise ValueError('model_result_unknown')
    if snapshot(db, character_id, json.loads(job['payload_json'])) != job['input_hash']:
        raise ValueError('job_input_changed')
    token = str(uuid.uuid4())
    now = time.time() if now is None else now
    changed = db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                         (token, now + 60, job_id)).rowcount
    if changed != 1:
        raise ValueError('claim_conflict')
    return token


def control(db, job_id, character_id, action, *, now=None):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    job = owned(db, job_id, character_id)
    if action=='resume':
        require_settled_for_resume(db,job)
    if 'contract_version' in job.keys() and job['contract_version']:
        from . import memory_v2_processing
        if action=='cancel': return memory_v2_processing.cancel(db,job_id,character_id)
        if action=='resume': return memory_v2_processing.resume(db,job_id,character_id,
            json.loads(job['read_set_json']))
        raise ValueError('a3_executor_required')
    now = time.time() if now is None else now
    if _a3_installed(db) and action=='cancel' and job['status']=='unknown':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(job_id,))
        return 'unknown'
    if action == 'pause' and job['status'] == 'queued':
        state = 'paused'
    elif action == 'recover' and job['status'] == 'running' and job['lease_until'] <= now:
        state = 'paused'
    elif action == 'resume' and job['status'] == 'paused' and job['attempts'] < 3:
        if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')", (job_id,)).fetchone():
            raise ValueError('model_result_unknown')
        if snapshot(db, character_id, json.loads(job['payload_json'])) != job['input_hash']:
            raise ValueError('job_input_changed')
        state = 'queued'
    elif action == 'cancel' and job['status'] in ('queued', 'paused', 'running'):
        state = 'cancelled'
    else:
        raise ValueError('invalid_job_transition')
    db.execute('UPDATE mv3_jobs SET status=?,lease_token=NULL,lease_until=NULL WHERE id=?', (state, job_id))
    return state


def execute_claimed(db, job_id, character_id, token, *, now=None):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    job = owned(db, job_id, character_id)
    now = time.time() if now is None else now
    if job['status'] != 'running' or job['lease_token'] != token or job['lease_until'] <= now:
        raise ValueError('invalid_job_lease')
    payload = json.loads(job['payload_json'])
    if payload.get('model_mode'):
        raise ValueError('model_requires_explicit_dispatch')
    db.execute('SAVEPOINT mv3_execution')
    try:
        if snapshot(db, character_id, payload) != job['input_hash']:
            raise ValueError('job_input_changed')
        if job['kind'] in ('METADATA', 'SYNOPSIS'):
            from . import memory_v2_metadata_jobs
            result = memory_v2_metadata_jobs.execute(db, job, payload)
            db.execute('RELEASE mv3_execution')
            db.execute("UPDATE mv3_jobs SET status='completed',lease_token=NULL,lease_until=NULL WHERE id=?", (job_id,))
            return {'status':'completed','fieldSetId':result['versionId']}
        refs = payload.pop('refs')
        if job['kind'] == 'CLEANED':
            version = memory_v2_cleaning.create_cleaned(db, character_id, refs, **payload)['version']
        elif job['kind'] == 'NARRATIVE':
            version = memory_v2_narrative.create_narrative(db, character_id, refs)
        else:
            children = payload.pop('children', [])
            version = memory_v2_digest.create_digest(db, character_id, refs, child_version_ids=children, **payload)
        db.execute('RELEASE mv3_execution')
    except ValueError as exc:
        db.execute('ROLLBACK TO mv3_execution')
        db.execute('RELEASE mv3_execution')
        db.execute("UPDATE mv3_jobs SET status='failed',error_code=?,lease_token=NULL,lease_until=NULL WHERE id=?", (str(exc), job_id))
        return {'status': 'failed', 'error': str(exc)}
    db.execute("UPDATE mv3_jobs SET status='completed',result_version_id=?,lease_token=NULL,lease_until=NULL WHERE id=?", (version['id'], job_id))
    return {'status': 'completed', 'versionId': version['id']}


def enqueue_model(db, character_id, kind, refs, *, input_cap=4096, output_cap=1024,
                  total_token_cap=12000, max_cost_units=None, target_chars=6000, level=None, period=None, children=()):
    if kind not in ('NARRATIVE', 'DIGEST') or not all(type(x) is int and x > 0 for x in
            (input_cap, output_cap, total_token_cap, target_chars)):
        raise ValueError('invalid_model_budget')
    if input_cap + output_cap > total_token_cap:
        raise ValueError('invalid_model_budget')
    if max_cost_units is None:
        max_cost_units=total_token_cap  # Synthetic rate: one budget unit per byte token.
    if type(max_cost_units) is not int or max_cost_units < 0:
        raise ValueError('invalid_model_cost_budget')
    args = {'model_mode': True, 'prompt_version': model_adapter.PROMPT_VERSION,
            'input_cap': input_cap, 'output_cap': output_cap,
            'total_token_cap': total_token_cap, 'max_cost_units':max_cost_units,
            'target_chars': target_chars}
    if kind == 'DIGEST':
        if level not in ('L1','L2','L3','L4') or not period:
            raise ValueError('invalid_digest_period')
        args.update(level=level, period=period, children=list(children))
    elif level or period or children:
        raise ValueError('narrative_has_no_digest_level')
    return enqueue(db, character_id, kind, refs, **args)


def _model_payload(job):
    payload = json.loads(job['payload_json'])
    if not payload.get('model_mode'):
        raise ValueError('not_model_job')
    return payload


def prepare_model(db, job_id, character_id, tokenizer):
    """Persist complete request identities and reservations before any dispatch."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    job = owned(db, job_id, character_id)
    payload = _model_payload(job)
    if payload.get('prompt_version') != model_adapter.PROMPT_VERSION:
        raise ValueError('model_prompt_version_changed')
    if job['status'] != 'queued' or snapshot(db, character_id, payload) != job['input_hash']:
        raise ValueError('job_not_preparable')
    old = db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?', (job_id,)).fetchone()[0]
    if old:
        return old
    sources = [core.live_source(db, character_id, ref['kind'], str(ref['id'])) for ref in payload['refs']]
    batches = model_adapter.batches(sources, tokenizer, kind=job['kind'], input_cap=payload['input_cap'],
                                    target_chars=payload['target_chars'])
    total_reserved = len(batches) * (payload['input_cap'] + payload['output_cap'])
    if total_reserved > payload['total_token_cap'] or total_reserved > payload['max_cost_units']:
        raise ValueError('model_budget_exceeded')
    for ordinal, items in enumerate(batches):
        encoded = json.dumps(items, ensure_ascii=False, separators=(',', ':'))
        references = [{k:v for k,v in item.items() if k!='content'} for item in items]
        db.execute('''INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens,reserved_cost_units)
            VALUES(?,?,?,?,?,'prepared',?,?)''',
            (job_id, ordinal, str(uuid.uuid4()), core._hash([encoded,model_adapter.PROMPT_VERSION,job['kind']]),
             json.dumps(references,ensure_ascii=False,separators=(',', ':')),
             payload['input_cap'] + payload['output_cap'],payload['input_cap'] + payload['output_cap']))
    return len(batches)


def model_status(db, job_id, character_id):
    job = owned(db, job_id, character_id)
    chunks = [dict(r) for r in db.execute('SELECT ordinal,state,reserved_tokens,reserved_cost_units,used_input_tokens,used_output_tokens,used_cost_units,warning_json FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal', (job_id,))]
    for chunk in chunks:
        chunk['warningCodes']=json.loads(chunk.pop('warning_json') or '[]')
    return {'id':job_id,'status':job['status'],'resultVersionId':job['result_version_id'],
            'chunks':chunks,'reservedTokens':sum(x['reserved_tokens'] for x in chunks if x['state'] in ('prepared','possibly_dispatched','unknown')),
            'usedTokens':sum((x['used_input_tokens'] or 0)+(x['used_output_tokens'] or 0) for x in chunks),
            'reservedCostUnits':sum(x['reserved_cost_units'] or 0 for x in chunks if x['state'] not in ('completed','rejected')),
            'usedCostUnits':sum(x['used_cost_units'] or 0 for x in chunks)}


def dispatch_model_chunk(connect, job_id, character_id, tokenizer, adapter):
    """One explicit mock request. Never retry a possibly dispatched request."""
    if not isinstance(adapter, model_adapter.LocalMockAdapter):
        raise ValueError('local_mock_adapter_required')
    with connect() as db:
        job = owned(db, job_id, character_id)
        require_dispatch_settled(db,job)
        payload = _model_payload(job)
        if payload.get('prompt_version') != model_adapter.PROMPT_VERSION:
            raise ValueError('model_prompt_version_changed')
        if job['status'] == 'queued':
            prepare_model(db, job_id, character_id, tokenizer)
            claim(db, job_id, character_id)
            job = owned(db, job_id, character_id)
        if job['status'] != 'running' or snapshot(db, character_id, payload) != job['input_hash']:
            raise ValueError('job_not_dispatchable')
        if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state IN ('possibly_dispatched','unknown')", (job_id,)).fetchone():
            raise ValueError('model_result_unknown')
        row = db.execute("SELECT * FROM mv3_model_chunks WHERE job_id=? AND state='prepared' ORDER BY ordinal LIMIT 1", (job_id,)).fetchone()
        if row is None:
            return _finish_model(db, job, payload)
        inputs=[]
        for ref in json.loads(row['input_json']):
            live=core.live_source(db,character_id,ref['sourceKind'],ref['sourceId'])
            if live is None or live.content_hash!=ref['contentHash'] or live.state_hash!=ref['stateHash']:
                raise ValueError('job_input_changed')
            inputs.append({**ref,'content':live.text})
        encoded=json.dumps(inputs,ensure_ascii=False,separators=(',', ':'))
        if core._hash([encoded,model_adapter.PROMPT_VERSION,job['kind']])!=row['input_hash']:
            raise ValueError('job_input_changed')
        attempt_id=begin_dispatch(db,job_id,character_id,row['ordinal'],
            expected_request_id=row['request_id'],expected_input_hash=row['input_hash'])
        request = {'requestId':row['request_id'],'inputHash':row['input_hash'], 'kind':job['kind'],
                   'promptVersion':model_adapter.PROMPT_VERSION,
                   'instruction':model_adapter.NARRATIVE_PROMPT if job['kind']=='NARRATIVE' else model_adapter.DIGEST_PROMPT,
                   'messages':inputs, 'maxOutputTokens':payload['output_cap']}
        ordinal = row['ordinal']
    # The durable possibly_dispatched record precedes the only adapter call.
    try:
        response = adapter.generate(request)
    except Exception:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            settle_dispatch(db,job_id,character_id,ordinal,row['request_id'],attempt_id,state='unknown')
            db.execute("UPDATE mv3_jobs SET error_code='model_result_unknown' WHERE id=?",(job_id,))
        raise ValueError('model_result_unknown') from None
    input_tokens = model_adapter.count_tokens(request['instruction'] + json.dumps(request['messages'],ensure_ascii=False), tokenizer)
    try:
        items,warnings = fidelity.validate_chunk(request['messages'],response,kind=request['kind'])
        output = json.dumps(items,ensure_ascii=False,separators=(',', ':'))
        output_tokens = model_adapter.count_tokens(output, tokenizer)
        if output_tokens > payload['output_cap']:
            raise ValueError('model_output_budget_exceeded')
    except ValueError as exc:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            settle_dispatch(db,job_id,character_id,ordinal,row['request_id'],attempt_id,state='rejected',
                used_input=input_tokens,used_output=0,used_cost=row['reserved_cost_units'],result_hash=core._hash(['rejected',str(exc)]))
            recompute_parent(db,job_id,character_id,error=str(exc))
        raise
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job = owned(db, job_id, character_id)
        try:
            unchanged = snapshot(db, character_id, payload) == job['input_hash']
        except ValueError:
            unchanged = False
        if not unchanged and not _a3_installed(db):
            # Legacy schema has no durable dispatch attempt binding. Retain its
            # conservative unresolved-result contract, even with a local response.
            settle_dispatch(db,job_id,character_id,ordinal,row['request_id'],attempt_id,state='unknown')
            db.execute("UPDATE mv3_jobs SET error_code='source_changed_during_model' WHERE id=?",(job_id,))
            return {'status':'paused','error':'source_changed_during_model'}
        if not unchanged or ('cancel_intent' in job.keys() and job['cancel_intent']):
            state=settle_dispatch(db,job_id,character_id,ordinal,row['request_id'],attempt_id,state='rejected',
                used_input=input_tokens,used_output=output_tokens,used_cost=input_tokens+output_tokens,
                result_hash=core._hash(['stale',row['request_id'],output]))
            state=recompute_parent(db,job_id,character_id,error='source_changed_during_model')
            return {'status':state,'error':'source_changed_during_model'}
        state=settle_dispatch(db,job_id,character_id,ordinal,row['request_id'],attempt_id,state='completed',
            used_input=input_tokens,used_output=output_tokens,used_cost=input_tokens+output_tokens,
            result_hash=core._hash(['completed',row['request_id'],output]))
        if state!='running':
            return {'status':state}
        db.execute("UPDATE mv3_model_chunks SET output_json=?,warning_json=? WHERE job_id=? AND ordinal=?",
            (output,json.dumps(warnings),job_id,ordinal))
        if db.execute("SELECT 1 FROM mv3_model_chunks WHERE job_id=? AND state!='completed'", (job_id,)).fetchone():
            return model_status(db,job_id,character_id)
        db.execute('SAVEPOINT m35_business_result')
        try:
            result=_finish_model(db,job,payload)
            db.execute('RELEASE m35_business_result')
            return result
        except (ValueError,sqlite3.DatabaseError) as exc:
            db.execute('ROLLBACK TO m35_business_result')
            db.execute('RELEASE m35_business_result')
            state=recompute_parent(db,job_id,character_id,error=type(exc).__name__)
            return {'status':state,'error':'business_result_failed'}


def _finish_model(db,job,payload,*,provider='local-mock',model='local-mock-m35',
                  prompt_version=model_adapter.PROMPT_VERSION):
    if not business_result_allowed(db,job['id'],job['character_id']):
        raise ValueError('business_result_blocked')
    rows = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',(job['id'],)).fetchall()
    if not rows or any(row['state']!='completed' for row in rows):
        raise ValueError('model_batches_incomplete')
    chunks = [{'items':json.loads(row['output_json']), 'warnings':json.loads(row['warning_json'] or '[]')} for row in rows]
    items,warnings = fidelity.join_checked(chunks)
    expected = [(ref['kind'],str(ref['id'])) for ref in payload['refs']]
    if [(item['sourceKind'],item['sourceId']) for item in items] != expected:
        raise ValueError('model_source_coverage_invalid')
    parts,mapping,offset=[],[],0
    for item in items:
        text=item['text']
        # Provenance belongs to mapping_json, not the reader-facing dialogue body.
        prefix='' if job['kind']=='NARRATIVE' else f"[{item['sourceKind']}:{item['sourceId']} | {item['speaker']}] "
        parts.append(prefix+text)
        mapping.append({'sourceKind':item['sourceKind'],'sourceId':item['sourceId'],'sourceRole':item['speaker'],
                        'outputStart':offset+len(prefix),'outputEnd':offset+len(prefix)+len(text),
                        'rawStart':None,'rawEnd':None,'mappingStatus':'requires_human_fidelity_review',
                        'warningCodes':warnings})
        offset += len(prefix)+len(text)+2
    refs=payload['refs']
    view=job['kind']
    derivation=core.create_derivation(db,job['character_id'],view,refs)
    rule='model-narrative-m35-v3' if view=='NARRATIVE' else f"model-digest-m35-v1:{payload['level']}:{payload['period']}"
    version=core.add_version(db,derivation['id'],refs,'\n\n'.join(parts),author_kind='model',
        rule_version=rule,prompt_version=prompt_version,
        model_provider=provider,model_id=model,
        child_version_ids=payload.get('children',()),mapping=mapping)
    state=recompute_parent(db,job['id'],job['character_id'],output_ready=True,result_version_id=version['id'])
    return {'status':state,'versionId':version['id'],'warnings':warnings}
