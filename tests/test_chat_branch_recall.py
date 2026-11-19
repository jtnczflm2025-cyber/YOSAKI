"""Real qualified Chronicle fixtures, synthetic content only, no provider."""
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
import os
import test_memory_v2_closeout as fixture
from backend.app import chat_history as h,chat_api,memory_v2_chronicle_channel as channel
from backend.app import memory_v2_chronicle_tools as tools,memory_v2_sources as sources


class BranchRecall(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.Closeout('test_explicit_confirm_link_publish_read_and_no_review_inheritance')
        self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.m=fixture.app_module
        with self.m.connect() as db:self.entry,self.narrative=self.fx.ready(db)

    def test_chronicle_expand_positive_prefix_negative_future_and_path_delete(self):
        with self.m.connect() as db:
            before=h.fork(db,self.fx.conv,self.fx.user,self.m.now())
            complete=h.fork(db,self.fx.conv,self.fx.assistant,self.m.now())
            with h.reading(db,complete):
                self.assertEqual(channel.expand(db,self.fx.cid,1,self.entry['entryId'])['text'],self.narrative['body'])
            with h.reading(db,before):
                with self.assertRaises(ValueError):channel.expand(db,self.fx.cid,1,self.entry['entryId'])
                self.assertEqual(channel.project(db,self.fx.cid,1,[self.entry['entryId']],now=datetime(2026,1,2,tzinfo=timezone.utc))['blocks'],[])
            db.execute('INSERT INTO chat_path_exclusions VALUES(?,?,?,?)',(complete,self.fx.assistant,self.fx.assistant,self.m.now()))
            with h.reading(db,complete):
                with self.assertRaises(ValueError):channel.expand(db,self.fx.cid,1,self.entry['entryId'])
            with h.reading(db,self.fx.conv):
                self.assertEqual(channel.expand(db,self.fx.cid,1,self.entry['entryId'])['text'],self.narrative['body'])

    def test_original_path_rejects_child_source_but_child_can_read_it(self):
        with self.m.connect() as db:
            child=h.fork(db,self.fx.conv,self.fx.user,self.m.now())
            mid=db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'assistant','only on child',?)",(child,self.m.now())).lastrowid
            with h.reading(db,self.fx.conv):self.assertFalse(h.source_allowed(db,mid,child))
            with h.reading(db,child):self.assertTrue(h.source_allowed(db,mid,child))

    def test_changed_version_never_inherits_approved_narrative(self):
        route=next(r.endpoint for r in self.m.app.routes if getattr(r,'path','')=='/api/chat/messages/{mid}/edit')
        changed=route(self.fx.assistant,chat_api.Action(conversation_id=self.fx.conv,expected_head=self.fx.assistant,content='人工更正，未审核'))
        with self.m.connect() as db:
            with h.reading(db,changed['conversation_id']):
                with self.assertRaises(ValueError):channel.expand(db,self.fx.cid,1,self.entry['entryId'])
            self.assertEqual(channel.expand(db,self.fx.cid,1,self.entry['entryId'])['text'],self.narrative['body'])


if __name__=='__main__':unittest.main()
