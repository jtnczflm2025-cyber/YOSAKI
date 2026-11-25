"""Operate only on a caller-owned, isolated SQLite snapshot connection."""
import sqlite3


def sanitize_voice_snapshot(snapshot_db: sqlite3.Connection, snapshot_time: str | None = None) -> None:
    names = {row[0] for row in snapshot_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("annotation_audio", "annotation_operations", "annotation_revisions", "annotation_plans",
                  "voice_ai_completion_receipts", "voice_auto_expression_decisions", "voice_expression_audio", "voice_message_audio",
                  "voice_audio_cache", "voice_privacy_consents",
                  "voice_auto_session_budget", "voice_auto_owner_day_budget",
                  "voice_scribe_attempts", "voice_scribe_owner_day_budget"):
        if table in names:
            snapshot_db.execute(f"DELETE FROM {table}")
    if "voice_scribe_owner_settings" in names:
        # Keep the user's numeric preference, but require a fresh opt-in after restore.
        snapshot_db.execute("UPDATE voice_scribe_owner_settings SET enabled=0")

    if "voice_ai_owner_settings" in names:
        snapshot_db.execute("UPDATE voice_ai_owner_settings SET enabled=0")
