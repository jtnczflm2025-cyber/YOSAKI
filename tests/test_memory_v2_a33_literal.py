"""Synthetic A3.3 first step: finite literal keywords and local lifecycle."""
import json
import time
import unittest
from unittest.mock import patch

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_keyword_rules as rules
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as legacy_jobs
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as processing_schema


class LiteralRules(unittest.TestCase):
    def source(self, text):
        return [{'kind': 'message', 'id': '1', 'role': 'user', 'text': text}]

    def test_whole_script_runs_and_original_offsets(self):
        text = '  ＡＰＩ・俳句の季語・ﬃ・e\u0301cole・㍿  '
        result = rules.evaluate(self.source(text))['keywords']
        self.assertEqual([x['text'] for x in result], ['ＡＰＩ', '俳句', '季語', 'ﬃ', 'e\u0301cole', '㍿'])
        for item in result:
            point = item['evidence'][0]
            self.assertEqual(text[point['start']:point['end']], item['text'])
        self.assertEqual(rules.evaluate(self.source('ＡＰＩ api API'))['keywords'][0]['text'], 'ＡＰＩ')
        self.assertEqual(len(rules.evaluate(self.source('ＡＰＩ api API'))['keywords']), 1)

    def test_no_arbitrary_cjk_bigrams_or_asserted_facts(self):
        result = rules.evaluate(self.source('没去陶艺课。只是引用：“数据库恢复”。'))
        surfaces = [x['text'] for x in result['keywords']]
        self.assertNotIn('陶艺', surfaces)
        self.assertNotIn('数据', surfaces)
        self.assertNotIn('恢复', surfaces)
        self.assertIn('数据库恢复', surfaces)
        self.assertTrue(all(x['kind'] == 'literal' for x in result['keywords']))
        self.assertNotIn('数据库', [x['text'] for x in rules.evaluate(
            self.source('这是很长的一整串连续汉字数据库不会被拆成双字'))['keywords']])

    def test_first_32_distinct_in_source_order(self):
        result = rules.evaluate(self.source(' '.join(f'w{i:02d}' for i in range(40))))
        self.assertEqual([x['text'] for x in result['keywords']], [f'w{i:02d}' for i in range(32)])
        self.assertEqual(result['reasonCodes'], ['keyword_capacity_reached'])

    def test_empty_and_invalid(self):
        self.assertEqual(rules.evaluate(self.source('啊。'))['keywords'], [])
        with self.assertRaisesRegex(ValueError, 'keyword_source_invalid'):
            list(rules.spans(None))


