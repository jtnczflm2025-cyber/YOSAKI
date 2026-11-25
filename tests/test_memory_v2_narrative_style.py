"""Synthetic, offline checks for high-fidelity dialogue chronicle presentation."""
import json
import unittest

from test_memory_v2_derivation import M3Base, app_module, derived
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_model_adapter as model_adapter
from backend.app import memory_v2_responses_adapter as responses
from backend.app.memory_v2_fidelity import validate_chunk


class NarrativeStyle(M3Base):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            jobs.init_db(db)

    def test_prompt_and_schema_separate_provenance_from_dialogue(self):
        payload = responses.request_payload('gpt-6.1-sol', 'NARRATIVE', responses.M35_VARIANT,
            [{'sourceKind': 'message', 'sourceId': '1', 'speaker': 'user', 'time': None,
              'content': '如果下雨，我就留在家里。'}], 256)
        instruction = payload['instructions']
        self.assertIn('高保真对话纪事', instruction)
        self.assertIn('以“她说：”归属', instruction)
        self.assertIn('不写“用户说”“助手说”', instruction)
        self.assertIn('详细正文不追求压缩', instruction)
        self.assertIn('“我说：”', instruction)
        self.assertIn('“她说：”', instruction)
        self.assertNotIn('我告诉她', instruction)
        self.assertEqual(payload['text']['format']['schema']['properties']['items']['items']['required'],
                         ['sourceKind', 'sourceId', 'speaker', 'text'])
        self.assertFalse(payload['store'])
        self.assertNotIn('tools', payload)

    def test_narrative_body_has_turns_without_machine_headers_and_mapping_stays_ordered(self):
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?',
                       ('如果下雨，我就留在家里。', self.user))
            db.execute('UPDATE messages SET content=? WHERE id=?',
                       ('俺说：わかった。\n下雨なら家で話そう。', self.assistant))
            self._index(db)
            job = jobs.enqueue_model(db, self.cid, 'NARRATIVE', self.refs(),
                                     input_cap=4000, output_cap=2000, total_token_cap=6000)
        expected = ['她说如果下雨，她就留在家里。', '我说：わかった。\n\n下雨なら家で話そう。']
        def model(request):
            self.assertEqual(request['promptVersion'], model_adapter.PROMPT_VERSION)
            return {'finishReason': 'stop', 'output': {'items': [
                {'sourceKind': m['sourceKind'], 'sourceId': m['sourceId'],
                 'speaker': m['speaker'], 'text': expected[index]}
                for index, m in enumerate(request['messages'])]}}
        class Bytes:
            def encode(self, text):
                return text.encode('utf-8')
        result = jobs.dispatch_model_chunk(app_module.connect, job['id'], self.cid,
            Bytes(), model_adapter.LocalMockAdapter(model))
        self.assertEqual(result['status'], 'completed')
        with app_module.connect() as db:
            version = derived._row(db, 'mv3_versions', result['versionId'])
            self.assertEqual(version['body'], '\n\n'.join(expected))
            self.assertNotIn('[message:', version['body'])
            self.assertEqual((version['status'], version['review_status'], version['validation_status']),
                             ('needs_review', 'unreviewed', 'pending'))
            mapping = json.loads(version['mapping_json'])
            self.assertEqual([m['sourceId'] for m in mapping], [str(self.user), str(self.assistant)])
            self.assertEqual([version['body'][m['outputStart']:m['outputEnd']] for m in mapping], expected)
            self.assertTrue(all(m['rawStart'] is None for m in mapping))

    def test_old_prompt_job_is_not_silently_reused(self):
        with app_module.connect() as db:
            old = jobs.enqueue(db, self.cid, 'NARRATIVE', self.refs(), model_mode=True,
                prompt_version='ssly-faithful-m35-v1', input_cap=2000, output_cap=1000,
                total_token_cap=3000, max_cost_units=3000, target_chars=6000)
            new = jobs.enqueue_model(db, self.cid, 'NARRATIVE', self.refs(),
                input_cap=2000, output_cap=1000, total_token_cap=3000)
            self.assertNotEqual(old['id'], new['id'])
            with self.assertRaisesRegex(ValueError, 'model_prompt_version_changed'):
                jobs.prepare_model(db, old['id'], self.cid, object())

    def test_transcript_rejected_and_duplicate_prefix_flagged_for_review(self):
        inputs = [
            {'sourceKind': 'message', 'sourceId': '1', 'speaker': 'user',
             'sourceStatus': 'completed', 'content': '我在家。'},
            {'sourceKind': 'message', 'sourceId': '2', 'speaker': 'assistant',
             'sourceStatus': 'completed', 'content': '俺说：わかった。'},
        ]
        response = {'finishReason': 'stop', 'output': {'items': [
            {'sourceKind': 'message', 'sourceId': '1', 'speaker': 'user', 'text': '用户说她在家。'},
            {'sourceKind': 'message', 'sourceId': '2', 'speaker': 'assistant',
             'text': '我说：我说：わかった。'},
        ]}}
        with self.assertRaisesRegex(ValueError, 'external_transcript_style'):
            validate_chunk(inputs, response, kind='NARRATIVE')
        response['output']['items'][0]['text'] = '她说：她在家。'
        _, warnings = validate_chunk(inputs, response, kind='NARRATIVE')
        self.assertIn('duplicate_first_person_prefix', warnings)

    def test_mixed_language_attribution_is_rejected(self):
        original = {'sourceKind': 'message', 'sourceId': '2', 'speaker': 'assistant',
                    'sourceStatus': 'completed', 'content': '俺说：わかった。'}
        output = {'finishReason': 'stop', 'output': {'items': [
            {'sourceKind': 'message', 'sourceId': '2', 'speaker': 'assistant',
             'text': '我说：俺说：わかった。'}]}}
        with self.assertRaisesRegex(ValueError, 'mixed_language_attribution'):
            validate_chunk([original], output, kind='NARRATIVE')
        output['output']['items'][0]['text'] = '我告诉她：わかった。'
        with self.assertRaisesRegex(ValueError, 'unsupported_attribution_prefix'):
            validate_chunk([original], output, kind='NARRATIVE')

    def test_local_quote_normalizes_leading_mixed_marker_with_exact_mapping(self):
        from backend.app import memory_v2_narrative as narrative
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?', ('俺说：わかった。', self.assistant))
            self._index(db)
            version = narrative.create_narrative(db, self.cid, self.refs())
            self.assertIn('我说：「わかった。」', version['body'])
            self.assertNotIn('俺说', version['body'])
            mapped = json.loads(version['mapping_json'])[-1]
            self.assertEqual(mapped['rawStart'], len('俺说：'))
            self.assertEqual(version['body'][mapped['outputStart']:mapped['outputEnd']], 'わかった。')

    def test_japanese_and_meaningful_parentheses_guards(self):
        original = {'sourceKind': 'message', 'sourceId': '2', 'speaker': 'assistant',
                    'sourceStatus': 'completed', 'content': '俺说：わかった。（地点是公园）'}
        def response(text):
            return {'finishReason': 'stop', 'output': {'items': [
                {'sourceKind': 'message', 'sourceId': '2', 'speaker': 'assistant', 'text': text}]}}
        with self.assertRaisesRegex(ValueError, 'japanese_expression_dropped'):
            validate_chunk([original], response('我说：地点是公园。'), kind='NARRATIVE')
        _, warnings = validate_chunk([original],
            response('我说：わかった。'), kind='NARRATIVE')
        self.assertIn('parenthetical_content_changed_needs_review', warnings)
        _, warnings = validate_chunk([original],
            response('我说：わかった。（地点是公园）'), kind='NARRATIVE')
        self.assertNotIn('parenthetical_content_changed_needs_review', warnings)


if __name__ == '__main__':
    unittest.main()
