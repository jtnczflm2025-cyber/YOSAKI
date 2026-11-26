"""Read optimizations retain live integrity, permissions and responsive HTTP."""
import asyncio
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

import httpx

import test_memory_v2_import_raw as import_fixture
import test_memory_v2_closeout as review_fixture
from test_memory_v2_import_raw import archive, app_module, importer, sources
from backend.app import memory_v2_dialogue_refinement as finite, memory_v2_derivation as derived, memory_v2_metadata as metadata


class IntegrityReuse(unittest.TestCase):
    def setUp(self):
        self.fixture = import_fixture.ImportRawTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with app_module.connect() as db:
            batch = importer.prepare(db, self.fixture.cid, [('test.json', archive([
                {'role':'user', 'content':'图册放在柜台。'},
                {'role':'assistant', 'content':'我记下了。'},
            ]))])['batchId']
            importer.confirm(db, batch, self.fixture.cid)
            result = importer.step(db, batch, self.fixture.cid)
            self.file = result['files'][0]['id']

    def test_receipt_change_invalidates_same_connection_cache(self):
        with app_module.connect() as db:
            cache = {}
            self.assertTrue(sources.verified_archive_file(db, self.file, cache))
            db.execute('UPDATE mv2_import_receipts SET ordinal=ordinal+10 WHERE file_id=?', (self.file,))
            self.assertFalse(sources.verified_archive_file(db, self.file, cache))

    def test_external_change_invalidates_cache_and_each_source_checks_access(self):
        with app_module.connect() as db:
            cache = {}
            self.assertTrue(sources.verified_archive_file(db, self.file, cache))
            mid = db.execute('SELECT message_id FROM mv2_import_receipts WHERE file_id=? LIMIT 1', (self.file,)).fetchone()[0]
            with patch('backend.app.chat_history.source_allowed', return_value=False):
                self.assertIsNone(sources.source(db, 'message', str(mid), cache))
            with sqlite3.connect(app_module.DB) as writer:
                writer.execute("UPDATE mv2_import_files SET status='paused' WHERE id=?", (self.file,))
            self.assertFalse(sources.verified_archive_file(db, self.file, cache))


class RefinementRead(unittest.TestCase):
    def test_source_reuse_rechecks_path_permissions_and_database_edits(self):
        from backend.app import memory_v2_read, chat_history
        fixture = review_fixture.Closeout('test_explicit_confirm_link_publish_read_and_no_review_inheritance')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with app_module.connect() as db:
            ref = metadata.read_draft(db, fixture.draft['entryId'], fixture.cid)['sourceRefs'][0]
            args = (db, fixture.cid, ref['source_kind'], ref['source_id'])
            with memory_v2_read.validation_scope(db):
                with patch.object(memory_v2_read, 'valid_source', wraps=memory_v2_read.valid_source) as check:
                    first = derived.live_source(*args)
                    self.assertIsNotNone(first)
                    self.assertEqual(derived.live_source(*args), first)
                    self.assertEqual(check.call_count, 1)
                token = chat_history.READ_SCOPE.set({'family': {int(first.stream_id)}, 'allowed': set()})
                try:
                    self.assertIsNone(derived.live_source(*args))
                finally:
                    chat_history.READ_SCOPE.reset(token)
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (fixture.cid,))
                self.assertIsNone(derived.live_source(*args))
                db.execute('UPDATE characters SET enabled=1 WHERE id=?', (fixture.cid,))
                self.assertIsNotNone(derived.live_source(*args))
                db.execute('UPDATE messages SET content=content||? WHERE id=?', (' 修订。', ref['source_id']))
                self.assertIsNone(derived.live_source(*args))
            self.assertIsNone(derived.live_source(*args))

    def test_whole_source_set_checked_once_and_later_edits_rejected(self):
        fixture = review_fixture.Closeout('test_explicit_confirm_link_publish_read_and_no_review_inheritance')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with app_module.connect() as db:
            version = finite.create(db, fixture.cid, 1, fixture.draft['entryId'], fixture.draft['versionId'])
            stored = derived._row(db, 'mv3_versions', version['id'])
            with patch.object(metadata, '_validate_sources', wraps=metadata._validate_sources) as check:
                self.assertIsNone(finite.invalid_reason(db, stored, fixture.cid))
                self.assertEqual(check.call_count, 1)
            source = derived._refs(db, version['id'])[0]
            db.execute('UPDATE messages SET content=content||? WHERE id=?', (' 已更正。', source['source_id']))
            self.assertIsNotNone(finite.invalid_reason(db, stored, fixture.cid))


class ResponsiveSession(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_for_session_does_not_block_health(self):
        entered, release = threading.Event(), threading.Event()
        def slow_session(_request):
            entered.set()
            release.wait(3)
            return None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url='http://test') as client:
            with patch.object(app_module, 'current_session', slow_session):
                protected = asyncio.create_task(client.get('/api/characters'))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    start = time.monotonic()
                    response = await asyncio.wait_for(client.get('/health'), 1)
                    self.assertEqual(response.status_code, 200)
                    self.assertLess(time.monotonic()-start, 1)
                    self.assertFalse(protected.done())
                finally:
                    release.set()
                    self.assertEqual((await protected).status_code, 401)
