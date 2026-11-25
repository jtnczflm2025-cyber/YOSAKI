"""Preview describes the same actual memory_context projection, no external calls."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_memory_v2_retained_synopsis as fixture
from test_memory_v2_entries import app_module
from backend.app import memory_v2_chronicle_channel as channel, memory_v2_metadata as meta

class ContextBudgetPreview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Retention();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cid=self.f.cid;self.eid=self.f.entry['entryId']
    def test_actual_preview_matches_carried_content_and_budget(self):
        with app_module.connect() as db:self.f.flags(db,permanent=True)
        with patch.dict(os.environ,{'YOSAKI_CHRONICLE_CONTEXT':'1'}):
            out=app_module.context_preview(app_module.PreviewInput(character_id=self.cid,
                conversation_id=self.f.fx.conv,message='无关的火山'),SimpleNamespace(state=SimpleNamespace(user={'id':1})))
        report=out['chronicle'];self.assertTrue(report['enabled'])
        self.assertNotIn('reason',report)
        self.assertEqual(report['budget']['limitTokens'],1024);self.assertEqual(report['budget']['limitBlocks'],5)
        item=next(x for x in report['selectionDetails'] if x['entryId']==self.eid)
        self.assertTrue(item['included']);self.assertTrue(item['retained'])
        self.assertEqual(item['projection'],'entry_synopsis')
        self.assertIn(self.f.text,str(out['messages']))
        self.assertEqual(item['estimatedTokens'],report['budget']['usedTokens'])
    def test_omitted_and_title_only_are_explicit_without_stale_body(self):
        with app_module.connect() as db:
            self.f.flags(db,fixed=True)
            out=channel.project(db,self.cid,1,[self.eid],now=self.f.now,budget_tokens=12)
            self.assertEqual(out['budget']['usedTokens'],0)
            self.assertEqual(out['selectionDetails'][0]['reason'],'retained_budget_exhausted')
            d=meta.revise(db,self.eid,self.cid,self.f.entry['versionId'],{'synopsis':'新待审内容不得返回'},1)
            meta.publish(db,self.eid,self.cid,d['versionId'],1)
            out=channel.project(db,self.cid,1,[self.eid],now=self.f.now)
            self.assertEqual(out['selectionDetails'][0]['projection'],'title_index')
            self.assertEqual(out['selectionDetails'][0]['reason'],'confirmed_synopsis_missing_title_only')
            self.assertNotIn('新待审内容',str(out))
    def test_revoke_and_cancel_reflected_on_next_preview(self):
        with app_module.connect() as db:
            self.f.flags(db,fixed=True)
            self.f.flags(db)
            out=channel.project(db,self.cid,1,[self.eid],now=self.f.now)
            self.assertFalse(out['selectionDetails'][0]['retained'])
            with self.assertRaises(ValueError):channel.project(db,self.cid,999,[self.eid])

    def test_saved_memory_switch_controls_actual_preview(self):
        with app_module.connect() as db:
            self.f.flags(db,permanent=True)
        with patch.dict(os.environ,{'YOSAKI_CHRONICLE_CONTEXT':'1'}):
            for enabled in (False, True):
                app_module.save_context_settings(self.cid, app_module.ContextSettingsInput(memory_enabled=enabled))
                saved=app_module.get_context_settings(self.cid)
                self.assertEqual(bool(saved['memory_enabled']),enabled)
                out=app_module.context_preview(app_module.PreviewInput(character_id=self.cid,
                    conversation_id=self.f.fx.conv,message='synthetic unrelated topic'),
                    SimpleNamespace(state=SimpleNamespace(user={'id':1})))
                self.assertEqual(out['chronicle']['enabled'], enabled)
                self.assertEqual(out['chronicle']['budget']['usedBlocks'],1 if enabled else 0)
                if not enabled:
                    self.assertNotIn(self.f.text,str(out['messages']))
