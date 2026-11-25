"""Synthetic, offline acceptance for the confirmed-entry v3 NARRATIVE path."""
import json
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_memory_v2_h32 as h32
from test_memory_v2_h32 import app_module, importer
from backend.app import memory_v2_derivation as m3
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as metadata_jobs
from backend.app import memory_v2_narrative_processing as narrative
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_fidelity as fidelity
from backend.app import memory_v2_model_adapter as model_adapter
from backend.app.memory_v2_model_adapter import MockNarrativeAdapter


def response(request):
    items = [{'sourceKind': x['sourceKind'], 'sourceId': x['sourceId'],
              'speaker': x['speaker'],
              'text': ('她说：' if x['speaker'] == 'user' else '我说：') + x['content']}
             for x in request['messages']]
    return {'finishReason': 'stop', 'output': {'items': items}}, 20, 20


class NarrativeFlow(unittest.TestCase):
    def setUp(self):
        self.fixture = h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
        self.cid, self.owner = self.fixture.cid, self.fixture.owner
        with app_module.connect() as db:
            result = importer.confirm_h32_drafts(db, self.fixture.batch, self.cid,
                self.fixture.file['id'], self.fixture.segments(), **self.fixture.arguments(db))
            self.entries = [x['entryId'] for x in result['items']]
            metadata_jobs.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)

    def tearDown(self):
        self.fixture.doCleanups()

    def create(self, index=0, **kwargs):
        with app_module.connect() as db:
            return narrative.create_job(db, self.entries[index], self.cid, self.owner, **kwargs)

    def execute(self, job_id, handler=response):
        adapter = MockNarrativeAdapter(handler)
        result = narrative.run_job(app_module.connect, job_id, self.cid, self.owner, adapter)
        return result, adapter

    def connect_with_lost_commit_ack(self, job_id, expected_state):
        fired = []
        def connect():
            db = app_module.connect()
            class LostAck:
                def __enter__(self):
                    return db.__enter__()
                def __exit__(self, exc_type, exc, traceback):
                    result = db.__exit__(exc_type, exc, traceback)
                    if exc_type is None and not fired:
                        with app_module.connect() as check:
                            row = check.execute('''SELECT state,used_cost_units,output_json
                                FROM mv3_model_chunks WHERE job_id=?''', (job_id,)).fetchone()
                        if (row and row['state'] == expected_state and
                                row['used_cost_units'] == 40 and
                                (row['output_json'] is not None) == (expected_state == 'completed')):
                            fired.append(True)
                            raise sqlite3.OperationalError('synthetic_commit_ack_lost')
                    return result
            return LostAck()
        return connect, fired

    def test_single_batch_creates_only_m3_draft_and_reuses_result(self):
        jid = self.create()
        self.assertEqual(jid, self.create())
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            self.assertEqual((version['status'], version['review_status']),
                             ('needs_review', 'unreviewed'))
            self.assertIsNone(m3._validity(db, version['id'], self.cid, set(), draft=True))
            self.assertEqual(metadata.read_draft(db, self.entries[0], self.cid)
                             ['fields']['narrative']['completionStatus'], 'missing')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks WHERE method=?',
                                        ('owner',)).fetchone()[0], 0)
            mapping = json.loads(version['mapping_json'])
            self.assertEqual(len(mapping), 4)
            self.assertTrue(all(x['rawStart'] is None and x['rawEnd'] is None for x in mapping))
        repeat, other = self.execute(jid)
        self.assertEqual(repeat['versionId'], result['versionId'])
        self.assertTrue(repeat['reused'])
        self.assertEqual(other.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)

    def test_multi_batch_actual_inputs_are_complete_ordered_and_bounded(self):
        jid = self.create(6, input_cap=1600, output_cap=2048, total_cap=80000)
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertGreaterEqual(len(adapter.calls), 3)
        seen = [(s['sourceKind'], s['sourceId'], s['speaker'])
                for call in adapter.calls for s in call['messages']]
        with app_module.connect() as db:
            refs = metadata.read_draft(db, self.entries[6], self.cid)['sourceRefs']
            expected = [(r['source_kind'], r['source_id'], r['source_role']) for r in refs]
            self.assertEqual(seen, expected)
            chunks = db.execute('SELECT state,used_cost_units,request_id,dispatch_attempt_id '
                'FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal', (jid,)).fetchall()
            self.assertEqual(len(chunks), len(adapter.calls))
            self.assertEqual(len({r['request_id'] for r in chunks}), len(chunks))
            self.assertTrue(all(r['state'] == 'completed' and r['used_cost_units'] == 40 and
                                r['dispatch_attempt_id'] for r in chunks))

    def test_unconfirmed_source_list_rejected_without_dispatch(self):
        with app_module.connect() as db:
            mid = db.execute('SELECT message_id FROM mv2_import_receipts ORDER BY ordinal').fetchone()[0]
            other = metadata.create_draft(db, self.cid, [{'kind':'message','id':str(mid)}],
                                          '合成未确认', self.owner, 'unconfirmed-a35')
            with self.assertRaisesRegex(ValueError, 'boundary_confirmation_required'):
                narrative.create_job(db, other['entryId'], self.cid, self.owner)

    def test_unknown_and_missing_usage_keep_reservation_and_scope(self):
        jid = self.create()
        def timeout(_):
            raise TimeoutError('synthetic_result_unknown')
        result, adapter = self.execute(jid, timeout)
        self.assertEqual(result['status'], 'unknown')
        with app_module.connect() as db:
            job = db.execute('SELECT status FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
            row = db.execute('SELECT state,reserved_tokens,used_cost_units FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual((job['status'], row['state']), ('unknown', 'unknown'))
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertIsNone(row['used_cost_units'])
            with self.assertRaises(ValueError):
                narrative.create_job(db, self.entries[0], self.cid, self.owner)
        with self.assertRaises(ValueError):
            self.execute(jid)
        self.assertEqual(len(adapter.calls), 1)
        missing = self.create(1)
        result, _ = self.execute(missing, lambda request: (response(request)[0], None, 20))
        self.assertEqual(result['status'], 'unknown')

    def test_cancel_after_dispatch_settles_without_version(self):
        jid = self.create()
        def cancel_then_return(request):
            with app_module.connect() as db:
                self.assertEqual(narrative.cancel_job(db, jid, self.cid, self.owner), 'unknown')
            return response(request)
        result, adapter = self.execute(jid, cancel_then_return)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name=?',
                                        ('narrative',)).fetchone()[0], 0)

    def test_source_change_settles_usage_and_rejects_product(self):
        jid = self.create()
        def change(request):
            with app_module.connect() as db:
                db.execute('UPDATE messages SET content=? WHERE id=?',
                           ('合成来源变化', int(request['messages'][0]['sourceId'])))
            return response(request)
        result, _ = self.execute(jid, change)
        self.assertEqual(result['status'], 'failed')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_missing_binding_cannot_use_old_read_review_or_activate(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            mapping = json.loads(version['mapping_json'])
            del mapping[0]['entryId']
            db.execute('UPDATE mv3_versions SET mapping_json=? WHERE id=?',
                       (json.dumps(mapping), version['id']))
            self.assertEqual(m3._validity(db, version['id'], self.cid, set(), draft=True),
                             'narrative_binding_invalid')
            with self.assertRaisesRegex(ValueError, 'narrative_binding_invalid'):
                m3.review_version(db, version['id'], self.cid, self.owner,
                                  version['output_hash'], version['input_hash'])
            with self.assertRaises(ValueError):
                m3.activate_version(db, version['id'], self.cid)
            with self.assertRaises(ValueError):
                m3.read_version(db, version['id'], self.cid)

    def test_boundary_change_blocks_review_and_later_direct_read(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            db.execute('UPDATE mv2_import_boundary_confirmations SET plan_hash=? WHERE file_id=?',
                       ('b'*64, self.fixture.file['id']))
            self.assertIn(m3._validity(db, version['id'], self.cid, set(), draft=True),
                          ('narrative_boundary_changed','narrative_binding_invalid'))
            with self.assertRaises(ValueError):
                m3.review_version(db, version['id'], self.cid, self.owner,
                                  version['output_hash'], version['input_hash'])

    def test_owner_review_activate_link_is_separate_and_vector_stays_ineligible(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            with self.assertRaisesRegex(ValueError, 'valid_review_required'):
                m3.activate_version(db, version['id'], self.cid)
            m3.review_version(db, version['id'], self.cid, self.owner,
                              version['output_hash'], version['input_hash'])
            active = m3.activate_version(db, version['id'], self.cid)
            self.assertEqual(active['status'], 'active')
            detail = metadata.read_draft(db, self.entries[0], self.cid)
            self.assertEqual(detail['fields']['narrative']['completionStatus'], 'missing')
            linked = metadata.revise(db, self.entries[0], self.cid, detail['versionId'],
                                     {'narrative': version['id']}, self.owner)
            self.assertEqual(linked['fields']['narrative']['lifecycleStatus'], 'draft')
            self.assertNotEqual(linked['fields']['narrative']['reviewStatus'], 'human_checked')
            metadata.review_field(db, self.entries[0], self.cid, 'narrative',
                                  linked['fields']['narrative']['token'], self.owner)
            detail = metadata.read_draft(db, self.entries[0], self.cid)
            self.assertEqual(detail['fields']['narrative']['lifecycleStatus'], 'available')
            self.assertFalse(detail['qualifications']['formalContext'])
            from backend.app.memory_v2_metadata_contract import a3_qualifications
            qualification = a3_qualifications(source_valid=True,
                field_statuses={'title':'available','narrative':'available'},
                entry_published=True, vector_index_valid=True)
            self.assertFalse(qualification['vectorCandidateRecall'])

    def test_lease_expiry_then_resume_uses_persisted_output(self):
        jid = self.create()
        def expire(request):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (jid,))
            return response(request)
        first, adapter = self.execute(jid, expire)
        self.assertEqual(first['status'], 'paused', first)
        with app_module.connect() as db:
            row = db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
            self.assertEqual(processing.resume(db, jid, self.cid, json.loads(row[0])), 'queued')
        final, retry = self.execute(jid)
        self.assertEqual(final['status'], 'completed', final)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(retry.calls, [])

    def test_business_write_failure_keeps_settlement_and_retries_without_dispatch(self):
        jid = self.create()
        with patch.object(m3, 'add_version', side_effect=sqlite3.IntegrityError('synthetic_failure')):
            first, adapter = self.execute(jid)
        self.assertEqual(first['status'], 'paused', first)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)
            row = db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
            self.assertEqual(processing.resume(db, jid, self.cid, json.loads(row[0])), 'queued')
        result, other = self.execute(jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(other.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 1)

    def test_manual_null_blocks_automatic_creation(self):
        with app_module.connect() as db:
            detail = metadata.read_draft(db, self.entries[0], self.cid)
            metadata.revise(db, self.entries[0], self.cid, detail['versionId'],
                            {'narrative':None}, self.owner)
            with self.assertRaisesRegex(ValueError, 'narrative_field_protected'):
                narrative.create_job(db, self.entries[0], self.cid, self.owner)

    def test_user_first_person_error_rejected_with_usage_settled(self):
        jid = self.create()
        def wrong(request):
            data, used_in, used_out = response(request)
            data['output']['items'][0]['text'] = '她说：我买了东西。'
            return data, used_in, used_out
        result, adapter = self.execute(jid, wrong)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'user_first_person_not_converted')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                             (jid,)).fetchone()
            self.assertEqual((row['state'], row['used_cost_units']), ('rejected', 40))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_new_mapping_never_downgrades_to_old_reader(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version_id = result['versionId']
            original = db.execute('SELECT mapping_json FROM mv3_versions WHERE id=?',
                                  (version_id,)).fetchone()[0]
            mapping = json.loads(original)
            del mapping[-1]['boundaryBinding']
            for bad in (mapping, [], ['not-a-map']):
                db.execute('UPDATE mv3_versions SET mapping_json=? WHERE id=?',
                           (json.dumps(bad), version_id))
                self.assertIsNotNone(m3._validity(db, version_id, self.cid, set(), draft=True))
            db.execute('UPDATE mv3_versions SET mapping_json=? WHERE id=?',
                       (original, version_id))
            self.assertIsNone(m3._validity(db, version_id, self.cid, set(), draft=True))

    def test_receipt_and_role_revocation_hide_direct_and_indirect_body(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            m3.review_version(db, version['id'], self.cid, self.owner,
                              version['output_hash'], version['input_hash'])
            m3.activate_version(db, version['id'], self.cid)
            detail = metadata.read_draft(db, self.entries[0], self.cid)
            metadata.revise(db, self.entries[0], self.cid, detail['versionId'],
                            {'narrative': version['id']}, self.owner)
            self.assertTrue(m3.read_version(db, version['id'], self.cid)['body'])
            db.execute('UPDATE mv2_import_receipts SET original_time_text=? WHERE ordinal=1',
                       ('synthetic-receipt-change',))
            with self.assertRaises(ValueError):
                m3.read_version(db, version['id'], self.cid)
            with self.assertRaises(ValueError):
                metadata.read_draft(db, self.entries[0], self.cid)
            db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
            with self.assertRaises(ValueError):
                m3.read_version(db, version['id'], self.cid)

    def test_source_change_between_review_and_activation_blocks_activation(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            m3.review_version(db, version['id'], self.cid, self.owner,
                              version['output_hash'], version['input_hash'])
            db.execute('UPDATE mv2_import_boundary_confirmations SET plan_hash=? WHERE file_id=?',
                       ('c'*64, self.fixture.file['id']))
            with self.assertRaises(ValueError):
                m3.activate_version(db, version['id'], self.cid)
            self.assertEqual(db.execute('SELECT status FROM mv3_versions WHERE id=?',
                                        (version['id'],)).fetchone()[0], 'needs_review')

    def test_unrelated_synopsis_change_does_not_invalidate_raw_narrative(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            detail = metadata.read_draft(db, self.entries[0], self.cid)
            metadata.revise(db, self.entries[0], self.cid, detail['versionId'],
                            {'synopsis':'合成梗概，不作为纪事事实输入'}, self.owner)
            self.assertIsNone(m3._validity(db, result['versionId'], self.cid,
                                           set(), draft=True))

    def test_declared_semantic_checks_preserve_marked_content(self):
        source = [{'sourceKind':'message','sourceId':'1','speaker':'assistant',
                   'sourceStatus':'complete',
                   'content':'如果没去，明天再说。其实是雨，不是雪。ありがとう（没关系）…'}]
        correct = {'finishReason':'stop','output':{'items':[
            {'sourceKind':'message','sourceId':'1','speaker':'assistant',
             'text':'我说：如果没去，明天再说。其实是雨，不是雪。ありがとう（没关系）…'}]}}
        items, _ = fidelity.validate_chunk(source, correct, kind='NARRATIVE')
        narrative._validate_v3_attribution(source, items)
        for removed in ('如果', '没', '其实', 'ありがとう'):
            wrong = json.loads(json.dumps(correct, ensure_ascii=False))
            changed = wrong['output']['items'][0]['text'].replace(removed, '')
            wrong['output']['items'][0]['text'] = changed.replace('不', '') if removed == '没' else changed
            with self.assertRaises(ValueError):
                fidelity.validate_chunk(source, wrong, kind='NARRATIVE')
        self.assertIn('（没关系）', items[0]['text'])
        self.assertIn('…', items[0]['text'])

    def test_wrong_order_duplicate_or_missing_batch_item_rejected(self):
        for index, corrupt in enumerate(('missing','duplicate','swapped')):
            jid = self.create(index)
            def wrong(request):
                data, used_in, used_out = response(request)
                items = data['output']['items']
                if corrupt == 'missing':
                    items.pop()
                elif corrupt == 'duplicate':
                    items[1]['sourceId'] = items[0]['sourceId']
                else:
                    items[0], items[1] = items[1], items[0]
                return data, used_in, used_out
            result, adapter = self.execute(jid, wrong)
            self.assertEqual(result['status'], 'failed', (corrupt, result))
            self.assertEqual(len(adapter.calls), 1)
            with app_module.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_output_budget_is_bounded_without_truncation(self):
        jid = self.create(output_cap=256)
        def excessive(request):
            data, used_in, used_out = response(request)
            data['output']['items'][0]['text'] += '合成扩展' * 200
            return data, used_in, used_out
        result, adapter = self.execute(jid, excessive)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'narrative_output_over_limit')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)

    def test_cancel_before_dispatch_never_calls_adapter(self):
        jid = self.create()
        with app_module.connect() as db:
            self.assertEqual(narrative.cancel_job(db, jid, self.cid, self.owner), 'cancelled')
        adapter = MockNarrativeAdapter(response)
        with self.assertRaises(ValueError):
            narrative.run_job(app_module.connect, jid, self.cid, self.owner, adapter)
        self.assertEqual(adapter.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 0)

    def test_source_change_after_activation_before_link_blocks_link(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            m3.review_version(db, version['id'], self.cid, self.owner,
                              version['output_hash'], version['input_hash'])
            m3.activate_version(db, version['id'], self.cid)
            db.execute('UPDATE mv2_import_receipts SET original_time_text=? WHERE ordinal=1',
                       ('synthetic-source-revision',))
            with self.assertRaises(ValueError):
                m3.read_version(db, version['id'], self.cid)
            with self.assertRaises(ValueError):
                metadata.read_draft(db, self.entries[0], self.cid)

    def test_all_batches_settled_then_business_crash_reuses_results(self):
        jid = self.create(2, input_cap=1600, output_cap=2048, total_cap=50000)
        with patch.object(m3, 'add_version', side_effect=sqlite3.IntegrityError('synthetic_write_failure')):
            first, adapter = self.execute(jid)
        self.assertEqual(first['status'], 'paused', first)
        self.assertGreaterEqual(len(adapter.calls), 3)
        with app_module.connect() as db:
            rows = db.execute('SELECT state,used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                              (jid,)).fetchall()
            self.assertEqual(len(rows), len(adapter.calls))
            self.assertTrue(all(r['state'] == 'completed' and r['used_cost_units'] == 40 for r in rows))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            read_set = json.loads(db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?',
                                             (jid,)).fetchone()[0])
            self.assertEqual(processing.resume(db, jid, self.cid, read_set), 'queued')
        final, retry = self.execute(jid)
        self.assertEqual(final['status'], 'completed', final)
        self.assertEqual(retry.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 1)

    def test_single_message_and_total_budget_reject_before_dispatch(self):
        with app_module.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0]
            with self.assertRaises(ValueError):
                narrative.create_job(db, self.entries[0], self.cid, self.owner,
                                     input_cap=512)
            with self.assertRaisesRegex(ValueError, 'narrative_total_budget_exceeded'):
                narrative.create_job(db, self.entries[0], self.cid, self.owner,
                                     total_cap=100)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0], before)

    def test_output_persistence_failure_settles_known_usage_without_product(self):
        jid = self.create()
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_output_failure
                BEFORE UPDATE OF output_json ON mv3_model_chunks
                WHEN NEW.output_json IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'synthetic_output_failure'); END''')
        result, adapter = self.execute(jid)
        self.assertEqual(result, {'status':'failed',
                                  'reason':'narrative_output_persistence_failed'})
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            parent = db.execute('SELECT status,result_version_id FROM mv3_jobs WHERE id=?',
                                (jid,)).fetchone()
            row = db.execute('''SELECT state,used_input_tokens,used_output_tokens,
                used_cost_units,reserved_tokens,output_json,request_id,dispatch_attempt_id
                FROM mv3_model_chunks WHERE job_id=?''', (jid,)).fetchone()
            self.assertEqual((parent['status'], parent['result_version_id']), ('failed', None))
            self.assertEqual((row['state'], row['used_input_tokens'],row['used_output_tokens'],
                              row['used_cost_units'],row['output_json']),
                             ('rejected',20,20,40,None))
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertEqual(db.execute('''SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?
                AND state IN ('possibly_dispatched','unknown')''', (jid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            request_identity = (row['request_id'], row['dispatch_attempt_id'])
            with self.assertRaises(ValueError):
                narrative.recover_job(db, jid, self.cid, self.owner, now=time.time()+1000)
        with self.assertRaises(ValueError):
            self.execute(jid)
        with app_module.connect() as db:
            row = db.execute('''SELECT used_cost_units,request_id,dispatch_attempt_id
                FROM mv3_model_chunks WHERE job_id=?''', (jid,)).fetchone()
            self.assertEqual((row['request_id'],row['dispatch_attempt_id']), request_identity)
            self.assertEqual(row['used_cost_units'], 40)

    def test_output_failure_in_middle_stops_later_batches(self):
        jid = self.create(6, input_cap=1600, output_cap=2048, total_cap=80000)
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_second_output_failure
                BEFORE UPDATE OF output_json ON mv3_model_chunks
                WHEN NEW.output_json IS NOT NULL AND NEW.ordinal=1
                BEGIN SELECT RAISE(ABORT, 'synthetic_second_output_failure'); END''')
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(adapter.calls), 2)
        with app_module.connect() as db:
            rows = db.execute('''SELECT state,used_cost_units,output_json
                FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal''', (jid,)).fetchall()
            self.assertGreaterEqual(len(rows), 3)
            self.assertEqual((rows[0]['state'],rows[0]['used_cost_units']), ('completed',40))
            self.assertEqual((rows[1]['state'],rows[1]['used_cost_units'],rows[1]['output_json']),
                             ('rejected',40,None))
            self.assertTrue(all(r['state']=='prepared' and r['used_cost_units'] is None
                                for r in rows[2:]))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.execute(jid)

    def test_unwritable_settlement_keeps_unknown_and_reservation(self):
        jid = self.create()
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_output_failure
                BEFORE UPDATE OF output_json ON mv3_model_chunks
                WHEN NEW.output_json IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'synthetic_output_failure'); END''')
            db.execute('''CREATE TRIGGER synthetic_settlement_failure
                BEFORE UPDATE OF state ON mv3_model_chunks
                WHEN NEW.state='rejected'
                BEGIN SELECT RAISE(ABORT, 'synthetic_settlement_failure'); END''')
        adapter = MockNarrativeAdapter(response)
        with self.assertRaisesRegex(RuntimeError, 'narrative_observed_usage_not_persisted'):
            narrative.run_job(app_module.connect, jid, self.cid, self.owner, adapter)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            parent = db.execute('SELECT status FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
            row = db.execute('''SELECT state,used_cost_units,reserved_tokens,output_json
                FROM mv3_model_chunks WHERE job_id=?''', (jid,)).fetchone()
            self.assertEqual((parent['status'],row['state'],row['used_cost_units'],row['output_json']),
                             ('unknown','possibly_dispatched',None,None))
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.execute(jid)

    def test_normal_commit_ack_lost_is_verified_without_double_charge(self):
        jid = self.create()
        connect, fired = self.connect_with_lost_commit_ack(jid, 'completed')
        adapter = MockNarrativeAdapter(response)
        result = narrative.run_job(connect, jid, self.cid, self.owner, adapter)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(fired, [True])
        self.assertEqual(len(adapter.calls), 1)
        repeat, other = self.execute(jid)
        self.assertEqual(repeat['versionId'], result['versionId'])
        self.assertEqual(other.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 1)

    def test_failure_settlement_ack_lost_is_verified_without_retry(self):
        jid = self.create()
        with app_module.connect() as db:
            db.execute('''CREATE TRIGGER synthetic_output_failure
                BEFORE UPDATE OF output_json ON mv3_model_chunks
                WHEN NEW.output_json IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'synthetic_output_failure'); END''')
        connect, fired = self.connect_with_lost_commit_ack(jid, 'rejected')
        adapter = MockNarrativeAdapter(response)
        result = narrative.run_job(connect, jid, self.cid, self.owner, adapter)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'narrative_output_persistence_failed')
        self.assertEqual(fired, [True])
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('''SELECT state,used_cost_units,output_json
                FROM mv3_model_chunks WHERE job_id=?''', (jid,)).fetchone()
            self.assertEqual((row['state'],row['used_cost_units'],row['output_json']),
                             ('rejected',40,None))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_crash_after_settled_output_recovers_without_new_dispatch(self):
        jid = self.create()
        adapter = MockNarrativeAdapter(response)
        with patch.object(narrative, '_finish', side_effect=RuntimeError('synthetic_post_commit_crash')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic_post_commit_crash'):
                narrative.run_job(app_module.connect, jid, self.cid, self.owner, adapter)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('''SELECT state,used_cost_units,output_json
                FROM mv3_model_chunks WHERE job_id=?''', (jid,)).fetchone()
            self.assertEqual((row['state'],row['used_cost_units']), ('completed',40))
            self.assertTrue(row['output_json'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (jid,))
            self.assertEqual(narrative.recover_job(db, jid, self.cid, self.owner), 'paused')
            read_set = json.loads(db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?',
                                             (jid,)).fetchone()[0])
            self.assertEqual(processing.resume(db, jid, self.cid, read_set), 'queued')
        result, retry = self.execute(jid)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(retry.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 1)

    def test_old_sender_cannot_override_new_claimant_after_settlement(self):
        jid = self.create()
        original = narrative._finish
        def handoff(db, job, payload, old_lease):
            db.execute("UPDATE mv3_jobs SET lease_token=?,lease_until=?,status='running' WHERE id=?",
                       ('synthetic-new-owner',time.time()+60,jid))
            return original(db, job, payload, old_lease)
        with patch.object(narrative, '_finish', side_effect=handoff):
            result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT status,lease_token,result_version_id FROM mv3_jobs WHERE id=?',
                             (jid,)).fetchone()
            self.assertEqual((row['status'],row['lease_token'],row['result_version_id']),
                             ('running','synthetic-new-owner',None))
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',
                                        (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_two_batch_correction_is_preserved_and_omission_rejected(self):
        def source(source_id, role, text):
            return SimpleNamespace(kind='message',source_id=source_id,role=role,text=text,
                occurred_at=None,status='imported',content_hash='a'*64,state_hash='b'*64)
        raw = [source('1','assistant','先说甲。'), source('2','user','其实更正为乙。')]
        batches = model_adapter.batches(raw, narrative._ByteTokenizer(),
                                        input_cap=2000, target_chars=8)
        self.assertEqual(len(batches), 2)
        checked = []
        for batch in batches:
            data, _, _ = response({'messages':batch})
            items, warnings = fidelity.validate_chunk(batch, data, kind='NARRATIVE')
            narrative._validate_v3_attribution(batch, items)
            checked.append({'items':items,'warnings':warnings})
        joined, _ = fidelity.join_checked(checked)
        self.assertEqual([item['sourceId'] for item in joined], ['1','2'])
        self.assertIn('更正', joined[1]['text'])
        bad = {'finishReason':'stop','output':{'items':[
            dict(checked[1]['items'][0], text='她说：乙。')]}}
        with self.assertRaisesRegex(ValueError, 'correction_dropped'):
            fidelity.validate_chunk(batches[1], bad, kind='NARRATIVE')


if __name__ == '__main__':
    unittest.main()
