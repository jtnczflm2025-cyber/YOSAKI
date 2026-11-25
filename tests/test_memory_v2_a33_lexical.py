"""Synthetic A3.3 Lexical contract; no real archive, key, provider or route."""
import sqlite3
import unittest
from unittest.mock import patch

from test_memory_v2_entries import EntryBase, app_module
from backend.app import memory_v2_metadata as metadata
from backend.app import memory_v2_metadata_jobs as legacy_jobs
from backend.app import memory_v2_jobs as jobs
from backend.app import memory_v2_processing_schema as a3_schema
from backend.app import memory_v2_tag_schema as tag_schema
from backend.app import memory_v2_lexical_schema as schema
from backend.app import memory_v2_lexical as lexical
from backend.app import memory_v2_vocabulary as vocabulary

SECRET = b'synthetic-lexical-signing-secret-32-bytes-only'


class LexicalBase(EntryBase):
    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            metadata.init_db(db)
            legacy_jobs.init_db(db)
        with app_module.connect() as db:
            a3_schema.migrate(db)
            tag_schema.migrate(db)
            schema.migrate(db)

    def entry(self, title='API 整理', *, changes=None, review=(), publish=False):
        with app_module.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM mv3_entries').fetchone()[0]
            detail = metadata.create_draft(
                db, self.cid, self.refs(), title, 1, f'lexical-{title}-{count}')
            entry_id = detail['entryId']
            detail = metadata.review_field(
                db, entry_id, self.cid, 'title', detail['fields']['title']['token'], 1)
            if changes:
                detail = metadata.revise(
                    db, entry_id, self.cid, detail['versionId'], changes, 1)
            for name in review:
                detail = metadata.review_field(
                    db, entry_id, self.cid, name, detail['fields'][name]['token'], 1)
            if publish:
                detail = metadata.publish(db, entry_id, self.cid, detail['versionId'], 1)
            return detail

    def keyword(self, text='api', *, kind='literal', weight=1, vocab_id=None):
        item = {'text': text, 'language': 'und', 'kind': kind, 'weight': weight,
                'field': 'raw', 'evidence': [
                    {'kind': 'message', 'id': str(self.user), 'start': 0, 'end': 1}]}
        if vocab_id:
            item['vocabularyId'] = vocab_id
        return item

    def search(self, **kwargs):
        return lexical.search(
            app_module.connect, self.cid, 1, signing_secret=SECRET,
            purpose=kwargs.pop('purpose', 'management'), **kwargs)

    def rebuild(self):
        return lexical.rebuild(app_module.connect, self.cid, 1)


class LexicalMigration(LexicalBase):
    def test_explicit_idempotent_shape_and_no_main_install(self):
        with app_module.connect() as db:
            self.assertEqual(schema.state(db), 'valid')
            self.assertFalse(schema.migrate(db))
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())
        self.assertNotIn('memory_v2_lexical_schema', app_module.init_db.__code__.co_names)

    def test_partial_structure_rejects_search_and_legacy_write(self):
        detail = self.entry('api')
        with app_module.connect() as db:
            db.execute('DROP INDEX ix_mv3_lexical_entry')
            self.assertEqual(schema.state(db), 'invalid')
            with self.assertRaisesRegex(ValueError, 'a33_schema_invalid'):
                legacy_jobs.enqueue(db, detail['entryId'], self.cid, 1, 'METADATA')
        with self.assertRaisesRegex(ValueError, 'a33_schema_invalid'):
            self.search(query_text='api')

    def test_migration_failure_rolls_back(self):
        with app_module.connect() as db:
            for name in ('ix_mv3_lexical_entry', 'ix_mv3_lexical_term',
                         'mv3_lexical_terms', 'mv3_lexical_state', 'mv3_a33_schema'):
                db.execute('DROP ' + ('INDEX ' if name.startswith('ix_') else 'TABLE ') + name)
            self.assertEqual(schema.state(db), 'absent')
        with patch.object(schema, 'DDL', {**schema.DDL, 'bad': 'CREATE TABLE mv3_a33_schema(x)'}):
            with app_module.connect() as db:
                with self.assertRaises(sqlite3.DatabaseError):
                    schema.migrate(db)
        with app_module.connect() as db:
            self.assertEqual(schema.state(db), 'absent')
            self.assertTrue(schema.migrate(db))


