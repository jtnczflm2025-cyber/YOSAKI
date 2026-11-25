import json
import math
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_chronicle_embedding as embedding
from backend.app import memory_v2_chronicle_schema as schema
from backend.app import memory_v2_chronicle_semantic as semantic
from backend.app import memory_v2_chronicle_vectors as vectors
from backend.app import memory_v2_chronicle_workspace as workspace
from backend.app import memory_v2_responses_adapter as responses


class SemanticSupplement(EntryBase):
    query = '两道关都过了，是直接动工还是只把施工这事拿出来商量？'
    end = '2026-11-17T00:00:00+08:00'
    documents = (
        ('银叶馆蓝灯指示牌', '馆方批准且夜间测试通过后才讨论是否安装，目前暂缓。', .31),
        ('松影馆指示牌', '许可与夜间测试均通过后可直接安装绿灯指示牌。', .49),
        ('银叶馆展台施工', '馆方已批准白色展台施工，夜间测试另有安排。', .47),
        ('银叶馆夜场开放', '馆方批准前暂缓公布开放时间。', .25),
        ('银叶馆夜班补贴', '夜间测试合格后仍需主管审核补贴申请。', .22),
        ('银叶馆展厅票价', '银叶馆展厅周末成人票六十元，学生凭证购半价票。', .19),
    )
    path = '/api/memory-v2/workspace/semantic-supplement'

    def setUp(self):
        super().setUp()
        self.calls = []
        self.auth = {'id': 1}
        self.mode = 'match'
        self.request = {}
        with app_module.connect() as db:
            schema.install(db)
            sources = [self.refs()]
            for i in range(1, len(self.documents)):
                message = db.execute("""INSERT INTO messages(conversation_id,role,content,created_at)
                    VALUES(?,'user',?,'2026-01-01')""", (self.conv, f'合成原文 {i}')).lastrowid
                sources.append([{'kind': 'message', 'id': str(message)}])
            self._index(db)
            for i, (title, synopsis, score) in enumerate(self.documents):
                item, version, _ = self.new_entry(db, refs=sources[i], title=title, synopsis=synopsis)
                self.approve_entry(db, version)
                eid = item['id']
                if not i:
                    self.target = eid
                _, fingerprint = vectors._input(db, self.cid, eid)
                vectors.store(db, self.cid, 1, eid, embedding.MODEL, fingerprint,
                              [score, math.sqrt(1-score**2)]+[0.0]*1534)
        api = FastAPI()
        workspace.install(api, app_module.connect, lambda _: self.auth,
                          semantic_factories=(self.provider, self.embedding_provider))
        self.client = TestClient(api)

    def price(self, kind, name, rate_in, rate_out):
        return {'provider': kind, 'model': name, 'mode': 'standard', 'checkedAt': '2026-11-17',
                'sourceUrl': 'https://developers.openai.com/api/docs/pricing',
                'inputUsdPerMillion': rate_in, 'outputUsdPerMillion': rate_out}

    def provider(self, _):
        return (responses.ResponsesAdapter(model=semantic.MODEL, transport=httpx.MockTransport(self.wire)),
                self.price('openai_responses', semantic.MODEL, '2.00', '10.00'))

    def embedding_provider(self, _):
        return (embedding.Adapter('synthetic', transport=httpx.MockTransport(self.wire)),
                self.price('openai_embeddings', embedding.MODEL, '0.02', 0))

    def wire(self, request):
        path = request.url.path
        self.calls.append(path)
        body = json.loads(request.content)
        if path == '/v1/embeddings':
            self.assertEqual(body['input'], [self.active_query])
            return httpx.Response(200, json={'model': embedding.MODEL, 'usage': {'prompt_tokens': 10, 'total_tokens': 10},
                'data': [{'index': 0, 'embedding': [1.0]+[0.0]*1535}]})
        if path.endswith('/input_tokens'):
            self.request = body
            if self.mode == 'stale_after_count':
                with app_module.connect() as db:
                    db.execute('UPDATE messages SET content=? WHERE id=?', ('已替换内容', self.user))
            return httpx.Response(200, json={'input_tokens': semantic.INPUT_CAP+1 if self.mode=='over_budget' else 200})
        self.assertEqual(self.request, responses.count_payload(body))
        candidates = json.loads(body['input'][0]['content'])['candidates']
        self.assertEqual([candidate['text'] for candidate in candidates],
                         ['\n'.join(self.documents[i][:2]) for i in (1, 2, 0)])
        if self.mode == 'timeout':
            raise httpx.ReadTimeout('simulated timeout')
        if self.mode == 'stale':
            with app_module.connect() as db:
                db.execute('UPDATE messages SET content=? WHERE id=?', ('已替换内容', self.user))
        decisions = [{'id': c['id'], 'relevant': self.mode != 'empty' and c['id']=='3',
                      'evidence': ('无来源内容' if self.mode=='bad_quote' else self.documents[0][1])
                                  if self.mode!='empty' and c['id']=='3' else ''} for c in candidates]
        return httpx.Response(200, json={'id': 'resp_test', 'model': semantic.MODEL, 'status': 'completed',
            'service_tier': 'default', 'usage': None if self.mode=='missing_usage' else {'input_tokens': 200, 'output_tokens': 50},
            'output': [{'type': 'message', 'content': [{'type': 'output_text',
                'text': json.dumps({'judgments': decisions})}]}]})

    def payload(self, **changes):
        return dict({'characterId': self.cid, 'modelId': 1, 'asOf': self.end, 'requestId': str(uuid4()),
                     'query': self.query, 'approveMaxMicrousd': semantic.BUDGET}, **changes)

    def post(self, **changes):
        payload = self.payload(**changes)
        self.active_query = payload['query']
        return self.client.post(self.path, json=payload)

    def ledger(self):
        with app_module.connect() as db:
            return [tuple(row) for row in db.execute("""SELECT c.state,b.reserved_microusd,b.spent_estimate_microusd
               FROM mv3_trial_budgets b JOIN mv3_model_chunks c ON c.trial_id=b.id ORDER BY b.rowid""")]

    def test_explicit_search_filters_three_candidates_and_returns_only_validated_snippet(self):
        normal = self.client.get('/api/memory-v2/calendar/entries',
            params={'characterId': self.cid, 'q': self.query, 'asOf': self.end})
        self.assertEqual(normal.json()['items'], [])
        self.assertEqual(self.calls, [])
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['items'], [{'entryId': self.target, 'title': self.documents[0][0],
                                                    'synopsis': self.documents[0][1]}])
        self.assertEqual(self.calls, ['/v1/embeddings', '/v1/responses/input_tokens', '/v1/responses'])
        self.assertEqual(self.ledger(), [('completed', 0, 1), ('completed', 0, 1500)])

    def test_explicit_empty_result_and_duplicate_request_never_resend(self):
        self.mode = 'empty'
        attempt = str(uuid4())
        self.assertEqual(self.post(requestId=attempt).json()['items'], [])
        self.assertEqual(self.post(requestId=attempt, query='另一个问题？').json()['detail'],
                         'semantic_request_already_used')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.post().status_code, 200)  # new consent, cached query vector
        self.assertEqual(self.calls.count('/v1/embeddings'), 1)

    def test_permission_empty_date_filter_and_existing_keyword_hits_do_not_send(self):
        self.assertEqual(self.post(query='蓝灯指示牌').json()['detail'], 'semantic_ordinary_results_available')
        self.assertEqual(self.post(fromDate='2026-11-12').json()['detail'], 'semantic_index_unavailable')
        self.assertEqual(self.post(approveMaxMicrousd=1).json()['detail'], 'semantic_approval_required')
        self.auth = None
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ledger(), [])

    def test_known_usage_settles_even_when_evidence_invalid(self):
        self.mode = 'bad_quote'
        response = self.post()
        self.assertEqual(response.json()['detail'], 'semantic_judgment_failed')
        self.assertEqual(self.ledger(), [('completed', 0, 1), ('rejected', 0, 1500)])

    def test_stale_ranked_sources_reject_result_but_settle_usage(self):
        self.mode = 'stale'
        self.assertEqual(self.post().json()['detail'], 'semantic_judgment_failed')
        self.assertEqual(self.ledger(), [('completed', 0, 1), ('rejected', 0, 1500)])

    def test_timeout_keeps_unknown_reservation_and_next_click_cannot_bypass(self):
        self.mode = 'timeout'
        self.assertEqual(self.post().json()['detail'], 'semantic_unknown')
        self.assertEqual(self.ledger(), [('completed', 0, 1), ('unknown', 13488, 0)])
        self.assertEqual(self.post().json()['detail'], 'semantic_scope_unknown')
        self.assertEqual(len(self.calls), 3)

    def test_missing_usage_keeps_unknown_reservation(self):
        self.mode = 'missing_usage'
        self.assertEqual(self.post().json()['detail'], 'semantic_unknown')
        self.assertEqual(self.ledger(), [('completed', 0, 1), ('unknown', 13488, 0)])

    def test_sources_changed_after_count_do_not_dispatch_generation(self):
        self.mode = 'stale_after_count'
        self.assertEqual(self.post().json()['detail'], 'semantic_snapshot_changed')
        self.assertEqual(self.calls, ['/v1/embeddings', '/v1/responses/input_tokens'])
        self.assertEqual(self.ledger(), [('completed', 0, 1)])

    def test_budget_preflight_does_not_dispatch_generation(self):
        self.mode = 'over_budget'
        self.assertEqual(self.post().json()['detail'], 'semantic_budget_exceeded')
        self.assertEqual(self.calls, ['/v1/embeddings', '/v1/responses/input_tokens'])
        self.assertEqual(self.ledger(), [('completed', 0, 1)])
