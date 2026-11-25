"""Synthetic A3.6 DIGEST path; no Provider or private data."""
import json
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_derivation as m3
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_temporal_digest_schema as temporal_schema
from backend.app import memory_v2_temporal_digest as temporal
from backend.app import memory_v2_time_projection as p2
from backend.app.memory_v2_model_adapter import MockTemporalDigestAdapter


NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def response(request):
    return ({'finishReason': 'stop',
             'output': {'items': [{'inputId': x['inputId'], 'text': '合成：' + x['text'][:40]}
                                  for x in request['inputs']]}}, 20, 20)


class TemporalDigestFlow(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            db.execute('UPDATE messages SET created_at=? WHERE id IN (?,?)',
                       ('2026-01-01T12:00:00+00:00', self.user, self.assistant))
            self._index(db)
            metadata.init_db(db)
            p2.init_db(db)
            item = metadata.create_draft(db, self.cid, self.refs(), '合成标题', 1, 'a36-entry')
            metadata.review_field(db, item['entryId'], self.cid, 'title',
                                  item['fields']['title']['token'], 1)
            metadata.publish(db, item['entryId'], self.cid, item['versionId'], 1)
            self.entry_id = item['entryId']
        with app_module.connect() as db:
            jobs.init_db(db)
            processing_schema.migrate(db)
            temporal_schema.migrate(db)
            self.node = next(n for n in p2._tree(db, self.cid, NOW, 480) if n.level == 'L1')

    def create(self):
        with app_module.connect() as db:
            return temporal.create_job(db, self.cid, self.node.id, 1, now=NOW)

    def execute(self, job_id, callback=response):
        adapter = MockTemporalDigestAdapter(callback)
        result = temporal.run_job(app_module.connect, job_id, self.cid, 1, adapter)
        return result, adapter

    def test_raw_digest_requires_review_and_preserves_exact_sources(self):
        jid = self.create()
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            self.assertEqual(version['status'], 'needs_review')
            self.assertIsNone(m3._validity(db, version['id'], self.cid, set(), draft=True))
            binding = db.execute('SELECT * FROM mv3_time_digest_bindings_v2 '
                                 'WHERE digest_version_id=?', (version['id'],)).fetchone()
            self.assertEqual(binding['input_profile'], 'raw_sources_v1')
            manifest = json.loads(binding['input_manifest_json'])
            self.assertEqual(manifest['members'][0]['entryId'], self.entry_id)
            self.assertEqual(p2._digest(db, self.cid, self.node)[1], 'stale_or_unreviewed')
            self.approve(db, version['id'])
            selected = p2.context_projection(db, self.cid, [self.entry_id], now=NOW)
            self.assertEqual(selected['blocks'][0]['projectionKind'], 'period_digest')
            self.assertEqual(selected['blocks'][0]['digestVersionId'], version['id'])
        repeat, other = self.execute(jid)
        self.assertTrue(repeat['reused'])
        self.assertEqual(other.calls, [])

    def test_recorded_time_never_creates_period_node(self):
        with app_module.connect() as db:
            db.execute('UPDATE messages SET created_at=? WHERE id=?',
                       ('unknown-date', self.user))
            self._index(db)
            self.assertFalse(any(n.level in ('L1', 'L2', 'L3', 'L4')
                                 for n in p2._tree(db, self.cid, NOW, 480)))

    def test_old_period_digest_direct_reader_rejects_recorded_only_evidence(self):
        with app_module.connect() as db:
            derivation = m3.create_derivation(db, self.cid, 'DIGEST', self.refs())
            version = m3.add_version(db, derivation['id'], self.refs(),
                '旧合成周期文本', author_kind='model',
                rule_version='model-digest-m35-v1:L1:2026-W01')
            self.approve(db, version['id'])
            original = m3.live_source
            def recorded_only(connection, cid, kind, source_id):
                live = original(connection, cid, kind, source_id)
                return replace(live, occurred_at=None, occurred_date=None,
                               time_precision='local_datetime_unverified')
            with patch.object(m3, 'live_source', side_effect=recorded_only):
                with self.assertRaisesRegex(ValueError, 'digest_time_unverified'):
                    m3.read_version(db, version['id'], self.cid)

    def test_three_batch_first_completed_second_unknown_third_prepared(self):
        with app_module.connect() as db:
            messages = []
            for index in range(5):
                mid = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) "
                                 "VALUES(?,'user',?,?)",
                                 (self.conv, '合成事项' + str(index) + '甲'*105,
                                  '2026-01-02T12:00:00+00:00')).lastrowid
                messages.append(mid)
            self._index(db)
            for index, mid in enumerate(messages):
                item = metadata.create_draft(db, self.cid,
                    [{'kind':'message','id':str(mid)}], f'合成{index}', 1, f'a36-batch-{index}')
                metadata.review_field(db, item['entryId'], self.cid, 'title',
                                      item['fields']['title']['token'], 1)
                metadata.publish(db, item['entryId'], self.cid, item['versionId'], 1)
            week = next(n for n in p2._tree(db, self.cid, NOW, 480)
                        if n.level == 'L1' and len(n.members) == 6)
            jid = temporal.create_job(db, self.cid, week.id, 1, now=NOW,
                                      input_cap=1400, output_cap=512)
            job = db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
            self.assertGreaterEqual(len(json.loads(job['read_set_json'])['batchIds']), 3)
        calls = []
        def second_unknown(request):
            calls.append(request['ordinal'])
            if request['ordinal'] == 1:
                raise TimeoutError('synthetic_second_unknown')
            return response(request)
        result, adapter = self.execute(jid, second_unknown)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(calls, [0, 1])
        self.assertEqual(len(adapter.calls), 2)
        with app_module.connect() as db:
            states = [r[0] for r in db.execute(
                'SELECT state FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal', (jid,))]
            self.assertEqual(states[:3], ['completed', 'unknown', 'prepared'])
            self.assertEqual(jobs.recompute_parent(db, jid, self.cid), 'unknown')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_missing_child_blocks_and_owner_bound_composite_is_draft(self):
        later = datetime(2026, 2, 5, 12, tzinfo=timezone.utc)
        with app_module.connect() as db:
            monthly = next(n for n in p2._tree(db, self.cid, later, 480) if n.level == 'L2')
            with self.assertRaisesRegex(ValueError, 'child_digest_missing'):
                temporal.create_job(db, self.cid, monthly.id, 1, now=later)
            child = monthly.children[0]
            with self.assertRaisesRegex(ValueError, 'digest_owner_choice_required'):
                temporal.create_job(db, self.cid, monthly.id, 1, now=later,
                    composite_choice={'ownerId': 2, 'nodeId': monthly.id,
                                      'scopeHash': monthly.scope_hash,
                                      'fallbackChildIds': [child.id]})
            choice = temporal.preview_composite_choice(db, self.cid, monthly.id, 1,
                                                       [child.id], now=later)
            with self.assertRaisesRegex(ValueError, 'digest_composite_plan_changed'):
                temporal.create_job(db, self.cid, monthly.id, 1, now=later,
                    composite_choice={**choice, 'inputPlanHash': 'wrong'})
            jid = temporal.create_job(db, self.cid, monthly.id, 1, now=later,
                                      composite_choice=choice)
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT input_profile FROM mv3_time_digest_bindings_v2 '
                             'WHERE digest_version_id=?', (result['versionId'],)).fetchone()
            self.assertEqual(row['input_profile'], 'composite_v1')
            self.assertEqual(m3._row(db, 'mv3_versions', result['versionId'])['status'],
                             'needs_review')

    def test_direct_child_digest_and_scope(self):
        later = datetime(2026, 2, 5, 12, tzinfo=timezone.utc)
        with app_module.connect() as db:
            monthly = next(n for n in p2._tree(db, self.cid, later, 480) if n.level == 'L2')
            child = monthly.children[0]
            child_job = temporal.create_job(db, self.cid, child.id, 1, now=later)
        first, _ = self.execute(child_job)
        self.assertEqual(first['status'], 'completed', first)
        with app_module.connect() as db:
            self.approve(db, first['versionId'])
            parent_job = temporal.create_job(db, self.cid, monthly.id, 1, now=later)
        parent, adapter = self.execute(parent_job)
        self.assertEqual(parent['status'], 'completed', parent)
        self.assertEqual(adapter.calls[0]['inputs'][0]['kind'], 'child_digest')
        with app_module.connect() as db:
            row = db.execute('SELECT input_profile FROM mv3_time_digest_bindings_v2 '
                             'WHERE digest_version_id=?', (parent['versionId'],)).fetchone()
            self.assertEqual(row['input_profile'], 'child_digests_v1')
            self.assertEqual([x['child_version_id'] for x in m3._deps(db, parent['versionId'])],
                             [first['versionId']])

    def test_cross_month_week_cannot_supply_out_of_scope_child_digest(self):
        later = datetime(2026, 12, 15, 12, tzinfo=timezone.utc)
        with app_module.connect() as db:
            ids = []
            for day in ('2026-10-31', '2026-11-01'):
                mid = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) "
                                 "VALUES(?,'user',?,?)",
                                 (self.conv, '合成跨月事项' + day,
                                  day + 'T12:00:00+00:00')).lastrowid
                ids.append(mid)
            self._index(db)
            entry_ids = []
            for index, mid in enumerate(ids):
                item = metadata.create_draft(db, self.cid,
                    [{'kind': 'message', 'id': str(mid)}],
                    '合成跨月标题', 1, 'a36-month-' + str(index))
                metadata.review_field(db, item['entryId'], self.cid, 'title',
                                      item['fields']['title']['token'], 1)
                metadata.publish(db, item['entryId'], self.cid, item['versionId'], 1)
                entry_ids.append(item['entryId'])
            roots = p2._tree(db, self.cid, later, 480)
            october = next(n for n in roots if n.level == 'L2' and n.period == '2026-10')
            november = next(n for n in roots if n.level == 'L2' and n.period == '2026-11')
            oc_child, nv_child = october.children[0], november.children[0]
            self.assertEqual(oc_child.period, nv_child.period)
            self.assertNotEqual(oc_child.scope_hash, nv_child.scope_hash)
            self.assertEqual([m.entry_id for m in oc_child.members], [entry_ids[0]])
            self.assertEqual([m.entry_id for m in nv_child.members], [entry_ids[1]])

            # A historically reviewed whole-week body includes a November fact.
            # It cannot be reused as the October-only child of a monthly node.
            both_refs = ([{'kind': 'message', 'id': str(mid)} for mid in ids])
            owner = m3.create_derivation(db, self.cid, 'DIGEST', both_refs)
            whole = m3.add_version(db, owner['id'], both_refs,
                '十月与十一月两项合成事实。', author_kind='user', rule_version='manual-v1')
            self.approve(db, whole['id'])
            with self.assertRaisesRegex(ValueError, 'child_digest_scope_mismatch'):
                temporal._child_input(db, self.cid, oc_child,
                                      pinned={'childVersionId': whole['id']})
            with self.assertRaisesRegex(ValueError, 'digest_child_partition_invalid'):
                temporal._plan(db, self.cid,
                    replace(october, children=(oc_child, oc_child)), later, 480)
            with self.assertRaisesRegex(ValueError, 'digest_child_partition_invalid'):
                temporal._plan(db, self.cid,
                    replace(october, children=()), later, 480)

    def test_day_precision_at_all_age_boundaries_is_conservative(self):
        with app_module.connect() as db:
            original = p2.entries.read_entry
            def dated(connection, entry_id, character_id, *, day):
                detail = dict(original(connection, entry_id, character_id))
                detail['timeBasis'] = 'occurred'
                detail['occurredAt'] = [day, day]
                return detail
            for lower, boundary in enumerate((7, 30, 90, 180, 365)):
                for offset, expected in ((0, f'L{lower}'),
                                         (1, f'L{lower + 1}')):
                    day = (NOW - timedelta(days=boundary + offset)).date().isoformat()
                    with self.subTest(boundary=boundary, offset=offset), \
                            patch.object(p2.entries, 'read_entry',
                                side_effect=lambda connection, entry_id, character_id:
                                    dated(connection, entry_id, character_id, day=day)):
                        node = next(n for n in p2._tree(db, self.cid, NOW, 480)
                                    if any(m.entry_id == self.entry_id for m in n.members))
                        self.assertEqual(node.level, expected)
                        self.assertEqual(node.time_basis, 'occurred_day')

    def test_unknown_cancel_and_reservation(self):
        jid = self.create()
        def timeout(_request):
            raise TimeoutError('synthetic_unknown')
        result, adapter = self.execute(jid, timeout)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,reserved_tokens,used_cost_units FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual(row['state'], 'unknown')
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertIsNone(row['used_cost_units'])
            self.assertEqual(temporal.cancel_job(db, jid, self.cid, 1), 'unknown')
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (jid,)).fetchone()[0], 'unknown')
            with self.assertRaises(ValueError):
                temporal.create_job(db, self.cid, self.node.id, 1, now=NOW,
                                    input_cap=5000)
        with self.assertRaises(ValueError):
            self.execute(jid)

    def test_cancel_during_adapter_settles_without_version(self):
        jid = self.create()
        def cancel(request):
            with app_module.connect() as db:
                temporal.cancel_job(db, jid, self.cid, 1)
            return response(request)
        result, adapter = self.execute(jid, cancel)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks '
                                        'WHERE job_id=?', (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_source_change_settles_but_does_not_publish(self):
        jid = self.create()
        def change(request):
            with app_module.connect() as db:
                db.execute('UPDATE messages SET content=? WHERE id=?',
                           ('合成来源修订', self.user))
            return response(request)
        result, _ = self.execute(jid, change)
        self.assertEqual(result['status'], 'failed', result)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks '
                                        'WHERE job_id=?', (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_expired_sender_settles_usage_but_cannot_submit_business_result(self):
        jid = self.create()
        def expire(request):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (jid,))
            return response(request)
        result, adapter = self.execute(jid, expire)
        self.assertEqual(result['status'], 'paused', result)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,used_cost_units,output_json FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual((row['state'], row['used_cost_units']), ('completed', 40))
            self.assertIsNotNone(row['output_json'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_output_write_abort_settles_usage_without_result(self):
        jid = self.create()
        with app_module.connect() as db:
            db.execute("""CREATE TRIGGER synthetic_a36_output_failure
                BEFORE UPDATE OF output_json ON mv3_model_chunks
                BEGIN SELECT RAISE(ABORT,'synthetic_output_failure'); END""")
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,used_cost_units,output_json FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual((row['state'], row['used_cost_units'], row['output_json']),
                             ('rejected', 40, None))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.execute(jid)

    def test_output_and_independent_settlement_both_fail_keeps_unknown(self):
        jid = self.create()
        with app_module.connect() as db:
            db.execute("""CREATE TRIGGER synthetic_a36_output_and_settlement_failure
                BEFORE UPDATE ON mv3_model_chunks
                WHEN NEW.output_json IS NOT OLD.output_json OR
                     NEW.used_cost_units IS NOT OLD.used_cost_units
                BEGIN SELECT RAISE(ABORT,'synthetic_storage_unavailable'); END""")
        adapter = MockTemporalDigestAdapter(response)
        with self.assertRaisesRegex(RuntimeError, 'digest_observed_usage_not_persisted'):
            temporal.run_job(app_module.connect, jid, self.cid, 1, adapter)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,reserved_tokens,used_cost_units,output_json '
                             'FROM mv3_model_chunks WHERE job_id=?', (jid,)).fetchone()
            self.assertIn(row['state'], ('possibly_dispatched', 'unknown'))
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertIsNone(row['used_cost_units'])
            self.assertIsNone(row['output_json'])
            self.assertEqual(jobs.owned(db, jid, self.cid)['status'], 'unknown')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            temporal.run_job(app_module.connect, jid, self.cid, 1, adapter)
        self.assertEqual(len(adapter.calls), 1)

    def test_binding_missing_rejects_review_and_direct_read(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            version = m3._row(db, 'mv3_versions', result['versionId'])
            db.execute('DELETE FROM mv3_time_digest_bindings_v2 WHERE digest_version_id=?',
                       (version['id'],))
            self.assertEqual(m3._validity(db, version['id'], self.cid, set(), draft=True),
                             'digest_binding_missing')
            with self.assertRaisesRegex(ValueError, 'digest_binding_missing'):
                m3.review_version(db, version['id'], self.cid, 1,
                                  version['output_hash'], version['input_hash'])

    def test_wrong_level_binding_cannot_be_read_or_projected(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            self.approve(db, result['versionId'])
            db.execute('UPDATE mv3_time_digest_bindings_v2 SET level=? '
                       'WHERE digest_version_id=?', ('L2', result['versionId']))
            with self.assertRaises(ValueError):
                m3.read_version(db, result['versionId'], self.cid)
            node = next(n for n in p2._tree(db, self.cid, NOW, 480) if n.id == self.node.id)
            self.assertIsNone(p2._digest(db, self.cid, node)[0])

    def test_active_binding_damage_hides_body_and_p2_falls_back(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            self.approve(db, result['versionId'])
            db.execute('UPDATE mv3_time_digest_bindings_v2 SET input_token=? '
                       'WHERE digest_version_id=?', ('tampered', result['versionId']))
            with self.assertRaisesRegex(ValueError, 'digest_binding_invalid'):
                m3.read_version(db, result['versionId'], self.cid)
            opened = p2.expand_node(db, self.cid, self.node.id, now=NOW)
            self.assertIsNone(opened['summaryPage'])
            self.assertNotIn('summaryPreview', opened)
            projection = p2.context_projection(db, self.cid, [self.entry_id], now=NOW)
            self.assertNotEqual(projection['blocks'][0]['projectionKind'], 'period_digest')

    def test_unrelated_title_change_does_not_stale_raw_input(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            self.approve(db, result['versionId'])
            prior = metadata.read_draft(db, self.entry_id, self.cid)
            updated = metadata.revise(db, self.entry_id, self.cid, prior['versionId'],
                                      {'title': '合成新标题'}, 1)
            metadata.review_field(db, self.entry_id, self.cid, 'title',
                                  updated['fields']['title']['token'], 1)
            metadata.publish(db, self.entry_id, self.cid, updated['versionId'], 1)
            self.assertIsNone(m3.validity(db, result['versionId'], self.cid))
            self.assertEqual(p2._digest(db, self.cid, self.node)[0]['id'],
                             result['versionId'])

    def test_projection_timezone_cannot_reuse_other_binding(self):
        result, _ = self.execute(self.create())
        with app_module.connect() as db:
            self.approve(db, result['versionId'])
            other = next(n for n in p2._tree(db, self.cid, NOW, 0)
                         if n.level == 'L1')
            self.assertIsNone(p2._digest(db, self.cid, other)[0])
            self.assertEqual(p2._digest(db, self.cid, self.node)[0]['id'],
                             result['versionId'])

    def test_reviewed_synopsis_input_and_change_invalidation(self):
        with app_module.connect() as db:
            current = metadata.read_draft(db, self.entry_id, self.cid)
            updated = metadata.revise(db, self.entry_id, self.cid, current['versionId'],
                                      {'synopsis': '合成梗概。'}, 1)
            metadata.review_field(db, self.entry_id, self.cid, 'synopsis',
                                  updated['fields']['synopsis']['token'], 1)
            metadata.publish(db, self.entry_id, self.cid, updated['versionId'], 1)
        result, adapter = self.execute(self.create())
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(adapter.calls[0]['inputs'][0]['kind'], 'synopsis')
        with app_module.connect() as db:
            binding = db.execute('SELECT input_profile FROM mv3_time_digest_bindings_v2 '
                                 'WHERE digest_version_id=?', (result['versionId'],)).fetchone()
            self.assertEqual(binding['input_profile'], 'synopsis_v1')
            changed = metadata.revise(db, self.entry_id, self.cid, updated['versionId'],
                                      {'synopsis': '不同的合成梗概。'}, 1)
            metadata.review_field(db, self.entry_id, self.cid, 'synopsis',
                                  changed['fields']['synopsis']['token'], 1)
            metadata.publish(db, self.entry_id, self.cid, changed['versionId'], 1)
            self.assertEqual(m3._validity(db, result['versionId'], self.cid, set(), draft=True),
                             'digest_input_changed')

    def test_reviewed_narrative_takes_priority_over_synopsis(self):
        with app_module.connect() as db:
            derivation = m3.create_derivation(db, self.cid, 'NARRATIVE', self.refs())
            narrative = m3.add_version(db, derivation['id'], self.refs(),
                                       '她说合成内容。我说合成回应。')
            self.approve(db, narrative['id'])
            current = metadata.read_draft(db, self.entry_id, self.cid)
            updated = metadata.revise(db, self.entry_id, self.cid, current['versionId'],
                {'narrative': narrative['id'], 'synopsis': '合成梗概。'}, 1)
            for name in ('narrative', 'synopsis'):
                metadata.review_field(db, self.entry_id, self.cid, name,
                                      updated['fields'][name]['token'], 1)
            metadata.publish(db, self.entry_id, self.cid, updated['versionId'], 1)
        result, adapter = self.execute(self.create())
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(adapter.calls[0]['inputs'][0]['kind'], 'narrative')
        with app_module.connect() as db:
            row = db.execute('SELECT input_profile FROM mv3_time_digest_bindings_v2 '
                             'WHERE digest_version_id=?', (result['versionId'],)).fetchone()
            self.assertEqual(row['input_profile'], 'narrative_v1')

    def test_l1_mixed_inputs_bind_each_actual_field(self):
        with app_module.connect() as db:
            mid = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) "
                             "VALUES(?,'user',?,?)",
                             (self.conv, '第二条合成事项。',
                              '2026-01-02T12:00:00+00:00')).lastrowid
            self._index(db)
            item = metadata.create_draft(db, self.cid,
                                         [{'kind':'message','id':str(mid)}],
                                         '第二条合成标题', 1, 'a36-second')
            updated = metadata.revise(db, item['entryId'], self.cid, item['versionId'],
                                      {'synopsis':'第二条合成梗概。'}, 1)
            for name in ('title', 'synopsis'):
                metadata.review_field(db, item['entryId'], self.cid, name,
                                      updated['fields'][name]['token'], 1)
            metadata.publish(db, item['entryId'], self.cid, updated['versionId'], 1)
            week = next(n for n in p2._tree(db, self.cid, NOW, 480)
                        if n.level == 'L1' and len(n.members) == 2)
            jid = temporal.create_job(db, self.cid, week.id, 1, now=NOW)
        result, adapter = self.execute(jid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([x['kind'] for x in adapter.calls[0]['inputs']],
                         ['raw', 'synopsis'])
        with app_module.connect() as db:
            row = db.execute('SELECT input_profile,input_manifest_json FROM '
                             'mv3_time_digest_bindings_v2 WHERE digest_version_id=?',
                             (result['versionId'],)).fetchone()
            self.assertEqual(row['input_profile'], 'composite_v1')
            self.assertEqual([x['kind'] for x in json.loads(row['input_manifest_json'])['inputs']],
                             ['raw', 'synopsis'])
            self.approve(db, result['versionId'])
            selected = p2.context_projection(db, self.cid, [self.entry_id], now=NOW)
            self.assertNotEqual(selected['blocks'][0]['projectionKind'], 'period_digest')
            self.assertNotIn(result['versionId'], [x.get('digestVersionId')
                                                     for x in selected['blocks']])

    def test_missing_usage_retains_unknown_and_no_version(self):
        jid = self.create()
        result, adapter = self.execute(jid,
            lambda request: (response(request)[0], None, 20))
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            row = db.execute('SELECT state,reserved_tokens,used_cost_units FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual(row['state'], 'unknown')
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertIsNone(row['used_cost_units'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_nonfinal_response_retains_unknown_despite_reported_usage(self):
        jid = self.create()
        result, _ = self.execute(jid, lambda request: (
            {'finishReason': 'incomplete', 'output': {'items': []}}, 20, 20))
        self.assertEqual(result['status'], 'unknown')
        with app_module.connect() as db:
            row = db.execute('SELECT state,reserved_tokens,used_cost_units FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual(row['state'], 'unknown')
            self.assertGreater(row['reserved_tokens'], 0)
            self.assertIsNone(row['used_cost_units'])

    def test_finite_claim_guard_rejects_unsupported_psychology(self):
        jid = self.create()
        def hallucinate(request):
            return ({'finishReason':'stop',
                     'output':{'items':[{'inputId':x['inputId'],
                                        'text':'她已经明白了自己的心。'}
                                        for x in request['inputs']]}}, 20, 20)
        result, adapter = self.execute(jid, hallucinate)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks '
                                        'WHERE job_id=?', (jid,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_final_binding_failure_rolls_back_version_then_recovers_without_dispatch(self):
        jid = self.create()
        with app_module.connect() as db:
            db.execute("""CREATE TRIGGER synthetic_a36_binding_failure
                BEFORE INSERT ON mv3_time_digest_bindings_v2
                BEGIN SELECT RAISE(ABORT,'synthetic_binding_failure'); END""")
        first, adapter = self.execute(jid)
        self.assertEqual(first['status'], 'paused', first)
        self.assertEqual(len(adapter.calls), 1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)
            row = db.execute('SELECT state,output_json,used_cost_units FROM mv3_model_chunks '
                             'WHERE job_id=?', (jid,)).fetchone()
            self.assertEqual((row['state'], row['used_cost_units']), ('completed', 40))
            self.assertIsNotNone(row['output_json'])
            db.execute('DROP TRIGGER synthetic_a36_binding_failure')
            job = db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
            self.assertEqual(processing.resume(db, jid, self.cid,
                                               json.loads(job['read_set_json'])), 'queued')
        second, other = self.execute(jid)
        self.assertEqual(second['status'], 'completed', second)
        self.assertEqual(other.calls, [])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks '
                                        'WHERE job_id=?', (jid,)).fetchone()[0], 40)

    def test_migration_idempotent_and_partial_schema_rejected(self):
        with app_module.connect() as db:
            self.assertFalse(temporal_schema.migrate(db))
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())
            db.execute('DROP INDEX ix_mv3_time_digest_v2_node')
            with self.assertRaisesRegex(ValueError, 'a36_schema_invalid'):
                temporal.create_job(db, self.cid, self.node.id, 1, now=NOW)


if __name__ == '__main__':
    unittest.main()