class LexicalSearch(LexicalBase):
    def test_three_purposes_draft_and_review_gate(self):
        first = self.entry('API 整理', changes={'keywords': [self.keyword('api')]})
        self.rebuild()
        self.assertEqual(self.search(query_text='api')['total'], 1)
        self.assertEqual(self.search(query_text='api', purpose='candidate')['total'], 0)
        draft = self.search(query_text='api', purpose='draft_management')
        self.assertEqual(draft['total'], 1)
        self.assertEqual(draft['items'][0]['fieldStates']['keywords'], 'draft')
        with app_module.connect() as db:
            checked = metadata.review_field(
                db, first['entryId'], self.cid, 'keywords',
                first['fields']['keywords']['token'], 1)
            metadata.publish(db, first['entryId'], self.cid, checked['versionId'], 1)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='api')
        self.rebuild()
        candidate = self.search(query_text='api', purpose='candidate')
        self.assertEqual(candidate['total'], 1)
        self.assertFalse(candidate['items'][0]['formalContext'])

    def test_nfkc_direct_and_global_best_are_exclusive(self):
        self.entry('ＡＰＩ', changes={'keywords': [self.keyword('api')]},
                   review=('keywords',), publish=True)
        self.rebuild()
        result = self.search(query_text='api', purpose='candidate')['items'][0]
        self.assertEqual(result['matchCounts'], {'direct': 1, 'alias': 0, 'expansion': 0})
        self.assertEqual(result['scoreMilli'], 6000)
        self.assertEqual({hit['field'] for hit in result['matches']}, {'title', 'keywords'})
        title_hit = next(hit for hit in result['matches'] if hit['field'] == 'title')
        self.assertEqual(title_hit['matchRange'], [0, 3])
        self.assertEqual(title_hit['matchSource'], 'field_value')

    def test_alias_and_reviewed_expansion(self):
        with app_module.connect() as db:
            tag = vocabulary.write(
                db, self.cid, 'tag', '接口', 'und', 1,
                definition='合成接口主题', aliases=['api'])
        self.entry('接口主题', changes={
            'tags': [{'id': tag['vocabulary_id'], 'evidence': [
                {'kind': 'message', 'id': str(self.user), 'start': 0, 'end': 1}]}],
            'keywords': [self.keyword('endpoint', kind='semantic_expansion')]},
            review=('tags', 'keywords'), publish=True)
        self.rebuild()
        alias = self.search(query_text='api', purpose='candidate')['items'][0]
        self.assertEqual(alias['matchCounts']['alias'], 1)
        expansion = self.search(query_text='endpoint', purpose='candidate')['items'][0]
        self.assertEqual(expansion['matchCounts']['expansion'], 1)

    def test_phrase_stays_within_one_value(self):
        item = self.entry('foo bar', changes={'keywords': [
            self.keyword('foo'), self.keyword('bar')]},
            review=('keywords',), publish=True)
        self.rebuild()
        phrase = self.search(phrases=['foo bar'])
        self.assertEqual(phrase['total'], 1)
        self.assertEqual(phrase['items'][0]['matches'][0]['matchRange'], [0, 7])
        with app_module.connect() as db:
            detail = metadata.read_draft(db, item['entryId'], self.cid)
            metadata.revise(db, item['entryId'], self.cid, detail['versionId'],
                            {'title': 'other title'}, 1)
        self.rebuild()
        self.assertEqual(self.search(phrases=['foo bar'])['total'], 0)

    def test_nfkc_expansion_phrase_maps_to_original_range(self):
        self.entry('㍿ ＡＰＩ', publish=True)
        self.rebuild()
        result = self.search(phrases=['株式会社 api'])
        self.assertEqual(result['total'], 1)
        match = result['items'][0]['matches'][0]
        self.assertEqual(match['matchRange'], [0, 5])
        self.assertEqual(match['matchSource'], 'field_value')

    def test_query_modes_dedup_and_weight_rejection(self):
        self.entry('alpha', changes={'keywords': [self.keyword('beta', weight=0.3335)]},
                   review=('keywords',), publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha beta', query_mode='ALL')['total'], 1)
        self.assertEqual(self.search(query_text='alpha beta gamma', query_mode='ANY')['total'], 1)
        self.assertEqual(self.search(query_text='alpha beta gamma', query_mode='ALL')['total'], 0)
        self.assertEqual(self.search(query_text='alpha alpha')['items'][0]['matchCounts']['direct'], 1)
        for bad in (True, float('nan'), float('inf'), -1, 1.1, '1'):
            with self.assertRaisesRegex(ValueError, 'keyword_weight_invalid'):
                lexical._weight(bad)

    def test_authorization_and_stale_source_do_not_leak(self):
        self.entry('secretword', changes={'keywords': [self.keyword('otherword')]})
        self.rebuild()
        with self.assertRaisesRegex(ValueError, 'role_unavailable'):
            lexical.search(app_module.connect, self.cid, 999, purpose='draft_management',
                           query_text='secretword', signing_secret=SECRET)
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?', ('changed', self.user))
        with self.assertRaisesRegex(ValueError, 'lexical_authority_invalid'):
            self.search(query_text='secretword', purpose='draft_management')

    def test_unmatched_entry_and_review_change_invalidate(self):
        first = self.entry('alpha', publish=True)
        self.rebuild()
        self.entry('unrelated', publish=True)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha')
        self.rebuild()
        with app_module.connect() as db:
            db.execute('DELETE FROM mv3_entry_field_checks WHERE field_id=?',
                       (first['fields']['title']['fieldId'],))
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha')

    def test_signed_cursor_same_score_and_replay_guards(self):
        for _ in range(4):
            self.entry('alpha', publish=True)
        self.rebuild()
        ids = []
        cursor = None
        for _ in range(4):
            result = self.search(query_text='alpha', limit=1, cursor=cursor)
            ids.extend(row['entryId'] for row in result['items'])
            cursor = result['nextCursor']
        self.assertIsNone(cursor)
        self.assertEqual(ids, sorted(set(ids)))
        first = self.search(query_text='alpha', limit=1)['nextCursor']
        for kwargs in ({'query_text': 'beta'}, {'purpose': 'candidate', 'query_text': 'alpha'}):
            with self.assertRaisesRegex(ValueError, 'lexical_cursor_invalid'):
                self.search(cursor=first, limit=1, **kwargs)
        with self.assertRaisesRegex(ValueError, 'lexical_cursor_invalid'):
            self.search(query_text='alpha', cursor=first[:-2]+'xx', limit=1)
        with self.assertRaisesRegex(ValueError, 'lexical_cursor_invalid'):
            lexical.search(app_module.connect, self.cid, 1, purpose='management',
                           query_text='alpha', cursor=first, limit=1, signing_secret=b'y'*32)
        with self.assertRaisesRegex(ValueError, 'lexical_signing_key_required'):
            lexical.search(app_module.connect, self.cid, 1, purpose='management', query_text='alpha')

    def test_index_row_damage_is_rejected(self):
        self.entry('alpha', publish=True)
        self.rebuild()
        with app_module.connect() as db:
            db.execute('DELETE FROM mv3_lexical_terms WHERE normalized_term=?', ('alpha',))
        with self.assertRaisesRegex(ValueError, 'lexical_index_invalid'):
            self.search(query_text='alpha')

    def test_excess_projection_rows_are_bounded_before_hashing(self):
        self.entry('alpha', publish=True)
        self.rebuild()
        with app_module.connect() as db:
            db.execute('''INSERT INTO mv3_lexical_terms
                SELECT character_id,generation,entry_id,field_name,field_id,field_token,
                       value_ordinal,'synthetic-extra',match_kind,vocabulary_id,evidence_hash
                FROM mv3_lexical_terms WHERE character_id=? LIMIT 1''', (self.cid,))
        with patch.object(lexical, 'MAX_TERMS', 1):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')

    def test_rebuild_snapshot_change_keeps_old_generation(self):
        self.entry('alpha', publish=True)
        first = self.rebuild()
        original = lexical._authority
        calls = 0
        def changed(db, character_id, deadline=None):
            nonlocal calls
            calls += 1
            result = original(db, character_id, deadline)
            return ('changed-snapshot', *result[1:]) if calls == 2 else result
        with patch.object(lexical, '_authority', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'lexical_rebuild_snapshot_changed'):
                self.rebuild()
        with app_module.connect() as db:
            state = db.execute('SELECT generation,authority_hash FROM mv3_lexical_state WHERE character_id=?',
                               (self.cid,)).fetchone()
            self.assertEqual(state['generation'], first['generation'])
            self.assertEqual(state['authority_hash'], first['authorityHash'])

    def test_category_or_and_tag_any_all(self):
        with app_module.connect() as db:
            c1 = vocabulary.write(db, self.cid, 'category', '技术', 'zh', 1,
                                  dimension='domain', definition='合成技术')
            c2 = vocabulary.write(db, self.cid, 'category', '工作', 'zh', 1,
                                  dimension='domain', definition='合成工作')
            c3 = vocabulary.write(db, self.cid, 'category', '技术讨论', 'zh', 1,
                                  dimension='form', definition='合成讨论')
            t1 = vocabulary.write(db, self.cid, 'tag', 'API', 'und', 1, definition='合成 API')
            t2 = vocabulary.write(db, self.cid, 'tag', 'SQL', 'und', 1, definition='合成 SQL')
        point = [{'kind': 'message', 'id': str(self.user), 'start': 0, 'end': 1}]
        item = self.entry('alpha', changes={
            'categories': [{'id': x['vocabulary_id'], 'evidence': point} for x in (c1, c3)],
            'tags': [{'id': t1['vocabulary_id'], 'evidence': point}]},
            review=('categories', 'tags'), publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', categories={
            'domain': [c1['vocabulary_id'], c2['vocabulary_id']],
            'form': [c3['vocabulary_id']]} )['total'], 1)
        self.assertEqual(self.search(query_text='alpha', categories={
            'domain': [c2['vocabulary_id']]} )['total'], 0)
        self.assertEqual(self.search(query_text='alpha', tag_ids=[
            t1['vocabulary_id'], t2['vocabulary_id']], tag_mode='ANY')['total'], 1)
        self.assertEqual(self.search(query_text='alpha', tag_ids=[
            t1['vocabulary_id'], t2['vocabulary_id']], tag_mode='ALL')['total'], 0)
        self.assertEqual(item['fields']['categories']['lifecycleStatus'], 'available')

    def test_stale_draft_state_can_be_browsed_without_old_match(self):
        item = self.entry('alpha', changes={'keywords': [self.keyword('oldword')]})
        self.rebuild()
        with app_module.connect() as db:
            field_id = item['fields']['keywords']['fieldId']
            db.execute("UPDATE mv3_entry_fields SET rule_version='keyword-literal-raw-v0' WHERE id=?",
                       (field_id,))
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='oldword', purpose='draft_management')
        self.rebuild()
        states = self.search(purpose='draft_management', draft_states={'keywords': 'stale'})
        self.assertEqual(states['total'], 1)
        self.assertEqual(states['items'][0]['matches'], [])
        self.assertEqual(self.search(query_text='oldword', purpose='draft_management')['total'], 0)

    def test_cursor_detects_unmatched_delete_publish_suppression_and_vocab_changes(self):
        first = self.entry('alpha', publish=True)
        second = self.entry('alpha', publish=True)
        with app_module.connect() as db:
            tag = vocabulary.write(db, self.cid, 'tag', '接口', 'und', 1,
                                   definition='合成接口', aliases=['api'])
        self.rebuild()
        cursor = self.search(query_text='alpha', limit=1)['nextCursor']
        with app_module.connect() as db:
            db.execute('DELETE FROM mv3_entries WHERE id=?', (second['entryId'],))
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha', cursor=cursor, limit=1)
        self.rebuild()
        third = self.entry('other', publish=False)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha')
        self.rebuild()
        with app_module.connect() as db:
            metadata.publish(db, third['entryId'], self.cid, third['versionId'], 1)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha')
        self.rebuild()
        with app_module.connect() as db:
            detail = metadata.read_draft(db, first['entryId'], self.cid)
            metadata.revise(db, first['entryId'], self.cid, detail['versionId'], {'title': None}, 1)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha')
        self.rebuild()
        with app_module.connect() as db:
            vocabulary.write(db, self.cid, 'tag', '接口', 'und', 1,
                             vocabulary_id=tag['vocabulary_id'], expected_version=tag['version'],
                             definition='合成接口', aliases=['api', 'endpoint'])
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha')

    def test_cross_role_cursor_and_alias_collision_reject(self):
        self.entry('alpha', publish=True)
        with app_module.connect() as db:
            db.execute("""INSERT INTO characters(name,created_at,updated_at)
                VALUES('other-synthetic-role','2026-01-01','2026-01-01')""")
            second_role = db.execute('SELECT MAX(id) FROM characters').fetchone()[0]
        self.entry('alpha', publish=True)
        self.rebuild()
        cursor = self.search(query_text='alpha', limit=1)['nextCursor']
        lexical.rebuild(app_module.connect, second_role, 1)
        with self.assertRaisesRegex(ValueError, 'lexical_cursor_invalid'):
            lexical.search(app_module.connect, second_role, 1, purpose='management',
                           query_text='alpha', cursor=cursor, signing_secret=SECRET)
        with app_module.connect() as db:
            first = vocabulary.write(db, self.cid, 'tag', '接口', 'und', 1,
                                     definition='合成 A', aliases=['api'])
            second = vocabulary.write(db, self.cid, 'tag', '数据库', 'und', 1,
                                      definition='合成 B')
            db.execute('UPDATE mv3_vocabulary_versions SET aliases_json=? WHERE vocabulary_id=?',
                       ('["api"]', second['vocabulary_id']))
        with self.assertRaisesRegex(ValueError, 'lexical_alias_conflict'):
            self.rebuild()

    def test_projection_rebuild_removes_old_generation(self):
        self.entry('alpha', publish=True)
        first = self.rebuild()
        second = self.rebuild()
        self.assertEqual(second['generation'], first['generation'] + 1)
        with app_module.connect() as db:
            generations = [row[0] for row in db.execute(
                'SELECT DISTINCT generation FROM mv3_lexical_terms WHERE character_id=?',
                (self.cid,))]
            self.assertEqual(generations, [second['generation']])
        self.assertEqual(self.search(query_text='alpha')['total'], 1)

    def test_bounded_resources_and_malformed_filter_reject(self):
        entry = self.entry('alpha', publish=True)
        self.rebuild()
        for categories in ({'domain': [['unhashable']]}, {'domain': ['missing-id']}):
            with self.assertRaisesRegex(ValueError, 'lexical_filter_invalid'):
                self.search(query_text='alpha', categories=categories)
        with patch.object(lexical, 'MAX_ENTRIES', 0):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        with patch.object(lexical, 'MAX_AUTHORITY_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        with patch.object(lexical, 'MAX_TERMS', 0):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        with patch.object(lexical, 'MAX_OPERATION_SECONDS', 0):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        original_budget = lexical._budget
        checks = 0
        def refuse_during_scan(deadline):
            nonlocal checks
            checks += 1
            if checks > 1:
                raise ValueError('lexical_resource_limit')
            return original_budget(deadline)
        with patch.object(lexical, '_budget', side_effect=refuse_during_scan):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        self.assertGreater(checks, 1)
        with app_module.connect() as db:
            term = vocabulary.write(db, self.cid, 'tag', 'synthetic', 'und', 1,
                                    definition='synthetic vocabulary')
        with patch.object(lexical, 'MAX_VOCAB_ROWS', 0):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        with patch.object(lexical, 'MAX_VOCAB_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        with app_module.connect() as db:
            db.execute('''INSERT INTO mv3_entry_tag_decisions
                (id,character_id,entry_id,tag_id,revision,action,source_token,
                 owner_id,created_at)
                VALUES(?,?,?,?,1,'suppress',?,1,'2026-01-01')''',
                ('synthetic-decision', self.cid, entry['entryId'],
                 term['vocabulary_id'], entry['sourceToken']))
        with patch.object(lexical, 'MAX_DECISION_ROWS', 0):
            with self.assertRaisesRegex(ValueError, 'lexical_resource_limit'):
                self.search(query_text='alpha')
        with self.assertRaisesRegex(ValueError, 'lexical_query_empty'):
            self.search(query_text='')


class LexicalCandidateQualification(LexicalBase):
    def test_original_zero_weight_stop_reproducer(self):
        item = self.entry('alpha', changes={'keywords': [self.keyword('beta', weight=0)]},
                          review=('keywords',), publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 0)
        with app_module.connect() as db:
            retained = metadata.read_draft(db, item['entryId'], self.cid)
            self.assertEqual(retained['fields']['keywords']['value'][0]['weight'], 0)
            self.assertEqual(retained['fields']['keywords']['reviewStatus'], 'human_checked')
        self.assertEqual(self.search(query_text='alpha', purpose='management')['total'], 1)
        self.assertEqual(self.search(query_text='beta', purpose='management')['total'], 0)

    def test_all_zero_and_empty_keyword_lists_do_not_qualify(self):
        self.entry('alpha', changes={'keywords': [
            self.keyword('beta', weight=0), self.keyword('gamma', weight=0)]},
            review=('keywords',), publish=True)
        self.entry('alpha', changes={'keywords': []}, publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='management')['total'], 2)
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 0)

    def test_mixed_zero_and_positive_keyword_is_eligible_without_keyword_query_hit(self):
        self.entry('alpha', changes={'keywords': [
            self.keyword('zero', weight=0), self.keyword('beta', weight=1)]},
            review=('keywords',), publish=True)
        self.rebuild()
        alpha = self.search(query_text='alpha', purpose='candidate')
        self.assertEqual(alpha['total'], 1)
        self.assertEqual({hit['field'] for hit in alpha['items'][0]['matches']}, {'title'})
        self.assertEqual(self.search(query_text='zero', purpose='candidate')['total'], 0)
        self.assertEqual(self.search(query_text='beta', purpose='candidate')['total'], 1)

    def test_tiny_positive_weight_uses_same_half_even_rounding_as_search(self):
        for weight in (0.0004, 0.0005, 0.0005001):
            self.entry('alpha', changes={'keywords': [self.keyword('beta', weight=weight)]},
                       review=('keywords',), publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 1)
        self.assertEqual(self.search(query_text='beta', purpose='candidate')['total'], 1)
        self.assertEqual(lexical._weight(0.0005), 0)
        self.assertEqual(lexical._weight(0.0005001), 1)

    def test_blank_keyword_and_invalid_weight_cannot_be_silent_qualification(self):
        self.entry('alpha', changes={'keywords': [self.keyword('   ', weight=1)]},
                   review=('keywords',), publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 0)
        for bad in (True, '1', float('nan'), float('inf'), -1, 1.01):
            with self.assertRaisesRegex(ValueError, 'keyword_weight_invalid'):
                lexical._weight(bad)

    def test_pending_keyword_does_not_qualify_and_source_change_rejects(self):
        item = self.entry('alpha', changes={'keywords': [self.keyword('beta', weight=1)]},
                          publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 0)
        with app_module.connect() as db:
            metadata.review_field(db, item['entryId'], self.cid, 'keywords',
                                  item['fields']['keywords']['token'], 1)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha', purpose='candidate')
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 1)
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?', ('changed', self.user))
        with self.assertRaisesRegex(ValueError, 'lexical_authority_invalid'):
            self.search(query_text='alpha', purpose='candidate')

    def test_zero_keyword_does_not_veto_independent_synopsis_qualification(self):
        self.entry('alpha', changes={
            'keywords': [self.keyword('beta', weight=0)],
            'synopsis': 'synthetic summary'},
            review=('keywords', 'synopsis'), publish=True)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 1)

    def test_title_only_or_unpublished_entry_cannot_recall(self):
        self.entry('alpha', publish=True)
        self.entry('alpha', changes={'keywords': [self.keyword('beta', weight=1)]},
                   review=('keywords',), publish=False)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='management')['total'], 2)
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 0)

    def test_weight_change_invalidates_old_cursor_and_new_projection(self):
        items = [self.entry('alpha', changes={'keywords': [self.keyword('beta', weight=1)]},
                            review=('keywords',), publish=True) for _ in range(2)]
        self.rebuild()
        page = self.search(query_text='alpha', purpose='candidate', limit=1)
        self.assertEqual(page['total'], 2)
        cursor = page['nextCursor']
        with app_module.connect() as db:
            detail = metadata.read_draft(db, items[1]['entryId'], self.cid)
            revised = metadata.revise(db, items[1]['entryId'], self.cid,
                                      detail['versionId'],
                                      {'keywords': [self.keyword('beta', weight=0)]}, 1)
            checked = metadata.review_field(db, items[1]['entryId'], self.cid,
                                            'keywords', revised['fields']['keywords']['token'], 1)
            metadata.publish(db, items[1]['entryId'], self.cid, checked['versionId'], 1)
        with self.assertRaisesRegex(ValueError, 'lexical_snapshot_changed'):
            self.search(query_text='alpha', purpose='candidate', cursor=cursor, limit=1)
        self.rebuild()
        self.assertEqual(self.search(query_text='alpha', purpose='candidate')['total'], 1)


