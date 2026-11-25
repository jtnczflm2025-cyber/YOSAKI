"""Synthetic daily keyword quality and actual save/review/publish routes."""
import json
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_memory_v2_review_flow as review_fixture
from test_memory_v2_entries import app_module
from backend.app import memory_v2_keyword_rules as rules
from backend.app import memory_v2_metadata as meta, memory_v2_processing as processing


class RetrievalPhrases(unittest.TestCase):
    def result(self,text):
        out=rules.evaluate_retrieval([dict(kind='message',id='synthetic',role='user',text=text)])
        for item in out['keywords']:
            self.assertEqual((item['kind'],item['field']),('literal','raw'))
            for point in item['evidence']:
                self.assertEqual(text[point['start']:point['end']],item['text'])
        return out['keywords']
    def words(self,text):return [x['text'] for x in self.result(text)]
    def test_explicit_name_remains_a_literal_mention_with_context(self):
        self.assertIn('林岚',self.words('友人名叫林岚，她提到《雪夜手记》。不是用户完成的。'))
    def test_noise_is_not_padded_to_capacity(self):
        self.assertEqual(self.words('哈哈哈，只是，偶尔，身体，声音，全部，自分，したことじゃない、ただ、たまに、さっきも。'),[])
    def test_named_objects_and_full_topics_not_sentence_scraps(self):
        words=self.words('我考虑在银叶馆展示蓝灯指示牌，但还没有决定。只有馆方批准且夜间测试通过后，才讨论是否安装。友人的装置已经完成。')
        self.assertIn('银叶馆',words);self.assertIn('蓝灯指示牌',words)
        self.assertNotIn('展示蓝灯指示牌',words);self.assertNotIn('批准',words)
        self.assertLess(len(words),16)
    def test_unrelated_domains_quotes_and_mixed_script(self):
        words=self.words('我们讨论了数据库恢复，计划去星河图书馆。书名是《月影奏鸣曲》。友人は「雪夜の約束」を引用した。夜間テストに合格したら再検討。アイススケートの練習。OpenAI API。')
        for value in ('数据库恢复','星河图书馆','月影奏鸣曲','雪夜の約束','夜間テスト','アイススケート','OpenAI API'):self.assertIn(value,words)
        for fragment in ('テスト','したら','練習','合格','API'):self.assertNotIn(fragment,words)
    def test_negation_joke_and_third_person_are_not_promoted_to_facts(self):
        text='不是我安装的。朋友的装置并未获批。我开玩笑引用「魔法升降机」，没有真的安装；只有许可与测试通过后才讨论。'
        words=self.words(text)
        self.assertIn('朋友的装置',words);self.assertIn('魔法升降机',words)
        self.assertNotIn('已获批',words);self.assertNotIn('我的装置',words)
        self.assertNotIn('安装',words)
    def test_normalized_dedupe_preserves_first_original_surface_and_offsets(self):
        words=self.words('《OpenAI API》 OpenAI API openai API。夜間テスト、夜間テスト。')
        self.assertEqual(sum('openai' in x.casefold() for x in words),1)
        self.assertEqual(words.count('夜間テスト'),1)
    def test_quoted_low_information_utterances_are_not_topics(self):
        self.assertEqual(self.words('「私はそれが好きです」；“原来是这样的”；“我觉得很好”。'),[])
    def test_unknown_long_conversation_is_not_arbitrarily_sliced(self):
        self.assertEqual(self.words('这是很长的一整串连续汉字并没有可辨认的主题实体不会被拆成双字片段。'),[])


