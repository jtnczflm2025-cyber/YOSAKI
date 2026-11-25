"""A3.4 tests use only synthetic H3.2 receipts and injected local Mock."""
import unittest

import test_memory_v2_h32 as h32
from test_memory_v2_h32 import app_module, importer
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as legacy_jobs
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_synopsis as synopsis
from backend.app.memory_v2_model_adapter import MockSynopsisAdapter


def mock_output(request):
    if request['phase'] == 'batch':
        sources = request['input']['sources']
        chosen = sources[0]
        text = chosen['text']
        units = [{'start':0,'end':len(text),'evidence':[{
            'kind':chosen['kind'],'id':chosen['id'],'start':0,'end':len(text),
            'quote':text,'speaker':chosen['speaker']}]}]
    else:
        parts = request['input']['parts']
        text = parts[0]['text']
        units = parts[0]['units']
        sources = request['input']['sourceManifest']
    return {'text':text,'units':units,'sourceCoverage':synopsis.coverage_for(sources,units)},20,20


class SynopsisStructure(unittest.TestCase):
    def sources(self, text='如果周六不下雨，再去公园。'):
        return [{'kind':'message','id':'1','speaker':'user','text':text,
                 'sourceStatus':'imported','contentHash':'a','stateHash':'b'}]

    def output(self, text='如果周六不下雨，再去公园。'):
        return {'text':text,'units':[{'start':0,'end':len(text),'evidence':[
            {'kind':'message','id':'1','start':0,'end':len(text),'quote':text,
             'speaker':'user'}]}],
            'sourceCoverage':[{'kind':'message','id':'1','used':True}]}

    def test_exact_original_offsets_and_no_unmapped_clause(self):
        source=self.sources(); self.assertEqual(synopsis.validate_output(self.output(),source)['text'],source[0]['text'])
        item=self.output(); item['text']+='。'
        with self.assertRaisesRegex(ValueError,'synopsis_unmapped_output'):
            synopsis.validate_output(item,source)
        item=self.output(); item['units'][0]['evidence'][0]['quote']='规范化替身'
        with self.assertRaisesRegex(ValueError,'synopsis_evidence_invalid'):
            synopsis.validate_output(item,source)

    def test_duplicate_wrong_role_and_coverage_rejected(self):
        source=self.sources(); item=self.output(); item['units'][0]['evidence'][0]['speaker']='assistant'
        with self.assertRaisesRegex(ValueError,'synopsis_evidence_invalid'):
            synopsis.validate_output(item,source)
        item=self.output(); item['sourceCoverage'][0]['used']=False
        with self.assertRaisesRegex(ValueError,'synopsis_coverage_invalid'):
            synopsis.validate_output(item,source)
        with self.assertRaisesRegex(ValueError,'synopsis_duplicate_input'):
            synopsis.validate_output(self.output(),source*2)

    def test_limited_semantics_and_unicode_character_cap(self):
        source=self.sources('我们只讨论排期。')
        item=self.output('我们只讨论排期。')
        item['text']='她因此很安心。'; item['units'][0]['end']=len(item['text'])
        item['units'][0]['evidence'][0]['quote']=source[0]['text']
        with self.assertRaisesRegex(ValueError,'synopsis_unsupported_psychology'):
            synopsis.validate_output(item,source)
        with self.assertRaisesRegex(ValueError,'synopsis_output_length_invalid'):
            synopsis.validate_output({'text':'界'*3001,'units':[],'sourceCoverage':[]},source)

    def test_bounded_batches_are_ordered_and_complete(self):
        sources=[{**self.sources(f'合成消息{i}')[0],'id':str(i)} for i in range(12)]
        batches=synopsis.plan_batches(sources,input_cap=1024)
        self.assertGreater(len(batches),1)
        self.assertEqual([s['id'] for b in batches for s in b],[s['id'] for s in sources])
        with self.assertRaisesRegex(ValueError,'synopsis_single_source_over_limit'):
            synopsis.plan_batches(self.sources('长'*13000))

    def test_sentence_units_cannot_hide_an_unmapped_second_sentence(self):
        sentence='她提到日程。随后讨论晚餐。'
        with self.assertRaisesRegex(ValueError,'synopsis_sentence_unit_required'):
            synopsis.validate_output(self.output(sentence),self.sources(sentence))


