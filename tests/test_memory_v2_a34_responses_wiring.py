"""A3.4 Responses wiring: synthetic SQLite and HTTPX MockTransport only."""
import json
import re
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import httpx

import test_memory_v2_h32 as h32
from test_memory_v2_h32 import app_module, importer
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_synopsis as synopsis
from backend.app import memory_v2_m36_trial as trials
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as metadata_jobs
from backend.app import memory_v2_processing as processing


MODEL = 'gpt-6.1-sol'
PRICE = {'provider': 'openai_responses', 'model': MODEL, 'mode': 'standard',
         'checkedAt': 'synthetic-2026-11-12', 'sourceUrl': 'synthetic-local-price',
         'inputUsdPerMillion': '2.00', 'outputUsdPerMillion': '10.00'}


def _output_for(actual):
    if actual['phase'] == 'batch':
        chunks = []
        for source in actual['sources']:
            content = source['text']
            chunks.append((content, [{'kind': source['kind'], 'id': source['id'],
                                      'start': 0, 'end': len(content), 'quote': content,
                                      'speaker': source['speaker']}]))
    else:
        chunks = []
        for part in actual['parts']:
            for unit in part['units']:
                chunks.append((part['text'][unit['start']:unit['end']], unit['evidence']))
    text = ''
    units = []
    for content, evidence in chunks:
        start = len(text)
        text += content
        units.append({'start': start, 'end': len(text), 'evidence': evidence})
    sources = actual['sources'] if actual['phase'] == 'batch' else actual['sourceManifest']
    used = {(e['kind'], e['id']) for u in units for e in u['evidence']}
    coverage = [{'kind': s['kind'], 'id': s['id'],
                 'used': (s['kind'], s['id']) in used} for s in sources]
    return {'text': text, 'units': units, 'sourceCoverage': coverage}


