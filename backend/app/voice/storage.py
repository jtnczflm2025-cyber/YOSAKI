import sqlite3
from datetime import datetime, timezone

from . import config
from .schemas import ProfileInput, VoiceConfiguration


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS voice_profiles (
        character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK(provider='elevenlabs'),
        voice_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        configuration_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS voice_privacy_consents (
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK(provider='elevenlabs'),
        notice_version TEXT NOT NULL, accepted_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,provider))""")
    db.execute("""CREATE TABLE IF NOT EXISTS voice_audio_cache (
        id INTEGER PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        cache_key TEXT NOT NULL UNIQUE,
        voice_hash TEXT NOT NULL, model_id TEXT NOT NULL, output_format TEXT NOT NULL,
        language_code TEXT NOT NULL, text_hash TEXT NOT NULL, planner_version TEXT NOT NULL,
        profile_revision INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('generating','ready','unknown','failed')),
        relative_path TEXT, audio_sha256 TEXT, audio_bytes INTEGER,
        attempt_id TEXT NOT NULL, created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL, last_accessed_at TEXT NOT NULL)""")
    db.execute("""CREATE INDEX IF NOT EXISTS voice_audio_cache_expiry
        ON voice_audio_cache(expires_at,status)""")
    db.execute("""CREATE TABLE IF NOT EXISTS voice_message_audio (
        id INTEGER PRIMARY KEY,
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        cache_id INTEGER NOT NULL REFERENCES voice_audio_cache(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL CHECK(sequence>=0),
        source_sha256 TEXT NOT NULL, profile_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(message_id,sequence))""")
    db.execute("""CREATE INDEX IF NOT EXISTS voice_message_audio_cache
        ON voice_message_audio(cache_id)""")


def profile(db: sqlite3.Connection, character_id: int) -> dict | None:
    row = db.execute("SELECT * FROM voice_profiles WHERE character_id=?", (character_id,)).fetchone()
    if row is None:
        return None
    value = dict(row)
    value["active"] = bool(value["active"])
    value["configuration"] = VoiceConfiguration.model_validate_json(value.pop("configuration_json")).model_dump()
    return value


def put_profile(db: sqlite3.Connection, character_id: int, value: ProfileInput) -> dict:
    db.execute("BEGIN IMMEDIATE")
    character = db.execute("SELECT id FROM characters WHERE id=?", (character_id,)).fetchone()
    if character is None:
        raise LookupError("角色不存在")
    old = db.execute("SELECT revision FROM voice_profiles WHERE character_id=?", (character_id,)).fetchone()
    revision = old[0] if old else 0
    if revision != value.expected_revision:
        raise ValueError("声音档案版本已变化，请刷新")
    moment = stamp()
    if old:
        db.execute("""UPDATE voice_profiles SET voice_id=?,active=?,revision=?,configuration_json=?,updated_at=?
            WHERE character_id=?""", (value.voice_id, int(value.active), revision+1,
            value.configuration.model_dump_json(), moment, character_id))
    else:
        db.execute("""INSERT INTO voice_profiles
            (character_id,provider,voice_id,active,revision,configuration_json,created_at,updated_at)
            VALUES (?,'elevenlabs',?, ?,1,?,?,?)""", (character_id,value.voice_id,int(value.active),
            value.configuration.model_dump_json(),moment,moment))
    return profile(db, character_id)


def consent(db: sqlite3.Connection, owner_id: int) -> dict | None:
    row = db.execute("SELECT * FROM voice_privacy_consents WHERE owner_id=? AND provider=?",
                     (owner_id, config.PROVIDER)).fetchone()
    return dict(row) if row else None


def put_consent(db: sqlite3.Connection, owner_id: int) -> dict:
    db.execute("""INSERT INTO voice_privacy_consents(owner_id,provider,notice_version,accepted_at)
        VALUES (?,?,?,?) ON CONFLICT(owner_id,provider) DO UPDATE SET
        notice_version=excluded.notice_version,accepted_at=excluded.accepted_at""",
        (owner_id,config.PROVIDER,config.NOTICE_VERSION,stamp()))
    return consent(db, owner_id)


def revoke_consent(db: sqlite3.Connection, owner_id: int) -> None:
    db.execute("DELETE FROM voice_privacy_consents WHERE owner_id=? AND provider=?",
               (owner_id,config.PROVIDER))
