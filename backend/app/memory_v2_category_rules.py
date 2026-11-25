"""A3.1 finite, source-anchored category proposals. No model or I/O.

The supported forms are intentionally narrow. A match establishes that a
topic was discussed in RAW; it never establishes that a described event is true.
"""
import re

# v2 requires the supported reply to begin directly, preventing a v1 false
# positive when a later, unrelated sentence happens to reuse answer words.
RULE_VERSION = 'category-direct-evidence-v2'
RULE_DEFINITIONS = {
    ('form', '技术讨论'): '讨论技术机制、实现或故障的具体内容',
    ('form', '创作讨论'): '讨论作品的写法、结构、修改或选择',
    ('domain', '生活'): '生活事项是对话主题',
    ('domain', '工作'): '工作任务或工作安排是主题',
    ('domain', '技术'): '技术机制、实现或故障是主题',
    ('domain', '兴趣'): '明确作为兴趣活动讨论',
}


def compatible_vocabulary(term, key):
    """Bind this rule version to its approved meaning, not a refreshed token.

    The original seed stored definition=name. That exact legacy seed value is
    accepted for this fixed ID; an explicit approved definition is also valid.
    Any other semantic edit requires a separately reviewed rule version.
    """
    return (term['kind'] == 'category' and term['dimension'] == key[0]
            and term['status'] == 'active' and not term['merged_into']
            and term['definition'] in (key[1], RULE_DEFINITIONS[key]))

# Each pair needs a concrete question/proposal and a matching substantive
# response. Both language alternatives are literal, versioned rule scope.
_PAIRS = (
    (('form', '技术讨论'), 'technical_zh',
     r'(?P<e>(?:这个\s*)?(?:API|接口)\s*(?:为什么返回\s*\d{3}|的状态码是什么))',
     r'(?P<e>(?:因为.{0,24}(?:来源版本变了|来源版本变更|游标已过期)|\d{3}\s*表示.{0,16}游标已过期))'),
    (('form', '技术讨论'), 'technical_script_zh',
     r'(?P<e>脚本的错误怎么修)', r'(?P<e>检查请求体的字段)'),
    (('form', '技术讨论'), 'technical_ja',
     r'(?P<e>API の応答が \d{3} になる理由)',
     r'(?P<e>参照元の版が変わった)'),
    (('domain', '技术'), 'technical_zh',
     r'(?P<e>(?:这个\s*)?(?:API|接口)\s*(?:为什么返回\s*\d{3}|的状态码是什么))',
     r'(?P<e>(?:因为.{0,24}(?:来源版本变了|来源版本变更|游标已过期)|\d{3}\s*表示.{0,16}游标已过期))'),
    (('domain', '技术'), 'technical_script_zh',
     r'(?P<e>脚本的错误怎么修)', r'(?P<e>检查请求体的字段)'),
    (('domain', '技术'), 'technical_ja',
     r'(?P<e>API の応答が \d{3} になる理由)',
     r'(?P<e>参照元の版が変わった)'),
    (('form', '创作讨论'), 'creative_zh',
     r'(?P<e>(?:短篇|故事|作品)的结尾怎么改)',
     r'(?P<e>把最后一段留白)'),
    (('domain', '生活'), 'living_mixed',
     r'(?P<e>今晚做咖喱，味付けはどうする)',
     r'(?P<e>先放少量盐，再尝味道)'),
    (('domain', '生活'), 'living_zh',
     r'(?P<e>今晚做咖喱，怎么调味)',
     r'(?P<e>先放少量盐，再尝味道)'),
    (('domain', '生活'), 'living_ja',
     r'(?P<e>今夜カレーを作る、味付けはどうする)',
     r'(?P<e>塩を少し入れてから味を見る)'),
    (('domain', '工作'), 'work_zh',
     r'(?P<e>(?:明天的)?工作报告要改排期)',
     r'(?P<e>先核对交付日期)'),
    (('domain', '工作'), 'work_ja',
     r'(?P<e>仕事の報告書の日程を変更したい)',
     r'(?P<e>提出日を先に確認しよう)'),
    (('domain', '兴趣'), 'hobby_zh',
     r'(?P<e>我把练吉他当作爱好，和弦怎么按)',
     r'(?P<e>先练这两个指位)'),
    (('domain', '兴趣'), 'hobby_ja',
     r'(?P<e>ギターの練習が趣味で、コードをどう押さえる)',
     r'(?P<e>まずこの二つの指の位置を練習)'),
)
_COMPILED = tuple((key, variant, re.compile(lead), re.compile(reply))
                  for key, variant, lead, reply in _PAIRS)
_NEGATED_TECH = re.compile(r'(?:不是技术讨论|技術の話ではない)')
_CLAUSE = re.compile(r'[。！？!?；;\n]')
_DISCLAIMERS = re.compile(r'(?:如果|假如|假设|もし|冗谈|冗談|开玩笑|只是引用|引用别人)')
_QUOTES = (('「', '」'), ('“', '”'), ('‘', '’'), ('"', '"'))


def _eligible_span(text, match, key):
    # A negation of technical discussion in this utterance applies to its
    # technical rule, not to unrelated later utterances in the entry.
    if key in (('form', '技术讨论'), ('domain', '技术')) and _NEGATED_TECH.search(text):
        return False
    start = match.start('e')
    clause_start = max((m.end() for m in _CLAUSE.finditer(text, 0, start)), default=0)
    if _DISCLAIMERS.search(text[clause_start:start]):
        return False
    for opening, closing in _QUOTES:
        if opening == closing:
            if text[:start].count(opening) % 2:
                return False
        elif text[:start].rfind(opening) > text[:start].rfind(closing):
            return False
    return True


def evaluate(sources):
    """Return per-category evidence and conservative diagnostics.

    Sources are ordered, already authorized members of one confirmed entry.
    Only a user turn followed immediately by an assistant response is eligible.
    """
    hits = {}
    rejected = set()
    for first, second in zip(sources, sources[1:]):
        if first['role'] != 'user' or second['role'] != 'assistant':
            continue
        for key, variant, lead, reply in _COMPILED:
            left = lead.search(first['text'])
            right = reply.search(second['text'])
            if left is None or right is None:
                continue
            if not _eligible_span(first['text'], left, key) or not _eligible_span(second['text'], right, key):
                rejected.add('context_not_asserted')
                continue
            # A matching phrase buried after another statement is not proof
            # that this assistant turn answers the preceding user question.
            if second['text'][:right.start('e')].strip():
                rejected.add('reply_not_direct')
                continue
            evidence = [
                {'kind': first['kind'], 'id': first['id'], 'start': left.start('e'), 'end': left.end('e')},
                {'kind': second['kind'], 'id': second['id'], 'start': right.start('e'), 'end': right.end('e')},
            ]
            hits.setdefault(key, {'dimension': key[0], 'name': key[1],
                                  'variant': variant, 'evidence': evidence})
    if not hits and not rejected:
        rejected.add('no_supported_pair')
    return {'hits': list(hits.values()), 'reasonCodes': sorted(rejected)}
