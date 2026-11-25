import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_memory_v2_closeout as fixture
from test_memory_v2_entries import app_module
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_chronicle_workspace as workspace
from backend.app.memory_v2_chronicle_dates import _calendar_day


class BatchReview(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.Closeout('test_explicit_confirm_link_publish_read_and_no_review_inheritance')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.cid, self.eid = self.fx.cid, self.fx.draft['entryId']
        self.owner = {'id': 1}
        self.app = FastAPI()
        workspace.install(self.app, app_module.connect, lambda _: self.owner)
        self.client = TestClient(self.app)
        self.url = '/api/memory-v2/workspace/review-selection'

    def prepare(self):
        with app_module.connect() as db:
            entry = metadata.read_draft(db, self.eid, self.cid)
            fixture.finite.create(db,self.cid,1,self.eid,entry['versionId'])
            metadata.revise(db,self.eid,self.cid,entry['versionId'],{'synopsis':'她想休息，我表示同意。'},1)

    def card(self):
        response = self.client.get(self.url, params={'characterId':self.cid})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['items'][0]

    def submit(self, card):
        return self.client.post(self.url+'/'+self.eid+'/publish',json={
            'characterId':self.cid,'expectedVersion':card['version'],'previewToken':card['token']})

    def test_snapshot_authority_and_publish(self):
        self.assertEqual(self.submit(self.card()).json()['status'],'skipped')
        stale=self.card()
        self.prepare()
        self.assertEqual(self.submit(stale).status_code,409)
        card=self.card()
        self.assertFalse(card['reasons'],card)
        self.assertEqual(self.submit(card).json()['status'],'published')
        self.assertEqual(self.submit(card).status_code,409)
        self.assertTrue(self.card()['published'])
        self.assertEqual(self.submit(self.card()).json()['status'],'skipped')
        self.owner = None
        self.assertEqual(self.submit(card).status_code,401)
        self.assertEqual(self.client.get(self.url,params={'characterId':self.cid}).status_code,401)

    def test_atomic_rollback_and_role_gate(self):
        self.prepare()
        card=self.card()
        with patch.object(metadata,'publish',side_effect=ValueError('forced_failure')):
            self.assertEqual(self.submit(card).status_code,409)
        self.assertEqual(self.card(),card)
        self.assertEqual(self.client.get(self.url,params={'characterId':-1}).status_code,403)

    def test_calendar_date_requires_matching_source(self):
        source={'precision':'local_datetime_unverified','occurredAt':None,
                'occurredDate':'2026-07-19','rawTime':'2026-07-19 17:37','timezoneBasis':'unknown'}
        day=_calendar_day(source)
        self.assertEqual((day['precision'],day['occurredAt'],day['rawTime']),('day',None,source['rawTime']))
        for change in ({'rawTime':'2026-07-20 17:37'},{'rawTime':'invalid'},{'precision':'unknown'}):
            row={**source,**change}
            self.assertEqual(_calendar_day(row),row)
