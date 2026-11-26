"""Opt-in isolated HTTP long-reading diagnostics; never installed by daily main.

Uses the official HTTP streaming route, NOT TTD WS. Header-parser observations
and return-to-caller observations are distinct; neither is a wire capture.
"""
import asyncio
from dataclasses import dataclass
import math

import httpx

from .elevenlabs_adapter import ElevenLabsAdapter, ProviderError
from .http_diagnostics import SegmentDiagnostics, DiagnosticFailure


@dataclass(frozen=True)
class LongHTTPPolicy:
    connect: float = 10
    first_response: float = 60
    read_idle: float = 30
    hard: float = 90
    write: float = 10
    pool: float = 5

    def __post_init__(self):
        values = vars(self)
        maxima = dict(connect=10, first_response=60, read_idle=30, hard=90, write=10, pool=5)
        if any(not isinstance(v, (int,float)) or isinstance(v,bool) or not math.isfinite(v)
               or v <= 0 or v > maxima[k] for k,v in values.items()):
            raise ValueError('isolated_http_policy_out_of_range')
        if self.first_response > self.hard or self.connect > self.first_response:
            raise ValueError('isolated_http_policy_order')


class LongHTTPDiagnostics(SegmentDiagnostics):
    EXTRA = frozenset(('client_start','tcp_start','tcp_complete','tls_start','tls_complete',
        'send_headers_start','send_body_complete','headers_wait','transport_headers_complete',
        'response_object_ready','timeout','cancel_seen','transport_error','cleanup_complete','cleanup_failed',
        'deadline_started','deadline_released','client_exit_complete'))
    ENUMS = {
        'hop': {'proxy_connect','origin','local'},
        'deadline_kind': {'connect_budget','first_response','request_hard'},
        'timeout_kind': {'connect_budget','first_response','audio_idle','request_hard',
                         'connect_io','read_io','write_io','pool_io','unclassified'},
        'error_type': {'TimeoutError','CancelledError','ConnectTimeout','ReadTimeout','WriteTimeout',
                       'PoolTimeout','ConnectError','ReadError','RemoteProtocolError','ProxyError',
                       'unclassified'},
    }

    def event(self, event, **details):
        if event not in self.EXTRA:
            return super().event(event,**details)
        if set(details) - {'hop','timeout_kind','error_type','http_status','deadline_kind'}:
            raise DiagnosticFailure('long_http_diagnostic_field_rejected')
        safe = {}
        for key,value in details.items():
            if key == 'http_status':
                if type(value) is not int or not 100 <= value <= 599:
                    raise DiagnosticFailure('long_http_diagnostic_status_invalid')
                safe[key] = value
            else:
                safe[key] = value if isinstance(value,str) and value in self.ENUMS[key] else 'unclassified'
        import time
        if len(self.record['events']) >= 128:
            raise DiagnosticFailure('long_http_diagnostic_terminal_capacity')
        self.record['events'].append({'event':event,
            'relative_seconds':round(time.monotonic()-self.started,6),**safe})
        self.flush()


class _ClientExitEvents:
    """Observe the existing client exit; never infer a remote stop or billing result."""
    def __init__(self, client, diagnostics):
        self.client, self.diagnostics = client, diagnostics

    async def __aenter__(self):
        return await self.client.__aenter__()

    async def __aexit__(self, exc_type, exc, traceback):
        result = await self.client.__aexit__(exc_type, exc, traceback)
        # Emit only after __aexit__ actually returns, including error/cancel paths.
        # is_closed alone would be insufficient: HTTPX sets it before transports close.
        self.diagnostics.event('client_exit_complete', hop='local')
        return result


