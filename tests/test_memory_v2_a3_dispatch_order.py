"""Deterministic dispatch races and independent-process loss of sender capability."""
import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from backend.app import memory_v2_jobs as jobs, memory_v2_processing as processing
from backend.app import memory_v2_processing_schema as schema
from test_memory_v2_a3_recovery import database


def rows(db):
    return {r[0]:[tuple(x) for x in db.execute('SELECT * FROM '+r[0]+' ORDER BY rowid')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mv3_%' ORDER BY name").fetchall()}


class DispatchOrder(unittest.TestCase):
    def test_three_entrypoints_concurrent_success_and_unknown(self):
        import test_memory_v2_model_tasks as m35
        import test_memory_v2_m36 as m36
        import test_memory_v2_metadata as meta
        from backend.app import memory_v2_metadata_jobs as work
        for path in ('m35','m36','metadata'):
            for unknown in (False,True):
                with self.subTest(path=path,unknown=unknown):
                    case=(m35.ModelTasks('test_multi_batch_persistent_identity_and_resume_without_repeat') if path=='m35'
                          else m36.M36Trial('test_trial_schema_preflight_and_two_variants') if path=='m36'
                          else meta.ProgressiveMetadata('test_title_only_order_and_idempotence'))
                    case.setUp()
                    try:
                        app=m35.app_module
                        with app.connect() as db:
                            if path=='metadata': work.init_db(db)
                        with app.connect() as db: schema.migrate(db)
                        entered=threading.Event(); release=threading.Event(); calls=[]; proofs=[]
                        def wait():
                            calls.append(1); entered.set()
                            if not release.wait(15): raise TimeoutError('test synchronization failed')
                        if path=='m35':
                            with app.connect() as db:
                                job=jobs.enqueue_model(db,case.cid,'NARRATIVE',case.refs(),input_cap=2000,output_cap=800,total_token_cap=5600)
                            jid=job['id']
                            def generate(request):
                                wait()
                                if unknown: raise TimeoutError('synthetic')
                                return case.model(request)
                            adapter=m35.adapter.LocalMockAdapter(generate)
                            dispatch=lambda cid: jobs.dispatch_model_chunk(app.connect,jid,cid,case.tok,adapter)
                        elif path=='metadata':
                            with app.connect() as db:
                                draft=case.draft(db); job=work.enqueue(db,draft['entryId'],case.cid,1,'SYNOPSIS',mock_model=True)
                            jid=job['id']; adapter=work.MockMetadataAdapter({'synopsis':'合成内容'})
                            def generate():
                                wait(); return adapter.changes,None if unknown else 20,None if unknown else 20
                            adapter.generate=generate
                            dispatch=lambda cid: work.dispatch_mock(app.connect,jid,cid,adapter)
                        else:
                            tid,variants,fake,_=case.prepare(m36.FakeProvider(usage=not unknown))
                            jid=variants[m36.responses.PLUGIN_VARIANT]; fake.on_generate=wait
                            dispatch=lambda cid: m36.trial.dispatch_trial_chunk(app.connect,tid,jid,cid,m36.adapter(fake))
                        original=jobs.begin_dispatch
                        def capture(*args,**kwargs):
                            proof=original(*args,**kwargs); proofs.append(proof); return proof
                        with patch.object(jobs,'begin_dispatch',capture), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                            future=pool.submit(dispatch,case.cid)
                            try:
                                self.assertTrue(entered.wait(15))
                                with app.connect() as db:
                                    before=rows(db)
                                    self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',(jid,)).fetchone()[0],'unknown')
                                    child=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(jid,)).fetchone()
                                    self.assertEqual(child['state'],'possibly_dispatched')
                                    self.assertEqual(child['dispatch_attempt_id'],proofs[0].attempt_id)
                                error=None
                                try: dispatch(case.cid)
                                except ValueError as exc: error=str(exc)
                                with app.connect() as db: self.assertEqual(before,rows(db))
                                self.assertEqual(calls,[1]); self.assertEqual(len(proofs),1)
                                with self.assertRaisesRegex(ValueError,'job_not_accessible'): dispatch(case.cid+1000)
                                self.assertEqual(error,'trial_unsettled_request' if path=='m36' else 'model_result_unknown')
                            finally: release.set()
                            if unknown and path!='metadata':
                                with self.assertRaisesRegex(ValueError,'provider_usage_missing' if path=='m36' else 'model_result_unknown'):
                                    future.result(timeout=15)
                            else: future.result(timeout=15)
                        with app.connect() as db:
                            parent=db.execute('SELECT * FROM mv3_jobs WHERE id=?',(jid,)).fetchone()
                            child=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(jid,)).fetchone()
                            self.assertEqual(parent['status'],'unknown' if unknown else 'completed')
                            self.assertEqual(child['state'],'unknown' if unknown else 'completed')
                            self.assertEqual(child['request_id'],proofs[0].request_id)
                            self.assertEqual(child['dispatch_attempt_id'],proofs[0].attempt_id)
                            if unknown:
                                self.assertIsNone(child['used_input_tokens'])
                                self.assertGreater(child['reserved_tokens'],0)
                            else: self.assertGreater(child['used_input_tokens'],0)
                            if path=='m36':
                                ledger=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(tid,)).fetchone()
                                self.assertGreater(ledger['reserved_microusd'],0) # other variant remains reserved
                                self.assertEqual(ledger['spent_estimate_microusd']==0,unknown)
                                self.assertIsNone(child['used_cost_units'])
                            elif path=='m35':
                                report=jobs.model_status(db,jid,case.cid)
                                self.assertEqual(report['reservedCostUnits']>0,unknown)
                            if path=='metadata':
                                self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',(jid,)).fetchone()[0],0 if unknown else 1)
                            elif path=='m35': self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0],0 if unknown else 1)
                        self.assertEqual(calls,[1]); self.assertEqual(len(proofs),1)
                        if not unknown:
                            expected={'m35':'job_not_dispatchable','metadata':'job_not_claimable','m36':'trial_chunk_not_prepared'}[path]
                            with self.assertRaisesRegex(ValueError,expected): dispatch(case.cid)
                            self.assertEqual(calls,[1])
                    finally: case.doCleanups()

    def test_hold_is_not_a_provider_request_and_access_precedes_reason(self):
        db=database()
        try:
            db.execute("INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at) VALUES('old',1,'NARRATIVE','h','{\"model_mode\":true}','paused','synthetic')")
            db.commit(); schema.migrate(db)
            self.assertEqual(jobs.unsettled_kind(db,jobs.owned(db,'old',1)),'migration_hold')
            with self.assertRaisesRegex(ValueError,'legacy_reconciliation_required'): jobs.claim(db,'old',1)
            with self.assertRaisesRegex(ValueError,'job_not_accessible'): jobs.claim(db,'old',2)
            with self.assertRaisesRegex(ValueError,'model_result_unknown'): jobs.control(db,'old',1,'resume')
        finally: db.close()

    def test_independent_process_cannot_restore_dispatch_capability(self):
        with tempfile.TemporaryDirectory(prefix='a3-synthetic-restart-') as directory:
            path=Path(directory)/'synthetic.sqlite3'
            source=database()
            with closing(sqlite3.connect(path)) as target: source.backup(target)
            source.close()
            db=sqlite3.connect(path); db.row_factory=sqlite3.Row
            try:
                schema.migrate(db)
                jid=processing.create(db,1,'entry','synthetic-entry',['title'],{'synthetic':1},rule_version='v1',input_version='v1')
                processing.prepare_chunks(db,jid,1,[{'reservedTokens':100,'reservedCostUnits':100}])
                request=processing.dispatch(db,jid,1,0); db.commit()
                before=rows(db)
                del request # original caller has lost its capability
                code='''
import sqlite3,sys
from backend.app import memory_v2_jobs as j, memory_v2_processing as p
db=sqlite3.connect(sys.argv[1]);db.row_factory=sqlite3.Row
job=db.execute('SELECT * FROM mv3_jobs').fetchone();c=db.execute('SELECT * FROM mv3_model_chunks').fetchone()
fake=j.DispatchProof(c['dispatch_attempt_id'],c['request_id'],job['id'],1,0,sys.argv[1],c['input_hash'])
checks=[lambda:j.settle_dispatch(db,job['id'],1,0,c['request_id'],fake,state='completed',used_input=1,used_output=1),lambda:j.control(db,job['id'],1,'resume'),lambda:p.create(db,1,'entry','synthetic-entry',['title'],{'synthetic':2},rule_version='v2',input_version='v2',origin='scheduler'),lambda:p.dispatch(db,job['id'],1,0)]
expected=['dispatch_identity_mismatch','model_result_unknown','semantic_scope_unknown','unsettled_request']
for fn,reason in zip(checks,expected):
    try: fn()
    except ValueError as exc: assert str(exc)==reason,(str(exc),reason)
    else: raise AssertionError('blocked action accepted')
    db.rollback()
db.close()
print('4 independent-process actions safely rejected')
'''
                proc=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True,timeout=30,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(proc.returncode,0,proc.stderr)
                self.assertIn('4 independent-process actions safely rejected',proc.stdout)
                self.assertEqual(before,rows(db))
                self.assertEqual(db.execute("SELECT SUM(reserved_cost_units) FROM mv3_model_chunks WHERE state IN ('possibly_dispatched','unknown')").fetchone()[0],100)
            finally: db.close()
