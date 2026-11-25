"""Bounded Responses SSE decoding and public receipts, with no dispatch authority."""
import hashlib
import json
import re
import time
from pathlib import Path
from uuid import UUID
from datetime import datetime, timezone

VERSION = 'responses-sse-public-events-v1'
MAX_BYTES = 8*1024*1024
MAX_FRAME_CHARS = 262144
MAX_EVENTS = 16384
MAX_PUBLIC_CHARS = 65536
LIMITS = dict(maxBytes=MAX_BYTES,maxFrameChars=MAX_FRAME_CHARS,
              maxEvents=MAX_EVENTS,maxPublicChars=MAX_PUBLIC_CHARS)
TERMINAL = {'response.completed':'completed','response.incomplete':'incomplete',
            'response.failed':'failed','response.cancelled':'cancelled'}


def write_public_event(directory, record):
    """One bounded public receipt per event, independently of money writes."""
    if set(record) != {'kind','jobId','ordinal','dispatchAttemptId','eventIndex','event'}:
        raise ValueError('stream_receipt_binding_invalid')
    event=record['event']
    if not isinstance(event,dict) or type(record['eventIndex']) is not int or not 1<=record['eventIndex']<=MAX_EVENTS:
        raise ValueError('stream_receipt_invalid')
    blob=json.dumps(record,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len(blob.encode('utf-8')) > 262144:raise ValueError('stream_receipt_over_limit')
    name=f"stream_{UUID(record['jobId'])}_{record['ordinal']}_{UUID(record['dispatchAttemptId'])}_{record['eventIndex']}.json"
    path=Path(directory)/name
    try:
        with path.open('x',encoding='utf-8') as f:f.write(blob)
    except FileExistsError:
        if path.read_text(encoding='utf-8')!=blob:raise ValueError('stream_receipt_conflict') from None
    return path

def safe_text(value):
    return re.sub(r'\bsk-[A-Za-z0-9_-]*','[redacted key]',
        re.sub(r'(?i)\bBearer\s+\S+','[redacted bearer]',value))

def public_receipt(data):
    if not isinstance(data,dict):raise ValueError('stream_response_invalid')
    texts=[];refusals=[]
    for item in data.get('output') or []:
        if item.get('type')!='message':continue
        for content in item.get('content') or []:
            if content.get('type')=='output_text':texts.append(content.get('text') or '')
            elif content.get('type')=='refusal':refusals.append(content.get('refusal') or '')
    usage=data.get('usage')
    if not isinstance(usage,dict) or len(json.dumps(usage))>4096:usage=None
    result=dict(responseId=data.get('id'),modelId=data.get('model'),status=data.get('status'),
        usage=usage,serviceTier=data.get('service_tier'),
        incompleteReason=(data.get('incomplete_details') or {}).get('reason'),
        text=safe_text(''.join(texts))[:MAX_PUBLIC_CHARS],
        refusalText=safe_text(''.join(refusals))[:MAX_PUBLIC_CHARS])
    for field in ('responseId','modelId','status','serviceTier','incompleteReason'):
        if result[field] is not None:result[field]=safe_text(str(result[field]))[:200]
    return result

class Events:
    def __init__(self,started,sink=None):
        self.started=started;self.sink=sink;self.line_buffer='';self.data_lines=[];self.event_name=None
        self.frame_chars=0;self.count=0;self.last_sequence=None;self.response_id=None;self.model=None
        self.parts={};self.refusals={};self.terminal=None;self.first_event=None;self.first_public=None
        self.last_event=None;self.max_gap=0.;self.diagnostic_errors=0;self.integrity_error=None
        self.saved_text='';self.saved_refusal='';self.ignored=0
    def _retain(self,row):
        if self.sink is None:return
        try:self.sink(row)
        except Exception:self.diagnostic_errors+=1
    def text(self):return ''.join(self.parts[k] for k in sorted(self.parts))
    def _public_progress(self,record):
        text=safe_text(self.text());refusal=safe_text(''.join(self.refusals[k] for k in sorted(self.refusals)))
        for current,attribute,label in ((text,'saved_text','publicText'),(refusal,'saved_refusal','refusal')):
            old=getattr(self,attribute)
            if current!=old:
                record[label+('Append' if current.startswith(old) else 'Replace')]=current[len(old):] if current.startswith(old) else current
                setattr(self,attribute,current)
    def _line(self,line):
        if line.endswith('\r'):line=line[:-1]
        if line=='':
            if self.data_lines:
                payload='\n'.join(self.data_lines);self.data_lines=[]
                name=self.event_name;self.event_name=None;self.frame_chars=0
                if payload!='[DONE]':self._event(json.loads(payload),name)
            else:self.event_name=None;self.frame_chars=0
            return
        self.frame_chars+=len(line)
        if self.frame_chars>MAX_FRAME_CHARS:raise ValueError('stream_frame_over_limit')
        if line.startswith('data:'):self.data_lines.append(line[5:].lstrip(' '))
        elif line.startswith('event:'):self.event_name=line[6:].strip()
        # Comments, id and retry lines never cause automatic reconnection.
    def feed(self,text):
        self.line_buffer+=text
        while self.terminal is None:
            match=re.search(r'[\r\n]',self.line_buffer)
            if match is None:break
            index=match.start();mark=self.line_buffer[index]
            if mark=='\r' and index==len(self.line_buffer)-1:break
            width=2 if mark=='\r' and self.line_buffer[index+1:index+2]=='\n' else 1
            line,self.line_buffer=self.line_buffer[:index],self.line_buffer[index+width:];self._line(line)
        if len(self.line_buffer)>MAX_FRAME_CHARS:raise ValueError('stream_line_over_limit')
    def finish(self):
        # A final CR is itself an actual SSE line delimiter, not invented data.
        if self.line_buffer.endswith('\r'):self.feed('\n')
    def _event(self,event,name):
        if not isinstance(event,dict) or not isinstance(event.get('type'),str):raise ValueError('stream_event_invalid')
        kind=event['type'];sequence=event.get('sequence_number')
        if name is not None and name!=kind:raise ValueError('stream_event_name_conflict')
        if sequence is not None:
            if type(sequence) is not int or sequence<0 or (self.last_sequence is not None and sequence<=self.last_sequence):
                raise ValueError('stream_sequence_invalid')
            self.last_sequence=sequence
        self.count+=1
        if self.count>MAX_EVENTS:raise ValueError('stream_events_over_limit')
        now=time.perf_counter()-self.started
        if self.first_event is None:self.first_event=now
        if self.last_event is not None:self.max_gap=max(self.max_gap,now-self.last_event)
        self.last_event=now
        record=dict(type=kind,sequence=sequence,elapsedSeconds=round(now,6),
                    utc=datetime.now(timezone.utc).isoformat())
        if kind in ('response.created','response.in_progress') or kind in TERMINAL:
            response=event.get('response')
            if not isinstance(response,dict) or not isinstance(response.get('id'),str) or not response['id']:
                raise ValueError('stream_identity_missing')
            if self.response_id is not None and response['id']!=self.response_id:raise ValueError('stream_identity_changed')
            self.response_id=response['id'];self.model=response.get('model') or self.model
            record.update(responseId=safe_text(self.response_id)[:200],status=response.get('status'))
            if kind in TERMINAL:
                if response.get('status')!=TERMINAL[kind]:self.integrity_error='stream_terminal_status_conflict'
                self.terminal=response
                try:public=public_receipt(response)
                except (ValueError,TypeError,AttributeError,KeyError):
                    self.integrity_error='stream_terminal_public_shape_invalid'
                    public=self.partial();public.update(responseId=self.response_id,status=response.get('status'),
                        usage=response.get('usage'),modelId=response.get('model'),serviceTier=response.get('service_tier'))
                record['response']=public
                if self.parts and public['text']!=safe_text(self.text()):self.integrity_error='stream_public_text_conflict'
                if public['text'] and self.first_public is None:self.first_public=now
        elif kind in ('response.output_text.delta','response.output_text.done','response.refusal.delta','response.refusal.done'):
            output_index=event.get('output_index',0);content_index=event.get('content_index',0)
            if type(output_index) is not int or type(content_index) is not int or min(output_index,content_index)<0:
                raise ValueError('stream_public_identity_invalid')
            key=(output_index,content_index);target=self.parts if 'output_text' in kind else self.refusals
            value=event.get('delta') if kind.endswith('.delta') else event.get('text',event.get('refusal'))
            if not isinstance(value,str):raise ValueError('stream_public_text_invalid')
            if kind.endswith('.delta'):target[key]=target.get(key,'')+value
            else:
                if key in target and target[key]!=value:self.integrity_error='stream_public_done_conflict'
                target[key]=value
            if sum(map(len,self.parts.values()))+sum(map(len,self.refusals.values()))>MAX_PUBLIC_CHARS:
                raise ValueError('stream_public_text_over_limit')
            if 'output_text' in kind and value and self.first_public is None:self.first_public=now
            record.update(outputIndex=output_index,contentIndex=content_index)
            self._public_progress(record)
        else:
            # No reasoning text, summary, encrypted reasoning or tool arguments
            # are retained. The event name/time alone supports gap measurement.
            self.ignored+=1
        self._retain(record)
    def observation(self):
        elapsed=time.perf_counter()-self.started
        return dict(version=VERSION,eventCount=self.count,ignoredContentEvents=self.ignored,
            firstCompleteEventSeconds=None if self.first_event is None else round(self.first_event,6),
            firstPublicTextSeconds=None if self.first_public is None else round(self.first_public,6),
            terminalSeconds=round(self.last_event,6) if self.terminal is not None else None,
            maxInterEventGapSeconds=round(self.max_gap,6),
            tailSilenceSeconds=round(elapsed-(self.last_event or 0),6),
            unparsedTailCharacters=len(self.line_buffer)+sum(map(len,self.data_lines)),
            unparsedTailHash=hashlib.sha256(('\n'.join(self.data_lines)+self.line_buffer).encode('utf8')).hexdigest(),
            diagnosticErrors=self.diagnostic_errors,integrityError=self.integrity_error)
    def partial(self):
        return dict(responseId=self.response_id,modelId=self.model,status='in_progress',usage=None,serviceTier=None,
            text=safe_text(self.text())[:MAX_PUBLIC_CHARS],refusalText=safe_text(''.join(self.refusals.values()))[:MAX_PUBLIC_CHARS],incompleteReason=None)

class FileJournal:
    """Append-only bounded public evidence; cannot mutate a task or its ledger."""
    def __init__(self,path,context,cap=16*1024*1024):
        self.path=path;self.context=dict(context);self.cap=cap;self.bytes=0;self.previous='0'*64
    def __call__(self,event):
        import os
        record=dict(context=self.context,previousHash=self.previous,event=event)
        body=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
        if self.bytes+len(body)+1>self.cap:raise ValueError('stream_journal_over_limit')
        with self.path.open('ab') as file:file.write(body+b'\n');file.flush();os.fsync(file.fileno())
        self.previous=hashlib.sha256(body).hexdigest();self.bytes+=len(body)+1
