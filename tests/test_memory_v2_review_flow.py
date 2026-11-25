"""Self-contained synthetic review/link/edit/publish lifecycle; no Provider."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_memory_v2_closeout as fixture
from backend.app import memory_v2_chronicle_workspace as w, memory_v2_metadata as m
from backend.app import memory_v2_derivation as d, memory_v2_dialogue_refinement as f
from test_memory_v2_entries import app_module

class ReviewFlow(unittest.TestCase):
 def setUp(self):
  self.fx=fixture.Closeout('test_explicit_confirm_link_publish_read_and_no_review_inheritance');self.fx.setUp();self.addCleanup(self.fx.doCleanups)
  self.cid=self.fx.cid;self.eid=self.fx.draft['entryId']
  api=FastAPI();w.install(api,app_module.connect,lambda _:{'id':1});self.client=TestClient(api)
  self.url=f'/api/memory-v2/workspace/entries/{self.eid}'
 def view(self):
  r=self.client.get(self.url,params={'characterId':self.cid});self.assertEqual(r.status_code,200,r.text);return r.json()
 def create(self):
  v=self.view();r=self.client.post(self.url+'/refine',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId'],expectedNarrativeVersionId=v['narrative']['id'] if v['narrative'] else None));self.assertEqual(r.status_code,200,r.text);return self.view()
 def confirm(self,v):
  n=v['narrative'];return self.client.post(self.url+'/confirm-narrative',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId'],versionId=n['id'],inputHash=n['input_hash'],outputHash=n['output_hash']))
 def edit(self,v,body):
  return self.client.post(self.url+'/narrative',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId'],expectedNarrativeVersionId=v['narrative']['id'] if v['narrative'] else None,body=body))
 def test_stale_narrative_confirmation_cannot_activate_previous_draft(self):
  old=self.create()
  with app_module.connect() as db:
   item=d.create_derivation(db,self.cid,'NARRATIVE',self.fx.refs());new=d.add_version(db,item['id'],self.fx.refs(),'イキヤ说累了。\n\n我说：请先休息，之后再讨论。',author_kind='user')
  response=self.confirm(old);self.assertEqual(response.status_code,409,response.text)
  self.assertEqual(response.json()['detail'],'narrative_snapshot_changed')
  with app_module.connect() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0],0)
   self.assertIsNone(m.read_draft(db,self.eid,self.cid)['fields']['narrative']['value'])
 def test_confirm_link_retry_is_idempotent_and_not_published(self):
  first=self.confirm(self.create());self.assertEqual(first.status_code,200,first.text);view=first.json()
  self.assertEqual(view['entry']['fields']['narrative']['value'],view['narrative']['id'])
  self.assertEqual(view['entry']['fields']['narrative']['lifecycleStatus'],'available')
  second=self.confirm(view);self.assertEqual(second.status_code,200,second.text)
  self.assertEqual(second.json()['entry']['versionId'],view['entry']['versionId'])
  with app_module.connect() as db:
   self.assertEqual(db.execute('SELECT status FROM mv3_entries WHERE id=?',(self.eid,)).fetchone()[0],'draft')
   self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0],1)
 def test_two_browser_edits_compare_actual_narrative_version(self):
  old=self.create();body=old['narrative']['body']+'\n\n明确保留条件。'
  a=self.edit(old,body);self.assertEqual(a.status_code,200,a.text)
  b=self.edit(old,'旧窗口不应覆盖新正文。');self.assertEqual(b.status_code,409,b.text)
  self.assertEqual(self.view()['narrative']['body'],body)
 def test_reviewed_text_revision_requires_its_own_review_and_republish(self):
  view=self.confirm(self.create()).json()
  with app_module.connect() as db:
   current=m.read_draft(db,self.eid,self.cid);m.review_field(db,self.eid,self.cid,'title',current['fields']['title']['token'],1)
   m.publish(db,self.eid,self.cid,current['versionId'],1)
  old=self.view();newbody=old['narrative']['body']+'\n\n我说：条件尚未满足。'
  result=self.edit(old,newbody);self.assertEqual(result.status_code,200,result.text)
  now=self.view();self.assertNotEqual(now['narrative']['id'],old['narrative']['id'])
  self.assertEqual(now['narrative']['status'],'needs_review');self.assertEqual(now['narrative']['review_status'],'unreviewed')
  confirmed=self.confirm(now);self.assertEqual(confirmed.status_code,200,confirmed.text)
  linked=confirmed.json();self.assertEqual(linked['entry']['fields']['narrative']['value'],linked['narrative']['id'])
  with app_module.connect() as db:
   with self.assertRaises(ValueError):m.read_active(db,self.eid,self.cid)
   m.publish(db,self.eid,self.cid,linked['entry']['versionId'],1)
   self.assertEqual(m.read_active(db,self.eid,self.cid)['narrativeVersionId'],linked['narrative']['id'])
 def test_metadata_revision_keeps_same_confirmed_narrative(self):
  view=self.confirm(self.create()).json();n=view['narrative']
  response=self.client.post(f'/api/memory-v2/entry-drafts/{self.eid}/revisions',json=dict(characterId=self.cid,expectedVersion=view['entry']['versionId'],changes={'title':'修改标题不替换精修','synopsis':'イキヤ说累了，阿夜建议休息。'}))
  self.assertEqual(response.status_code,200,response.text)
  now=self.view();self.assertEqual(now['narrative'],n);self.assertEqual(now['entry']['fields']['narrative']['lifecycleStatus'],'available')
  self.assertEqual(now['entry']['fields']['synopsis']['lifecycleStatus'],'draft')

 def test_guarded_publish_does_not_publish_a_different_visible_refinement(self):
  old=self.confirm(self.create()).json()
  with app_module.connect() as db:
   m.review_field(db,self.eid,self.cid,'title',old['entry']['fields']['title']['token'],1)
  self.assertEqual(self.edit(old,old['narrative']['body']+'\n新候选尚未确认。').status_code,200)
  now=self.view();url=f'/api/memory-v2/entry-drafts/{self.eid}/publish'
  stale=self.client.post(url,json=dict(characterId=self.cid,expectedVersion=old['entry']['versionId'],expectedNarrativeVersionId=old['narrative']['id']))
  self.assertEqual(stale.status_code,409);self.assertEqual(stale.json()['detail'],'narrative_snapshot_changed')
  pending=self.client.post(url,json=dict(characterId=self.cid,expectedVersion=now['entry']['versionId'],expectedNarrativeVersionId=now['narrative']['id']))
  self.assertEqual(pending.status_code,409);self.assertEqual(pending.json()['detail'],'narrative_confirmation_required')
  confirmed=self.confirm(now).json()
  result=self.client.post(url,json=dict(characterId=self.cid,expectedVersion=confirmed['entry']['versionId'],expectedNarrativeVersionId=confirmed['narrative']['id']))
  self.assertEqual(result.status_code,200,result.text)

 def test_guarded_publish_requires_visible_synopsis_confirmation(self):
  old=self.confirm(self.create()).json()
  with app_module.connect() as db:
   m.review_field(db,self.eid,self.cid,'title',old['entry']['fields']['title']['token'],1)
   now=m.revise(db,self.eid,self.cid,old['entry']['versionId'],{'synopsis':'独立待审梗概。'},1)
  args=dict(characterId=self.cid,expectedVersion=now['versionId'],expectedNarrativeVersionId=old['narrative']['id'])
  response=self.client.post(f'/api/memory-v2/entry-drafts/{self.eid}/publish',json=args)
  self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail'],'synopsis_confirmation_required')

 def test_reopening_is_read_only_and_edit_snapshot_required(self):
  v=self.create()
  with app_module.connect() as db:before={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('mv3_versions','mv3_entry_field_sets','mv3_reviews','mv3_entry_field_checks')}
  self.assertEqual(self.view(),self.view())
  response=self.client.post(self.url+'/narrative',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId'],body='过时客户端缺少精修版本。'))
  self.assertEqual(response.status_code,409)
  with app_module.connect() as db:
   after={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before}
  self.assertEqual(before,after)

if __name__=='__main__':unittest.main()
