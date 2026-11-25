"""Read-only Chronicle tools reusing chat's persisted confirmation lifecycle.

Only references are stored in tool messages. Every subsequent model request
rehydrates qualified content, so old tool history cannot leak revoked text.
"""
import json
import os
from . import memory_v2_chronicle_channel as channel, memory_v2_entries as entries

SERVER = -34  # Reserved internal marker, never an MCP transport or server row.
NAMES = ('yosaki_chronicle_search', 'yosaki_chronicle_expand')


def definitions():
    common = {'type': 'object', 'additionalProperties': False}
    schemas = [dict(common, properties={'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}, required=['query']),
        dict(common, properties={'entryId': {'type':'string','maxLength':100},
            'offset':{'type':'integer','minimum':0},'versionId':{'type':'string','maxLength':100}},
            required=['entryId','offset','versionId'])]
    descriptions = ['查找已确认的 Chronicle 记忆，返回按时间层级选择的精修记录或摘要；不是当前聊天记录搜索。',
                    '按经历身份展开完整精修对话。首次 offset=0、versionId=空字符串；续页使用上次版本及 nextOffset。不返回原始对话。']
    definitions, mapping = [], {}
    for name, schema, description in zip(NAMES, schemas, descriptions):
        definitions.append({'type':'function','function':{'name':name,'description':description,'parameters':schema}})
        mapping[name] = dict(id=None, server_id=SERVER, name=name, server_name='Chronicle',
            display_name='查阅记忆' if name==NAMES[0] else '展开完整记忆', description=description,
            input_schema=schema, plugin_tool_id=None, plugin_name=None)
    return definitions, mapping


def execute(db, cid, owner_id, name, arguments):
    channel.authorized(db, cid, owner_id)
    if os.getenv('YOSAKI_CHRONICLE_CONTEXT','0') != '1' or name not in NAMES:
        raise ValueError('chronicle_tools_disabled')
    if name == NAMES[0]:
        from . import memory_v2_chronicle_embedding as embedding
        result = embedding.cached_search(db, cid, owner_id, arguments['query'])
        ids = list(dict.fromkeys(eid for block in result['blocks'] for eid in block['entryIds']))
    else:
        result = channel.expand(db, cid, owner_id, arguments['entryId'], offset=arguments['offset'],
            expected_version=arguments['versionId'] or None)
        ids = [arguments['entryId']]
    bound = []
    for eid in ids:
        d = entries.read_entry(db,eid,cid); n = channel._narrative(db,cid,d)
        bound.append({'entryId':eid,'versionId':d['versionId'],'narrativeId':n['id']})
    receipt = {'chronicleReceipt':1,'characterId':cid,'ownerId':owner_id,'action':name,
               'entries':bound,'offset':arguments.get('offset',0)}
    return result, receipt


def hydrate(db, cid, owner_id, name, receipt):
    channel.authorized(db,cid,owner_id)
    if (os.getenv('YOSAKI_CHRONICLE_CONTEXT','0')!='1' or name not in NAMES
            or receipt.get('chronicleReceipt')!=1 or receipt.get('characterId')!=cid
            or receipt.get('ownerId')!=owner_id or receipt.get('action')!=name):
        raise ValueError('chronicle_reference_unavailable')
    bound=receipt.get('entries')
    if not isinstance(bound,list) or len(bound)>20:
        raise ValueError('chronicle_reference_invalid')
    for row in bound:
        d=entries.read_entry(db,row['entryId'],cid); n=channel._narrative(db,cid,d)
        if d['versionId']!=row['versionId'] or n['id']!=row['narrativeId']:
            raise ValueError('chronicle_reference_changed')
    if name==NAMES[0]:
        return channel.project(db,cid,owner_id,[r['entryId'] for r in bound])
    if len(bound)!=1:
        raise ValueError('chronicle_reference_invalid')
    return channel.expand(db,cid,owner_id,bound[0]['entryId'],offset=receipt['offset'],expected_version=bound[0]['narrativeId'])


def refresh_history(db, cid, owner_id, history):
    result=[]
    for original in history:
        row=dict(original)
        if row.get('role')=='tool' and row.get('tool_name') in NAMES:
            try:
                if owner_id is None:
                    raise ValueError('chronicle_owner_unavailable')
                value=hydrate(db,cid,owner_id,row['tool_name'],json.loads(row.get('content') or '{}'))
            except (ValueError, TypeError, KeyError):
                value={'state':'unavailable','message':'这份记忆当前不可读取；不返回旧内容或原始对话。'}
            row['content']=json.dumps(value,ensure_ascii=False)
        result.append(row)
    return result
