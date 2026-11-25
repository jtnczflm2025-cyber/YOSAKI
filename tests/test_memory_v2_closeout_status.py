"""Current daily-price state is visible without authorizing or dispatching work."""
import os
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_chronicle_workspace as workspace
from backend.app import memory_v2_chronicle_schema as schema


class CloseoutStatus(EntryBase):
    base = '/api/memory-v2/workspace'

    def build(self, current=False, installed=True, authenticated=True, legacy=False):
        self.calls = []
        with app_module.connect() as db:
            schema.install(db)
        def provider(_):
            self.calls.append('unexpected-provider')
            raise AssertionError('A status read must not acquire a provider')
        api = FastAPI()
        options = {} if legacy else {'rate_status': lambda: {
            'ratesCurrent': current, 'ratesVerifiedDate': '2026-11-19',
            'utcDate': '2026-11-22', 'expiresAt': '2026-11-20T00:00:00+00:00'}}
        workspace.install(api, app_module.connect,
            lambda _: {'id': 1} if authenticated else None,
            provider_factory=provider if installed else None,
            semantic_factories=(provider, provider) if installed else None, **options)
        return TestClient(api)

    def snapshot(self):
        with app_module.connect() as db:
            return tuple(db.iterdump())

    def test_expired_chat_status_is_explicit_and_limits_unchanged(self):
        flags = {key: '1' for key in ('YOSAKI_CHRONICLE_WORKSPACE',
            'YOSAKI_CHRONICLE_CONTEXT', 'YOSAKI_CHRONICLE_CHAT_SUPPLEMENT')}
        flags['YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE'] = '2026-11-19'
        before = self.snapshot()
        with patch.dict(os.environ, flags), patch.object(app_module, 'chronicle_provider', create=True):
            result = app_module.chronicle_chat_supplement_status()
            self.assertFalse(result['enabled'])
            self.assertEqual(result['reason'], 'chronicle_daily_rates_verification_required')
            self.assertEqual(result['expiresAt'], '2026-11-20T00:00:00+00:00')
            self.assertEqual((result['maxMicrousd'], result['maxWaitSeconds']), (37000, 30))
            self.assertEqual(result['defaultWaitSeconds'],12)
            self.assertEqual(result['waitSecondsOptions'],[12,30])
            os.environ['YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE'] = datetime.now(timezone.utc).date().isoformat()
            self.assertTrue(app_module.chronicle_chat_supplement_enabled())
            os.environ['YOSAKI_CHRONICLE_CONTEXT'] = '0'
            self.assertFalse(app_module.chronicle_chat_supplement_enabled())
        self.assertEqual(before, self.snapshot())

    def test_expired_workspace_statuses_are_read_only(self):
        client = self.build()
        before = self.snapshot()
        for _ in range(2):
            for path in ('generation-status', 'semantic-supplement/status'):
                response = client.get(self.base+'/'+path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(response.json()['enabled'])
                self.assertEqual(response.json()['reason'], 'chronicle_daily_rates_verification_required')
            period = client.get(self.base+'/periods/task', params={'characterId': self.cid, 'nodeId': 'missing'})
            self.assertEqual(period.status_code, 200, period.text)
            self.assertEqual(period.json()['status'], 'chronicle_daily_rates_verification_required')
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.calls, [])

    def test_disabled_and_legacy_installers_preserve_contract(self):
        client = self.build(current=True, installed=False)
        self.assertEqual(client.get(self.base+'/generation-status').json()['reason'], 'paid_generation_not_enabled')
        self.assertEqual(client.get(self.base+'/semantic-supplement/status').json()['reason'], 'semantic_supplement_disabled')
        self.assertTrue(self.build(current=True).get(self.base+'/generation-status').json()['enabled'])
        self.assertTrue(self.build(legacy=True).get(self.base+'/generation-status').json()['enabled'])
        self.assertEqual(self.calls, [])

    def test_unauthenticated_status_does_not_expose_configuration(self):
        client = self.build(authenticated=False)
        before = self.snapshot()
        for path in ('generation-status', 'semantic-supplement/status', 'periods/task?characterId=1&nodeId=x'):
            response = client.get(self.base+'/'+path)
            self.assertEqual(response.status_code, 401, response.text)
            self.assertNotIn('ratesVerifiedDate', response.text)
        self.assertEqual(before, self.snapshot())
