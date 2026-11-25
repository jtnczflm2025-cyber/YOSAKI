"""Generic final-only Synopsis task; all sources and transport are synthetic."""
import json
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx

from test_memory_v2_a34_responses_wiring import (FakeWire, MODEL, PRICE, app_module,
                                                  synopsis, trials, metadata)
import test_memory_v2_h32 as h32
from test_memory_v2_h32 import importer
from backend.app import memory_v2_synopsis_final as final
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_metadata_jobs as metadata_jobs
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_processing as processing
from backend.app.memory_v2_model_adapter import MockSynopsisAdapter
from test_memory_v2_a34_synopsis import mock_output


class FinalWire:
    def __init__(self, *, on_generation=None, missing_usage=False,
                 invalid=False, timeout=False):
        self.counts = []
        self.generations = []
        self.on_generation = on_generation
        self.missing_usage = missing_usage
        self.invalid = invalid
        self.timeout = timeout
        self.adapter = responses.ResponsesAdapter(
            model=MODEL, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        body = json.loads(request.content)
        if request.url.path == '/v1/responses/input_tokens':
            self.counts.append(body)
            return httpx.Response(200, json={'input_tokens': 500})
        self.generations.append(body)
        if self.on_generation:
            self.on_generation(body)
        if self.timeout:
            raise httpx.ReadTimeout('synthetic timeout')
        actual = json.loads(body['input'][0]['content'])
        evidence = [part['units'][0]['evidence'][0] for part in actual['parts']]
        text = '两批合成消息被重新概括。'
        output = {'text': text,
                  'units': [{'start': 0, 'end': len(text), 'evidence': evidence}],
                  'sourceCoverage': [
                      {'kind': ref['kind'], 'id': ref['id'],
                       'used': (ref['kind'], ref['id']) in
                       {(r['kind'], r['id']) for r in evidence}}
                      for ref in actual['sourceManifest']]}
        if self.invalid:
            output['units'] = []
        answer = {'id': 'resp_'+str(uuid.uuid4()), 'model': MODEL,
                  'status': 'completed', 'service_tier': 'default',
                  'output': [{'type': 'message', 'content': [
                      {'type': 'output_text', 'text': json.dumps(output, ensure_ascii=False)}]}]}
        if not self.missing_usage:
            answer['usage'] = {'input_tokens': 500, 'output_tokens': 100}
        return httpx.Response(200, json=answer)


class GenericFinal(unittest.TestCase):
    def setUp(self):
        self.fixture = h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cid = self.fixture.cid
        self.owner = self.fixture.owner
        with app_module.connect() as db:
            result = importer.confirm_h32_drafts(
                db, self.fixture.batch, self.cid, self.fixture.file['id'],
                self.fixture.segments(), **self.fixture.arguments(db))
            self.entries = [item['entryId'] for item in result['items']]
            metadata_jobs.init_db(db)
            trials.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)
        self.source_wire = FakeWire(on_generation=self._reject_source_final)
        with app_module.connect() as db:
            self.source = synopsis.create_real_trial(
                db, self.entries[1], self.cid, self.owner, model=MODEL, price=PRICE,
                batch_input_cap=1400)
        synopsis.prepare_real_trial(app_module.connect, self.source['jobId'],
                                    self.cid, self.owner, self.source_wire.adapter)
        result = synopsis.run_real_trial(app_module.connect, self.source['jobId'],
                                         self.cid, self.owner, self.source_wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        with app_module.connect() as db:
            rows = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=? '
                              'ORDER BY ordinal', (self.source['jobId'],)).fetchall()
        self.assertEqual([r['state'] for r in rows], ['completed', 'completed', 'rejected'])

    def _reject_source_final(self, request):
        self.source_wire.bad_schema = json.loads(request['input'][0]['content'])['phase'] == 'final'

    def make(self):
        with app_module.connect() as db:
            return final.create_task(db, self.entries[1], self.cid, self.owner,
                                     self.source['jobId'], model=MODEL, price=PRICE)

    def test_generic_final_has_new_identity_budget_and_review_only_output(self):
        wire = FinalWire()
        task = self.make()
        self.assertNotEqual(task['jobId'], self.source['jobId'])
        self.assertNotEqual(task['trialId'], self.source['trialId'])
        prepared = final.prepare_task(app_module.connect, task['jobId'], self.cid,
                                      self.owner, wire.adapter)
        self.assertEqual((prepared['countRequests'], prepared['generationRequests']), (1, 1))
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual((len(wire.counts), len(wire.generations)), (1, 1))
        self.assertEqual(wire.counts[0], responses.count_payload(wire.generations[0]))
        actual = json.loads(wire.generations[0]['input'][0]['content'])
        self.assertEqual(actual['phase'], 'final')
        self.assertEqual(len(actual['parts']), 2)
        self.assertNotIn('sources', actual)
        with app_module.connect() as db:
            trial = trials.trial_status(db, task['trialId'], self.cid)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            field = metadata.read_draft(db, self.entries[1], self.cid)['fields']['synopsis']
            self.assertEqual((field['reviewStatus'], field['lifecycleStatus']),
                             ('needs_review', 'draft'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 0)
        again = final.run_task(app_module.connect, task['jobId'], self.cid,
                               self.owner, wire.adapter)
        self.assertTrue(again['reused'])
        self.assertEqual(len(wire.generations), 1)

    def test_missing_or_unknown_batch_blocks_without_final_request(self):
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE mv3_model_chunks SET output_json=NULL WHERE job_id=? AND ordinal=1',
                       (self.source['jobId'],))
            with self.assertRaisesRegex(ValueError, 'synopsis_batch_not_reliably_settled'):
                final.create_task(db, self.entries[1], self.cid, self.owner,
                                  self.source['jobId'], model=MODEL, price=PRICE)

    def test_unknown_final_keeps_reservation_and_does_not_retry(self):
        wire = FinalWire(timeout=True)
        task = self.make()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        outcome = final.run_task(app_module.connect, task['jobId'], self.cid,
                                 self.owner, wire.adapter)
        self.assertEqual(outcome['status'], 'unknown')
        with app_module.connect() as db:
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'reserved_microusd'], 0)
        with self.assertRaises(ValueError):
            final.run_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 1)

    def test_source_edit_after_prepare_prevents_dispatch(self):
        wire = FinalWire()
        task = self.make()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=? AND ordinal=0',
                       ('{}', self.source['jobId']))
        with self.assertRaises(ValueError):
            final.run_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 0)

    def test_invalid_final_settles_usage_without_field(self):
        wire = FinalWire(invalid=True)
        task = self.make()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        with app_module.connect() as db:
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)
            self.assertIsNone(db.execute('SELECT result_version_id FROM mv3_jobs WHERE id=?',
                                         (task['jobId'],)).fetchone()[0])

    def test_repeated_create_reuses_same_trial_and_not_old_source_spend(self):
        first = self.make()
        second = self.make()
        self.assertEqual(first['jobId'], second['jobId'])
        self.assertTrue(second['reused'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_trial_budgets').fetchone()[0], 2)
            source_spent = trials.trial_status(db, self.source['trialId'], self.cid)[
                'spent_estimate_microusd']
            self.assertGreater(source_spent, 0)
            self.assertEqual(trials.trial_status(db, first['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)

    def test_cancel_after_dispatch_settles_usage_without_candidate(self):
        task = self.make()
        def cancel(_request):
            with app_module.connect() as db:
                final.cancel_task(db, task['jobId'], self.cid, self.owner)
        wire = FinalWire(on_generation=cancel)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertNotEqual(result['status'], 'completed')
        with app_module.connect() as db:
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)
            self.assertIsNone(db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                                         (task['jobId'],)).fetchone())

    def test_revoked_role_settles_only_original_usage(self):
        task = self.make()
        def revoke(_request):
            with app_module.connect() as db:
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        wire = FinalWire(on_generation=revoke)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertNotEqual(result['status'], 'completed')
        with app_module.connect() as db:
            row = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=?',
                             (task['jobId'],)).fetchone()
            self.assertEqual(row['state'], 'rejected')
            self.assertIsNone(row['output_json'])
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)

    def test_expired_original_worker_settles_but_cannot_write(self):
        task = self.make()
        def expire(_request):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (task['jobId'],))
        wire = FinalWire(on_generation=expire)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertNotEqual(result['status'], 'completed')
        with app_module.connect() as db:
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)
            self.assertIsNone(db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                                         (task['jobId'],)).fetchone())

    def test_source_change_during_generation_settles_but_rejects_output(self):
        task = self.make()
        def replace(_request):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=? AND ordinal=0',
                           ('{}', self.source['jobId']))
        wire = FinalWire(on_generation=replace)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        with app_module.connect() as db:
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)
            self.assertIsNone(db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                                         (task['jobId'],)).fetchone())

    def test_output_storage_failure_settles_in_independent_transaction(self):
        task = self.make()
        wire = FinalWire()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute("""CREATE TRIGGER synthetic_final_output_failure BEFORE UPDATE OF output_json
                ON mv3_model_chunks WHEN NEW.job_id='""" + task['jobId'] + """'
                BEGIN SELECT RAISE(FAIL,'synthetic_output_write_failed'); END""")
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        with app_module.connect() as db:
            trial = trials.trial_status(db, task['trialId'], self.cid)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            row = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=?',
                             (task['jobId'],)).fetchone()
            self.assertEqual(row['state'], 'rejected')
            self.assertIsNone(row['output_json'])
        with self.assertRaises(ValueError):
            final.run_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 1)

    def test_diagnostic_sink_failure_cannot_rollback_known_usage(self):
        task = self.make()
        wire = FinalWire(invalid=True)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        def broken(_record):
            raise OSError('synthetic_diagnostic_failure')
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter, diagnostic_sink=broken)
        self.assertFalse(result['diagnosticStored'])
        with app_module.connect() as db:
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)

    def test_batch_replacement_makes_completed_candidate_stale_at_read(self):
        task = self.make()
        wire = FinalWire()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(final.run_task(app_module.connect, task['jobId'], self.cid,
                                        self.owner, wire.adapter)['status'], 'completed')
        with app_module.connect() as db:
            before = metadata.read_draft(db, self.entries[1], self.cid)['fields']['synopsis']
            self.assertEqual(before['lifecycleStatus'], 'draft')
            db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=? AND ordinal=0',
                       ('{}', self.source['jobId']))
            after = metadata.read_draft(db, self.entries[1], self.cid)['fields']['synopsis']
            self.assertEqual(after['lifecycleStatus'], 'stale')
            self.assertIsNone(after['value'])

    def test_low_budget_rejects_before_generation(self):
        with app_module.connect() as db:
            task = final.create_task(db, self.entries[1], self.cid, self.owner,
                                     self.source['jobId'], model=MODEL, price=PRICE,
                                     max_microusd=1)
        wire = FinalWire()
        with self.assertRaisesRegex(ValueError, 'trial_budget_exceeded'):
            final.prepare_task(app_module.connect, task['jobId'], self.cid,
                               self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 0)

    def test_other_entry_source_cannot_be_mixed(self):
        with app_module.connect() as db:
            with self.assertRaisesRegex(ValueError, 'synopsis_source_job_invalid'):
                final.create_task(db, self.entries[0], self.cid, self.owner,
                                  self.source['jobId'], model=MODEL, price=PRICE)

    def test_unsettled_and_missing_proof_batch_both_block(self):
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                                  (self.source['jobId'],)).fetchone()
            db.execute('UPDATE mv3_model_chunks SET settlement_hash=NULL WHERE job_id=? AND ordinal=0',
                       (self.source['jobId'],))
            with self.assertRaisesRegex(ValueError, 'synopsis_batch_not_reliably_settled'):
                final.create_task(db, self.entries[1], self.cid, self.owner,
                                  self.source['jobId'], model=MODEL, price=PRICE)
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?",
                       (self.source['jobId'],))
            db.execute('UPDATE mv3_model_chunks SET settlement_hash=?,state=\'unknown\' '
                       'WHERE job_id=? AND ordinal=0',
                       (previous['settlement_hash'], self.source['jobId']))
            with self.assertRaises(ValueError):
                final.create_task(db, self.entries[1], self.cid, self.owner,
                                  self.source['jobId'], model=MODEL, price=PRICE)

    def test_literal_batch_concatenation_is_not_final_success(self):
        task = self.make()
        wire = FakeWire()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('synopsis_final_concat_not_integrated', result['reason'])
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                                         (task['jobId'],)).fetchone())

    def test_claim_identity_and_reservation_exist_before_mock_generation(self):
        task = self.make()
        seen = []
        def inspect(_request):
            with app_module.connect() as db:
                row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',
                                 (task['jobId'],)).fetchone()
                trial = trials.trial_status(db, task['trialId'], self.cid)
                seen.append((row['state'], bool(row['dispatch_attempt_id']),
                             row['request_id'], row['reserved_usd_micros'],
                             trial['reserved_microusd'], trial['dispatched_requests']))
        wire = FinalWire(on_generation=inspect)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(final.run_task(app_module.connect, task['jobId'], self.cid,
                                        self.owner, wire.adapter)['status'], 'completed')
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0][0], seen[0][1], seen[0][3] > 0,
                          seen[0][4] > 0, seen[0][5]),
                         ('possibly_dispatched', True, True, True, 1))
        self.assertTrue(jobs._valid_request_identity(seen[0][2]))

    def test_synopsis_field_write_failure_does_not_undo_settlement(self):
        task = self.make()
        wire = FinalWire()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute("""CREATE TRIGGER synthetic_final_field_failure BEFORE INSERT ON mv3_entry_fields
                WHEN NEW.name='synopsis' BEGIN SELECT RAISE(FAIL,'synthetic_field_write_failed'); END""")
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        with app_module.connect() as db:
            row = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=?',
                             (task['jobId'],)).fetchone()
            self.assertEqual(row['state'], 'completed')
            self.assertIsNotNone(row['output_json'])
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'spent_estimate_microusd'], 0)
            self.assertIsNone(db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                                         (task['jobId'],)).fetchone())

    def test_wrong_owner_and_wrong_role_cannot_use_task(self):
        task = self.make()
        wire = FinalWire()
        with self.assertRaises(ValueError):
            final.prepare_task(app_module.connect, task['jobId'], self.cid + 999,
                               self.owner, wire.adapter)
        with self.assertRaises(ValueError):
            final.prepare_task(app_module.connect, task['jobId'], self.cid,
                               self.owner + 999, wire.adapter)
        self.assertEqual((len(wire.counts), len(wire.generations)), (0, 0))

    def test_three_or_more_settled_batches_use_same_generic_final_task(self):
        with app_module.connect() as db:
            source_id = processing.create_synopsis_job(
                db, self.entries[1], self.cid, self.owner, input_cap=500)
        source_result = processing.run_synopsis_job(
            app_module.connect, source_id, self.cid, self.owner,
            MockSynopsisAdapter(mock_output))
        self.assertEqual(source_result['status'], 'failed', source_result)
        self.assertEqual(source_result['reason'], 'synopsis_final_input_over_limit')
        with app_module.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',
                               (source_id,)).fetchone()[0]
            self.assertGreaterEqual(count, 3)
            self.assertLessEqual(count, final.MAX_PARTS)
            task = final.create_task(db, self.entries[1], self.cid, self.owner,
                                     source_id, model=MODEL, price=PRICE)
        wire = FinalWire()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(json.loads(wire.generations[0]['input'][0]['content'])['parts']),
                         count - 1)

    def test_concurrent_second_run_cannot_dispatch_or_rebind_first(self):
        task = self.make()
        entered, release = Event(), Event()
        def wait_for_second(_request):
            entered.set()
            self.assertTrue(release.wait(10))
        wire = FinalWire(on_generation=wait_for_second)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(final.run_task, app_module.connect, task['jobId'],
                                 self.cid, self.owner, wire.adapter)
            self.assertTrue(entered.wait(10))
            with app_module.connect() as db:
                before = dict(db.execute('SELECT request_id,dispatch_attempt_id,state '
                                         'FROM mv3_model_chunks WHERE job_id=?',
                                         (task['jobId'],)).fetchone())
                reserved = trials.trial_status(db, task['trialId'], self.cid)[
                    'reserved_microusd']
            with self.assertRaises(ValueError):
                final.run_task(app_module.connect, task['jobId'], self.cid,
                               self.owner, wire.adapter)
            with app_module.connect() as db:
                after = dict(db.execute('SELECT request_id,dispatch_attempt_id,state '
                                        'FROM mv3_model_chunks WHERE job_id=?',
                                        (task['jobId'],)).fetchone())
                self.assertEqual(reserved, trials.trial_status(db, task['trialId'],
                                                                self.cid)['reserved_microusd'])
            self.assertEqual(before, after)
            release.set()
            self.assertEqual(future.result(timeout=20)['status'], 'completed')
        self.assertEqual(len(wire.generations), 1)

    def test_expired_never_dispatched_claim_recovers_without_second_identity(self):
        task = self.make()
        wire = FinalWire()
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='running',lease_token='synthetic-old',"
                       'lease_until=0,attempts=1 WHERE id=?', (task['jobId'],))
            self.assertEqual(final.recover_task(db, task['jobId'], self.cid,
                                                self.owner), 'queued')
            row = db.execute('SELECT state,dispatch_attempt_id FROM mv3_model_chunks '
                             'WHERE job_id=?', (task['jobId'],)).fetchone()
            self.assertEqual(row['state'], 'prepared')
            self.assertIsNone(row['dispatch_attempt_id'])
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(wire.generations), 1)

    def test_unknown_request_cannot_use_local_recovery(self):
        task = self.make()
        wire = FinalWire(timeout=True)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(final.run_task(app_module.connect, task['jobId'], self.cid,
                                        self.owner, wire.adapter)['status'], 'unknown')
        with app_module.connect() as db:
            db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (task['jobId'],))
            with self.assertRaises(ValueError):
                final.recover_task(db, task['jobId'], self.cid, self.owner)
            self.assertGreater(trials.trial_status(db, task['trialId'], self.cid)[
                'reserved_microusd'], 0)
        self.assertEqual(len(wire.generations), 1)


if __name__ == '__main__':
    unittest.main()
