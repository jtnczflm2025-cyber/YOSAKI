"""M5-A0 read-only calendar API acceptance with synthetic M1/M3/P1-A evidence."""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_time_projection import P2Base, app_module, derived, entries, p2
from backend.app import memory_v2_calendar_api as calendar_api
from backend.app import memory_v2_time_api as time_api


class CalendarA0(P2Base):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        time_api.install(app, app_module.connect, app_module.current_session)
        calendar_api.install(app, app_module.connect, app_module.current_session)
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with app_module.connect() as db:
            db.execute("""INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at)
                VALUES(?,1,'2026-01-01','2099-01-01')""", (app_module.session_digest("calendar-a0-token"),))
        self.client.cookies.set(app_module.SESSION_COOKIE, "calendar-a0-token")
        self.params = {"characterId": self.cid, "asOf": self.now.isoformat()}

    def test_only_read_routes_and_owner_session(self):
        routes = [route for route in self.app.routes if getattr(route, "path", "").startswith("/api/memory-v2/")]
        self.assertTrue(routes)
        self.assertTrue(all(route.methods == {"GET"} or
            route.path == "/api/memory-v2/calendar/projection-preview" and route.methods == {"POST"}
            for route in routes))
        response = self.client.get("/api/memory-v2/calendar/characters")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/memory-v2/calendar/characters").status_code, 401)
        self.assertEqual(self.client.get("/api/memory-v2/chronicle/tree", params=self.params).status_code, 401)

    def test_live_tree_digest_missing_draft_and_raw(self):
        with app_module.connect() as db:
            week = self.root(db, "L1")
            draft = p2.create_extractive_digest(db, self.cid, week["nodeId"], now=self.now)
        result = self.client.get("/api/memory-v2/chronicle/tree", params=self.params)
        self.assertEqual(result.status_code, 200)
        nodes = result.json()["nodes"]
        self.assertEqual({n["level"] for n in nodes}, {f"L{i}" for i in range(6)})
        self.assertEqual(next(n for n in nodes if n["level"] == "L1")["summaryStatus"], "stale_or_unreviewed")
        self.assertNotIn(draft["body"], result.text)
        with app_module.connect() as db:
            self.approve(db, draft["id"])
        current = self.client.get("/api/memory-v2/chronicle/tree", params=self.params).json()
        week = next(n for n in current["nodes"] if n["level"] == "L1")
        self.assertEqual(week["summaryStatus"], "available")
        opened = self.client.get("/api/memory-v2/chronicle/nodes/" + week["nodeId"], params=self.params)
        self.assertIn(draft["body"][:20], opened.json()["summaryPage"])
        raw = self.client.get(f"/api/memory-v2/chronicle/entries/{self.ids[365]}/raw/0",
                              params={"characterId": self.cid})
        self.assertEqual(raw.status_code, 200)
        self.assertIn("合成的第365天记录", raw.json()["text"])

    def test_entry_views_only_active_and_paged_with_version(self):
        item_id = self.ids[0]
        detail = self.client.get(f"/api/memory-v2/calendar/entries/{item_id}",
                                 params={"characterId": self.cid}).json()
        self.assertEqual(detail["views"]["CLEANED"]["status"], "missing")
        self.assertEqual(detail["views"]["NARRATIVE"]["status"], "available")
        with app_module.connect() as db:
            refs = detail["sourceRefs"]
            clean = derived.create_derivation(db, self.cid, "CLEANED", refs)
            version = derived.add_version(db, clean["id"], refs, "合成保守清理文本。")
        draft = self.client.get(f"/api/memory-v2/calendar/entries/{item_id}",
                                params={"characterId": self.cid}).json()
        self.assertEqual(draft["views"]["CLEANED"]["status"], "needs_review")
        blocked = self.client.get(f"/api/memory-v2/calendar/entries/{item_id}/views/CLEANED",
                                  params={"characterId": self.cid})
        self.assertEqual(blocked.status_code, 409)
        self.assertNotIn("合成保守清理文本", blocked.text)
        with app_module.connect() as db:
            self.approve(db, version["id"])
        path = f"/api/memory-v2/calendar/entries/{item_id}/views/CLEANED"
        first = self.client.get(path, params={"characterId": self.cid, "limit": 4}).json()
        self.assertEqual(first["text"], "合成保守")
        self.assertEqual(self.client.get(path, params={"characterId": self.cid,
            "offset": first["nextOffset"]}).status_code, 409)
        later = self.client.get(path, params={"characterId": self.cid, "offset": first["nextOffset"],
            "versionId": first["versionId"]}).json()
        self.assertEqual(first["text"] + later["text"], "合成保守清理文本。")

    def test_source_change_and_role_loss_never_return_old_body(self):
        item_id = self.ids[0]
        with app_module.connect() as db:
            source_id = entries.read_entry(db, item_id, self.cid)["sourceRefs"][0]["id"]
            db.execute("UPDATE messages SET content='已修改的合成来源。' WHERE id=?", (source_id,))
        detail = self.client.get(f"/api/memory-v2/calendar/entries/{item_id}",
                                 params={"characterId": self.cid})
        self.assertEqual(detail.status_code, 409)
        self.assertNotIn("第0天的合成记录", detail.text)
        self.assertEqual(self.client.get(f"/api/memory-v2/calendar/entries/{item_id}/views/NARRATIVE",
            params={"characterId": self.cid}).status_code, 409)
        listing = self.client.get("/api/memory-v2/calendar/entries", params=self.params).json()
        self.assertNotIn(item_id, [x["entryId"] for x in listing["items"]])
        with app_module.connect() as db:
            db.execute("UPDATE characters SET enabled=0 WHERE id=?", (self.cid,))
        self.assertEqual(self.client.get("/api/memory-v2/calendar/status",
            params={"characterId": self.cid}).status_code, 404)

    def test_search_date_cursor_and_unknown_time(self):
        listing = self.client.get("/api/memory-v2/calendar/entries",
            params={**self.params, "q": "第7天"}).json()
        self.assertEqual([x["entryId"] for x in listing["items"]], [self.ids[7]])
        dated = self.client.get("/api/memory-v2/calendar/entries", params={**self.params,
            "fromDate": (self.now - timedelta(days=7)).date().isoformat(),
            "toDate": self.now.date().isoformat()}).json()
        self.assertEqual({x["entryId"] for x in dated["items"]}, {self.ids[0], self.ids[7]})
        first = self.client.get("/api/memory-v2/calendar/entries",
            params={**self.params, "limit": 2}).json()
        self.assertIsNotNone(first["nextCursor"])
        with app_module.connect() as db:
            old = entries.read_entry(db, self.ids[0], self.cid)
            new = entries.add_version(db, self.ids[0], old["sourceRefs"], "新标题",
                old["keywords"], old["tags"], old["synopsis"], old["narrativeVersionId"], 1)
            self.approve_entry(db, new)
        self.assertEqual(self.client.get("/api/memory-v2/calendar/entries",
            params={**self.params, "limit": 2, "cursor": first["nextCursor"]}).status_code, 409)

    def test_gets_do_not_write_and_cross_role_entry_is_blocked(self):
        with app_module.connect() as db:
            other = db.execute("INSERT INTO characters(name,created_at,updated_at) VALUES('other','2026-01-01','2026-01-01')").lastrowid
        observer = sqlite3.connect(app_module.DB)
        try:
            before = observer.execute("PRAGMA data_version").fetchone()[0]
            self.assertEqual(self.client.get("/api/memory-v2/calendar/status",
                params={"characterId": self.cid}).status_code, 200)
            self.assertEqual(self.client.get("/api/memory-v2/calendar/entries",
                params=self.params).status_code, 200)
            self.assertEqual(self.client.get(f"/api/memory-v2/calendar/entries/{self.ids[0]}",
                params={"characterId": self.cid}).status_code, 200)
            self.assertEqual(observer.execute("PRAGMA data_version").fetchone()[0], before)
            self.assertEqual(self.client.get(f"/api/memory-v2/calendar/entries/{self.ids[0]}",
                params={"characterId": other}).status_code, 409)
        finally:
            observer.close()


if __name__ == "__main__":
    unittest.main()
