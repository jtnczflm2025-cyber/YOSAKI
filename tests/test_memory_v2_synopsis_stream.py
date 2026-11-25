"""Streaming transport through actual A3 coordinator. Synthetic identities only."""
import copy,json,sqlite3,time,unittest,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import httpx
import test_memory_v2_a34_diagnostics as diagnostics
from test_memory_v2_h32 import app_module
from test_memory_v2_a34_responses_wiring import _output_for,MODEL,PRICE
from backend.app import memory_v2_synopsis as s,memory_v2_responses_adapter as r
from backend.app import memory_v2_responses_stream as streams,memory_v2_metadata as metadata

def frame(data):return ('event: '+data['type']+'\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode('utf8')
class Chunks(httpx.SyncByteStream):
    def __init__(self,pieces,cut=False):self.pieces=pieces;self.cut=cut
    def __iter__(self):
        for p in self.pieces:
            # Deliberately split multibyte UTF-8 and SSE frame delimiters.
            for i in range(0,len(p),19):yield p[i:i+19]
        if self.cut:raise httpx.RemoteProtocolError('synthetic midway disconnection')

class Wire:
    def __init__(self,*,cut=False,usage=True,bad_json=False,wrong_quote=False,terminal='completed',
                 sink=None,on_generation=None,conflict=False,shape_invalid=False,unclassified_usage=False):
        self.cut=cut;self.usage=usage;self.bad_json=bad_json;self.wrong_quote=wrong_quote;self.terminal=terminal
        self.on_generation=on_generation;self.conflict=conflict;self.shape_invalid=shape_invalid
        self.unclassified_usage=unclassified_usage
        self.counts=[];self.generations=[];self.journal=[]
        self.adapter=r.ResponsesAdapter(model=MODEL,transport=httpx.MockTransport(self.handle),
            wait_policy=r.SLOW_WAIT_POLICY,stream_policy=streams.VERSION,stream_sink=sink or self.journal.append)
    def handle(self,request):
        payload=json.loads(request.content)
        if request.url.path.endswith('/input_tokens'):
            self.counts.append(payload);return httpx.Response(200,json={'input_tokens':100})
        self.generations.append(payload);assert payload['stream'] is True
        if self.on_generation:self.on_generation(payload)
        actual=json.loads(payload['input'][0]['content']);out=_output_for(actual)
        for unit in out['units']:
            for e in unit['evidence']:e.pop('start');e.pop('end')
        if self.wrong_quote:out['units'][0]['evidence'][0]['quote']='此引文不在来源中'
        text='not valid json' if self.bad_json else json.dumps(out,ensure_ascii=False)
        usage=dict(input_tokens=100,output_tokens=120,total_tokens=220)
        if not self.unclassified_usage:usage['input_tokens_details']={'cached_tokens':0,'cache_write_tokens':0}
        response=dict(id='resp_stream_synthetic',model=MODEL,status=self.terminal,service_tier='default',
            output=[dict(type='reasoning',summary=[dict(type='summary_text',text='PRIVATE_REASONING_NOT_RETAINED')]),
                    dict(type='message',content=[dict(type='output_text',text=text)])],usage=usage if self.usage else None)
        if self.shape_invalid:response['output'][1]['content'][0]['text']=123
        frames=[dict(type='response.created',sequence_number=0,response=dict(id=response['id'],model=MODEL,status='in_progress')),
            dict(type='response.reasoning_text.delta',sequence_number=1,delta='PRIVATE_REASONING_NOT_RETAINED')]
        pos=2
        for i in range(0,len(text),57):
            frames.append(dict(type='response.output_text.delta',sequence_number=pos,output_index=1,content_index=0,
                delta=text[i:i+57]));pos+=1
        if self.conflict:response['output'][1]['content'][0]['text']=text+'conflict'
        if self.cut:frames=frames[:4]
        else:frames.append(dict(type='response.'+self.terminal,sequence_number=pos,response=response))
        return httpx.Response(200,headers={'content-type':'text/event-stream','x-request-id':'req_synthetic_stream'},
            stream=Chunks([frame(x) for x in frames],cut=self.cut))

class StreamingBusiness(unittest.TestCase):
    setUp=diagnostics.A34DiagnosticRetention.setUp
    def create(self):
        with app_module.connect() as db:return s.create_real_trial(db,self.entries[0],self.cid,self.owner,
            model=MODEL,price=PRICE,profile=s.REAL_STREAM_PROFILE,batch_input_cap=12000,output_cap=6144,max_microusd=122880)
    def execute(self,wire,trial=None):
        self.addCleanup(wire.adapter.close);trial=trial or self.create()
        s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        result=s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter,diagnostic_sink=lambda x:None)
        return trial,result
    def check_money(self,trial,unknown=False):
        with app_module.connect() as db:
            b=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            c=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            if unknown:
                self.assertGreater(b['reserved_microusd'],0);self.assertEqual(b['spent_estimate_microusd'],0)
                self.assertEqual(c['state'],'unknown');self.assertIsNone(c['settlement_hash']);self.assertIsNone(c['output_json'])
            else:
                self.assertEqual(b['reserved_microusd'],0);self.assertEqual(b['spent_estimate_microusd'],1400)
                self.assertTrue(c['settlement_hash'])
            return dict(c)
    def test_stream_complete_saved_read_bound_and_retry_idempotent(self):
        wire=Wire();trial=self.create()
        def committed(payload):
            with app_module.connect() as db:
                row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
                self.assertEqual(row['state'],'possibly_dispatched');self.assertTrue(row['dispatch_attempt_id'])
                self.assertTrue(db.execute("SELECT 1 FROM mv3_a3_audit WHERE job_id=? AND event='dispatch_owner_binding_v1'",(trial['jobId'],)).fetchone())
        wire.on_generation=committed
        trial,result=self.execute(wire,trial);self.assertEqual(result['status'],'completed',result)
        self.check_money(trial)
        with app_module.connect() as db:
            f=metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']
            self.assertTrue(f['value']);self.assertEqual(f['reviewStatus'],'needs_review')
            p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0])
            self.assertEqual(p['readSet']['streamVersion'],streams.VERSION)
            row=db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=?',(trial['jobId'],)).fetchone()
            self.assertTrue(json.loads(row['warning_json'])['normalizedHash'])
        t=wire.adapter.last_transport_diagnostic
        self.assertEqual(t['timingOrigin'],'after_committed_A3_dispatch')
        self.assertLessEqual(t['headersSeconds'],t['stream']['firstCompleteEventSeconds'])
        self.assertLessEqual(t['stream']['firstPublicTextSeconds'],t['stream']['terminalSeconds'])
        self.assertNotIn('PRIVATE_REASONING_NOT_RETAINED',json.dumps(wire.journal))
        self.assertEqual(s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)['reused'],True)
        self.assertEqual((len(wire.counts),len(wire.generations)),(1,1))
    def test_midstream_disconnect_keeps_id_partial_unknown_and_no_repeat(self):
        wire=Wire(cut=True);trial,result=self.execute(wire);self.assertEqual(result['status'],'unknown')
        row=self.check_money(trial,True);self.assertEqual(row['provider_response_id'],'resp_stream_synthetic')
        self.assertTrue(wire.adapter.last_transport_diagnostic['publicResponse']['text'])
        with app_module.connect() as db:self.assertIsNone(metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['value'])
        with self.assertRaises(ValueError):s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        self.assertEqual(len(wire.generations),1)
    def test_terminal_missing_usage_stays_unknown(self):
        wire=Wire(usage=False);trial,result=self.execute(wire);self.assertEqual(result['status'],'unknown');self.check_money(trial,True)
        self.assertTrue(wire.adapter.last_transport_diagnostic['stream']['terminalSeconds'] is not None)
    def test_v4_quote_failure_bills_known_usage_without_candidate(self):
        wire=Wire(wrong_quote=True);trial,result=self.execute(wire);self.assertEqual(result['status'],'failed')
        row=self.check_money(trial);self.assertEqual(row['state'],'rejected');self.assertIsNone(row['output_json'])
    def test_invalid_json_bills_known_usage(self):
        trial,result=self.execute(Wire(bad_json=True));self.assertEqual(result['status'],'failed');self.check_money(trial)
    def test_unclassified_usage_uses_conservative_upper_price(self):
        trial,result=self.execute(Wire(unclassified_usage=True));self.assertEqual(result['status'],'completed')
        with app_module.connect() as db:
            b=db.execute('SELECT * FROM mv3_trial_budgets WHERE id=?',(trial['trialId'],)).fetchone()
            self.assertEqual(b['spent_estimate_microusd'],1700);self.assertEqual(b['reserved_microusd'],0)
    def test_incomplete_terminal_bills_and_keeps_public_receipt(self):
        wire=Wire(terminal='incomplete');trial,result=self.execute(wire);self.assertEqual(result['status'],'failed');self.check_money(trial)
        self.assertEqual(wire.journal[-1]['response']['status'],'incomplete')
    def test_terminal_delta_disagreement_rejects_with_known_usage(self):
        trial,result=self.execute(Wire(conflict=True));self.assertEqual(result['status'],'failed');self.check_money(trial)
    def test_malformed_public_terminal_still_settles_known_usage(self):
        trial,result=self.execute(Wire(shape_invalid=True));self.assertEqual(result['status'],'failed');self.check_money(trial)
    def test_stream_journal_failure_does_not_rollback_usage_or_valid_output(self):
        def fail(x):raise OSError('synthetic diagnostic failure')
        wire=Wire(sink=fail);trial,result=self.execute(wire);self.assertEqual(result['status'],'completed');self.check_money(trial)
        self.assertGreater(wire.adapter.last_transport_diagnostic['stream']['diagnosticErrors'],0)
    def test_output_persistence_failure_independent_settlement(self):
        trial=self.create()
        with app_module.connect() as db:db.execute("CREATE TRIGGER synthetic_output_failure BEFORE UPDATE OF output_json ON mv3_model_chunks WHEN NEW.output_json IS NOT NULL BEGIN SELECT RAISE(FAIL,'synthetic output failure'); END")
        trial,result=self.execute(Wire(),trial);self.assertEqual(result['status'],'failed')
        row=self.check_money(trial);self.assertIsNone(row['output_json'])
    def test_field_write_failure_keeps_known_usage_and_full_batch(self):
        trial=self.create()
        with app_module.connect() as db:db.execute("CREATE TRIGGER synthetic_field_failure BEFORE INSERT ON mv3_entry_fields WHEN NEW.name='synopsis' BEGIN SELECT RAISE(FAIL,'synthetic field failure'); END")
        trial,result=self.execute(Wire(),trial);self.assertEqual(result['status'],'failed');row=self.check_money(trial)
        self.assertTrue(row['output_json'])
    def test_revoke_or_cancel_or_old_worker_never_gains_business_permission(self):
        # Three independent fixtures; each allowed original sender only settles.
        for action in ('revoke','cancel','expire'):
            with self.subTest(action=action):
                self.setUp();trial=self.create()
                def mutate(payload):
                    with app_module.connect() as db:
                        if action=='revoke':db.execute('UPDATE characters SET enabled=0 WHERE id=?',(self.cid,))
                        elif action=='cancel':db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?',(trial['jobId'],))
                        else:db.execute('UPDATE mv3_jobs SET lease_until=0 WHERE id=?',(trial['jobId'],))
                trial,result=self.execute(Wire(on_generation=mutate),trial)
                self.assertNotEqual(result['status'],'completed');self.check_money(trial)
                with app_module.connect() as db:self.assertFalse(db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',(trial['jobId'],)).fetchone())
    def test_unknown_stream_version_and_wrong_adapter_zero_send(self):
        trial=self.create();wire=Wire();self.addCleanup(wire.adapter.close)
        with app_module.connect() as db:
            p=json.loads(db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(trial['jobId'],)).fetchone()[0]);p['readSet']['streamVersion']='unknown'
            db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(p),trial['jobId']))
        with self.assertRaises(ValueError):s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,wire.adapter)
        self.assertEqual(wire.counts,[])
    def test_prompt_schema_input_equal_v4_only_stream_flag_differs(self):
        actual={'phase':'batch','sources':[]}
        old=r.synopsis_request_payload(MODEL,'batch',actual,6144,profile=s.REAL_QUOTE_PROFILE)
        new=r.synopsis_request_payload(MODEL,'batch',actual,6144,profile=s.REAL_STREAM_PROFILE)
        expected=dict(old,stream=True);self.assertEqual(new,expected);self.assertFalse(old['stream'])
    def test_61_second_stream_event_gap_receives_settles_and_reads(self):
        class Server(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path.endswith('/input_tokens'):
                    body=b'{"input_tokens":100}';self.send_response(200);self.send_header('Content-Length',str(len(body)))
                    self.end_headers();self.wfile.write(body);return
                output=_output_for(json.loads(request['input'][0]['content']))
                for unit in output['units']:
                    for e in unit['evidence']:e.pop('start');e.pop('end')
                text=json.dumps(output,ensure_ascii=False)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
                self.wfile.write(frame(dict(type='response.created',sequence_number=0,response={'id':'resp_local_slow','model':MODEL,'status':'in_progress'})));self.wfile.flush()
                time.sleep(61)
                self.wfile.write(frame(dict(type='response.output_text.delta',sequence_number=1,output_index=0,content_index=0,delta=text)));self.wfile.flush()
                terminal=dict(id='resp_local_slow',model=MODEL,status='completed',service_tier='default',
                    output=[dict(type='message',content=[dict(type='output_text',text=text)])],
                    usage=dict(input_tokens=100,output_tokens=120,input_tokens_details={'cached_tokens':0,'cache_write_tokens':0}))
                self.wfile.write(frame(dict(type='response.completed',sequence_number=2,response=terminal)));self.wfile.flush();self.close_connection=True
        server=ThreadingHTTPServer(('127.0.0.1',0),Server);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        adapter=r.ResponsesAdapter(model=MODEL,allow_network=True,api_key='synthetic-only-loopback-key',
            wait_policy=r.SLOW_WAIT_POLICY,stream_policy=streams.VERSION,stream_sink=lambda row:None)
        try:
            with patch.object(r,'API_ROOT','http://127.0.0.1:'+str(server.server_address[1])+'/v1'):
                trial=self.create();s.prepare_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter)
                result=s.run_real_trial(app_module.connect,trial['jobId'],self.cid,self.owner,adapter,diagnostic_sink=lambda row:None)
            self.assertEqual(result['status'],'completed',result);self.check_money(trial)
            metrics=adapter.last_transport_diagnostic['stream']
            self.assertLess(metrics['firstCompleteEventSeconds'],3)
            self.assertGreaterEqual(metrics['maxInterEventGapSeconds'],60)
            with app_module.connect() as db:self.assertTrue(metadata.read_draft(db,self.entries[0],self.cid)['fields']['synopsis']['value'])
        finally:adapter.close();server.shutdown();server.server_close();thread.join()
