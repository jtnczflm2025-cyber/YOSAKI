"""Branch-aware context diagnostics and bounded complete-turn selection."""
import json
import math
from . import chat_history
from fastapi import HTTPException

STATES=('included','not_connected','disabled','not_selected','eligibility_rejected','budget_rejected','error')


def trace(db,cid,settings,memories,summary,advisory,chronicle):
    def row(channel,state,reason=''):
        return {'channel':channel,'state':state,'reason':reason}
    output=[row('structured_volition','not_connected'),row('psychology','not_connected'),row('learning_strategy','not_connected')]
    enabled=bool(settings.get('memory_enabled'))
    learning_ids={r[0] for r in db.execute('SELECT memory_id FROM learning_memories WHERE memory_id IS NOT NULL')}
    manual_included=any(x.get('channel')!='chronicle' and x['id'] not in learning_ids for x in map(dict,memories))
    output.append(row('manual_memory','included' if manual_included else 'disabled' if not enabled else 'eligibility_rejected' if chat_history.scope(db,cid) and db.execute('SELECT 1 FROM memories WHERE character_id=? AND enabled=1',(settings.get('character_id'),)).fetchone() else 'not_selected'))
    control=db.execute('SELECT enabled FROM desire_settings WHERE character_id=?',(settings.get('character_id'),)).fetchone() if settings.get('character_id') else None
    output.append(row('drive_advisory','included' if advisory else 'disabled' if not control or not control[0] else 'not_selected'))
    learned_included=any(x['id'] in learning_ids for x in memories)
    isolated=chat_history.scope(db,cid)
    rejected_learning=bool(isolated and db.execute('SELECT 1 FROM learning_memories WHERE character_id=? AND status=?',(settings.get('character_id'),'accepted')).fetchone())
    output.append(row('approved_learning_memory','included' if learned_included else 'disabled' if not enabled else 'eligibility_rejected' if rejected_learning else 'not_selected',
        '旧学习投影没有分支来源覆盖证明' if enabled and rejected_learning else ''))
    summary_candidates=[cid,*isolated['family']] if isolated else [cid]
    old_summary=db.execute('SELECT enabled FROM conversation_summaries WHERE conversation_id IN ('+','.join('?' for _ in summary_candidates)+') AND content!=? ORDER BY enabled DESC LIMIT 1',[*summary_candidates,'']).fetchone()
    # The legacy summary switch is independent from the memory switch.
    summary_state='included' if summary else 'disabled' if old_summary and not old_summary[0] else 'eligibility_rejected' if isolated and old_summary else 'not_selected'
    output.append(row('conversation_summary',summary_state,'旧手填摘要没有覆盖范围，不自动继承' if summary_state=='eligibility_rejected' else ''))
    if not enabled: state='disabled'
    elif not chronicle.get('enabled'): state='error' if chronicle.get('reason')=='current_content_or_capacity_unavailable' else 'disabled'
    elif any(dict(x).get('channel')=='chronicle' for x in memories): state='included'
    else: state='not_selected'
    output.append(row('chronicle',state,chronicle.get('reason','')))
    for item in chronicle.get('selectionDetails',[]):
        reason=item.get('reason','')
        output.append(dict(item,channel='chronicle_entry',state='included' if item.get('included') else 'budget_rejected' if 'budget' in reason else 'eligibility_rejected' if reason else 'not_selected'))
    return {'channels':output,'history_ids':[r['id'] for r in chat_history.rows(db,cid)] if cid else [],
            'memory_ids':[r['id'] for r in memories],
            'memory_refs':[{'id':r['id'],'sha256':chat_history.digest(r['content'])} for r in memories],
            'branch_isolated':bool(chat_history.scope(db,cid))}


def estimate(value):
    # Conservative UTF-8 upper bound, including protocol structure. Not billing.
    return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))


