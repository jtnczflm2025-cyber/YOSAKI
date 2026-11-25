"""Portable A3.4 profile and evidence checks using synthetic sources only."""

import json
import unittest

import test_memory_v2_h32 as h32
from test_memory_v2_h32 import app_module, importer

from backend.app import memory_v2_m36_trial as trials
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs
from backend.app import memory_v2_processing_schema
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_synopsis as synopsis

from test_memory_v2_a34_responses_wiring import FakeWire, PRICE


class VersionedRequests(unittest.TestCase):
    def test_old_default_and_new_profiles_keep_separate_output_caps(self):
        actual = {'phase': 'batch', 'sources': [], 'ordinal': 0, 'inputVersion': 'synthetic'}
        old = responses.synopsis_request_payload('gpt-6.1-sol', 'batch', actual, 2048)
        self.assertEqual(old['max_output_tokens'], 2048)
        with self.assertRaisesRegex(ValueError, 'invalid_synopsis_request'):
            responses.synopsis_request_payload('gpt-6.1-sol', 'batch', actual, 6144)
        e1 = responses.synopsis_request_payload('gpt-6.1-sol', 'batch', actual, 6144,
                                                profile=synopsis.REAL_E1_PROFILE)
        e2 = responses.synopsis_request_payload('gpt-6.1-sol', 'batch', actual, 6144,
                                                profile=synopsis.REAL_E2_CAPACITY_PROFILE)
        self.assertEqual(e1['text']['format']['schema'], old['text']['format']['schema'])
        self.assertEqual(e2['text']['format']['schema'], old['text']['format']['schema'])
        self.assertEqual(responses.count_payload(e1)['model'], e1['model'])
        with self.assertRaisesRegex(ValueError, 'invalid_synopsis_request'):
            responses.synopsis_request_payload('gpt-6.1-sol', 'final',
                {'phase': 'final', 'parts': [], 'sourceManifest': []}, 6144,
                profile=synopsis.REAL_E1_PROFILE)

    def test_v6_prompt_is_final_only_and_does_not_rewrite_old_request(self):
        actual = {'phase': 'final', 'parts': [], 'sourceManifest': [],
                  'inputVersion': 'synthetic'}
        old = responses.synopsis_request_payload('gpt-6.1-sol', 'final', actual, 6144,
                                                profile=synopsis.REAL_E2_CAPACITY_PROFILE)
        new = responses.synopsis_request_payload('gpt-6.1-sol', 'final', actual, 6144,
            profile=responses.SYNOPSIS_E2_INTEGRATED_FINAL_PROMPT_VERSION)
        self.assertEqual(old['input'], new['input'])
        self.assertEqual(old['text'], new['text'])
        self.assertNotEqual(old['instructions'], new['instructions'])
        self.assertIn('合并重复信息', new['instructions'])
        self.assertIn('重算 start/end', new['instructions'])
        self.assertNotIn('合并重复信息', old['instructions'])
        self.assertEqual(responses.count_payload(new)['model'], new['model'])
        with self.assertRaisesRegex(ValueError, 'invalid_synopsis_request'):
            responses.synopsis_request_payload('gpt-6.1-sol', 'batch',
                {'phase': 'batch', 'sources': []}, 6144,
                profile=responses.SYNOPSIS_E2_INTEGRATED_FINAL_PROMPT_VERSION)