class LegacyLexicalCompatibility(EntryBase):
    """A3.2 is installed; A3.3 migration remains explicit."""

    def setUp(self):
        super().setUp()
        with app_module.connect() as db:
            metadata.init_db(db)
            legacy_jobs.init_db(db)
        with app_module.connect() as db:
            a3_schema.migrate(db)
            tag_schema.migrate(db)

    def draft(self):
        with app_module.connect() as db:
            return metadata.create_draft(db, self.cid, self.refs(), '合成标题', 1,
                                         'legacy-a33-' + str(db.execute(
                                             'SELECT COUNT(*) FROM mv3_entries').fetchone()[0]))

    def test_unmigrated_old_keywords_remain_and_new_search_refuses(self):
        item = self.draft()
        with app_module.connect() as db:
            job = legacy_jobs.enqueue(db, item['entryId'], self.cid, 1, 'METADATA')
            lease = jobs.claim(db, job['id'], self.cid)
        with app_module.connect() as db:
            result = jobs.execute_claimed(db, job['id'], self.cid, lease)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(schema.state(db), 'absent')
        with self.assertRaisesRegex(ValueError, 'a33_migration_required'):
            lexical.search(app_module.connect, self.cid, 1, purpose='management',
                           query_text='合成', signing_secret=SECRET)

    def test_pre_migration_local_job_fails_after_explicit_migration(self):
        item = self.draft()
        with app_module.connect() as db:
            job = legacy_jobs.enqueue(db, item['entryId'], self.cid, 1, 'METADATA')
            lease = jobs.claim(db, job['id'], self.cid)
        with app_module.connect() as db:
            schema.migrate(db)
        with app_module.connect() as db:
            result = jobs.execute_claimed(db, job['id'], self.cid, lease)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error'], 'keyword_pipeline_required')
            self.assertEqual(metadata.read_draft(db, item['entryId'], self.cid)
                             ['fields']['keywords']['completionStatus'], 'missing')
            with self.assertRaisesRegex(ValueError, 'keyword_pipeline_required'):
                legacy_jobs.enqueue(db, item['entryId'], self.cid, 1, 'METADATA')

    def test_inflight_mock_after_migration_settles_known_usage_and_rejects_mixed(self):
        item = self.draft()
        with app_module.connect() as db:
            job = legacy_jobs.enqueue(db, item['entryId'], self.cid, 1, 'METADATA',
                                      mock_model=True)
        changes = {'title': '旧模型标题', 'keywords': [
            {'text': '旧词', 'language': 'und', 'kind': 'literal',
             'weight': 1, 'field': 'raw', 'evidence': [
                 {'kind': 'message', 'id': str(self.user), 'start': 0, 'end': 1}]}]}
        adapter = legacy_jobs.MockMetadataAdapter(changes, input_tokens=7, output_tokens=9)
        def migrate_then_return(_adapter):
            with app_module.connect() as db:
                schema.migrate(db)
            return changes, 7, 9
        with patch.object(legacy_jobs.MockMetadataAdapter, 'generate', migrate_then_return):
            result = legacy_jobs.dispatch_mock(app_module.connect, job['id'], self.cid, adapter)
        self.assertEqual(result['status'], 'rejected')
        with app_module.connect() as db:
            chunk = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',
                               (job['id'],)).fetchone()
            self.assertEqual(chunk['state'], 'rejected')
            self.assertEqual((chunk['used_input_tokens'], chunk['used_output_tokens']), (7, 9))
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',
                                         (job['id'],)).fetchone())
            detail = metadata.read_draft(db, item['entryId'], self.cid)
            self.assertEqual(detail['fields']['title']['value'], '合成标题')
            self.assertEqual(detail['fields']['keywords']['completionStatus'], 'missing')

    def test_inflight_mock_unknown_keeps_reservation(self):
        item = self.draft()
        with app_module.connect() as db:
            job = legacy_jobs.enqueue(db, item['entryId'], self.cid, 1, 'METADATA',
                                      mock_model=True)
        adapter = legacy_jobs.MockMetadataAdapter({}, timeout=True)
        def migrate_then_timeout(_adapter):
            with app_module.connect() as db:
                schema.migrate(db)
            raise TimeoutError('synthetic_unknown')
        with patch.object(legacy_jobs.MockMetadataAdapter, 'generate', migrate_then_timeout):
            result = legacy_jobs.dispatch_mock(app_module.connect, job['id'], self.cid, adapter)
        self.assertEqual(result['status'], 'unknown')
        with app_module.connect() as db:
            parent = jobs.owned(db, job['id'], self.cid)
            child = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',
                               (job['id'],)).fetchone()
            self.assertEqual(parent['status'], 'unknown')
            self.assertEqual(child['state'], 'unknown')
            self.assertEqual(child['reserved_tokens'], 4096)
            self.assertIsNone(child['used_input_tokens'])
            self.assertIsNone(db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',
                                         (job['id'],)).fetchone())



