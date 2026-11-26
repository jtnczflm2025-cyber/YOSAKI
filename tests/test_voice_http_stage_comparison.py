"""Offline stage audit using a loopback CONNECT/TLS fake, never a Provider.

Uses installed HTTPX/httpcore, real local TLS and HTTP parsing, fake credentials
and synthetic audio. Production adapters, deadlines and receipts are unchanged.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx

from backend.app.voice.elevenlabs_adapter import ProviderError
from backend.app.voice.long_http_adapter import (
    LongHTTPAdapter, LongHTTPDiagnostics, LongHTTPPolicy, _ClientExitEvents,
)


class LoopbackStages(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='yosaki-http-stage-audit-')
        self.root = Path(self.temp.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic.invalid')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('synthetic.invalid')]), False)
                .sign(key, hashes.SHA256()))
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        (self.root / 'fake.crt').write_bytes(cert_pem)
        (self.root / 'fake.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_ssl.load_cert_chain(self.root / 'fake.crt', self.root / 'fake.key')
        self.client_ssl = ssl.create_default_context(cadata=cert_pem.decode('ascii'))
        self.tasks, self.diags = set(), []
        self.attempts = 0
        self.body_seen = asyncio.Event()
        self.release = asyncio.Event()
        self.header_delay, self.body_delay, self.status, self.stall = .02, .02, 200, False
        self.payload = b'synthetic-audio'
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        # Hard test boundary: even an accidental transport change cannot leave localhost.
        original = socket.socket.connect
        def local_only(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] != '127.0.0.1':
                raise AssertionError('offline_test_non_loopback_connection_refused')
            return original(sock, address)
        self.network_guard = patch.object(socket.socket, 'connect', local_only)
        self.network_guard.start()

    async def asyncTearDown(self):
        self.release.set()
        self.server.close()
        await self.server.wait_closed()
        if self.tasks:
            await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), 2)
        self.network_guard.stop()
        for d in self.diags:
            d.finish()
        self.temp.cleanup()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            connect = await reader.readuntil(b'\r\n\r\n')
            assert connect.startswith(b'CONNECT synthetic.invalid:443 ')
            writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
            await writer.drain()
            await writer.start_tls(self.server_ssl)
            head = await reader.readuntil(b'\r\n\r\n')
            length = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n')
                              if line.lower().startswith(b'content-length:')))
            await reader.readexactly(length)
            self.attempts += 1
            self.body_seen.set()
            await asyncio.sleep(self.header_delay)
            length = len(self.payload) * (2 if self.stall else 1)
            writer.write(f'HTTP/1.1 {self.status} Synthetic\r\nContent-Length: {length}\r\n\r\n'.encode())
            await writer.drain()
            await asyncio.sleep(self.body_delay)
            writer.write(self.payload)
            await writer.drain()
            if self.stall:
                await self.release.wait()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass  # Expected when the adapter cancels or rejects a synthetic response.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass
            self.tasks.discard(task)

    def make(self):
        transport = httpx.AsyncHTTPTransport(proxy=f'http://127.0.0.1:{self.port}',
            verify=self.client_ssl, trust_env=False, retries=0)
        class LocalOnly(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                request.url = request.url.copy_with(host='synthetic.invalid')
                return await transport.handle_async_request(request)
            async def aclose(self):
                await transport.aclose()
        d = LongHTTPDiagnostics(self.root / 'diagnostics', 'synthetic', 0, 'attempt')
        self.diags.append(d)
        return LongHTTPAdapter('fake-only', LocalOnly(), diagnostics=d,
            policy=LongHTTPPolicy(connect=.4, first_response=.6, read_idle=.15, hard=1.5)), d

    @staticmethod
    def events(d, event, hop=None):
        return [v for v in d.record['events'] if v['event'] == event
                and (hop is None or v.get('hop') == hop)]

    def assert_deadline_observations(self, d):
        for kind in ('request_hard', 'connect_budget', 'first_response'):
            begin = [e for e in self.events(d, 'deadline_started') if e['deadline_kind'] == kind]
            end = [e for e in self.events(d, 'deadline_released') if e['deadline_kind'] == kind]
            self.assertEqual(len(begin), 1)
            self.assertEqual(len(end), 1)
            self.assertLessEqual(begin[0]['relative_seconds'], end[0]['relative_seconds'])
        self.assertEqual(len(self.events(d, 'client_exit_complete', 'local')), 1)

    async def test_delayed_target_headers_distinct_from_connect_tls_and_audio(self):
        self.header_delay, self.body_delay = .12, .05
        a, d = self.make()
        self.assertEqual(await a.synthesize_http('合成测试。', 'fake', 'zh'), self.payload)
        proxy = self.events(d, 'transport_headers_complete', 'proxy_connect')[0]
        tls = self.events(d, 'tls_complete', 'origin')[0]
        body = self.events(d, 'send_body_complete', 'origin')[0]
        target = self.events(d, 'transport_headers_complete', 'origin')[0]
        returned = self.events(d, 'response_object_ready')[0]
        audio = self.events(d, 'first_audio')[0]
        self.assertLessEqual(proxy['relative_seconds'], tls['relative_seconds'])
        self.assertLessEqual(tls['relative_seconds'], body['relative_seconds'])
        self.assertGreater(target['relative_seconds'] - body['relative_seconds'], .10)
        self.assertLessEqual(target['relative_seconds'], returned['relative_seconds'])
        self.assertGreater(audio['relative_seconds'] - target['relative_seconds'], .04)
        self.assertEqual(self.attempts, 1)
        self.assert_deadline_observations(d)
        connect_end = next(e for e in self.events(d, 'deadline_released')
                           if e['deadline_kind'] == 'connect_budget')
        self.assertLessEqual(connect_end['relative_seconds'], body['relative_seconds'])
        header_end = next(e for e in self.events(d, 'deadline_released')
                          if e['deadline_kind'] == 'first_response')
        self.assertLessEqual(header_end['relative_seconds'], returned['relative_seconds'])

    async def test_target_header_deadline_not_proxy_success_or_audio_stall(self):
        self.header_delay = .75
        a, d = self.make()
        with self.assertRaises(TimeoutError):
            await a.synthesize_http('合成测试。', 'fake', 'zh')
        self.assertEqual(a.timeout_kind, 'first_response')
        self.assertTrue(self.events(d, 'transport_headers_complete', 'proxy_connect'))
        self.assertTrue(self.events(d, 'tls_complete', 'origin'))
        self.assertEqual(self.events(d, 'transport_headers_complete', 'origin'), [])
        self.assertEqual(self.events(d, 'first_audio'), [])
        self.assertEqual(self.attempts, 1)
        self.assert_deadline_observations(d)

    async def test_error_response_has_target_status_but_no_audio(self):
        self.status = 429
        a, d = self.make()
        with self.assertRaises(ProviderError) as caught:
            await a.synthesize_http('合成测试。', 'fake', 'zh')
        self.assertFalse(caught.exception.unknown)
        self.assertEqual(self.events(d, 'transport_headers_complete', 'origin')[0]['http_status'], 429)
        self.assertEqual(self.events(d, 'response_object_ready')[0]['http_status'], 429)
        self.assertEqual(self.events(d, 'first_audio'), [])
        self.assertEqual(self.events(d, 'timeout'), [])
        self.assertEqual(self.attempts, 1)
        self.assert_deadline_observations(d)

    async def test_audio_read_stall_has_target_headers_and_partial_audio(self):
        self.stall = True
        a, d = self.make()
        with self.assertRaises(TimeoutError):
            await a.synthesize_http('合成测试。', 'fake', 'zh')
        self.assertEqual(a.timeout_kind, 'audio_idle')
        self.assertTrue(self.events(d, 'transport_headers_complete', 'origin'))
        self.assertTrue(self.events(d, 'first_audio'))
        self.assertEqual(self.events(d, 'receive_complete'), [])
        self.assertEqual(d.record['audio_bytes'], len(self.payload))
        self.assertEqual(self.attempts, 1)
        self.assert_deadline_observations(d)
        d.finish()
        self.assertFalse(d.record['diagnostic_audio']['publishable'])

    async def test_cancel_releases_deadlines_and_observes_only_local_exit(self):
        self.header_delay = .75
        a, d = self.make()
        task = asyncio.create_task(a.synthesize_http('合成测试。', 'fake', 'zh'))
        await self.body_seen.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.attempts, 1)
        self.assertEqual(len(self.events(d, 'cancel_seen')), 1)
        self.assertEqual(self.events(d, 'first_audio'), [])
        self.assert_deadline_observations(d)


class ClientExitContract(unittest.IsolatedAsyncioTestCase):
    async def test_failed_close_does_not_claim_exit_completed(self):
        with tempfile.TemporaryDirectory(prefix='yosaki-exit-contract-') as temp:
            d = LongHTTPDiagnostics(Path(temp), 'synthetic', 0, 'attempt')
            class FailingClient:
                is_closed = True  # This flag must not be used as completion evidence.
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    raise OSError('synthetic close failure')
            try:
                with self.assertRaises(OSError):
                    async with _ClientExitEvents(FailingClient(), d):
                        pass
                self.assertFalse(any(e['event'] == 'client_exit_complete' for e in d.record['events']))
            finally:
                d.finish()

    async def test_original_exception_is_forwarded_and_not_suppressed(self):
        with tempfile.TemporaryDirectory(prefix='yosaki-exit-contract-') as temp:
            d = LongHTTPDiagnostics(Path(temp), 'synthetic', 0, 'attempt')
            seen = []
            class Client:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, kind, value, traceback):
                    seen.append((kind, value))
                    return False
            error = ValueError('synthetic')
            try:
                with self.assertRaises(ValueError) as caught:
                    async with _ClientExitEvents(Client(), d):
                        raise error
                self.assertIs(caught.exception, error)
                self.assertEqual(seen, [(ValueError, error)])
                self.assertEqual(len([e for e in d.record['events'] if e['event'] == 'client_exit_complete']), 1)
                self.assertEqual(vars(LongHTTPPolicy()), dict(connect=10, first_response=60,
                    read_idle=30, hard=90, write=10, pool=5))
            finally:
                d.finish()


if __name__ == '__main__':
    unittest.main()
