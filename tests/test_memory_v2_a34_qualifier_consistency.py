"""Synthetic v3 qualifier lifecycle and read-only public-output replay."""
import copy
import json
import unittest
from pathlib import Path

from test_memory_v2_a34_qualifier_candidate import FIXTURE
import test_memory_v2_a34_qualifier_integration as base
from test_memory_v2_a34_responses_wiring import MODEL, PRICE, app_module, synopsis, metadata
from backend.app import memory_v2_synopsis_final as final
from backend.app import memory_v2_synopsis_qualifiers as qualifiers
from backend.app import memory_v2_m36_trial as trials
from backend.app import memory_v2_responses_adapter as responses

PUBLIC = json.loads((Path(__file__).parent / 'fixtures' /
    'a34_e2pv2_second_public_replay.json').read_text(encoding='utf-8-sig'))
SOURCES = [{'kind':'message', 'id':s['sourceId'], 'speaker':s['speaker'],
            'text':s['raw'], 'sourceStatus':'success'} for s in PUBLIC['sources']]


def change_unit(output, index, old, new):
    result = copy.deepcopy(output)
    segments = [result['text'][u['start']:u['end']] for u in result['units']]
    assert old in segments[index]
    segments[index] = segments[index].replace(old, new)
    result['text'] = ''.join(segments)
    cursor = 0
    for unit, segment in zip(result['units'], segments):
        unit['start'], unit['end'] = cursor, cursor + len(segment)
        cursor += len(segment)
    return result


class QualifierScope(unittest.TestCase):
    def test_public_replay_and_legacy_policies(self):
        self.assertEqual(PUBLIC['historicalStatus'], 'failed/rejected')
        for policy in ('legacy', qualifiers.VERSION):
            with self.subTest(policy=policy), self.assertRaisesRegex(
                    ValueError, 'synopsis_qualifier_dropped'):
                synopsis.validate_output(PUBLIC['output'], SOURCES,
                                         qualifier_policy=policy)
        self.assertEqual(synopsis.validate_output(PUBLIC['output'], SOURCES,
            qualifier_policy=qualifiers.VERSION_V3)['text'], PUBLIC['output']['text'])
        all_sources = FIXTURE['sources']
        output = copy.deepcopy(PUBLIC['output'])
        output['sourceCoverage'] = synopsis.coverage_for(all_sources, output['units'])
        parts = [FIXTURE['parts'][0], {'text': PUBLIC['output']['text'],
                                      'units': PUBLIC['output']['units']}]
        synopsis.validate_integrated_final_output(output, all_sources, parts,
                                                  qualifier_policy=qualifiers.VERSION_V3)

    def test_proposition_and_subject_counterexamples(self):
        output = PUBLIC['output']
        changes = [
            (3, '并完成实际测试后再讨论是否提交', '后再讨论是否提交'),
            (3, '拿到许可并完成实际测试后再讨论是否提交', '拿到许可后再讨论是否提交'),
            (3, '后再讨论是否提交', '后即可提交'),
            (3, '后再讨论是否提交', '后已经提交'),
            (4, '并非已经获批', '已经获批，但别处没有测试'),
            (4, '用户决定', '助手决定'),
        ]
        for index, old, new in changes:
            with self.subTest(index=index, new=new):
                bad = change_unit(output, index, old, new)
                with self.assertRaises(ValueError):
                    synopsis.validate_output(bad, SOURCES,
                                             qualifier_policy=qualifiers.VERSION_V3)
        # The same conjunctive form on an unrelated subject is supported;
        # changing the consequent from deliberation to execution is not.
        raw = '若拿到场地许可并完成设备测试，再讨论是否发布。'
        good = '拿到场地许可并完成设备测试后再讨论是否发布。'
        source = [{'kind':'message','id':'x','speaker':'assistant',
                   'text':raw,'sourceStatus':'success'}]
        ref = {'kind':'message','id':'x','speaker':'assistant',
               'start':0,'end':len(raw),'quote':raw}
        item = {'text':good,'units':[{'start':0,'end':len(good),'evidence':[ref]}],
                'sourceCoverage':[{'kind':'message','id':'x','used':True}]}
        synopsis.validate_output(item, source, qualifier_policy=qualifiers.VERSION_V3)
        bad = change_unit(item, 0, '后再讨论是否发布', '后即可发布')
        with self.assertRaises(ValueError):
            synopsis.validate_output(bad, source, qualifier_policy=qualifiers.VERSION_V3)


