"""Explicit A3.2 Tag migration for isolated databases only.

The marker authenticates the *shape* expected by this code, not database contents.
No application startup path calls :func:`migrate`.
"""
import hashlib
import re

from . import memory_v2_processing_schema as a3_schema

VERSION = 'a32-tags-v1'

DDL = {
    'mv3_a32_schema': '''CREATE TABLE mv3_a32_schema(
        version TEXT PRIMARY KEY CHECK(version='a32-tags-v1'),
        schema_hash TEXT NOT NULL, applied_at TEXT NOT NULL)''',
    'mv3_entry_tag_decisions': '''CREATE TABLE mv3_entry_tag_decisions(
        id TEXT PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
        tag_id TEXT NOT NULL REFERENCES mv3_vocabulary(id) DEFERRABLE INITIALLY DEFERRED,
        revision INTEGER NOT NULL CHECK(revision>0),
        action TEXT NOT NULL CHECK(action IN ('keep','suppress','revoke')),
        source_token TEXT NOT NULL,
        basis_field_id TEXT REFERENCES mv3_entry_fields(id),
        owner_id INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        UNIQUE(character_id,entry_id,tag_id,revision))''',
    'mv3_tag_proposal_revisions': '''CREATE TABLE mv3_tag_proposal_revisions(
        id TEXT PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
        proposal_key TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),
        tag_id TEXT REFERENCES mv3_vocabulary(id) DEFERRABLE INITIALLY DEFERRED,
        surface TEXT NOT NULL CHECK(length(surface) BETWEEN 1 AND 80),
        language TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        source_token TEXT NOT NULL,
        dependency_json TEXT NOT NULL,
        rule_version TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','rejected','handled','reopened')),
        origin_job_id TEXT,
        origin_job_hash TEXT NOT NULL,
        reviewer_id INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL,
        UNIQUE(character_id,entry_id,proposal_key,revision))''',
    'ix_mv3_tag_decisions_latest': '''CREATE INDEX ix_mv3_tag_decisions_latest
        ON mv3_entry_tag_decisions(character_id,entry_id,tag_id,revision DESC)''',
    'ix_mv3_tag_proposals_latest': '''CREATE INDEX ix_mv3_tag_proposals_latest
        ON mv3_tag_proposal_revisions(character_id,entry_id,proposal_key,revision DESC)''',
    'ix_mv3_tag_proposals_read': '''CREATE INDEX ix_mv3_tag_proposals_read
        ON mv3_tag_proposal_revisions(character_id,entry_id,created_at,id)''',
    'mv3_a32_decision_owner': '''CREATE TRIGGER mv3_a32_decision_owner BEFORE INSERT
        ON mv3_entry_tag_decisions BEGIN
        SELECT CASE WHEN NOT EXISTS(
            SELECT 1 FROM mv3_entries e JOIN mv3_vocabulary v
            ON v.id=NEW.tag_id JOIN characters c ON c.id=e.character_id
            WHERE e.id=NEW.entry_id AND e.character_id=NEW.character_id
            AND v.character_id=NEW.character_id AND v.kind='tag' AND c.enabled=1)
        THEN RAISE(ABORT,'tag_decision_scope_invalid') END; END''',
    'mv3_a32_proposal_owner': '''CREATE TRIGGER mv3_a32_proposal_owner BEFORE INSERT
        ON mv3_tag_proposal_revisions BEGIN
        SELECT CASE WHEN NOT EXISTS(
            SELECT 1 FROM mv3_entries e JOIN characters c ON c.id=e.character_id
            WHERE e.id=NEW.entry_id AND e.character_id=NEW.character_id AND c.enabled=1)
        OR (NEW.tag_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM mv3_vocabulary v WHERE v.id=NEW.tag_id
            AND v.character_id=NEW.character_id AND v.kind='tag'))
        THEN RAISE(ABORT,'tag_proposal_scope_invalid') END; END''',
}


def _normalized(sql):
    return re.sub(r'\s+', ' ', sql.strip()).lower()


SCHEMA_HASH = hashlib.sha256('\n'.join(
    key + ':' + _normalized(value) for key, value in sorted(DDL.items())
).encode()).hexdigest()


def state(db):
    """absent, valid, or invalid. Partial installation is never 'absent'."""
    existing = {r['name']: r['sql'] for r in db.execute(
        "SELECT name,sql FROM sqlite_master WHERE name IN (" +
        ','.join('?' for _ in DDL) + ')', tuple(DDL))}
    if not existing:
        return 'absent'
    if set(existing) != set(DDL):
        return 'invalid'
    if any(_normalized(existing[k] or '') != _normalized(v) for k, v in DDL.items()):
        return 'invalid'
    row = db.execute('SELECT version,schema_hash FROM mv3_a32_schema').fetchall()
    if len(row) != 1 or row[0]['version'] != VERSION or row[0]['schema_hash'] != SCHEMA_HASH:
        return 'invalid'
    return 'valid'


def require(db):
    status = state(db)
    if status != 'valid':
        raise ValueError('a32_migration_required' if status == 'absent' else 'a32_schema_invalid')


def migrate(db):
    if db.in_transaction:
        raise ValueError('migration_requires_idle_connection')
    status = state(db)
    if status == 'valid':
        return False
    if status != 'absent':
        raise ValueError('a32_schema_invalid')
    db.execute('BEGIN IMMEDIATE')
    try:
        if not a3_schema.installed(db):
            raise ValueError('a3_migration_required')
        needed = ('mv3_entries','mv3_entry_fields','mv3_vocabulary','mv3_jobs')
        if any(not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (n,)).fetchone()
               for n in needed):
            raise ValueError('a32_prerequisite_missing')
        for sql in DDL.values():
            db.execute(sql)
        db.execute("INSERT INTO mv3_a32_schema VALUES(?,?,datetime('now'))", (VERSION, SCHEMA_HASH))
        if state(db) != 'valid' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('a32_migration_check_failed')
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