class SynopsisFlow(unittest.TestCase):
    def setUp(self):
        self.fixture=h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
        self.cid=self.fixture.cid; self.owner=self.fixture.owner
        with app_module.connect() as db:
            confirmed=importer.confirm_h32_drafts(db,self.fixture.batch,self.cid,
                self.fixture.file['id'],self.fixture.segments(),**self.fixture.arguments(db))
            self.entries=[x['entryId'] for x in confirmed['items']]
            legacy_jobs.init_db(db)
        with app_module.connect() as db:
            processing_schema.migrate(db)

    def tearDown(self):
        self.fixture.doCleanups()

    def create(self, index=0, *, input_cap=4096):
        with app_module.connect() as db:
            return processing.create_synopsis_job(db,self.entries[index],self.cid,self.owner,
                                                  input_cap=input_cap)

    def execute(self, jid, handler=mock_output):
        adapter=MockSynopsisAdapter(handler)
        result=processing.run_synopsis_job(app_module.connect,jid,self.cid,self.owner,adapter)
        return result,adapter

    def test_unconfirmed_draft_rejected_and_single_batch_pending(self):
        with app_module.connect() as db:
            # A persisted source list by itself has no confirmation row.
            source_id=db.execute('SELECT message_id FROM mv2_import_receipts ORDER BY ordinal').fetchone()[0]
            unconfirmed=metadata.create_draft(db,self.cid,[{'kind':'message','id':str(source_id)}],
                '无确认边界',self.owner,'unconfirmed')
            with self.assertRaisesRegex(ValueError,'boundary_confirmation_required'):
                processing.create_synopsis_job(db,unconfirmed['entryId'],self.cid,self.owner)
        jid=self.create()
        self.assertEqual(jid,self.create())
        result,adapter=self.execute(jid)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(adapter.calls),1)
        with app_module.connect() as db:
            detail=metadata.read_draft(db,self.entries[0],self.cid)
            self.assertEqual([s['id'] for s in adapter.calls[0]['input']['sources']],
                             [r['source_id'] for r in detail['sourceRefs']])
            self.assertEqual(detail['fields']['synopsis']['lifecycleStatus'],'draft')
            self.assertFalse(detail['qualifications']['formalContext'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks WHERE field_id=?',
                (detail['fields']['synopsis']['fieldId'],)).fetchone()[0],0)
        repeat,other=self.execute(jid)
        self.assertTrue(repeat['reused']); self.assertEqual(other.calls,[])

    def test_three_or_more_batches_and_final_have_distinct_dispatches(self):
        jid=self.create(6,input_cap=2200)
        result,adapter=self.execute(jid)
        self.assertEqual(result['status'],'completed',result)
        self.assertGreaterEqual(len(adapter.calls),4)
        self.assertEqual(adapter.calls[-1]['phase'],'final')
        observed=[s['id'] for call in adapter.calls if call['phase']=='batch'
                  for s in call['input']['sources']]
        with app_module.connect() as db:
            refs=metadata.read_draft(db,self.entries[6],self.cid)['sourceRefs']
            self.assertEqual(observed,[r['source_id'] for r in refs])
            chunks=db.execute('SELECT request_id,dispatch_attempt_id,state,used_input_tokens FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',(jid,)).fetchall()
            self.assertEqual(len(chunks),len(adapter.calls))
            self.assertEqual(len({r['request_id'] for r in chunks}),len(chunks))
            self.assertTrue(all(r['state']=='completed' and r['used_input_tokens']==20 for r in chunks))

    def test_unknown_keeps_reservation_and_never_resends(self):
        jid=self.create()
        def timeout(_): raise TimeoutError('synthetic_unknown')
        result,adapter=self.execute(jid,timeout)
        self.assertEqual(result['status'],'unknown')
        with app_module.connect() as db:
            parent=db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()
            child=db.execute('SELECT state,reserved_tokens,used_input_tokens FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()
            self.assertEqual(parent[0],'unknown'); self.assertEqual(child[0],'unknown')
            self.assertGreater(child[1],0); self.assertIsNone(child[2])
            with self.assertRaises(ValueError): processing.create_synopsis_job(db,self.entries[0],self.cid,self.owner)
        with self.assertRaises(ValueError): self.execute(jid)
        self.assertEqual(len(adapter.calls),1)

    def test_cancel_after_dispatch_settles_without_field(self):
        jid=self.create()
        def cancel_then_return(request):
            with app_module.connect() as db:
                self.assertEqual(processing.cancel(db,jid,self.cid),'unknown')
            return mock_output(request)
        result,adapter=self.execute(jid,cancel_then_return)
        self.assertEqual(result['status'],'cancelled')
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],40)
        self.assertEqual(len(adapter.calls),1)

    def test_source_change_rejects_business_but_settles_usage(self):
        jid=self.create()
        def change_then_return(request):
            with app_module.connect() as db:
                mid=int(request['input']['sources'][0]['id'])
                db.execute('UPDATE messages SET content=? WHERE id=?',('合成内容变化',mid))
            return mock_output(request)
        result,_=self.execute(jid,change_then_return)
        self.assertIn(result['status'],('failed','rejected'))
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],40)

    def test_unrelated_title_revision_does_not_invalidate_synopsis_input(self):
        jid=self.create()
        def title_then_return(request):
            with app_module.connect() as db:
                detail=metadata.read_draft(db,self.entries[0],self.cid)
                metadata.revise(db,self.entries[0],self.cid,detail['versionId'],
                                {'title':'合成新标题'},self.owner)
            return mock_output(request)
        result,_=self.execute(jid,title_then_return)
        self.assertEqual(result['status'],'completed',result)
        with app_module.connect() as db:
            detail=metadata.read_draft(db,self.entries[0],self.cid)
            self.assertEqual(detail['fields']['title']['value'],'合成新标题')
            self.assertEqual(detail['fields']['synopsis']['lifecycleStatus'],'draft')

    def test_late_settled_result_recovers_without_second_dispatch(self):
        jid=self.create()
        def expire_then_return(request):
            with app_module.connect() as db:
                db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?',(jid,))
            return mock_output(request)
        first,adapter=self.execute(jid,expire_then_return)
        self.assertEqual(first['status'],'paused',first)
        self.assertEqual(len(adapter.calls),1)
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],0)
            row=db.execute('SELECT read_set_json FROM mv3_jobs WHERE id=?',(jid,)).fetchone()
            import json
            self.assertEqual(processing.resume(db,jid,self.cid,json.loads(row[0])),'queued')
        result,next_adapter=self.execute(jid)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(next_adapter.calls,[])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],40)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],1)

    def test_manual_field_and_cancel_before_dispatch(self):
        with app_module.connect() as db:
            detail=metadata.read_draft(db,self.entries[0],self.cid)
            metadata.revise(db,self.entries[0],self.cid,detail['versionId'],
                            {'synopsis':None},self.owner)
            with self.assertRaisesRegex(ValueError,'synopsis_field_protected'):
                processing.create_synopsis_job(db,self.entries[0],self.cid,self.owner)
        jid=self.create(1)
        with app_module.connect() as db:
            self.assertEqual(processing.cancel_synopsis_job(db,jid,self.cid,self.owner),'cancelled')
        with self.assertRaisesRegex(ValueError,'synopsis_job_not_claimable'):
            self.execute(jid)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],0)

    def test_boundary_change_invalidates_queued_job_without_dispatch(self):
        jid=self.create()
        with app_module.connect() as db:
            db.execute('UPDATE mv2_import_segments SET end_ordinal=end_ordinal+1 WHERE entry_id=?',
                       (self.entries[0],))
        with self.assertRaisesRegex(ValueError,'synopsis_input_changed'):
            self.execute(jid)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],0)

    def test_old_synopsis_job_cannot_overwrite_full_entry_candidate(self):
        with app_module.connect() as db:
            old=legacy_jobs.enqueue(db,self.entries[0],self.cid,self.owner,'SYNOPSIS')
        jid=self.create()
        result,_=self.execute(jid)
        self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            current=metadata.read_draft(db,self.entries[0],self.cid)
            saved=current['fields']['synopsis']['token']
            from backend.app import memory_v2_jobs as jobs
            with self.assertRaises(ValueError):
                jobs.claim(db,old['id'],self.cid)
            self.assertEqual(metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['token'],saved)

    def test_unknown_second_batch_blocks_remaining_dispatch_and_field(self):
        jid=self.create(6,input_cap=2200)
        def second_timeout(request):
            if request['ordinal']==1:
                raise TimeoutError('synthetic_timeout')
            return mock_output(request)
        result,adapter=self.execute(jid,second_timeout)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual([c['ordinal'] for c in adapter.calls],[0,1])
        with app_module.connect() as db:
            states=[r[0] for r in db.execute('SELECT state FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',(jid,))]
            self.assertEqual(states[:2],['completed','unknown'])
            self.assertTrue(all(s=='prepared' for s in states[2:]))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],0)

    def test_reliable_invalid_output_settles_cost_without_candidate(self):
        jid=self.create()
        def invalid_output(request):
            item=mock_output(request)[0]
            item['units'][0]['evidence'][0]['speaker']='assistant' if item['units'][0]['evidence'][0]['speaker']=='user' else 'user'
            return item,20,20
        result,_=self.execute(jid,invalid_output)
        self.assertIn(result['status'],('failed','rejected'))
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],40)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],0)

    def test_manual_revision_during_dispatch_preserves_usage_and_manual_field(self):
        jid=self.create()
        def manual_during_request(request):
            with app_module.connect() as db:
                detail=metadata.read_draft(db,self.entries[0],self.cid)
                metadata.revise(db,self.entries[0],self.cid,detail['versionId'],
                                {'synopsis':'Owner 手工梗概'},self.owner)
            return mock_output(request)
        result,_=self.execute(jid,manual_during_request)
        self.assertIn(result['status'],('failed','rejected'))
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],40)
            self.assertEqual(metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['value'],'Owner 手工梗概')

    def test_field_write_failure_keeps_settlement_and_rolls_back_candidate(self):
        import sqlite3
        from unittest.mock import patch
        jid=self.create()
        with patch.object(metadata,'revise',side_effect=sqlite3.IntegrityError('synthetic_field_failure')):
            result,_=self.execute(jid)
        self.assertEqual(result['status'],'failed',result)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT used_cost_units FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()[0],40)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_entry_fields WHERE name='synopsis'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',(jid,)).fetchone()[0],0)

    def test_boundary_change_after_candidate_stales_it_without_deleting_history(self):
        jid=self.create()
        self.assertEqual(self.execute(jid)[0]['status'],'completed')
        with app_module.connect() as db:
            old=metadata.read_draft(db,self.entries[0],self.cid)
            token=old['fields']['synopsis']['token']
            db.execute('UPDATE mv2_import_segments SET end_ordinal=end_ordinal+1 WHERE entry_id=?',
                       (self.entries[0],))
            current=metadata.read_draft(db,self.entries[0],self.cid)
            self.assertEqual(current['fields']['synopsis']['lifecycleStatus'],'stale')
            self.assertIsNone(current['fields']['synopsis']['value'])
            self.assertEqual(current['fields']['synopsis']['token'],token)

    def test_validator_upgrade_stales_candidate_and_blocks_old_inflight_job(self):
        from unittest.mock import patch
        pending=self.create(0)
        completed=self.create(1)
        self.assertEqual(self.execute(completed)[0]['status'],'completed')
        with patch.object(synopsis,'VALIDATOR','synopsis-evidence-v2'):
            with app_module.connect() as db:
                detail=metadata.read_draft(db,self.entries[1],self.cid)
                self.assertEqual(detail['fields']['synopsis']['lifecycleStatus'],'stale')
            with self.assertRaisesRegex(ValueError,'synopsis_input_changed'):
                self.execute(pending)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(pending,)).fetchone()[0],0)

    def test_receipt_change_after_candidate_rejects_read_without_old_value(self):
        jid=self.create()
        self.assertEqual(self.execute(jid)[0]['status'],'completed')
        with app_module.connect() as db:
            previous=metadata.read_draft(db,self.entries[0],self.cid)
            self.assertEqual(previous['fields']['synopsis']['lifecycleStatus'],'draft')
            first=previous['sourceRefs'][0]['source_id']
            db.execute('UPDATE mv2_import_receipts SET content_sha256=? WHERE message_id=?',
                       ('0'*64,int(first)))
            with self.assertRaisesRegex(ValueError,'source_missing_or_stale'):
                metadata.read_draft(db,self.entries[0],self.cid)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name=?',
                                        ('synopsis',)).fetchone()[0],1)


if __name__ == '__main__':
    unittest.main()
