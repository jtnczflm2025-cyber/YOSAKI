"""Local Sign in with ChatGPT client. OAuth credentials never enter SQLite."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes
from fastapi import APIRouter, HTTPException, Request

from . import responses_adapter


AUTH_BASE = "https://auth.openai.com"
API_BASE = "https://api.openai.com/v1"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
router = APIRouter(prefix="/api/chatgpt")
store_lock = threading.RLock()
refresh_lock = asyncio.Lock()
_attempt: dict | None = None
_last_error = ""
_data_dir: Path | None = None
_fernet = None


def _enabled() -> bool:
    return os.getenv("YOSAKI_CHATGPT_PLAN_ENABLED") == "1"


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(503, "本机 ChatGPT 套餐接入未启用；请设置 YOSAKI_CHATGPT_PLAN_ENABLED=1")


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _read() -> dict:
    with store_lock:
        path = _data_dir / ".chatgpt_plan"  # type: ignore[operator]
        if not path.exists():
            return {}
        try:
            return json.loads(_fernet().decrypt(path.read_bytes()))
        except Exception as exc:
            raise HTTPException(500, "ChatGPT 授权文件无法解密；请检查本机密钥") from exc


def _write(record: dict) -> None:
    with store_lock:
        path = _data_dir / ".chatgpt_plan"  # type: ignore[operator]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        content = _fernet().encrypt(json.dumps(record, ensure_ascii=False).encode())
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(temporary, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()


def _public_status() -> dict:
    record = _read()
    account = record.get("account") or {}
    return {
        "enabled": _enabled(),
        "connected": bool(account.get("access_token") and account.get("refresh_token")),
        "plan_allowed": bool(account.get("access_token") and "chatgpt.tokens.use.direct" in account.get("scopes", [])),
        "email": account.get("email"),
        "pending": _attempt is not None,
        "error": _last_error,
    }


async def _validate_id_token(raw: str, client_id: str, nonce: str) -> dict:
    try:
        encoded_header, encoded_body, encoded_signature = raw.split(".")
        header = json.loads(_b64url(encoded_header))
        claims = json.loads(_b64url(encoded_body))
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError("unsupported token header")
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(f"{AUTH_BASE}/.well-known/jwks.json")
            response.raise_for_status()
        key = next(k for k in response.json()["keys"] if k.get("kid") == header["kid"] and k.get("kty") == "RSA")
        public_key = rsa.RSAPublicNumbers(int.from_bytes(_b64url(key["e"]), "big"), int.from_bytes(_b64url(key["n"]), "big")).public_key()
        public_key.verify(_b64url(encoded_signature), f"{encoded_header}.{encoded_body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        audience = claims.get("aud")
        if claims.get("iss") != AUTH_BASE or client_id not in (audience if isinstance(audience, list) else [audience]):
            raise ValueError("wrong issuer or audience")
        if claims.get("nonce") != nonce or not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError("wrong nonce or subject")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= time.time():
            raise ValueError("expired token")
        return claims
    except Exception as exc:
        raise HTTPException(401, "ChatGPT 身份令牌验证失败") from exc


async def _exchange(code: str, client_id: str, attempt: dict) -> None:
    global _last_error
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(f"{AUTH_BASE}/api/accounts/oauth/token", data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": attempt["verifier"], "redirect_uri": attempt["redirect_uri"], "resource": RESOURCE,
        })
    if response.status_code != 200:
        raise HTTPException(502, f"ChatGPT 授权交换失败（HTTP {response.status_code}）")
    token = response.json()
    if token.get("token_type", "Bearer").lower() != "bearer" or not token.get("access_token") or not token.get("refresh_token") or not token.get("id_token"):
        raise HTTPException(502, "ChatGPT 授权返回的令牌不完整")
    claims = await _validate_id_token(token["id_token"], client_id, attempt["nonce"])
    previous = _read()
    old = previous.get("account") or {}
    if attempt["client_id"] != "dynamic_agent_client" and (old.get("client_id") != client_id or old.get("subject") != claims["sub"]):
        raise HTTPException(401, "重新授权的 ChatGPT 账户与原账户不一致")
    scopes = token.get("scope", "").split()
    previous["account"] = {
        "client_id": client_id, "subject": claims["sub"], "email": claims.get("email"),
        "id_token": token["id_token"], "access_token": token["access_token"],
        "refresh_token": token["refresh_token"], "scopes": scopes,
        "expires_at": time.time() + int(token.get("expires_in", 3600)),
    }
    _write(previous)
    _last_error = "" if "chatgpt.tokens.use.direct" in scopes else "已登录，但账户未授予 ChatGPT 套餐使用权限"


async def _callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    global _attempt, _last_error
    attempt = _attempt
    status, message = 400, "授权回调无效；请返回 YOSAKI 重试。"
    valid_state = False
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        if len(line) > 8192:
            raise ValueError("callback too large")
        method, target, _ = line.decode("ascii").split(" ", 2)
        query = parse_qs(urlsplit(target).query)
        if method != "GET" or urlsplit(target).path != "/auth/callback" or not attempt or not secrets.compare_digest(query.get("state", [""])[0], attempt["state"]):
            raise ValueError("invalid callback")
        valid_state = True
        if query.get("error"):
            _last_error = "ChatGPT 授权被取消或拒绝"
            message = "授权未完成；可以关闭此页面。"
        else:
            client_id = query.get("client_id", [None])[0] or (attempt["client_id"] if attempt["client_id"] != "dynamic_agent_client" else None)
            if not client_id or (attempt["client_id"] != "dynamic_agent_client" and client_id != attempt["client_id"]):
                raise ValueError("client ID mismatch")
            code = query.get("code", [""])[0]
            if not code:
                raise ValueError("missing code")
            await _exchange(code, client_id, attempt)
            status, message = 200, "授权已处理；请返回 YOSAKI 查看账户状态。"
    except (ValueError, UnicodeDecodeError, asyncio.TimeoutError):
        _last_error = "ChatGPT 授权回调验证失败"
    except HTTPException as exc:
        _last_error = str(exc.detail)
    except (httpx.HTTPError, OSError):
        _last_error = "ChatGPT 授权服务暂时不可用"
    finally:
        if valid_state and attempt is _attempt:
            _attempt = None
            attempt["server"].close()
        body = f"<!doctype html><meta charset='utf-8'><title>YOSAKI</title><p>{message}</p>".encode()
        writer.write(f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()


async def access_token() -> str:
    _require_enabled()
    async with refresh_lock:
        record = _read()
        account = record.get("account") or {}
        if not account.get("access_token") or "chatgpt.tokens.use.direct" not in account.get("scopes", []):
            raise HTTPException(401, "请先连接已授权 ChatGPT 套餐的账户")
        if account.get("expires_at", 0) > time.time() + 60:
            return account["access_token"]
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(f"{AUTH_BASE}/api/accounts/oauth/token", data={
                "grant_type": "refresh_token", "client_id": account["client_id"],
                "refresh_token": account["refresh_token"], "resource": RESOURCE,
            })
        if response.status_code != 200:
            try:
                code = response.json().get("error")
            except ValueError:
                code = None
            if code in {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}:
                for key in ("access_token", "refresh_token", "id_token"):
                    account.pop(key, None)
                record["account"] = account
                _write(record)
                raise HTTPException(401, "ChatGPT 授权已失效，请重新登录")
            raise HTTPException(502, "ChatGPT 令牌刷新失败，请稍后重试")
        token = response.json()
        if not token.get("access_token") or not token.get("refresh_token"):
            raise HTTPException(502, "ChatGPT 刷新令牌返回不完整")
        account.update(access_token=token["access_token"], refresh_token=token["refresh_token"],
                       expires_at=time.time() + int(token.get("expires_in", 3600)),
                       scopes=token.get("scope", " ".join(account["scopes"])).split())
        record["account"] = account
        _write(record)
        return account["access_token"]


def payload(profile, messages: list[dict], tools: list[dict]) -> dict:
    instructions = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    body = {"model": profile["model_name"], "input": responses_adapter.input_items([m for m in messages if m.get("role") != "system"]),
            "store": False, "stream": True}
    if instructions:
        body["instructions"] = instructions
    if profile["reasoning_effort"]:
        body["reasoning"] = {"effort": profile["reasoning_effort"], "summary": "auto"}
        body["include"] = ["reasoning.encrypted_content"]
    if tools:
        body["tools"] = [{"type": "namespace", "name": "yosaki", "description": "YOSAKI 已授权的本地工具；每次调用仍由用户确认。",
                          "tools": [{"type": "function", **tool["function"]} for tool in tools]}]
        body["parallel_tool_calls"] = False
    return body


def error_message(event: dict) -> str:
    error = (event.get("response") or {}).get("error") or event.get("error") or {}
    code = error.get("code") if isinstance(error, dict) else None
    messages = {
        "subscription_sharing_usage_limit_exceeded": "ChatGPT 套餐或此应用的用量已达到限制；请在 ChatGPT 设置中查看用量。不会自动改用付费 API。",
        "subscription_sharing_usage_unavailable": "暂时无法检查 ChatGPT 套餐用量，请稍后重试。",
        "subscription_sharing_user_not_eligible": "当前 ChatGPT 账户或工作区不能使用套餐额度。",
        "subscription_sharing_unsupported_capability": "当前请求包含 ChatGPT 套餐通道不支持的参数或工具。",
        "subscription_sharing_invalid_user": "ChatGPT 授权已失效，请重新登录。",
    }
    return messages.get(code, f"ChatGPT 套餐请求失败（{code or '未知原因'}）")


def install(app, data_dir: Path, get_fernet) -> None:
    global _data_dir, _fernet
    _data_dir, _fernet = data_dir, get_fernet
    app.include_router(router)


@router.get("/status")
def status():
    return _public_status()


@router.post("/start")
async def start(request: Request):
    global _attempt, _last_error
    _require_enabled()
    if request.url.hostname not in ("127.0.0.1", "localhost") or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "ChatGPT 授权只能从本机工作台启动")
    if _attempt:
        raise HTTPException(409, "已有授权流程正在进行")
    record = _read()
    if not record.get("host_id"):
        record["host_id"] = f"urn:uuid:{uuid.uuid4()}"
        _write(record)
    account = record.get("account") or {}
    client_id = account.get("client_id") or "dynamic_agent_client"
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    server = await asyncio.start_server(_callback, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
    _attempt = {"state": state, "nonce": nonce, "verifier": verifier, "redirect_uri": redirect_uri,
                "client_id": client_id, "server": server}
    params = {"client_id": client_id, "ext_agent_host_id": record["host_id"], "response_type": "code",
              "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE, "state": state,
              "nonce": nonce, "code_challenge_method": "S256",
              "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()}
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = "YOSAKI"
    _last_error = ""
    async def expire():
        global _attempt
        await asyncio.sleep(600)
        if _attempt and _attempt.get("state") == state:
            _attempt["server"].close()
            _attempt = None
    asyncio.create_task(expire())
    return {"authorization_url": f"{AUTH_BASE}/api/accounts/authorize?{urlencode(params)}"}


@router.get("/models")
async def models():
    token = await access_token()
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(f"{API_BASE}/models", headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise HTTPException(502, f"读取 ChatGPT 可用模型失败（HTTP {response.status_code}）")
    return [{"slug": item["slug"], "display_name": item.get("display_name") or item["slug"]}
            for item in response.json().get("models", []) if item.get("visibility") == "list" and isinstance(item.get("slug"), str)]


@router.post("/disconnect")
async def disconnect():
    global _last_error
    record = _read()
    account = record.get("account") or {}
    revoked = False
    if account.get("refresh_token"):
        for retry in range(3):
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    config = await client.get(f"{AUTH_BASE}/.well-known/openid-configuration")
                    config.raise_for_status()
                    endpoint = config.json()["revocation_endpoint"]
                    response = await client.post(endpoint, data={"token": account["refresh_token"], "token_type_hint": "refresh_token", "client_id": account["client_id"]})
                if response.status_code == 200:
                    revoked = True
                    break
                if response.status_code < 500:
                    break
            except (httpx.HTTPError, KeyError, ValueError):
                pass
            await asyncio.sleep(0.2 * (retry + 1))
    for key in ("access_token", "refresh_token", "id_token"):
        account.pop(key, None)
    record["account"] = account
    _write(record)
    _last_error = "" if revoked else "已清除本机令牌；远端撤销未确认，请在 ChatGPT 设置中检查应用授权"
    return {"disconnected": True, "remote_revoked": revoked}
