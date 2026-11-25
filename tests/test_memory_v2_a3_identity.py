"""Synthetic identity boundaries; no provider or private data."""
import dataclasses
import sqlite3
import unittest
import uuid
from backend.app import memory_v2_jobs as jobs, memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as schema
from test_memory_v2_a3_recovery import database


class Identity(unittest.TestCase):
    def setUp(self):
        self.db=database(); schema.migrate(self.db)
        self.db.execute('INSERT INTO characters VALUES(2,1)')
        self.db.execute('ALTER TABLE mv3_model_chunks ADD COLUMN trial_id TEXT')
        self.db.execute('ALTER TABLE mv3_model_chunks ADD COLUMN reserved_usd_micros INTEGER')
        self.db.execute('CREATE TABLE mv3_trial_budgets(id TEXT PRIMARY KEY,character_id INTEGER,reserved_microusd INTEGER,spent_estimate_microusd INTEGER)')
        self.db.execute("INSERT INTO mv3_trial_budgets VALUES('trial',1,1000,0)")
        self.requests={}; self.proofs={}
        for jid in ('one','two'):
            self.db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES(?,1,'NARRATIVE',?,'{}','running','synthetic')",(jid,jid))
            for ordinal in (0,1):
                request=str(uuid.uuid4()); self.requests[jid,ordinal]=request
                self.db.execute("INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens,reserved_cost_units,trial_id,reserved_usd_micros) VALUES(?,?,?,?,'{}','prepared',100,100,'trial',250)",(jid,ordinal,request,jid))
                self.proofs[jid,ordinal]=jobs.persist_dispatch_identity(self.db,jid,1,ordinal)
        self.db.commit()

    def tearDown(self): self.db.close()

    def snapshot(self):
        tables=[r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}

    def settle(self,proof=None,request=None,**kwargs):
        return jobs.settle_dispatch(self.db,'one',1,0,request or self.requests['one',0],proof,
            state='completed',used_input=10,used_output=20,used_cost=30,result_hash='synthetic',**kwargs)

    def test_rejected_identities_preserve_every_row_and_effective_budgets(self):
        valid=self.proofs['one',0]
        cases=[None,'',' ',valid.attempt_id,'not-uuid',1,dataclasses.replace(valid),
               self.proofs['one',1],self.proofs['two',0]]
        for money in (False,True):
            for proof in cases:
                with self.subTest(money=money,proof_type=type(proof).__name__):
                    before=self.snapshot()
                    with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                        self.settle(proof,**({'trial_id':'trial','usd_estimate':30} if money else {}))
                    self.assertEqual(before,self.snapshot())
            for request in (None,'',' ',str(uuid.uuid4()),self.requests['one',1]):
                before=self.snapshot()
                with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                    jobs.settle_dispatch(self.db,'one',1,0,request,valid,state='unknown')
                self.assertEqual(before,self.snapshot())
        self.assertEqual(self.db.execute("SELECT SUM(reserved_cost_units) FROM mv3_model_chunks WHERE state IN ('prepared','possibly_dispatched','unknown')").fetchone()[0],400)
        self.assertEqual(self.db.execute('SELECT reserved_microusd FROM mv3_trial_budgets').fetchone()[0],1000)

    def test_missing_invalid_or_mismatched_persisted_identity(self):
        valid=self.proofs['one',0]
        for value in (None,'',' ','not-uuid',str(uuid.uuid4())):
            self.db.execute('UPDATE mv3_model_chunks SET dispatch_attempt_id=? WHERE job_id=? AND ordinal=0',(value,'one'))
            for supplied in (None,value,valid):
                before=self.snapshot()
                with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                    self.settle(supplied,trial_id='trial',usd_estimate=30)
                self.assertEqual(before,self.snapshot())

    def test_valid_late_result_replay_conflict_and_dollars(self):
        self.db.execute("UPDATE mv3_jobs SET lease_until=0,cancel_intent=1 WHERE id='one'")
        proof=self.proofs['one',0]
        self.assertEqual(self.settle(proof,trial_id='trial',usd_estimate=30),'unknown')
        before=self.snapshot()
        self.assertEqual(self.settle(proof,trial_id='trial',usd_estimate=30),'unknown')
        self.assertEqual(before,self.snapshot())
        with self.assertRaisesRegex(ValueError,'conflicting_settlement'):
            self.settle(proof,trial_id='trial',usd_estimate=31)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.db.execute('SELECT reserved_microusd,spent_estimate_microusd FROM mv3_trial_budgets').fetchone()[:],(750,30))

    def test_usage_missing_and_role_mismatch_no_effect(self):
        proof=self.proofs['one',0]; before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'usage_missing'):
            jobs.settle_dispatch(self.db,'one',1,0,proof.request_id,proof,state='completed')
        self.assertEqual(before,self.snapshot())
        with self.assertRaisesRegex(ValueError,'job_not_accessible'):
            jobs.settle_dispatch(self.db,'one',2,0,proof.request_id,proof,state='unknown')
        self.assertEqual(before,self.snapshot())

    def test_processing_reconcile_never_fills_identity_from_database(self):
        db=database()
        try:
            schema.migrate(db)
            jid=processing.create(db,1,'entry','e',['title'],{'synthetic':1},rule_version='v',input_version='i')
            processing.prepare_chunks(db,jid,1,[{'reservedTokens':100}])
            request=processing.dispatch(db,jid,1,0)
            before=db.total_changes
            with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                processing.reconcile(db,jid,1,0,request['requestId'],evidence={'kind':'mock_completed','requestId':request['requestId']},used_input_tokens=1,used_output_tokens=1)
            self.assertEqual(before,db.total_changes)
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs').fetchone()[0],'unknown')
            self.assertEqual(processing.reconcile(db,jid,1,0,request['requestId'],dispatch_proof=request['dispatchProof'],evidence={'kind':'mock_completed','requestId':request['requestId']},used_input_tokens=1,used_output_tokens=1,current_read_set={'synthetic':1}),'paused')
        finally: db.close()

    def test_unmigrated_process_proof_does_not_allow_reconstruction(self):
        db=database()
        try:
            rid=str(uuid.uuid4())
            db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES('old',1,'NARRATIVE','h','{}','running','synthetic')")
            db.execute("INSERT INTO mv3_model_chunks(job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens) VALUES('old',0,?,'h','{}','prepared',100)",(rid,))
            proof=jobs.persist_dispatch_identity(db,'old',1,0)
            with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                jobs.settle_dispatch(db,'old',1,0,rid,proof.attempt_id,state='completed',used_input=1,used_output=1)
            self.assertEqual(jobs.settle_dispatch(db,'old',1,0,rid,proof,state='unknown'),'unknown')
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs').fetchone()[0],'paused')
            self.assertEqual(jobs.settle_dispatch(db,'old',1,0,rid,proof,state='completed',used_input=1,used_output=1),'running')
            before=db.total_changes
            self.assertEqual(jobs.settle_dispatch(db,'old',1,0,rid,proof,state='completed',used_input=1,used_output=1),'running')
            self.assertEqual(before,db.total_changes)
        finally: db.close()


