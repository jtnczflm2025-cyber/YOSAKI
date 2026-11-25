"""Explicit, isolated A3 task migration. Never called by main.init_db()."""
import sqlite3
import json
import hashlib

VERSION = 'a3-processing-v1'


def installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv3_a3_audit'").fetchone())


def _classify_legacy(db):
    db.execute('''CREATE TABLE mv3_a3_migration_holds(
        job_id TEXT PRIMARY KEY REFERENCES mv3_jobs(id) ON DELETE CASCADE,
        prior_status TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT NOT NULL)''')
    for job in db.execute('SELECT * FROM mv3_jobs').fetchall():
        if job['contract_version']:
            continue
        chunks=[dict(r) for r in db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(job['id'],))]
        try: payload=json.loads(job['payload_json'])
        except (ValueError,TypeError): payload={}
        model=bool(payload.get('model_mode') or chunks)
        uncertain=any(r['state'] in ('possibly_dispatched','unknown') for r in chunks)
        settled=bool(chunks) and all(r['state'] in ('completed','rejected') and
            type(r['used_input_tokens']) is int and type(r['used_output_tokens']) is int and
            r['used_input_tokens']>=0 and r['used_output_tokens']>=0 and
            (not r.get('trial_id') or (r.get('provider_response_id') and r.get('provider_usage_json') and
                                      r.get('usage_estimated_usd_micros') is not None)) for r in chunks)
        # Known legacy dispatchers claim before calling an adapter. A fresh queued
        # task with zero claims and no dispatched/accounted children is a bounded
        # implementation proof, not an inference from timestamp/lease expiration.
        unclaimed=(job['status']=='queued' and job['attempts']==0 and all(
            r['state']=='prepared' and r['request_id'] and r['input_hash'] and
            r['used_input_tokens'] is None and r['used_output_tokens'] is None for r in chunks))
        hold=uncertain or job['status']=='unknown' or (model and not settled and not unclaimed)
        if job['status']=='running': hold=True
        if not hold: continue
        evidence=json.dumps({'policy':'legacy-evidence-v2','status':job['status'],
            'attempts':job['attempts'],'modelMode':model,'settled':settled,'unclaimed':unclaimed,
            'chunks':[{k:r.get(k) for k in ('ordinal','request_id','state','reserved_tokens',
                'reserved_cost_units','used_input_tokens','used_output_tokens','used_cost_units',
                'reserved_usd_micros','usage_estimated_usd_micros')} for r in chunks]},sort_keys=True)
        reason='unsettled_child' if uncertain else 'dispatch_or_settlement_unproven'
        db.execute('INSERT INTO mv3_a3_migration_holds VALUES(?,?,?,?)',(job['id'],job['status'],reason,evidence))
        db.execute('''INSERT INTO mv3_a3_audit(job_id,event,evidence_hash,prior_status,created_at)
            VALUES(?,?,?,?,datetime('now'))''',(job['id'],reason,hashlib.sha256(evidence.encode()).hexdigest(),job['status']))
        db.execute("UPDATE mv3_jobs SET status='unknown',error_code=?,lease_token=NULL,lease_until=NULL WHERE id=?",(reason,job['id']))
    db.execute('''CREATE TRIGGER mv3_a3_hold_guard BEFORE UPDATE OF status ON mv3_jobs
        WHEN NEW.status!='unknown' AND EXISTS(SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=NEW.id)
        BEGIN SELECT RAISE(ABORT,'legacy_reconciliation_required'); END''')


def migrate(db):
    if db.in_transaction:
        raise ValueError('migration_requires_idle_connection')
    if installed(db):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_a3_migration_holds'").fetchone():
            return False
        try:
            db.execute('BEGIN IMMEDIATE'); _classify_legacy(db)
            if db.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('migration_integrity_failed')
            db.commit(); return True
        except Exception:
            db.rollback(); raise
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv3_jobs'").fetchone():
        raise ValueError('mv3_jobs_missing')
    before = db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0]
    children = db.execute('SELECT COUNT(*) FROM mv3_model_chunks').fetchone()[0]
    old_fk = db.execute('PRAGMA foreign_keys').fetchone()[0]
    try:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute('BEGIN IMMEDIATE')
        # The existing table has a status CHECK without unknown. Rebuild it while
        # preserving IDs and dependent rows; refuse unrecognized schema changes.
        columns = [r[1] for r in db.execute('PRAGMA table_info(mv3_jobs)')]
        expected = ['id','character_id','kind','input_hash','payload_json','status',
                    'attempts','lease_token','lease_until','result_version_id','error_code','created_at']
        if columns != expected:
            raise ValueError('unrecognized_jobs_schema')
        db.execute('ALTER TABLE mv3_jobs RENAME TO mv3_jobs_a3_old')
        db.execute('''CREATE TABLE mv3_jobs (
            id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, input_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','paused','unknown','completed','failed','cancelled')),
            attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
            result_version_id TEXT REFERENCES mv3_versions(id) ON DELETE SET NULL,
            error_code TEXT, created_at TEXT NOT NULL,
            contract_version TEXT, scope_key TEXT, cancel_intent INTEGER NOT NULL DEFAULT 0,
            read_set_json TEXT, write_set_json TEXT,
            UNIQUE(character_id,kind,input_hash))''')
        db.execute('''INSERT INTO mv3_jobs (id,character_id,kind,input_hash,payload_json,status,
            attempts,lease_token,lease_until,result_version_id,error_code,created_at)
            SELECT id,character_id,kind,input_hash,payload_json,status,attempts,lease_token,
                   lease_until,result_version_id,error_code,created_at FROM mv3_jobs_a3_old''')
        db.execute('''CREATE TABLE mv3_a3_audit (
            id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES mv3_jobs(id) ON DELETE CASCADE,
            event TEXT NOT NULL, evidence_hash TEXT, prior_status TEXT, created_at TEXT NOT NULL)''')
        _classify_legacy(db)
        db.execute('DROP TABLE mv3_jobs_a3_old')
        db.execute('ALTER TABLE mv3_model_chunks ADD COLUMN dispatch_attempt_id TEXT')
        db.execute('ALTER TABLE mv3_model_chunks ADD COLUMN settlement_hash TEXT')
        db.execute('CREATE INDEX ix_mv3_a3_scope ON mv3_jobs(character_id,scope_key,status)')
        # Old dispatchers update a child first. On migrated DB they fail before
        # sending, rather than creating a child whose parent says running.
        db.execute('''CREATE TRIGGER mv3_a3_child_guard BEFORE UPDATE OF state ON mv3_model_chunks
            WHEN NEW.state IN ('possibly_dispatched','unknown')
            AND (SELECT status FROM mv3_jobs WHERE id=NEW.job_id)!='unknown'
            BEGIN SELECT RAISE(ABORT,'a3_parent_must_be_unknown'); END''')
        db.execute('''CREATE TRIGGER mv3_a3_parent_guard BEFORE UPDATE OF status ON mv3_jobs
            WHEN NEW.status!='unknown' AND EXISTS
            (SELECT 1 FROM mv3_model_chunks WHERE job_id=NEW.id
             AND state IN ('possibly_dispatched','unknown'))
            BEGIN SELECT RAISE(ABORT,'a3_unsettled_child'); END''')
        if (db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0] != before or
            db.execute('SELECT COUNT(*) FROM mv3_model_chunks').fetchone()[0] != children or
            db.execute('PRAGMA foreign_key_check').fetchone() or
            db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'):
            raise ValueError('migration_integrity_failed')
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA legacy_alter_table=OFF')
        db.execute(f'PRAGMA foreign_keys={old_fk}')
