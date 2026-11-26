"""Offline proof that the frozen planning credential callback cannot refresh.

Fake credential records and fake model transport only; no local secret read.
The existing production source and historical frozen manifests stay unchanged.
"""
import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

import httpx

from backend.app import chatgpt_plan
from scripts import voice_annotation_planning as planning
from tests import test_voice_annotation_planning as fixtures
from tests.test_voice_annotation_wiring import ByteStream, frames


class NoRefreshBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.FrozenPlanning()
        await self.fixture.asyncSetUp()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    def fake_account(self, *, expired=True, scoped=True):
        return {"account": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
            "client_id": "synthetic-client", "expires_at": time.time() + (-10 if expired else 600),
            "scopes": ["chatgpt.tokens.use.direct"] if scoped else []}}

    async def call_entry(self, transport):
        f = self.fixture
        return await planning.execute(f.manifest, f.receipt, "0" * 64, planning.existing_plan_token,
            enabled=True, database=f.database, transport=transport, mock_directory=f.directory)

    async def denied_case(self, record):
        # _read is replaced before the real callback is entered: neither the
        # lambda that opens .secret_key nor the encrypted store is evaluated.
        with patch.dict(os.environ, {"YOSAKI_CHATGPT_PLAN_ENABLED": "1"}), \
             patch.object(chatgpt_plan, "_data_dir"), patch.object(chatgpt_plan, "_fernet"), \
             patch.object(chatgpt_plan, "refresh_lock", asyncio.Lock()), \
             patch.object(chatgpt_plan, "_read", return_value=record) as read, \
             patch.object(chatgpt_plan, "_write") as write, \
             patch.object(chatgpt_plan, "access_token", wraps=chatgpt_plan.access_token) as credential, \
             patch.object(httpx, "AsyncClient", side_effect=AssertionError("any HTTP client is forbidden")) as clients:
            result = await self.call_entry(httpx.MockTransport(lambda _: self.fail("model dispatched")))
        self.assertEqual(result["status"], "not_dispatched")
        self.assertEqual(result["safe_reason"], "planning_credential_unavailable_no_refresh")
        self.assertEqual(result["model_dispatch_attempts"], 0)
        self.assertEqual(result["tts_requests"], 0)
        self.assertEqual(clients.call_count, 0)  # OAuth AND model HTTP clients.
        self.assertEqual(read.call_count, 1)
        write.assert_not_called()
        credential.assert_awaited_once_with(allow_refresh=False)
        self.assertTrue((self.fixture.directory / "approval.json.used").exists())

    async def test_expired_credential_zero_refresh_zero_model_through_final_entry(self):
        await self.denied_case(self.fake_account())

    async def test_missing_scope_zero_refresh_zero_model_through_final_entry(self):
        await self.denied_case(self.fake_account(expired=False, scoped=False))

    async def test_missing_credential_zero_refresh_zero_model_through_final_entry(self):
        await self.denied_case({})

    async def test_valid_existing_credential_one_fake_model_and_no_oauth(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            self.assertEqual(str(request.url), self.fixture.manifest["endpoint"])
            self.assertEqual(json.loads(request.content), self.fixture.manifest["wire_body"])
            raw = {"schema_version": 1, "input_sha256": self.fixture.manifest["input_sha256"], "annotations": []}
            return httpx.Response(200, stream=ByteStream(frames(json.dumps(raw))))
        original = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            # Even an accidental OAuth branch cannot contact a real server.
            kwargs["transport"] = httpx.MockTransport(handler)
            kwargs.pop("proxy", None)
            kwargs["trust_env"] = False
            return original(*args, **kwargs)
        with patch.dict(os.environ, {"YOSAKI_CHATGPT_PLAN_ENABLED": "1"}), \
             patch.object(chatgpt_plan, "_data_dir"), patch.object(chatgpt_plan, "_fernet"), \
             patch.object(chatgpt_plan, "refresh_lock", asyncio.Lock()), \
             patch.object(chatgpt_plan, "_read", return_value=self.fake_account(expired=False)), \
             patch.object(chatgpt_plan, "_write") as write, \
             patch.object(httpx, "AsyncClient", side_effect=fake_client):
            result = await self.call_entry(httpx.MockTransport(handler))
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["model_dispatch_attempts"], 1)
        self.assertEqual(calls, [self.fixture.manifest["endpoint"]])
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