class KeywordWorkflow(unittest.TestCase):
    def setUp(self):
        self.f=review_fixture.ReviewFlow();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cid=self.f.cid;self.eid=self.f.eid;self.client=self.f.client;self.url=self.f.url
        from backend.app import memory_v2_chronicle_schema
        with app_module.connect() as db: memory_v2_chronicle_schema.install(db)
    def ready(self):
        v=self.f.confirm(self.f.create()).json()
        with app_module.connect() as db:
            d=meta.revise(db,self.eid,self.cid,v['entry']['versionId'],{'synopsis':'イキヤ说累了，阿夜建议休息。'},1)
            for name in ('title','synopsis'):meta.review_field(db,self.eid,self.cid,name,d['fields'][name]['token'],1)
        return self.f.view()
    def save(self,v,words,categories=None,tags=None):
        return self.client.post(self.url+'/labels',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId'],categories=categories or [],tags=tags or [],keywords=words,sourceOrdinal=0))
    def publish(self,v):
        return self.client.post(f'/api/memory-v2/entry-drafts/{self.eid}/publish',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId'],expectedNarrativeVersionId=v['narrative']['id']))
    def test_save_confirm_publish_preserves_other_checks(self):
        before=self.ready();saved=self.save(before,[' 今天累了 ','休息','今天累了']);self.assertEqual(saved.status_code,200,saved.text)
        now=self.f.view();self.assertEqual(now['narrative'],before['narrative'])
        for name in ('title','synopsis','narrative'):
            self.assertEqual(now['entry']['fields'][name]['token'],before['entry']['fields'][name]['token'])
            self.assertEqual(now['entry']['fields'][name]['lifecycleStatus'],'available')
        field=now['entry']['fields']['keywords'];self.assertEqual([x['text'] for x in field['value']],['今天累了','休息'])
        checked=self.client.post(f'/api/memory-v2/entry-drafts/{self.eid}/review',json=dict(characterId=self.cid,field='keywords',token=field['token']))
        self.assertEqual(checked.status_code,200,checked.text);self.assertEqual(self.publish(self.f.view()).status_code,200)
        self.assertEqual(self.f.view()['entry']['fields']['keywords']['lifecycleStatus'],'available')
    def test_whitespace_duplicates_and_order_noop_keep_actual_version_and_check(self):
        v=self.ready();self.assertEqual(self.save(v,['今天累了','休息']).status_code,200)
        v=self.f.view();field=v['entry']['fields']['keywords']
        self.client.post(f'/api/memory-v2/entry-drafts/{self.eid}/review',json=dict(characterId=self.cid,field='keywords',token=field['token']))
        r=self.save(v,[' 休息 ','今天累了','今天累了',' ']);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['versionId'],v['entry']['versionId']);self.assertEqual(r.json()['fields']['keywords']['fieldId'],field['fieldId'])
        self.assertEqual(r.json()['fields']['keywords']['lifecycleStatus'],'available')
    def test_empty_optional_vocabulary_and_keywords_do_not_block_publication(self):
        v=self.ready();self.assertEqual(self.save(v,[]).status_code,200)
        self.assertEqual(self.publish(self.f.view()).status_code,200)
        self.assertFalse(self.f.view()['entry']['fields']['tags']['value'])
    def test_pending_keywords_do_not_gain_qualification_merely_by_publish(self):
        v=self.ready();self.assertEqual(self.save(v,['休息']).status_code,200)
        self.assertEqual(self.publish(self.f.view()).status_code,200)
        self.assertEqual(self.f.view()['entry']['fields']['keywords']['lifecycleStatus'],'draft')
    def test_invalid_literal_does_not_change_stored_field_or_other_reviews(self):
        v=self.ready();r=self.save(v,['原文不存在的词']);self.assertEqual(r.status_code,409)
        self.assertEqual(r.json()['detail'],'literal_keyword_not_in_source');self.assertEqual(self.f.view(),v)
    def test_old_window_version_conflict_does_not_overwrite_new_keyword(self):
        old=self.ready();self.assertEqual(self.save(old,['休息']).status_code,200)
        r=self.save(old,['今天累了']);self.assertEqual(r.status_code,409);self.assertEqual(r.json()['detail'],'version_conflict')
        self.assertEqual(self.f.view()['entry']['fields']['keywords']['value'][0]['text'],'休息')
    def test_new_profile_protects_manual_values_and_old_literal_profile(self):
        v=self.ready();self.save(v,['休息']);before=self.f.view()['entry']['fields']['keywords']
        with app_module.connect() as db:
            jid=processing.create_keyword_job(db,self.eid,self.cid,1,rule_version=rules.RETRIEVAL_RULE_VERSION)
        out=processing.run_keyword_job(app_module.connect,jid,self.cid,1)
        self.assertEqual(out['candidateCount'],0)
        self.assertEqual(self.f.view()['entry']['fields']['keywords']['fieldId'],before['fieldId'])
        self.assertEqual(rules.evaluate([dict(kind='message',id='x',text='只是 哈哈哈')])['keywords'][0]['text'],'只是')
    def test_daily_actual_local_route_selects_new_version_without_model(self):
        v=self.f.view();r=self.client.post(self.url+'/prepare-local',json=dict(characterId=self.cid,expectedVersion=v['entry']['versionId']))
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['providerRequests'],0)
        with app_module.connect() as db:
            row=db.execute("SELECT payload_json FROM mv3_jobs WHERE json_extract(payload_json,'$.fields[0]')='keywords' ORDER BY rowid DESC LIMIT 1").fetchone()
            self.assertEqual(json.loads(row[0])['ruleVersion'],rules.SELECTION_RULE_VERSION)

    def positive_entry(self,key):
        with app_module.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?',('数据库恢复。',self.f.fx.user));self.f.fx._index(db)
            return meta.create_draft(db,self.cid,self.f.fx.refs(),'合成数据库恢复',1,key)
    def test_reviewed_legacy_auto_candidate_is_not_rebuilt_by_v2(self):
        entry=self.positive_entry('legacy-reviewed-v2-protection')
        with app_module.connect() as db:old=processing.create_keyword_job(db,entry['entryId'],self.cid,1)
        processing.run_keyword_job(app_module.connect,old,self.cid,1)
        with app_module.connect() as db:
            d=meta.read_draft(db,entry['entryId'],self.cid);field=d['fields']['keywords']
            self.assertTrue(field['value']);meta.review_field(db,entry['entryId'],self.cid,'keywords',field['token'],1)
            new=processing.create_keyword_job(db,entry['entryId'],self.cid,1,rule_version=rules.RETRIEVAL_RULE_VERSION)
        self.assertEqual(processing.run_keyword_job(app_module.connect,new,self.cid,1)['candidateCount'],0)
        with app_module.connect() as db:
            current=meta.read_draft(db,entry['entryId'],self.cid)['fields']['keywords']
            self.assertEqual(current['fieldId'],field['fieldId']);self.assertEqual(current['lifecycleStatus'],'available')
    def test_new_candidate_read_version_and_unknown_policy_rejection(self):
        entry=self.positive_entry('new-profile-version')
        with app_module.connect() as db:jid=processing.create_keyword_job(db,entry['entryId'],self.cid,1,rule_version=rules.RETRIEVAL_RULE_VERSION)
        self.assertGreater(processing.run_keyword_job(app_module.connect,jid,self.cid,1)['candidateCount'],0)
        with app_module.connect() as db:
            current=meta.read_draft(db,entry['entryId'],self.cid)['fields']['keywords']
            self.assertEqual(current['lifecycleStatus'],'draft')
            meta.review_field(db,entry['entryId'],self.cid,'keywords',current['token'],1)
        with patch.object(rules,'RETRIEVAL_RULE_VERSION','keyword-retrieval-phrases-unknown'):
            with app_module.connect() as db:
                self.assertEqual(meta.read_draft(db,entry['entryId'],self.cid)['fields']['keywords']['lifecycleStatus'],'stale')
            with self.assertRaisesRegex(ValueError,'keyword_rule_outdated'):
                processing.run_keyword_job(app_module.connect,jid,self.cid,1)

    def test_automatic_import_preparation_uses_v3_only_for_missing_keywords(self):
        from backend.app import memory_v2_chronicle_processing as daily
        entry=self.positive_entry('new-daily-preparation')
        result=daily.prepare_entry(app_module.connect,self.cid,1,entry['entryId'])
        self.assertEqual(result['status'],'prepared')
        with app_module.connect() as db:
            d=meta.read_draft(db,entry['entryId'],self.cid);field=d['fields']['keywords']
            self.assertEqual([x['text'] for x in field['value']],['数据库恢复'])
            stored=meta._fields(db,d['versionId'])['keywords'];self.assertEqual(stored['rule_version'],rules.SELECTION_RULE_VERSION)
        again=daily.prepare_entry(app_module.connect,self.cid,1,entry['entryId'])
        self.assertIn({'field':'keyword','status':'existing_keywords_preserved'},again['metadata'])
        with app_module.connect() as db:self.assertEqual(meta.read_draft(db,entry['entryId'],self.cid)['fields']['keywords']['fieldId'],field['fieldId'])
    def test_automatic_upkeep_does_not_rewrite_old_unreviewed_keywords(self):
        from backend.app import memory_v2_chronicle_processing as daily
        entry=self.positive_entry('old-daily-keywords')
        with app_module.connect() as db:old=processing.create_keyword_job(db,entry['entryId'],self.cid,1)
        processing.run_keyword_job(app_module.connect,old,self.cid,1)
        with app_module.connect() as db:before=meta.read_draft(db,entry['entryId'],self.cid)
        daily.prepare_entry(app_module.connect,self.cid,1,entry['entryId'])
        with app_module.connect() as db:self.assertEqual(meta.read_draft(db,entry['entryId'],self.cid)['fields']['keywords'],before['fields']['keywords'])

    def test_v3_task_matches_suggestions_and_binds_lifecycle(self):
        from backend.app import memory_v2_keyword_suggestions as selection
        entry=self.positive_entry('v3-default-bindings')
        with app_module.connect() as db:
            jid=processing.create_keyword_job(db,entry['entryId'],self.cid,1,rule_version=rules.SELECTION_RULE_VERSION)
            row=processing._owned(db,jid,self.cid)
            self.assertEqual(json.loads(row['read_set_json'])['ruleVersion'],selection.VERSION)
        result=processing.run_keyword_job(app_module.connect,jid,self.cid,1)
        self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            d=meta.read_draft(db,entry['entryId'],self.cid)
            sources=[]
            from backend.app import memory_v2_derivation as m3
            for r in d['sourceRefs']:
                source=m3.live_source(db,self.cid,r['source_kind'],r['source_id'])
                sources.append(dict(kind=source.kind,id=source.source_id,text=source.text))
            self.assertEqual(d['fields']['keywords']['value'],selection.evaluate(sources)['keywords'])
            self.assertEqual(d['fields']['keywords']['lifecycleStatus'],'draft')
        with patch.object(rules,'SELECTION_RULE_VERSION','keyword-selection-suggestions-unknown'):
            with app_module.connect() as db:self.assertEqual(meta.read_draft(db,entry['entryId'],self.cid)['fields']['keywords']['lifecycleStatus'],'stale')
            with self.assertRaisesRegex(ValueError,'keyword_rule_outdated'):processing.run_keyword_job(app_module.connect,jid,self.cid,1)
