import unittest
from a3_gold import CASES
from backend.app import memory_v2_metadata_contract as c


class A3Contract(unittest.TestCase):
    def test_gold_is_synthetic_and_partitioned(self):
        self.assertEqual(len(CASES),32)
        self.assertEqual({x['group'] for x in CASES},
                         {'category_ambiguity','entity_alias','zh_ja_dialogue','time_boundary'})

    def test_vector_needs_title_and_synopsis(self):
        for fields, eligible in [({'title':'available','narrative':'available'},False),
                                 ({'title':'available','tags':'available'},False),
                                 ({'title':'available','synopsis':'available'},True)]:
            q=c.a3_qualifications(source_valid=True,field_statuses=fields,
                entry_published=True,lexical_match=True,vector_index_valid=True)
            self.assertEqual(q['vectorCandidateRecall'],eligible)
            self.assertFalse(q['formalContext'])

    def test_draft_and_source_gates(self):
        q=c.a3_qualifications(source_valid=True,field_statuses={'title':'candidate'},
                              entry_published=False)
        self.assertTrue(q['draftManagementSearch'])
        self.assertFalse(q['lexicalCandidateRecall'])
        self.assertFalse(q['p2Projection'])
        self.assertFalse(c.a3_qualifications(source_valid=False,field_statuses={'title':'available'})['draftManagementSearch'])

    def test_legacy_contract_unchanged(self):
        self.assertEqual(c.qualifications(source_valid=True,field_statuses={'title':'available'},entry_published=True),
            {'browse':True,'search':True,'candidateRecall':False,'p2Projection':True,'formalContext':False})
