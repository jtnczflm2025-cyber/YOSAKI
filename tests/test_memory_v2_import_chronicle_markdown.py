"""Synthetic H5-B H1 tests; never reads a private archive or imports RAW."""

import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import memory_v2_import as importer
from backend.app import memory_v2_import_api
from backend.app import memory_v2_import_markdown as markdown
from backend.app import memory_v2_import_preview as preview
from backend.app import memory_v2_import_schema as schema


def document(first="  如果明天不下雨（雨停以后），また会おう。\n\n",
             second="我说：うん。\n", first_time="2026-07-20 19:38",
             second_time="2026-07-19 17:37", second_role="阿夜"):
    return ("# 合成会话\n> 来源：合成\n\n---\n\n"
            f"**[用户]** _{first_time}_\n\n{first}---\n\n"
            f"**[{second_role}]** _{second_time}_\n\n{second}").encode("utf-8")


def empty_database():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE characters(id INTEGER PRIMARY KEY,enabled INTEGER)")
    db.execute("INSERT INTO characters VALUES(1,1)")
    db.execute("CREATE TABLE conversations(id INTEGER PRIMARY KEY,character_id INTEGER)")
    db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER)")
    db.execute("CREATE TABLE imported_archives(digest TEXT PRIMARY KEY)")
    schema.init_db(db)
    return db


