"""Shared final serialization for preview, ordinary chat and tool continuation."""
from . import responses_adapter, chatgpt_plan


def serialize(profile,messages,tools):
    if profile['protocol']=='chatgpt_plan':
        return chatgpt_plan.payload(profile,messages,tools),chatgpt_plan.API_BASE+'/responses'
    if profile['protocol']=='openai_responses':
        return responses_adapter.payload(profile,messages,tools,True),responses_adapter.endpoint(profile['base_url'])
    body={'model':profile['model_name'],'messages':messages,'temperature':profile['temperature'],
        'top_p':profile['top_p'],'max_tokens':profile['max_tokens'],'stream':True,'stream_options':{'include_usage':True}}
    if profile['reasoning_effort']:body['reasoning_effort']=profile['reasoning_effort']
    if tools:body.update(tools=tools,tool_choice='auto',parallel_tool_calls=False)
    base=profile['base_url'].rstrip('/')
    return body,base if base.endswith('/chat/completions') else base+'/chat/completions'


def safe_history(db,history,profile):
    output=[]
    for source in history:
        row=dict(source)
        if row.get('provider_state_json'):
            attempt=db.execute('SELECT protocol,requested_model FROM chat_attempts WHERE assistant_message_id=?',(row['id'],)).fetchone()
            if not attempt or attempt['protocol']!=profile['protocol'] or attempt['requested_model']!=profile['model_name']:
                row['provider_state_json']=None
        output.append(row)
    return output
