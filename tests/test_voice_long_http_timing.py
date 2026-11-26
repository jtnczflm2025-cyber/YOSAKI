"""Synthetic HTTP timing, milliseconds scaled as seconds. No provider network.

Mock traces exercise the actual HTTPX adapter boundary; a loopback test below
also checks the installed httpcore parser's real trace (not a hand-made event).
"""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import httpx

from backend.app.voice.long_http_adapter import LongHTTPAdapter,LongHTTPPolicy,LongHTTPDiagnostics
from backend.app.voice.segmented_reading import SegmentedReading
from scripts.voice_segmented_lab import bootstrap,MP3
from backend.app.voice import auto_budget


def policy(**changes):
    # Scale 1 protocol second to 0.01 wall seconds to keep CI bounded.
    values=dict(connect=.10,first_response=.60,read_idle=.30,hard=.90,write=.10,pool=.05)
    values.update(changes)
    return LongHTTPPolicy(**values)


class Stream(httpx.AsyncByteStream):
    def __init__(self,delays):self.delays=delays;self.closed=False
    async def __aiter__(self):
        for delay in self.delays:
            await asyncio.sleep(delay)
            yield MP3
    async def aclose(self):self.closed=True


class Supplier:
    def __init__(self,headers=.0,delays=(.01,),return_delay=0,proxy=False,trace_headers=True):
        self.headers,self.delays,self.return_delay=headers,delays,return_delay
        self.calls=[];self.streams=[];self.proxy=proxy;self.trace_headers=trace_headers
        self.entered=asyncio.Event();self.cancelled=False
    async def handle(self,request):
        self.calls.append(request);self.entered.set()
        trace=request.extensions['trace']
        class R:method=b'CONNECT'
        if self.proxy:
            await trace('http11.send_request_headers.started',{'request':R()})
            await trace('http11.receive_response_headers.complete',{'return_value':(b'HTTP/1.1',200,b'OK',[])})
        await trace('connection.connect_tcp.started',{})
        await trace('connection.connect_tcp.complete',{})
        await trace('proxy.start_tls.started',{})
        await trace('proxy.start_tls.complete',{})
        class Post:method=b'POST'
        await trace('http11.send_request_headers.started',{'request':Post()})
        await trace('http11.send_request_body.complete',{})
        await trace('http11.receive_response_headers.started',{})
        try:await asyncio.sleep(self.headers)
        except asyncio.CancelledError:self.cancelled=True;raise
        if self.trace_headers:
            await trace('http11.receive_response_headers.complete',{'return_value':(b'HTTP/1.1',200,b'OK',[(b'private',b'do-not-log')])})
        await asyncio.sleep(self.return_delay)
        stream=Stream(self.delays);self.streams.append(stream)
        return httpx.Response(200,stream=stream)


