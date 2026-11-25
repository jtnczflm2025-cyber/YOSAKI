"""Actual product fixture with raw synthetic SSE, never real provider access."""
from copy import deepcopy
from pathlib import Path

from backend.app.voice.performance_preferences import CANDIDATE
from scripts.voice_ai_chat_candidate_fixture import Fixture

BODY = ("今日は、少し疲れたな。急がなくていい。ここで一緒に休もう。\n\n"
        "……いや、眠ったわけじゃない。少し目を閉じていただけだ。")
CHAT_INPUT = "次の文章だけを、そのまま発話してください。説明や前置きは不要です。段落と句読点を保ってください。\n\n" + BODY


def choose_for_simulation(content):
    """Deliberate synthetic recommendations, never a real AI quality claim."""
    sentences = [f for f in content['fragments'] if f['kind'] == 'sentence']
    return [{'fragment_id': sentences[0]['fragment_id'], 'intent': 'resigned'},
            {'fragment_id': sentences[2]['fragment_id'], 'intent': 'thoughtful'}]


class MultiFixture(Fixture):
    def __init__(self, root: Path):
        super().__init__(root, public_reply=BODY, annotation_selector=choose_for_simulation)
        while self.model_id < 5:
            self.model_id = self.client.post('/api/models',json={'name':'明确规划档案5',
                'provider_name':'synthetic','protocol':'chatgpt_plan',
                'base_url':'https://synthetic.invalid/v1','model_name':'gpt-6.1-sol',
                'max_tokens':2048,'enabled':True}).json()['id']
        self.client.patch('/api/characters/1',json={'default_model_id':5})
        self.runtime = self.main.app.state.voice_ai_http
        self.runtime.plans.performance_preferences = deepcopy(CANDIDATE)

    def enable(self):
        response = self.client.put('/api/voice/ai-http/settings',json={'enabled':True,
            'model_profile_id':5,'expected_revision':0,'confirm_planning':True})
        if response.status_code != 200:
            raise RuntimeError('synthetic_enable_failed')
