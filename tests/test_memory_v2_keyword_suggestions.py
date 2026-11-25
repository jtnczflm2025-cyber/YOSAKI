import unittest
from backend.app.memory_v2_keyword_suggestions import suggest
from test_memory_v2_keyword_workflow import KeywordWorkflow
class Suggestions(unittest.TestCase):
 def test_quality(self):
  text='我有些太紧张了。緊張しています。「今天聊天真的非常开心」。只是、ただ、たまに、したことじゃない。我们讨论数据库恢复和データベース復旧。'
  r=suggest([dict(kind='message',id=1,text=text)])
  words=[x['text'] for x in r['candidates']]
  self.assertIn('紧张',words);self.assertNotIn('緊張',words)
  self.assertIn('数据库恢复',words);self.assertNotIn('データベース復旧',words)
  self.assertNotIn('今天聊天真的非常开心',words)
  for x in r['candidates']:
   e=x['evidence'][0];self.assertEqual(text[e['start']:e['end']],x['text'])
 def test_complete_selection_cross_language(self):
  text='银叶馆的蓝灯指示牌尚未安装。数据库恢复和データベース復旧。夜間テスト与夜间测试说的是同一次测试。'
  words=[x['text'] for x in suggest([dict(kind='message',id=1,text=text)])['candidates']]
  self.assertEqual(set(words),{'银叶馆','蓝灯指示牌','数据库恢复','夜間テスト'})
class EntrySuggestions(KeywordWorkflow):
 def test_read_only_suggestions(self):
  before=self.ready();r=self.client.get(self.url+'/keyword-suggestions',params={'characterId':self.cid})
  self.assertEqual(r.status_code,200,r.text)
  self.assertEqual(self.f.view(),before)
  self.assertEqual(r.json()['versionId'],before['entry']['versionId'])
if __name__=='__main__':unittest.main()
