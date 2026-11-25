"""Finite, source-anchored literal keyword candidates; no semantic inference."""
import unicodedata
import re

from . import memory_v2_metadata_contract as contract

RULE_VERSION = 'keyword-literal-raw-v1'
MAX_KEYWORDS = 32
MAX_EVIDENCE_TEXT = 80


def _script(text):
    if text and all(char.isascii() and char.isalnum() for char in text):
        return 'latin'
    if text and all(unicodedata.name(char, '').startswith('LATIN') or
                    (char.isascii() and char.isdigit()) for char in text):
        return 'latin'
    if text and all(0x3400 <= ord(char) <= 0x9fff or 0xf900 <= ord(char) <= 0xfaff
                    for char in text):
        return 'han'
    if text and all(0x3040 <= ord(char) <= 0x30ff for char in text):
        return 'kana'
    return None


def _clusters(text):
    """Attach combining marks to their base so NFKC cannot move an offset."""
    start = 0
    for index, char in enumerate(text):
        if index != start and not unicodedata.combining(char):
            yield start, index, text[start:index]
            start = index
    if start < len(text):
        yield start, len(text), text[start:]


def spans(text):
    """Return whole script runs and original offsets, never CJK sliding pairs."""
    if not isinstance(text, str):
        raise ValueError('keyword_source_invalid')
    kind = None
    first = 0
    last = 0

    def emit(start, end, script):
        surface = text[start:end]
        normalized = contract.normalized(surface)
        low = 2
        high = 48 if script == 'latin' else 8
        if (low <= len(normalized) <= high and len(surface) <= MAX_EVIDENCE_TEXT and
                _script(normalized) == script):
            return start, end, surface, normalized, 'ja' if script == 'kana' else 'und'
        return None

    for start, end, cluster in _clusters(text):
        normalized = unicodedata.normalize('NFKC', cluster).casefold()
        script = _script(normalized)
        if script != kind:
            if kind is not None:
                item = emit(first, last, kind)
                if item is not None:
                    yield item
            first = start
            kind = script
        last = end
    if kind is not None:
        item = emit(first, last, kind)
        if item is not None:
            yield item


def evaluate(sources):
    """Keep first 32 distinct terms in ordered RAW/source-position order."""
    found = {}
    overflow = False
    for source in sources:
        for start, end, surface, normalized, language in spans(source['text']):
            key = (language, normalized)
            if key in found:
                continue
            if len(found) == MAX_KEYWORDS:
                overflow = True
                continue
            if contract.normalized(source['text'][start:end]) != normalized:
                raise ValueError('keyword_evidence_mapping_invalid')
            found[key] = {'text': surface, 'language': language, 'kind': 'literal',
                          'weight': 1, 'field': 'raw',
                          'evidence': [{'kind': source['kind'], 'id': source['id'],
                                        'start': start, 'end': end}]}
    return {'keywords': list(found.values()),
            'reasonCodes': ['keyword_capacity_reached'] if overflow else
                           ['no_supported_literal'] if not found else []}


# A separate, opt-in daily profile. Historical literal-v1 output and validation
# keep their original semantics; no old field is rewritten on import or read.
RETRIEVAL_RULE_VERSION = 'keyword-retrieval-phrases-v2'
_LOW_INFO = frozenset(('哈哈 呵呵 嘿嘿 谈谈 身体 声音 只是 偶尔 全部 自分 不是什麼大事 不是啥大事 '
    '不要紧 没关系 对不对 你说是不是 我们 你们 他们 自己 这个 那个 这样 那样 '
    '俺 お前 私 あなた これ それ だけ ただ たまに さっきも したことじゃない '
    'this that they them with from have just maybe really also then about').split())
_HAN = r'\u3400-\u9fff\uf900-\ufaff々'
_KATA = r'ァ-ヺー'
_PREFIX = re.compile(r'^(?:我们|他们|她们|我|你|她|他|今天|昨天|明天|后来|当时|下午|早上|晚上|'
                     r'一起|关于|谈到|提到|讨论|考虑|计划|准备|决定|说|想|展示|安装|制作|设计|先|才|还|又|也|在|去|到|了)+')
_THING = re.compile(r'['+_HAN+r']{1,24}?(?:图书馆|美术馆|博物馆|纪念馆|展览馆|'
    r'馆|大学|公园|车站|剧院|医院|公司|工作室|指示牌|装置|望远镜|数据库|服务器|载体|'
    r'约定|计划|课程|测试|试验|维修|恢复|备份|迁移|旅行|制作|设计|比赛|音乐会|陶艺课|申请)')
