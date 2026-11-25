"""M3.1 security and authenticated API acceptance on synthetic temporary data."""
import concurrent.futures

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_memory_v2_derivation import M3Base, app_module, derived, schema
from backend.app import memory_v2_derivation_api as api


class M31Security(M3Base):
    def test_legacy_schema_migration_does_not_grandfather_activation(self):
        with app_module.connect() as db:
            _, v = self.draft(db)
            db.execute("UPDATE mv3_versions SET status='active',review_status='auto_checked' WHERE id=?", (v['id'],))
            db.commit()
            for name in ('validation_status', 'validation_binding', 'edited_by'):
                db.execute(f'ALTER TABLE mv3_versions DROP COLUMN {name}')
            schema.init_db(db)
            schema.init_db(db)
            with self.assertRaisesRegex(ValueError, 'validation_binding_invalid'):
                derived.read_version(db, v['id'], self.cid)

    def draft(self, db, view="NARRATIVE", author="model", body="合成待审核文本", rule="manual-v1"):
        item = derived.create_derivation(db, self.cid, view, self.refs())
        return item, derived.add_version(db, item["id"], self.refs(), body, author_kind=author, rule_version=rule)

    def test_default_and_boolean_activation_denied(self):
        with app_module.connect() as db:
            for author in ("model", "user"):
                _, v = self.draft(db, author=author)
                with self.assertRaisesRegex(ValueError, "valid_review_required"):
                    derived.activate_version(db, v["id"], self.cid)
                with self.assertRaises(TypeError):
                    derived.activate_version(db, v["id"], self.cid, human_checked=True)
                self.assertEqual(derived._row(db, "mv3_versions", v["id"])["status"], "needs_review")

    def test_verified_identity_cleaning_only(self):
        with app_module.connect() as db:
            _, v = self.draft(db, "CLEANED", "rule", "今天累了。\n\n那就休息。", "identity-v1")
            result = derived.activate_version(db, v["id"], self.cid)
            self.assertEqual(result["validation_status"], "passed")
            self.assertEqual(result["review_status"], "unreviewed")
            self.assertIsNotNone(derived.read_version(db, v["id"], self.cid))

    def test_body_tamper_and_new_revision_do_not_inherit_review(self):
        with app_module.connect() as db:
            item, v = self.draft(db, author="user")
            self.approve(db, v["id"])
            revised = derived.add_version(db, item["id"], self.refs(), "新修订")
            with self.assertRaises(ValueError):
                derived.activate_version(db, revised["id"], self.cid)
            db.execute("UPDATE mv3_versions SET body='tampered' WHERE id=?", (v["id"],))
            with self.assertRaisesRegex(ValueError, "body_changed"):
                derived.read_version(db, v["id"], self.cid)

    def test_reviewed_source_change_and_double_activation(self):
        with app_module.connect() as db:
            _, v = self.draft(db)
            self.approve(db, v["id"])
            with self.assertRaisesRegex(ValueError, "version_not_activatable"):
                derived.activate_version(db, v["id"], self.cid)
            db.execute("DELETE FROM messages WHERE id=?", (self.user,))
            with self.assertRaises(ValueError):
                derived.read_version(db, v["id"], self.cid)

    def test_concurrent_activation_one_winner(self):
        with app_module.connect() as db:
            _, v = self.draft(db, "CLEANED", "rule", "今天累了。\n\n那就休息。", "identity-v1")
        def activate():
            try:
                with app_module.connect() as db:
                    derived.activate_version(db, v["id"], self.cid)
                return "ok"
            except ValueError:
                return "rejected"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: activate(), range(2))), ["ok", "rejected"])


