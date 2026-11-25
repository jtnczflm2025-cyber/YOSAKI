"""Finite, local Tag topic evidence. It does not infer events or facts."""
import json
import re
import unicodedata

RULE_VERSION = 'tag-direct-topic-v1'


def normalized(value):
    return unicodedata.normalize('NFKC', value).casefold().strip()


def _mapped(value):
    result, offsets = '', []
    for pos, char in enumerate(value):
        part = unicodedata.normalize('NFKC', char).casefold()
        result += part
        offsets.extend([pos] * len(part))
    return result, offsets


def _quoted(text, pos):
    pairs = {'“':'”', '‘':'’', '「':'」', '『':'』', '"':'"'}
    for left, right in pairs.items():
        start = text.rfind(left, 0, pos+1)
        if start < 0: continue
        end = text.find(right, start+1)
        if start < pos < end:
            return True
    return False


def _boundary(text, start, end, spelling):
    if not spelling: return False
    latin = bool(re.fullmatch(r'[a-z0-9_]+', spelling))
    if latin:
        return (not (start and re.match(r'[a-z0-9_]', text[start-1])) and
                not (end < len(text) and re.match(r'[a-z0-9_]', text[end])))
    # CJK particles and punctuation can end a complete expression. A longer
    # unrecognized compound, such as 陶艺馆 for 陶艺, is not an exact name.
    if end < len(text) and '\u3400' <= text[end] <= '\u9fff' and text[end] not in '的是什么和与について':
        return False
    return True


def _language(surface, surrounding):
    if re.search(r'[\u3040-\u30ff]', surface): return 'ja'
    if re.search(r'[\u3400-\u9fff]', surface):
        return 'ja' if re.search(r'[\u3040-\u30ff]', surrounding) else 'zh'
    if re.search(r'[a-zA-ZＡ-Ｚａ-ｚ]', surface): return 'en'
    return 'und'


def _topic_span(text, start, end):
    """Return a supported topical clause, never a quoted-only assertion."""
    if _quoted(text, start): return None
    left = max(text.rfind(c, 0, start) for c in '。！？!?\n；;') + 1
    stops = [p for c in '。！？!?\n；;' if (p := text.find(c, end)) >= 0]
    right = min(stops) if stops else len(text)
    clause = text[left:right]
    local_start, local_end = start-left, end-left
    before = clause[max(0,local_start-14):local_start]
    after = clause[local_end:local_end+16]
    # Direct current discussion or a direct question about a named topic.
    before_cue = re.search(r'(?:聊聊?|讨论|谈谈?|关于|想了解|请问|在学|想做|最近关注|研究|話す|話したい|について)\s*$',before)
    after_cue = re.match(r'(?:的(?:具体问题|釉料|状态码|课程|用法)|是什么|について|を(?:話す|話したい)|の(?:使い方|問題)|は何)',after)
    if not (before_cue or after_cue): return None
    phrase_start = start-len(before)+before_cue.start() if before_cue else start
    phrase_end = end+len(after_cue.group()) if after_cue else end
    phrase = text[phrase_start:phrase_end]
    if not phrase.strip(): return None
    # The complete phrase must itself be outside a quotation. A separate
    # unquoted occurrence later in the message remains eligible.
    if _quoted(text, phrase_start): return None
    return phrase_start, phrase_end


def _occurrences(text, spelling):
    key = normalized(spelling)
    if not key: return
    mapped, offsets = _mapped(text)
    pos = 0
    while (found := mapped.find(key, pos)) >= 0:
        pos = found + 1
        start, end = offsets[found], offsets[found+len(key)-1]+1
        if normalized(text[start:end]) == key and _boundary(mapped, found, found+len(key), key):
            yield start, end


def evaluate(sources, vocabulary):
    """Sources are ordered RAW; vocabulary items are active, same-role Tags."""
    lexicon = {}
    for item in vocabulary:
        if item['kind'] != 'tag' or item['status'] != 'active': continue
        spellings = [item['name'], *json.loads(item['aliases_json'])]
        for spelling in spellings:
            lexicon.setdefault((normalized(spelling),item['language']), set()).add(item['vocabulary_id'])
    hits, suggestions, reasons = [], [], []
    seen = set()
    for source in sources:
        text = source['text']
        # A complete name match is evaluated independently for every source;
        # adjacency or a reply relationship is not required.
        matches=[]
        for item in vocabulary:
            if item['kind'] != 'tag' or item['status'] != 'active': continue
            for spelling in [item['name'], *json.loads(item['aliases_json'])]:
                for start,end in _occurrences(text,spelling):
                    span=_topic_span(text,start,end)
                    if not span: continue
                    language=_language(text[start:end],text[max(0,start-20):min(len(text),end+20)])
                    if item['language'] != language: continue
                    matches.append((start,end,span,item,spelling,language))
        for start,end,span,item,spelling,language in matches:
            key=(normalized(text[start:end]),language)
            ids=lexicon.get(key,set())
            # Overlapping longer names take precedence; never choose one of
            # several IDs or overlapping aliases arbitrarily.
            if len(ids) != 1:
                reasons.append('ambiguous_vocabulary')
                continue
            if any(a<=start and b>=end and (a,b)!=(start,end)
                   for a,b,*_ in matches):
                reasons.append('embedded_name')
                continue
            identity=(source['kind'],source['id'],start,end,item['vocabulary_id'])
            if identity in seen: continue
            seen.add(identity)
            point={'kind':source['kind'],'id':source['id'],'start':span[0],'end':span[1]}
            hits.append({'tagId':item['vocabulary_id'],'surface':text[start:end],
                         'language':language,'evidence':[point],
                         'evidenceText':text[span[0]:span[1]]})
        # Unmatched explicit topic phrases are management suggestions only.
        for pattern in (r'(?:聊聊?|讨论|谈谈?|关于|想了解|最近关注)\s*([\u3400-\u9fff\u3040-\u30ffA-Za-zＡ-Ｚａ-ｚ0-9_]{2,20})',
                        r'([\u3400-\u9fff\u3040-\u30ffA-Za-zＡ-Ｚａ-ｚ0-9_]{2,20})について'):
            for found in re.finditer(pattern,text):
                start,end=found.span(1)
                if '的' in text[start:end] and text[start:end].index('的') >= 2:
                    end=start+text[start:end].index('的')
                if _quoted(text,start): continue
                surface=text[start:end]
                if any(a<=start and b>=end for a,b,*_ in matches): continue
                if any(normalized(surface)==normalized(x['surface']) and
                       x['evidence'][0]['id']==source['id'] for x in suggestions): continue
                language=_language(surface,text[max(0,start-20):min(len(text),end+20)])
                suggestions.append({'tagId':None,'surface':surface,'language':language,
                    'evidence':[{'kind':source['kind'],'id':source['id'],
                                 'start':found.start(),'end':found.end()}],
                    'evidenceText':text[found.start():found.end()]})
    return {'hits':hits,'suggestions':suggestions,'reasonCodes':sorted(set(reasons))}
