"""Versioned batch punctuation and prepared-reserve release, synthetic only."""

import copy
import json
import sqlite3
import time
import threading
import unittest
import uuid
from pathlib import Path

from test_memory_v2_a34_qualifier_candidate import FIXTURE
import test_memory_v2_a34_qualifier_integration as qualifier_integration
from test_memory_v2_a34_responses_wiring import MODEL, PRICE, app_module, synopsis, trials, metadata
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_synopsis_final as final
from backend.app import memory_v2_synopsis_punctuation as punctuation
from backend.app import memory_v2_responses_adapter as responses


PUBLIC = json.loads((Path(__file__).parent / 'fixtures' /
                     'e2v2_rejected_public_replay.json').read_text(encoding='utf-8'))


class PunctuationAndReserve(unittest.TestCase):
    def setUp(self):
        # Reuse the existing in-repository synthetic H3.2 confirmation builder.
        self.ctx = qualifier_integration.VersionedFinal(
            'test_v2_business_path_accepts_rewrite_and_is_idempotent')
        self.ctx.setUp()
        self.addCleanup(self.ctx.doCleanups)
        self.cid, self.owner, self.entry = self.ctx.cid, self.ctx.owner, self.ctx.entry

    def task(self, *, old=False, budget=300_000):
        with app_module.connect() as db:
            creator = (synopsis.create_batch_handoff_trial if old else
                       synopsis.create_punctuation_batch_handoff_trial)
            return creator(db, self.entry, self.cid, self.owner, price=PRICE,
                           batch_input_cap=1200, max_microusd=budget)

    def batch_outputs(self, first=None):
        return [self.ctx.remap(first if first is not None else PUBLIC['output'],
                               self.ctx.batches[0]),
                self.ctx.remap(FIXTURE['parts'][1], self.ctx.batches[1])]

    def prepared(self, outputs, *, old=False, missing_usage=False):
        task = self.task(old=old)
        wire = qualifier_integration.FixtureWire(
            outputs, missing_usage=missing_usage)
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        return task, wire

    def rows(self, task):
        with app_module.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT * FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
                (task['jobId'],))]

    def budget(self, task):
        with app_module.connect() as db:
            return dict(trials.trial_status(db, task['trialId'], self.cid))

    def final_output(self):
        output = self.ctx.remap(FIXTURE['publicOutput'], self.ctx.sources)
        # The public first-batch replay cites source 9 as one exact RAW span,
        # while the earlier final fixture cites two adjoining spans.
        unit = output['units'][5]
        refs = unit['evidence']
        combined = self.ctx.remap(PUBLIC['output'], self.ctx.batches[0])[
            'units'][5]['evidence'][0]
        unit['evidence'] = [r for r in refs if r['id'] != combined['id']] + [combined]
        return output

    def test_public_replay_new_profile_handoffs_to_v2_review_field(self):
        task, wire = self.prepared(self.batch_outputs())
        with app_module.connect() as db:
            payload = json.loads(db.execute(
                'SELECT payload_json FROM mv3_jobs WHERE id=?',
                (task['jobId'],)).fetchone()[0])
            self.assertEqual(payload['ruleVersion'],
                             synopsis.REAL_PUNCTUATION_HANDOFF_PROFILE)
            self.assertEqual(payload['readSet']['punctuationVersion'], punctuation.VERSION)
            self.assertEqual(payload['readSet']['validator'], synopsis.PUNCTUATION_VALIDATOR)
        result = synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual((len(wire.counts), len(wire.generations)), (2, 2))
        rows = self.rows(task)
        raw = self.batch_outputs()[0]
        normalized = json.loads(rows[0]['output_json'])
        artifact = json.loads(rows[0]['warning_json'])
        self.assertEqual(artifact['rawOutput'], raw)
        self.assertEqual(artifact['rawHash'], synopsis._hash(raw))
        self.assertEqual(artifact['normalizedHash'], synopsis._hash(normalized))
        self.assertEqual(artifact['adjustments']['moves'][0]['codepoint'], 236)
        self.assertEqual((normalized['units'][5]['end'],
                          normalized['units'][6]['start'],
                          normalized['units'][6]['end']), (237, 237, 273))
        self.assertEqual(self.budget(task)['reserved_microusd'], 0)
        with app_module.connect() as db:
            follow = final.create_task_v2(db, self.entry, self.cid, self.owner,
                                          task['jobId'], model=MODEL, price=PRICE)
            dependency = json.loads(db.execute(
                'SELECT payload_json FROM mv3_jobs WHERE id=?',
                (follow['jobId'],)).fetchone()[0])['readSet']['batchDependency']
            self.assertEqual(dependency['punctuationVersion'], punctuation.VERSION)
            self.assertEqual(dependency['batches'][0]['outputHash'],
                             synopsis._hash(normalized))
            self.assertEqual(dependency['batches'][0]['normalizationHash'],
                             synopsis._hash(artifact))
        final_wire = qualifier_integration.FixtureWire([self.final_output()])
        final.prepare_task(app_module.connect, follow['jobId'], self.cid,
                           self.owner, final_wire.adapter)
        outcome = final.run_task(app_module.connect, follow['jobId'], self.cid,
                                 self.owner, final_wire.adapter)
        self.assertEqual(outcome['status'], 'completed', outcome)
        with app_module.connect() as db:
            field = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual((field['reviewStatus'], field['lifecycleStatus']),
                             ('needs_review', 'draft'))
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 0)
        self.assertTrue(final.run_task(
            app_module.connect, follow['jobId'], self.cid, self.owner,
            final_wire.adapter)['reused'])
        self.assertEqual(len(final_wire.generations), 1)

    def test_old_profile_still_rejects_public_offset_and_releases_unsent_second(self):
        task, wire = self.prepared(self.batch_outputs(), old=True)
        result = synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('synopsis_sentence_unit_required', result['reason'])
        self.assertEqual(len(wire.generations), 1)
        rows, budget = self.rows(task), self.budget(task)
        self.assertEqual(rows[0]['state'], 'rejected')
        self.assertEqual((rows[1]['state'], rows[1]['provider_status']),
                         ('rejected', 'not_dispatched'))
        self.assertIsNone(rows[1]['dispatch_attempt_id'])
        self.assertIsNone(rows[1]['used_input_tokens'])
        self.assertIsNone(rows[1]['settlement_hash'])
        self.assertEqual(budget['reserved_microusd'], 0)
        self.assertGreater(budget['spent_estimate_microusd'], 0)
        with app_module.connect() as db:
            self.assertIsNone(db.execute(
                'SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                (task['jobId'],)).fetchone())

    def test_unknown_first_keeps_own_reserve_but_releases_unsent_second(self):
        task, wire = self.prepared(self.batch_outputs(), missing_usage=True)
        before = self.budget(task)['reserved_microusd']
        outcome = synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(outcome['status'], 'unknown', outcome)
        rows, budget = self.rows(task), self.budget(task)
        self.assertEqual((rows[0]['state'], rows[1]['provider_status']),
                         ('unknown', 'not_dispatched'))
        self.assertEqual(budget['reserved_microusd'], rows[0]['reserved_usd_micros'])
        self.assertEqual(before - budget['reserved_microusd'],
                         rows[1]['reserved_usd_micros'])
        self.assertEqual(budget['spent_estimate_microusd'], 0)
        self.assertEqual(len(wire.generations), 1)
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(
                app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 1)

    def test_queued_cancel_is_terminal_and_idempotently_releases_both(self):
        task, wire = self.prepared(self.batch_outputs())
        with app_module.connect() as db:
            self.assertEqual(synopsis.cancel_real_trial(
                db, task['jobId'], self.cid, self.owner), 'cancelled')
        self.assertEqual(self.budget(task)['reserved_microusd'], 0)
        self.assertEqual(self.budget(task)['spent_estimate_microusd'], 0)
        self.assertTrue(all(r['provider_status'] == 'not_dispatched'
                            for r in self.rows(task)))
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(jobs.release_never_dispatched_trial_requests(
                db, task['jobId'], self.cid, task['trialId'],
                'synopsis_cancelled'), 0)
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(
                app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(wire.generations, [])

    def test_inflight_cancel_settles_first_and_releases_only_second(self):
        task = self.task()
        def cancel_during_generation(_body):
            with app_module.connect() as db:
                synopsis.cancel_real_trial(
                    db, task['jobId'], self.cid, self.owner)
        wire = qualifier_integration.FixtureWire(
            self.batch_outputs(), on_generation=cancel_during_generation)
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        outcome = synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertNotEqual(outcome['status'], 'completed')
        rows, budget = self.rows(task), self.budget(task)
        self.assertEqual(len(wire.generations), 1)
        self.assertEqual(rows[1]['provider_status'], 'not_dispatched')
        self.assertEqual(budget['reserved_microusd'], 0)
        self.assertGreater(budget['spent_estimate_microusd'], 0)
        self.assertIsNone(rows[0]['output_json'])

    def test_version_change_after_dispatch_still_settles_known_usage(self):
        task = self.task()
        def change_version(_body):
            with app_module.connect() as db:
                payload = json.loads(db.execute(
                    'SELECT payload_json FROM mv3_jobs WHERE id=?',
                    (task['jobId'],)).fetchone()[0])
                payload['readSet']['punctuationVersion'] = 'unknown-version'
                db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                           (json.dumps(payload, ensure_ascii=False), task['jobId']))
        wire = qualifier_integration.FixtureWire(
            self.batch_outputs(), on_generation=change_version)
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        outcome = synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertNotEqual(outcome['status'], 'completed', outcome)
        rows, budget = self.rows(task), self.budget(task)
        self.assertEqual(len(wire.generations), 1)
        self.assertGreater(budget['spent_estimate_microusd'], 0)
        self.assertIsNone(rows[0]['output_json'])
        self.assertEqual(rows[1]['provider_status'], 'prepared')
        self.assertEqual(budget['reserved_microusd'], rows[1]['reserved_usd_micros'])

    def test_old_and_new_batch_profiles_construct_identical_request(self):
        actual = {'phase': 'batch', 'sources': [{'id': 'synthetic',
                  'speaker': 'user', 'content': '合成原文。'}]}
        old = responses.synopsis_request_payload(
            MODEL, 'batch', actual, 6144,
            profile=synopsis.REAL_BATCH_HANDOFF_PROFILE)
        new = responses.synopsis_request_payload(
            MODEL, 'batch', actual, 6144,
            profile=synopsis.REAL_PUNCTUATION_HANDOFF_PROFILE)
        self.assertEqual(old, new)

    def test_invalid_evidence_remains_rejected_after_punctuation_adjustment(self):
        bad = copy.deepcopy(PUBLIC['output'])
        bad['units'][5]['evidence'][0]['quote'] = '不匹配的 RAW'
        task, wire = self.prepared(self.batch_outputs(bad))
        result = synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('synopsis_evidence_invalid', result['reason'])
        self.assertIsNone(self.rows(task)[0]['output_json'])

    def test_ambiguous_mark_and_substantive_interval_still_reject(self):
        for mode in ('ambiguous', 'substantive'):
            with self.subTest(mode=mode):
                output = copy.deepcopy(PUBLIC['output'])
                if mode == 'ambiguous':
                    at = 236
                    output['text'] = output['text'][:at] + '！' + output['text'][at+1:]
                else:
                    output['units'][6]['start'] = 235
                with self.assertRaises(ValueError):
                    normalized, _ = punctuation.normalize_terminal_boundaries_v1(output)
                    synopsis.validate_output(normalized, PUBLIC['sources'])

    def test_tampered_normalization_or_readset_blocks_final_and_read(self):
        task, wire = self.prepared(self.batch_outputs())
        self.assertEqual(synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner,
            wire.adapter)['status'], 'completed')
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE mv3_model_chunks SET warning_json=? '
                       'WHERE job_id=? AND ordinal=0',
                       ('{}', task['jobId']))
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError, 'synopsis_batch_output_invalid'):
                final.create_task_v2(db, self.entry, self.cid, self.owner,
                                     task['jobId'], model=MODEL, price=PRICE)
        with app_module.connect() as db:
            row = db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                             (task['jobId'],)).fetchone()
            payload = json.loads(row[0])
            payload['readSet']['punctuationVersion'] = 'unknown-v9'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                       (json.dumps(payload), task['jobId']))
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(
                app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)

    def test_unknown_new_rule_version_blocks_preflight_with_zero_send(self):
        task = self.task()
        wire = qualifier_integration.FixtureWire(self.batch_outputs())
        with app_module.connect() as db:
            payload = json.loads(db.execute(
                'SELECT payload_json FROM mv3_jobs WHERE id=?',
                (task['jobId'],)).fetchone()[0])
            payload['readSet']['punctuationVersion'] = 'unknown-v9'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                       (json.dumps(payload), task['jobId']))
        with self.assertRaises(ValueError):
            synopsis.prepare_real_trial(app_module.connect, task['jobId'],
                                        self.cid, self.owner, wire.adapter)
        self.assertEqual((wire.counts, wire.generations), ([], []))

    def test_durable_artifact_change_stales_completed_v2_field_at_read(self):
        task, wire = self.prepared(self.batch_outputs())
        self.assertEqual(synopsis.run_real_trial(
            app_module.connect, task['jobId'], self.cid, self.owner,
            wire.adapter)['status'], 'completed')
        with app_module.connect() as db:
            follow = final.create_task_v2(db, self.entry, self.cid, self.owner,
                                          task['jobId'], model=MODEL, price=PRICE)
        final_wire = qualifier_integration.FixtureWire([self.final_output()])
        final.prepare_task(app_module.connect, follow['jobId'], self.cid,
                           self.owner, final_wire.adapter)
        self.assertEqual(final.run_task(app_module.connect, follow['jobId'],
            self.cid, self.owner, final_wire.adapter)['status'], 'completed')
        with app_module.connect() as db:
            before = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual(before['lifecycleStatus'], 'draft')
            db.execute('UPDATE mv3_model_chunks SET warning_json=? '
                       'WHERE job_id=? AND ordinal=0',
                       ('{}', task['jobId']))
            after = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual(after['lifecycleStatus'], 'stale')
            self.assertIsNone(after['value'])

    def test_release_failure_rolls_back_and_preserves_other_reserves(self):
        task, _ = self.prepared(self.batch_outputs())
        before = self.budget(task)['reserved_microusd']
        with app_module.connect() as db:
            db.execute("UPDATE mv3_trial_budgets SET status='stopped' WHERE id=?",
                       (task['trialId'],))
            db.execute('''CREATE TRIGGER synthetic_release_failure
                BEFORE UPDATE OF reserved_microusd ON mv3_trial_budgets
                BEGIN SELECT RAISE(FAIL,'synthetic_release_failure'); END''')
        with self.assertRaises(sqlite3.DatabaseError):
            with app_module.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                jobs.release_never_dispatched_trial_requests(
                    db, task['jobId'], self.cid, task['trialId'], 'synthetic_stop')
        self.assertEqual(self.budget(task)['reserved_microusd'], before)
        self.assertTrue(all(r['state'] == 'prepared' for r in self.rows(task)))

    def test_hold_or_suspicious_attempt_cannot_release_prepared_request(self):
        task, _ = self.prepared(self.batch_outputs())
        before = self.budget(task)['reserved_microusd']
        with app_module.connect() as db:
            db.execute("UPDATE mv3_trial_budgets SET status='stopped' WHERE id=?",
                       (task['trialId'],))
            db.execute("INSERT INTO mv3_a3_migration_holds "
                       "(job_id,prior_status,reason,evidence_json) VALUES(?,?,?,?)",
                       (task['jobId'], 'queued', 'synthetic_hold', '{}'))
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'prepared_release_not_authorized'):
                jobs.release_never_dispatched_trial_requests(
                    db, task['jobId'], self.cid, task['trialId'], 'synthetic_stop')
            db.rollback()
        self.assertEqual(self.budget(task)['reserved_microusd'], before)
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM mv3_a3_migration_holds WHERE job_id=?',
                       (task['jobId'],))
            db.execute('UPDATE mv3_model_chunks SET dispatch_attempt_id=? '
                       'WHERE job_id=? AND ordinal=0',
                       (str(uuid.uuid4()), task['jobId']))
            with self.assertRaisesRegex(ValueError, 'prepared_release_identity_unproven'):
                jobs.release_never_dispatched_trial_requests(
                    db, task['jobId'], self.cid, task['trialId'], 'synthetic_stop')
            db.rollback()
        self.assertEqual(self.budget(task)['reserved_microusd'], before)

    def test_release_and_dispatch_cannot_both_win_for_same_request(self):
        task, _ = self.prepared(self.batch_outputs())
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE mv3_trial_budgets SET status='stopped' WHERE id=?",
                       (task['trialId'],))
            released = jobs.release_never_dispatched_trial_requests(
                db, task['jobId'], self.cid, task['trialId'], 'synthetic_stop')
            self.assertGreater(released, 0)
        with app_module.connect() as db:
            with self.assertRaises(ValueError):
                processing.dispatch(db, task['jobId'], self.cid, 0,
                                    original_owner_id=self.owner)

    def test_dispatch_first_remains_reserved_and_release_skips_it(self):
        task, _ = self.prepared(self.batch_outputs())
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE mv3_jobs SET status='running',lease_token='synthetic',"
                       'lease_until=? WHERE id=?', (time.time()+120, task['jobId']))
            processing.dispatch(db, task['jobId'], self.cid, 0,
                                original_owner_id=self.owner)
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',"
                       'dispatched_requests=1 WHERE id=?', (task['trialId'],))
            released = jobs.release_never_dispatched_trial_requests(
                db, task['jobId'], self.cid, task['trialId'], 'synthetic_stop')
            second = db.execute('SELECT reserved_usd_micros FROM mv3_model_chunks '
                                'WHERE job_id=? AND ordinal=1',
                                (task['jobId'],)).fetchone()
            self.assertEqual(released, second[0])
        rows = self.rows(task)
        self.assertEqual(rows[0]['state'], 'possibly_dispatched')
        self.assertEqual(rows[1]['provider_status'], 'not_dispatched')
        self.assertEqual(self.budget(task)['reserved_microusd'],
                         rows[0]['reserved_usd_micros'])

    def test_concurrent_release_and_dispatch_have_one_winner(self):
        task, _ = self.prepared(self.batch_outputs())
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE mv3_jobs SET status='running',lease_token='synthetic',"
                       'lease_until=? WHERE id=?', (time.time()+120, task['jobId']))
            db.execute("UPDATE mv3_trial_budgets SET status='stopped' WHERE id=?",
                       (task['trialId'],))
        barrier = threading.Barrier(2)
        outcomes = []
        def attempt(kind):
            try:
                barrier.wait(timeout=10)
                with app_module.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if kind == 'dispatch':
                        processing.dispatch(db, task['jobId'], self.cid, 0,
                                            original_owner_id=self.owner)
                    else:
                        jobs.release_never_dispatched_trial_requests(
                            db, task['jobId'], self.cid, task['trialId'],
                            'synthetic_stop')
                outcomes.append((kind, 'ok'))
            except ValueError:
                outcomes.append((kind, 'blocked'))
        workers = [threading.Thread(target=attempt, args=(name,))
                   for name in ('dispatch', 'release')]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive())
        first = self.rows(task)[0]
        self.assertIn(first['state'], ('possibly_dispatched', 'rejected'))
        if first['state'] == 'possibly_dispatched':
            self.assertEqual(first['provider_status'], 'possibly_dispatched')
            self.assertGreater(self.budget(task)['reserved_microusd'], 0)
        else:
            self.assertEqual(first['provider_status'], 'not_dispatched')
        self.assertEqual(len(outcomes), 2)


if __name__ == '__main__':
    unittest.main()
