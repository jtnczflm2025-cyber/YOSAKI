"""Read-only literal suggestions; selection is never a field review."""
import re
from . import memory_v2_keyword_rules as legacy
VERSION = legacy.SELECTION_RULE_VERSION
# Equivalence is limited to explicit lexical pairs. No paraphrase is persisted.
GROUPS = (('紧张','緊張'), ('数据库恢复','データベース復旧'), ('夜间测试','夜間テスト'), ('备份','バックアップ'))
CORE = re.compile(r'紧张|緊張|数据库恢复|データベース復旧|夜间测试|夜間テスト|备份|バックアップ')

def suggest(sources):
    candidates=[]
    for n,s in enumerate(sources):
        text=s['text']
        def add(start,end,reason,priority):
            word=text[start:end]
            if not legacy._informative(word): return
            if re.search(r'(?:我|你|他|她|我们|他们)(?:准备|考虑|觉得|说|想|要|会)|^(?:与|和|但|而|因为|所以)',word): return
            candidates.append(dict(text=word,reason=reason,priority=priority,ordinal=n,
                evidence=[dict(kind=s['kind'],id=s['id'],start=start,end=end)],
                context=text[max(0,start-35):min(len(text),end+35)]))
        for m in legacy._NAMED.finditer(text): add(*m.span(1),'原文明示的人名或名称',0)
        # Book-title punctuation has an explicit naming role. Ordinary quotation
        # marks are not evidence of retrieval value or of a complete noun phrase.
        for m in re.finditer(r'《([^《》\n]{2,40})》',text): add(*m.span(1),'明确作品或主题名称',0)
        for item in legacy.evaluate_retrieval([s])['keywords']:
            point=item['evidence'][0]; start,end=point['start'],point['end']
            word=item['text']
            if word.startswith('的') and start>0:
                start+=1;word=text[start:end]
            if (legacy._THING.fullmatch(word) and not re.search(r'和|与|说|是|已经|尚未',word)) or re.fullmatch(r'[一-鿿]{0,6}[ァ-ヺー]{3,24}(?:研究|設計|実験|開発|装置|検査)?',word) or legacy._LATIN_TERM.fullmatch(word):
                add(start,end,'具体事物、活动或完整术语；请结合上下文选择',1)
        for m in CORE.finditer(text): add(*m.span(),'原句中的检索核心词；不表示诊断或事实确认',2)
    kept=[];excluded=[];keys=set()
    for c in sorted(candidates,key=lambda x:(x['priority'],x['ordinal'],x['evidence'][0]['start'])):
        word=c['text'];key=legacy.contract.normalized(word)
        key=next(('equivalent:'+str(i) for i,g in enumerate(GROUPS) if word in g),key)
        if key in keys:
            excluded.append(dict(text=word,reason='与已保留候选同义或重复'));continue
        keys.add(key);kept.append(c)
    return dict(ruleVersion=VERSION,candidates=kept[:16],excluded=excluded,
        limitation='本地规则只识别有限名称、术语与核心词，不能判断通用主题重要性；引述不是事实标签。')


def evaluate(sources):
    result=suggest(sources)
    return dict(keywords=[dict(text=c['text'],language='und',kind='literal',weight=1,field='raw',evidence=c['evidence']) for c in result['candidates']],reasonCodes=[] if result['candidates'] else ['no_distinctive_literal_phrase'])


def with_topics(sources, saved):
    """Recheck stored topical phrases against their original source and offsets."""
    from . import memory_v2_import_filing as filing
    local = suggest(sources)
    originals = {(s['kind'], s['id']): s['text'] for s in sources}
    topics = []
    for item in saved:
        for ref in item.get('evidence', []):
            raw = originals.get((ref['kind'], ref['id']))
            start, end = ref['start'], ref['end']
            if raw is not None and 0 <= start < end <= len(raw) and raw[start:end] == item['text']:
                topics.append(dict(text=item['text'], evidence=[ref],
                    reason='导入时按这段经历选出的主题词，已核对原话',
                    context=raw[max(0,start-35):end+35]))
                break
    keys = {legacy.contract.normalized(c['text']) for c in topics}
    extras = [c for c in local['candidates'] if legacy.contract.normalized(c['text']) not in keys]
    return dict(local, candidates=(topics+extras)[:16], ruleVersion=filing.VERSION+' / '+VERSION,
                limitation='优先展示有原文依据的导入主题词，其后是本地规则提取的候选。可以按检索习惯取舍。')
