"""Owner-selected retention stays separate from ordinary time compression."""
import os,unittest
from datetime import datetime,timezone
from unittest.mock import patch
import test_memory_v2_closeout as fixture
from test_memory_v2_entries import app_module
from backend.app import memory_v2_chronicle_curation as c,memory_v2_chronicle_channel as ch,memory_v2_metadata as m
class Retention(unittest.TestCase):
 def setUp(self):
  self.fx=fixture.Closeout('test_explicit_confirm_link_publish_read_and_no_review_inheritance');self.fx.setUp();self.addCleanup(self.fx.doCleanups)
  self.cid=self.fx.cid;self.now=datetime(2028,2,1,tzinfo=timezone.utc)
  self.text='イキヤ与阿夜约定：只有得到许可且测试通过后，才讨论是否执行；目前暂缓。'
  with app_module.connect() as db:self.entry,self.version=self.fx.ready(db,self.text)
 def flags(self,db,**flags):
  current=c.read(db,self.cid,1,self.entry['entryId']);values={k:flags.get(k,False) for k in c.FLAGS}
  return c.save(db,self.cid,1,self.entry['entryId'],current['revision'],values,'合成：明确保留条件')
 def projection(self,db,budget=1024):return ch.project(db,self.cid,1,[self.entry['entryId']],now=self.now,budget_tokens=budget)
 def test_important_and_foundational_do_not_become_resident(self):
  with app_module.connect() as db:
   self.flags(db,important=True,foundational=True)
   self.assertEqual(c.fixed_entries(db,self.cid,1),[])
   out=self.projection(db);self.assertEqual(out['blocks'][0]['projectionKind'],'title_index');self.assertNotIn('只有得到许可',out['blocks'][0]['text'])
 def test_both_explicit_choices_retain_whole_confirmed_synopsis_after_one_year(self):
  with app_module.connect() as db:
   for flag in ('permanent','fixed'):
    self.flags(db,**{flag:True});out=self.projection(db)
    self.assertEqual(out['blocks'][0]['text'],self.text);self.assertTrue(out['blocks'][0]['retainedBeyondTimeLayer'])
    self.assertEqual(out['retentionPolicy'],ch.RETENTION_POLICY)
    self.assertEqual(ch.expand(db,self.cid,1,self.entry['entryId'])['text'],self.version['body'])
 def test_cancel_applies_on_next_read_and_history_is_preserved(self):
  with app_module.connect() as db:
   self.flags(db,permanent=True);self.assertEqual(self.projection(db)['blocks'][0]['text'],self.text)
   self.flags(db);self.assertEqual(self.projection(db)['blocks'][0]['projectionKind'],'title_index')
   self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_curation').fetchone()[0],2)
 def test_unreviewed_revision_never_retained_and_republish_requires_check(self):
  with app_module.connect() as db:
   self.flags(db,fixed=True)
   draft=m.revise(db,self.entry['entryId'],self.cid,self.entry['versionId'],{'synopsis':'不得使用的待审新条件。'},1)
   self.assertEqual(self.projection(db)['blocks'],[])
   m.publish(db,self.entry['entryId'],self.cid,draft['versionId'],1)
   out=self.projection(db);self.assertEqual(out['blocks'][0]['projectionKind'],'title_index');self.assertNotIn('不得使用',str(out))
   self.assertEqual(out['retentionDiagnostics'][0]['reason'],'confirmed_synopsis_missing_title_only')
 def test_budget_never_truncates_retained_agreement(self):
  with app_module.connect() as db:
   self.flags(db,permanent=True);out=self.projection(db,12)
   self.assertEqual(out['blocks'],[]);self.assertEqual(out['estimatedTokens'],0)
   self.assertEqual(out['retentionDiagnostics'][0]['reason'],'retained_budget_exhausted')
 def test_actual_chat_builder_picks_up_flags_without_network(self):
  with patch.dict(os.environ,{'YOSAKI_CHRONICLE_CONTEXT':'1'}):
   with app_module.connect() as db:
    self.flags(db,permanent=True)
    memories,_,_=app_module.memory_context(db,self.cid,self.fx.conv,'无关的火山问题',chronicle_owner_id=1)
    self.assertEqual([x['content'] for x in memories if x.get('channel')=='chronicle'],[self.text])
    self.flags(db)
    memories,_,_=app_module.memory_context(db,self.cid,self.fx.conv,'无关的火山问题',chronicle_owner_id=1)
    self.assertEqual([x for x in memories if x.get('channel')=='chronicle'],[])
if __name__=='__main__':unittest.main()
