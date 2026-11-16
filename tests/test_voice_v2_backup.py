"""Exercise both real backup entry points against a fresh synthetic database."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


_CHILD = r'''
import hashlib
import os
from pathlib import Path
import sqlite3

from backend.app import main, backup_v2
from backend.app.voice import auto_budget, storage

base = Path(os.environ['YOSAKI_DATA_DIR'])
source = main.DATA / 'yosaki.sqlite3'
audio = base / 'voice' / 'audio' / 'aa'
audio.mkdir(parents=True)
(audio / ('a' * 64 + '.mp3')).write_bytes(b'synthetic-mp3-not-played')
(audio / ('b' * 64 + '.mp3.part')).write_bytes(b'synthetic-part')
with main.connect() as db:
    db.execute("INSERT INTO users(username,password_hash,created_at) VALUES('owner','synthetic-hash','2026-01-01')")
    owner = db.execute('SELECT id FROM users').fetchone()[0]
    auto_budget.init_db(db)
    auto_budget.init_daily_db(db)
    db.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES ('synthetic-session',?,'2026-01-01','2099-01-01')", (owner,))
    db.execute("""INSERT INTO voice_auto_session_budget
        (session_hash,owner_id,synth_jobs,request_count,character_count,created_at,updated_at)
        VALUES ('synthetic-session',?,1,1,20,'2026-01-01','2026-01-01')""", (owner,))
    db.execute("""INSERT INTO voice_auto_owner_day_budget
        (owner_id,local_day,synth_jobs,request_count,character_count,created_at,updated_at)
        VALUES (?,'2026-01-01',1,1,20,'2026-01-01','2026-01-01')""", (owner,))
    db.execute("""INSERT INTO voice_auto_owner_settings
        (owner_id,synth_jobs,request_count,character_count,revision,created_at,updated_at)
        VALUES (?,3,3,300,1,'2026-01-01','2026-01-01')""", (owner,))
    character = db.execute('SELECT id FROM characters LIMIT 1').fetchone()[0]
    db.execute('INSERT INTO conversations(character_id,created_at,updated_at) VALUES(?,?,?)',
               (character,'2026-01-01','2026-01-01'))
    conversation = db.execute('SELECT id FROM conversations LIMIT 1').fetchone()[0]
    db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
               (conversation,'assistant','合成公开文字。','2026-01-01'))
    message = db.execute('SELECT id FROM messages LIMIT 1').fetchone()[0]
    db.execute("INSERT INTO voice_profiles VALUES (?,'elevenlabs','synthetic-voice',0,1,'{\"schema_version\":1,\"model_standard\":\"eleven_v4\",\"model_realtime\":\"eleven_v4_turbo\",\"model_stt\":\"scribe_v2_realtime\",\"language\":\"ja\",\"personality\":\"\",\"audio_format\":\"mp3_44100_128\"}','2026-01-01','2026-01-01')", (character,))
    db.execute("INSERT INTO voice_privacy_consents VALUES (?,'elevenlabs','v1','2026-01-01')", (owner,))
    db.execute("""INSERT INTO voice_audio_cache
        (character_id,cache_key,voice_hash,model_id,output_format,language_code,text_hash,
         planner_version,profile_revision,status,attempt_id,created_at,expires_at,last_accessed_at,
         relative_path,audio_sha256,audio_bytes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (character,'synthetic-key','hash','eleven_v4','mp3_44100_128','ja','text-hash',
         'test',1,'ready','attempt','2026-01-01','2099-01-01','2026-01-01',
         'aa/'+'a'*64+'.mp3','audio-hash',25))
    cache = db.execute('SELECT id FROM voice_audio_cache').fetchone()[0]
    db.execute("""INSERT INTO voice_message_audio
        (message_id,cache_id,sequence,source_sha256,profile_revision,created_at)
        VALUES(?,?,?,?,?,?)""", (message,cache,0,'source',1,'2026-01-01'))

def inventory(path):
    with sqlite3.connect(path) as db:
        return {name: db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                for name in ('users','characters','conversations','messages','voice_profiles',
                             'voice_privacy_consents','voice_audio_cache','voice_message_audio',
                             'voice_auto_session_budget','voice_auto_owner_day_budget',
                             'voice_auto_owner_settings')}

before = inventory(source)
assert before['voice_audio_cache'] == before['voice_message_audio'] == 1
source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
password = 'synthetic-backup-password'

v1 = main.build_encrypted_backup(password)
payload, _ = main.decrypt_backup(v1,password)
v1_path = base / 'v1-restored.sqlite3'
v1_path.write_bytes(payload)

v2_path = base / 'synthetic-v2.ybackup'
manifest = backup_v2.create_backup(source,v2_path,password,base/'work')
restored = base / 'v2-restored'
receipt = backup_v2.restore_to_directory(v2_path,password,restored,base/'work')
assert receipt['database_sha256'] == manifest['database_sha256']

for path in (v1_path, restored/'yosaki.sqlite3'):
    counts = inventory(path)
    for name in ('users','characters','conversations','messages','voice_profiles',
                 'voice_auto_owner_settings'):
        assert counts[name] == before[name], (path,name,counts[name])
    for name in ('voice_privacy_consents','voice_audio_cache','voice_message_audio',
                 'voice_auto_session_budget','voice_auto_owner_day_budget'):
        assert counts[name] == 0, (path,name,counts[name])
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
    assert not (path.parent/'voice'/'audio').exists() if path.parent != base else True

assert inventory(source) == before
assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
assert len(list(audio.iterdir())) == 2
'''


class VoiceV2Backup(unittest.TestCase):
    def test_real_v1_and_v2_snapshot_paths_preserve_source(self):
        with tempfile.TemporaryDirectory(prefix="yosaki-v2a-backup-") as root:
            env = os.environ.copy()
            env.update({"YOSAKI_DATA_DIR": root, "YOSAKI_VOICE_ENABLED": "1",
                        "YOSAKI_BACKGROUND_TASKS_ENABLED": "0"})
            result = subprocess.run([sys.executable, "-c", _CHILD], cwd=Path(__file__).resolve().parents[1],
                                    env=env, text=True, capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
