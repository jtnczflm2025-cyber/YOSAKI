import unittest
from test_memory_v2_derivation import M3Base, app_module, derived
from backend.app import memory_v2_cleaning as cleaning


class CleaningRules(unittest.TestCase):
    def test_confirmed_translation_and_switch(self):
        self.assertEqual(cleaning.clean_text('大丈夫（没关系）')['text'], '大丈夫')
        self.assertEqual(cleaning.clean_text('大丈夫（没关系）', translation=False)['text'], '大丈夫（没关系）')

    def test_negative_cases_stay_verbatim(self):
        for text in ('我同意（但有一个条件）', '没事（其实很难受）', 'fn(a, b)', '(2026-10-28)',
                     '……', '不行（不可以）', 'もし雨なら行かない', '「大丈夫（没关系）」',
                     '```\n大丈夫（没关系）\n```', '- 大丈夫（没关系）', '大丈夫（没有问题，但是明天再说）',
                     '用户说：大丈夫（没关系）', '大丈夫（没关系）\n否定和条件必须保留', '啊' * 20000):
            with self.subTest(text=text[:30]):
                self.assertEqual(cleaning.clean_text(text)['text'], text)

    def test_diff_reconstructs_and_spans_match(self):
        for raw in ('大丈夫（没关系）！', '甲\r\n乙（有条件）'):
            result = cleaning.clean_text(raw)
            restored = raw
            for edit in reversed(result['edits']):
                restored = restored[:edit['rawStart']] + edit['after'] + restored[edit['rawEnd']:]
            self.assertEqual(restored, result['text'])
            for span in result['spans']:
                self.assertEqual(raw[span['rawStart']:span['rawEnd']], result['text'][span['outputStart']:span['outputEnd']])


class CleaningLifecycle(M3Base):
    def test_rule_recomputed_at_activation_and_source_unchanged(self):
        with app_module.connect() as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM messages')]
            result = cleaning.create_cleaned(db, self.cid, self.refs())
            v = result['version']
            derived.activate_version(db, v['id'], self.cid)
            self.assertIn('[user | message:', derived.read_version(db, v['id'], self.cid)['body'])
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM messages')])
            self.assertEqual(cleaning.create_cleaned(db, self.cid, self.refs())['version']['id'], v['id'])

    def test_forged_rule_body_cannot_auto_activate(self):
        with app_module.connect() as db:
            item = derived.create_derivation(db, self.cid, 'CLEANED', self.refs())
            v = derived.add_version(db, item['id'], self.refs(), '俺明白了她的心情',
                author_kind='rule', rule_version=cleaning.rule_name())
            with self.assertRaisesRegex(ValueError, 'valid_review_required'):
                derived.activate_version(db, v['id'], self.cid)