class M31API(M31Security):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        api.install(app, app_module.connect, app_module.current_session)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with app_module.connect() as db:
            db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(1,'synthetic','unused','2026-01-01')")
            db.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,1,'2026-01-01','2099-01-01')",
                       (app_module.session_digest("synthetic-token"),))
        self.client.cookies.set(app_module.SESSION_COOKIE, "synthetic-token")

    def test_create_review_activate_history_sources_and_revoke(self):
        base = "/api/memory-v2/derivations"
        response = self.client.post(base, json={"characterId": self.cid, "viewType": "NARRATIVE", "sourceRefs": self.refs()})
        self.assertEqual(response.status_code, 201)
        item = response.json()
        root = base + "/" + item["id"]
        response = self.client.post(root + "/versions", json={"characterId": self.cid,
            "sourceRefs": self.refs(), "body": "合成待审核正文"})
        self.assertEqual(response.status_code, 201)
        v = response.json()
        path = root + "/versions/" + v["id"]
        query = {"characterId": self.cid}
        self.assertEqual(self.client.get(path, params=query).status_code, 409)
        preview = self.client.get(path + "/preview", params=query)
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.json()["eligibleForRecall"])
        self.assertEqual([x["source_id"] for x in preview.json()["sourceRefs"]], [str(self.user), str(self.assistant)])
        forged = self.client.post(path + "/review", json={"characterId": self.cid, "outputHash": v["output_hash"],
            "inputHash": v["input_hash"], "reviewerId": 1, "active": True})
        self.assertEqual(forged.status_code, 422)
        self.assertEqual(self.client.post(path + "/activate", params=query).status_code, 409)
        reviewed = self.client.post(path + "/review", json={"characterId": self.cid,
            "outputHash": v["output_hash"], "inputHash": v["input_hash"]})
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(self.client.post(path + "/activate", params=query).status_code, 200)
        self.assertEqual(self.client.get(path, params=query).status_code, 200)
        self.assertEqual(self.client.get(path + "/sources", params=query).status_code, 200)
        history = self.client.get(root + "/versions", params=query)
        self.assertNotIn("body", history.json()[0])
        self.assertEqual(self.client.get(path, params={"characterId": self.cid + 999}).status_code, 404)
        with app_module.connect() as db:
            db.execute("UPDATE characters SET enabled=0 WHERE id=?", (self.cid,))
        for suffix in ("", "/preview", "/sources"):
            blocked = self.client.get(path + suffix, params=query)
            self.assertIn(blocked.status_code, (404, 409))
            self.assertNotIn("合成待审核正文", blocked.text)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(root + "/versions", params=query).status_code, 401)

    def test_forged_status_and_cross_role_source(self):
        base = "/api/memory-v2/derivations"
        self.assertEqual(self.client.post(base, json={"characterId": self.cid, "viewType": "RAW",
            "sourceRefs": self.refs()}).status_code, 409)
        self.assertEqual(self.client.post(base, json={"characterId": self.cid, "viewType": "CLEANED",
            "sourceRefs": self.refs(), "status": "active", "reviewStatus": "auto_checked"}).status_code, 422)
        self.assertEqual(self.client.post(base, json={"characterId": self.cid + 99, "viewType": "CLEANED",
            "sourceRefs": self.refs()}).status_code, 409)

    def test_api_revision_requires_new_review_and_deleted_source_blocks_all_bodies(self):
        with app_module.connect() as db:
            item, v = self.draft(db, author='user')
            self.approve(db, v['id'])
        root = f'/api/memory-v2/derivations/{item["id"]}'
        response = self.client.post(root + '/versions', json={'characterId': self.cid,
            'sourceRefs': self.refs(), 'body': 'new synthetic revision'})
        self.assertEqual(response.status_code, 201)
        revised = response.json()
        self.assertNotEqual(v['id'], revised['id'])
        path = root + '/versions/' + revised['id']
        self.assertEqual(self.client.post(path + '/activate', params={'characterId': self.cid}).status_code, 409)
        with app_module.connect() as db:
            self.assertEqual(derived._row(db, 'mv3_versions', revised['id'])['edited_by'], 1)
            db.execute('DELETE FROM messages WHERE id=?', (self.user,))
        for version in (v, revised):
            for suffix in ('', '/preview', '/sources'):
                response = self.client.get(root + '/versions/' + version['id'] + suffix,
                                           params={'characterId': self.cid})
                self.assertEqual(response.status_code, 409)
                self.assertNotIn('body', response.json())
        history = self.client.get(root + '/versions', params={'characterId': self.cid})
        self.assertEqual(history.status_code, 200)
        self.assertTrue(all('body' not in row for row in history.json()))

    def test_review_snapshot_source_edit_and_api_history_rejection(self):
        with app_module.connect() as db:
            item, v = self.draft(db)
            derived.review_version(db, v["id"], self.cid, 1, v["output_hash"], v["input_hash"])
            db.execute("UPDATE messages SET content='changed synthetic source' WHERE id=?", (self.user,))
        path = f'/api/memory-v2/derivations/{item["id"]}/versions/{v["id"]}'
        for suffix in ("", "/preview", "/sources"):
            response = self.client.get(path + suffix, params={"characterId": self.cid})
            self.assertEqual(response.status_code, 409)
            self.assertNotIn(v["body"], response.text)
        self.assertEqual(self.client.post(path + '/activate', params={"characterId": self.cid}).status_code, 409)

    def test_cycle_and_stale_dependency(self):
        with app_module.connect() as db:
            a, av = self.draft(db, "CLEANED", "rule", "今天累了。\n\n那就休息。", "identity-v1")
            derived.activate_version(db, av["id"], self.cid)
            b = derived.create_derivation(db, self.cid, "NARRATIVE", self.refs())
            bv = derived.add_version(db, b["id"], self.refs(), 'synthetic narrative', child_version_ids=(av["id"],))
            self.approve(db, bv["id"])
            db.execute("INSERT INTO mv3_dependencies VALUES(?,0,?,?)", (av["id"], bv["id"], bv["output_hash"]))
            with self.assertRaisesRegex(ValueError, "cycle"):
                derived.read_version(db, bv["id"], self.cid)
            db.execute("DELETE FROM mv3_dependencies WHERE version_id=?", (av["id"],))
            db.execute("UPDATE mv3_versions SET status='stale' WHERE id=?", (av["id"],))
            with self.assertRaisesRegex(ValueError, "dependency_stale"):
                derived.read_version(db, bv["id"], self.cid)
