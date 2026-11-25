"""Versioned final Synopsis through confirmed synthetic RAW and MockTransport."""
import copy
import json
import unittest
import uuid
from pathlib import Path

import httpx

import test_memory_v2_h32 as h32
from test_memory_v2_a34_responses_wiring import (MODEL, PRICE, app_module, importer,
                                                  synopsis, trials, metadata)
from backend.app import memory_v2_synopsis_final as final
from backend.app import memory_v2_responses_adapter as responses
from backend.app import memory_v2_processing_schema as processing_schema
from backend.app import memory_v2_metadata_jobs as metadata_jobs
from backend.app import memory_v2_index as indexer
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_synopsis_qualifiers as qualifiers
from backend.app import memory_v2_metadata_contract as contract
from test_memory_v2_a34_qualifier_candidate import FIXTURE, rewrite


class FixtureWire:
    def __init__(self, outputs, *, missing_usage=False, on_generation=None):
        self.outputs = outputs
        self.missing_usage = missing_usage
        self.on_generation = on_generation
        self.counts = []
        self.generations = []
        self.adapter = responses.ResponsesAdapter(
            model=MODEL, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        body = json.loads(request.content)
        if request.url.path == '/v1/responses/input_tokens':
            self.counts.append(body)
            return httpx.Response(200, json={'input_tokens': 100})
        self.generations.append(body)
        if self.on_generation:
            self.on_generation(body)
        output = self.outputs[len(self.generations)-1]
        answer = {'id': 'resp_'+str(uuid.uuid4()), 'model': MODEL,
                  'status': 'completed', 'service_tier': 'default',
                  'output': [{'type': 'message', 'content': [{
                      'type': 'output_text', 'text': json.dumps(output, ensure_ascii=False)}]}]}
        if not self.missing_usage:
            answer['usage'] = {'input_tokens': 100, 'output_tokens': 100}
        return httpx.Response(200, json=answer)


class VersionedFinal(unittest.TestCase):
    def setUp(self):
        self.fixture = h32.H32Contract('test_confirm_once_and_reuse_exact_uuids_without_review')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cid, self.owner = self.fixture.cid, self.fixture.owner
        with app_module.connect() as db:
            metadata_jobs.init_db(db)
            trials.init_db(db)
            messages = [{'role': 'user' if i % 2 == 0 else 'assistant',
                         'content': '无关合成消息'+str(i+1),
                         'created_at': '2026-07-20 19:38'} for i in range(130)]
            messages[4:10] = [{'role': s['speaker'], 'content': s['text'],
                               'created_at': '2026-07-20 19:38'}
                              for s in FIXTURE['sources']]
            messages[0]['role'] = 'assistant'  # Preserve H3.2's 65/65 role fixture.
            body = json.dumps({'format': 'yosaki-conversation', 'version': 2,
                               'conversation': {'id': 'qualifier-e2-synthetic'},
                               'messages': messages},
                              ensure_ascii=False).encode()
            batch = importer.prepare(db, self.cid, [('qualifier-synthetic.json', body)])['batchId']
            importer.confirm(db, batch, self.cid)
            step = importer.step(db, batch, self.cid)
            while step['status'] == 'running':
                step = importer.step(db, batch, self.cid)
            file = db.execute('SELECT * FROM mv2_import_files WHERE batch_id=?',
                              (batch,)).fetchone()
            job = indexer.start_job(db, self.cid, 'rebuild')
            while job['status'] == 'running':
                job = indexer.step_job(db, job['job_id'])
            _, _, snapshot = importer.confirmed_file_snapshot(
                db, batch, self.cid, file['id'], file['file_sha256'], file['parser_version'])
            db.commit()
            result = importer.confirm_h32_drafts(
                db, batch, self.cid, file['id'],
                [{'start': start, 'end': end, 'title': '纯合成 '+str(i)}
                 for i, (start, end) in enumerate(h32.RANGES)],
                expected_sha=file['file_sha256'], expected_parser=file['parser_version'],
                expected_snapshot=snapshot, h31_snapshot_hash='a'*64,
                editor_id=self.owner, confirmation_origin='synthetic_owner_instruction')
            self.entry = result['items'][1]['entryId']
        with app_module.connect() as db:
            processing_schema.migrate(db)
            _, self.sources, self.batches = synopsis._read_set(db, self.entry, self.cid, 1200)
        self.assertEqual(len(self.batches), 2)
        self.idmap = {old['id']: new['id'] for old, new in zip(FIXTURE['sources'], self.sources)}

    def remap(self, output, sources):
        result = copy.deepcopy(output)
        for unit in result['units']:
            for ref in unit['evidence']:
                ref['id'] = self.idmap[ref['id']]
        used = {(r['kind'], r['id']) for u in result['units'] for r in u['evidence']}
        result['sourceCoverage'] = [{'kind': s['kind'], 'id': s['id'],
                                     'used': (s['kind'], s['id']) in used} for s in sources]
        return result

    def source_task(self):
        with app_module.connect() as db:
            task = synopsis.create_batch_handoff_trial(
                db, self.entry, self.cid, self.owner, price=PRICE, batch_input_cap=1200)
        batch_outputs = [self.remap(part, batch)
                         for part, batch in zip(FIXTURE['parts'], self.batches)]
        wire = FixtureWire(batch_outputs)
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        result = synopsis.run_real_trial(app_module.connect, task['jobId'], self.cid,
                                         self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(wire.generations), 2)
        return task

    def final_task(self, source, profile=final.PROFILE_V2):
        with app_module.connect() as db:
            creator = final.create_task_v2 if profile == final.PROFILE_V2 else final.create_task
            return creator(db, self.entry, self.cid, self.owner,
                           source['jobId'], model=MODEL, price=PRICE)

    def test_v2_business_path_accepts_rewrite_and_is_idempotent(self):
        source = self.source_task()
        task = self.final_task(source)
        wire = FixtureWire([self.remap(FIXTURE['publicOutput'], self.sources)])
        final.prepare_task(app_module.connect, task['jobId'], self.cid, self.owner, wire.adapter)
        result = final.run_task(app_module.connect, task['jobId'], self.cid,
                                self.owner, wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        with app_module.connect() as db:
            field = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual((field['reviewStatus'], field['lifecycleStatus']),
                             ('needs_review', 'draft'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results WHERE job_id=?',
                                        (task['jobId'],)).fetchone()[0], 1)
        again = final.run_task(app_module.connect, task['jobId'], self.cid,
                               self.owner, wire.adapter)
        self.assertTrue(again['reused'])
        self.assertEqual(len(wire.generations), 1)

    def run_final_output(self, output, *, profile=final.PROFILE_V2,
                         missing_usage=False):
        source = self.source_task()
        task = self.final_task(source, profile)
        wire = FixtureWire([self.remap(output, self.sources)],
                           missing_usage=missing_usage)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        outcome = final.run_task(app_module.connect, task['jobId'], self.cid,
                                 self.owner, wire.adapter)
        return task, wire, outcome

    def test_v1_keeps_legacy_rejection_and_v2_keeps_version_in_readset(self):
        source = self.source_task()
        v1 = self.final_task(source, final.PROFILE)
        v2 = self.final_task(source, final.PROFILE_V2)
        self.assertNotEqual(v1['jobId'], v2['jobId'])
        with app_module.connect() as db:
            old = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                        (v1['jobId'],)).fetchone()[0])
            new = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                        (v2['jobId'],)).fetchone()[0])
            self.assertEqual(old['readSet']['validator'], final.VALIDATOR)
            self.assertNotIn('qualifierVersion', old['readSet'])
            self.assertEqual(new['readSet']['qualifierVersion'], qualifiers.VERSION)
            self.assertEqual(new['readSet']['validator'], final.VALIDATOR_V2)
        wire = FixtureWire([self.remap(FIXTURE['publicOutput'], self.sources)])
        final.prepare_task(app_module.connect, v1['jobId'], self.cid,
                           self.owner, wire.adapter)
        outcome = final.run_task(app_module.connect, v1['jobId'], self.cid,
                                 self.owner, wire.adapter)
        self.assertEqual(outcome['status'], 'failed', outcome)
        self.assertIn('synopsis_qualifier_dropped', outcome['reason'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (v1['jobId'],)).fetchone()[0], 0)

    def test_version_tamper_and_unknown_block_preparation_without_send(self):
        source = self.source_task()
        task = self.final_task(source)
        wire = FixtureWire([])
        with app_module.connect() as db:
            row = db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                             (task['jobId'],)).fetchone()
            payload = json.loads(row[0])
            payload['readSet']['qualifierVersion'] = 'unknown-v99'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                       (json.dumps(payload), task['jobId']))
        with self.assertRaisesRegex(ValueError, 'synopsis_final_job_invalid'):
            final.prepare_task(app_module.connect, task['jobId'], self.cid,
                               self.owner, wire.adapter)
        self.assertEqual((wire.counts, wire.generations), ([], []))

    def test_v2_request_bytes_match_v1_and_prepared_tamper_blocks_dispatch(self):
        source = self.source_task()
        old = self.final_task(source, final.PROFILE)
        new = self.final_task(source)
        with app_module.connect() as db:
            old_payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                                (old['jobId'],)).fetchone()[0])
            new_payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                                (new['jobId'],)).fetchone()[0])
            _, old_sources, old_parts = final._current(db, old_payload, self.cid)
            _, new_sources, new_parts = final._current(db, new_payload, self.cid)
            self.assertEqual(final._request(old_payload, old_sources, old_parts),
                             final._request(new_payload, new_sources, new_parts))
        wire = FixtureWire([])
        final.prepare_task(app_module.connect, new['jobId'], self.cid,
                           self.owner, wire.adapter)
        with app_module.connect() as db:
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                            (new['jobId'],)).fetchone()[0])
            payload['readSet']['validator'] = 'unknown-validator'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                       (json.dumps(payload), new['jobId']))
        with self.assertRaisesRegex(ValueError, 'synopsis_final_job_invalid'):
            final.run_task(app_module.connect, new['jobId'], self.cid,
                           self.owner, wire.adapter)
        self.assertEqual(wire.generations, [])

    def test_v2_field_unknown_validator_is_stale_without_exposing_text(self):
        task, _, outcome = self.run_final_output(FIXTURE['publicOutput'])
        self.assertEqual(outcome['status'], 'completed')
        with app_module.connect() as db:
            detail = metadata.read_draft(db, self.entry, self.cid)
            row = metadata._fields(db, detail['versionId'])['synopsis']
            deps = json.loads(row['dependencies_json'])
            deps['synopsisValidator'] = 'unknown-validator'
            token = contract.token(['synopsis', json.loads(row['value_json']),
                                    row['provenance'], row['rule_version'],
                                    json.loads(row['evidence_json']), deps])
            db.execute('UPDATE mv3_entry_fields SET dependencies_json=?,token=? WHERE id=?',
                       (json.dumps(deps), token, row['id']))
            field = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual(field['lifecycleStatus'], 'stale')
            self.assertIsNone(field['value'])

    def test_missing_usage_remains_unknown_with_reservation(self):
        task, wire, outcome = self.run_final_output(
            FIXTURE['publicOutput'], missing_usage=True)
        self.assertEqual(outcome['status'], 'unknown', outcome)
        with app_module.connect() as db:
            trial = trials.trial_status(db, task['trialId'], self.cid)
            self.assertGreater(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (task['jobId'],)).fetchone()[0], 0)
        self.assertEqual(len(wire.generations), 1)

    def test_readset_change_after_dispatch_rejects_field_but_settles_usage(self):
        source = self.source_task()
        task = self.final_task(source)
        def change_version(_):
            with app_module.connect() as db:
                payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                                (task['jobId'],)).fetchone()[0])
                payload['readSet']['qualifierVersion'] = 'unknown-v99'
                db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                           (json.dumps(payload), task['jobId']))
        wire = FixtureWire([self.remap(FIXTURE['publicOutput'], self.sources)],
                           on_generation=change_version)
        final.prepare_task(app_module.connect, task['jobId'], self.cid,
                           self.owner, wire.adapter)
        outcome = final.run_task(app_module.connect, task['jobId'], self.cid,
                                 self.owner, wire.adapter)
        self.assertEqual(outcome['status'], 'failed', outcome)
        self.assertIn('synopsis_final_version_changed', outcome['reason'])
        with app_module.connect() as db:
            trial = trials.trial_status(db, task['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (task['jobId'],)).fetchone()[0], 0)


def _negative_case(changes, expected):
    def check(self):
        task, wire, outcome = self.run_final_output(rewrite(changes=changes))
        self.assertEqual(outcome['status'], 'failed', outcome)
        self.assertIn(expected, outcome['reason'])
        self.assertEqual(len(wire.generations), 1)
        with app_module.connect() as db:
            trial = trials.trial_status(db, task['trialId'], self.cid)
            self.assertGreater(trial['spent_estimate_microusd'], 0)
            self.assertEqual(trial['reserved_microusd'], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                        'WHERE job_id=?', (task['jobId'],)).fetchone()[0], 0)
    return check


VersionedFinal.test_old_name_correction_cannot_disappear = _negative_case(
    {0: '用户为社区科学展准备说明，主题仍在讨论。'},
    'synopsis_correction_relation_dropped')
VersionedFinal.test_tentative_cannot_become_certain = _negative_case(
    {1: '用户随后更正为已经确定采用白色卡纸，展示名称暂改为「降雨观察草案」。'},
    'synopsis_qualifier_conflict')
VersionedFinal.test_unapproved_cannot_become_approved = _negative_case(
    {9: '用户决定暂缓提交，日文备注表示已经获批。'},
    'synopsis_qualifier_conflict')
VersionedFinal.test_unrelated_negation_cannot_license_approval = _negative_case(
    {9: '用户不提交；日文备注表示已经获批。'},
    'synopsis_qualifier_conflict')
VersionedFinal.test_condition_cannot_disappear = _negative_case(
    {8: '助手强调，若之后拿到许可，就提交；现在不能写成展品已可展示。'},
    'synopsis_condition_dependency_dropped')
VersionedFinal.test_friend_device_cannot_become_user_device = _negative_case(
    {5: '友人说用户的装置已经运转，用户也未看到测试记录。'},
    'synopsis_attribution_conflict')


if __name__ == '__main__':
    unittest.main()
