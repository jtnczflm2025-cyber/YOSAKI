import unittest
from backend.app.memory_v2_metadata_contract import time_tokens, digest_token, qualifications


class MetadataContract(unittest.TestCase):
    def evidence(self):
        return [dict(kind='message', id='1', sourceOrdinal=1, rawTime='2026-07-20 19:38',
                     occurredAt=None, occurredDate=None, precision='local_datetime_unverified',
                     timezoneBasis='none', recordedAt='2026-11-08T00:00:00Z')]

    def test_import_time_cannot_become_occurrence(self):
        first = self.evidence()
        self.assertFalse(time_tokens(first)['ageEligible'])
        changed = [dict(first[0], recordedAt='2030-01-01')]
        self.assertEqual(time_tokens(first), time_tokens(changed))

    def test_time_evidence_and_projection_are_independent(self):
        rows = self.evidence()
        before = time_tokens(rows)
        self.assertEqual(before['timeEvidenceToken'], time_tokens(rows, 0)['timeEvidenceToken'])
        self.assertNotEqual(before['timeToken'], time_tokens(rows, 0)['timeToken'])
        for key, value in [('rawTime', 'changed'), ('sourceOrdinal', 2), ('timezoneBasis', '+08:00'),
                           ('occurredDate', '2026-07-20'), ('occurredAt', '2026-07-20T11:38:00Z'),
                           ('precision', 'day')]:
            self.assertNotEqual(before['timeEvidenceToken'],
                time_tokens([dict(rows[0], **{key: value})])['timeEvidenceToken'])

    def test_digest_uses_only_its_profile_fields(self):
        before = digest_token('raw_sources_v1', 's', ['a'], ['child'], {'title': 'old'})
        self.assertEqual(before, digest_token('raw_sources_v1', 's', ['a'], ['child'], {'title': 'new'}))
        self.assertNotEqual(before, digest_token('raw_sources_v1', 's', ['a'], ['newchild']))
        with self.assertRaisesRegex(ValueError, 'input_missing'):
            digest_token('synopsis_v1', 's', ['a'], [])

    def test_draft_can_browse_but_cannot_recall(self):
        result = qualifications(source_valid=True, field_statuses={'title': 'available'})
        self.assertTrue(result['browse']); self.assertTrue(result['search'])
        self.assertFalse(result['candidateRecall']); self.assertFalse(result['formalContext'])
        self.assertFalse(any(qualifications(source_valid=False,
            field_statuses={'synopsis': 'available'}, entry_published=True).values()))
