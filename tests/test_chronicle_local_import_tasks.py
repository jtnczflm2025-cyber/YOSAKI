"""Offline durability and controls for user-requested local archives."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_memory_v2_closeout as fixture
from fastapi import HTTPException
from backend.app import memory_v2_import as importer
from backend.app import memory_v2_import_schema, memory_v2_jobs
from backend.app import memory_v2_local_import_tasks as local


class DurableArchives(unittest.TestCase):
    def setUp(self):
        case = fixture.Closeout('test_general_import_receipts_confirmation_not_h32_specimen')
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.cid = case.cid
        self.connect = fixture.app_module.connect
        self.worker = local.Worker(self.connect)
        with self.connect() as db:
            memory_v2_import_schema.init_db(db)
            memory_v2_jobs.init_db(db)
            db.commit()
            messages = [{'role': 'user' if n % 2 == 0 else 'assistant', 'content': f'合成资料 {n}'} for n in range(135)]
            artifact = json.dumps({'format':'yosaki-conversation','version':2,'messages':messages}).encode()
            self.batch = importer.prepare(db,self.cid,[('本地材料.json',artifact)])['batchId']

    def enqueue(self, mode='raw'):
        with self.connect() as db:
            source = importer.status(db,self.batch,self.cid)
            local.enqueue(db,self.batch,self.cid,1,mode,source['planVersion'],source['previewFingerprint'])

    def read(self):
        with self.connect() as db:
            return local.public(db,local.task(db,self.batch,self.cid))

    def control(self,action):
        expected=self.read()['version']
        with self.connect() as db:
            local.change(db,self.batch,self.cid,1,action,expected)

    def test_duplicate_pause_restart_and_cancel(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: self.enqueue(),range(3)))
        self.worker.tick()
        self.worker.tick()
        self.control('pause')
        restarted=local.Worker(self.connect)
        self.assertFalse(restarted.tick())
        self.control('resume')
        self.control('cancel')
        self.enqueue()
        self.assertFalse(restarted.tick())
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mv3_jobs WHERE kind=?',(local.KIND,)).fetchone()[0],1)
            self.assertEqual(importer.status(db,self.batch,self.cid)['processed'],100)
            with self.assertRaises(HTTPException):
                local.change(db,self.batch,self.cid,1,'resume',0)
        self.assertEqual(self.read()['status'],'cancelled')

    def test_restart_after_partial_index_failure_and_manual_recovery(self):
        self.enqueue('organize')
        for _ in range(10):
            self.worker.tick()
            if self.read()['stage']=='index':
                break
        with self.connect() as db:
            job=json.loads(local.task(db,self.batch,self.cid)['payload_json'])['index']
            self.assertTrue(job)
            local.index.set_status(db,job,'paused')
        restarted=local.Worker(self.connect)
        with patch.object(local.index,'step_job',side_effect=RuntimeError('injected')):
            restarted.tick()
        self.assertEqual(self.read()['status'],'failed')
        self.assertFalse(restarted.tick())
        self.control('resume')
        for _ in range(100):
            if not restarted.tick():
                break
        self.assertEqual(self.read()['status'],'completed')
        with self.connect() as db:
            state=importer.status(db,self.batch,self.cid)
            self.assertTrue(all(s['entry_id'] for s in state['segments']))
            self.assertEqual(len({s['entry_id'] for s in state['segments']}),len(state['segments']))
            self.assertEqual(db.execute('SELECT count(*) FROM mv2_import_receipts').fetchone()[0],135)
            self.assertEqual(json.loads(local.task(db,self.batch,self.cid)['payload_json'])['index'],job)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_model_chunks").fetchone()[0],0)
