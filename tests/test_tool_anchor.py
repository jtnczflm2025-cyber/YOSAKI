"""R2.1 synthetic tool-turn identity checks; run only with an isolated data directory."""

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_import_data = tempfile.TemporaryDirectory(prefix="yosaki-r21-import-")
os.environ["YOSAKI_DATA_DIR"] = _import_data.name
from backend.app import main as app_module  # noqa: E402


class ToolAnchor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yosaki-r21-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        app_module.DATA = root
        app_module.DB = root / "yosaki.sqlite3"
        app_module.SECRET_FILE = root / ".secret_key"
        app_module.init_db()
        with app_module.connect() as db:
            self.character = db.execute("SELECT id FROM characters ORDER BY id LIMIT 1").fetchone()[0]
            self.profile = db.execute("""INSERT INTO model_profiles(name,base_url,model_name,created_at,updated_at)
                VALUES('synthetic','http://127.0.0.1:1','synthetic','2026-01-01','2026-01-01')""").lastrowid

    def seed(self, batches=2, *, legacy=False):
        with app_module.connect() as db:
            conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'synthetic','2026-01-01','2026-01-01')""", (self.character,)).lastrowid
            old = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','older unrelated turn','2026-01-01')""", (conv,)).lastrowid
            origin = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(?,'user','remember 美咲','2026-01-01')""", (conv,)).lastrowid
            assistants, runs = [], []
            for n in range(batches):
                call = f"call-{n}"
                status = "awaiting_confirmation" if n == batches - 1 else "completed"
                assistant = db.execute("""INSERT INTO messages(conversation_id,role,content,tool_calls_json,
                    tool_batch_status,origin_user_message_id,origin_user_content_sha256,origin_character_id,created_at)
                    VALUES(?,'assistant','',?,?,?,?,?,'2026-01-01')""", (
                    conv, json.dumps([{"id":call,"type":"function","function":{"name":"synthetic"}}]),
                    status, None if legacy else origin,
                    None if legacy else app_module.tool_origin_fingerprint("remember 美咲"),
                    None if legacy else self.character)).lastrowid
                run = db.execute("""INSERT INTO tool_runs(server_id,tool_name,arguments,status,result,confirmed,
                    created_at,conversation_id,assistant_message_id,provider_call_id,model_profile_id)
                    VALUES(1,'synthetic','{}','success','synthetic-result',1,'2026-01-01',?,?,?,?)""",
                    (conv, assistant, call, self.profile)).lastrowid
                db.execute("""INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at)
                    VALUES(?,'tool','synthetic-result',?,'synthetic','2026-01-01')""", (conv, call))
                assistants.append(assistant)
                runs.append(run)
        return conv, old, origin, assistants, runs

    def check(self, run_id):
        with app_module.connect() as db:
            run = db.execute("SELECT * FROM tool_runs WHERE id=?", (run_id,)).fetchone()
            return app_module.verified_tool_turn(db, run)

    def test_three_batches_inherit_exact_user(self):
        conv, old, origin, assistants, runs = self.seed(3)
        _, _, current, _, topic = self.check(runs[-1])
        self.assertEqual(topic, "remember 美咲")
        self.assertEqual(current["origin_user_message_id"], origin)
        with app_module.connect() as db:
            identities = db.execute("""SELECT DISTINCT origin_user_message_id,origin_user_content_sha256,
                origin_character_id FROM messages WHERE id IN (?,?,?)""", assistants).fetchall()
            self.assertEqual(len(identities), 1)

    def test_deleted_or_edited_user_never_falls_back(self):
        for operation in ("DELETE FROM messages WHERE id=?", "UPDATE messages SET content='altered' WHERE id=?"):
            _, _, origin, _, runs = self.seed()
            with app_module.connect() as db:
                db.execute(operation, (origin,))
            with self.assertRaises(app_module.HTTPException) as caught:
                self.check(runs[-1])
            self.assertEqual(caught.exception.status_code, 409)

    def test_new_user_and_forged_or_cross_role_origin_reject(self):
        conv, old, origin, assistants, runs = self.seed()
        with app_module.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','new turn','2026-01-01')", (conv,))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])
        with app_module.connect() as db:
            other_character = db.execute("""INSERT INTO characters(name,subtitle,mood,system_prompt,greeting,created_at,updated_at)
                VALUES('other','','','','','2026-01-01','2026-01-01')""").lastrowid
            other_conv = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'other role','2026-01-01','2026-01-01')""", (other_character,)).lastrowid
            other_user = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','remember 美咲','2026-01-01')", (other_conv,)).lastrowid
            db.execute("UPDATE messages SET origin_user_message_id=? WHERE id=?", (other_user, assistants[-1]))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])
        with app_module.connect() as db:
            db.execute("DELETE FROM messages WHERE conversation_id=? AND role='user' AND id>?", (conv, origin))
            db.execute("UPDATE messages SET origin_user_message_id=? WHERE id=?", (old, assistants[-1]))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])
        with app_module.connect() as db:
            db.execute("UPDATE messages SET origin_user_message_id=?,origin_character_id=? WHERE id=?",
                       (origin, self.character + 1, assistants[-1]))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])
        with app_module.connect() as db:
            other = db.execute("""INSERT INTO conversations(character_id,title,created_at,updated_at)
                VALUES(?,'other','2026-01-01','2026-01-01')""", (self.character,)).lastrowid
            foreign = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','remember 美咲','2026-01-01')", (other,)).lastrowid
            db.execute("UPDATE messages SET origin_user_message_id=?,origin_character_id=? WHERE id=?",
                       (foreign, self.character, assistants[-1]))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])

    def test_call_result_and_assistant_mismatch_reject(self):
        _, _, _, assistants, runs = self.seed()
        with app_module.connect() as db:
            db.execute("UPDATE tool_runs SET provider_call_id='forged' WHERE id=?", (runs[-1],))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])
        with app_module.connect() as db:
            db.execute("UPDATE tool_runs SET provider_call_id='call-1' WHERE id=?", (runs[-1],))
            db.execute("UPDATE tool_runs SET assistant_message_id=? WHERE id=?", (assistants[0], runs[-1]))
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])

    def test_legacy_pending_fails_closed_and_migration_preserves_history(self):
        _, _, _, assistants, runs = self.seed(legacy=True)
        with self.assertRaises(app_module.HTTPException) as caught:
            self.check(runs[-1])
        self.assertEqual(caught.exception.status_code, 409)
        with app_module.connect() as db:
            for column in ("origin_user_message_id", "origin_user_content_sha256", "origin_character_id"):
                db.execute(f"ALTER TABLE messages DROP COLUMN {column}")
        app_module.init_db()
        app_module.init_db()
        with app_module.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (assistants[-1],)).fetchone()
            self.assertIsNone(row["origin_user_message_id"])
            self.assertEqual(row["tool_batch_status"], "awaiting_confirmation")
        with self.assertRaises(app_module.HTTPException):
            self.check(runs[-1])

    def test_backup_copy_and_role_deletion(self):
        _, _, origin, _, runs = self.seed()
        encrypted = app_module.build_encrypted_backup("synthetic-backup-password")
        db_bytes, _ = app_module.decrypt_backup(encrypted, "synthetic-backup-password")
        backup = sqlite3.connect(":memory:")
        try:
            backup.deserialize(db_bytes)
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT origin_user_message_id FROM messages WHERE tool_batch_status='awaiting_confirmation'").fetchone()[0], origin)
        finally:
            backup.close()
        with app_module.connect() as db:
            db.execute("INSERT INTO characters(name,subtitle,mood,system_prompt,greeting,created_at,updated_at) VALUES('other','','','','','2026-01-01','2026-01-01')")
        app_module.delete_character(self.character)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_runs WHERE id=?", (runs[-1],)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE id=?", (origin,)).fetchone()[0], 0)

    def test_atomic_double_continue_claim(self):
        _, _, _, assistants, runs = self.seed()
        def claim():
            try:
                return asyncio.run(app_module.continue_after_tool(runs[-1]))
            except app_module.HTTPException as exc:
                return exc
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(claim)
            second = executor.submit(claim)
            results = (first.result(), second.result())
        self.assertEqual(sum(isinstance(item, app_module.StreamingResponse) for item in results), 1)
        self.assertEqual(sum(isinstance(item, app_module.HTTPException) and item.status_code == 409 for item in results), 1)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT tool_batch_status FROM messages WHERE id=?", (assistants[-1],)).fetchone()[0], "continuing")

    def test_delete_after_claim_before_stream_rechecks_without_model(self):
        _, _, origin, assistants, runs = self.seed()
        response = asyncio.run(app_module.continue_after_tool(runs[-1]))
        with app_module.connect() as db:
            db.execute("DELETE FROM messages WHERE id=?", (origin,))
        async def drain():
            return "".join([part async for part in response.body_iterator])
        events = asyncio.run(drain())
        self.assertIn("原始用户消息缺失", events)
        self.assertNotIn("message_complete", events)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT tool_batch_status FROM messages WHERE id=?",
                (assistants[-1],)).fetchone()[0], "awaiting_confirmation")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0], 2)

    def test_app_mutations_cannot_cross_active_continue(self):
        conv, _, _, _, runs = self.seed()
        response = asyncio.run(app_module.continue_after_tool(runs[-1]))
        self.assertIsInstance(response, app_module.StreamingResponse)
        with app_module.connect() as db:
            db.execute("INSERT INTO characters(name,subtitle,mood,system_prompt,greeting,created_at,updated_at) VALUES('other','','','','','2026-01-01','2026-01-01')")
        actions = (
            lambda: app_module.delete_conversation(conv),
            lambda: app_module.delete_character(self.character),
            lambda: asyncio.run(app_module.regenerate_chat(app_module.ChatInput(
                character_id=self.character, conversation_id=conv, model_profile_id=self.profile))),
            lambda: asyncio.run(app_module.chat_stream(app_module.ChatInput(
                character_id=self.character, conversation_id=conv, model_profile_id=self.profile,
                message="conflicting user turn"))),
        )
        for action in actions:
            with self.assertRaises(app_module.HTTPException) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 409)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role='user'", (conv,)).fetchone()[0], 2)

    def test_regenerate_preserves_and_revokes_old_pending_batch(self):
        conv, _, _, assistants, runs = self.seed()
        old_run = None
        with app_module.connect() as db:
            old_run = db.execute("SELECT * FROM tool_runs WHERE id=?", (runs[-1],)).fetchone()
        payload = app_module.ChatInput(character_id=self.character, conversation_id=conv,
                                       model_profile_id=self.profile, message="")
        response = asyncio.run(app_module.regenerate_chat(payload))
        self.assertIsInstance(response, app_module.StreamingResponse)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM tool_runs WHERE id=?", (runs[-1],)).fetchone()[0], old_run['status'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE id=?", (assistants[-1],)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT hidden FROM chat_message_state WHERE message_id=?',(assistants[-1],)).fetchone()[0],1)
            with self.assertRaises(app_module.HTTPException):
                app_module.verified_tool_turn(db, old_run)


if __name__ == "__main__":
    unittest.main()