class Timing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='yosaki-http-timing-')
        self.root=Path(self.temp.name)
        self.diags=[]
    async def asyncTearDown(self):
        for d in self.diags:d.finish()
        self.temp.cleanup()
    def make(self,supplier,p=None):
        d=LongHTTPDiagnostics(self.root,'job',0,'attempt'+str(len(self.diags)))
        self.diags.append(d)
        a=LongHTTPAdapter('fake',httpx.MockTransport(supplier.handle),diagnostics=d,policy=p or policy())
        return a,d
    def events(self,d,name):return [e for e in d.record['events'] if e['event']==name]

    async def test_headers_after_old_30_window_can_succeed_with_new_policy(self):
        s=Supplier(headers=.35);a,d=self.make(s)
        self.assertEqual(await a.synthesize_http('新文本。','fake','zh'),MP3)
        self.assertGreater(self.events(d,'response_object_ready')[0]['relative_seconds'],.30)
        self.assertEqual(len(s.calls),1)
        self.assertEqual(s.calls[0].url.path,'/v1/text-to-dialogue/stream')
        self.assertTrue(s.streams[0].closed)

    async def test_old_outer_30_stops_same_synthetic_sequence(self):
        s=Supplier(headers=.35);a,d=self.make(s)
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(.30):await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual(len(s.calls),1);self.assertTrue(s.cancelled)
        self.assertEqual(self.events(d,'response_object_ready'),[])

    async def test_parser_headers_distinct_from_api_return_and_first_audio(self):
        s=Supplier(headers=.01,return_delay=.02,delays=(.08,));a,d=self.make(s)
        await a.synthesize_http('新文本。','fake','zh')
        parsed=self.events(d,'transport_headers_complete')[0]['relative_seconds']
        returned=self.events(d,'response_object_ready')[0]['relative_seconds']
        audio=self.events(d,'first_audio')[0]['relative_seconds']
        self.assertGreater(returned-parsed,.015);self.assertGreater(audio-returned,.07)
        self.assertNotIn('do-not-log',d.path.read_text())

    async def test_continuous_chunks_not_mistaken_for_total_timeout(self):
        s=Supplier(delays=(.12,)*4);a,d=self.make(s)
        self.assertEqual(await a.synthesize_http('新文本。','fake','zh'),MP3*4)
        self.assertEqual(d.record['audio_bytes'],len(MP3)*4)
        self.assertEqual(len(s.calls),1)

    async def test_body_stall_is_audio_idle_and_partial_is_quarantined(self):
        s=Supplier(delays=(.01,.40));a,d=self.make(s)
        with self.assertRaises(TimeoutError):await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual(a.timeout_kind,'audio_idle');self.assertTrue(s.streams[0].closed)
        d.finish();self.assertEqual(d.record['diagnostic_audio']['bytes'],len(MP3))
        self.assertFalse(d.record['diagnostic_audio']['publishable'])

    async def test_headers_deadline_and_hard_deadline_differ(self):
        s=Supplier(headers=.20);a,d=self.make(s,policy(first_response=.10,connect=.05))
        with self.assertRaises(TimeoutError):await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual(a.timeout_kind,'first_response')
        s=Supplier(delays=(.07,)*10);a,d=self.make(s,policy(hard=.35,first_response=.30))
        with self.assertRaises(TimeoutError):await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual(a.timeout_kind,'request_hard');self.assertEqual(len(s.calls),1)

    async def test_cancel_before_headers_and_during_audio(self):
        s=Supplier(headers=.5);a,d=self.make(s)
        task=asyncio.create_task(a.synthesize_http('新文本。','fake','zh'))
        await s.entered.wait();await asyncio.sleep(.01);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(s.cancelled);self.assertEqual(len(s.calls),1)
        s=Supplier(delays=(.01,.5));a,d=self.make(s)
        task=asyncio.create_task(a.synthesize_http('新文本。','fake','zh'))
        await asyncio.sleep(.06);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(s.streams[0].closed)

    async def test_connect_budget_and_proxy_headers_are_not_origin_headers(self):
        s=Supplier(proxy=True);a,d=self.make(s)
        await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual([e['hop'] for e in self.events(d,'transport_headers_complete')],['proxy_connect','origin'])
        async def silent(request):await asyncio.sleep(.3)
        d=LongHTTPDiagnostics(self.root,'job',0,'connect');self.diags.append(d)
        a=LongHTTPAdapter('fake',httpx.MockTransport(silent),diagnostics=d,policy=policy(connect=.03))
        with self.assertRaises(TimeoutError):await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual(a.timeout_kind,'connect_budget')

    async def test_byte_limit_still_stops(self):
        class Oversize(httpx.AsyncByteStream):
            async def __aiter__(self):yield bytes(16*1024*1024+1)
        s=Supplier();a,d=self.make(s)
        async def handle(request):
            response=await s.handle(request)
            return httpx.Response(200,stream=Oversize())
        a.transport=httpx.MockTransport(handle)
        from backend.app.voice.elevenlabs_adapter import ProviderError
        with self.assertRaises(ProviderError):await a.synthesize_http('新文本。','fake','zh')
        self.assertEqual(d.record['audio_bytes'],0)

    async def test_installed_httpcore_real_loopback_headers_arrive_before_body(self):
        # Real local socket only: proves AsyncClient.send(stream=True) does not
        # await the full body and the installed parser emits the expected trace.
        async def handler(reader,writer):
            try:
                header=await reader.readuntil(b'\r\n\r\n')
                length=int(next(l.split(b':',1)[1] for l in header.split(b'\r\n') if l.lower().startswith(b'content-length:')))
                await reader.readexactly(length)
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(MP3)).encode()+b'\r\n\r\n')
                await writer.drain();await asyncio.sleep(.08)
                writer.write(MP3);await writer.drain()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(handler,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        class LocalOnly(httpx.AsyncBaseTransport):
            def __init__(self):self.transport=httpx.AsyncHTTPTransport(retries=0)
            async def handle_async_request(self,request):
                request.url=request.url.copy_with(scheme='http',host='127.0.0.1',port=port)
                return await self.transport.handle_async_request(request)
            async def aclose(self):await self.transport.aclose()
        d=LongHTTPDiagnostics(self.root,'job',0,'loopback');self.diags.append(d)
        a=LongHTTPAdapter('fake',LocalOnly(),diagnostics=d,
                         policy=LongHTTPPolicy(connect=2,first_response=3,read_idle=1,hard=5))
        async with server:await a.synthesize_http('新文本。','fake','zh')
        self.assertGreater(self.events(d,'first_audio')[0]['relative_seconds']-
                           self.events(d,'transport_headers_complete')[0]['relative_seconds'],.06)


class JobTiming(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_cancel_never_dispatch_next_segment(self):
        for scenario in ('headers','stall','cancel'):
            with tempfile.TemporaryDirectory(prefix='yosaki-timing-job-') as temp,patch.dict(os.environ,{},clear=False):
                root=Path(temp);connect=bootstrap(root)
                s=Supplier(headers=.4 if scenario!='stall' else 0,
                           delays=(.01,.4) if scenario=='stall' else (.01,))
                p=policy(first_response=.2,connect=.1,read_idle=.1)
                reader=SegmentedReading(root/'result',connect,'甲乙丙丁。'*220+'\n\n'+'另一段文字。'*200,
                    lambda d:LongHTTPAdapter('fake',httpx.MockTransport(s.handle),diagnostics=d,policy=p),
                    lambda:{'voice_id':'fake','revision':3},
                    lambda n,f:auto_budget.reserve(connect,'synthetic-session',1,n,first_request=f),
                    request_timeout_seconds=.8,diagnostics_factory=LongHTTPDiagnostics)
                await reader.start(reader.preview['preview_sha256'])
                if scenario=='cancel':
                    await s.entered.wait();await reader.cancel()
                else:await reader.task
                self.assertEqual(len(s.calls),1)
                self.assertEqual(reader.result['segments'][1]['status'],'not_executed')


if __name__=='__main__':unittest.main()
