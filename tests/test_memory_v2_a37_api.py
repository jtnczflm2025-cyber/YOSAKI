"""Opt-in A3.7 API contract using synthetic owner and database."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_a33_lexical import LexicalBase
from test_memory_v2_entries import app_module
from backend.app import memory_v2_metadata_api as api

KEY = b'a37-synthetic-plan-signing-key-32-bytes-only'


class MaintenanceApi(LexicalBase):
    def client(self, *, identity=None, enabled=True):
        app = FastAPI()
        api.install(app, app_module.connect,
                    lambda request: {'id': 1} if identity is None else identity,
                    maintenance_enabled=enabled, maintenance_secret=KEY)
        return TestClient(app)

    def test_opt_in_routes_auth_and_no_store(self):
        self.assertEqual(self.client(enabled=False).get(
            '/api/memory-v2/maintenance/status', params={'characterId': self.cid}).status_code, 404)
        client = self.client()
        response = client.get('/api/memory-v2/maintenance/status',
                              params={'characterId': self.cid})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['lexical']['state'], 'missing')
        forbidden = client.get('/api/memory-v2/maintenance/status',
                               params={'characterId': self.cid + 99})
        self.assertEqual(forbidden.status_code, 404)
        self.assertNotIn('jobs', forbidden.text)
        unauthenticated = self.client(identity={}).get(
            '/api/memory-v2/maintenance/status', params={'characterId': self.cid})
        self.assertEqual(unauthenticated.status_code, 401)

    def test_explicit_rebuild_action_and_retry(self):
        client = self.client()
        plan = client.get('/api/memory-v2/maintenance/plan',
                          params={'characterId': self.cid})
        self.assertEqual(plan.status_code, 200, plan.text)
        token = plan.json()['lexical']['actionToken']
        payload = {'characterId': self.cid, 'action': 'lexical_rebuild',
                   'planToken': token, 'requestKey': plan.json()['lexical']['requestKey']}
        first = client.post('/api/memory-v2/maintenance/actions', json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['generation'], 1)
        second = client.post('/api/memory-v2/maintenance/actions', json=payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()['reused'])
        invalid = client.post('/api/memory-v2/maintenance/actions',
                              json={**payload, 'action': 'arbitrary_sql'})
        self.assertEqual(invalid.status_code, 422)
        wrong_origin = client.post('/api/memory-v2/maintenance/actions',
                                   headers={'Origin': 'https://not-allowed.example'},
                                   json=payload)
        self.assertEqual(wrong_origin.status_code, 403)

    def test_role_revocation_precedes_plan_and_action(self):
        client = self.client()
        plan = client.get('/api/memory-v2/maintenance/plan',
                          params={'characterId': self.cid}).json()
        with app_module.connect() as db:
            db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        denied = client.get('/api/memory-v2/maintenance/status',
                            params={'characterId': self.cid})
        self.assertEqual(denied.status_code, 404)
        self.assertNotIn('lexical', denied.text)
        action = client.post('/api/memory-v2/maintenance/actions', json={
            'characterId': self.cid, 'action': 'lexical_rebuild',
            'planToken': plan['lexical']['actionToken'],
            'requestKey': plan['lexical']['requestKey']})
        self.assertEqual(action.status_code, 404)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())

    def test_key_is_required_for_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'maintenance_signing_key_required'):
            api.install(FastAPI(), app_module.connect, lambda request: {'id': 1},
                        maintenance_enabled=True)

    def test_action_api_requires_exact_plan_key_before_index_write(self):
        client = self.client()
        offer = client.get('/api/memory-v2/maintenance/plan',
                           params={'characterId': self.cid}).json()['lexical']
        payload = {'characterId': self.cid, 'action': 'lexical_rebuild',
                   'planToken': offer['actionToken'], 'requestKey': offer['requestKey']}
        for change, expected in (({'requestKey': 'another-client-key'}, 409),
                                 ({'requestKey': offer['actionToken']}, 422),
                                 ({'planToken': offer['requestKey']}, 409),
                                 ({'requestKey': None}, 422)):
            with self.subTest(change=change):
                result = client.post('/api/memory-v2/maintenance/actions',
                                     json={**payload, **change})
                self.assertEqual(result.status_code, expected, result.text)
                with app_module.connect() as db:
                    self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_a3_audit').fetchone()[0], 0)
        without_key = dict(payload)
        del without_key['requestKey']
        self.assertEqual(client.post('/api/memory-v2/maintenance/actions',
                         json=without_key).status_code, 422)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())


if __name__ == '__main__':
    unittest.main()
