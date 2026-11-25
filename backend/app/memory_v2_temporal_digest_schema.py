"""Explicit A3.6 input bindings. Never called by normal initialization."""

TABLE = "mv3_time_digest_bindings_v2"
INDEX = "ix_mv3_time_digest_v2_node"
SQL = """CREATE TABLE mv3_time_digest_bindings_v2 (
    id TEXT PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    level TEXT NOT NULL CHECK(level IN ('L1','L2','L3','L4')),
    period TEXT NOT NULL,
    time_basis TEXT NOT NULL CHECK(time_basis IN ('occurred_at','occurred_day')),
    offset_minutes INTEGER NOT NULL CHECK(offset_minutes BETWEEN -840 AND 840),
    input_profile TEXT NOT NULL CHECK(input_profile IN
        ('raw_sources_v1','narrative_v1','synopsis_v1','child_digests_v1','composite_v1')),
    scope_hash TEXT NOT NULL,
    input_manifest_json TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    input_token TEXT NOT NULL,
    time_evidence_token TEXT NOT NULL,
    time_token TEXT NOT NULL,
    digest_version_id TEXT NOT NULL UNIQUE REFERENCES mv3_versions(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
)"""
INDEX_SQL = "CREATE INDEX ix_mv3_time_digest_v2_node ON mv3_time_digest_bindings_v2(character_id,node_id,created_at)"


def installed(db):
    table = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    index = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX,)).fetchone()
    if table is None and index is None:
        return False
    if table is None or index is None or table[0] != SQL or index[0] != INDEX_SQL:
        raise ValueError('a36_schema_invalid')
    return True


def migrate(db):
    """Run only on an explicitly selected isolated database."""
    if db.in_transaction:
        raise ValueError('migration_requires_idle_connection')
    if installed(db):
        return False
    for name in ('mv3_time_digest_bindings', 'mv3_versions', 'mv3_jobs'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is None:
            raise ValueError('a36_prerequisite_missing')
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute(SQL)
        db.execute(INDEX_SQL)
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('a36_migration_foreign_key_error')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('a36_migration_integrity_error')
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not installed(db):
        raise ValueError('a36_schema_invalid')
    return True