_JAPANESE_NOUN = re.compile(r'['+_HAN+r']+['+_KATA+r']+['+_HAN+_KATA+r']*|['+_KATA+r']{3,24}(?:['+_HAN+r']+)?')
_LATIN_TERM = re.compile(r'(?<![\w])(?:[A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ0-9０-９._+#/-]{2,40})'
                         r'(?:[ \t]+[A-ZＡ-Ｚ][A-Za-zＡ-Ｚａ-ｚ0-9._+#/-]{1,20}){0,2}')
_NAMED = re.compile(r'(?:名叫|名字叫|叫作|称为)[“「]?(['+_HAN+_KATA+r']{2,10})[”」]?(?=[，、。！？!? ,.\n])')
_QUOTED = re.compile(r'《([^《》\n]{2,40})》|「([^「」\n]{2,40})」|『([^『』\n]{2,40})』|“([^“”\n]{2,40})”')


def normalize_manual(words):
    """Stable literal surfaces; separators are UI syntax, never paraphrases."""
    out=[]
    for word in words:
        if not isinstance(word,str): raise ValueError('keyword_invalid')
        word=word.strip().strip('\ufeff').strip()
        if not word: continue
        if len(word)>MAX_EVIDENCE_TEXT: raise ValueError('keyword_invalid')
        if word not in out: out.append(word)
    if len(out)>MAX_KEYWORDS: raise ValueError('keyword_capacity_exceeded')
    return out


def _informative(surface):
    word=contract.normalized(surface)
    if (not 2<=len(word)<=40 or word in _LOW_INFO or
            re.search(r'[、,，。！？!?\r\n]',word) or
            re.fullmatch(r'[哈呵嘿啊嗯哦唔…\.]+',word)):
        return False
    # Full propositions and uncertain attribution stay in the source, not in a
    # new asserted fact label. Names/topics still have their exact local quote.
    return not re.search(r'不是|没有|不知|不太|你说|是不是|只有|只要|如果|并且|后才|且|'
        r'是这样的|是那样的|没什么|(?:我|你|他|她)(?:觉得|认为|喜欢|想要|会去)|'
        r'(?:です|ます|でした|ました|じゃない|ではない|だよ|だね|よね|かな|したい)$|'
        r'[的地得]$|^(?:若|虽然|但是|因为|所以)',word)


def evaluate_retrieval(sources):
    """Conservative topic phrases, not segmentation or semantic fact inference.

    Prefer explicit titles/quotations, named things, complete mixed Japanese
    nouns, and intact Latin identifiers. Ambiguous ordinary CJK speech is not
    split into n-grams. Selection is by evidence form then source order, never
    by frequency and never padded to capacity.
    """
    candidates=[]
    for ordinal,source in enumerate(sources):
        text=source['text']
        if not isinstance(text,str): raise ValueError('keyword_source_invalid')
        for match in _NAMED.finditer(text):
            candidates.append((0,ordinal,match.start(1),match.end(1),source))
        for match in _QUOTED.finditer(text):
            group=next(i for i in range(1,5) if match.group(i) is not None)
            candidates.append((0,ordinal,match.start(group),match.end(group),source))
        last_end=None
        for match in _THING.finditer(text):
            start,end=match.span()
            if start and re.fullmatch('['+_HAN+']',text[start-1]) and start!=last_end:continue
            last_end=end
            extension=re.match(r'(?:恢复|备份|迁移|维修|计划)',text[end:])
            if extension:end+=extension.end()
            prefix=_PREFIX.match(text[start:end])
            if prefix:start+=prefix.end()
            if end-start>=3:candidates.append((1,ordinal,start,end,source))
        for pattern,priority in ((_JAPANESE_NOUN,2),(_LATIN_TERM,3)):
            for match in pattern.finditer(text):
                candidates.append((priority,ordinal,*match.span(),source))
    found={}
    for _,ordinal,start,end,source in sorted(candidates,key=lambda x:x[:4]):
        surface=source['text'][start:end]
        if not _informative(surface):continue
        key=contract.normalized(surface)
        if key in found:continue
        # Avoid a weaker nested piece when the complete same-source phrase is
        # already retained; never cut or guess a quote to manufacture evidence.
        if any(x['ordinal']==ordinal and x['start']<=start and x['end']>=end for x in found.values()):continue
        found[key]=dict(ordinal=ordinal,start=start,end=end,item=dict(text=surface,language='und',kind='literal',
            weight=1,field='raw',evidence=[dict(kind=source['kind'],id=source['id'],start=start,end=end)]))
        if len(found)==16:break
    return dict(keywords=[x['item'] for x in found.values()],
                reasonCodes=[] if found else ['no_distinctive_literal_phrase'])

SELECTION_RULE_VERSION = 'keyword-selection-suggestions-v3'