class QualifierBusiness(unittest.TestCase):
    def setUp(self):
        self.ctx = base.VersionedFinal('test_v2_business_path_accepts_rewrite_and_is_idempotent')
        self.ctx.setUp()
        self.addCleanup(self.ctx.doCleanups)
        self.cid, self.owner, self.entry = self.ctx.cid, self.ctx.owner, self.ctx.entry

    def batch_outputs(self):
        return [self.ctx.remap(FIXTURE['parts'][0], self.ctx.batches[0]),
                self.ctx.remap(PUBLIC['output'], self.ctx.batches[1])]

    def final_output(self):
        original = FIXTURE['publicOutput']
        selected = [0,1,2,4,5,6,7,8]
        output = {'text':'', 'units':[], 'sourceCoverage':[]}
        for i in selected:
            unit = copy.deepcopy(original['units'][i])
            part = original['text'][unit['start']:unit['end']]
            unit['start'], unit['end'] = len(output['text']), len(output['text']) + len(part)
            output['text'] += part
            output['units'].append(unit)
        return self.ctx.remap(output, self.ctx.sources)

    def test_v3_batch_to_final_review_field_and_idempotency(self):
        with app_module.connect() as db:
            task = synopsis.create_qualifier_batch_handoff_trial(
                db, self.entry, self.cid, self.owner, price=PRICE, batch_input_cap=1200)
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                           (task['jobId'],)).fetchone()[0])
            self.assertEqual(payload['readSet']['validator'], synopsis.QUALIFIER_VALIDATOR)
            self.assertEqual(payload['readSet']['qualifierVersion'], qualifiers.VERSION_V3)
        wire = base.FixtureWire(self.batch_outputs())
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        outcome = synopsis.run_real_trial(app_module.connect, task['jobId'], self.cid,
                                          self.owner, wire.adapter)
        self.assertEqual(outcome['status'], 'completed', outcome)
        self.assertEqual((len(wire.counts),len(wire.generations)), (2,2))
        with app_module.connect() as db:
            follow = final.create_task_v3(db, self.entry, self.cid, self.owner,
                task['jobId'], model=MODEL, price=PRICE)
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                           (follow['jobId'],)).fetchone()[0])
            self.assertEqual(payload['readSet']['qualifierVersion'], qualifiers.VERSION_V3)
            self.assertEqual(payload['readSet']['batchDependency']['qualifierVersion'],
                             qualifiers.VERSION_V3)
        final_wire = base.FixtureWire([self.final_output()])
        final.prepare_task(app_module.connect, follow['jobId'], self.cid,
                           self.owner, final_wire.adapter)
        result = final.run_task(app_module.connect, follow['jobId'], self.cid,
                                self.owner, final_wire.adapter)
        self.assertEqual(result['status'], 'completed', result)
        with app_module.connect() as db:
            field = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual((field['reviewStatus'], field['lifecycleStatus']),
                             ('needs_review','draft'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_field_checks').fetchone()[0],0)
        self.assertTrue(final.run_task(app_module.connect, follow['jobId'], self.cid,
                                      self.owner, final_wire.adapter)['reused'])
        self.assertEqual(len(final_wire.generations),1)
        with app_module.connect() as db:
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                           (task['jobId'],)).fetchone()[0])
            payload['readSet']['qualifierVersion'] = 'unknown-v99'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                       (json.dumps(payload), task['jobId']))
            field = metadata.read_draft(db, self.entry, self.cid)['fields']['synopsis']
            self.assertEqual(field['lifecycleStatus'],'stale')
            self.assertIsNone(field['value'])

    def test_version_tamper_blocks_preflight(self):
        with app_module.connect() as db:
            task = synopsis.create_qualifier_batch_handoff_trial(
                db, self.entry, self.cid, self.owner, price=PRICE, batch_input_cap=1200)
            payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                           (task['jobId'],)).fetchone()[0])
            payload['readSet']['qualifierVersion'] = 'unknown-v99'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                       (json.dumps(payload), task['jobId']))
        wire = base.FixtureWire([])
        with self.assertRaises(ValueError):
            synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                        self.owner, wire.adapter)
        self.assertEqual((wire.counts,wire.generations),([],[]))

    def test_old_batch_business_path_rejects_public_output_with_known_usage(self):
        with app_module.connect() as db:
            task = synopsis.create_punctuation_batch_handoff_trial(
                db, self.entry, self.cid, self.owner, price=PRICE, batch_input_cap=1200)
        wire = base.FixtureWire(self.batch_outputs())
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        result = synopsis.run_real_trial(app_module.connect, task['jobId'], self.cid,
                                         self.owner, wire.adapter)
        self.assertEqual(result['status'],'failed',result)
        self.assertIn('synopsis_qualifier_dropped',result['reason'])
        with app_module.connect() as db:
            rows = db.execute('SELECT state,used_input_tokens,used_output_tokens,output_json '
                              'FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
                              (task['jobId'],)).fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual((rows[1]['used_input_tokens'],rows[1]['used_output_tokens']),
                             (100,100))
            self.assertIsNone(rows[1]['output_json'])

    def test_v3_batch_rejection_and_unknown_stop_with_reserves(self):
        for bad_output, missing_usage in (
                (change_unit(PUBLIC['output'], 3, '后再讨论是否提交', '后即可提交'), False),
                (PUBLIC['output'], True)):
            # Each branch gets a separate synthetic entry and budget ledger.
            with self.subTest(missing_usage=missing_usage):
                with app_module.connect() as db:
                    task = synopsis.create_qualifier_batch_handoff_trial(
                        db, self.entry, self.cid, self.owner, price=PRICE,
                        batch_input_cap=1200)
                wire = base.FixtureWire([
                    self.batch_outputs()[0], self.ctx.remap(bad_output, self.ctx.batches[1])],
                    missing_usage=missing_usage)
                synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                            self.owner, wire.adapter)
                outcome = synopsis.run_real_trial(app_module.connect, task['jobId'],
                                                  self.cid, self.owner, wire.adapter)
                self.assertEqual(outcome['status'], 'unknown' if missing_usage else 'failed')
                self.assertEqual(len(wire.generations), 1 if missing_usage else 2)
                with app_module.connect() as db:
                    budget = trials.trial_status(db, task['trialId'], self.cid)
                    self.assertGreater(budget['reserved_microusd'],0) if missing_usage else (
                        self.assertEqual(budget['reserved_microusd'],0))
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_job_field_results '
                                                'WHERE job_id=?',(task['jobId'],)).fetchone()[0],0)
                if missing_usage:
                    break

    def test_v3_version_tamper_after_dispatch_settles_without_output(self):
        with app_module.connect() as db:
            task = synopsis.create_qualifier_batch_handoff_trial(
                db, self.entry, self.cid, self.owner, price=PRICE, batch_input_cap=1200)
        def tamper(_):
            with app_module.connect() as db:
                payload = json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                                                (task['jobId'],)).fetchone()[0])
                payload['readSet']['qualifierVersion'] = 'unknown-v99'
                db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',
                           (json.dumps(payload),task['jobId']))
        wire = base.FixtureWire(self.batch_outputs(), on_generation=tamper)
        synopsis.prepare_real_trial(app_module.connect, task['jobId'], self.cid,
                                    self.owner, wire.adapter)
        outcome = synopsis.run_real_trial(app_module.connect, task['jobId'], self.cid,
                                          self.owner, wire.adapter)
        self.assertNotEqual(outcome['status'],'completed')
        self.assertEqual(len(wire.generations),1)
        with app_module.connect() as db:
            budget = trials.trial_status(db, task['trialId'], self.cid)
            self.assertGreater(budget['spent_estimate_microusd'],0)
            self.assertIsNone(db.execute('SELECT output_json FROM mv3_model_chunks '
                                          'WHERE job_id=? AND ordinal=0',
                                          (task['jobId'],)).fetchone()[0])

    def test_request_text_and_schema_unchanged_across_profiles(self):
        actual_batch = {'phase':'batch','sources':[{'id':'synthetic','speaker':'user',
                                                    'content':'合成原文。'}]}
        old = responses.synopsis_request_payload(MODEL,'batch',actual_batch,6144,
            profile=synopsis.REAL_PUNCTUATION_HANDOFF_PROFILE)
        new = responses.synopsis_request_payload(MODEL,'batch',actual_batch,6144,
            profile=synopsis.REAL_QUALIFIER_HANDOFF_PROFILE)
        self.assertEqual(old,new)
