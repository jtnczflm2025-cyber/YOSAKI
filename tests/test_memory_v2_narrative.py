import json
from test_memory_v2_derivation import M3Base, app_module, derived
from backend.app import memory_v2_narrative as narrative


class NarrativeAcceptance(M3Base):
    def replace_sources(self, db, user, assistant):
        db.execute('UPDATE messages SET content=? WHERE id=?', (user, self.user))
        db.execute('UPDATE messages SET content=? WHERE id=?', (assistant, self.assistant))
        self._index(db)

    def test_first_person_attribution_mapping_and_review_gate(self):
        with app_module.connect() as db:
            self.replace_sources(db, '昨天我摔倒了，你一定很难过吧', '……俺有点不安，但明天不一定去。')
            v = narrative.create_narrative(db, self.cid, self.refs())
            self.assertIn('她说：「昨天我摔倒了', v['body'])
            self.assertIn('我说：「……俺有点不安', v['body'])
            self.assertNotIn('俺说', v['body'])
            with self.assertRaisesRegex(ValueError, 'valid_review_required'):
                derived.activate_version(db, v['id'], self.cid)
            for span in json.loads(v['mapping_json']):
                raw = derived.live_source(db, self.cid, span['sourceKind'], span['sourceId']).text
                self.assertEqual(v['body'][span['outputStart']:span['outputEnd']], raw[span['rawStart']:span['rawEnd']])
            self.approve(db, v['id'])
            self.assertEqual(derived.read_version(db, v['id'], self.cid)['evidence_kind'], 'system_derived_text')

    def test_mock_positive_and_no_synthetic_introspection_negatives(self):
        with app_module.connect() as db:
            self.replace_sources(db, '你一定很难过吧', '……')
            v = narrative.create_narrative(db, self.cid, self.refs(), mock_model=lambda request: request['expectedBody'])
            self.assertEqual(v['status'], 'needs_review')
            count = db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0]
            for invented in ('俺那时很难过', '俺因为害怕失去她而不安', '俺昨天摔倒了',
                             '俺终于成熟了', '俺答应了', '操作成功了'):
                with self.assertRaisesRegex(ValueError, 'unverifiable_narrative'):
                    narrative.create_narrative(db, self.cid, self.refs(), mock_model=lambda _: invented)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], count)

    def test_source_changes_during_mock_generation_block_persistence(self):
        with app_module.connect() as db:
            def changed(request):
                db.execute('UPDATE messages SET content=? WHERE id=?', ('changed', self.user))
                return request['expectedBody']
            with self.assertRaises(ValueError):
                narrative.create_narrative(db, self.cid, self.refs(), mock_model=changed)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], 0)

    def test_corrections_and_long_quotes_stay_in_order(self):
        with app_module.connect() as db:
            self.replace_sources(db, '先前说是周一，后来更正为周二。', '不是同意；如果有条件才考虑。' * 1000)
            v = narrative.create_narrative(db, self.cid, self.refs())
            spans = json.loads(v['mapping_json'])
            self.assertEqual([s['sourceId'] for s in spans], [str(self.user), str(self.assistant)])
            self.assertNotIn('成长', v['body'])
