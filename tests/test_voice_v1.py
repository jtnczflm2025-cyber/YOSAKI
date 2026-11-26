"""Voice V1 isolated contract checks; no provider network access."""
import importlib
from contextlib import closing
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


class VoiceV1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["YOSAKI_DATA_DIR"] = cls.tmp.name
        os.environ["YOSAKI_VOICE_ENABLED"] = "1"
        os.environ.pop("ELEVENLABS_API_KEY", None)
        if "backend.app.main" in sys.modules:
            cls.main = importlib.reload(sys.modules["backend.app.main"])
        else:
            cls.main = importlib.import_module("backend.app.main")
        cls.client = TestClient(cls.main.app)
        cls.client.post("/api/auth/setup", json={"username":"voice_owner","password":"voice-test-password-123"})

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.tmp.cleanup()

    def setUp(self):
        with self.main.connect() as db:
            db.execute("DELETE FROM voice_profiles")
            db.execute("DELETE FROM voice_privacy_consents")

    def create_profile(self):
        from backend.app.voice.schemas import ProfileInput
        from backend.app.voice.storage import put_profile
        with self.main.connect() as db:
            put_profile(db, 1, ProfileInput(expected_revision=0, voice_id="test_voice_1"))

    def test_privacy_profile_and_revision(self):
        state = self.client.get("/api/voice/status")
        self.assertEqual(state.status_code, 200)
        self.assertFalse(state.json()["api_key_configured"])
        self.assertFalse(state.json()["real_api_verified"])
        self.assertEqual(self.client.get("/api/voice/privacy").json()["accepted"], False)
        config = {"schema_version":1,"language":"auto"}
        payload = {"expected_revision":0,"voice_id":"test_voice_1","active":True,"configuration":config}
        self.assertEqual(self.client.put("/api/voice/profiles/1",json=payload).status_code,403)
        notice = self.client.get("/api/voice/privacy").json()["notice_version"]
        self.assertEqual(self.client.put("/api/voice/privacy/consent",json={"notice_version":notice,"accepted":True}).status_code,200)
        response = self.client.put("/api/voice/profiles/1",json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["revision"],1)
        self.assertEqual(self.client.put("/api/voice/profiles/1",json=payload).status_code,409)
        self.assertEqual(self.client.post("/api/voice/profiles/1/test").status_code,409)
        self.assertEqual(self.client.delete("/api/voice/privacy/consent").status_code,204)
        self.assertFalse(self.client.get("/api/voice/privacy").json()["accepted"])

    def test_disabled_service_allows_only_inactive_local_profile(self):
        payload = {"expected_revision": 0, "voice_id": "local_ivc_1", "active": False}
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"}), \
             patch("httpx.AsyncClient.request", side_effect=AssertionError("external HTTP request")) as http_request, \
             patch("websockets.connect", side_effect=AssertionError("external WebSocket connection")) as ws_connect:
            self.assertFalse(self.client.get("/api/voice/status").json()["enabled"])
            self.assertFalse(self.client.get("/api/voice/privacy").json()["accepted"])
            self.assertIsNone(self.client.get("/api/voice/profiles?character_id=1").json())
            rejected = self.client.put("/api/voice/profiles/1", json={**payload, "active": True})
            self.assertEqual(rejected.status_code, 409)
            created = self.client.put("/api/voice/profiles/1", json=payload)
            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(created.json()["character_id"], 1)
            self.assertEqual(created.json()["provider"], "elevenlabs")
            self.assertEqual(created.json()["voice_id"], "local_ivc_1")
            self.assertFalse(created.json()["active"])
            self.assertEqual(created.json()["revision"], 1)
            updated = self.client.put("/api/voice/profiles/1", json={
                "expected_revision": 1, "voice_id": "local_ivc_2", "active": False})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["revision"], 2)
            self.assertEqual(self.client.put("/api/voice/profiles/1", json=payload).status_code, 409)
            self.assertEqual(self.client.put("/api/voice/profiles/1", json={
                "expected_revision": 2, "voice_id": "local_ivc_3", "active": True}).status_code, 409)
            self.assertEqual(self.client.get("/api/voice/profiles?character_id=999999").status_code, 404)
            self.assertEqual(self.client.put("/api/voice/profiles/999999", json={
                "expected_revision": 0, "voice_id": "other", "active": False}).status_code, 404)
            self.assertEqual(self.client.post("/api/voice/profiles/1/test").status_code, 503)
            self.assertEqual(self.client.put("/api/voice/privacy/consent", json={
                "notice_version": "2026-11-08-v1", "accepted": True}).status_code, 503)
            self.assertEqual(TestClient(self.main.app).get("/api/voice/profiles?character_id=1").status_code, 401)
            self.assertEqual(self.client.put("/api/voice/profiles/1", headers={
                "Origin": "https://invalid.example"}, json={
                "expected_revision": 2, "voice_id": "other", "active": False}).status_code, 403)
            routes = {route.path for route in self.main.app.routes}
            self.assertNotIn("/api/voice/messages/{id}/speak", routes)
            self.assertNotIn("/api/voice/sessions", routes)
            self.assertNotIn("/api/voice/sessions/{id}/stream", routes)
            http_request.assert_not_called()
            ws_connect.assert_not_called()
        with self.main.connect() as db:
            stored = db.execute("SELECT voice_id,active,revision FROM voice_profiles WHERE character_id=1").fetchone()
            self.assertEqual(tuple(stored), ("local_ivc_2", 0, 2))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_privacy_consents").fetchone()[0], 0)

    def test_disabled_service_save_deactivates_existing_profile(self):
        from backend.app.voice.schemas import ProfileInput
        from backend.app.voice.storage import put_profile
        with self.main.connect() as db:
            put_profile(db, 1, ProfileInput(expected_revision=0, voice_id="prior_voice", active=True))
        with patch.dict(os.environ, {"YOSAKI_VOICE_ENABLED": "0"}):
            saved = self.client.put("/api/voice/profiles/1", json={
                "expected_revision": 1, "voice_id": "new_voice", "active": False})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertFalse(saved.json()["active"])
            self.assertEqual(saved.json()["revision"], 2)
            self.assertFalse(self.client.get("/api/voice/profiles?character_id=1").json()["active"])

    def test_schema_and_role_isolation(self):
        self.create_profile()
        with self.main.connect() as db:
            self.main.init_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_profiles").fetchone()[0],1)
        self.assertEqual(self.client.get("/api/voice/profiles?character_id=999999").status_code,404)
        self.assertEqual(self.client.put("/api/voice/profiles/1",json={"expected_revision":1,"voice_id":"ok","configuration":{"unknown":1}}).status_code,422)

    def test_auth_and_origin(self):
        self.assertEqual(TestClient(self.main.app).get("/api/voice/status").status_code,401)
        self.assertEqual(self.client.put("/api/voice/privacy/consent",headers={"Origin":"https://invalid.example"},
                        json={"notice_version":"2026-11-08-v1","accepted":True}).status_code,403)

    def test_mock_provider_errors_are_redacted(self):
        import asyncio
        from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter, ProviderError
        def handler(request):
            self.assertEqual(request.headers["xi-api-key"], "fake-secret")
            return httpx.Response(401, json={"detail":"fake-secret"})
        provider = ElevenLabsAdapter("fake-secret", httpx.MockTransport(handler))
        with self.assertRaises(ProviderError) as caught:
            asyncio.run(provider.models())
        self.assertNotIn("fake-secret",str(caught.exception))

    def test_bounded_ttd_probe_closes_mock_socket(self):
        import asyncio
        from backend.app.voice.elevenlabs_adapter import bounded_ttd_idle_probe
        class Socket:
            def __init__(self): self.messages=[]; self.closed=False
            async def send(self, message): self.messages.append(message)
            async def ping(self):
                future=asyncio.get_running_loop().create_future()
                future.set_result(None)
                return future
            async def close(self): self.closed=True
        socket=Socket()
        with self.assertRaises(ValueError):
            asyncio.run(bounded_ttd_idle_probe(socket,duration_seconds=56))
        self.assertFalse(socket.closed)
        asyncio.run(bounded_ttd_idle_probe(socket,duration_seconds=0.04,keepalive_seconds=0.01))
        self.assertTrue(socket.closed)
        self.assertTrue(socket.messages)
        self.assertTrue(all(message == '{"keep_alive": true}' for message in socket.messages))
        class Broken(Socket):
            async def send(self, message): raise OSError("synthetic disconnect")
        broken=Broken()
        with self.assertRaises(OSError):
            asyncio.run(bounded_ttd_idle_probe(broken,duration_seconds=0.04,keepalive_seconds=0.01))
        self.assertTrue(broken.closed)

    def test_role_copy_does_not_inherit_and_delete_cascades(self):
        self.create_profile()
        duplicate = self.client.post("/api/characters/1/duplicate")
        self.assertEqual(duplicate.status_code,201,duplicate.text)
        copy_id = duplicate.json()["id"]
        self.assertIsNone(self.client.get(f"/api/voice/profiles?character_id={copy_id}").json())
        saved = self.client.put(f"/api/voice/profiles/{copy_id}",json={
            "expected_revision":0,"voice_id":"copy_voice","active":False})
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(self.client.delete(f"/api/characters/{copy_id}").status_code,204)
        with self.main.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_profiles WHERE character_id=?",(copy_id,)).fetchone()[0],0)

    def test_both_backup_paths_exclude_consent_and_keep_profile(self):
        from backend.app import backup_v2
        self.create_profile()
        with self.main.connect() as db:
            storage = __import__("backend.app.voice.storage", fromlist=["put_consent"])
            storage.put_consent(db, 1)
        password = "voice-backup-test-password"
        raw = self.main.build_encrypted_backup(password)
        restored, _ = self.main.decrypt_backup(raw, password)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            v1 = root / "v1.sqlite3"
            v1.write_bytes(restored)
            with closing(sqlite3.connect(v1)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_profiles").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_privacy_consents").fetchone()[0],0)
            v2 = root / "v2.ybackup"
            backup_v2.create_backup(self.main.DB,v2,password,root / "work")
            backup_v2.restore_to_directory(v2,password,root / "restore",root / "work")
            with closing(sqlite3.connect(root / "restore" / "yosaki.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_profiles").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_privacy_consents").fetchone()[0],0)
        with self.main.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_privacy_consents").fetchone()[0],1)


if __name__ == "__main__":
    unittest.main()
