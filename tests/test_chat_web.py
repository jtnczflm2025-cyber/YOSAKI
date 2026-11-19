import os
import unittest
from unittest.mock import patch,MagicMock
from backend.app import chat_web as web


class WebReader(unittest.TestCase):
    def addresses(self,ip):return [(2,1,6,'',(ip,443))]
    def test_default_disabled_and_each_confirm_required(self):
        with patch.dict(os.environ,{'YOSAKI_CHAT_WEB_ENABLED':'0'}):
            self.assertEqual(web.definitions(),([] ,{}))
            with self.assertRaises(ValueError):web.read('https://example.test')
    def test_rejects_ip_and_dns_private_variants(self):
        for ip in ('127.0.0.1','10.0.0.1','169.254.169.254','0.0.0.0','::1','fc00::1','::ffff:127.0.0.1'):
            with self.subTest(ip=ip),patch.object(web.socket,'getaddrinfo',return_value=self.addresses(ip)):
                with self.assertRaises(ValueError):web.target('https://example.test')
        for url in ('file:///etc/passwd','https://user:password@example.test','http://example.test:8080','https://example.test/#secret'):
            with self.assertRaises(ValueError):web.target(url)
    def test_dns_mixed_public_private_refused(self):
        with patch.object(web.socket,'getaddrinfo',return_value=self.addresses('93.184.216.34')+self.addresses('10.1.1.1')):
            with self.assertRaises(ValueError):web.target('https://example.test')
    def test_pin_redirect_revalidation_and_text_only(self):
        response=MagicMock(status=302)
        response.getheader.side_effect=lambda k:'http://internal.test' if k=='Location' else None
        connection=MagicMock();connection.getresponse.return_value=response
        with patch.dict(os.environ,{'YOSAKI_CHAT_WEB_ENABLED':'1'}),patch.object(web.socket,'getaddrinfo',side_effect=[self.addresses('93.184.216.34'),self.addresses('127.0.0.1')]),patch.object(web.socket,'create_connection') as socket,patch.object(web.http.client,'HTTPConnection',return_value=connection):
            with self.assertRaises(ValueError):web.read('http://example.test/?a=b')
            self.assertEqual(socket.call_args.args[0],('93.184.216.34',80))
            self.assertEqual(socket.call_count,1)
            self.assertEqual(connection.request.call_args.args[:2],('GET','/?a=b'))
    def test_response_limit_and_script_removed(self):
        parser=web.Text();parser.feed('<p>visible</p><script>secret code</script><p>next</p>')
        self.assertNotIn('secret',''.join(parser.parts))
        response=MagicMock(status=200);response.getheader.return_value='text/plain';response.read.side_effect=[b'x'*(1024*1024+1)]
        connection=MagicMock();connection.getresponse.return_value=response
        with patch.dict(os.environ,{'YOSAKI_CHAT_WEB_ENABLED':'1'}),patch.object(web.socket,'getaddrinfo',return_value=self.addresses('93.184.216.34')),patch.object(web.socket,'create_connection'),patch.object(web.http.client,'HTTPConnection',return_value=connection):
            with self.assertRaises(ValueError):web.read('http://example.test/')

    def test_one_get_mode_stops_at_redirect(self):
        response=MagicMock(status=302)
        connection=MagicMock();connection.getresponse.return_value=response
        with patch.dict(os.environ,{'YOSAKI_CHAT_WEB_ENABLED':'1'}),patch.object(web.socket,'getaddrinfo',return_value=self.addresses('93.184.216.34')),patch.object(web.socket,'create_connection'),patch.object(web.http.client,'HTTPConnection',return_value=connection):
            with self.assertRaisesRegex(ValueError,'重定向已停止'):web.read('http://example.test/',follow_redirects=False)
            self.assertEqual(connection.request.call_count,1)
            response.getheader.assert_not_called()
