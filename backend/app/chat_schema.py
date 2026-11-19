"""Additive chat schema. Installed only by the application's explicit initializer."""

DDL = (
    """CREATE TABLE IF NOT EXISTS chat_path_exclusions (
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(conversation_id,message_id))""",
    """CREATE TABLE IF NOT EXISTS chat_attempt_workers (
        attempt_id TEXT PRIMARY KEY REFERENCES chat_attempts(id) ON DELETE CASCADE,
        pid INTEGER NOT NULL, process_identity TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_usage_receipts (
        attempt_id TEXT NOT NULL REFERENCES chat_attempts(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL, response_id TEXT, usage_json TEXT NOT NULL,
        disposition TEXT NOT NULL, PRIMARY KEY(attempt_id,fingerprint))""",
    """CREATE TABLE IF NOT EXISTS chat_model_capabilities (
        model_id INTEGER PRIMARY KEY REFERENCES model_profiles(id) ON DELETE CASCADE,
        vision INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_attempts (
        id TEXT PRIMARY KEY, conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
        character_id INTEGER REFERENCES characters(id) ON DELETE SET NULL,
        purpose TEXT NOT NULL, origin_message_id INTEGER, assistant_message_id INTEGER,
        protocol TEXT, requested_model TEXT, returned_model TEXT, response_id TEXT,
        status TEXT NOT NULL DEFAULT 'prepared', dispatched INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL, finished_at TEXT, first_text_ms REAL, first_public_ms REAL,
        last_text_ms REAL, elapsed_ms REAL, usage_json TEXT, normalized_json TEXT,
        reliability TEXT NOT NULL DEFAULT 'unknown', error_kind TEXT, terminal TEXT,
        parameters_json TEXT, snapshot_json TEXT, snapshot_sha256 TEXT, sources_json TEXT)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS chat_one_active ON chat_attempts(conversation_id)
        WHERE status IN ('prepared','dispatching','streaming')""",
    """CREATE TABLE IF NOT EXISTS chat_message_state (
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        hidden INTEGER NOT NULL DEFAULT 0, revision_of INTEGER,
        author TEXT NOT NULL DEFAULT 'model', attempt_id TEXT,
        group_id INTEGER, reason TEXT, changed_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_branches (
        conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
        root_id INTEGER NOT NULL, parent_id INTEGER, fork_message_id INTEGER,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_branch_members (
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
        PRIMARY KEY(conversation_id,position), UNIQUE(conversation_id,message_id))""",
    """CREATE TABLE IF NOT EXISTS chat_memory_scope (
        memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
        conversation_id INTEGER, source_ids_json TEXT NOT NULL, shared INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS chat_attachments (
        id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        name TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
        data BLOB NOT NULL, extracted_text TEXT, diagnostics_json TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_message_attachments (
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        attachment_id TEXT NOT NULL REFERENCES chat_attachments(id), PRIMARY KEY(message_id,attachment_id))""",
    """CREATE TABLE IF NOT EXISTS chat_quotes (
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        source_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        source_sha256 TEXT NOT NULL, excerpt TEXT NOT NULL, PRIMARY KEY(message_id,source_message_id))""",
    """CREATE TABLE IF NOT EXISTS chat_translations (
        id TEXT PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        source_sha256 TEXT NOT NULL, language TEXT NOT NULL, model_id INTEGER NOT NULL,
        prompt_version TEXT NOT NULL, attempt_id TEXT NOT NULL, text TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_summaries (
        id TEXT PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        coverage_json TEXT NOT NULL, content TEXT NOT NULL, attempt_id TEXT,
        enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS chat_context_policy (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 0, auto_summary INTEGER NOT NULL DEFAULT 0,
        trigger_ratio REAL NOT NULL DEFAULT 0.75, target_ratio REAL NOT NULL DEFAULT 0.58)""",
)


def install(db):
    for statement in DDL:
        db.execute(statement)
    if 'summary_model_id' not in {r[1] for r in db.execute('PRAGMA table_info(chat_context_policy)')}:
        db.execute('ALTER TABLE chat_context_policy ADD COLUMN summary_model_id INTEGER')
    if 'root_id' not in {r[1] for r in db.execute('PRAGMA table_info(chat_attempts)')}:
        db.execute('ALTER TABLE chat_attempts ADD COLUMN root_id INTEGER')
    if 'dispatched_at' not in {r[1] for r in db.execute('PRAGMA table_info(chat_attempts)')}:
        db.execute('ALTER TABLE chat_attempts ADD COLUMN dispatched_at TEXT')


def installed(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_attempts'").fetchone())
