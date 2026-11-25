import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from backend.app import memory_v2_import_processing as workflow


class SubscriptionImportTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            'id': '41', 'kind': 'message', 'speaker': 'user',
            'text': '图书馆借来的图册还在柜台。', 'sourceStatus': 'valid',
        }

    def test_citation_binds_to_original_character_offsets(self):
        sentence = {'sentence': 'イキヤ提到借来的图册。', 'citations': [{'id': '41', 'quote': '借来的图册'}]}
        text, record = workflow.checked([sentence], [self.source])
        self.assertEqual(text, sentence['sentence'])
        ref = record['units'][0]['evidence'][0]
        self.assertEqual(self.source['text'][ref['start']:ref['end']], '借来的图册')
        self.assertEqual(ref['speaker'], 'user')

    def test_wrong_source_and_ambiguous_quotes_are_rejected(self):
        self.source['text'] = '图册放在图册旁边。'
        for key, reason in [('42', 'plan_citation_invalid'), ('41', 'synopsis_quote_ambiguous')]:
            sentence = {'sentence': '她提到图册。', 'citations': [{'id': key, 'quote': '图册'}]}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, reason):
                workflow.checked([sentence], [self.source])

    def test_explicit_speaker_inversion_is_rejected(self):
        sentence = {'sentence': '阿夜说图册还在柜台。', 'citations': [{'id': '41', 'quote': self.source['text']}]}
        with self.assertRaisesRegex(ValueError, 'synopsis_explicit_speaker_conflict'):
            workflow.checked([sentence], [self.source])

    def test_missing_terminal_does_not_send_again(self):
        requests = []

        def fixture(request):
            requests.append(request)
            return httpx.Response(200, text='data: {"type":"response.created"}\n\ndata: [DONE]\n\n')

        with self.assertRaisesRegex(ValueError, 'plan_terminal_missing'):
            asyncio.run(workflow.terminal({'store': False}, 'fixture', httpx.MockTransport(fixture)))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, 'api.openai.com')
        self.assertFalse(json.loads(requests[0].content)['store'])

    def test_import_endpoint_fills_review_draft_and_replay_sends_nothing(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from test_memory_v2_closeout import Closeout, app_module, workspace
        from backend.app import memory_v2_import_schema, memory_v2_processing_schema, memory_v2_jobs, memory_v2_vocabulary
        from backend.app import memory_v2_metadata_jobs, memory_v2_tag_schema

        case = Closeout('test_general_import_receipts_confirmation_not_h32_specimen')
        case.setUp()
        self.addCleanup(case.doCleanups)
        with app_module.connect() as db:
            memory_v2_import_schema.init_db(db)
            memory_v2_jobs.init_db(db)
            memory_v2_metadata_jobs.init_db(db)
            memory_v2_vocabulary.init_db(db)
            db.commit()
            memory_v2_processing_schema.migrate(db)
            memory_v2_tag_schema.migrate(db)
            db.execute("""INSERT INTO model_profiles(name,base_url,model_name,protocol,created_at,updated_at)
                VALUES('fixture','https://api.openai.com/v1',?,'chatgpt_plan','now','now')""", (workflow.MODEL,))
        app = FastAPI()
        workspace.install(app, app_module.connect, lambda request: {'id': 1})
        client = TestClient(app)
        messages = [
            {'role': 'user', 'content': '我在图书馆归还图册。', 'created_at': '2026-09-06 15:30'},
            {'role': 'assistant', 'content': '我记下归还日期。', 'created_at': '2026-09-06 15:31'},
        ]
        archive = json.dumps({'format': 'yosaki-conversation', 'version': 2, 'messages': messages}).encode()
        requests = []

        def service(request):
            requests.append(request)
            wire = json.loads(request.content)
            material = json.loads(wire['input'][0]['content'])
            self.assertEqual([m['text'] for m in material], [m['content'] for m in messages])
            self.assertEqual(set(material[0]), {'speaker', 'id', 'text'})
            sentence = {'sentence': 'イキヤ说她在图书馆归还图册。',
                        'citations': [{'id': material[0]['id'], 'quote': '在图书馆归还图册'}]}
            result = {'status': 'completed', 'id': 'fixture', 'usage': {'input_tokens': 82, 'output_tokens': 45},
                      'output': [{'type': 'message', 'content': [{'type': 'output_text',
                                  'text': json.dumps({'sentences': [sentence]})}]}]}
            return httpx.Response(200, text='data: '+json.dumps({'type': 'response.completed', 'response': result})+'\n\n')

        real_terminal = workflow.terminal
        async def local_terminal(body, token, transport=None):
            return await real_terminal(body, token, httpx.MockTransport(service))

        with patch.object(workflow, 'terminal', local_terminal), patch.object(workflow.chatgpt_plan, 'access_token', return_value='fixture'):
            res = client.post('/api/memory-v2/imports/batches', data={'characterId': case.cid, 'withPlanSynopsis': 'true'},
                              files={'files': ('library.json', archive, 'application/json')})
            self.assertEqual(res.status_code, 200)
            url = '/api/memory-v2/imports/batches/' + res.json()['batchId']
            data = client.get(url, params={'characterId': case.cid}).json()
            self.assertEqual(data['processing']['status'], 'completed', data['processing'])
            self.assertEqual(data['processing']['synopses'], 1)
            with app_module.connect() as db:
                card = workspace.review_card(db, case.cid, data['segments'][0]['entry_id'])
                self.assertEqual(card['synopsis'], 'イキヤ说她在图书馆归还图册。')
                self.assertTrue(card['narrativeReady'])
                self.assertFalse(card['published'])
            self.assertEqual(client.post(url+'/prepare-review', params={'characterId':case.cid}).status_code, 200)
            self.assertEqual(len(requests), 1)
