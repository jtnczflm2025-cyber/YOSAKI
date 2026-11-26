"""Isolated, bounded ChatGPT Plan renewal for one Voice V4 candidate.

Does not import main.py or open SQLite. Existing encrypted credentials are
staged in a temporary directory. Only a fresh credential for the same account
is atomically published back to the original credential file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from backend.app import chatgpt_plan


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data"
NAME = ".chatgpt_plan"
PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YOSAKI · ChatGPT 授权续期</title>
<style>body{font:16px system-ui;max-width:720px;margin:5vh auto;padding:24px;
background:#171521;color:#f3edf9}button,a{margin:8px;padding:10px 14px}
button{background:#534267;color:white;border:0;border-radius:6px}
pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#cdb8ff}</style>
<h1>ChatGPT 套餐授权续期 · 隔离入口</h1>
<p>此页面只更新本机 ChatGPT Plan 凭据，不发送聊天正文或语音。</p>
<p id="state">读取本地状态中…</p>
<button onclick="call('/refresh')">使用现有授权续期</button>
<button onclick="login()">需要时重新登录</button>
<button onclick="call('/finish')">登录完成后核验并保存</button>
<p id="link"></p><pre id="result"></pre>
<script>
async function status(){
 const r=await fetch('/status',{cache:'no-store'});document.getElementById('state').textContent=JSON.stringify(await r.json());
}
async function call(path){
 const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'}});
 document.getElementById('result').textContent=JSON.stringify(await r.json());
 await status();
}
async function login(){
 const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'}});
 const x=await r.json();document.getElementById('result').textContent=x.error||'请在新页面自行完成登录。';
 if(x.authorization_url){
  const a=document.createElement('a');a.href=x.authorization_url;a.target='_blank';
  a.rel='noopener noreferrer';a.textContent='打开 ChatGPT 官方授权页面';
  document.getElementById('link').replaceChildren(a);
 }
}
status();
</script></html>"""


class Stage:
    def __init__(self, temporary: Path) -> None:
        self.source = SOURCE.resolve()
        self.credential = self.source / NAME
        self.stage = temporary
        self.staged_credential = temporary / NAME
        self.key = (os.getenv("YOSAKI_SECRET_KEY") or "").encode() or (
            self.source / ".secret_key").read_bytes()
        raw = self.credential.read_bytes()
        self.source_sha256 = hashlib.sha256(raw).hexdigest()
        self.staged_credential.write_bytes(raw)
        self.original = self._decode(raw)
        account = self.original.get("account") or {}
        if not account.get("subject") or not account.get("client_id") or not account.get("refresh_token"):
            raise ValueError("existing_plan_account_incomplete")
        self.published = False
        self.refresh_attempted = False
        self.login_attempted = False
        self.catalog_ok = False

    def _decode(self, raw: bytes) -> dict:
        value = json.loads(Fernet(self.key).decrypt(raw))
        if not isinstance(value, dict):
            raise ValueError("credential_record_invalid")
        return value

    def status(self) -> dict:
        account = (self._decode(self.staged_credential.read_bytes()).get("account") or {})
        return {"source": "isolated_stage", "published": self.published,
                "access_token_present": bool(account.get("access_token")),
                "refresh_token_present": bool(account.get("refresh_token")),
                "required_scope_present": "chatgpt.tokens.use.direct" in account.get("scopes", []),
                "fresh_90s": float(account.get("expires_at") or 0) > time.time() + 90,
                "catalog_ok": self.catalog_ok}

    def publish(self) -> None:
        if self.published:
            raise ValueError("credential_already_published")
        raw = self.staged_credential.read_bytes()
        record = self._decode(raw)
        account = record.get("account") or {}
        previous = self.original.get("account") or {}
        if (account.get("subject") != previous["subject"] or
                account.get("client_id") != previous["client_id"] or
                not account.get("access_token") or not account.get("refresh_token") or
                "chatgpt.tokens.use.direct" not in account.get("scopes", []) or
                float(account.get("expires_at") or 0) <= time.time() + 90):
            raise ValueError("renewed_account_or_expiry_invalid")
        if hashlib.sha256(self.credential.read_bytes()).hexdigest() != self.source_sha256:
            raise ValueError("source_credential_changed")
        target = self.source / f"{NAME}.{secrets.token_hex(8)}.tmp"
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(self.credential.read_bytes()).hexdigest() != self.source_sha256:
                raise ValueError("source_credential_changed")
            os.replace(target, self.credential)
            self.published = True
        finally:
            if target.exists():
                target.unlink()