def constrain(messages,tools,profile,policy):
    if not policy or not policy['enabled'] or not profile:
        return messages, {'enabled':False,'method':'utf8_upper_bound_not_billing'}
    capacity=profile['context_limit'];reserve=profile['max_tokens']
    if not capacity or capacity<=reserve:
        raise HTTPException(422,'上下文容量未知或不够预留输出，请核对模型档案')
    budget=capacity-reserve
    current=estimate({'messages':messages,'tools':tools})
    result=list(messages); removed=0
    if current>math.floor(budget*policy['trigger_ratio']):
        target=math.floor(budget*policy['target_ratio'])
        while estimate({'messages':result,'tools':tools})>target:
            users=[i for i,m in enumerate(result) if m['role']=='user']
            if len(users)<=1:break
            start,end=users[0],users[1]
            # Keep late system controls; remove whole user/assistant/tool round.
            result=result[:start]+[m for m in result[start:end] if m['role']=='system']+result[end:]
            removed+=1
    final=estimate({'messages':result,'tools':tools})
    if final>budget:
        raise HTTPException(422,'固定参考与本条输入超过预算；未截断正文，请减少附件或调整容量')
    return result,{'enabled':True,'method':'utf8_upper_bound_not_billing','before':current,'after':final,
        'input_budget':budget,'output_reserve':reserve,'removed_turns':removed,'capacity_source':'saved_profile_unverified'}


def summary(db,cid):
    active={r['id']:chat_history.digest(r['content'] or '') for r in chat_history.rows(db,cid)}
    for row in db.execute('SELECT * FROM chat_summaries WHERE conversation_id=? AND enabled=1 ORDER BY created_at DESC',(cid,)):
        refs=json.loads(row['coverage_json'])
        if refs and all(active.get(ref['id'])==ref['sha256'] for ref in refs):
            return dict(row)
    return None


def uncovered(db,cid,rows):
    covered=summary(db,cid)
    if not covered:return rows
    ids={r['id'] for r in json.loads(covered['coverage_json'])}
    return [r for r in rows if r['id'] not in ids]


def sources_current(db,attempt,manifest):
    cid=attempt['conversation_id'];character=attempt['character_id']
    current={r['id']:r for r in chat_history.rows(db,cid)}
    if not set(manifest.get('history_ids',[])).issubset(current):return False
    for ref in manifest.get('history_refs',[]):
        if ref['id'] not in current or chat_history.digest(current[ref['id']]['content'] or '')!=ref['sha256']:return False
    if manifest.get('memory_refs'):
        from . import memory_authority
        manual,approved=memory_authority.eligible_rows(db,character)
        qualified=[*manual,*[r for r,_ in approved if not chat_history.scope(db,cid)]]
        chronicle_refs = [ref for ref in manifest['memory_refs'] if str(ref['id']).startswith('chronicle:')]
        if chronicle_refs:
            from . import memory_v2_chronicle_channel as channel
            selected = [item['entryId'] for item in manifest.get('channels', [])
                        if item.get('channel') == 'chronicle_entry' and item.get('entryId')]
            if not selected:
                selected = list(dict.fromkeys(eid for ref in chronicle_refs
                                              for eid in ref['id'][10:].split(',')))
            with chat_history.reading(db,cid):
                owner = db.execute('SELECT id FROM users LIMIT 1').fetchone()
                try:
                    blocks = channel.project(db,character,owner[0] if owner else None,selected)['blocks']
                except (ValueError,TypeError):
                    return False
            current_blocks = {('chronicle:' + ','.join(block['entryIds']), chat_history.digest(block['text']))
                              for block in blocks}
            if any((ref['id'],ref['sha256']) not in current_blocks for ref in chronicle_refs):
                return False
        for ref in manifest['memory_refs']:
            if str(ref['id']).startswith('chronicle:'):
                continue
            else:
                row=next((r for r in qualified if r['id']==ref['id']),None)
                if not row or chat_history.digest(row['content'])!=ref['sha256'] or not chat_history.memory_allowed(db,row,cid):return False
    return True
