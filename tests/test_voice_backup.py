import sqlite3
import unittest

from backend.app.voice.backup_policy import sanitize_voice_snapshot


class SnapshotPolicy(unittest.TestCase):
    def test_only_copy_changes_and_missing_tables(self):
        source = sqlite3.connect(":memory:")
        copy = sqlite3.connect(":memory:")
        try:
            source.executescript("""CREATE TABLE voice_profiles (id INTEGER); INSERT INTO voice_profiles VALUES(1);
            CREATE TABLE voice_privacy_consents (id INTEGER); INSERT INTO voice_privacy_consents VALUES(2);
            CREATE TABLE voice_audio_cache (id INTEGER); INSERT INTO voice_audio_cache VALUES(3);
            CREATE TABLE voice_message_audio (id INTEGER); INSERT INTO voice_message_audio VALUES(4);""")
            source.backup(copy)
            sanitize_voice_snapshot(copy)
            for table, expected in (("voice_profiles",1),("voice_privacy_consents",0),
                                    ("voice_audio_cache",0),("voice_message_audio",0)):
                self.assertEqual(copy.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],expected)
                self.assertEqual(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],1)
            empty = sqlite3.connect(":memory:")
            sanitize_voice_snapshot(empty)
            empty.close()
        finally:
            copy.close()
            source.close()


if __name__ == "__main__":
    unittest.main()