class ProviderIdentity(unittest.TestCase):
    def test_mocktransport_unknown_rejects_id_only_without_ledger_or_detail_changes(self):
        from unittest.mock import patch
        import test_memory_v2_m36 as f
        case=f.M36Trial('test_trial_schema_preflight_and_two_variants')
        case.setUp(); self.addCleanup(case.doCleanups)
        with f.app_module.connect() as db: schema.migrate(db)
        fake=f.FakeProvider(status='timeout')
        tid,variants,fake,_=case.prepare(fake)
        jid=variants[f.responses.PLUGIN_VARIANT]
        captured=[]; original=jobs.begin_dispatch
        def capture(*args,**kwargs):
            proof=original(*args,**kwargs); captured.append(proof); return proof
        with patch.object(jobs,'begin_dispatch',capture):
            with self.assertRaisesRegex(ValueError,'provider_result_unknown'):
                f.trial.dispatch_trial_chunk(f.app_module.connect,tid,jid,case.cid,f.adapter(fake))
        calls=len(fake.calls)
        with f.app_module.connect() as db:
            def snapshot():
                return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')]
                        for t in ('mv3_jobs','mv3_model_chunks','mv3_trial_budgets','mv3_versions','mv3_a3_migration_holds')}
            before=snapshot(); proof=captured[0]
            for supplied in (None,'',proof.attempt_id,dataclasses.replace(proof)):
                with self.assertRaisesRegex(ValueError,'dispatch_identity_mismatch'):
                    jobs.settle_dispatch(db,jid,case.cid,0,proof.request_id,supplied,
                        state='completed',used_input=500,used_output=100,result_hash='unproven',trial_id=tid,usd_estimate=2000)
                self.assertEqual(before,snapshot())
            self.assertEqual(len(fake.calls),calls)
            self.assertGreater(db.execute('SELECT reserved_microusd FROM mv3_trial_budgets WHERE id=?',(tid,)).fetchone()[0],0)
