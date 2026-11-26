"""Ground topical filing suggestions in the imported entry's own messages."""
import re
import uuid
from . import memory_v2_metadata as metadata
from . import memory_v2_processing as work
from . import memory_v2_vocabulary as vocabulary
from . import memory_v2_tags as tags

VERSION = 'chronicle-import-topic-filing-v1'
FIELDS = ('categories', 'tags', 'keywords')
CATEGORIES = tuple(name for names in metadata.c.CATEGORIES.values() for name in names)
# Stable navigation topics. Only a used term is installed. Assignment to an
# entry is a versioned draft; it has no owner check until explicitly reviewed.
TOPICS = ('软件与功能','记忆与身份','提醒与日程','声音与音乐','影像与创作','阅读与学习',
          '工作安排','饮食与口味','身体感受','休息与睡眠','天气与出行','动物照护',
          '手作与物品','关系与陪伴','个人边界','沟通与误会','感官体验','未来设想',
          '生活安排','地点与旅行','家庭与朋友','游戏与兴趣','购买与选择','共同约定')
PROMPT = (
    '另在根对象提供 filing。categories 与 tags 都是 [名称,原话锚点] 的数组；'
    'keywords 是这段原话中实际出现的辨识度高的具体词或连续短语数组。'
    '分类从 '+ '、'.join(CATEGORIES)+' 选 1～3 项；主题标签从 '+ '、'.join(TOPICS)+' 选最相关的 1～2 项。'
    '各项锚点须是支持该话题的原话。关键词优先物名、人名、作品名、具体功能或事件；'
    '要能用来找回这一段。不要选参与者的常用称呼、但是、我说过、整句、动作描写套话或残词。'
    '中日文同义表达择一。玩笑、否定和假设只作讨论主题，不变成事实或诊断。'
    '仅在有依据时选，允许少给或空数组。'
)
NOISE = set('但是 可是 然后 所以 不过 因为 其实 我说过 说过 你说 我说 只是 这个 那个 真的 知道 没事 好的 哈哈 哈哈哈 イキヤ 阿夜 语气恢复 语气 声音低沉 sender assistant user でも ただ そして だから そうだね ありがとう'.split())


def writable(db,detail,cid):
    entry=metadata._entry(db,detail['entryId'],cid)
    if entry['status']!='draft' or entry['active_version_id']:
        return []
    stored=metadata._fields(db,detail['versionId'])
    allowed=[]
    for name in FIELDS:
        f=stored.get(name)
        if f is None:
            allowed.append(name)
        elif (f['provenance']=='rule' and not f['locked'] and f['value_json']!='null'
              and not db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',(f['id'],)).fetchone()):
            allowed.append(name)
    return allowed


def point(sources,term):
    if not isinstance(term,str) or not term.strip() or len(term)>100:
        return None
    for s in sources:
        start=s['text'].find(term)
        if start>=0:
            return dict(kind=s['kind'],id=s['id'],start=start,end=start+len(term))
    return None


def keyword_values(words,sources):
    if not isinstance(words,list):
        raise ValueError('import_filing_invalid')
    result=[]; seen=set()
    for word in words[:24]:
        if not isinstance(word,str):
            continue
        word=word.strip()
        key=metadata.c.normalized(word)
        if (not key or key in seen or key in NOISE or len(word)>32
                or len(key)==1 and not any('《'+word+'》' in s['text'] for s in sources)
                or re.search(r'[\n。！？!?；;，,]|^(?:我说过|但是|所以说|其实我)|^(.)\1{2,}$',word)):
            continue
        hit=point(sources,word)
        if hit:
            language='ja' if re.search(r'[ぁ-ヿ]',word) else 'en' if word.isascii() else 'zh'
            result.append(dict(text=word,language=language,kind='literal',field='raw',weight=1,evidence=[hit]))
            seen.add(key)
        if len(result)==8:break
    # Keep the specific phrase when the shorter term only repeats part of it.
    return [item for item in result if not any(
        item is not other and metadata.c.normalized(item['text']) in metadata.c.normalized(other['text'])
        for other in result)]


def term_for(db,cid,owner,eid,kind,name):
    if kind=='category':
        return vocabulary.match(db,cid,kind,name,'zh') if name in CATEGORIES else None
    if name not in TOPICS:return None
    existing=vocabulary.match(db,cid,'tag',name,'zh')
    if existing:
        return None if tags.merge_guard(db,cid,eid,existing['vocabulary_id']) else existing
    vid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'yosaki/import-topic/{cid}/{name}'))
    if (db.execute('SELECT 1 FROM mv3_vocabulary WHERE id=?',(vid,)).fetchone()
            or tags.suggestion_guard(db,cid,eid,name,'zh')):
        return None
    return vocabulary.write(db,cid,'tag',name,'zh',owner,seed_id=vid,definition='讨论主题：'+name,
                            seed_provenance='system_suggested')


def save(db,cid,owner,detail,sources,proposal,*,job_id=None):
    work._transaction(db)
    metadata._owner(db,owner)
    eid=detail['entryId']
    current=metadata.read_draft(db,eid,cid)
    if current['versionId']!=detail['versionId']:
        raise ValueError('version_conflict')
    fields=writable(db,current,cid)
    if not fields:return []
    if not isinstance(proposal,dict) or any(not isinstance(proposal.get(k),list) for k in FIELDS):
        raise ValueError('import_filing_invalid')
    work._block_overlaps(db,cid,work._scope(cid,'entry',eid,fields),fields,except_id=job_id)
    changes={}
    for field in fields:
        if field=='keywords':
            values=keyword_values(proposal[field],sources)
        else:
            kind='category' if field=='categories' else 'tag'
            values=[];seen=set()
            for pair in proposal[field][:12]:
                if not isinstance(pair,list) or len(pair)!=2 or not isinstance(pair[0],str):continue
                name,anchor=pair
                hit=point(sources,anchor)
                if not hit or name in seen:continue
                term=term_for(db,cid,owner,eid,kind,name)
                if term:
                    values.append(dict(id=term['vocabulary_id'],evidence=[hit]))
                    seen.add(name)
                if len(values)>=(3 if field=='categories' else 2):break
        if values:changes[field]=values
    if changes:
        metadata.revise(db,eid,cid,current['versionId'],changes,owner,provenance='model',rule=VERSION)
    return [name for name in fields if name not in changes]