class ChronicleMarkdownTests(unittest.TestCase):
    def test_roles_order_time_and_exact_body_slice(self):
        body = document()
        parsed = preview.parse_source("sample.md", body)
        self.assertEqual(parsed["parserVersion"], markdown.PARSER_VERSION)
        self.assertEqual([row["role"] for row in parsed["rows"]], ["user", "assistant"])
        self.assertEqual(parsed["rows"][0]["content"], "  如果明天不下雨（雨停以后），また会おう。\n\n")
        self.assertEqual(parsed["rows"][1]["content"], "我说：うん。\n")
        text = body.decode("utf-8")
        for row, location in zip(parsed["rows"], parsed["locations"]):
            self.assertEqual(row["content"], text[location["bodyStartChar"]:location["bodyEndChar"]])
        report = preview.preview_file("sample.md", body)
        self.assertEqual([item["ordinal"] for item in report["messages"]], [1, 2])
        self.assertEqual([item["time"]["original"] for item in report["messages"]],
                         ["2026-07-20 19:38", "2026-07-19 17:37"])
        self.assertTrue(all(item["time"]["occurredAt"] is None for item in report["messages"]))
        self.assertTrue(report["bodyLossless"])
        self.assertEqual(report["conversionNote"], "structural_header_and_separator_only")
        self.assertIsNone(report["externalConversationId"])
        self.assertTrue(all(item["externalMessageId"] is None for item in report["messages"]))

    def test_empty_body_multiple_newlines_and_marker_like_text(self):
        body = document(first="", second="第一段\n\n**[用户]** _2026-07-20 20:00_\n第二段\n")
        rows = preview.parse_source("sample.md", body)["rows"]
        self.assertEqual(rows[0]["content"], "")
        self.assertIn("**[用户]** _2026-07-20 20:00_\n", rows[1]["content"])
        self.assertEqual(rows[1]["content"], "第一段\n\n**[用户]** _2026-07-20 20:00_\n第二段\n")

    def test_invalid_time_role_and_ambiguous_separator_rejected(self):
        for body in (document(first_time="2026-99-20 19:38"),
                     document(second_role="未知"),
                     document(first="正文\n---\n不是有效边界\n"),
                     document(first="正文\n---\n\n**[用户]** 缺少时间\n")):
            with self.subTest(body=body[:50]), self.assertRaises(HTTPException):
                preview.preview_file("sample.md", body)

    def test_legacy_markdown_parser_behavior_unchanged(self):
        old = preview.preview_file("old.md", "# 合成\n## 用户 · 2024-05-06\n\n 内容 \n".encode())
        self.assertEqual(old["format"], "md")
        self.assertFalse(old["bodyLossless"])
        mixed = ("# 合成\n## 用户 · 2024-05-06\n\n正文\n---\n\n"
                 "**[阿夜]** _2026-07-20 19:38_\n").encode()
        self.assertEqual(preview.parse_source("old.md", mixed)["format"], "md")

    def test_preview_fingerprint_binds_source_parser_and_staged_snapshot(self):
        db = empty_database()
        try:
            original = document()
            report = preview.preview_file("sample.md", original, character_id=1)
            expected = preview.batch_preview_fingerprint([report])
            self.assertNotEqual(expected, preview.batch_preview_fingerprint(
                [preview.preview_file("sample.md", document(second="已修改\n"), character_id=1)]))
            with self.assertRaises(HTTPException):
                importer.prepare(db, 1, [("sample.md", document(second="已修改\n"))],
                                 expected_preview_fingerprint=expected)
            with self.assertRaises(HTTPException) as missing:
                importer.prepare(db, 1, [("sample.md", original)])
            self.assertEqual(missing.exception.status_code, 409)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_import_batches").fetchone()[0], 0)
            staged = importer.prepare(db, 1, [("sample.md", original)], expected_preview_fingerprint=expected)
            self.assertEqual(staged["previewFingerprint"], expected)
            batch_id = staged["batchId"]
            with self.assertRaises(HTTPException):
                importer.confirm(db, batch_id, 1, expected_version=1,
                                 expected_preview_fingerprint="0" * 64)
            db.rollback()
            with patch.object(markdown, "PARSER_VERSION", "chronicle-bracket-md-v2"):
                with self.assertRaises(HTTPException):
                    importer.confirm(db, batch_id, 1, expected_version=1,
                                     expected_preview_fingerprint=expected)
            db.rollback()
            file_id = db.execute("SELECT id FROM mv2_import_files WHERE batch_id=?", (batch_id,)).fetchone()[0]
            db.execute("UPDATE mv2_import_files SET title='已改变的预览标题' WHERE id=?", (file_id,))
            db.commit()
            with self.assertRaises(HTTPException):
                importer.confirm(db, batch_id, 1, expected_version=1,
                                 expected_preview_fingerprint=expected)
            db.rollback()
            db.execute("UPDATE mv2_import_files SET title='合成会话' WHERE id=?", (file_id,))
            db.commit()
            status = importer.confirm(db, batch_id, 1, expected_version=1,
                                      expected_preview_fingerprint=expected)
            self.assertEqual(status["status"], "confirmed")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv2_import_receipts").fetchone()[0], 0)
        finally:
            db.close()

    def test_isolated_api_requires_matching_preview_before_confirmation(self):
        db = empty_database()
        app = FastAPI()
        memory_v2_import_api.install(app, lambda: db,
                                     lambda request: request.headers.get("X-Test-Owner") == "yes")
        body = document()
        files = {"files": ("sample.md", body, "text/markdown")}
        headers = {"X-Test-Owner": "yes"}
        try:
            with TestClient(app) as client:
                preview_response = client.post("/api/memory-v2/imports/preview", headers=headers,
                                               data={"characterId": "1"}, files=files)
                self.assertEqual(preview_response.status_code, 200)
                fingerprint = preview_response.json()["previewFingerprint"]
                self.assertEqual(client.post("/api/memory-v2/imports/batches", headers=headers,
                                             data={"characterId": "1"}, files=files).status_code, 409)
                prepared = client.post("/api/memory-v2/imports/batches", headers=headers,
                                       data={"characterId": "1", "expectedPreviewFingerprint": fingerprint},
                                       files=files)
                self.assertEqual(prepared.status_code, 200)
                batch_id = prepared.json()["batchId"]
                endpoint = f"/api/memory-v2/imports/batches/{batch_id}/confirm"
                self.assertEqual(client.post(endpoint, headers=headers,
                                             params={"characterId": 1}).status_code, 409)
                confirmed = client.post(endpoint, headers=headers,
                                        params={"characterId": 1,
                                                "expectedVersion": 1,
                                                "expectedPreviewFingerprint": fingerprint})
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(confirmed.json()["status"], "confirmed")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
