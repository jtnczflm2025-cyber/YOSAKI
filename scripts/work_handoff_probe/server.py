"""Loopback-only, synthetic handoff probe. No Codex turns or project writes."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = '127.0.0.1'
PORT = 18743
ORIGIN = f'http://{HOST}:{PORT}'
NONCE = secrets.token_urlsafe(24)
REVIEWS = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, mime='application/json; charset=utf-8'):
        data = body.encode('utf-8') if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', f"default-src 'none'; script-src 'nonce-{NONCE}'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        return self.headers.get('Host') == f'{HOST}:{PORT}'

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error': 'host rejected'})
        if self.path == '/':
            return self.reply(200, (ROOT / 'index.html').read_text(encoding='utf-8').replace('__NONCE__', NONCE), 'text/html; charset=utf-8')
        if self.path == '/api/report':
            return self.reply(200, {'report': '待审合成报告：仅验证本机浏览器访问、指定文件读取和审核意见回执。没有执行任务，也没有向模型提交内容。', 'codexDelivery': '未连接桌面会话；意见只保存在本探针内存。', 'reviews': REVIEWS})
        if self.path == '/api/file':
            target = ROOT / 'test-file.txt'
            if target.is_symlink() or target.resolve().parent != ROOT:
                return self.reply(403, {'error': 'file rejected'})
            return self.reply(200, {'path': 'scripts/work_handoff_probe/test-file.txt', 'content': target.read_text(encoding='utf-8')})
        return self.reply(404, {'error': 'not allowlisted'})

    def do_POST(self):
        if not self.allowed() or self.headers.get('Origin') != ORIGIN or self.headers.get('X-Probe-Nonce') != NONCE:
            return self.reply(403, {'error': 'origin or nonce rejected'})
        if self.path != '/api/review':
            return self.reply(404, {'error': 'not allowlisted'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096 or len(REVIEWS) >= 100:
                return self.reply(413, {'error': 'limit'})
            payload = json.loads(self.rfile.read(size))
            if set(payload) != {'text'} or not isinstance(payload['text'], str) or not payload['text'].strip():
                raise ValueError()
        except (ValueError, TypeError):
            return self.reply(400, {'error': 'invalid review'})
        receipt = {'id': len(REVIEWS) + 1, 'text': payload['text'], 'status': '本机已收件；未提交 Codex，未调用模型'}
        REVIEWS.append(receipt)
        return self.reply(200, receipt)

if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
