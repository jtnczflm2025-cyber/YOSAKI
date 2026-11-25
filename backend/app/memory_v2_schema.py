"""Additive, rebuildable Memory V2 M1 index schema. No source tables change."""

SCHEMA_VERSION = 2


def init_db(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS mv2_meta (
      character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
      schema_version INTEGER NOT NULL,
      active_generation INTEGER NOT NULL DEFAULT 0,
      order_revision INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL CHECK(state IN ('needs_rebuild','ready')),
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mv2_sources (
      generation INTEGER NOT NULL,
      source_kind TEXT NOT NULL CHECK(source_kind IN ('message','activity_operation')),
      source_id TEXT NOT NULL,
      archive_file_id TEXT,
      archive_ordinal INTEGER,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      stream_kind TEXT NOT NULL,
      stream_id TEXT NOT NULL,
      source_role TEXT NOT NULL,
      source_status TEXT NOT NULL,
      evidence_kind TEXT NOT NULL,
      occurred_at TEXT,
      recorded_at TEXT,
      outcome_recorded_at TEXT,
      time_basis TEXT NOT NULL CHECK(time_basis IN ('occurred','recorded','unknown')),
      indexed_at TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      state_hash TEXT NOT NULL,
      text_length INTEGER NOT NULL,
      seen_job_id TEXT,
      PRIMARY KEY(generation,source_kind,source_id)
    );
    CREATE INDEX IF NOT EXISTS ix_mv2_sources_role_time
      ON mv2_sources(character_id,generation,occurred_at,recorded_at,source_kind,source_id);
    CREATE INDEX IF NOT EXISTS ix_mv2_sources_duplicate
      ON mv2_sources(character_id,generation,content_hash);
    CREATE TABLE IF NOT EXISTS mv2_terms (
      generation INTEGER NOT NULL,
      source_kind TEXT NOT NULL,
      source_id TEXT NOT NULL,
      term TEXT NOT NULL,
      PRIMARY KEY(generation,source_kind,source_id,term),
      FOREIGN KEY(generation,source_kind,source_id)
        REFERENCES mv2_sources(generation,source_kind,source_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS ix_mv2_terms_lookup ON mv2_terms(term,generation,source_kind,source_id);
    CREATE TABLE IF NOT EXISTS mv2_segments (
      generation INTEGER NOT NULL,
      segment_id TEXT NOT NULL,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      stream_kind TEXT NOT NULL,
      stream_id TEXT NOT NULL,
      time_basis TEXT NOT NULL,
      start_at TEXT,
      end_at TEXT,
      member_count INTEGER NOT NULL,
      PRIMARY KEY(generation,segment_id)
    );
    CREATE INDEX IF NOT EXISTS ix_mv2_segments_role ON mv2_segments(character_id,generation,start_at,segment_id);
    CREATE TABLE IF NOT EXISTS mv2_segment_members (
      generation INTEGER NOT NULL,
      segment_id TEXT NOT NULL,
      source_kind TEXT NOT NULL,
      source_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL,
      order_warning TEXT,
      PRIMARY KEY(generation,segment_id,source_kind,source_id),
      FOREIGN KEY(generation,segment_id)
        REFERENCES mv2_segments(generation,segment_id) ON DELETE CASCADE,
      FOREIGN KEY(generation,source_kind,source_id)
        REFERENCES mv2_sources(generation,source_kind,source_id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS mv2_jobs (
      job_id TEXT PRIMARY KEY,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      mode TEXT NOT NULL CHECK(mode IN ('incremental','rebuild')),
      status TEXT NOT NULL CHECK(status IN ('running','paused','completed','failed')),
      phase TEXT NOT NULL CHECK(phase IN ('messages','activities','sweep','segments','done')),
      target_generation INTEGER NOT NULL,
      message_cursor INTEGER NOT NULL DEFAULT 0,
      activity_time_cursor TEXT NOT NULL DEFAULT '',
      activity_id_cursor TEXT NOT NULL DEFAULT '',
      sweep_kind_cursor TEXT NOT NULL DEFAULT '',
      sweep_id_cursor TEXT NOT NULL DEFAULT '',
      processed INTEGER NOT NULL DEFAULT 0,
      changed INTEGER NOT NULL DEFAULT 0,
      invalidated INTEGER NOT NULL DEFAULT 0,
      segment_cursor_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      error_code TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_mv2_one_open_job
      ON mv2_jobs(character_id) WHERE status IN ('running','paused');
    """)
    # Startup must never ALTER an existing v1 index. Explicit migration is separate.
    version = SCHEMA_VERSION if "order_revision" in {r[1] for r in db.execute("PRAGMA table_info(mv2_meta)")} else 1
    if version == SCHEMA_VERSION:
        db.execute("""CREATE INDEX IF NOT EXISTS ix_mv2_archive_order
            ON mv2_sources(character_id,generation,archive_file_id,stream_id,archive_ordinal)""")
    db.execute("""INSERT OR IGNORE INTO mv2_meta(character_id,schema_version,active_generation,state,updated_at)
        SELECT id,?,0,'needs_rebuild',datetime('now') FROM characters""", (version,))
    db.execute("UPDATE mv2_jobs SET status='paused',updated_at=datetime('now') WHERE status='running'")


def migrate_order_v2(db):
    """Explicit migration for an authorized isolated database, never called by main.init_db."""
    if db.in_transaction:
        raise ValueError("migration_requires_own_transaction")
    columns_ready = ("order_revision" in {r[1] for r in db.execute("PRAGMA table_info(mv2_meta)")}
                     and "archive_file_id" in {r[1] for r in db.execute("PRAGMA table_info(mv2_sources)")}
                     and "order_warning" in {r[1] for r in db.execute("PRAGMA table_info(mv2_segment_members)")})
    if columns_ready and not db.execute("SELECT 1 FROM mv2_meta WHERE schema_version<? LIMIT 1",
                                        (SCHEMA_VERSION,)).fetchone():
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        additions = (
            ("mv2_meta", "order_revision", "INTEGER NOT NULL DEFAULT 0"),
            ("mv2_sources", "archive_file_id", "TEXT"),
            ("mv2_sources", "archive_ordinal", "INTEGER"),
            ("mv2_segment_members", "order_warning", "TEXT"),
        )
        for table, column, definition in additions:
            present = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            if column not in present:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.execute("""CREATE INDEX IF NOT EXISTS ix_mv2_archive_order
            ON mv2_sources(character_id,generation,archive_file_id,stream_id,archive_ordinal)""")
        db.execute("""UPDATE mv2_jobs SET status='failed',error_code='order_contract_upgrade',updated_at=datetime('now')
            WHERE status IN ('running','paused') AND character_id IN
                (SELECT character_id FROM mv2_meta WHERE schema_version<?)""", (SCHEMA_VERSION,))
        db.execute("""UPDATE mv2_meta SET schema_version=?,state='needs_rebuild',order_revision=order_revision+1,
            updated_at=datetime('now') WHERE schema_version<?""", (SCHEMA_VERSION, SCHEMA_VERSION))
        db.commit()
    except Exception:
        db.rollback()
        raise
