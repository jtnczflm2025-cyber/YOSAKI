"""A3.2 synthetic-only Tag migration, evidence and local task acceptance."""
import json
import time
import unittest
from unittest.mock import patch

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as legacy_jobs
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as a3_schema
from backend.app import memory_v2_tag_schema as tag_schema
from backend.app import memory_v2_tag_rules as rules
from backend.app import memory_v2_tags as tags
from backend.app import memory_v2_vocabulary as vocab


class TagRules(unittest.TestCase):
    def setUp(self):
        self.lex=[{'vocabulary_id':'one','kind':'tag','status':'active',
                   'name':'陶艺','language':'zh','aliases_json':'["制陶"]'}]

    def source(self, text):
        return [{'kind':'message','id':'1','role':'user','text':text}]

    def test_direct_topic_and_original_offsets(self):
        out=rules.evaluate(self.source('我没学陶艺，不过想聊聊陶艺的釉料。'),self.lex)
        self.assertEqual(len(out['hits']),1)
        self.assertEqual(out['suggestions'],[])
        point=out['hits'][0]['evidence'][0]
        self.assertEqual('我没学陶艺，不过想聊聊陶艺的釉料。'[point['start']:point['end']],out['hits'][0]['evidenceText'])
        self.assertIn('陶艺',out['hits'][0]['evidenceText'])

    def test_quote_mention_substring_and_ambiguity(self):
        for text in ('陶艺','她说“我们聊陶艺”，现在改谈排期。','我们去陶艺馆。'):
            with self.subTest(text=text):
                self.assertFalse(rules.evaluate(self.source(text),self.lex)['hits'])
        quoted='她说“我在学陶艺”，我们来讨论陶艺的课程。'
        self.assertEqual(len(rules.evaluate(self.source(quoted),self.lex)['hits']),1)
        duplicate=[*self.lex,{**self.lex[0],'vocabulary_id':'two'}]
        self.assertEqual(rules.evaluate(self.source('想聊陶艺。'),duplicate)['hits'],[])

    def test_unicode_and_language_boundary(self):
        api=[{'vocabulary_id':'api','kind':'tag','status':'active','name':'API',
              'language':'en','aliases_json':'[]'}]
        out=rules.evaluate(self.source('想了解 ＡＰＩ 的状态码。'),api)
        self.assertEqual(len(out['hits']),1)
        point=out['hits'][0]['evidence'][0]
        self.assertIn('ＡＰＩ','想了解 ＡＰＩ 的状态码。'[point['start']:point['end']])
        self.assertFalse(rules.evaluate(self.source('想了解 APIClient 的状态码。'),api)['hits'])
        self.assertFalse(rules.evaluate(self.source('陶芸について話そう。'),self.lex)['hits'])

    def test_confirmed_alias_and_japanese_topic(self):
        self.assertEqual(len(rules.evaluate(self.source('想聊制陶的釉料。'),self.lex)['hits']),1)
        japanese=[{'vocabulary_id':'ja','kind':'tag','status':'active','name':'陶芸',
                   'language':'ja','aliases_json':'[]'}]
        self.assertEqual(len(rules.evaluate(self.source('陶芸について話そう。'),japanese)['hits']),1)
        self.assertFalse(rules.evaluate(self.source('引用：「陶芸について話そう」。別の話をしよう。'),japanese)['hits'])


