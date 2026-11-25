import concurrent.futures
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from test_memory_v2_derivation import M3Base, app_module, derived
from backend.app import memory_v2_digest as digest, memory_v2_jobs as jobs


class DigestJobs(M3Base):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            jobs.init_db(db)

    def test_time_levels_unknown_imported_and_timezone(self):
        with app_module.connect() as db:
            source = derived.live_source(db, self.cid, 'message', str(self.user))
            now = datetime(2026, 10, 29, tzinfo=timezone.utc)
            for age, expected in ((0, 'L0'), (7, 'L1'), (30, 'L2'), (90, 'L3'), (180, 'L4'), (365, 'L5')):
                value = replace(source, occurred_at=(now - timedelta(days=age)).isoformat())
                self.assertEqual(digest.projection(value, now)['level'], expected)
            imported = replace(source, occurred_at=None, recorded_at=now.isoformat())
            self.assertEqual(digest.projection(imported, now)['timeBasis'], 'recorded_at')
            # A3.6 approved correction: storage time cannot grant event age.
            self.assertIsNone(digest.projection(imported, now)['level'])
            unknown = replace(source, occurred_at=None, recorded_at=None)
            self.assertIsNone(digest.projection(unknown, now)['level'])
            value = replace(source, occurred_at='2026-10-28T23:30:00+00:00')
            self.assertEqual(digest.projection(value, now)['period'], '2026-10-29')

    def test_dependencies_review_unfold_and_source_invalidation(self):
        with app_module.connect() as db:
            child = digest.create_digest(db, self.cid, self.refs()[:1], level='L1', period='2026-W01')
            with self.assertRaises(ValueError):
                derived.activate_version(db, child['id'], self.cid)
            self.approve(db, child['id'])
            parent = digest.create_digest(db, self.cid, self.refs(), level='L2', period='2026-01', child_version_ids=(child['id'],))
            self.approve(db, parent['id'])
            self.assertEqual(len(digest.unfold(db, parent['id'], self.cid)['children']), 1)
            db.execute('DELETE FROM messages WHERE id=?', (self.user,))
            with self.assertRaises(ValueError):
                digest.unfold(db, parent['id'], self.cid)
            self.assertEqual(derived.invalidate_stale(db, self.cid), 2)

    def test_job_idempotence_pause_recover_resume_and_no_activation(self):
        with app_module.connect() as db:
            job = jobs.enqueue(db, self.cid, 'NARRATIVE', self.refs())
            self.assertEqual(jobs.enqueue(db, self.cid, 'NARRATIVE', self.refs())['id'], job['id'])
            jobs.control(db, job['id'], self.cid, 'pause')
            jobs.control(db, job['id'], self.cid, 'resume')
            old_token = jobs.claim(db, job['id'], self.cid, now=0)
        with app_module.connect() as db:  # fresh connection simulates worker restart
            jobs.control(db, job['id'], self.cid, 'recover', now=61)
            jobs.control(db, job['id'], self.cid, 'resume')
            token = jobs.claim(db, job['id'], self.cid, now=62)
            with self.assertRaises(ValueError):
                jobs.execute_claimed(db, job['id'], self.cid, old_token, now=63)
            result = jobs.execute_claimed(db, job['id'], self.cid, token, now=63)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(derived._row(db, 'mv3_versions', result['versionId'])['status'], 'needs_review')
            with self.assertRaises(ValueError):
                jobs.claim(db, job['id'], self.cid)

    def test_budget_failure_cancellation_and_source_change(self):
        with app_module.connect() as db:
            job = jobs.enqueue(db, self.cid, 'DIGEST', self.refs(), level='L1', period='2026-W01', max_chars=1)
            token = jobs.claim(db, job['id'], self.cid, now=0)
            self.assertEqual(jobs.execute_claimed(db, job['id'], self.cid, token, now=1)['error'], 'digest_budget_exceeded')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            clean = jobs.enqueue(db, self.cid, 'CLEANED', self.refs())
            jobs.control(db, clean['id'], self.cid, 'cancel')
            with self.assertRaises(ValueError):
                jobs.claim(db, clean['id'], self.cid)
            narrative = jobs.enqueue(db, self.cid, 'NARRATIVE', self.refs())
            token = jobs.claim(db, narrative['id'], self.cid, now=0)
            db.execute("UPDATE messages SET content='changed' WHERE id=?", (self.user,))
            self.assertEqual(jobs.execute_claimed(db, narrative['id'], self.cid, token, now=1)['status'], 'failed')

    def test_concurrent_claim_role_guard_and_cascade(self):
        with app_module.connect() as db:
            job = jobs.enqueue(db, self.cid, 'CLEANED', self.refs())
        def claim():
            try:
                with app_module.connect() as db:
                    jobs.claim(db, job['id'], self.cid)
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: claim(), range(2))), [True, False])
        with app_module.connect() as db:
            with self.assertRaises(ValueError):
                jobs.owned(db, job['id'], self.cid + 100)
            db.execute('DELETE FROM characters WHERE id=?', (self.cid,))
            self.assertIsNone(derived._row(db, 'mv3_jobs', job['id']))

    def test_same_sources_lower_level_dependency_and_business_tables_unchanged(self):
        with app_module.connect() as db:
            # Auth fixture is established before the no-business-write measurement.
            db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(1,'synthetic','unused','2026-01-01')")
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not row[0].startswith(('mv3_', 'sqlite_'))]
            def fingerprints():
                return {table: derived._hash([tuple(row) for row in db.execute(f'SELECT * FROM "{table}"')]) for table in tables}
            before = fingerprints()
            child = digest.create_digest(db, self.cid, self.refs(), level='L1', period='2026-W01')
            self.approve(db, child['id'])
            parent = digest.create_digest(db, self.cid, self.refs(), level='L2', period='2026-01', child_version_ids=(child['id'],))
            self.approve(db, parent['id'])
            self.assertEqual(digest.unfold(db, parent['id'], self.cid)['children'][0]['versionId'], child['id'])
            with self.assertRaises(ValueError):
                digest.create_digest(db, self.cid, self.refs(), level='L1', period='2026-W02', child_version_ids=(parent['id'],))
            with self.assertRaises(ValueError):
                digest.create_digest(db, self.cid, self.refs(), level='L1', period='俺终于成长了')
            job = jobs.enqueue(db, self.cid, 'NARRATIVE', self.refs())
            token = jobs.claim(db, job['id'], self.cid)
            jobs.execute_claimed(db, job['id'], self.cid, token)
            self.assertEqual(before, fingerprints())
