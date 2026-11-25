"""Synthetic, read-only H1 import preview checks."""

import io
import json
import sqlite3
import unittest
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.memory_v2_import_preview import preview_file, preview_upload
from backend.app import memory_v2_import_api


def sample(messages):
    return json.dumps({"format": "yosaki-conversation", "version": 2,
                       "conversation": {"id": "old-chat-7", "title": "旧会话"},
                       "messages": messages}, ensure_ascii=False).encode()


class ImportPreviewTests(unittest.TestCase):
    def test_json_preserves_identity_order_and_time_precision(self):
        body = sample([
            {"id": "old-2", "role": "user", "content": "まだ？", "created_at": "2024-05-06T09:12:00+09:00"},
            {"id": "old-1", "role": "assistant", "content": "うん", "created_at": "2024-05-06"},
            {"role": "user", "content": "无时区", "created_at": "2024-05-06T10:00:00"},
            {"role": "tool", "content": "private", "created_at": None},
        ])
        view = preview_file("a.json", body)
        self.assertEqual(view["externalConversationId"], "old-chat-7")
        self.assertEqual([m["externalMessageId"] for m in view["messages"]], ["old-2", "old-1", None, None])
        self.assertEqual([m["ordinal"] for m in view["messages"]], [1, 2, 3, 4])
        self.assertEqual(view["messages"][0]["time"]["occurredAt"], "2024-05-06T09:12:00+09:00")
        self.assertEqual(view["messages"][1]["time"]["precision"], "day")
        self.assertIsNone(view["messages"][1]["time"]["occurredAt"])
        self.assertEqual(view["messages"][2]["time"]["precision"], "local_datetime_unverified")
        self.assertIsNone(view["messages"][2]["time"]["occurredAt"])
        self.assertFalse(view["messages"][3]["indexedTextEligible"])
        confirmed = preview_file("a.json", body, confirmed_offset_minutes=540)
        self.assertEqual(confirmed["messages"][2]["time"]["timezoneBasis"], "user_confirmed_offset")

    def test_markdown_is_explicitly_lossy(self):
        view = preview_file("a.md", "# 会话\n## 用户 · 2024-05-06\n\n 内容 \n".encode())
        self.assertFalse(view["lossless"])
        self.assertIn("有损格式", view["warnings"][0])
        self.assertEqual(view["messages"][0]["time"]["precision"], "day")

    def test_zip_accepts_only_bounded_supported_entries(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as packed:
            packed.writestr("chat/a.json", sample([{"role": "user", "content": "test"}]))
        self.assertEqual(len(preview_upload("a.zip", buf.getvalue())), 1)
        for name in ("../a.json", "inner.zip", "C:/a.json"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as packed:
                packed.writestr(name, sample([{"role": "user", "content": "test"}]))
            with self.subTest(name=name), self.assertRaises(HTTPException):
                preview_upload("a.zip", buf.getvalue())

    def test_invalid_identity_or_time_fails_closed(self):
        for message in ({"id": [], "role": "user", "content": "x"},
                        {"role": "user", "content": "x", "created_at": "2024-99-99"}):
            with self.subTest(message=message), self.assertRaises(HTTPException):
                preview_file("a.json", sample([message]))

    def test_reexported_legacy_import_time_is_not_promoted(self):
        view = preview_file("legacy.json", sample([{"role": "user", "content": "old",
            "created_at": "2024-05-06T12:00:00+08:00", "generation_status": "imported"}]))
        when = view["messages"][0]["time"]
        self.assertEqual((when["precision"], when["occurredAt"], when["occurredDate"]),
                         ("unknown", None, None))
        self.assertTrue(any("可能是导入时刻" in warning for warning in view["warnings"]))

    def test_installable_api_requires_owner_role_and_never_imports(self):
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER)")
        db.execute("INSERT INTO characters VALUES(1,1)")
        db.execute("CREATE TABLE imported_archives(digest TEXT PRIMARY KEY)")
        app = FastAPI()
        memory_v2_import_api.install(app, lambda: db, lambda request: request.headers.get("X-Test-Owner") == "yes")
        with TestClient(app) as client:
            body = sample([{"role": "user", "content": "合成内容"}])
            request = {"data": {"characterId": "1"}, "files": {"files": ("a.json", body, "application/json")}}
            self.assertEqual(client.post("/api/memory-v2/imports/preview", **request).status_code, 401)
            response = client.post("/api/memory-v2/imports/preview", headers={"X-Test-Owner": "yes"}, **request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertFalse(response.json()["confirmed"])
            self.assertEqual(response.json()["files"][0]["messages"][0]["contentPreview"], "合成内容")
            denied = client.post("/api/memory-v2/imports/preview", headers={"X-Test-Owner": "yes"},
                                 data={"characterId": "2"}, files=request["files"])
            self.assertEqual(denied.status_code, 404)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM imported_archives").fetchone()[0], 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