class TagTasks(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            metadata.init_db(db); legacy_jobs.init_db(db)
        with app_module.connect() as db:
            a3_schema.migrate(db)
        with app_module.connect() as db:
            tag_schema.migrate(db)

    def make_entry(self, text='想聊陶艺的釉料。', assistant='可以讨论陶艺的釉料。'):
        with app_module.connect() as db:
            refs=[]
            for role,body in (('user',text),('assistant',assistant)):
                mid=db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,'2026-01-01')",
                    (self.conv,role,body)).lastrowid
                refs.append({'kind':'message','id':str(mid)})
            self._index(db)
            return metadata.create_draft(db,self.cid,refs,'合成标题',1,'a32-'+refs[0]['id'])

    def tag(self, name='陶艺', aliases=(), language='zh'):
        with app_module.connect() as db:
            return vocab.write(db,self.cid,'tag',name,language,1,definition='合成主题',aliases=aliases)

    def run_tag(self, detail):
        with app_module.connect() as db:
            jid=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        return jid,processing.run_tag_job(app_module.connect,jid,self.cid,1)

    def merge_tag(self, source, target, *, aliases=()):
        with app_module.connect() as db:
            return vocab.write(db,self.cid,'tag',source['name'],source['language'],1,
                vocabulary_id=source['vocabulary_id'],expected_version=source['version'],
                definition=source['definition'],aliases=aliases,
                merged_into=target['vocabulary_id'],
                evidence=[{'synthetic':'owner-confirmed-merge'}])

    def suppress_tag(self, detail, tag_id, *, revision=0):
        with app_module.connect() as db:
            current=metadata.read_draft(db,detail['entryId'],self.cid)
            return tags.decide(db,detail['entryId'],self.cid,tag_id,'suppress',revision,
                               current['fields']['tags'].get('token'),1)

    def test_migration_idempotent_shape_and_rollback(self):
        with app_module.connect() as db:
            self.assertEqual(tag_schema.state(db),'valid')
            self.assertFalse(tag_schema.migrate(db))
            db.execute('PRAGMA foreign_key_check')
        with app_module.connect() as db:
            db.execute('UPDATE mv3_a32_schema SET schema_hash=?',('wrong',))
        with app_module.connect() as db:
            self.assertEqual(tag_schema.state(db),'invalid')
            with self.assertRaisesRegex(ValueError,'a32_schema_invalid'):
                tag_schema.migrate(db)
            with self.assertRaisesRegex(ValueError,'a32_schema_invalid'):
                legacy_jobs.enqueue(db,'no-entry',self.cid,1,'METADATA')

    def test_candidate_evidence_review_gate_and_idempotence(self):
        self.tag()
        detail=self.make_entry()
        jid,result=self.run_tag(detail)
        self.assertEqual(result['candidateCount'],1)
        self.assertEqual(result['proposalCount'],0)
        self.assertEqual(processing.run_tag_job(app_module.connect,jid,self.cid,1)['fieldSetId'],result['fieldSetId'])
        with app_module.connect() as db:
            after=metadata.read_draft(db,detail['entryId'],self.cid)
            field=after['fields']['tags']
            self.assertEqual(field['lifecycleStatus'],'draft')
            self.assertEqual(len(field['value']),1)
            self.assertEqual(after['fields']['title']['fieldId'],detail['fields']['title']['fieldId'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],0)
            self.assertFalse(after['qualifications']['formalContext'])
            count=db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0]
            jid2=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        self.assertEqual(processing.run_tag_job(app_module.connect,jid2,self.cid,1)['candidateCount'],0)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields').fetchone()[0],count)

    def test_protected_field_proposals_and_owner_suppression(self):
        tag=self.tag()
        detail=self.make_entry()
        with app_module.connect() as db:
            point={'kind':'message','id':detail['sourceRefs'][0]['source_id'],'start':2,'end':4}
            revised=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],
                {'tags':[{'id':tag['vocabulary_id'],'evidence':[point]}]},1)
            field=revised['fields']['tags']
            decision=tags.decide(db,detail['entryId'],self.cid,tag['vocabulary_id'],
                'suppress',0,field['token'],1)
            self.assertNotEqual(decision['fieldSetId'],revised['versionId'])
            after=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertEqual(after['fields']['tags']['value'],[])
            self.assertNotEqual(field['fieldId'],after['fields']['tags']['fieldId'])
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',
                                        (after['fields']['tags']['fieldId'],)).fetchone())
            with self.assertRaisesRegex(ValueError,'tag_decision_conflict'):
                tags.decide(db,detail['entryId'],self.cid,tag['vocabulary_id'],
                            'revoke',0,after['fields']['tags']['token'],1)
        jid,result=self.run_tag(after)
        self.assertEqual(result['candidateCount'],0)
        self.assertEqual(result['proposalCount'],0)

    def test_whole_field_suppression_does_not_propose(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            suppressed=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],{'tags':None},1)
        _,result=self.run_tag(suppressed)
        self.assertEqual((result['candidateCount'],result['proposalCount']),(0,0))
        with app_module.connect() as db:
            self.assertEqual(tags.list_proposals(db,detail['entryId'],self.cid,1),[])
            self.assertIsNone(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['value'])

    def test_protected_field_gets_management_proposal_only(self):
        known=self.tag()
        other=self.tag('水彩')
        detail=self.make_entry('想聊陶艺，也想聊水彩。','可以讨论水彩。')
        with app_module.connect() as db:
            point={'kind':'message','id':detail['sourceRefs'][0]['source_id'],'start':2,'end':4}
            protected=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],
                {'tags':[{'id':known['vocabulary_id'],'evidence':[point]}]},1)
            field_id=protected['fields']['tags']['fieldId']
            metadata.review_field(db,detail['entryId'],self.cid,'tags',protected['fields']['tags']['token'],1)
        _,result=self.run_tag(protected)
        self.assertEqual(result['candidateCount'],0)
        self.assertGreaterEqual(result['proposalCount'],1)
        with app_module.connect() as db:
            after=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertEqual(after['fields']['tags']['fieldId'],field_id)
            self.assertEqual(after['fields']['tags']['reviewStatus'],'human_checked')
            proposals=tags.list_proposals(db,detail['entryId'],self.cid,1)
            self.assertTrue(any(p['tagId']==other['vocabulary_id'] and p['valid'] for p in proposals))
            target_key=next(p['proposalKey'] for p in proposals if p['tagId']==other['vocabulary_id'])
            # A semantic definition change leaves history, but hides the stale suggestion.
            term=vocab.get(db,self.cid,other['vocabulary_id'])
            vocab.write(db,self.cid,'tag','水彩','zh',1,vocabulary_id=other['vocabulary_id'],
                        expected_version=term['version'],definition='不兼容的新语义')
            stale=tags.list_proposals(db,detail['entryId'],self.cid,1)
            hidden=next(p for p in stale if p['proposalKey']==target_key)
            self.assertFalse(hidden['valid'])
            self.assertNotIn('surface',hidden)

    def test_unknown_suggestion_reject_and_reopen(self):
        detail=self.make_entry('想聊玻璃工艺。','可以。')
        jid,result=self.run_tag(detail)
        self.assertGreaterEqual(result['proposalCount'],1)
        with app_module.connect() as db:
            current=tags.list_proposals(db,detail['entryId'],self.cid,1)
            self.assertTrue(current[0]['valid'])
            key=current[0]['proposalKey']
            tags.review_proposal(db,detail['entryId'],self.cid,key,1,'rejected',1)
            detail=metadata.read_draft(db,detail['entryId'],self.cid)
            jid2=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        # Same inputs are idempotent; even a new task key cannot re-propose a rejection.
        self.assertEqual(jid2,jid)
        with app_module.connect() as db:
            tags.review_proposal(db,detail['entryId'],self.cid,key,2,'reopened',1)
            self.assertEqual(tags.list_proposals(db,detail['entryId'],self.cid,1)[0]['state'],'reopened')
            new_job=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        self.assertNotEqual(new_job,jid)
        self.assertEqual(processing.run_tag_job(app_module.connect,new_job,self.cid,1)['proposalCount'],1)
        with app_module.connect() as db:
            self.assertEqual(tags.list_proposals(db,detail['entryId'],self.cid,1)[0]['state'],'pending')

    def test_distinct_source_evidence_and_rejected_identity(self):
        detail=self.make_entry('想聊玻璃工艺。','我也想聊玻璃工艺。')
        self.run_tag(detail)
        with app_module.connect() as db:
            proposals=tags.list_proposals(db,detail['entryId'],self.cid,1)
            self.assertEqual(len(proposals),2)
            self.assertNotEqual(proposals[0]['proposalKey'],proposals[1]['proposalKey'])
            tags.review_proposal(db,detail['entryId'],self.cid,proposals[0]['proposalKey'],1,'rejected',1)
            states={p['proposalKey']:p['state'] for p in tags.list_proposals(db,detail['entryId'],self.cid,1)}
            self.assertEqual(states[proposals[0]['proposalKey']],'rejected')
            self.assertEqual(states[proposals[1]['proposalKey']],'pending')

    def test_source_revoke_hides_proposal_surface(self):
        detail=self.make_entry('想聊玻璃工艺。','可以。')
        self.run_tag(detail)
        with app_module.connect() as db:
            before=tags.list_proposals(db,detail['entryId'],self.cid,1)
            self.assertTrue(before)
            db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
        with app_module.connect() as db:
            with self.assertRaises(ValueError):
                tags.list_proposals(db,detail['entryId'],self.cid,1)

    def test_cancel_and_stale_worker(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            jid=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        original=rules.evaluate
        def cancel_midway(sources,lexicon):
            with app_module.connect() as db:
                processing.cancel_tag_job(db,jid,self.cid,1)
            return original(sources,lexicon)
        with patch.object(rules,'evaluate',side_effect=cancel_midway):
            self.assertEqual(processing.run_tag_job(app_module.connect,jid,self.cid,1)['status'],'cancelled')
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',(jid,)).fetchone())

    def test_recovered_worker_rejects_old_claim(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            jid=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        original=rules.evaluate
        def expire_and_reclaim(sources,lexicon):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=? WHERE id=?',(time.time()-1,jid))
                self.assertEqual(processing.recover_tag_job(db,jid,self.cid,1),'paused')
                processing.resume(db,jid,self.cid,json.loads(db.execute(
                    'SELECT read_set_json FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0]))
            with patch.object(rules,'evaluate',original):
                winner=processing.run_tag_job(app_module.connect,jid,self.cid,1)
            self.assertEqual(winner['candidateCount'],1)
            return original(sources,lexicon)
        with patch.object(rules,'evaluate',side_effect=expire_and_reclaim):
            with self.assertRaisesRegex(ValueError,'tag_lease_changed'):
                processing.run_tag_job(app_module.connect,jid,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name=?',('tags',)).fetchone()[0],1)

    def test_unknown_scope_blocks_local_task(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            blocker=processing.create(db,self.cid,'entry',detail['entryId'],['tags'],
                {'synthetic':'blocker'},rule_version='test',input_version='test')
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?",(blocker,))
            with self.assertRaisesRegex(ValueError,'semantic_scope_unknown'):
                processing.create_tag_job(db,detail['entryId'],self.cid,1)

    def test_suppressed_merged_source_blocks_target(self):
        """Direct regression: an old suppressed ID must protect its merge target."""
        old=self.tag('旧陶艺')
        target=self.tag('陶艺')
        detail=self.make_entry()
        with app_module.connect() as db:
            tags.decide(db,detail['entryId'],self.cid,old['vocabulary_id'],
                        'suppress',0,None,1)
            vocab.write(db,self.cid,'tag','旧陶艺','zh',1,
                        vocabulary_id=old['vocabulary_id'],expected_version=old['version'],
                        definition='合成主题',merged_into=target['vocabulary_id'],
                        evidence=[{'synthetic':'owner-confirmed-merge'}])
        job,result=self.run_tag(detail)
        with app_module.connect() as db:
            field=metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']
            proposals=tags.list_proposals(db,detail['entryId'],self.cid,1)
            audit=db.execute('SELECT event FROM mv3_a3_audit WHERE job_id=?',(job,)).fetchone()[0]
        actual={'candidateCount':result['candidateCount'],
                'proposalCount':len(proposals),
                'tagIds':[item['id'] for item in (field['value'] or [])],
                'diagnostic':audit}
        self.assertEqual(actual,{'candidateCount':0,'proposalCount':0,
                                 'tagIds':[],'diagnostic':'tag_merge_suppressed'})

    def test_chain_and_converging_merges_check_all_ancestors(self):
        old=self.tag('旧陶艺'); middle=self.tag('制陶'); target=self.tag('陶艺')
        detail=self.make_entry()
        self.suppress_tag(detail,old['vocabulary_id'])
        self.merge_tag(old,middle)
        self.merge_tag(middle,target)
        _,result=self.run_tag(detail)
        self.assertEqual(result['candidateCount'],0)
        self.assertIn('tag_merge_suppressed',result['reasonCodes'])

        sibling=self.tag('陶器制作')
        second=self.make_entry()
        self.suppress_tag(second,sibling['vocabulary_id'])
        self.merge_tag(sibling,target)
        _,result=self.run_tag(second)
        self.assertEqual(result['candidateCount'],0)
        self.assertIn('tag_merge_suppressed',result['reasonCodes'])

    def test_target_keep_does_not_cancel_old_suppress(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry()
        with app_module.connect() as db:
            point={'kind':'message','id':detail['sourceRefs'][0]['source_id'],'start':2,'end':4}
            revised=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],
                {'tags':[{'id':target['vocabulary_id'],'evidence':[point]}]},1)
            tags.decide(db,detail['entryId'],self.cid,target['vocabulary_id'],'keep',0,
                        revised['fields']['tags']['token'],1)
        self.suppress_tag(revised,old['vocabulary_id'])
        self.merge_tag(old,target)
        _,result=self.run_tag(revised)
        self.assertEqual((result['candidateCount'],result['proposalCount']),(0,0))
        self.assertIn('tag_merge_suppressed',result['reasonCodes'])
        with app_module.connect() as db:
            after=metadata.read_draft(db,detail['entryId'],self.cid)
            self.assertEqual(after['fields']['tags']['fieldId'],revised['fields']['tags']['fieldId'])

    def test_explicit_revoke_restores_evaluation_only(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry()
        self.suppress_tag(detail,old['vocabulary_id'])
        self.merge_tag(old,target)
        with app_module.connect() as db:
            tags.decide(db,detail['entryId'],self.cid,old['vocabulary_id'],'revoke',1,None,1)
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['completionStatus'],'missing')
        _,result=self.run_tag(detail)
        self.assertEqual(result['candidateCount'],1)
        with app_module.connect() as db:
            field=metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']
            self.assertEqual(field['lifecycleStatus'],'draft')
            self.assertEqual(field['reviewStatus'],'needs_review')

        other=self.tag('另一旧名')
        second=self.make_entry()
        self.suppress_tag(second,old['vocabulary_id'])
        self.suppress_tag(second,other['vocabulary_id'])
        self.merge_tag(other,target)
        with app_module.connect() as db:
            tags.decide(db,second['entryId'],self.cid,old['vocabulary_id'],'revoke',1,None,1)
        _,result=self.run_tag(second)
        self.assertEqual(result['candidateCount'],0)
        self.assertIn('tag_merge_suppressed',result['reasonCodes'])

    def test_target_name_old_name_and_alias_cannot_bypass(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        self.merge_tag(old,target)
        with app_module.connect() as db:
            target=vocab.write(db,self.cid,'tag','陶艺','zh',1,
                vocabulary_id=target['vocabulary_id'],expected_version=target['version'],
                definition=target['definition'],aliases=['旧陶艺','制陶'])
        for index,spelling in enumerate(('陶艺','旧陶艺','制陶')):
            with self.subTest(spelling=spelling):
                detail=self.make_entry('想聊'+spelling+'。','可以。')
                self.suppress_tag(detail,old['vocabulary_id'])
                _,result=self.run_tag(detail)
                self.assertEqual((result['candidateCount'],result['proposalCount']),(0,0))
                self.assertIn('tag_merge_suppressed',result['reasonCodes'])

    def test_old_name_without_target_alias_cannot_be_unknown_suggestion(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry('想聊旧陶艺。','可以。')
        self.suppress_tag(detail,old['vocabulary_id'])
        self.merge_tag(old,target)
        _,result=self.run_tag(detail)
        self.assertEqual((result['candidateCount'],result['proposalCount']),(0,0))
        self.assertIn('tag_merge_suppressed',result['reasonCodes'])

    def test_unrelated_tag_in_same_entry_still_gets_candidate(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺'); other=self.tag('水彩')
        detail=self.make_entry('想聊陶艺，也想聊水彩。','可以讨论水彩。')
        self.suppress_tag(detail,old['vocabulary_id'])
        self.merge_tag(old,target)
        _,result=self.run_tag(detail)
        self.assertEqual(result['candidateCount'],1)
        self.assertIn('tag_merge_suppressed',result['reasonCodes'])
        with app_module.connect() as db:
            values=metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['value']
            self.assertEqual([item['id'] for item in values],[other['vocabulary_id']])

    def test_merge_added_after_calculation_fails_commit(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry()
        self.suppress_tag(detail,old['vocabulary_id'])
        original=rules.evaluate
        def merge_midway(sources,lexicon):
            result=original(sources,lexicon)
            self.merge_tag(old,target)
            return result
        with app_module.connect() as db:
            jid=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        with patch.object(rules,'evaluate',side_effect=merge_midway):
            result=processing.run_tag_job(app_module.connect,jid,self.cid,1)
        self.assertEqual(result,{'status':'failed','reason':'tag_input_changed'})
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['completionStatus'],'missing')
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',(jid,)).fetchone())

    def test_suppression_added_after_calculation_fails_commit(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry()
        self.merge_tag(old,target)
        original=rules.evaluate
        def suppress_midway(sources,lexicon):
            result=original(sources,lexicon)
            self.suppress_tag(detail,old['vocabulary_id'])
            return result
        with app_module.connect() as db:
            jid=processing.create_tag_job(db,detail['entryId'],self.cid,1)
        with patch.object(rules,'evaluate',side_effect=suppress_midway):
            result=processing.run_tag_job(app_module.connect,jid,self.cid,1)
        self.assertEqual(result,{'status':'failed','reason':'tag_input_changed'})
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['completionStatus'],'missing')

    def test_premerge_proposal_is_not_adoptable_after_merge(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry()
        with app_module.connect() as db:
            protected=metadata.revise(db,detail['entryId'],self.cid,detail['versionId'],{'tags':[]},1)
        prior_job,prior_result=self.run_tag(protected)
        self.assertGreater(prior_result['proposalCount'],0)
        with app_module.connect() as db:
            before=tags.list_proposals(db,detail['entryId'],self.cid,1)
            target_proposal=next(x for x in before if x['tagId']==target['vocabulary_id'])
            self.assertTrue(target_proposal['valid'])
        self.suppress_tag(protected,old['vocabulary_id'])
        self.merge_tag(old,target)
        with app_module.connect() as db:
            after=tags.list_proposals(db,detail['entryId'],self.cid,1)
            blocked=next(x for x in after if x['proposalKey']==target_proposal['proposalKey'])
            self.assertEqual(blocked['reason'],'tag_merge_suppressed')
            self.assertNotIn('surface',blocked)
            with self.assertRaisesRegex(ValueError,'tag_merge_suppressed'):
                tags.review_proposal(db,detail['entryId'],self.cid,
                    target_proposal['proposalKey'],target_proposal['revision'],'handled',1)
            new_job=processing.create_tag_job(db,detail['entryId'],self.cid,1)
            self.assertNotEqual(new_job,prior_job)
        result=processing.run_tag_job(app_module.connect,new_job,self.cid,1)
        self.assertEqual((result['candidateCount'],result['proposalCount']),(0,0))
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_tag_proposal_revisions WHERE proposal_key=?',
                                        (target_proposal['proposalKey'],)).fetchone()[0],1)

    def test_premerge_auto_field_becomes_stale_after_suppression(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry()
        _,result=self.run_tag(detail)
        self.assertEqual(result['candidateCount'],1)
        self.suppress_tag(detail,old['vocabulary_id'])
        self.merge_tag(old,target)
        with app_module.connect() as db:
            field=metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']
            self.assertEqual(field['lifecycleStatus'],'stale')
            self.assertIsNone(field['value'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0],0)

    def test_old_local_task_contract_cannot_run_after_guard_upgrade(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            jid=processing.create_tag_job(db,detail['entryId'],self.cid,1)
            row=db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(jid,)).fetchone()
            payload=json.loads(row['payload_json'])
            payload['readSet'].pop('mergeGuardVersion')
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(payload),jid))
        with self.assertRaisesRegex(ValueError,'not_tag_job'):
            processing.run_tag_job(app_module.connect,jid,self.cid,1)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0],'queued')

    def test_corrupt_graph_shapes_fail_closed(self):
        """Defensive fixtures: normal vocabulary.write rejects these shapes."""
        base={'character_id':self.cid,'kind':'tag','status':'deprecated',
              'name':'合成','language':'zh','aliases_json':'[]'}
        cases=(
            {'A':{**base,'merged_into':'missing'}},
            {'A':{**base,'merged_into':'B'},'B':{**base,'merged_into':'A'}},
            {'A':{**base,'merged_into':'C'},
             'C':{**base,'kind':'category','status':'active','merged_into':None}},
            {'A':{**base,'merged_into':'C'},
             'C':{**base,'character_id':self.cid+10,'status':'active','merged_into':None}},
        )
        for graph in cases:
            with self.subTest(graph=graph):
                with self.assertRaisesRegex(ValueError,'tag_merge_invalid'):
                    tags._root(graph,'A',self.cid)

    def test_cross_role_incoming_merge_fails_entire_local_task(self):
        target=self.tag('陶艺'); detail=self.make_entry()
        with app_module.connect() as db:
            other_role=db.execute("INSERT INTO characters(name,created_at,updated_at) VALUES('other','2026-01-01','2026-01-01')").lastrowid
            foreign=vocab.write(db,other_role,'tag','跨角色旧名','zh',1,definition='合成主题')
            # Defensive corruption only: the public vocabulary writer forbids
            # a cross-role target, but a damaged SQLite row must fail closed.
            db.execute('''UPDATE mv3_vocabulary_versions SET status='deprecated',merged_into=?
                WHERE vocabulary_id=? AND version=?''',
                (target['vocabulary_id'],foreign['vocabulary_id'],foreign['version']))
        _,result=self.run_tag(detail)
        self.assertEqual(result,{'status':'failed','reason':'tag_merge_invalid'})
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['completionStatus'],'missing')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_tag_proposal_revisions WHERE entry_id=?',
                                        (detail['entryId'],)).fetchone()[0],0)

    def test_non_tag_incoming_merge_fails_entire_local_task(self):
        target=self.tag('陶艺'); detail=self.make_entry()
        with app_module.connect() as db:
            wrong=vocab.write(db,self.cid,'category','合成分类','zh',1,
                              dimension='form',definition='合成定义')
            # Defensive corruption: the writer cannot merge a Category into Tag.
            db.execute('''UPDATE mv3_vocabulary_versions SET status='deprecated',merged_into=?
                WHERE vocabulary_id=? AND version=?''',
                (target['vocabulary_id'],wrong['vocabulary_id'],wrong['version']))
        _,result=self.run_tag(detail)
        self.assertEqual(result,{'status':'failed','reason':'tag_merge_invalid'})
        with app_module.connect() as db:
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['completionStatus'],'missing')

    def test_old_name_without_suppression_is_not_new_entity(self):
        old=self.tag('旧陶艺'); target=self.tag('陶艺')
        detail=self.make_entry('想聊旧陶艺。','可以。')
        self.merge_tag(old,target)
        _,result=self.run_tag(detail)
        self.assertEqual((result['candidateCount'],result['proposalCount']),(0,0))
        self.assertIn('tag_existing_vocabulary_reference',result['reasonCodes'])

    def test_migrated_mock_tag_is_settled_but_not_published(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            job=legacy_jobs.enqueue(db,detail['entryId'],self.cid,1,'METADATA',mock_model=True)
        adapter=legacy_jobs.MockMetadataAdapter({'tags':[],'keywords':[]},input_tokens=7,output_tokens=4)
        result=legacy_jobs.dispatch_mock(app_module.connect,job['id'],self.cid,adapter)
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['reason'],'tag_pipeline_required')
        with app_module.connect() as db:
            chunk=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone()
            self.assertEqual((chunk['state'],chunk['used_input_tokens'],chunk['used_output_tokens']),('rejected',7,4))
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',(job['id'],)).fetchone())
            self.assertEqual(metadata.read_draft(db,detail['entryId'],self.cid)['fields']['tags']['completionStatus'],'missing')

    def test_migrated_mock_unknown_keeps_reservation(self):
        detail=self.make_entry()
        with app_module.connect() as db:
            job=legacy_jobs.enqueue(db,detail['entryId'],self.cid,1,'METADATA',mock_model=True)
        result=legacy_jobs.dispatch_mock(app_module.connect,job['id'],self.cid,
            legacy_jobs.MockMetadataAdapter({},timeout=True))
        self.assertEqual(result['status'],'unknown')
        with app_module.connect() as db:
            chunk=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(job['id'],)).fetchone()
            self.assertEqual(chunk['state'],'unknown')
            self.assertEqual(chunk['reserved_tokens'],4096)
            self.assertIsNone(chunk['used_input_tokens'])
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(job['id'],)).fetchone()[0],'unknown')

    def test_owner_role_and_fk_scope(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            with self.assertRaises(ValueError):
                tags.list_proposals(db,detail['entryId'],self.cid+100,1)
            with self.assertRaises(ValueError):
                processing.create_tag_job(db,detail['entryId'],self.cid+100,1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_role_deletion_cascades_without_orphaning_history(self):
        term=self.tag(); detail=self.make_entry('想聊玻璃工艺。','可以。')
        self.run_tag(detail)
        with app_module.connect() as db:
            tags.decide(db,detail['entryId'],self.cid,term['vocabulary_id'],
                'suppress',0,None,1)
            self.assertTrue(db.execute('SELECT 1 FROM mv3_entry_tag_decisions WHERE character_id=?',(self.cid,)).fetchone())
            self.assertTrue(db.execute('SELECT 1 FROM mv3_tag_proposal_revisions WHERE character_id=?',(self.cid,)).fetchone())
            db.execute('DELETE FROM characters WHERE id=?',(self.cid,))
        with app_module.connect() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertFalse(db.execute('SELECT 1 FROM mv3_entry_tag_decisions WHERE character_id=?',(self.cid,)).fetchone())
            self.assertFalse(db.execute('SELECT 1 FROM mv3_tag_proposal_revisions WHERE character_id=?',(self.cid,)).fetchone())

    def test_legacy_metadata_after_migration_does_not_write_tag(self):
        self.tag(); detail=self.make_entry()
        with app_module.connect() as db:
            job=legacy_jobs.enqueue(db,detail['entryId'],self.cid,1,'METADATA')
            result=legacy_jobs.execute(db,job,json.loads(job['payload_json']))
            self.assertEqual(result['fields']['tags']['completionStatus'],'missing')
            self.assertTrue(db.execute("SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event='legacy_tags_deferred'",(job['id'],)).fetchone())


class TagMigrationAndLegacy(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            metadata.init_db(db); legacy_jobs.init_db(db)
        with app_module.connect() as db:
            a3_schema.migrate(db)

    def test_legacy_schema_behavior_and_a32_refusal(self):
        with app_module.connect() as db:
            self.assertEqual(tag_schema.state(db),'absent')
            term=vocab.write(db,self.cid,'tag','陶艺','zh',1,definition='合成主题')
            mid=db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','想聊陶艺。','2026-01-01')",
                           (self.conv,)).lastrowid
            self._index(db)
            detail=metadata.create_draft(db,self.cid,[{'kind':'message','id':str(mid)}],
                                         '合成标题',1,'a32-legacy')
            with self.assertRaisesRegex(ValueError,'a32_migration_required'):
                processing.create_tag_job(db,detail['entryId'],self.cid,1)
            old=legacy_jobs.enqueue(db,detail['entryId'],self.cid,1,'METADATA')
            result=legacy_jobs.execute(db,old,json.loads(old['payload_json']))
            self.assertEqual(result['fields']['tags']['value'][0]['id'],term['vocabulary_id'])

    def test_partial_migration_rolls_back(self):
        original=tag_schema.DDL['ix_mv3_tag_proposals_read']
        with app_module.connect() as db:
            with patch.dict(tag_schema.DDL,{'ix_mv3_tag_proposals_read':'CREATE INVALID A32'}):
                with self.assertRaises(Exception):
                    tag_schema.migrate(db)
            self.assertEqual(tag_schema.state(db),'absent')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(tag_schema.DDL['ix_mv3_tag_proposals_read'],original)

if __name__=='__main__': unittest.main()