class FakeWire:
    def __init__(self, *, missing_usage=False, timeout=False, count_tokens=100,
                 on_generation=None, bad_json=False, response_status='completed',
                 refusal=False, bad_schema=False):
        self.counts = []
        self.generations = []
        self.missing_usage = missing_usage
        self.timeout = timeout
        self.count_tokens = count_tokens
        self.on_generation = on_generation
        self.bad_json = bad_json
        self.response_status = response_status
        self.refusal = refusal
        self.bad_schema = bad_schema
        self.adapter = responses.ResponsesAdapter(
            model=MODEL, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        body = json.loads(request.content)
        if request.url.path == '/v1/responses/input_tokens':
            self.counts.append(body)
            amount = (self.count_tokens[len(self.counts)-1]
                      if isinstance(self.count_tokens, list) else self.count_tokens)
            return httpx.Response(200, json={'input_tokens': amount})
        if request.url.path != '/v1/responses':
            raise AssertionError(request.url.path)
        self.generations.append(body)
        if self.on_generation:
            self.on_generation(body)
        if self.timeout:
            raise httpx.ReadTimeout('synthetic timeout')
        actual = json.loads(body['input'][0]['content'])
        output = _output_for(actual)
        if self.bad_schema:
            output['units'] = []
        content = ([{'type': 'refusal', 'refusal': 'synthetic refusal'}] if self.refusal else
                   [{'type': 'output_text', 'text': 'not json' if self.bad_json else
                     json.dumps(output, ensure_ascii=False)}])
        response = {'id': 'resp_'+str(uuid.uuid4()), 'model': MODEL,
                    'status': self.response_status, 'service_tier': 'default',
                    'output': [{'type': 'message', 'content': content}]}
        if not self.missing_usage:
            response['usage'] = {'input_tokens': 100, 'output_tokens': 100}
        return httpx.Response(200, json=response)


class SynopsisResponsesWiring(unittest.TestCase):
    def setUp(self):
        self.fixture = h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
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

    def tearDown(self):
        self.fixture.doCleanups()

    def create(self, index=0, *, batch_cap=4096, max_microusd=200000):
        with app_module.connect() as db:
            return synopsis.create_real_trial(
                db, self.entries[index], self.cid, self.owner,
                model=MODEL, price=PRICE, batch_input_cap=batch_cap,
                max_microusd=max_microusd)

    def test_single_batch_request_and_usd_ledger(self):
        wire = FakeWire()
        planned = self.create()
        prepared = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual((prepared['generationRequests'], prepared['countRequests']), (1, 1))
        done = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(done['status'], 'completed', done)
        self.assertEqual((len(wire.counts), len(wire.generations)), (1, 1))
        self.assertEqual(wire.generations[0]['text']['format']['name'], 'yosaki_a34_synopsis')
        self.assertEqual(wire.counts[0], responses.count_payload(wire.generations[0]))
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertEqual(trial['status'], 'completed')
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            field = metadata.read_draft(db, self.entries[0], self.cid)['fields']['synopsis']
            self.assertEqual(field['reviewStatus'], 'needs_review')
            self.assertEqual(field['lifecycleStatus'], 'draft')
            self.assertEqual(db.execute('SELECT rule_version FROM mv3_entry_fields WHERE id=?',
                                        (field['fieldId'],)).fetchone()[0], synopsis.REAL_PROFILE)
        again = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertTrue(again['reused'])
        self.assertEqual(len(wire.generations), 1)

    def test_timeout_keeps_unknown_and_usd_reservation(self):
        wire = FakeWire(timeout=True)
        planned = self.create()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'unknown')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 1)

    def test_two_batches_and_final_use_only_persisted_parts(self):
        wire = FakeWire()
        planned = self.create(1, batch_cap=1400)
        prepared = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual((prepared['generationRequests'], prepared['countRequests']), (3, 2))
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual((len(wire.counts), len(wire.generations)), (3, 3))
        self.assertEqual([json.loads(x['input'][0]['content'])['phase']
                          for x in wire.generations], ['batch', 'batch', 'final'])
        self.assertEqual(wire.counts[-1], responses.count_payload(wire.generations[-1]))
        final = json.loads(wire.generations[-1]['input'][0]['content'])
        self.assertNotIn('sources', final)
        self.assertEqual(len(final['parts']), 2)
        self.assertEqual(len(final['sourceManifest']), 6)
        with app_module.connect() as db:
            self.assertEqual(trials.trial_status(
                db, planned['trialId'], self.cid)['reserved_microusd'], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=? AND state='completed'",
                (planned['jobId'],)).fetchone()[0], 3)

    def test_missing_usage_retains_unknown_reservation_only(self):
        wire = FakeWire(missing_usage=True)
        planned = self.create(1, batch_cap=1400)
        before = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner,
            wire.adapter)['reservedMicrousd']
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'unknown')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            rows = db.execute('SELECT state,provider_status,reserved_usd_micros '
                              'FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
                              (planned['jobId'],)).fetchall()
            self.assertEqual(trial['reserved_microusd'], rows[0]['reserved_usd_micros'])
            self.assertLess(trial['reserved_microusd'], before)
            self.assertEqual([row['provider_status'] for row in rows[1:]],
                             ['not_dispatched', 'not_dispatched'])
            self.assertEqual(trial['spent_estimate_microusd'], 0)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
        self.assertEqual(len(wire.generations), 1)

    def test_input_count_limit_prevents_any_generation(self):
        wire = FakeWire(count_tokens=4097)
        planned = self.create()
        with self.assertRaisesRegex(ValueError, 'synopsis_input_token_limit'):
            synopsis.prepare_real_trial(
                app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(len(wire.counts), 1)
        self.assertEqual(wire.generations, [])
        with app_module.connect() as db:
            self.assertEqual(trials.trial_status(
                db, planned['trialId'], self.cid)['reserved_microusd'], 0)

    def test_invalid_json_has_known_usage_but_no_field(self):
        wire = FakeWire(bad_json=True)
        planned = self.create()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_schema_shaped_but_unmapped_text_is_rejected(self):
        planned = self.create()
        wire = FakeWire(bad_schema=True)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('synopsis_output_invalid', result['reason'])
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_incomplete_and_refusal_settle_known_usage_without_candidate(self):
        for wire in (FakeWire(response_status='incomplete'), FakeWire(refusal=True)):
            with self.subTest(incomplete=wire.response_status == 'incomplete'):
                planned = self.create()
                synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                            self.cid, self.owner, wire.adapter)
                result = synopsis.run_real_trial(
                    app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
                self.assertEqual(result['status'], 'failed')
                with app_module.connect() as db:
                    trial = trials.trial_status(db, planned['trialId'], self.cid)
                    self.assertGreater(trial['spent_estimate_microusd'], 0)
                    self.assertEqual(trial['reserved_microusd'], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                                'WHERE job_id=?', (planned['jobId'],)).fetchone()[0], 0)

    def test_budget_rejects_before_any_generation(self):
        planned = self.create(max_microusd=1)
        wire = FakeWire()
        with self.assertRaisesRegex(ValueError, 'trial_budget_exceeded'):
            synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                        self.cid, self.owner, wire.adapter)
        self.assertEqual(wire.generations, [])
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertEqual(trial['status'], 'stopped')
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(trial['dispatched_requests'], 0)

    def test_final_worst_case_is_bounded_before_batch_generation(self):
        with app_module.connect() as db:
            planned = synopsis.create_real_trial(
                db, self.entries[1], self.cid, self.owner, model=MODEL, price=PRICE,
                batch_input_cap=1400, final_input_cap=256)
        wire = FakeWire()
        with self.assertRaisesRegex(ValueError, 'synopsis_final_worst_case_over_limit'):
            synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                        self.cid, self.owner, wire.adapter)
        self.assertEqual((len(wire.counts), len(wire.generations)), (0, 0))

    def test_count_generation_fingerprint_conflict_rejects_before_dispatch(self):
        planned = self.create()
        wire = FakeWire()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute('UPDATE mv3_model_chunks SET input_hash=? WHERE job_id=? AND ordinal=0',
                       ('0'*64, planned['jobId']))
        with self.assertRaisesRegex(ValueError, 'synopsis_count_generation_mismatch'):
            synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        self.assertEqual(wire.generations, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',
                                        (planned['jobId'],)).fetchone()[0], 'prepared')

    def test_cancel_during_adapter_settles_usage_without_candidate(self):
        planned = self.create()
        def cancel(_):
            with app_module.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',
                           (planned['jobId'],))
        wire = FakeWire(on_generation=cancel)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'cancelled')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_expired_worker_can_settle_usage_but_cannot_publish(self):
        planned = self.create()
        def expire(_):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?',
                           (planned['jobId'],))
        wire = FakeWire(on_generation=expire)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'synopsis_lease_changed')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_role_disabled_during_request_still_settles_known_usage(self):
        planned = self.create(1, batch_cap=1400)
        def disable(_):
            with app_module.connect() as db:
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?',
                           (planned['jobId'],))
        wire = FakeWire(on_generation=disable)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'synopsis_role_revoked')
        self.assertEqual(len(wire.generations), 1)
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['dispatched_requests'], 1)
            row = db.execute('SELECT state,used_input_tokens,used_output_tokens,output_json '
                             'FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                             (planned['jobId'],)).fetchone()
            self.assertEqual((row['state'], row['used_input_tokens'],
                              row['used_output_tokens'], row['output_json']),
                             ('rejected', 100, 100, None))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (planned['jobId'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)
            with self.assertRaises(ValueError):
                metadata.read_draft(db, self.entries[1], self.cid)
            with self.assertRaises(ValueError):
                synopsis.create_real_trial(db, self.entries[1], self.cid,
                                           self.owner, model=MODEL, price=PRICE)
            db.execute('UPDATE characters SET enabled=1 WHERE id=?', (self.cid,))
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 1)

    def test_role_disabled_with_missing_usage_retains_unknown_and_reservation(self):
        planned = self.create()
        def disable(_):
            with app_module.connect() as db:
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        wire = FakeWire(on_generation=disable, missing_usage=True)
        before = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner,
            wire.adapter)['reservedMicrousd']
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'unknown')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertEqual(trial['reserved_microusd'], before)
            self.assertEqual(trial['spent_estimate_microusd'], 0)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (planned['jobId'],)).fetchone()[0], 0)

    def test_responses_dispatch_requires_creator_owner_binding_before_send(self):
        planned = self.create()
        wire = FakeWire()
        prepared = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            before = db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                (planned['jobId'],)).fetchone()[0]
            with self.assertRaisesRegex(ValueError, 'dispatch_owner_binding_required'):
                processing.dispatch(db, planned['jobId'], self.cid, 0)
            with self.assertRaisesRegex(ValueError, 'dispatch_owner_binding_required'):
                processing.dispatch(db, planned['jobId'], self.cid, 0,
                                    original_owner_id=self.owner + 1)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (planned['jobId'],)).fetchone()[0], before)
            row = db.execute('SELECT state,dispatch_attempt_id FROM mv3_model_chunks '
                             'WHERE job_id=? AND ordinal=0', (planned['jobId'],)).fetchone()
            self.assertEqual(tuple(row), ('prepared', None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_a3_audit WHERE job_id=? "
                                        "AND event='dispatch_owner_binding_v1'",
                                        (planned['jobId'],)).fetchone()[0], 0)
            self.assertEqual(trials.trial_status(db, planned['trialId'], self.cid)
                             ['reserved_microusd'], prepared['reservedMicrousd'])
        self.assertEqual(wire.generations, [])

    def test_revoked_and_cancelled_request_settles_without_next_batch(self):
        planned = self.create(1, batch_cap=1400)
        def revoke_and_cancel(_):
            with app_module.connect() as db:
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
                db.execute('UPDATE mv3_jobs SET cancel_intent=1,lease_until=0 WHERE id=?',
                           (planned['jobId'],))
        wire = FakeWire(on_generation=revoke_and_cancel)
        prepared = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(wire.generations), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,used_input_tokens,used_output_tokens,output_json,'
                             'reserved_usd_micros FROM mv3_model_chunks WHERE job_id=? '
                             'AND ordinal=0', (planned['jobId'],)).fetchone()
            self.assertEqual(tuple(row)[:4], ('rejected', 100, 100, None))
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            expected = responses.synopsis_usage_estimate(
                {'input_tokens':100, 'output_tokens':100},
                PRICE['inputUsdPerMillion'], PRICE['outputUsdPerMillion'])['microusd']
            self.assertEqual(trial['spent_estimate_microusd'], expected)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT provider_status FROM mv3_model_chunks '
                                        'WHERE job_id=? AND ordinal=1',
                                        (planned['jobId'],)).fetchone()[0],
                             'not_dispatched')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (planned['jobId'],)).fetchone()[0], 0)

    def test_revoked_settlement_requires_original_proof_and_is_idempotent(self):
        planned = self.create()
        other = self.create(1, batch_cap=1400)
        captured = {}
        def disable(_):
            captured['proof'] = next(proof for proof in list(jobs._issued_dispatches.values())
                                     if proof.job_id == planned['jobId'])
            with app_module.connect() as db:
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        wire = FakeWire(on_generation=disable)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        proof = captured['proof']
        with app_module.connect() as db:
            chunk = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',
                               (planned['jobId'],)).fetchone()
            response_id = chunk['provider_response_id']
            estimate = chunk['usage_estimated_usd_micros']
            trial_before = trials.trial_status(db, planned['trialId'], self.cid)
            audit_before = db.execute("SELECT COUNT(*) FROM mv3_a3_audit WHERE job_id=? "
                                      "AND event='revoked_usage_settled_v1'",
                                      (planned['jobId'],)).fetchone()[0]
        self.assertEqual(audit_before, 1)
        result_hash = synopsis._hash(['synopsis-provider-result-v1', response_id,
                                      'completed', {'input_tokens':100,'output_tokens':100},
                                      'synopsis_role_revoked'])
        base = dict(job_id=planned['jobId'], character_id=self.cid, ordinal=0,
                    request_id=proof.request_id, attempt_id=proof, state='rejected',
                    used_input=100, used_output=100, used_cost=0,
                    result_hash=result_hash, trial_id=planned['trialId'],
                    usd_estimate=estimate, original_owner_id=self.owner)

        def settle(**changes):
            arguments = {**base, **changes}
            with app_module.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                return jobs.settle_dispatch(db, **arguments)

        for changes in (
                {'original_owner_id':self.owner+1},
                {'character_id':self.cid+1},
                {'job_id':other['jobId']},
                {'request_id':str(uuid.uuid4())},
                {'attempt_id':replace(proof, attempt_id=str(uuid.uuid4()))},
                {'attempt_id':replace(proof)},
                {'original_owner_id':None},
                {'used_input':101},
                {'state':'completed'},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settle(**changes)
        barrier = Barrier(2)
        def concurrent_retry():
            barrier.wait(timeout=5)
            return settle()
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: concurrent_retry(), range(2)))
        self.assertEqual(replies, ['failed', 'failed'])
        with app_module.connect() as db:
            self.assertEqual(trials.trial_status(db, planned['trialId'], self.cid),
                             trial_before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_a3_audit WHERE job_id=? "
                                        "AND event='revoked_usage_settled_v1'",
                                        (planned['jobId'],)).fetchone()[0], audit_before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (planned['jobId'],)).fetchone()[0], 0)

    def test_revoked_settlement_refuses_missing_durable_owner_binding(self):
        planned = self.create()
        def tamper(_):
            with app_module.connect() as db:
                db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
                db.execute("DELETE FROM mv3_a3_audit WHERE job_id=? "
                           "AND event='dispatch_owner_binding_v1'", (planned['jobId'],))
        wire = FakeWire(on_generation=tamper)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with self.assertRaisesRegex(ValueError, 'revoked_settlement_not_authorized'):
            synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',
                                        (planned['jobId'],)).fetchone()[0],
                             'possibly_dispatched')
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['reserved_microusd'], 0)
            self.assertEqual(trial['spent_estimate_microusd'], 0)

    def test_output_write_failure_settles_known_usage_independently(self):
        planned = self.create(1, batch_cap=1400)
        wire = FakeWire()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_output_failure BEFORE UPDATE OF output_json
                ON mv3_model_chunks WHEN NEW.output_json IS NOT NULL
                BEGIN SELECT RAISE(ABORT,'synthetic_output_failure'); END''')
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['reason'], 'synopsis_output_persist_failed')
        self.assertEqual(len(wire.generations), 1)
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT provider_status FROM mv3_model_chunks '
                                        'WHERE job_id=? AND ordinal=1',
                                        (planned['jobId'],)).fetchone()[0],
                             'not_dispatched')
            row = db.execute('SELECT state,used_input_tokens,output_json FROM mv3_model_chunks '
                             'WHERE job_id=? AND ordinal=0', (planned['jobId'],)).fetchone()
            self.assertEqual((row['state'], row['used_input_tokens'], row['output_json']),
                             ('rejected', 100, None))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        self.assertEqual(len(wire.generations), 1)

    def test_e1_e2_full_synthetic_text_has_expected_bounded_partition(self):
        path = Path(__file__).resolve().parents[1] / 'docs' / 'A34_SYNOPSIS_REAL_EVAL_SYNTHETIC.md'
        document = path.read_text(encoding='utf-8')
        for prefix, cap, expected in (
                ('E1', 4096, [['E1-01','E1-02','E1-03','E1-04','E1-05','E1-06']]),
                ('E2', 1200, [['E2-01','E2-02','E2-03'],['E2-04','E2-05','E2-06']])):
            items = []
            for line in document.splitlines():
                match = re.match(r'^\| (' + prefix + r'-\d\d) \| (user|assistant) \| (.*?) \|$', line)
                if match:
                    label, speaker, text = match.groups()
                    items.append((label, {'kind':'message', 'id': str(100+len(items)),
                                          'speaker':speaker, 'text':text,
                                          'sourceStatus':'imported',
                                          'contentHash':'a'*64, 'stateHash':'b'*64}))
            self.assertEqual(len(items), 6)
            groups = synopsis.plan_batches([source for _,source in items], input_cap=cap)
            actual = []
            for group in groups:
                actual.append([items[int(source['id'])-100][0] for source in group])
            self.assertEqual(actual, expected)
            request = responses.synopsis_request_payload(
                MODEL, 'batch', {'phase':'batch', 'sources':groups[0],
                                 'ordinal':0, 'inputVersion':'synthetic-source-token'}, 2048)
            self.assertIn(groups[0][0]['text'], request['input'][0]['content'])
            self.assertNotIn('事实核对表', request['input'][0]['content'])
            self.assertEqual(request['text']['format']['strict'], True)
            self.assertEqual(set(responses.count_payload(request)),
                             {'model','instructions','input','text','reasoning'})

    def test_final_count_over_limit_stops_after_two_settled_generations(self):
        wire = FakeWire(count_tokens=[100,100,4097])
        planned = self.create(1, batch_cap=1400)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(wire.generations), 2)
        self.assertEqual(len(wire.counts), 3)
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertEqual(trial['dispatched_requests'], 2)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(tuple(db.execute('SELECT state,provider_status '
                                        'FROM mv3_model_chunks WHERE job_id=? '
                                        'AND ordinal=2', (planned['jobId'],)).fetchone()),
                             ('rejected', 'not_dispatched'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_source_change_during_transport_preserves_usage_without_field(self):
        planned = self.create()
        with app_module.connect() as db:
            source_id = metadata.read_draft(db, self.entries[0], self.cid)['sourceRefs'][0]['source_id']
        def change(_):
            with app_module.connect() as db:
                db.execute('UPDATE messages SET content=? WHERE id=?',
                           ('合成来源变化', source_id))
        wire = FakeWire(on_generation=change)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_manual_field_during_transport_wins_and_cost_is_settled(self):
        planned = self.create()
        def revise(_):
            with app_module.connect() as db:
                detail = metadata.read_draft(db, self.entries[0], self.cid)
                metadata.revise(db, self.entries[0], self.cid, detail['versionId'],
                                {'synopsis':'Owner 手工梗概'}, self.owner)
        wire = FakeWire(on_generation=revise)
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed')
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(metadata.read_draft(db, self.entries[0], self.cid)
                             ['fields']['synopsis']['value'], 'Owner 手工梗概')

    def test_storage_unavailable_for_both_settlements_keeps_unknown(self):
        planned = self.create()
        wire = FakeWire()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_settlement_failure
                BEFORE UPDATE OF used_input_tokens ON mv3_model_chunks
                BEGIN SELECT RAISE(ABORT,'synthetic_settlement_failure'); END''')
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'unknown', result)
        self.assertTrue(result['usageObservedNotPersisted'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (planned['jobId'],)).fetchone()[0], 'unknown')
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['reserved_microusd'], 0)
            self.assertEqual(trial['spent_estimate_microusd'], 0)

    def test_field_commit_failure_keeps_output_and_known_usage(self):
        planned = self.create()
        wire = FakeWire()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_field_failure BEFORE INSERT ON mv3_entry_fields
                WHEN NEW.name='synopsis' BEGIN SELECT RAISE(ABORT,'synthetic_field_failure'); END''')
        result = synopsis.run_real_trial(
            app_module.connect, planned['jobId'], self.cid, self.owner, wire.adapter)
        self.assertEqual(result['status'], 'failed', result)
        with app_module.connect() as db:
            trial = trials.trial_status(db, planned['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            row = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=?',
                             (planned['jobId'],)).fetchone()
            self.assertEqual(row['state'], 'completed')
            self.assertIsNotNone(row['output_json'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields '
                                        "WHERE name='synopsis'").fetchone()[0], 0)

    def test_pre_dispatch_cancel_does_not_generate(self):
        planned = self.create()
        wire = FakeWire()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        with app_module.connect() as db:
            self.assertEqual(processing.cancel(db, planned['jobId'], self.cid), 'cancelled')
        with self.assertRaises(ValueError):
            synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                    self.cid, self.owner, wire.adapter)
        self.assertEqual(wire.generations, [])
