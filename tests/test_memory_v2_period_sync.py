import json
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_memory_v2_batch_review as review
from test_memory_v2_batch_review import app_module, metadata
from backend.app import memory_v2_period_sync as sync
from backend.app import memory_v2_m36_trial as trials, memory_v2_processing_schema as schema


class CalendarSync(unittest.TestCase):
    def setUp(self):
        self.case=review.BatchReview('test_snapshot_authority_and_publish')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.prepare()
        self.cid=self.case.cid
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'grant.json'
        self.grant={'version':sync.KIND,'ownerId':1,'characterId':self.cid,'modelId':6,'enabled':True}
        self.when=datetime(2026,1,15,tzinfo=timezone.utc)
        self.grant.update(policy=sync.policy.POLICY,scope=sync.policy.SCOPE,approvalId='synthetic',
            startsAt='2025-01-01T00:00:00+00:00',endsAt='2032-01-01T00:00:00+00:00',
            perTask=400000,perDay=2000000,total=5000000,futurePublished=True,recall=True)
        self.path.write_text(json.dumps(self.grant))
        with app_module.connect() as db:
            trials.init_db(db);schema.migrate(db)
        self.adapter=Mock()
        self.adapter.count_input.return_value=100
        self.response={'status':'completed','serviceTier':'default','modelId':sync.MODEL,'text':'这一时期的合成回顾。','usage':{'input_tokens':100,'output_tokens':36}}
        self.adapter.generate.return_value=self.response
        self.service=sync.PeriodSync(app_module.connect,self.path,lambda *_:self.adapter,lambda day:{
            'provider':'openai_responses','model':sync.MODEL,'mode':'standard','checkedAt':day,
            'inputUsdPerMillion':'2.00','outputUsdPerMillion':'10.00','sourceUrl':'fixture'},clock=lambda:self.when)

    def publish(self):
        self.assertEqual(self.case.submit(self.case.card()).json()['status'],'published')

    def test_metered_existing_scope_reuses_work_after_old_expiry(self):
        self.publish()
        self.grant.update(endsAt=self.when.isoformat(), perTask=1, perDay=1, total=1)
        self.path.write_text(json.dumps(self.grant))
        with patch.dict("os.environ", {"YOSAKI_RUN_MODE": "development", "YOSAKI_OWNER_BILLING": "metered"}):
            grant = self.service.grant()
            self.assertTrue(self.service.valid_grant(grant))
            self.service.refresh()
            generated = self.adapter.generate.call_count
            self.assertGreater(generated, 0)
            self.service.refresh()
            self.assertEqual(self.adapter.generate.call_count, generated)
            self.assertEqual(self.grant, json.loads(self.path.read_text()))
            self.grant.update(revoked=True)
            self.path.write_text(json.dumps(self.grant))
            self.assertFalse(self.service.valid_grant(self.service.grant()))

    def test_daily_price_refresh_and_task_cap(self):
        def tariff(input_rate, read, write, output):
            page = Mock()
            page.text = ('### Text tokens\n\n| Metric | Price | Unit |\n| --- | ---: | --- |\n'
                         f'| Input | ${input_rate} | 1M tokens |\n| Cached input | ${read} | 1M tokens |\n'
                         f'| Cache writes | ${write} | 1M tokens |\n| Output | ${output} | 1M tokens |\n'
                         '- API prompts with more than 272K input tokens are priced separately.\n## Endpoints')
            return page
        sync.official_price.cache_clear()
        self.addCleanup(sync.official_price.cache_clear)
        self.publish()
        self.service.pricing = sync.official_price
        with patch.dict('os.environ', {'YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE':'2000-01-01'}), patch.object(
                sync.httpx, 'get', side_effect=[tariff(2,'0.1',2.5,10), tariff(4,'0.20',5,100),
                                                tariff(4,'0.25',5,10)]) as fetch:
            self.assertEqual(sync.official_price('2026-01-14')['outputUsdPerMillion'],'10')
            self.assertEqual(sync.official_price('2026-01-14')['checkedAt'],'2026-01-14')
            self.service.refresh()
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(self.adapter.count_input.call_count,3)
            self.adapter.generate.assert_not_called()
            self.assertEqual({p['reason'] for p in self.service.view(self.cid,1)['periods']},{'task_budget'})
            with self.assertRaisesRegex(ValueError,'period_official_rates_changed'):
                sync.official_price('2026-12-31')

    def test_publication_republish_and_read(self):
        self.service.refresh();self.adapter.generate.assert_not_called()
        self.publish()
        self.service.refresh();self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,3)
        rows=self.service.view(self.cid,1)['periods']
        self.assertEqual({r['level'] for r in rows},{'L1','L2','L3'})
        self.assertTrue(all(r['body']==self.response['text'] and r['ready']==1 for r in rows))
        for call in self.adapter.generate.call_args_list:
            entry=json.loads(call.args[0]['input'][0]['content'])['entries'][0]
            self.assertEqual(set(entry),{'title','synopsis','start','end'})
        with app_module.connect() as db:
            old=metadata.read_draft(db,self.case.eid,self.cid)
            metadata.revise(db,self.case.eid,self.cid,old['versionId'],{'synopsis':'她建议休息，稍后讨论。'},1)
        self.assertFalse(self.service.view(self.cid,1)['periods'])
        self.publish()
        self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,6)
        with app_module.connect() as db:
            self.assertEqual(db.execute('select sum(reserved_microusd) from mv3_trial_budgets').fetchone()[0],0)
            self.assertGreater(db.execute('select sum(spent_estimate_microusd) from mv3_trial_budgets').fetchone()[0],0)

    def test_unknown_does_not_retry_and_does_not_return_body(self):
        self.publish();self.adapter.generate.return_value={}
        self.service.refresh();self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,3)
        self.assertTrue(all(row['status']=='unknown' and not row['body'] for row in self.service.view(self.cid,1)['periods']))
        with app_module.connect() as db:self.assertGreater(db.execute('select sum(reserved_microusd) from mv3_trial_budgets').fetchone()[0],0)

    def test_deselection_before_dispatch(self):
        self.publish()
        def stop(_):
            self.path.write_text(json.dumps({**self.grant,'enabled':False}))
            return 100
        self.adapter.count_input.side_effect=stop
        self.service.refresh()
        self.adapter.generate.assert_not_called()
        with app_module.connect() as db:
            self.assertEqual(db.execute('select sum(dispatched_requests+reserved_microusd) from mv3_trial_budgets').fetchone()[0],0)


    def test_age_clock_reuse_restart_and_annual_recall(self):
        self.publish()
        self.service.refresh()
        self.when=datetime(2026,8,1,tzinfo=timezone.utc)
        self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,4)
        self.when=datetime(2027,2,1,tzinfo=timezone.utc)
        self.service.start()
        self.service.thread and self.service.thread.join(20)
        self.service.stop()
        self.assertEqual(self.adapter.generate.call_count,5)
        self.service.start()
        self.service.thread and self.service.thread.join(20)
        self.service.stop()
        self.assertEqual(self.adapter.generate.call_count,5)
        self.assertTrue(self.service.view(self.cid,1)['check']['nextCheck'])
        with app_module.connect() as db,patch.object(sync.policy,'authority_for',return_value=self.grant):
            result=sync.channel.project(db,self.cid,1,[self.case.eid],now=self.when)
            self.assertEqual(result['blocks'][0]['projectionKind'],'calendar_summary')
            block=result['blocks'][0]
            self.assertEqual((block['entryIds'],block['level']),([self.case.eid],'L5'))
            full=sync.channel.expand(db,self.cid,1,self.case.eid)
            self.assertEqual(full['view'],'NARRATIVE')
            self.assertTrue(full['text'])
            reader=sync.recall_reader(db,self.cid,1,self.when)
            node=sync.projection._tree(db,self.cid,self.when,480)[0]
            self.assertIsNone(reader(node,set()))
        with app_module.connect() as db,patch.object(sync.policy,'authority_for',return_value={**self.grant,'recall':False}):
            fallback=sync.channel.project(db,self.cid,1,[self.case.eid],now=self.when)
            self.assertEqual(fallback['blocks'][0]['projectionKind'],'title_index')

    def test_budget_pause_revoke_and_no_legacy_dispatch(self):
        self.publish()
        self.path.write_text(json.dumps({k:v for k,v in self.grant.items() if k!='policy'}))
        self.service.refresh()
        self.assertFalse(self.service.view(self.cid,1)['enabled'])
        self.adapter.generate.assert_not_called()
        limited={**self.grant,'perTask':100000,'perDay':100000,'total':100000}
        self.path.write_text(json.dumps(limited))
        self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,1)
        self.assertIn('budget_blocked',[r['status'] for r in self.service.view(self.cid,1)['periods']])
        self.service.toggle(self.cid,1,False,revoke=True)
        with self.assertRaisesRegex(ValueError,'bounded_authorization_required'):
            self.service.toggle(self.cid,1,True)
        self.assertFalse(self.service.view(self.cid,1)['recall'])

    def test_cross_year_member_and_subset_never_inherit_full_year(self):
        from backend.app import memory_v2_dialogue_refinement as finite
        self.publish()
        first=self.case.eid
        with app_module.connect() as db:
            ids=[]
            for role,body,day in [('user','我计划读书。','2026-12-31'),('assistant','我也想读。','2027-01-01')]:
                ids.append(db.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',
                    (self.case.fx.conv,role,body,day)).lastrowid)
            self.case.fx._index(db)
            d=metadata.create_draft(db,self.cid,[{'kind':'message','id':str(i)} for i in ids],'跨年阅读',1,'year-fixture')
            second=d['entryId']
            finite.create(db,self.cid,1,second,d['versionId'])
            metadata.revise(db,second,self.cid,d['versionId'],{'synopsis':'她计划读书，我也想读。'},1)
        url=self.case.url
        cards=self.case.client.get(url,params={'characterId':self.cid}).json()['items']
        card=next(c for c in cards if c['entryId']==second)
        self.assertEqual(self.case.client.post(url+'/'+second+'/publish',json=dict(characterId=self.cid,
            expectedVersion=card['version'],previewToken=card['token'])).json()['status'],'published')
        self.when=datetime(2028,3,1,tzinfo=timezone.utc)
        self.service.refresh()
        with app_module.connect() as db,patch.object(sync.policy,'authority_for',return_value=self.grant):
            years=sync.snapshots(db,self.cid,1,self.when)
            self.assertEqual(len(years['2026']),2)
            self.assertNotIn('2027',years)
            partial=sync.channel.project(db,self.cid,1,[first],now=self.when)['blocks']
            self.assertEqual([(b['projectionKind'],b['entryIds']) for b in partial],[('title_index',[first])])
            full=sync.channel.project(db,self.cid,1,[first,second],now=self.when)['blocks']
            self.assertEqual(len(full),1)
            self.assertEqual(set(full[0]['entryIds']),{first,second})
            self.assertEqual(full[0]['projectionKind'],'calendar_summary')

    def test_due_clock_and_no_get_dispatch(self):
        self.publish()
        self.service.refresh()
        self.service.wake=Mock()
        self.service.view(self.cid,1)
        self.service.check_due()
        self.service.wake.assert_not_called()
        self.when+=timedelta(days=400)
        self.service.check_due()
        self.service.wake.assert_called_once()
        self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,5)
        self.service.check_due()
        self.service.wake.assert_called_once()

    def test_cancel_inflight_settles_no_output(self):
        self.publish()
        def cancel(request):
            with app_module.connect() as db:
                jid=db.execute('select id from mv3_jobs order by rowid desc limit 1').fetchone()[0]
                sync.proc.cancel(db,jid,self.cid)
            return self.response
        self.adapter.generate.side_effect=cancel
        self.service.refresh()
        rows=self.service.view(self.cid,1)
        self.assertTrue(all(r['status']=='cancelled' and not r['body'] for r in rows['periods']))
        self.assertGreater(rows['budget']['spent'],0)
        self.assertEqual(rows['budget']['reserved'],0)
        self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,3)

    def test_count_interruption_and_duplicate_restart(self):
        self.publish()
        self.adapter.count_input.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.service.refresh()
        self.adapter.generate.assert_not_called()
        self.service.recover_unsent()
        self.assertEqual(self.service.view(self.cid,1)['budget']['reserved'],0)
        self.adapter.count_input.side_effect=None
        self.service.refresh()
        self.service.refresh()
        self.assertEqual(self.adapter.generate.call_count,3)
        self.assertTrue(all(r['body'] for r in self.service.view(self.cid,1)['periods']))

    def test_date_revision_membership_uses_calendar_year_not_age(self):
        from backend.app import memory_v2_chronicle_dates as dates
        self.publish()
        self.when=datetime(2027,2,1,tzinfo=timezone.utc)
        self.service.refresh()
        with app_module.connect() as db:
            current=metadata.read_draft(db,self.case.eid,self.cid)
            dates.init_db(db)
            dates.save(db,self.cid,1,self.case.eid,current['versionId'],'2026-12-31','核对日期')
        self.publish()
        self.when=datetime(2027,1,2,tzinfo=timezone.utc)
        rows=self.service.view(self.cid,1)['periods']
        self.assertEqual({r['period'] for r in rows},{'2026-W53','2026-12','2026-Q4'})
        self.assertTrue(all(not r['body'] for r in rows))
        self.when=datetime(2028,1,3,tzinfo=timezone.utc)
        self.service.refresh()
        self.assertIn('2026',{r['period'] for r in self.service.view(self.cid,1)['periods'] if r['body']})

    def test_real_batch_publish_endpoint_wakes_only_after_commit(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.app import memory_v2_chronicle_workspace as ws
        from backend.app import memory_v2_metadata_jobs as mj
        with app_module.connect() as db:mj.init_db(db)
        app=FastAPI()
        ws.install(app,app_module.connect,lambda request:{'id':1},calendar_sync=self.service)
        @app.middleware('http')
        async def synthetic_identity(request,call_next):
            request.state.user={'id':1}
            return await call_next(request)
        self.case.client=TestClient(app)
        self.publish()
        t=self.service.thread
        if t:t.join(timeout=20)
        response=self.case.client.get('/api/memory-v2/workspace/calendar-summaries',params={'characterId':self.cid})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(all(r['status']=='completed' for r in response.json()['periods']))
        self.assertEqual(self.adapter.generate.call_count,3)
