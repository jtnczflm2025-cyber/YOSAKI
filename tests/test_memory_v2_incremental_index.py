"""Scoped A3 document/query embeddings, synthetic SQLite + MockTransport."""
import json
import uuid
import unittest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_memory_v2_entries import EntryBase, app_module, entries
from backend.app import memory_v2_chronicle_schema as schema
from backend.app import memory_v2_chronicle_indexing as index
from backend.app import memory_v2_chronicle_embedding as embedding
from backend.app import memory_v2_chronicle_vectors as vectors
from backend.app import memory_v2_chronicle_workspace as workspace
from backend.app import memory_v2_processing as processing

PRICE=dict(provider='openai_embeddings',model=embedding.MODEL,mode='standard',
    checkedAt='synthetic-local',sourceUrl='synthetic-local-price',inputUsdPerMillion='0.02',outputUsdPerMillion=0)

class IncrementalIndex(unittest.TestCase):
    def setUp(self):
        self.f=EntryBase();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cid=self.f.cid;self.ids=[];self.versions=[];self.calls=[];self.mode='ok';self.on_send=None
        with app_module.connect() as db:
            schema.install(db)
            common=self.f.narrative(db)
            for n in range(3):
                e,v,narrative=self.f.new_entry(db,title=f'合成索引{n}',synopsis='需许可且测试通过后才讨论是否安装；目前暂缓。'*80,narrative=common)
                self.f.approve_entry(db,v);self.ids.append(e['id']);self.versions.append((v,narrative))
        self.adapter=embedding.Adapter('test',transport=httpx.MockTransport(self.wire));self.addCleanup(self.adapter.close)
    def wire(self,request):
        payload=json.loads(request.content);self.calls.append(payload)
        if self.on_send:self.on_send()
        if self.mode=='disconnect':raise httpx.RemoteProtocolError('synthetic disconnect')
        used=min(100,len(''.join(payload['input']).encode('utf-8')))
        return httpx.Response(200,json={'model':embedding.MODEL,'usage':{'prompt_tokens':used,'total_tokens':used} if self.mode!='unknown' else {},
            'data':[{'index':i,'embedding':[1.]+[0.]*1535} for i in range(len(payload['input']))]})
    def plan(self,ids=None):
        with app_module.connect() as db:return index.plan(db,self.cid,1,ids or self.ids)
    def create(self,ids=None,approval=None):
        ids=ids or self.ids;p=self.plan(ids);a=approval or str(uuid.uuid4())
        with app_module.connect() as db:
            return index.create(db,self.cid,1,ids,p['planHash'],a,PRICE,p['maxMicrousd'] or 1),p,a
    def run_job(self,jid):return index.execute(app_module.connect,jid,self.cid,1,self.adapter)
    def test_cross_batch_reuse_restart_and_incremental(self):
        group,p,a=self.create();self.assertEqual(len(group['jobs']),3)
        self.assertTrue(all(b['inputBytes']<=8192 for b in p['batches']))
        self.assertEqual(self.run_job(group['jobs'][0])['status'],'completed')
        # Recreate coordinator/approval after first commit; no new task or call.
        with app_module.connect() as db:
            retry=index.create(db,self.cid,1,self.ids,p['planHash'],a,PRICE,p['maxMicrousd'])
        self.assertEqual(retry['jobs'],group['jobs'])
        self.assertTrue(self.run_job(group['jobs'][0])['reused'])
        for jid in group['jobs'][1:]:self.assertEqual(self.run_job(jid)['status'],'completed')
        self.assertEqual(len(self.calls),3);self.assertEqual(self.plan()['batches'],[])
        eid=self.ids[0];old,narr=self.versions[0]
        with app_module.connect() as db:
            v=entries.add_version(db,eid,self.f.refs(),'合成索引修改',[],[], '条件已更正，仍须审核。',narr['id'],1)
            self.f.approve_entry(db,v)
            self.assertNotIn(eid,[x[0] for x in vectors._qualified_vectors(db,self.cid,1,index.MODEL)])
        new,changed,_=self.create()
        self.assertEqual(len(new['jobs']),1)
        self.assertEqual([m['entryId'] for m in changed['batches'][0]['members']],[eid])
        self.assertEqual(self.run_job(new['jobs'][0])['status'],'completed')
        self.assertEqual(len(self.calls),4)
    def test_unknown_never_resends_and_other_batches_no_reservation(self):
        group,p,a=self.create();self.mode='unknown';jid=group['jobs'][0]
        self.assertEqual(self.run_job(jid)['status'],'unknown')
        self.assertEqual(self.run_job(jid)['status'],'unknown');self.assertEqual(len(self.calls),1)
        with app_module.connect() as db:
            chunk=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()
            self.assertTrue(chunk['dispatch_attempt_id']);self.assertIsNone(chunk['settlement_hash'])
            self.assertGreater(db.execute('SELECT reserved_microusd FROM mv3_trial_budgets WHERE id=?',(chunk['trial_id'],)).fetchone()[0],0)
            for other in group['jobs'][1:]:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',(other,)).fetchone()[0],0)
            with self.assertRaisesRegex(ValueError,'plan_expired|already_planned'):
                index.create(db,self.cid,1,self.ids,p['planHash'],str(uuid.uuid4()),PRICE,p['maxMicrousd'])
        self.assertEqual(len(self.calls),1)
    def test_revoked_during_send_settles_without_vectors(self):
        group,_,_=self.create([self.ids[0]])
        def revoke():
            with app_module.connect() as db:db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
        self.on_send=revoke
        self.assertEqual(self.run_job(group['jobs'][0])['status'],'failed')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_vectors').fetchone()[0],0)
            b=db.execute('SELECT * FROM mv3_trial_budgets').fetchone()
            self.assertEqual((b['spent_estimate_microusd'],b['reserved_microusd']),(2,0))
    def test_source_change_rejects_current_batch_keeps_previous(self):
        group,_,_=self.create();self.run_job(group['jobs'][0])
        def change():
            with app_module.connect() as db:db.execute('UPDATE messages SET content=? WHERE id=?',('来源变化',self.f.user))
        self.on_send=change
        self.assertEqual(self.run_job(group['jobs'][1])['status'],'failed')
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_vectors').fetchone()[0],1)
            self.assertEqual(list(vectors._qualified_vectors(db,self.cid,1,index.MODEL)),[])
    def test_output_write_failure_keeps_known_cost_no_resend(self):
        group,_,_=self.create([self.ids[0]]);jid=group['jobs'][0]
        with app_module.connect() as db:
            db.execute("CREATE TRIGGER synthetic_fail BEFORE UPDATE OF output_json ON mv3_model_chunks WHEN NEW.output_json IS NOT NULL BEGIN SELECT RAISE(FAIL,'synthetic'); END")
        with self.assertRaises(Exception):self.run_job(jid)
        self.assertEqual(self.run_job(jid)['status'],'failed')
        with app_module.connect() as db:
            b=db.execute('SELECT * FROM mv3_trial_budgets').fetchone()
            self.assertEqual((b['spent_estimate_microusd'],b['reserved_microusd']),(2,0))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_vectors').fetchone()[0],0)
        self.assertEqual(len(self.calls),1)
    def test_query_is_independent_and_reused_across_corpus_updates(self):
        with app_module.connect() as db:
            jid=index.create_query(db,self.cid,1,'何时才能讨论装设？',PRICE,164)
        self.assertEqual(self.run_job(jid)['status'],'completed')
        self.assertEqual(self.calls[0]['input'],['何时才能讨论装设？'])
        with app_module.connect() as db:
            self.assertEqual(len(embedding.cached_query_vector(db,self.cid,1,'何时才能讨论装设？')),1536)
            db.execute('UPDATE messages SET content=? WHERE id=?',('更正来源',self.f.user))
        with app_module.connect() as db:
            self.assertEqual(len(embedding.cached_query_vector(db,self.cid,1,'何时才能讨论装设？')),1536)
            self.assertEqual(index.create_query(db,self.cid,1,'何时才能讨论装设？',PRICE,164),jid)
        self.run_job(jid);self.assertEqual(len(self.calls),1)
    def test_preview_zero_writes_wrong_identity_and_capacity_rejected(self):
        with app_module.connect() as db:
            before=db.total_changes;index.plan(db,self.cid,1,self.ids)
            self.assertEqual(db.total_changes,before)
            for cid,owner in [(self.cid,999),(self.cid+99,1)]:
                with self.assertRaises(ValueError):index.plan(db,cid,owner,self.ids)
            e,v,n=self.f.new_entry(db,title='超限',synopsis='大'*2990,narrative=self.versions[0][1]);self.f.approve_entry(db,v)
            with self.assertRaisesRegex(ValueError,'single_document_capacity'):index.plan(db,self.cid,1,[e['id']])
    def test_actual_http_scope_and_price_binding(self):
        api=FastAPI();auth={'id':1}
        workspace.install(api,app_module.connect,lambda _:auth,
            embedding_factory=lambda _:(embedding.Adapter('test',transport=httpx.MockTransport(self.wire)),PRICE))
        client=TestClient(api);base='/api/memory-v2/workspace/vector-index'
        body={'characterId':self.cid,'entryIds':self.ids}
        p=client.post(base+'/preview',json=body).json()
        r=client.post(base+'/approve',json={**body,'modelId':1,'planHash':p['planHash'],
            'approvalId':str(uuid.uuid4()),'approveMaxMicrousd':p['maxMicrousd']})
        self.assertEqual(r.status_code,200,r.text)
        jid=r.json()['jobs'][0]
        r=client.post(base+'/jobs/'+jid+'/execute',params={'characterId':self.cid})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'completed')
        auth=None
        self.assertEqual(client.post(base+'/preview',json=body).status_code,401)

    def test_dispatch_interruption_keeps_identity_without_resend(self):
        group,_,_=self.create([self.ids[0]]);jid=group['jobs'][0]
        def interrupt():raise KeyboardInterrupt('synthetic process interruption after dispatch commit')
        self.on_send=interrupt
        with self.assertRaises(KeyboardInterrupt):self.run_job(jid)
        self.on_send=None
        self.assertEqual(self.run_job(jid)['status'],'unknown');self.assertEqual(len(self.calls),1)
        with app_module.connect() as db:
            row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(jid,)).fetchone()
            self.assertEqual(row['state'],'possibly_dispatched');self.assertTrue(row['dispatch_attempt_id'])
            self.assertIsNone(row['used_input_tokens'])
            self.assertGreater(db.execute('SELECT reserved_microusd FROM mv3_trial_budgets WHERE id=?',(row['trial_id'],)).fetchone()[0],0)

    def test_concurrent_execute_only_original_sender_and_cancel_intent(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        entered,finish=Event(),Event()
        group,_,_=self.create([self.ids[0]]);jid=group['jobs'][0]
        def wait():entered.set();self.assertTrue(finish.wait(10))
        self.on_send=wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.run_job,jid)
            self.assertTrue(entered.wait(10))
            self.assertEqual(self.run_job(jid)['status'],'unknown')
            with app_module.connect() as db:self.assertEqual(index.cancel(db,jid,self.cid,1)['status'],'unknown')
            finish.set();self.assertEqual(future.result()['status'],'cancelled')
        self.assertEqual(len(self.calls),1)
        with app_module.connect() as db:
            b=db.execute('SELECT * FROM mv3_trial_budgets').fetchone()
            self.assertEqual((b['spent_estimate_microusd'],b['reserved_microusd']),(2,0))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_vectors').fetchone()[0],0)

    def test_expired_plan_rejected_and_unsent_cancel_has_zero_fee(self):
        group,_,_=self.create([self.ids[0]]);jid=group['jobs'][0]
        with app_module.connect() as db:index.cancel(db,jid,self.cid,1)
        self.assertEqual(self.run_job(jid)['status'],'cancelled');self.assertEqual(self.calls,[])
        with app_module.connect() as db:
            b=db.execute('SELECT * FROM mv3_trial_budgets').fetchone()
            self.assertEqual((b['dispatched_requests'],b['reserved_microusd'],b['spent_estimate_microusd']),(0,0,0))

    def test_group_business_driver_stops_unknown_without_repeating_completed_batch(self):
        api=FastAPI()
        workspace.install(api,app_module.connect,lambda _:{'id':1},
            embedding_factory=lambda _:(embedding.Adapter('test',transport=httpx.MockTransport(self.wire)),PRICE))
        client=TestClient(api);base='/api/memory-v2/workspace/vector-index'
        body={'characterId':self.cid,'entryIds':self.ids};p=client.post(base+'/preview',json=body).json();a=str(uuid.uuid4())
        response=client.post(base+'/approve',json={**body,'planHash':p['planHash'],'approvalId':a,
                'modelId':1,'approveMaxMicrousd':p['maxMicrousd']})
        self.assertEqual(response.status_code,200,response.text)
        def uncertain_second():
            if len(self.calls)==2:self.mode='unknown'
        self.on_send=uncertain_second
        path=base+'/approvals/'+a+'/execute'
        r=client.post(path,params={'characterId':self.cid})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'stopped')
        self.assertEqual([x['status'] for x in r.json()['results']],['completed','unknown'])
        self.assertEqual(len(self.calls),2)
        r=client.post(path,params={'characterId':self.cid});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len(self.calls),2)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_chronicle_vectors').fetchone()[0],1)
