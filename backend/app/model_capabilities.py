"""Conservative protocol contracts. Saved limits are not provider guarantees."""
from fastapi import HTTPException


def describe(profile):
    p=dict(profile)
    protocol=p['protocol']
    return {'protocol':protocol,'model':p['model_name'],
        'output_limit':{'supported':None if protocol=='chatgpt_plan' else True,
            'wire_field':None if protocol=='chatgpt_plan' else 'max_output_tokens' if protocol=='openai_responses' else 'max_tokens',
            'evidence':'unverified_subscription_channel' if protocol=='chatgpt_plan' else 'protocol_contract_not_model_acceptance'},
        'temperature':protocol=='openai_compatible', 'top_p':protocol=='openai_compatible',
        'reasoning_efforts':['low','medium','high'], 'vision':'unknown',
        'note':'模型能力仍需具体通道证据；不使用客户端停流保证输出上限或费用。'}


def effective(profile, overrides):
    p=dict(profile); caps=describe(p)
    if not p.get('supports_streaming',True):raise HTTPException(422,'此档案标记为不支持流式；当前聊天仅接入流式通道，未派发')
    for key,value in (overrides or {}).items():
        if key not in ('max_tokens','temperature','top_p','reasoning_effort'):
            raise HTTPException(422,'未知单次模型设置')
        if key=='max_tokens':
            if caps['output_limit']['supported'] is not True:
                raise HTTPException(422,'套餐通道尚无输出硬上限能力证据；此设置不能生效或保证费用')
            if type(value) is not int or not 1<=value<=128000: raise HTTPException(422,'输出上限无效')
        elif key=='reasoning_effort':
            if value not in ('low','medium','high',None): raise HTTPException(422,'此推理强度尚未接入')
        elif not caps[key]:
            raise HTTPException(422,'此通道没有接入该参数，请取消单次覆盖')
        elif type(value) not in (float,int) or not (0<=value<= (2 if key=='temperature' else 1)):
            raise HTTPException(422,'模型参数无效')
        p[key]=value
    return p


def effects(profile, body):
    p=dict(profile)
    mapping={'max_tokens':'max_output_tokens' if p['protocol']!='openai_compatible' else 'max_tokens',
        'temperature':'temperature','top_p':'top_p','reasoning_effort':'reasoning_effort'}
    result={}
    for key,wire in mapping.items():
        value=(body.get('reasoning') or {}).get('effort') if key=='reasoning_effort' and p['protocol']!='openai_compatible' else body.get(wire)
        result[key]={'requested':p.get(key),'serialized':value,'state':'serialized' if value is not None else 'not_selected' if key=='reasoning_effort' else 'not_connected','provider_acceptance':'unverified'}
    if p['protocol']=='chatgpt_plan':
        result['max_tokens']['evidence']='unverified_subscription_channel'
        result['max_tokens']['hard_limit_confirmed']=False
    return result
