"""Synthetic-only replay of the retained E1 status and usage; the text is invented.

No Provider request, private RAW, credential, or original partial response is used.
"""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import httpx

import test_memory_v2_h32 as h32
from test_memory_v2_h32 import app_module, importer
from backend.app import memory_v2_metadata_jobs as metadata_jobs
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_m36_trial as trials
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_synopsis as synopsis


MODEL = 'gpt-6.1-sol'
PRICE = {'provider': 'openai_responses', 'model': MODEL, 'mode': 'standard',
         'checkedAt': 'synthetic-2026-11-13', 'sourceUrl': 'synthetic-local-price',
         'inputUsdPerMillion': '2.00', 'outputUsdPerMillion': '10.00'}
RETAINED_USAGE = {'input_tokens': 1169,
                  'input_tokens_details': {'cache_write_tokens': 1166,
                                           'cached_tokens': 0},
                  'output_tokens': 2048,
                  'output_tokens_details': {'reasoning_tokens': 1668},
                  'total_tokens': 3217}
SYNTHETIC_PARTIAL_TEXT = '{"text":"合成的未完成梗概片段'


class Wire:
    def __init__(self, status='incomplete', usage=RETAINED_USAGE, text=SYNTHETIC_PARTIAL_TEXT,
                 refusal=False):
        self.status, self.usage, self.text, self.refusal = status, usage, text, refusal
        self.counts = self.generations = 0
        self.adapter = responses.ResponsesAdapter(
            model=MODEL, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        if request.url.path == '/v1/responses/input_tokens':
            self.counts += 1
            return httpx.Response(200, json={'input_tokens': 100})
        assert request.url.path == '/v1/responses'
        self.generations += 1
        content = ([{'type': 'refusal', 'refusal': 'synthetic refusal'}] if self.refusal else
                   [{'type': 'output_text', 'text': self.text}])
        body = {'id': 'resp_synthetic_not_the_original', 'model': MODEL,
                'service_tier': 'default', 'status': self.status,
                'incomplete_details': {'reason': 'max_output_tokens'},
                'output': [{'type': 'message', 'content': content}]}
        if self.usage is not None:
            body['usage'] = self.usage
        return httpx.Response(200, json=body)


class A34DiagnosticRetention(unittest.TestCase):
    def setUp(self):
        self.fixture = h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cid, self.owner = self.fixture.cid, self.fixture.owner
        with app_module.connect() as db:
            result = importer.confirm_h32_drafts(
                db, self.fixture.batch, self.cid, self.fixture.file['id'],
                self.fixture.segments(), **self.fixture.arguments(db))
            self.entries = [item['entryId'] for item in result['items']]
            metadata_jobs.init_db(db)
            trials.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)

    def run_case(self, wire, *, two_batches=False, sink=None):
        with app_module.connect() as db:
            trial = synopsis.create_real_trial(
                db, self.entries[1 if two_batches else 0], self.cid, self.owner,
                model=MODEL, price=PRICE, batch_input_cap=1400 if two_batches else 4096,
                max_microusd=200000)
        synopsis.prepare_real_trial(app_module.connect, trial['jobId'],
                                    self.cid, self.owner, wire.adapter)
        result = synopsis.run_real_trial(app_module.connect, trial['jobId'],
                                         self.cid, self.owner, wire.adapter,
                                         diagnostic_sink=sink)
        return trial, result

    def test_cached_categories_are_parts_of_total_and_reserve_uses_upper_bound(self):
        estimate = responses.synopsis_usage_estimate(
            RETAINED_USAGE, '2.00', '10.00')
        self.assertEqual(estimate, {'microusd': 23401,
                                    'billingMode': 'default_30m_cache_write'})
        self.assertEqual(responses.synopsis_quote_microusd(4096, 2048, '2.00', '10.00'),
                         49152)
        mixed = {'input_tokens': 100, 'output_tokens': 10,
                 'input_tokens_details': {'cached_tokens': 20, 'cache_write_tokens': 30,
                                          'cache_write_tokens_details': {
                                              'ttl_30m_tokens': 10, 'ttl_12h_tokens': 20}}}
        # 50 ordinary + 20 cache reads + 10 half-hour writes + 20 twelve-hour
        # writes = 227 input micro-USD; output is another 100.
        self.assertEqual(responses.synopsis_usage_estimate(mixed, '2', '10')['microusd'],
                         327)
        missing = responses.synopsis_usage_estimate(
            {'input_tokens': 100, 'output_tokens': 10}, '2', '10')
        self.assertEqual(missing, {'microusd': 600,
                                   'billingMode': 'unclassified_input_upper_bound'})
        unknown = responses.synopsis_usage_estimate(
            {'input_tokens': 100, 'output_tokens': 10,
             'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0,
                                      'new_billed_category': 50}}, '2', '10')
        self.assertEqual(unknown['microusd'], 600)
        with self.assertRaisesRegex(ValueError, 'provider_usage_missing'):
            responses.synopsis_usage_estimate({'input_tokens': True,
                                               'output_tokens': 10}, '2', '10')

    def test_public_diagnostic_is_bounded_and_redacts_key_like_text(self):
        job, attempt = str(uuid4()), str(uuid4())
        record = synopsis._real_diagnostic(
            {'status': 'incomplete', 'responseId': 'resp_synthetic',
             'incompleteReason': 'max_output_tokens', 'usage': RETAINED_USAGE,
             'text': 'Bearer synthetic_secret sk-' + 'X' * 20 + ' ' + 'A' * 9000},
            {'status': 'failed', 'reason': 'provider_incomplete'},
            {'requestId': str(uuid4()),
             'dispatchProof': SimpleNamespace(attempt_id=attempt)},
            job, 0, 'default_30m_cache_write')
        self.assertTrue(record['publicOutputTruncated'])
        self.assertLessEqual(len(record['publicOutputText']), synopsis.REAL_DIAGNOSTIC_TEXT_CAP)
        self.assertNotIn('synthetic_secret', record['publicOutputText'])
        self.assertNotIn('sk-' + 'X' * 20, record['publicOutputText'])
        with tempfile.TemporaryDirectory() as directory:
            first = synopsis.write_isolated_diagnostic(directory, record)
            self.assertEqual(first, synopsis.write_isolated_diagnostic(directory, record))
            changed = dict(record, providerStatus='completed')
            with self.assertRaisesRegex(ValueError, 'synopsis_diagnostic_conflict'):
                synopsis.write_isolated_diagnostic(directory, changed)

    def test_incomplete_settles_usage_and_retains_only_synthetic_public_diagnostic(self):
        wire = Wire()
        with tempfile.TemporaryDirectory() as directory:
            trial, result = self.run_case(
                wire, two_batches=True,
                sink=lambda record: synopsis.write_isolated_diagnostic(directory, record))
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(result['reason'], 'provider_incomplete')
            self.assertTrue(result['diagnosticStored'])
            self.assertEqual((wire.counts, wire.generations), (2, 1))
            paths = list(Path(directory).glob('provider_*.json'))
            self.assertEqual(len(paths), 1)
            diagnostic = json.loads(paths[0].read_text(encoding='utf-8'))
            self.assertEqual(diagnostic['providerStatus'], 'incomplete')
            self.assertEqual(diagnostic['providerReason'], 'max_output_tokens')
            self.assertEqual(diagnostic['responseId'], 'resp_synthetic_not_the_original')
            self.assertEqual(diagnostic['rawUsage'], RETAINED_USAGE)
            self.assertEqual(diagnostic['publicOutputText'], SYNTHETIC_PARTIAL_TEXT)
            self.assertIsNone(diagnostic['publicOutputStructure'])
            self.assertNotIn('authorization', paths[0].read_text(encoding='utf-8').lower())
        with app_module.connect() as db:
            budget = trials.trial_status(db, trial['trialId'], self.cid)
            self.assertEqual(budget['spent_estimate_microusd'], 23401)
            self.assertEqual(budget['dispatched_requests'], 1)
            self.assertEqual(budget['reserved_microusd'], 0)
            rows = db.execute('SELECT state,provider_status,reserved_usd_micros,'
                              'used_input_tokens,used_output_tokens,output_json '
                              'FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
                              (trial['jobId'],)).fetchall()
            self.assertEqual((rows[0]['state'], rows[0]['used_input_tokens'],
                              rows[0]['used_output_tokens'],rows[0]['output_json']),
                             ('rejected', 1169, 2048, None))
            self.assertTrue(all(r['state'] == 'rejected' and
                                r['provider_status'] == 'not_dispatched' and
                                r['used_input_tokens'] is None and
                                r['used_output_tokens'] is None
                                for r in rows[1:]))
            self.assertGreater(rows[1]['reserved_usd_micros'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (trial['jobId'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_versions').fetchone()[0], 0)

    def test_missing_usage_preserves_unknown_and_reservation(self):
        wire = Wire(usage=None)
        records = []
        trial, result = self.run_case(wire, two_batches=True, sink=records.append)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual((wire.counts, wire.generations), (2, 1))
        self.assertTrue(records[0]['usageUnavailable'])
        with app_module.connect() as db:
            budget = trials.trial_status(db, trial['trialId'], self.cid)
            self.assertEqual(budget['spent_estimate_microusd'], 0)
            self.assertGreater(budget['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (trial['jobId'],)).fetchone()[0], 'unknown')
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=? '
                                        'AND ordinal=0', (trial['jobId'],)).fetchone()[0], 'unknown')

    def test_network_path_requires_diagnostic_sink_before_dispatch(self):
        wire = Wire()
        wire.adapter.network_enabled = True  # Still MockTransport; no network access.
        with app_module.connect() as db:
            trial = synopsis.create_real_trial(db, self.entries[0], self.cid, self.owner,
                                               model=MODEL, price=PRICE,
                                               max_microusd=200000)
        synopsis.prepare_real_trial(app_module.connect, trial['jobId'], self.cid,
                                    self.owner, wire.adapter)
        with self.assertRaisesRegex(ValueError, 'synopsis_diagnostic_sink_required'):
            synopsis.run_real_trial(app_module.connect, trial['jobId'], self.cid,
                                    self.owner, wire.adapter)
        self.assertEqual(wire.generations, 0)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (trial['jobId'],)).fetchone()[0], 'queued')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE '
                                        'job_id=? AND dispatch_attempt_id IS NOT NULL',
                                        (trial['jobId'],)).fetchone()[0], 0)

    def test_refusal_and_parse_failure_keep_public_diagnostics_separate(self):
        for wire, expected in ((Wire(status='completed', refusal=True), 'provider_refused'),
                               (Wire(status='completed', text='synthetic not-json'),
                                'synopsis_output_invalid')):
            with self.subTest(expected=expected):
                records = []
                trial, result = self.run_case(wire, sink=records.append)
                self.assertEqual(result['status'], 'failed')
                self.assertIn(expected, result['reason'])
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['responseId'], 'resp_synthetic_not_the_original')
                self.assertTrue(records[0]['publicRefusalText'] or records[0]['publicOutputText'])
                with app_module.connect() as db:
                    self.assertEqual(db.execute('SELECT output_json FROM mv3_model_chunks '
                                                'WHERE job_id=?', (trial['jobId'],)).fetchone()[0], None)

    def test_diagnostic_sink_failure_cannot_rollback_confirmed_usage(self):
        def fail(_record):
            raise OSError('synthetic diagnostic disk failure')
        wire = Wire()
        trial, result = self.run_case(wire, sink=fail)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['diagnosticStored'])
        with app_module.connect() as db:
            budget = trials.trial_status(db, trial['trialId'], self.cid)
            self.assertEqual(budget['spent_estimate_microusd'], 23401)
            self.assertEqual(budget['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=?',
                                        (trial['jobId'],)).fetchone()[0], 'rejected')
        with self.assertRaisesRegex(ValueError, 'synopsis_trial_not_claimable'):
            synopsis.run_real_trial(app_module.connect, trial['jobId'], self.cid,
                                    self.owner, wire.adapter, diagnostic_sink=fail)
        self.assertEqual(wire.generations, 1)

    def test_slow_body_records_read_timeout_after_headers_without_retry(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                time.sleep(0.15)
                try:
                    self.wfile.write(b'{}')
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(responses, 'API_ROOT',
                              f'http://127.0.0.1:{server.server_port}/v1'):
                adapter = responses.ResponsesAdapter(
                    model=MODEL, allow_network=True, api_key='synthetic-loopback-only')
                self.addCleanup(adapter.close)
                adapter.client.timeout = httpx.Timeout(0.03, connect=0.03)
                with self.assertRaises(httpx.ReadTimeout):
                    adapter.generate({'model': MODEL})
                trace = adapter.last_transport_diagnostic
                self.assertEqual(trace['httpStatus'], 200)
                self.assertEqual(trace['stage'], 'reading_body')
                self.assertEqual(trace['exceptionType'], 'ReadTimeout')
                self.assertEqual(trace['timeouts']['read'], 0.03)
                self.assertFalse(trace.get('bodyComplete', False))
                self.assertNotIn('synthetic-loopback-only', json.dumps(trace))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_disconnect_keeps_original_unknown_identity_and_reservation(self):
        class BrokenBody(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"id":"resp_partial_untrusted"'
                raise httpx.ReadError('synthetic disconnect; not provider text')

        class BrokenWire(Wire):
            def handle(self, request):
                if request.url.path.endswith('/input_tokens'):
                    return super().handle(request)
                self.generations += 1
                return httpx.Response(200, stream=BrokenBody())

        wire, records = BrokenWire(), []
        trial, result = self.run_case(wire, two_batches=True, sink=records.append)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(wire.generations, 1)
        trace = records[0]['transport']
        self.assertEqual((trace['httpStatus'], trace['stage'], trace['exceptionType']),
                         (200, 'reading_body', 'ReadError'))
        self.assertGreater(trace['receivedBytes'], 0)
        self.assertIsNone(records[0]['responseId'])
        with app_module.connect() as db:
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                             (trial['jobId'],)).fetchone()
            self.assertEqual(row['state'], 'unknown')
            self.assertTrue(row['request_id'] and row['dispatch_attempt_id'])
            self.assertIsNone(row['output_json'])
            self.assertIsNone(row['used_output_tokens'])
            self.assertGreater(trials.trial_status(db, trial['trialId'], self.cid)
                               ['reserved_microusd'], 0)
        with self.assertRaisesRegex(ValueError, 'synopsis_trial_not_claimable'):
            synopsis.run_real_trial(app_module.connect, trial['jobId'], self.cid,
                                    self.owner, wire.adapter, diagnostic_sink=records.append)
        self.assertEqual(wire.generations, 1)

    def test_invalid_json_is_distinct_from_status_normalization_failure(self):
        class InvalidJSON(Wire):
            def handle(self, request):
                if request.url.path.endswith('/input_tokens'):
                    return super().handle(request)
                self.generations += 1
                return httpx.Response(200, content=b'{synthetic-invalid-json')

        records = []
        _, result = self.run_case(InvalidJSON(), sink=records.append)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(records[0]['transport']['stage'], 'parsing_json')
        self.assertEqual(records[0]['transport']['exceptionType'], 'JSONDecodeError')
        wire, records = Wire(status='in_progress'), []
        # The first independent entry must remain locked by its unknown request.
        trial, result = self.run_case(wire, two_batches=True, sink=records.append)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(records[0]['transport']['stage'], 'normalizing_response')
        self.assertEqual(records[0]['responseId'], 'resp_synthetic_not_the_original')
        self.assertEqual(records[0]['rawUsage'], RETAINED_USAGE)
        self.assertEqual(records[0]['publicOutputText'], SYNTHETIC_PARTIAL_TEXT)
        with app_module.connect() as db:
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',
                             (trial['jobId'],)).fetchone()
            self.assertEqual(row['provider_response_id'], 'resp_synthetic_not_the_original')
            self.assertIsNone(row['output_json'])
            self.assertIsNone(row['used_output_tokens'])
            self.assertGreater(trials.trial_status(db, trial['trialId'], self.cid)
                               ['reserved_microusd'], 0)

    def test_exception_diagnostic_failure_does_not_release_unknown_reservation(self):
        class TimeoutWire(Wire):
            def handle(self, request):
                if request.url.path.endswith('/input_tokens'):
                    return super().handle(request)
                self.generations += 1
                raise httpx.ReadTimeout('synthetic; do not retain exception message')

        def fail(_record):
            raise OSError('synthetic diagnostic disk failure')

        wire = TimeoutWire()
        trial, result = self.run_case(wire, sink=fail)
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['diagnosticStored'])
        self.assertEqual(wire.generations, 1)
        with app_module.connect() as db:
            budget = trials.trial_status(db, trial['trialId'], self.cid)
            self.assertEqual(budget['spent_estimate_microusd'], 0)
            self.assertGreater(budget['reserved_microusd'], 0)


if __name__ == '__main__':
    unittest.main()