class KeywordTasks(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            metadata.init_db(db); legacy_jobs.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)

    def entry(self, user='ＡＰＩ の仕様を調べる', assistant='API について話す'):
        with app_module.connect() as db:
            refs = []
            for role, text in (('user', user), ('assistant', assistant)):
                mid = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,'2026-01-01')",
                                 (self.conv, role, text)).lastrowid
                refs.append({'kind': 'message', 'id': str(mid)})
            self._index(db)
            return metadata.create_draft(db, self.cid, refs, '合成标题', 1, 'a33-'+refs[0]['id'])

    def run_keyword(self, entry):
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        return jid, processing.run_keyword_job(app_module.connect, jid, self.cid, 1)

    def test_candidate_evidence_no_tag_dependency_and_zero_external_effects(self):
        entry = self.entry()
        jid, result = self.run_keyword(entry)
        with app_module.connect() as db:
            after = metadata.read_draft(db, entry['entryId'], self.cid)
            field = after['fields']['keywords']
            self.assertEqual(field['lifecycleStatus'], 'draft')
            self.assertGreater(result['candidateCount'], 0)
            self.assertEqual(after['fields']['categories']['completionStatus'], 'missing')
            self.assertEqual(after['fields']['tags']['completionStatus'], 'missing')
            self.assertEqual(after['fields']['title']['fieldId'], entry['fields']['title']['fieldId'])
            self.assertFalse(after['qualifications']['formalContext'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?', (jid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM feedback_activities_v4').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM psychological_events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM learning_evidence').fetchone()[0], 0)
            for item in field['value']:
                self.assertEqual(item['kind'], 'literal')
                for point in item['evidence']:
                    text = db.execute('SELECT content FROM messages WHERE id=?', (point['id'],)).fetchone()[0]
                    self.assertEqual(text[point['start']:point['end']], item['text'])
            count = db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0]
        self.assertTrue(processing.run_keyword_job(app_module.connect, jid, self.cid, 1)['reused'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0], count)
            second_id = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        second = processing.run_keyword_job(app_module.connect, second_id, self.cid, 1)
        self.assertTrue(second['reused'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0], count)

    def test_no_hit_keeps_missing_and_no_new_field_set(self):
        entry = self.entry('啊。', '嗯。')
        jid, result = self.run_keyword(entry)
        self.assertEqual(result['candidateCount'], 0)
        with app_module.connect() as db:
            after = metadata.read_draft(db, entry['entryId'], self.cid)
            self.assertEqual(after['versionId'], entry['versionId'])
            self.assertEqual(after['fields']['keywords']['completionStatus'], 'missing')
        self.assertTrue(processing.run_keyword_job(app_module.connect, jid, self.cid, 1)['reused'])

    def test_manual_suppression_and_reviewed_candidate_are_protected(self):
        entry = self.entry()
        with app_module.connect() as db:
            suppressed = metadata.revise(db, entry['entryId'], self.cid, entry['versionId'], {'keywords': None}, 1)
        _, result = self.run_keyword(suppressed)
        self.assertEqual(result['candidateCount'], 0)
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db, entry['entryId'], self.cid)['fields']['keywords']['fieldId'],
                             suppressed['fields']['keywords']['fieldId'])
        other = self.entry('Docker ログ', 'Docker log')
        _, result = self.run_keyword(other)
        with app_module.connect() as db:
            current = metadata.read_draft(db, other['entryId'], self.cid)
            metadata.review_field(db, other['entryId'], self.cid, 'keywords', current['fields']['keywords']['token'], 1)
            reviewed_id = current['fields']['keywords']['fieldId']
        _, repeat = self.run_keyword(other)
        self.assertEqual(repeat['candidateCount'], 0)
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db, other['entryId'], self.cid)['fields']['keywords']['fieldId'], reviewed_id)

    def test_rule_upgrade_invalidates_field_and_inflight_job(self):
        entry = self.entry()
        jid, _ = self.run_keyword(entry)
        with app_module.connect() as db:
            pending = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        with patch.object(rules, 'RULE_VERSION', 'keyword-literal-raw-v2'):
            with app_module.connect() as db:
                self.assertEqual(metadata.read_draft(db, entry['entryId'], self.cid)['fields']['keywords']['lifecycleStatus'], 'stale')
            with self.assertRaisesRegex(ValueError, 'keyword_rule_outdated'):
                processing.run_keyword_job(app_module.connect, pending, self.cid, 1)
        self.assertNotEqual(jid, pending)

    def test_cancel_during_compute_prevents_write(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        original = rules.evaluate
        def cancel_then_return(sources):
            with app_module.connect() as db:
                processing.cancel_keyword_job(db, jid, self.cid, 1)
            return original(sources)
        with patch.object(rules, 'evaluate', side_effect=cancel_then_return):
            result = processing.run_keyword_job(app_module.connect, jid, self.cid, 1)
        self.assertEqual(result['status'], 'cancelled')
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db, entry['entryId'], self.cid)['fields']['keywords']['completionStatus'], 'missing')

    def test_expired_worker_cannot_submit_after_recovery(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        original = rules.evaluate
        def recover_then_return(sources):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (jid,))
                self.assertEqual(processing.recover_keyword_job(db, jid, self.cid, 1), 'paused')
                row = db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?', (jid,)).fetchone()
                processing.resume(db, jid, self.cid, json.loads(row['read_set_json']))
            return original(sources)
        with patch.object(rules, 'evaluate', side_effect=recover_then_return):
            with self.assertRaisesRegex(ValueError, 'keyword_lease_changed'):
                processing.run_keyword_job(app_module.connect, jid, self.cid, 1)
        self.assertEqual(processing.run_keyword_job(app_module.connect, jid, self.cid, 1)['status'], 'completed')

    def test_source_change_and_unknown_block(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
            first_source = entry['sourceRefs'][0]['source_id']
            db.execute('UPDATE messages SET content=? WHERE id=?', ('来源变化', first_source))
        with self.assertRaises(ValueError):
            processing.run_keyword_job(app_module.connect, jid, self.cid, 1)
        other = self.entry('Docker log', 'API response')
        with app_module.connect() as db:
            other_jid = processing.create_keyword_job(db, other['entryId'], self.cid, 1)
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?", (other_jid,))
            with self.assertRaises(ValueError):
                processing.create_keyword_job(db, other['entryId'], self.cid, 1)

    def test_role_revoked_before_claim(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
            db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        with self.assertRaisesRegex(ValueError, 'job_not_accessible'):
            processing.run_keyword_job(app_module.connect, jid, self.cid, 1)

    def test_cancel_before_claim_and_migration_hold(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
            self.assertEqual(processing.cancel_keyword_job(db, jid, self.cid, 1), 'cancelled')
        with self.assertRaisesRegex(ValueError, 'keyword_job_not_claimable'):
            processing.run_keyword_job(app_module.connect, jid, self.cid, 1)
        with app_module.connect() as db:
            held = legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA')
            db.execute('''INSERT INTO mv3_a3_migration_holds(job_id,prior_status,reason,evidence_json)
                VALUES(?,'paused','dispatch_or_settlement_unproven','{}')''', (held['id'],))
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?", (held['id'],))
            with self.assertRaisesRegex(ValueError, 'legacy_unknown_scope'):
                processing.create_keyword_job(db, entry['entryId'], self.cid, 1)

    def test_compute_then_manual_revision_prevents_stale_submit(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        original = rules.evaluate
        def edit_then_return(sources):
            with app_module.connect() as db:
                current = metadata.read_draft(db, entry['entryId'], self.cid)
                metadata.revise(db, entry['entryId'], self.cid, current['versionId'], {'keywords': None}, 1)
            return original(sources)
        with patch.object(rules, 'evaluate', side_effect=edit_then_return):
            result = processing.run_keyword_job(app_module.connect, jid, self.cid, 1)
        self.assertEqual(result['status'], 'failed')
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db, entry['entryId'], self.cid)['fields']['keywords']['lifecycleStatus'], 'disabled')

    def test_crash_in_final_write_rolls_back_and_recovers(self):
        entry = self.entry()
        with app_module.connect() as db:
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
            before = db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0]
        original = metadata.revise
        def revise_then_crash(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic_crash_before_commit')
        with patch.object(metadata, 'revise', side_effect=revise_then_crash):
            with self.assertRaisesRegex(RuntimeError, 'synthetic_crash_before_commit'):
                processing.run_keyword_job(app_module.connect, jid, self.cid, 1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?', (jid,)).fetchone()[0], 0)
            db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?', (jid,))
            self.assertEqual(processing.recover_keyword_job(db, jid, self.cid, 1), 'paused')
            read_set = json.loads(db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?', (jid,)).fetchone()[0])
            self.assertEqual(processing.resume(db, jid, self.cid, read_set), 'queued')
        self.assertEqual(processing.run_keyword_job(app_module.connect, jid, self.cid, 1)['status'], 'completed')

    def test_old_local_task_before_new_write_is_version_checked(self):
        entry = self.entry()
        with app_module.connect() as db:
            old = legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA')
            lease = jobs.claim(db, old['id'], self.cid)
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
        self.assertEqual(processing.run_keyword_job(app_module.connect, jid, self.cid, 1)['status'], 'completed')
        with app_module.connect() as db:
            result = jobs.execute_claimed(db, old['id'], self.cid, lease)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?', (old['id'],)).fetchone()[0], 0)

    def test_old_local_task_cannot_replace_unreviewed_new_candidate(self):
        entry = self.entry()
        self.run_keyword(entry)
        with app_module.connect() as db:
            current = metadata.read_draft(db, entry['entryId'], self.cid)
            candidate_id = current['fields']['keywords']['fieldId']
            old = legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA')
            lease = jobs.claim(db, old['id'], self.cid)
            self.assertEqual(jobs.execute_claimed(db, old['id'], self.cid, lease)['status'], 'failed')
            self.assertEqual(metadata.read_draft(db, entry['entryId'], self.cid)['fields']['keywords']['fieldId'], candidate_id)

    def test_old_mock_rejected_with_known_usage_and_no_field_overwrite(self):
        entry = self.entry()
        self.run_keyword(entry)
        with app_module.connect() as db:
            current = metadata.read_draft(db, entry['entryId'], self.cid)
            metadata.review_field(db, entry['entryId'], self.cid, 'keywords', current['fields']['keywords']['token'], 1)
            protected_id = current['fields']['keywords']['fieldId']
            old = legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA', mock_model=True)
        adapter = legacy_jobs.MockMetadataAdapter({'keywords': [{'text': 'API', 'language': 'und',
            'kind': 'literal', 'weight': 1, 'field': 'raw', 'evidence': []}]}, input_tokens=7, output_tokens=4)
        result = legacy_jobs.dispatch_mock(app_module.connect, old['id'], self.cid, adapter)
        self.assertEqual(result['status'], 'rejected')
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db, entry['entryId'], self.cid)['fields']['keywords']['fieldId'], protected_id)
            chunk = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?', (old['id'],)).fetchone()
            self.assertEqual(chunk['state'], 'rejected')
            self.assertEqual((chunk['used_input_tokens'], chunk['used_output_tokens']), (7, 4))

    def test_unknown_new_scope_blocks_legacy_enqueue_and_mock_dispatch(self):
        entry = self.entry()
        with app_module.connect() as db:
            old = legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA', mock_model=True)
            jid = processing.create_keyword_job(db, entry['entryId'], self.cid, 1)
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?", (jid,))
            with self.assertRaisesRegex(ValueError, 'semantic_scope_unknown'):
                legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA')
        adapter = legacy_jobs.MockMetadataAdapter({'keywords': []})
        with patch.object(adapter, 'generate', wraps=adapter.generate) as generate:
            with self.assertRaisesRegex(ValueError, 'semantic_scope_unknown'):
                legacy_jobs.dispatch_mock(app_module.connect, old['id'], self.cid, adapter)
            generate.assert_not_called()
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?', (old['id'],)).fetchone()[0], 0)

    def test_no_lexical_schema_or_formal_context_activation(self):
        entry = self.entry()
        self.run_keyword(entry)
        with app_module.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_metadata_lexical_terms_v1'").fetchone())
            self.assertFalse(metadata.read_draft(db, entry['entryId'], self.cid)['qualifications']['formalContext'])

    def test_legacy_metadata_cannot_overwrite_reviewed_keyword(self):
        """Direct coexistence counterexample: old task must respect review protection."""
        entry = self.entry()
        self.run_keyword(entry)
        with app_module.connect() as db:
            current = metadata.read_draft(db, entry['entryId'], self.cid)
            metadata.review_field(db, entry['entryId'], self.cid, 'keywords', current['fields']['keywords']['token'], 1)
            reviewed_id = current['fields']['keywords']['fieldId']
            old = legacy_jobs.enqueue(db, entry['entryId'], self.cid, 1, 'METADATA')
            lease = jobs.claim(db, old['id'], self.cid)
            result = jobs.execute_claimed(db, old['id'], self.cid, lease)
            self.assertEqual(result['status'], 'failed')
            after = metadata.read_draft(db, entry['entryId'], self.cid)
            self.assertEqual(after['fields']['keywords']['fieldId'], reviewed_id)


if __name__ == '__main__':
    unittest.main()
