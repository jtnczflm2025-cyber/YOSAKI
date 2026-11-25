"""Explicit, isolated Chronicle import migration. Never called by main.init_db()."""


def init_db(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
    if "archive_only" not in columns:
        db.execute("ALTER TABLE conversations ADD COLUMN archive_only INTEGER NOT NULL DEFAULT 0 CHECK(archive_only IN (0,1))")
    db.execute("""CREATE TABLE IF NOT EXISTS mv2_import_batches(
        id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('preview','confirmed','running','paused','raw_complete','failed')),
        plan_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        processed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, error_code TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS mv2_import_files(
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES mv2_import_batches(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        name TEXT NOT NULL, file_sha256 TEXT NOT NULL, container_sha256 TEXT, parser_version TEXT NOT NULL,
        external_conversation_id TEXT, title TEXT NOT NULL, staged_json TEXT,
        conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
        cursor INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL, status TEXT NOT NULL,
        conversion_note TEXT NOT NULL, UNIQUE(character_id,file_sha256))""")
    db.execute("""CREATE TABLE IF NOT EXISTS mv2_import_receipts(
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        file_id TEXT NOT NULL REFERENCES mv2_import_files(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, external_message_id TEXT, external_conversation_id TEXT,
        original_time_text TEXT, occurred_at TEXT, occurred_date TEXT,
        time_precision TEXT NOT NULL, timezone_basis TEXT NOT NULL,
        recorded_at TEXT NOT NULL, file_sha256 TEXT NOT NULL, container_sha256 TEXT, parser_version TEXT NOT NULL,
        source_path TEXT NOT NULL, conversion_note TEXT NOT NULL, content_sha256 TEXT NOT NULL,
        UNIQUE(file_id,ordinal))""")
    db.execute("""CREATE TABLE IF NOT EXISTS mv2_import_identity_claims(
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        external_conversation_id TEXT NOT NULL, external_message_id TEXT NOT NULL,
        file_id TEXT NOT NULL REFERENCES mv2_import_files(id) ON DELETE CASCADE,
        content_sha256 TEXT NOT NULL,
        PRIMARY KEY(character_id,external_conversation_id,external_message_id))""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_mv2_external_message_identity
        ON mv2_import_receipts(character_id,external_conversation_id,external_message_id)
        WHERE external_conversation_id IS NOT NULL AND external_message_id IS NOT NULL""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_mv2_import_file_batch ON mv2_import_files(batch_id,status)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_mv2_receipts_character ON mv2_import_receipts(character_id,file_id,ordinal)")
    db.execute("""CREATE TABLE IF NOT EXISTS mv2_import_segments(
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES mv2_import_batches(id) ON DELETE CASCADE,
        file_id TEXT NOT NULL REFERENCES mv2_import_files(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, start_ordinal INTEGER NOT NULL, end_ordinal INTEGER NOT NULL,
        title TEXT NOT NULL, entry_id TEXT,
        UNIQUE(batch_id,file_id,ordinal), UNIQUE(batch_id,file_id,start_ordinal,end_ordinal))""")
    # H3.2 confirmation is independent of the H1 preview suggestions. The
    # owner confirmed boundaries; suggested titles remain unreviewed fields.
    db.execute("""CREATE TABLE IF NOT EXISTS mv2_import_boundary_confirmations(
        file_id TEXT PRIMARY KEY REFERENCES mv2_import_files(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL REFERENCES mv2_import_batches(id) ON DELETE CASCADE,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        plan_version INTEGER NOT NULL, plan_hash TEXT NOT NULL,
        source_snapshot_hash TEXT NOT NULL, h31_snapshot_hash TEXT NOT NULL,
        plan_json TEXT NOT NULL, confirmation_origin TEXT NOT NULL,
        confirmed_by_user_id INTEGER REFERENCES users(id), confirmed_at TEXT NOT NULL)""")
