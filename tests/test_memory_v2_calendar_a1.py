"""M5-A1 isolated calendar projection and digest display contracts."""

import sqlite3
from datetime import timedelta

from test_memory_v2_calendar_a0 import CalendarA0
from test_memory_v2_time_projection import app_module, entries, p2


class CalendarA1(CalendarA0):
    def preview(self, ids, **overrides):
        payload = {"characterId": self.cid, "entryIds": ids,
                   "asOf": self.now.isoformat(), "offsetMinutes": 480,
                   "budgetTokens": 512, "maxBlocks": 5, **overrides}
        return self.client.post("/api/memory-v2/calendar/projection-preview", json=payload)

    def test_preview_matches_p2_six_levels_and_does_not_write(self):
        observer = sqlite3.connect(app_module.DB)
        try:
            before = observer.execute("PRAGMA data_version").fetchone()[0]
            for age in (0, 7, 30, 90, 180, 365):
                response = self.preview([self.ids[age]])
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                with app_module.connect() as db:
                    original = p2.context_projection(db, self.cid, [self.ids[age]], now=self.now,
                        offset_minutes=480, budget_tokens=512, max_blocks=5)
                self.assertEqual([{k: block[k] for k in original["blocks"][0]}
                    for block in result["blocks"]], original["blocks"])
                self.assertEqual(result["selectionDiagnostics"][0]["level"], f"L{(0, 7, 30, 90, 180, 365).index(age)}")
                self.assertEqual(result["selectionDiagnostics"][0]["eligibility"], "current_validated")
                self.assertEqual(result["selectedCount"], 1)
                self.assertEqual(result["uniqueExperienceCount"], 1)
                self.assertTrue(result["projectionOnly"])
                self.assertFalse(result["formalModelReceived"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(observer.execute("PRAGMA data_version").fetchone()[0], before)
        finally:
            observer.close()

    def test_approved_digest_display_separates_body_and_source_metadata(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            digest = self.approved_digest(db, weekly["nodeId"])
        params = {**self.params, "limit": 5}
        path = "/api/memory-v2/calendar/nodes/" + weekly["nodeId"] + "/digest-display"
        first = self.client.get(path, params=params)
        self.assertEqual(first.status_code, 200, first.text)
        data = first.json()
        self.assertEqual(data["mode"], "extractive")
        self.assertEqual(data["label"], "摘录式摘要")
        self.assertNotIn("[message:", str(data["sections"]))
        self.assertEqual(data["sourceMeta"][0]["sourceRef"]["kind"], "message")
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get(path, params={**params, "offset": data["nextOffset"]}).status_code, 409)
        following = self.client.get(path, params={**params, "offset": data["nextOffset"],
            "digestVersionId": digest["id"]})
        self.assertEqual(following.status_code, 200, following.text)
        self.assertEqual("".join(s["text"] for s in data["sections"] + following.json()["sections"]),
                         "合成的第7天记录。"[:10])
        self.assertNotIn("[message:", data["text"])
        self.assertNotIn("[message:", following.json()["text"])

    def test_draft_digest_and_stale_source_cannot_leak(self):
        with app_module.connect() as db:
            weekly = self.root(db, "L1")
            draft = p2.create_extractive_digest(db, self.cid, weekly["nodeId"], now=self.now)
        path = "/api/memory-v2/calendar/nodes/" + weekly["nodeId"] + "/digest-display"
        pending = self.client.get(path, params=self.params)
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["mode"], "unavailable")
        self.assertNotIn(draft["body"], pending.text)
        with app_module.connect() as db:
            self.approve(db, draft["id"])
        self.assertEqual(self.client.get(path, params=self.params).json()["mode"], "extractive")
        with app_module.connect() as db:
            ref = entries.read_entry(db, self.ids[7], self.cid)["sourceRefs"][0]
            db.execute("UPDATE messages SET content='来源现已改动' WHERE id=?", (ref["id"],))
        stale = self.client.get(path, params=self.params)
        self.assertEqual(stale.status_code, 409)
        self.assertNotIn("合成的第7天记录", stale.text)
        preview = self.preview([self.ids[7]])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["blocks"], [])
        self.assertEqual(preview.json()["selectionDiagnostics"][0]["eligibility"], "missing_or_stale")
        self.assertNotIn("第7天的单经历梗概", preview.text)

    def test_selection_budget_dedup_and_asof_change(self):
        ids = [self.ids[0], self.ids[0], self.ids[7]]
        result = self.preview(ids, budgetTokens=1).json()
        self.assertEqual(result["selectedCount"], 3)
        self.assertEqual(result["uniqueExperienceCount"], 2)
        self.assertTrue(result["rejected"])
        self.assertLessEqual(result["estimatedTokens"], 1)
        future = self.preview([self.ids[0]], asOf=(self.now + timedelta(days=365)).isoformat())
        self.assertEqual(future.status_code, 200)
        self.assertEqual(future.json()["selectionDiagnostics"][0]["level"], "L5")

    def test_preview_owner_role_and_input_guard(self):
        self.client.cookies.clear()
        self.assertEqual(self.preview([self.ids[0]]).status_code, 401)
        self.client.cookies.set(app_module.SESSION_COOKIE, "calendar-a0-token")
        self.assertEqual(self.preview([self.ids[0]], characterId=self.cid + 1).status_code, 404)
        self.assertEqual(self.preview([self.ids[0]], budgetTokens=4097).status_code, 422)
        self.assertEqual(self.preview([], budgetTokens=512).status_code, 422)


if __name__ == "__main__":
    import unittest
    unittest.main()
