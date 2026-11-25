from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_memory_v2_derivation_security import M31API, app_module, api
from backend.app import memory_v2_jobs as jobs


class ModelApiAcceptance(M31API):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            jobs.init_db(db)

    def test_model_jobs_are_opt_in_and_strict(self):
        base='/api/memory-v2/derivations/jobs'
        body={'characterId':self.cid,'kind':'NARRATIVE','sourceRefs':self.refs(),'modelMode':True}
        self.assertEqual(self.client.post(base,json=body).status_code,503)
        app=FastAPI()
        api.install(app,app_module.connect,app_module.current_session,enable_model_jobs=True)
        with TestClient(app) as client:
            client.cookies.set(app_module.SESSION_COOKIE,'synthetic-token')
            self.assertEqual(client.post(base,json={**body,'modelUrl':'https://example.com'}).status_code,422)
            self.assertEqual(client.post(base,json={**body,'reviewerId':1}).status_code,422)
            response=client.post(base,json=body)
            self.assertEqual(response.status_code,201)
            jid=response.json()['jobId']
            self.assertEqual(client.post(base,json=body).json()['jobId'],jid)
            self.assertEqual(client.get(base+'/'+jid,params={'characterId':self.cid}).json()['status'],'queued')
            self.assertEqual(client.post(base+'/'+jid+'/pause',params={'characterId':self.cid}).json()['status'],'paused')
            self.assertEqual(client.post(base+'/'+jid+'/resume',params={'characterId':self.cid}).json()['status'],'queued')
            self.assertEqual(client.get(base+'/'+jid,params={'characterId':self.cid+100}).status_code,404)
            client.cookies.clear()
            self.assertEqual(client.get(base+'/'+jid,params={'characterId':self.cid}).status_code,401)
