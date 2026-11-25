import json
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_memory_v2_closeout import Closeout, app_module, workspace
from backend.app import memory_v2_import as importer, memory_v2_import_schema as schema


class ImportReading(unittest.TestCase):
    def test_local_times_reading_replay_and_separate_owner_confirmation(self):
        case=Closeout('test_general_import_receipts_confirmation_not_h32_specimen')
        case.setUp()
        self.addCleanup(case.doCleanups)
        messages=[{'role':('user','assistant')[i%2], 'content':f'合成对话第{i}句。',
                   'created_at':'2026-06-20 10:00' if i<4 else '2026-06-20 12:00'} for i in range(8)]
        archive=json.dumps({'format':'yosaki-conversation','version':2,'messages':messages}).encode()
        with app_module.connect() as db:
            schema.init_db(db);db.commit()
            batch=importer.prepare(db,case.cid,[('reading.json',archive)])['batchId']
            importer.confirm(db,batch,case.cid)
            file=importer.step(db,batch,case.cid)['files'][0]['id']
            case._index(db);db.commit()
        app=FastAPI()
        workspace.install(app,app_module.connect,lambda req:{'id':1} if req.headers.get('x-test-owner') else None)
        client=TestClient(app)
        base=f'/api/memory-v2/workspace/imports/{batch}/{file}'
        url=base+'/reading-drafts'
        q={'characterId':case.cid}
        self.assertEqual(client.post(url,params=q).status_code,401)
        client.headers['x-test-owner']='yes'
        first=client.post(url,params=q)
        self.assertEqual(first.status_code,200,first.text)
        items=first.json()['items']
        self.assertEqual([(r['start'],r['end']) for r in items],[(1,4),(5,8)])
        self.assertEqual(client.post(url,params=q).json()['items'],items)
        for i,item in enumerate(items):
            detail=client.get('/api/memory-v2/workspace/entries/'+item['entryId'],params=q).json()
            self.assertEqual(detail['narrative']['status'],'needs_review')
            self.assertEqual([r['text'] for r in detail['raw']],[m['content'] for m in messages[i*4:i*4+4]])
            self.assertFalse(detail['entry']['qualifications']['p2Projection'])
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT confirmed_by_user_id FROM mv2_import_boundary_confirmations WHERE file_id=?',(file,)).fetchone()[0])
        plan=client.get(base+'/boundaries',params=q).json()
        result=client.post(base+'/boundaries',json={**q,'sourceSnapshot':plan['sourceSnapshot'],'segments':plan['segments']})
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['reused'])
        self.assertEqual(result.json()['items'],items)
