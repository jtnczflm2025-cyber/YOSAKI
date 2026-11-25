"""Synthetic H3.4 title confirmation tests; never opens the retained H5-B work DB."""

import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_memory_v2_h32 as h32
from test_memory_v2_h32 import app_module, importer, metadata
from backend.app import memory_v2_h34 as h34
from backend.app import memory_v2_h34_api as h34_api
from backend.app import memory_v2_metadata_search as search


KEEP = {1, 3, 8, 10, 12}


class H34Contract(unittest.TestCase):
    def setUp(self):
        h32.H32Contract.setUp(self)
        with app_module.connect() as db:
            args = h32.H32Contract.arguments(self, db)
            established = importer.confirm_h32_drafts(
                db, self.batch, self.cid, self.file['id'],
                h32.H32Contract.segments(self), **args)
            self.plan = established['planHash']
            self.snapshot = established['sourceSnapshot']
            self.ids = [r['entryId'] for r in established['items']]
            db.execute('''INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at)
                VALUES('h34-synthetic-session',?,'2026-01-01','2099-01-01')''', (self.owner,))
            db.commit()
        app = FastAPI()
        h34_api.install(app, app_module.connect, self.authenticate)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.cookies = {'yosaki_session': 'synthetic-cookie'}

    def authenticate(self, request):
        if request.cookies.get('yosaki_session') == 'synthetic-cookie':
            return {'id': self.owner, 'token_hash': 'h34-synthetic-session'}
        return None

    def payload(self):
        with app_module.connect() as db:
            items = []
            for i, eid in enumerate(self.ids, 1):
                detail = metadata.read_draft(db, eid, self.cid)
                title = detail['fields']['title']
                items.append(dict(entryId=eid,
                    title=title['value'] if i in KEEP else f'确认主题{i}',
                    expectedVersion=detail['versionId'],
                    expectedFieldId=title['fieldId'],
                    expectedFieldToken=title['token']))
        return dict(characterId=self.cid, batchId=self.batch, fileId=self.file['id'],
            batchKey='synthetic-h34-once', expectedFileSha=self.file['file_sha256'],
            expectedParser=self.file['parser_version'], expectedSourceSnapshot=self.snapshot,
            expectedPlanHash=self.plan, items=items)

    def post(self, payload, **kwargs):
        return self.client.post('/api/memory-v2/h34/title-confirmations',
            json=payload, cookies=self.cookies, **kwargs)

    def test_exact_title_revisions_reviews_index_and_idempotency(self):
        payload = self.payload()
        with app_module.connect() as db:
            before = {eid: metadata.read_draft(db, eid, self.cid) for eid in self.ids}
            raw_terms = db.execute('SELECT COUNT(*) FROM mv2_terms').fetchone()[0]
        first = self.post(payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers['cache-control'], 'no-store')
        self.assertFalse(first.json()['reused'])
        second = self.post(payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()['reused'])
        self.assertEqual(first.json()['items'], second.json()['items'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_title_confirmation_batches').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 13)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0], 21)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_sets').fetchone()[0], 21)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv2_terms').fetchone()[0], raw_terms)
            for i, eid in enumerate(self.ids, 1):
                detail = metadata.read_draft(db, eid, self.cid)
                title = detail['fields']['title']
                self.assertEqual(title['value'], payload['items'][i - 1]['title'])
                self.assertEqual(title['completionStatus'], 'complete')
                self.assertEqual(title['validationStatus'], 'passed')
                self.assertEqual(title['reviewStatus'], 'human_checked')
                self.assertEqual(title['lifecycleStatus'], 'available')
                self.assertEqual(detail['sourceToken'], before[eid]['sourceToken'])
                self.assertEqual(detail['timeEvidenceToken'], before[eid]['timeEvidenceToken'])
                self.assertEqual(detail['timeToken'], before[eid]['timeToken'])
                self.assertFalse(detail['qualifications']['candidateRecall'])
                self.assertFalse(detail['qualifications']['formalContext'])
                for name in ('categories', 'tags', 'keywords', 'synopsis', 'narrative'):
                    self.assertEqual(detail['fields'][name]['completionStatus'], 'missing')
                row = db.execute('SELECT * FROM mv3_entry_fields WHERE id=?',
                                 (title['fieldId'],)).fetchone()
                self.assertEqual(row['provenance'],
                                 'system_suggested' if i in KEEP else 'user_corrected')
                self.assertEqual(title['fieldId'] == before[eid]['fields']['title']['fieldId'], i in KEEP)
                check = db.execute('SELECT * FROM mv3_entry_field_checks WHERE field_id=?',
                                   (title['fieldId'],)).fetchone()
                self.assertEqual((check['binding'], check['method'], check['reviewer_id']),
                                 (title['token'], 'owner', self.owner))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_metadata_terms WHERE entry_id=? AND field_name=?',
                    (eid, 'title')).fetchone()[0] > 0, True)
            old_hits = {r['entryId'] for r in search.search(db, self.cid, '建议', purpose='management')}
            new_hits = {r['entryId'] for r in search.search(db, self.cid, '确认', purpose='management')}
            self.assertEqual(old_hits, {self.ids[i-1] for i in KEEP})
            self.assertEqual(new_hits, {self.ids[i-1] for i in range(1, 14) if i not in KEEP})
            self.assertEqual(search.search(db, self.cid, '确认', purpose='candidate'), [])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_auth_forged_reviewer_revocation_and_conflicting_retry(self):
        payload = self.payload()
        unauth = self.client.post('/api/memory-v2/h34/title-confirmations', json=payload)
        self.assertEqual(unauth.status_code, 401)
        self.assertEqual(self.post({**payload, 'reviewerId': self.owner}).status_code, 422)
        self.assertEqual(self.post({**payload, 'reviewStatus': 'owner_reviewed'}).status_code, 422)
        self.assertEqual(self.post(payload, headers={'Origin': 'https://untrusted.example'}).status_code, 403)
        with app_module.connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash='h34-synthetic-session'")
        self.assertEqual(self.post(payload).status_code, 404)
        with app_module.connect() as db:
            db.execute('''INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at)
                VALUES('h34-synthetic-session',?,'2026-01-01','2099-01-01')''', (self.owner,))
        ok = self.post(payload)
        self.assertEqual(ok.status_code, 200, ok.text)
        altered = json.loads(json.dumps(payload))
        altered['items'][0]['title'] += '额外内容'
        self.assertEqual(self.post(altered).status_code, 409)
        self.assertEqual(self.post({**payload, 'characterId': self.cid + 999}).status_code, 404)

    def test_real_session_authenticator_with_synthetic_owner_login(self):
        payload = self.payload()
        password = 'SyntheticTitleOnly_2026!'
        with app_module.connect() as db:
            db.execute('UPDATE users SET password_hash=? WHERE id=?',
                       (app_module.password_hash(password), self.owner))
        app = FastAPI()
        app.add_api_route('/api/auth/login', app_module.auth_login, methods=['POST'])
        h34_api.install(app, app_module.connect, app_module.current_session)
        client = TestClient(app)
        self.assertEqual(client.post('/api/memory-v2/h34/title-confirmations',
                                     json=payload).status_code, 401)
        logged_in = client.post('/api/auth/login',
                                json={'username': 'owner', 'password': password})
        self.assertEqual(logged_in.status_code, 200, logged_in.text)
        accepted = client.post('/api/memory-v2/h34/title-confirmations', json=payload)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_title_confirmation_batches').fetchone()[0], 1)

    def test_source_change_and_disabled_role_reject_before_any_review(self):
        payload = self.payload()
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?', ('已改变的合成来源', 1))
        self.assertEqual(self.post(payload).status_code, 409)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 0)
            db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        self.assertEqual(self.post(payload).status_code, 404)

    def test_mid_batch_exception_rolls_back_all_revisions_reviews_and_terms(self):
        payload = self.payload()
        real_review = h34.metadata.review_field
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic_review_failure')
            return real_review(*args, **kwargs)

        with patch.object(h34.metadata, 'review_field', side_effect=fail_second):
            response = self.post(payload)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(calls, 2)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0], 13)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_sets').fetchone()[0], 13)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_title_confirmation_batches'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_metadata_terms'").fetchone())
            for eid in self.ids:
                self.assertEqual(metadata.read_draft(db, eid, self.cid)['fields']['title']['lifecycleStatus'], 'draft')
