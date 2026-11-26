"""Step 4 synthetic saved replies, Responses adapter and HTTP TTS; loopback only.

Explicit --enable required; no main import, real keys or daily database access.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

import httpx

from backend.app.voice import auto_budget, storage
from backend.app.voice.annotation_http_product import HTTPReplyBridge, SavedReplyRepository, create_app
from backend.app.voice.annotation_model import ProfilePlanningAdapter
from backend.app.voice.realtime_answer_display import AnswerDisplayGate
from backend.app.voice.schemas import ProfileInput
from scripts.voice_segmented_lab import bootstrap, MP3

BODY = '今夜は静かだ。少し考えてから話そう。\n\n急がなくていい。ここで休もう。'


def events(text, identity='synthetic-answer'):
    item = {'type': 'message', 'id': identity, 'phase': 'final_answer',
            'content': [{'type': 'output_text', 'text': text}]}
    return [{'type': 'response.output_item.added', 'item': item, 'output_index': 0},
        {'type': 'response.output_text.delta', 'item_id': identity, 'content_index': 0, 'delta': text},
        {'type': 'response.completed', 'response': {'status': 'completed', 'output': [item],
            'usage': {'input_tokens': 100, 'output_tokens': 30, 'total_tokens': 130}}}]


class SSE(httpx.AsyncByteStream):
    def __init__(self, values):
        self.data = ''.join('event: ' + v['type'] + '\ndata: ' + json.dumps(v, ensure_ascii=False) + '\n\n'
                            for v in values).encode()
    async def __aiter__(self):
        for i in range(0, len(self.data), 7):
            yield self.data[i:i+7]


class Fixture:
    def __init__(self, root, *, enabled=False):
        self.root = Path(root)
        self.connect = bootstrap(self.root / 'product')
        # All process flags and this placeholder are local to this fake launcher.
        os.environ['ELEVENLABS_API_KEY'] = 'synthetic-only'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY,character_id INTEGER)')
            columns = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
            for name, kind in [('conversation_id', 'INTEGER'), ('role', 'TEXT'), ('content', 'TEXT'),
                               ('generation_status', 'TEXT'), ('tool_calls_json', 'TEXT'), ('tool_batch_status', 'TEXT')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE messages ADD COLUMN {name} {kind}')
            db.execute('INSERT OR IGNORE INTO conversations VALUES(1,1)')
            db.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,generation_status) VALUES(501,1,'assistant',?,'completed')", (BODY,))
        with self.connect() as db:
            if storage.profile(db, 1) is None:
                storage.put_profile(db, 1, ProfileInput(expected_revision=0, voice_id='synthetic-voice', active=True))
        with self.connect() as db:
            db.execute('UPDATE voice_profiles SET revision=3')
            storage.put_consent(db, 1)
        gate = AnswerDisplayGate()
        for e in events(BODY):
            gate.observe(e)
        assert gate.finish(model_completed=True, public_text=BODY).action == 'allow'
        self.proof = dict(owner_id=1, character_id=1, conversation_id=1, message_id=501,
            generation_id='synthetic-generation', body_sha256=hashlib.sha256(BODY.encode()).hexdigest(),
            model_completed=True, stream_ended=True, display_action='allow')
        self.model_requests, self.tts_requests = [], []
        self.scenario = 'normal'
        self.model_hold = None
        self.tts_hold = None
        self.model_entered = asyncio.Event()
        self.tts_entered = asyncio.Event()
        self.profile = dict(id=9, enabled=1, protocol='openai_responses', model_name='gpt-6.1-sol',
            base_url='https://synthetic.invalid/v1', max_tokens=2048, reasoning_effort=None)
        model = ProfilePlanningAdapter(9, lambda _: self.profile, self.token,
            enabled=True, transport=httpx.MockTransport(self.model))
        repository = SavedReplyRepository(self.root / 'product/yosaki.sqlite3', self.connect,
                                         lambda message, owner: self.proof)
        self.bridge = HTTPReplyBridge(self.root / 'plans', repository, model, httpx.MockTransport(self.tts),
            lambda owner, n: auto_budget.reserve(self.connect, 'synthetic-session', owner, n, first_request=True),
            enabled=enabled)
        self.app = create_app(self.bridge, message_ids=[501], model_profile_id=9)

    async def token(self, profile):
        return 'synthetic-token'

    async def model(self, request):
        body = json.loads(request.content)
        self.model_requests.append(body)
        self.model_entered.set()
        if self.model_hold:
            await self.model_hold.wait()
        if self.scenario == 'model_failed':
            return httpx.Response(429)
        if self.scenario == 'model_unknown':
            raise httpx.ReadTimeout('synthetic')
        data = json.loads(next(m['content'] for m in body['input'] if m['role'] == 'user'))
        sentence = next(f for f in data['fragments'] if f['kind'] == 'sentence')
        annotations = [] if self.scenario == 'empty' else [{'fragment_id': sentence['fragment_id'], 'intent': 'thoughtful'}]
        if self.scenario == 'invalid':
            annotations[0]['fragment_id'] = 'forged'
        result = dict(schema_version=1, input_sha256=data['input_sha256'], annotations=annotations)
        return httpx.Response(200, stream=SSE(events(json.dumps(result, ensure_ascii=False))))

    async def tts(self, request):
        self.tts_requests.append(json.loads(request.content))
        self.tts_entered.set()
        if self.tts_hold:
            try:
                await self.tts_hold.wait()
            except asyncio.CancelledError:
                if self.scenario != 'late_tts':
                    raise
        if self.scenario == 'tts_unknown':
            raise httpx.ReadTimeout('synthetic')
        return httpx.Response(200, content=MP3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    fixture = Fixture(args.data_dir, enabled=args.enable)
    async def serve():
        import uvicorn
        def deny_network(event, arguments):
            if event == 'socket.connect':
                raise PermissionError('synthetic_lab_network_denied')
        sys.addaudithook(deny_network)
        await uvicorn.Server(uvicorn.Config(fixture.app, host='127.0.0.1', port=18896,
                                           workers=1, access_log=False)).serve()
    asyncio.run(serve())


if __name__ == '__main__':
    main()
