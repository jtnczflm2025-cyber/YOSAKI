"""Topic titles for import drafts. The segmented source receipt remains immutable."""
import json
import re
import unicodedata

from . import memory_v2_metadata as metadata
from . import memory_v2_processing as work

VERSION = 'chronicle-import-topic-title-v2'
INSTRUCTIONS = ('归纳整段经历的主要话题与具体事件，写一个可独立理解的简短中文标题。'
    '采用具体的名词或动宾短语，保留未定、提议、玩笑等会改变含义的限定。'
    '不要复述开场白、摘取首句、代角色发言、堆砌关键词或使用“日常对话”等泛称。'
    '不同经历即使开场相同也要按实际内容分别命名。只写标题主题，不加日期、编号。')


def _plain(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def reading_title(db,eid,cid):
    entry=metadata._entry(db,eid,cid)
    version=metadata._set(db,entry)
    metadata._validate_sources(db,entry,version)
    field=metadata._fields(db,version['id']).get('title')
    if not field:
        return None
    deps=json.loads(field['dependencies_json'])
    if set(deps)!={'source'}:
        return metadata.read_draft(db,eid,cid)['fields']['title'].get('value')
    value=json.loads(field['value_json'])
    token=metadata.c.token(['title',value,field['provenance'],field['rule_version'],json.loads(field['evidence_json']),deps])
    return value if token==field['token'] and deps['source']==version['source_token'] else None


def prepare(db, batch, eid, detail):
    seg=db.execute("""SELECT s.*,e.status AS entry_status,e.active_version_id
        FROM mv2_import_segments s JOIN mv3_entries e ON e.id=s.entry_id
        WHERE s.batch_id=? AND s.entry_id=?""",(batch,eid)).fetchone()
    if not seg or seg['entry_status']!='draft' or seg['active_version_id']:
        return None
    field=metadata._fields(db,detail['versionId']).get('title')
    if (not field or field['provenance']!='system_suggested' or field['locked']
            or field['rule_version']!='h31-title-suggestion-v1'
            or db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',(field['id'],)).fetchone()):
        return None
    original=db.execute("""SELECT r.occurred_date,m.role,m.content FROM mv2_import_receipts r
        JOIN messages m ON m.id=r.message_id WHERE r.file_id=? AND r.ordinal BETWEEN ? AND ?
        ORDER BY r.ordinal""",(seg['file_id'],seg['start_ordinal'],seg['end_ordinal'])).fetchall()
    if not original:return None
    lead=next((r['content'] for r in original if r['role']=='user' and len(r['content'].strip())>=5),original[0]['content'])
    excerpt=' '.join(lead.split())
    prefix=f"{seg['ordinal']:02d} · {original[0]['occurred_date'] or '日期未核实'} · "
    legacy=prefix+((excerpt[:28]+'…' if len(excerpt)>28 else excerpt) or '对话片段')
    value=json.loads(field['value_json'])
    if value!=seg['title'] or value not in (legacy,prefix+'主题待整理'):
        return None
    return dict(prefix=prefix,first=lead,token=field['token'],source=detail['sourceToken'])


def format_topic(topic, target):
    if not isinstance(topic,str) or '\n' in topic or '\r' in topic:
        raise ValueError('import_title_invalid')
    topic=topic.strip()
    key=_plain(topic)
    lead=_plain(target['first'])
    if (not 2<=len(topic)<=60 or len(key)<2 or key in ('日常对话','对话片段','聊天记录','主题待整理')
            or (lead and (lead==key or len(key)>=8 and lead.startswith(key)))
            or re.match(r'^\d+\s*[·、]|^\d{4}[-年]',topic)):
        raise ValueError('import_title_needs_topic')
    return target['prefix']+topic


def save(db,batch,eid,cid,owner,target,topic,*,job_id=None):
    current=metadata.read_draft(db,eid,cid)
    if not target or prepare(db,batch,eid,current)!=target:
        raise ValueError('import_title_changed')
    scope=work._scope(cid,'entry',eid,['title'])
    work._block_overlaps(db,cid,scope,['title'],except_id=job_id)
    return metadata.revise(db,eid,cid,current['versionId'],{'title':format_topic(topic,target)},owner,
                           provenance='model',rule=VERSION)