class IntegratedEvidence(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {'kind': 'message', 'id': 1, 'speaker': 'user',
             'text': '我先写蓝色草案。', 'sourceStatus': 'imported'},
            {'kind': 'message', 'id': 2, 'speaker': 'user',
             'text': '更正：草案改为白色。', 'sourceStatus': 'imported'},
        ]
        self.refs = [
            {'kind': 'message', 'id': s['id'], 'speaker': s['speaker'],
             'start': 0, 'end': len(s['text']), 'quote': s['text']}
            for s in self.sources
        ]
        self.parts = [
            {'text': '用户先写蓝色草案。',
             'units': [{'start': 0, 'end': len('用户先写蓝色草案。'),
                        'evidence': [self.refs[0]]}]},
            {'text': '用户更正为白色草案。',
             'units': [{'start': 0, 'end': len('用户更正为白色草案。'),
                        'evidence': [self.refs[1]]}]},
        ]

    def _output(self, text, refs=None):
        return {'text': text,
                'units': [{'start': 0, 'end': len(text),
                           'evidence': refs or self.refs}],
                'sourceCoverage': [{'kind': 'message', 'id': 1, 'used': True},
                                   {'kind': 'message', 'id': 2, 'used': True}]}

    def test_cross_part_rewrite_can_cite_both_original_sources(self):
        text = '用户先写蓝色草案，后来更正为白色草案。'
        self.assertEqual(synopsis.validate_integrated_final_output(
            self._output(text), self.sources, self.parts)['text'], text)

    def test_final_rewrite_rejects_wrong_actor_and_new_evidence(self):
        with self.assertRaisesRegex(ValueError, 'synopsis_final_attribution_conflict'):
            synopsis.validate_integrated_final_output(
                self._output('助手：先写蓝色草案，后来更正为白色草案。'),
                self.sources, self.parts)
        altered = json.loads(json.dumps(self.parts, ensure_ascii=False))
        altered[0]['units'][0]['evidence'] = [self.refs[0]]
        new_ref = dict(self.refs[0], start=1, end=len(self.sources[0]['text']),
                       quote=self.sources[0]['text'][1:])
        with self.assertRaisesRegex(ValueError, 'synopsis_final_evidence_not_in_parts'):
            synopsis.validate_integrated_final_output(
                self._output('用户先写蓝色草案，后来更正为白色草案。',
                             [new_ref, self.refs[1]]), self.sources, altered)

    def test_single_unit_cannot_hide_two_sentences(self):
        with self.assertRaisesRegex(ValueError, 'synopsis_sentence_unit_required'):
            synopsis.validate_integrated_final_output(
                self._output('用户先写蓝色草案。后来更正为白色草案。'),
                self.sources, self.parts)


class PersistedProfileBinding(unittest.TestCase):
    def setUp(self):
        self.fixture = h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.character = self.fixture.cid
        self.owner = self.fixture.owner
        with app_module.connect() as db:
            result = importer.confirm_h32_drafts(
                db, self.fixture.batch, self.character, self.fixture.file['id'],
                self.fixture.segments(), **self.fixture.arguments(db))
            self.entries = [item['entryId'] for item in result['items']]
            memory_v2_metadata_jobs.init_db(db)
            trials.init_db(db)
        with app_module.connect() as db:
            memory_v2_processing_schema.migrate(db)

    def test_e2_capacity_readset_and_preflight_are_version_bound(self):
        with app_module.connect() as db:
            planned = synopsis.create_e2_capacity_trial(
                db, self.entries[1], self.character, self.owner,
                price=PRICE, batch_input_cap=1400)
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                            (planned['jobId'],)).fetchone()[0])
        self.assertEqual(payload['ruleVersion'], synopsis.REAL_E2_CAPACITY_PROFILE)
        self.assertEqual(payload['readSet']['profile'], synopsis.REAL_E2_CAPACITY_PROFILE)
        config = payload['readSet']['realConfig']
        self.assertEqual(config['requestProfile'], synopsis.REAL_E2_CAPACITY_PROFILE)
        self.assertEqual(config['partCharsCap'], 2000)
        self.assertEqual(config['finalInputCap'], 4600)
        self.assertEqual(config['outputTokenCap'], 6144)
        wire = FakeWire()
        prepared = synopsis.prepare_real_trial(
            app_module.connect, planned['jobId'], self.character, self.owner, wire.adapter)
        self.assertEqual((prepared['generationRequests'], prepared['countRequests']), (3, 2))
        self.assertEqual(len(wire.generations), 0)

    def test_e1_success_remains_unreviewed_and_readable_under_its_version(self):
        with app_module.connect() as db:
            planned = synopsis.create_e1_trial(
                db, self.entries[0], self.character, self.owner, price=PRICE)
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                            (planned['jobId'],)).fetchone()[0])
        self.assertEqual(payload['ruleVersion'], synopsis.REAL_E1_PROFILE)
        self.assertEqual(payload['readSet']['realConfig']['requestProfile'],
                         synopsis.REAL_E1_PROFILE)
        wire = FakeWire()
        synopsis.prepare_real_trial(app_module.connect, planned['jobId'],
                                    self.character, self.owner, wire.adapter)
        done = synopsis.run_real_trial(app_module.connect, planned['jobId'],
                                       self.character, self.owner, wire.adapter)
        self.assertEqual(done['status'], 'completed', done)
        self.assertEqual((len(wire.counts), len(wire.generations)), (1, 1))
        with app_module.connect() as db:
            field = metadata.read_draft(db, self.entries[0], self.character)['fields']['synopsis']
            self.assertEqual(field['reviewStatus'], 'needs_review')
            self.assertEqual(field['lifecycleStatus'], 'draft')
            self.assertEqual(db.execute('SELECT rule_version FROM mv3_entry_fields WHERE id=?',
                                        (field['fieldId'],)).fetchone()[0],
                             synopsis.REAL_E1_PROFILE)


if __name__ == '__main__':
    unittest.main()