def make_app(stage: Stage, port: int) -> FastAPI:
    os.environ["YOSAKI_CHATGPT_PLAN_ENABLED"] = "1"
    os.environ["YOSAKI_DATA_DIR"] = str(stage.stage)
    # Install existing OAuth logic on an unused app to bind its encrypted
    # storage, while exposing only the three needed operations below.
    chatgpt_plan.install(FastAPI(), stage.stage, lambda: Fernet(stage.key))
    app = FastAPI()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
            return JSONResponse({"error": "local_only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"http://127.0.0.1:{port}":
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def panel():
        return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})

    @app.get("/status")
    def status():
        return stage.status()

    @app.post("/refresh")
    async def refresh():
        if stage.refresh_attempted or stage.published:
            return JSONResponse({"error": "refresh_already_attempted"}, status_code=409)
        stage.refresh_attempted = True
        try:
            await chatgpt_plan.access_token()
        except Exception as exc:
            return JSONResponse({"error": "oauth_refresh_failed",
                                 "category": type(exc).__name__}, status_code=409)
        try:
            catalog = await chatgpt_plan.models()
            stage.catalog_ok = True
            catalog_count = len(catalog)
            target_listed = any(item.get("slug") == "gpt-6.1-sol" for item in catalog)
        except Exception:
            catalog_count, target_listed = None, None
        try:
            stage.publish()
        except Exception as exc:
            return JSONResponse({"error": "credential_publish_failed",
                                 "category": type(exc).__name__}, status_code=409)
        return {"credential_published": True, "fresh_90s": True,
                "catalog_ok": stage.catalog_ok, "catalog_count": catalog_count,
                "target_model_listed": target_listed}

    @app.post("/login")
    async def login(request: Request):
        if stage.login_attempted or stage.published:
            return JSONResponse({"error": "login_already_attempted"}, status_code=409)
        stage.login_attempted = True
        try:
            return await chatgpt_plan.start(request)
        except HTTPException as exc:
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.post("/finish")
    async def finish():
        if not stage.login_attempted or stage.published:
            return JSONResponse({"error": "login_not_ready"}, status_code=409)
        try:
            catalog = await chatgpt_plan.models()
            stage.catalog_ok = True
            catalog_count = len(catalog)
            target_listed = any(item.get("slug") == "gpt-6.1-sol" for item in catalog)
        except Exception:
            catalog_count, target_listed = None, None
        try:
            stage.publish()
        except Exception as exc:
            return JSONResponse({"error": "credential_not_ready",
                                 "category": type(exc).__name__}, status_code=409)
        return {"credential_published": True, "fresh_90s": True,
                "catalog_ok": stage.catalog_ok, "catalog_count": catalog_count,
                "target_model_listed": target_listed}

    @app.post("/shutdown")
    async def shutdown():
        server = getattr(app.state, "server", None)
        if server is None:
            return JSONResponse({"error": "server_not_ready"}, status_code=503)
        server.should_exit = True
        return {"stopping": True, "credential_published": stage.published}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18787)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("invalid_port")
    with tempfile.TemporaryDirectory(prefix="yosaki-plan-renew-") as location:
        stage = Stage(Path(location))
        app = make_app(stage, args.port)
        print(json.dumps({"panel_url": f"http://127.0.0.1:{args.port}/",
                          "credential_source": str(stage.credential),
                          "isolated_data_dir": str(stage.stage),
                          "oauth_callback": "127.0.0.1:ephemeral/auth/callback",
                          "provider_requests_dispatched": 0}, ensure_ascii=True),
              flush=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
            port=args.port, log_level="warning", access_log=False, workers=1))
        app.state.server = server
        server.run()


if __name__ == "__main__":
    main()
