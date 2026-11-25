"""M3 derived text tables. Explicit initialization only; never imported by main."""

SCHEMA_VERSION = 4


def init_db(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS mv3_derivations (
      id TEXT PRIMARY KEY,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      view_type TEXT NOT NULL CHECK(view_type IN ('CLEANED','NARRATIVE','DIGEST')),
      experience_key TEXT NOT NULL,
      active_version INTEGER,
      created_at TEXT NOT NULL,
      UNIQUE(character_id,view_type,experience_key)
    );
    CREATE TABLE IF NOT EXISTS mv3_versions (
      id TEXT PRIMARY KEY,
      derivation_id TEXT NOT NULL REFERENCES mv3_derivations(id) ON DELETE CASCADE,
      version INTEGER NOT NULL,
      body TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','stale','failed','needs_review')),
      review_status TEXT NOT NULL CHECK(review_status IN ('unreviewed','auto_checked','human_checked')),
      author_kind TEXT NOT NULL CHECK(author_kind IN ('rule','model','user')),
      input_hash TEXT NOT NULL,
      output_hash TEXT NOT NULL,
      rule_version TEXT NOT NULL,
      prompt_version TEXT,
      model_provider TEXT,
      model_id TEXT,
      created_at TEXT NOT NULL,
      stale_reason TEXT,
      UNIQUE(derivation_id,version)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_mv3_automatic_input
      ON mv3_versions(derivation_id,input_hash,author_kind)
      WHERE author_kind IN ('rule','model');
    CREATE TABLE IF NOT EXISTS mv3_source_refs (
      version_id TEXT NOT NULL REFERENCES mv3_versions(id) ON DELETE CASCADE,
      ordinal INTEGER NOT NULL CHECK(ordinal>=0),
      source_kind TEXT NOT NULL CHECK(source_kind IN ('message','activity_operation')),
      source_id TEXT NOT NULL,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      source_role TEXT NOT NULL,
      evidence_kind TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      state_hash TEXT NOT NULL,
      PRIMARY KEY(version_id,ordinal),
      UNIQUE(version_id,source_kind,source_id)
    );
    CREATE INDEX IF NOT EXISTS ix_mv3_source_ref
      ON mv3_source_refs(character_id,source_kind,source_id);
    CREATE TABLE IF NOT EXISTS mv3_dependencies (
      version_id TEXT NOT NULL REFERENCES mv3_versions(id) ON DELETE CASCADE,
      ordinal INTEGER NOT NULL CHECK(ordinal>=0),
      child_version_id TEXT NOT NULL REFERENCES mv3_versions(id) ON DELETE CASCADE,
      child_output_hash TEXT NOT NULL,
      PRIMARY KEY(version_id,ordinal),
      UNIQUE(version_id,child_version_id),
      CHECK(version_id<>child_version_id)
    );
    CREATE INDEX IF NOT EXISTS ix_mv3_dependency_child
      ON mv3_dependencies(child_version_id);
    """)
    columns = {row[1] for row in db.execute('PRAGMA table_info(mv3_versions)')}
    for name, declaration in (("validation_status", "TEXT NOT NULL DEFAULT 'pending'"),
                              ("validation_binding", "TEXT"), ("edited_by", "INTEGER"),
                              ("mapping_json", "TEXT NOT NULL DEFAULT '[]'")):
        if name not in columns:
            db.execute(f'ALTER TABLE mv3_versions ADD COLUMN {name} {declaration}')
    db.execute("""CREATE TABLE IF NOT EXISTS mv3_reviews (
        id TEXT PRIMARY KEY,
        version_id TEXT NOT NULL REFERENCES mv3_versions(id) ON DELETE CASCADE,
        reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        binding TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(version_id,binding,reviewer_id))""")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS mv3_entries (
      id TEXT PRIMARY KEY,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      status TEXT NOT NULL CHECK(status IN ('draft','active','superseded','archived')),
      active_version_id TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS mv3_entry_versions (
      id TEXT PRIMARY KEY,
      entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      version INTEGER NOT NULL,
      title TEXT NOT NULL,
      keywords_json TEXT NOT NULL,
      tags_json TEXT NOT NULL,
      synopsis TEXT NOT NULL,
      narrative_version_id TEXT REFERENCES mv3_versions(id),
      occurred_start TEXT,
      occurred_end TEXT,
      recorded_start TEXT,
      recorded_end TEXT,
      time_basis TEXT NOT NULL CHECK(time_basis IN ('occurred','recorded','mixed','unknown')),
      source_hash TEXT NOT NULL,
      metadata_hash TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('draft','active','stale')),
      review_status TEXT NOT NULL CHECK(review_status IN ('unreviewed','human_checked')),
      review_binding TEXT,
      edited_by INTEGER REFERENCES users(id),
      created_at TEXT NOT NULL,
      UNIQUE(entry_id,version)
    );
    CREATE TABLE IF NOT EXISTS mv3_entry_sources (
      version_id TEXT NOT NULL REFERENCES mv3_entry_versions(id) ON DELETE CASCADE,
      ordinal INTEGER NOT NULL CHECK(ordinal>=0),
      source_kind TEXT NOT NULL CHECK(source_kind IN ('message','activity_operation')),
      source_id TEXT NOT NULL,
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      source_role TEXT NOT NULL,
      evidence_kind TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      state_hash TEXT NOT NULL,
      PRIMARY KEY(version_id,ordinal),
      UNIQUE(version_id,source_kind,source_id)
    );
    CREATE INDEX IF NOT EXISTS ix_mv3_entry_source ON mv3_entry_sources(character_id,source_kind,source_id);
    CREATE TABLE IF NOT EXISTS mv3_entry_reviews (
      id TEXT PRIMARY KEY,
      version_id TEXT NOT NULL REFERENCES mv3_entry_versions(id) ON DELETE CASCADE,
      reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      binding TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(version_id,binding,reviewer_id)
    );
    CREATE TABLE IF NOT EXISTS mv3_entry_lineage (
      parent_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      child_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      operation TEXT NOT NULL CHECK(operation IN ('split','merge')),
      status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','finalized','cancelled')),
      created_at TEXT NOT NULL,
      PRIMARY KEY(parent_id,child_id),
      CHECK(parent_id<>child_id)
    );
    """)
    lineage_columns = {row[1] for row in db.execute('PRAGMA table_info(mv3_entry_lineage)')}
    if 'status' not in lineage_columns:
        db.execute("ALTER TABLE mv3_entry_lineage ADD COLUMN status TEXT NOT NULL DEFAULT 'proposed'")
