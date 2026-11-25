"""A3.7 maintenance contract against synthetic SQLite only."""
import json
import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from test_memory_v2_a33_lexical import LexicalBase, SECRET
from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_lexical as lexical
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_maintenance as maintenance
from backend.app import memory_v2_processing as processing
from backend.app import memory_v2_vocabulary as vocabulary
from backend.app import memory_v2_time_projection as p2
from backend.app import memory_v2_temporal_digest_schema as temporal_schema

KEY = b'a37-synthetic-plan-signing-key-32-bytes-only'


class MaintenanceFlow(LexicalBase):
    def make_category_job(self):
        with app_module.connect() as db:
            vocabulary.seed(db, self.cid, 1)
        detail = self.entry('合成标题')
        with app_module.connect() as db:
            return processing.create_category_job(db, detail['entryId'], self.cid, 1)

    def lifecycle_snapshot(self):
        tables = ('mv3_jobs', 'mv3_model_chunks', 'mv3_a3_audit',
                  'mv3_lexical_state', 'mv3_lexical_terms', 'mv3_entry_fields')
        with app_module.connect() as db:
            return {table: [tuple(row) for row in db.execute(
                'SELECT * FROM ' + table + ' ORDER BY 1')] for table in tables}

    def test_bound_key_rejects_original_cross_target_counterexample(self):
        first_job = self.make_category_job()
        second_job = self.make_category_job()
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        offers = {item['jobId']: item['actions']['cancel'] for item in plan['jobs']
                  if item['jobId'] in (first_job, second_job)}
        self.assertNotEqual(offers[first_job]['requestKey'], offers[second_job]['requestKey'])
        first = maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                                target_id=first_job, plan_token=offers[first_job]['planToken'],
                                request_key=offers[first_job]['requestKey'], signing_secret=KEY)
        self.assertEqual(first['status'], 'cancelled')
        before = self.lifecycle_snapshot()
        with self.assertRaisesRegex(ValueError, 'maintenance_request_key_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                            target_id=second_job, plan_token=offers[second_job]['planToken'],
                            request_key=offers[first_job]['requestKey'], signing_secret=KEY)
        self.assertEqual(self.lifecycle_snapshot(), before)
        second = maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                                 target_id=second_job, plan_token=offers[second_job]['planToken'],
                                 request_key=offers[second_job]['requestKey'], signing_secret=KEY)
        self.assertEqual(second['status'], 'cancelled')
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_a3_audit "
                                        "WHERE event='maintenance_cancel' AND job_id IN (?,?)",
                                        (first_job, second_job)).fetchone()[0], 2)

    def test_bound_key_rejects_cross_action_and_lexical_exchange(self):
        job_id = self.make_category_job()
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='running',attempts=1,lease_token='old',lease_until=? WHERE id=?",
                       (time.time() - 30, job_id))
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        item = next(x for x in plan['jobs'] if x['jobId'] == job_id)
        cancel = item['actions']['cancel']
        recover = item['actions']['recover_local']
        lexical_offer = plan['lexical']
        self.assertEqual(len({cancel['requestKey'], recover['requestKey'],
                              lexical_offer['requestKey']}), 3)
        before = self.lifecycle_snapshot()
        attempted = (
            ('cancel', job_id, cancel['planToken'], recover['requestKey']),
            ('recover_local', job_id, recover['planToken'], cancel['requestKey']),
            ('cancel', job_id, cancel['planToken'], lexical_offer['requestKey']),
            ('lexical_rebuild', None, lexical_offer['actionToken'], cancel['requestKey']),
        )
        for action, target, token, key in attempted:
            with self.subTest(action=action, target=target):
                with self.assertRaisesRegex(ValueError, 'maintenance_request_key_invalid'):
                    maintenance.act(app_module.connect, self.cid, 1, action=action,
                                    target_id=target, plan_token=token, request_key=key,
                                    signing_secret=KEY)
                self.assertEqual(self.lifecycle_snapshot(), before)

    def test_missing_replaced_swapped_and_rotated_keys_reject_without_writes(self):
        first = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)['lexical']
        before = self.lifecycle_snapshot()
        for key in (None, '', '0' * 64):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, 'maintenance_request_key_invalid'):
                    maintenance.act(app_module.connect, self.cid, 1,
                                    action='lexical_rebuild', target_id=None,
                                    plan_token=first['actionToken'], request_key=key,
                                    signing_secret=KEY)
                self.assertEqual(self.lifecycle_snapshot(), before)
        self.entry('new synthetic dependency')
        second = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)['lexical']
        self.assertNotEqual(first['requestKey'], second['requestKey'])
        changed_before = self.lifecycle_snapshot()
        with self.assertRaisesRegex(ValueError, 'maintenance_request_key_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=second['actionToken'],
                            request_key=first['requestKey'], signing_secret=KEY)
        with self.assertRaisesRegex(ValueError, 'maintenance_plan_stale'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=first['actionToken'],
                            request_key=first['requestKey'], signing_secret=KEY)
        with self.assertRaisesRegex(ValueError, 'maintenance_plan_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=second['actionToken'],
                            request_key=second['requestKey'],
                            signing_secret=b'rotated-synthetic-signing-key-32-bytes-only')
        self.assertEqual(self.lifecycle_snapshot(), changed_before)

    def test_same_plan_cancel_concurrency_and_lost_ack_reuse_one_audit(self):
        job_id = self.make_category_job()
        item = next(x for x in maintenance.plan(app_module.connect, self.cid, 1,
                    signing_secret=KEY)['jobs'] if x['jobId'] == job_id)
        offer = item['actions']['cancel']
        barrier = Barrier(2)

        def submit(_):
            barrier.wait(timeout=10)
            return maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                                   target_id=job_id, plan_token=offer['planToken'],
                                   request_key=offer['requestKey'], signing_secret=KEY)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, range(2)))
        self.assertCountEqual([x['reused'] for x in outcomes], [False, True])
        retry = maintenance.act(
            app_module.connect, self.cid, 1, action='cancel', target_id=job_id,
            plan_token=offer['planToken'], request_key=offer['requestKey'],
            signing_secret=KEY)
        self.assertTrue(retry['reused'])
        with app_module.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_a3_audit WHERE job_id=? "
                                        "AND event='maintenance_cancel'", (job_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',
                                        (job_id,)).fetchone()[0], 0)

    def test_completed_lexical_retry_survives_new_process_and_revocation_blocks(self):
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)['lexical']
        first = maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                                target_id=None, plan_token=plan['actionToken'],
                                request_key=plan['requestKey'], signing_secret=KEY)
        self.assertEqual(first['generation'], 1)
        program = '''import json,sqlite3,sys
from backend.app import memory_v2_maintenance as maintenance
def connect():
    db=sqlite3.connect(sys.argv[1],timeout=30)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db
value=json.load(sys.stdin)
result=maintenance.act(connect,value["characterId"],value["ownerId"],
    action="lexical_rebuild",target_id=None,plan_token=value["planToken"],
    request_key=value["requestKey"],signing_secret=bytes.fromhex(value["secretHex"]))
print(json.dumps(result))
'''
        root = Path(__file__).resolve().parents[1]
        env = {name: value for name, value in os.environ.items()
               if not (name.upper().startswith('YOSAKI_') or any(part in name.upper()
                       for part in ('API_KEY', 'TOKEN', 'SECRET', 'CREDENTIAL', 'OPENAI',
                                    'ELEVENLABS', 'ANTHROPIC', 'PROXY')))}
        env['YOSAKI_DATA_DIR'] = str(app_module.DATA)
        response = subprocess.run([sys.executable, '-c', program, str(app_module.DB)],
                                  cwd=root, env=env, input=json.dumps({
                                      'characterId': self.cid, 'ownerId': 1,
                                      'planToken': plan['actionToken'],
                                      'requestKey': plan['requestKey'],
                                      'secretHex': KEY.hex()}), capture_output=True,
                                  text=True, timeout=30)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertTrue(json.loads(response.stdout)['reused'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT generation FROM mv3_lexical_state').fetchone()[0], 1)
            db.execute('UPDATE characters SET enabled=0 WHERE id=?', (self.cid,))
        with self.assertRaisesRegex(ValueError, 'role_unavailable'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=plan['actionToken'],
                            request_key=plan['requestKey'], signing_secret=KEY)

    def test_cross_owner_role_and_tampered_plan_cannot_reuse_key(self):
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)['lexical']
        before = self.lifecycle_snapshot()
        for character_id, owner_id in ((self.cid, 999), (self.cid + 99, 1)):
            with self.subTest(character_id=character_id, owner_id=owner_id):
                with self.assertRaises(ValueError):
                    maintenance.act(app_module.connect, character_id, owner_id,
                                    action='lexical_rebuild', target_id=None,
                                    plan_token=plan['actionToken'],
                                    request_key=plan['requestKey'], signing_secret=KEY)
                self.assertEqual(self.lifecycle_snapshot(), before)
        tampered = plan['actionToken'][:-1] + ('A' if plan['actionToken'][-1] != 'A' else 'B')
        with self.assertRaisesRegex(ValueError, 'maintenance_plan_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=tampered,
                            request_key=plan['requestKey'], signing_secret=KEY)
        with self.assertRaisesRegex(ValueError, 'maintenance_request_key_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=plan['requestKey'],
                            request_key=plan['actionToken'], signing_secret=KEY)
        self.assertEqual(self.lifecycle_snapshot(), before)

    def test_read_only_status_and_plan_do_not_install_or_repair(self):
        before = {}
        with app_module.connect() as db:
            for table in ('mv3_jobs', 'mv3_a3_audit', 'mv3_lexical_terms', 'mv3_lexical_state'):
                before[table] = db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        first = maintenance.status(app_module.connect, self.cid, 1)
        second = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        self.assertEqual(first['lexical']['state'], 'missing')
        self.assertEqual(second['lexical']['state'], 'missing')
        self.assertEqual(second['temporalSchema'], 'absent')
        self.assertIn('actionToken', second['lexical'])
        self.assertEqual(len(second['lexical']['requestKey']), 64)
        self.assertEqual(second['lexical'], maintenance.plan(
            app_module.connect, self.cid, 1, signing_secret=KEY)['lexical'])
        with app_module.connect() as db:
            for table, count in before.items():
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], count)

    def test_rebuild_plan_retry_and_candidate_review_gate(self):
        valid = self.entry('api 整理', changes={'keywords': [self.keyword('beta')]},
                           review=('keywords',), publish=True)
        pending = self.entry('草稿 API')
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        token = plan['lexical']['actionToken']
        first = maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                                target_id=None, plan_token=token,
                                request_key=plan['lexical']['requestKey'],
                                signing_secret=KEY)
        self.assertEqual(first['generation'], 1)
        retry = maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                                target_id=None, plan_token=token,
                                request_key=plan['lexical']['requestKey'],
                                signing_secret=KEY)
        self.assertTrue(retry['reused'])
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT generation FROM mv3_lexical_state').fetchone()[0], 1)
        result = self.search(query_text='api', purpose='candidate')
        self.assertEqual([x['entryId'] for x in result['items']], [valid['entryId']])
        self.assertNotEqual(valid['entryId'], pending['entryId'])

    def test_changed_authority_rejects_old_rebuild_plan(self):
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        self.entry('new unrelated entry')
        with self.assertRaisesRegex(ValueError, 'maintenance_plan_stale'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=plan['lexical']['actionToken'],
                            request_key=plan['lexical']['requestKey'], signing_secret=KEY)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())

    def test_unknown_hold_cancel_intent_and_scope_remain_blocked(self):
        job_id = self.make_category_job()
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?", (job_id,))
            db.execute("INSERT INTO mv3_a3_migration_holds VALUES(?,?,?,?)",
                       (job_id, 'paused', 'dispatch_or_settlement_unproven', '{}'))
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        item = next(x for x in plan['jobs'] if x['jobId'] == job_id)
        self.assertEqual(item['blockReason'], 'migration_hold')
        self.assertNotIn('recover_local', item['actions'])
        result = maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                                 target_id=job_id, plan_token=item['actions']['cancel']['planToken'],
                                 request_key=item['actions']['cancel']['requestKey'], signing_secret=KEY)
        self.assertEqual(result['status'], 'unknown')
        again = maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                                target_id=job_id, plan_token=item['actions']['cancel']['planToken'],
                                request_key=item['actions']['cancel']['requestKey'], signing_secret=KEY)
        self.assertTrue(again['reused'])
        with app_module.connect() as db:
            row = db.execute('SELECT status,cancel_intent FROM mv3_jobs WHERE id=?', (job_id,)).fetchone()
            self.assertEqual(tuple(row), ('unknown', 1))
            self.assertIsNotNone(db.execute('SELECT 1 FROM mv3_a3_migration_holds WHERE job_id=?',
                                             (job_id,)).fetchone())
            with self.assertRaises(ValueError):
                processing.create_category_job(db, json.loads(db.execute(
                    'SELECT payload_json FROM mv3_jobs WHERE id=?', (job_id,)).fetchone()[0])['targetId'],
                    self.cid, 1)

    def test_expired_local_worker_recovery_is_paused_and_idempotent(self):
        job_id = self.make_category_job()
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='running',attempts=1,lease_token='old',lease_until=? WHERE id=?",
                       (time.time() - 30, job_id))
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        item = next(x for x in plan['jobs'] if x['jobId'] == job_id)
        self.assertIn('recover_local', item['actions'])
        token = item['actions']['recover_local']['planToken']
        request_key = item['actions']['recover_local']['requestKey']
        result = maintenance.act(app_module.connect, self.cid, 1, action='recover_local',
                                 target_id=job_id, plan_token=token,
                                 request_key=request_key, signing_secret=KEY)
        self.assertEqual(result['status'], 'paused')
        again = maintenance.act(app_module.connect, self.cid, 1, action='recover_local',
                                target_id=job_id, plan_token=token,
                                request_key=request_key, signing_secret=KEY)
        self.assertTrue(again['reused'])
        with app_module.connect() as db:
            row = db.execute('SELECT status,lease_token FROM mv3_jobs WHERE id=?', (job_id,)).fetchone()
            self.assertEqual(tuple(row), ('paused', None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mv3_a3_audit WHERE job_id=? AND event='maintenance_recover_local'",
                                        (job_id,)).fetchone()[0], 1)

    def test_forged_or_cross_role_plan_is_rejected_before_side_effect(self):
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        token = plan['lexical']['actionToken']
        request_key = plan['lexical']['requestKey']
        with self.assertRaisesRegex(ValueError, 'maintenance_plan_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=token[:-3] + 'AAA',
                            request_key=request_key, signing_secret=KEY)
        with self.assertRaisesRegex(ValueError, 'maintenance_plan_invalid'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token='a',
                            request_key=request_key, signing_secret=KEY)
        with self.assertRaisesRegex(ValueError, 'role_unavailable'):
            maintenance.act(app_module.connect, self.cid + 99, 1, action='lexical_rebuild',
                            target_id=None, plan_token=token, request_key=request_key,
                            signing_secret=KEY)
        with self.assertRaisesRegex(ValueError, 'role_unavailable'):
            maintenance.status(app_module.connect, self.cid + 99, 1)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())

    def test_partial_index_structure_refuses_maintenance(self):
        with app_module.connect() as db:
            db.execute('DROP INDEX ix_mv3_lexical_entry')
        with self.assertRaisesRegex(ValueError, 'a33_schema_invalid'):
            maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())


    def test_index_corruption_is_detected_and_explicitly_rebuilt(self):
        self.entry('api', publish=True, changes={'keywords': [self.keyword('beta')]},
                   review=('keywords',))
        first = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                        target_id=None, plan_token=first['lexical']['actionToken'],
                        request_key=first['lexical']['requestKey'], signing_secret=KEY)
        with app_module.connect() as db:
            db.execute("""UPDATE mv3_lexical_terms SET normalized_term='corrupt'
                WHERE character_id=? AND normalized_term='api'""", (self.cid,))
        with self.assertRaisesRegex(ValueError, 'lexical_index_invalid'):
            self.search(query_text='api')
        repair = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        self.assertEqual(repair['lexical']['state'], 'invalid')
        fixed = maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                                target_id=None, plan_token=repair['lexical']['actionToken'],
                                request_key=repair['lexical']['requestKey'], signing_secret=KEY)
        self.assertEqual(fixed['generation'], 2)
        self.assertEqual(maintenance.status(app_module.connect, self.cid, 1)['lexical']['state'],
                         'valid')

    def test_unknown_chunk_keeps_reservation_and_blocks_rebuild(self):
        lexical_plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        job_id = self.make_category_job()
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?", (job_id,))
            db.execute("""INSERT INTO mv3_model_chunks
                (job_id,ordinal,request_id,input_hash,input_json,state,reserved_tokens,
                 reserved_cost_units,dispatch_attempt_id)
                VALUES(?,0,'synthetic-request','input','{}','possibly_dispatched',50,50,
                       'synthetic-attempt')""", (job_id,))
        state = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        self.assertEqual(state['maintenanceBlockReason'], 'unknown_or_hold')
        self.assertNotIn('actionToken', state['lexical'])
        item = next(x for x in state['jobs'] if x['jobId'] == job_id)
        self.assertEqual(item['blockReason'], 'request')
        with self.assertRaisesRegex(ValueError, 'maintenance_blocked_unknown'):
            maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=lexical_plan['lexical']['actionToken'],
                            request_key=lexical_plan['lexical']['requestKey'], signing_secret=KEY)
        maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                        target_id=job_id, plan_token=item['actions']['cancel']['planToken'],
                        request_key=item['actions']['cancel']['requestKey'], signing_secret=KEY)
        with app_module.connect() as db:
            row = db.execute('SELECT state,reserved_tokens,reserved_cost_units,used_cost_units '
                             'FROM mv3_model_chunks WHERE job_id=?', (job_id,)).fetchone()
            self.assertEqual(tuple(row), ('possibly_dispatched', 50, 50, None))
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (job_id,)).fetchone()[0], 'unknown')

    def test_unknown_created_after_plan_check_blocks_index_publish(self):
        job_id = self.make_category_job()
        lexical_offer = maintenance.plan(app_module.connect, self.cid, 1,
                                         signing_secret=KEY)['lexical']
        token = lexical_offer['actionToken']
        original = lexical.rebuild

        def concurrent_unknown(*args, **kwargs):
            with app_module.connect() as db:
                db.execute("UPDATE mv3_jobs SET status='unknown' WHERE id=?", (job_id,))
                db.execute("INSERT INTO mv3_a3_migration_holds VALUES(?,?,?,?)",
                           (job_id, 'queued', 'synthetic_concurrent_hold', '{}'))
            return original(*args, **kwargs)

        with patch.object(lexical, 'rebuild', side_effect=concurrent_unknown):
            with self.assertRaisesRegex(ValueError, 'maintenance_blocked_unknown'):
                maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                                target_id=None, plan_token=token,
                                request_key=lexical_offer['requestKey'],
                                signing_secret=KEY)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_lexical_state').fetchone())
            self.assertEqual(db.execute('SELECT status FROM mv3_jobs WHERE id=?',
                                        (job_id,)).fetchone()[0], 'unknown')

    def test_running_local_cancel_only_records_intent(self):
        job_id = self.make_category_job()
        with app_module.connect() as db:
            db.execute("UPDATE mv3_jobs SET status='running',lease_token='worker',lease_until=? WHERE id=?",
                       (time.time() + 100, job_id))
        item = next(x for x in maintenance.plan(app_module.connect, self.cid, 1,
                    signing_secret=KEY)['jobs'] if x['jobId'] == job_id)
        result = maintenance.act(app_module.connect, self.cid, 1, action='cancel',
                                 target_id=job_id, plan_token=item['actions']['cancel']['planToken'],
                                 request_key=item['actions']['cancel']['requestKey'], signing_secret=KEY)
        self.assertEqual(result['status'], 'running')
        with app_module.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT status,cancel_intent FROM mv3_jobs WHERE id=?',
                                              (job_id,)).fetchone()), ('running', 1))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_entry_fields WHERE name=?',
                                        ('categories',)).fetchone()[0], 0)

    def test_source_change_rejects_diagnostic_and_stale_index_content(self):
        self.entry('api', changes={'keywords': [self.keyword('beta')]},
                   review=('keywords',), publish=True)
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        maintenance.act(app_module.connect, self.cid, 1, action='lexical_rebuild',
                        target_id=None, plan_token=plan['lexical']['actionToken'],
                        request_key=plan['lexical']['requestKey'], signing_secret=KEY)
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?',
                       ('synthetic source changed', self.user))
        with self.assertRaisesRegex(ValueError, 'lexical_authority_invalid') as error:
            maintenance.status(app_module.connect, self.cid, 1)
        self.assertNotIn('synthetic source changed', str(error.exception))
        with self.assertRaises(ValueError):
            self.search(query_text='api', purpose='candidate')

    def test_rebuild_rechecks_authority_after_read_snapshot(self):
        self.entry('api')
        plan = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        calls = 0
        def racing_connect():
            nonlocal calls
            calls += 1
            if calls == 4:
                self.entry('new snapshot member')
            return app_module.connect()
        with self.assertRaisesRegex(ValueError, 'lexical_rebuild_snapshot_changed'):
            maintenance.act(racing_connect, self.cid, 1, action='lexical_rebuild',
                            target_id=None, plan_token=plan['lexical']['actionToken'],
                            request_key=plan['lexical']['requestKey'], signing_secret=KEY)
        with app_module.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM mv3_lexical_state').fetchone())

    def test_temporal_proposals_are_read_only_and_partial_binding_refuses(self):
        with app_module.connect() as db:
            p2.init_db(db)
        with app_module.connect() as db:
            temporal_schema.migrate(db)
            before_jobs = db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0]
            before_versions = db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0]
        result = maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)
        self.assertEqual(result['temporalSchema'], 'valid')
        self.assertIsInstance(result['digestProposals'], list)
        with app_module.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_jobs').fetchone()[0], before_jobs)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mv3_versions').fetchone()[0], before_versions)
            db.execute('DROP INDEX ix_mv3_time_digest_v2_node')
        with self.assertRaisesRegex(ValueError, 'a36_schema_invalid'):
            maintenance.status(app_module.connect, self.cid, 1)

    def test_partial_processing_schema_rejects_maintenance(self):
        with app_module.connect() as db:
            db.execute('DROP TRIGGER mv3_a3_parent_guard')
        with self.assertRaisesRegex(ValueError, 'a3_schema_invalid'):
            maintenance.plan(app_module.connect, self.cid, 1, signing_secret=KEY)


class MaintenanceUnmigrated(EntryBase):
    def test_absent_a3_a33_a36_are_reported_without_install(self):
        before = None
        with app_module.connect() as db:
            before = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        status = maintenance.status(app_module.connect, self.cid, 1)
        self.assertEqual(status['processingSchema'], 'absent')
        self.assertEqual(status['lexical']['state'], 'schema_missing')
        self.assertEqual(status['temporalSchema'], 'absent')
        with app_module.connect() as db:
            after = sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
