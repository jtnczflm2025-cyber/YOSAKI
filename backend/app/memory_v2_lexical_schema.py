"""Explicit A3.3 Lexical projection migration. Never called by main.init_db()."""
import hashlib
import re

from . import memory_v2_processing_schema as a3_schema
from . import memory_v2_tag_schema as tag_schema

VERSION = 'a33-lexical-v1'

DDL = {
    'mv3_a33_schema': '''CREATE TABLE mv3_a33_schema(
        version TEXT PRIMARY KEY CHECK(version='a33-lexical-v1'),
        schema_hash TEXT NOT NULL, applied_at TEXT NOT NULL)''',
    'mv3_lexical_state': '''CREATE TABLE mv3_lexical_state(
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        generation INTEGER NOT NULL CHECK(generation>0),
        revision INTEGER NOT NULL CHECK(revision>0),
        authority_hash TEXT NOT NULL, projection_hash TEXT NOT NULL,
        config_version TEXT NOT NULL)''',
    'mv3_lexical_terms': '''CREATE TABLE mv3_lexical_terms(
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        generation INTEGER NOT NULL CHECK(generation>0),
        entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
        field_name TEXT NOT NULL CHECK(field_name IN ('title','tags','keywords','synopsis')),
        field_id TEXT NOT NULL REFERENCES mv3_entry_fields(id),
        field_token TEXT NOT NULL,
        value_ordinal INTEGER NOT NULL CHECK(value_ordinal>=0),
        normalized_term TEXT NOT NULL CHECK(length(normalized_term)>0),
        match_kind TEXT NOT NULL CHECK(match_kind IN ('direct','alias','expansion')),
        vocabulary_id TEXT NOT NULL DEFAULT '',
        evidence_hash TEXT NOT NULL,
        PRIMARY KEY(character_id,generation,entry_id,field_id,value_ordinal,
                    normalized_term,match_kind,vocabulary_id))''',
    'ix_mv3_lexical_term': '''CREATE INDEX ix_mv3_lexical_term
        ON mv3_lexical_terms(character_id,generation,normalized_term,entry_id)''',
    'ix_mv3_lexical_entry': '''CREATE INDEX ix_mv3_lexical_entry
        ON mv3_lexical_terms(character_id,generation,entry_id)''',
}


def _normalized(sql):
    return re.sub(r'\s+', ' ', sql.strip()).lower()


SCHEMA_HASH = hashlib.sha256('\n'.join(
    name + ':' + _normalized(sql) for name, sql in sorted(DDL.items())
).encode()).hexdigest()


def state(db):
    existing = {row['name']: row['sql'] for row in db.execute(
        'SELECT name,sql FROM sqlite_master WHERE name IN (' +
        ','.join('?' for _ in DDL) + ')', tuple(DDL))}
    if not existing:
        return 'absent'
    if set(existing) != set(DDL) or any(
            _normalized(existing[name] or '') != _normalized(sql)
            for name, sql in DDL.items()):
        return 'invalid'
    rows = db.execute('SELECT version,schema_hash FROM mv3_a33_schema').fetchall()
    if len(rows) != 1 or rows[0]['version'] != VERSION or rows[0]['schema_hash'] != SCHEMA_HASH:
        return 'invalid'
    return 'valid'


def require(db):
    current = state(db)
    if current != 'valid':
        raise ValueError('a33_migration_required' if current == 'absent' else 'a33_schema_invalid')


def migrate(db):
    if db.in_transaction:
        raise ValueError('migration_requires_idle_connection')
    current = state(db)
    if current == 'valid':
        return False
    if current != 'absent':
        raise ValueError('a33_schema_invalid')
    db.execute('BEGIN IMMEDIATE')
    try:
        if not a3_schema.installed(db) or tag_schema.state(db) != 'valid':
            raise ValueError('a33_prerequisite_missing')
        for name in ('mv3_entries', 'mv3_entry_fields', 'mv3_entry_field_checks', 'mv3_vocabulary'):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
                raise ValueError('a33_prerequisite_missing')
        for sql in DDL.values():
            db.execute(sql)
        db.execute("INSERT INTO mv3_a33_schema VALUES(?,?,datetime('now'))", (VERSION, SCHEMA_HASH))
        if (state(db) != 'valid' or db.execute('PRAGMA foreign_key_check').fetchone() or
                db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'):
            raise ValueError('a33_migration_check_failed')
        db.commit()
        return True
    except BaseException:
        db.rollback()
        raise