class LongHTTPAdapter(ElevenLabsAdapter):
    def __init__(self, api_key, transport=None, proxy_url=None, *, diagnostics, policy=None,
                 endpoint='/v1/text-to-dialogue/stream'):
        super().__init__(api_key, transport, proxy_url, diagnostics=diagnostics)
        if endpoint not in ('/v1/text-to-dialogue','/v1/text-to-dialogue/stream'):
            raise ValueError('isolated_http_endpoint_invalid')
        self.endpoint, self.policy = endpoint, policy or LongHTTPPolicy()
        self.timeout_kind = None

    async def synthesize_http(self,text,voice_id,language_code):
        p, d = self.policy, self.diagnostics
        if language_code not in ('zh','ja','auto') or not 1 <= len(text) <= 2000:
            raise ValueError('isolated_http_input_invalid')
        body={'model_id':'eleven_v4','inputs':[{'text':text,'voice_id':voice_id}]}
        if language_code != 'auto':body['language_code']=language_code
        connect_scope = header_scope = hard_scope = None
        response = None
        origin_started = False
        connect_released = False
        hop='proxy_connect' if self.proxy_url else 'origin'
        self.synthesis_phase='client_start'
        d.event('client_start',hop='local')

        async def trace(event,info):
            nonlocal hop,origin_started,connect_released
            # Request object/return_value never enter diagnostics. Only method
            # selects CONNECT vs origin and a validated status integer survives.
            if event.endswith('send_request_headers.started'):
                request=info.get('request')
                method=getattr(request,'method',None)
                if method in (b'CONNECT','CONNECT'):hop='proxy_connect'
                elif method in (b'POST','POST'):hop='origin'
                else:raise DiagnosticFailure('long_http_trace_method_unavailable')
                if hop == 'origin':
                    origin_started=True
                    if connect_scope:
                        connect_scope.reschedule(None)
                        if not connect_released:
                            connect_released = True
                            d.event('deadline_released',deadline_kind='connect_budget',hop='local')
                self.synthesis_phase='sending_request' if hop=='origin' else 'proxy_connect'
                d.event('send_headers_start',hop=hop)
            elif event.endswith('send_request_body.complete'):
                d.event('send_body_complete',hop=hop)
            elif event.endswith('receive_response_headers.started'):
                self.synthesis_phase='waiting_response' if hop=='origin' else 'proxy_connect'
                d.event('headers_wait',hop=hop)
            elif event.endswith('receive_response_headers.complete'):
                value=info.get('return_value')
                if not isinstance(value,(tuple,list)) or len(value)<2:
                    raise DiagnosticFailure('long_http_trace_status_unavailable')
                status=value[1]
                d.event('transport_headers_complete',hop=hop,http_status=status)
            elif event.endswith(('connect_tcp.started','connect_tcp.complete','start_tls.started','start_tls.complete')):
                suffix=event.rsplit('.',2)[-2:]
                name=('tcp' if suffix[0]=='connect_tcp' else 'tls') + ('_start' if suffix[1]=='started' else '_complete')
                self.synthesis_phase='connecting'
                d.event(name,hop='origin' if name.startswith('tls') else hop)

        try:
            async with asyncio.timeout(p.hard) as hard_scope:
                d.event('deadline_started',deadline_kind='request_hard',hop='local')
                async with _ClientExitEvents(httpx.AsyncClient(base_url='https://api.elevenlabs.io', transport=self.transport,
                    proxy=self.proxy_url, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(connect=p.connect,read=p.first_response,write=p.write,pool=p.pool)),d) as client:
                    request=client.build_request('POST',self.endpoint,
                        params={'output_format':'mp3_44100_128'},headers={'xi-api-key':self.api_key},
                        json=body,extensions={'trace':trace})
                    d.event('dispatch_attempt')
                    self.synthesis_phase='connecting'
                    try:
                        async with asyncio.timeout(p.connect) as connect_scope:
                            d.event('deadline_started',deadline_kind='connect_budget',hop='local')
                            try:
                                async with asyncio.timeout(p.first_response) as header_scope:
                                    d.event('deadline_started',deadline_kind='first_response',hop='local')
                                    response=await client.send(request,stream=True)
                            finally:
                                if header_scope is not None:
                                    d.event('deadline_released',deadline_kind='first_response',hop='local')
                    finally:
                        if connect_scope is not None and not connect_released:
                            connect_released = True
                            d.event('deadline_released',deadline_kind='connect_budget',hop='local')
                    # Mock/custom transports must implement the trace contract;
                    # do not silently invent transport-level header evidence.
                    d.event('response_object_ready',hop='origin',http_status=response.status_code)
                    if response.status_code != 200:
                        raise ProviderError('isolated_http_rejected',unknown=response.status_code==408 or response.status_code>=500)
                    self.synthesis_phase='receiving_audio'
                    chunks=[];size=0;iterator=response.aiter_bytes().__aiter__()
                    while True:
                        try:
                            chunk=await asyncio.wait_for(anext(iterator),p.read_idle)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            self.timeout_kind='audio_idle'
                            raise
                        size+=len(chunk)
                        if size>16*1024*1024:raise ProviderError('isolated_audio_limit',unknown=True)
                        d.chunk(chunk)
                        chunks.append(chunk)
                        d.event('first_audio' if len(chunks)==1 else 'receive_progress',audio_bytes=size)
                    d.event('receive_complete',audio_bytes=size)
                    if not size:raise ProviderError('isolated_empty_audio',unknown=True)
                    await response.aclose();response=None
                    d.event('response_closed')
                    return b''.join(chunks)
        except TimeoutError:
            if hard_scope and hard_scope.expired():kind='request_hard'
            elif connect_scope and connect_scope.expired():kind='connect_budget'
            elif header_scope and header_scope.expired():kind='first_response'
            else:kind=self.timeout_kind or 'unclassified'
            self.timeout_kind=kind
            d.event('timeout',timeout_kind=kind,error_type='TimeoutError',hop='origin' if origin_started else hop)
            raise
        except httpx.TimeoutException as exc:
            self.timeout_kind={httpx.ConnectTimeout:'connect_io',httpx.ReadTimeout:'read_io',
                httpx.WriteTimeout:'write_io',httpx.PoolTimeout:'pool_io'}.get(type(exc),'unclassified')
            d.event('timeout',timeout_kind=self.timeout_kind,error_type=type(exc).__name__,hop=hop)
            raise ProviderError('isolated_transport_timeout',unknown=True) from None
        except asyncio.CancelledError:
            d.event('cancel_seen',error_type='CancelledError',hop=hop)
            raise
        except httpx.TransportError as exc:
            d.event('transport_error',error_type=type(exc).__name__,hop=hop)
            raise ProviderError('isolated_transport_error',unknown=True) from None
        finally:
            if response is not None:
                try:
                    await asyncio.wait_for(response.aclose(),1)
                    d.event('cleanup_complete',hop='local')
                except (TimeoutError,asyncio.CancelledError):
                    # Keep the original outcome; no retry or new request.
                    pass
                except Exception as exc:
                    d.event('cleanup_failed',hop='local',error_type=type(exc).__name__)
            if hard_scope is not None:
                d.event('deadline_released',deadline_kind='request_hard',hop='local')
