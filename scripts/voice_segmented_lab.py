"""Separate loopback, synthetic HTTP only. No daily main import or credentials."""
import argparse
import asyncio
from contextlib import contextmanager, asynccontextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from backend.app.voice import auto_budget, storage
from backend.app.voice.elevenlabs_adapter import ElevenLabsAdapter
from backend.app.voice.schemas import ProfileInput
from backend.app.voice.segmented_reading import SegmentedReading

ROOT = Path(__file__).resolve().parents[1]
MP3 = (b'\xff\xfb\x90\x64' + bytes(413)) * 96


class FakeStream(httpx.AsyncByteStream):
    def __init__(self, scenario):
        self.scenario = scenario

    async def __aiter__(self):
        for n in range(4):
            await asyncio.sleep(.04)
            yield MP3[n * len(MP3)//4:(n+1) * len(MP3)//4]
            if self.scenario == 'partial_unknown':
                raise httpx.ReadError('synthetic')


class FakeSupplier:
    def __init__(self, scenario='normal'):
        self.scenario, self.calls = scenario, []

    async def handle(self, request):
        self.calls.append(json.loads(request.content))
        trace = request.extensions['trace']
        for event in ('connection.connect_tcp.started', 'connection.start_tls.started',
                      'http11.receive_response_headers.started'):
            await trace(event, {})
        n = len(self.calls)
        if self.scenario == 'first_rejected' or (self.scenario == 'later_failed' and n == 2):
            return httpx.Response(401)
        if self.scenario == 'first_unknown':
            raise httpx.ReadTimeout('synthetic')
        return httpx.Response(200, stream=FakeStream(self.scenario))


def bootstrap(root):
    root = Path(root).resolve()
    if root.is_relative_to(ROOT) or root.is_symlink():
        raise ValueError('independent_directory_required')
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(YOSAKI_DATA_DIR=str(root), YOSAKI_VOICE_ENABLED='1',
        YOSAKI_VOICE_AUTO_SPEAK_ENABLED='1', YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED='1')
    @contextmanager
    def connect():
        db = sqlite3.connect(root / 'yosaki.sqlite3', timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER);
        CREATE TABLE IF NOT EXISTS characters(id INTEGER PRIMARY KEY,enabled INTEGER);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO users VALUES(1);
        INSERT OR IGNORE INTO auth_sessions VALUES('synthetic-session',1);
        INSERT OR IGNORE INTO characters VALUES(1,1);''')
        storage.init_db(db)
        auto_budget.init_db(db)
        auto_budget.init_daily_db(db)
        if not db.execute('SELECT 1 FROM voice_auto_owner_settings WHERE owner_id=1').fetchone():
            db.execute('''INSERT INTO voice_auto_owner_settings
                VALUES(1,20,40,4000,1,'synthetic','synthetic')''')
    return connect


def make_app(root):
    connect = bootstrap(root)
    # Synthetic demonstrations use a separate login audit, never the daily DB.
    os.environ['YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED'] = '0'
    sessions, suppliers = {}, {}
    @asynccontextmanager
    async def lifespan(app):
        yield
        for session in sessions.values():
            await session.cancel()
    app = FastAPI(lifespan=lifespan)
    @app.middleware('http')
    async def loopback(request, call_next):
        if request.headers.get('origin') not in (None, 'http://127.0.0.1:18893'):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'origin_rejected'}, status_code=403)
        return await call_next(request)
    def session(name):
        if name not in ('normal', 'first_rejected', 'first_unknown', 'partial_unknown', 'later_failed', 'cancel'):
            raise HTTPException(404)
        if name not in sessions:
            supplier = FakeSupplier(name)
            suppliers[name] = supplier
            text = (ROOT / 'tests/fixtures/voice_step3_two_request_zh.txt').read_text(encoding='utf-8')
            async def between():
                await asyncio.sleep(2 if name == 'cancel' else .05)
            sessions[name] = SegmentedReading(Path(root) / name, connect, text,
                lambda diag: ElevenLabsAdapter('synthetic', httpx.MockTransport(supplier.handle), diagnostics=diag),
                lambda: {'voice_id': 'synthetic_ivc_' + name, 'revision': 3},
                lambda count, first: auto_budget.reserve(connect, 'synthetic-session', 1, count,
                    first_request=first, limits=auto_budget.Limits(20,40,40000)),
                between=between)
        return sessions[name]
    @app.get('/api/voice-segmented-lab/{name}')
    async def status(name: str):
        s = session(name)
        return dict(preview=s.preview, result=s.result, real_requests=0,
                    fake_http=len(suppliers[name].calls))
    @app.post('/api/voice-segmented-lab/{name}/start')
    async def start(name: str, request: Request):
        try:
            return await session(name).start((await request.json())['preview_sha256'])
        except ValueError:
            raise HTTPException(409, '预览已变化或该次运行已占用') from None
    @app.post('/api/voice-segmented-lab/{name}/cancel')
    async def cancel(name: str):
        await session(name).cancel()
        return session(name).result
    @app.get('/api/voice-segmented-lab/{name}/audio/{sequence}')
    async def audio(name: str, sequence: int):
        try:
            return FileResponse(session(name).audio(sequence), media_type='audio/mpeg')
        except (ValueError, IndexError):
            raise HTTPException(404, '该段没有完整音频') from None
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--enable-synthetic-lab', action='store_true')
    args = parser.parse_args()
    if not args.enable_synthetic_lab:
        raise SystemExit('disabled')
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    def no_network(event, args):
        if event == 'socket.connect':
            raise PermissionError('synthetic_lab_network_denied')
    sys.addaudithook(no_network)
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(make_app(args.data_dir), host='127.0.0.1', port=18892,
        workers=1, access_log=False))
    loop.run_until_complete(server.serve())


if __name__ == '__main__':
    main()
