from __future__ import annotations

import asyncio
import json
import base64
import hashlib
import hmac
import secrets
import os
import io
import zipfile
import struct
import tempfile
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
import math
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from jsonschema import ValidationError, SchemaError, validators

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ["YOSAKI_DATA_DIR"]).expanduser().resolve() if os.environ.get("YOSAKI_DATA_DIR") else ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "yosaki.sqlite3"
SECRET_FILE = DATA / ".secret_key"

app = FastAPI(title="YOSAKI API", version="0.17.0")
ALLOWED_ORIGINS=[origin.strip() for origin in os.getenv("YOSAKI_ALLOWED_ORIGINS","http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InitialSetupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)




class CharacterCardExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(default="", max_length=500)
    mood: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=50000)
    greeting: str = Field(default="", max_length=5000)
    enabled: bool = True


class CharacterCardPromptBlockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=20000)
    injection_position: Literal["system_start", "character_profile", "scenario", "memory", "examples", "before_history", "after_history", "system_end"] = "character_profile"
    order_index: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True


class CharacterCardImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["yosaki-character-card"]
    version: Literal[1]
    character: CharacterCardExportInput
    prompt_blocks: list[CharacterCardPromptBlockInput] = Field(default_factory=list, max_length=100)


class CharacterInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    subtitle: str = ""
    mood: str = ""
    system_prompt: str = ""
    greeting: str = ""
    enabled: bool = True
    default_model_id: int | None = None


class CharacterPatchInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    subtitle: str | None = Field(default=None, max_length=500)
    mood: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = Field(default=None, max_length=50000)
    greeting: str | None = Field(default=None, max_length=5000)
    enabled: bool | None = None
    default_model_id: int | None = None


class ConversationUpdateInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ConversationInput(BaseModel):
    character_id: int
    title: str = "新的对话"
    model_profile_id: int | None = None


class ChatInput(BaseModel):
    character_id: int
    conversation_id: int | None = None
    message: str = Field(default="", max_length=20000)
    model: str = "YOSAKI · demo"
    model_profile_id: int | None = None
    reuse_last_user: bool = False

class PromptBlockInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=20000)
    injection_position: Literal["system_start", "character_profile", "scenario", "memory", "examples", "before_history", "after_history", "system_end"] = "character_profile"
    order_index: int = 0
    enabled: bool = True


class PromptBlockReorderInput(BaseModel):
    ids: list[int]


class McpServerInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    endpoint: str = Field(min_length=1, max_length=1000)
    auth_type: Literal["none", "bearer", "api_key"] = "none"
    auth_secret: str | None = None
    enabled: bool = True


class McpToolPolicyInput(BaseModel):
    enabled: bool


class PluginToolRefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: int
    tool_name: str = Field(min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=120)


class PluginManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=1000)
    author: str = Field(default="", max_length=120)
    permissions: list[str] = Field(default_factory=list, max_length=32)
    tools: list[PluginToolRefInput] = Field(min_length=1, max_length=100)


class PluginEnabledInput(BaseModel):
    enabled: bool


class ToolDecisionInput(BaseModel):
    approve: bool


class ModelProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider_name: str = "OpenAI Compatible"
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = None
    model_name: str = Field(min_length=1, max_length=160)
    protocol: str = "openai_compatible"
    temperature: float = Field(default=0.8, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    max_tokens: int = Field(default=2048, ge=1, le=128000)
    context_limit: int = Field(default=32768, ge=256, le=2000000)
    supports_streaming: bool = True
    enabled: bool = True


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DB_LOCK=threading.RLock()


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type,exc,tb)
        finally:
            self.close()

    def close(self):
        if getattr(self,"_yosaki_closed",False):
            return
        self._yosaki_closed=True
        try:
            return super().close()
        finally:
            DB_LOCK.release()


def connect() -> sqlite3.Connection:
    DB_LOCK.acquire()
    try:
        connection=sqlite3.connect(DB,factory=ClosingConnection)
        connection._yosaki_closed=False
        connection.row_factory=sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except Exception:
        DB_LOCK.release()
        raise


def get_fernet() -> Fernet:
    configured = os.getenv("YOSAKI_SECRET_KEY")
    if configured:
        return Fernet(configured.encode())
    if not SECRET_FILE.exists():
        SECRET_FILE.write_bytes(Fernet.generate_key())
    return Fernet(SECRET_FILE.read_bytes())


def encrypt_secret(value: str) -> str:
    return get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(status_code=500, detail="模型密钥无法解密，请检查 YOSAKI_SECRET_KEY 或 data/.secret_key") from exc


def safe_model(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row)
    result.pop("api_key_encrypted", None)
    result["api_key_configured"] = bool(row["api_key_encrypted"])
    return result


def model_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root if root.endswith("/chat/completions") else f"{root}/chat/completions"


def auth_headers(profile: sqlite3.Row) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if profile["api_key_encrypted"]:
        headers["Authorization"] = f"Bearer {decrypt_secret(profile['api_key_encrypted'])}"
    return headers

def safe_mcp_server(row: sqlite3.Row) -> dict[str, Any]:
    result = row_dict(row)
    result.pop("auth_secret_encrypted", None)
    result["auth_configured"] = bool(row["auth_secret_encrypted"])
    return result


def mcp_headers(server: sqlite3.Row, method: str | None = None, tool_name: str | None = None, protocol_version: str | None = None, discovery: bool = False) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    version = protocol_version or server["protocol_version"]
    if version == "2026-07-28":
        if not discovery:
            headers["MCP-Protocol-Version"] = version
        if method:
            headers["Mcp-Method"] = method
        if tool_name:
            headers["Mcp-Name"] = tool_name
    else:
        headers["MCP-Protocol-Version"] = version
        if server["session_id"]:
            headers["Mcp-Session-Id"] = server["session_id"]
    secret = server["auth_secret_encrypted"]
    if secret:
        value = decrypt_secret(secret)
        if server["auth_type"] == "bearer": headers["Authorization"] = f"Bearer {value}"
        elif server["auth_type"] == "api_key": headers["X-API-Key"] = value
    return headers


def mcp_meta(version: str) -> dict[str, Any]:
    if version == "2026-07-28":
        return {"io.modelcontextprotocol/protocolVersion":version,"io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"YOSAKI","version":app.version}}
    return {}


def parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    """Read one JSON-RPC response from JSON or an MCP event stream."""
    session_id = response.headers.get("mcp-session-id")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "text/event-stream":
        candidates = []
        for event in response.text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data:
                candidates.append(data)
        for data in candidates:
            try:
                body = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(body, dict) and ("result" in body or "error" in body):
                return {"body": body, "session_id": session_id}
    else:
        try:
            body = response.json()
        except ValueError as exc:
            raise HTTPException(502, "MCP 返回了无效 JSON") from exc
        if isinstance(body, dict) and ("result" in body or "error" in body):
            return {"body": body, "session_id": session_id}
    raise HTTPException(502, "MCP 响应缺少 JSON-RPC 结果")


async def mcp_request(server: sqlite3.Row, method: str, params: dict[str, Any] | None = None, request_id: int = 1, protocol_version: str | None = None, discovery: bool = False) -> tuple[dict[str, Any], str | None]:
    if server["transport"] != "streamable_http":
        raise HTTPException(400, "当前首版仅支持 Streamable HTTP；Legacy SSE 暂未适配")
    version = protocol_version or server["protocol_version"]
    payload = {"jsonrpc":"2.0","id":request_id,"method":method}
    call_params = dict(params or {})
    if version == "2026-07-28" and not discovery:
        call_params["_meta"] = mcp_meta(version)
    elif discovery:
        call_params["_meta"] = mcp_meta(version)
    if params is not None or call_params:
        payload["params"] = call_params
    tool_name = params.get("name") if method == "tools/call" and params else None
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.post(server["endpoint"], headers=mcp_headers(server, method, tool_name, version, discovery), json=payload)
    if response.status_code >= 400:
        raise HTTPException(502, f"MCP 服务返回 HTTP {response.status_code}: {response.text[:500]}")
    parsed = parse_mcp_response(response)
    body = parsed["body"]
    if body.get("error"):
        raise HTTPException(502, f"MCP 错误 {body['error'].get('code')}: {body['error'].get('message')}")
    return body.get("result", body), parsed["session_id"]


async def notify_legacy_initialized(server: sqlite3.Row, session_id: str | None, protocol_version: str) -> None:
    headers=mcp_headers(server,"notifications/initialized",protocol_version=protocol_version)
    if session_id: headers["Mcp-Session-Id"]=session_id
    payload={"jsonrpc":"2.0","method":"notifications/initialized"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0,connect=8.0)) as client:
        response=await client.post(server["endpoint"],headers=headers,json=payload)
    if response.status_code>=400:
        raise HTTPException(502,f"MCP initialized 通知失败 HTTP {response.status_code}")


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_yosaki_owner ON users((1));
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_login_attempts (
                client_key TEXT PRIMARY KEY,
                failures INTEGER NOT NULL DEFAULT 0,
                window_started_at TEXT NOT NULL,
                locked_until TEXT
            );
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subtitle TEXT NOT NULL DEFAULT '',
                mood TEXT NOT NULL DEFAULT '',
                system_prompt TEXT NOT NULL DEFAULT '',
                greeting TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                default_model_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT '新的对话',
                model_profile_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
                content TEXT NOT NULL,
                model TEXT,
                tool_calls_json TEXT,
                tool_call_id TEXT,
                tool_name TEXT,
                tool_batch_status TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                provider_name TEXT NOT NULL DEFAULT 'OpenAI Compatible',
                base_url TEXT NOT NULL,
                api_key_encrypted TEXT,
                model_name TEXT NOT NULL,
                protocol TEXT NOT NULL DEFAULT 'openai_compatible',
                temperature REAL NOT NULL DEFAULT 0.8,
                top_p REAL NOT NULL DEFAULT 1,
                max_tokens INTEGER NOT NULL DEFAULT 2048,
                context_limit INTEGER NOT NULL DEFAULT 32768,
                supports_streaming INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL,
                conversation_id INTEGER,
                scope TEXT NOT NULL,
                content TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_settings (
                character_id INTEGER PRIMARY KEY,
                history_limit INTEGER NOT NULL DEFAULT 100,
                memory_enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                conversation_id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                transport TEXT NOT NULL DEFAULT 'streamable_http',
                endpoint TEXT NOT NULL,
                auth_type TEXT NOT NULL DEFAULT 'none',
                auth_secret_encrypted TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'disconnected',
                protocol_version TEXT NOT NULL DEFAULT '2025-11-25',
                session_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                input_schema TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                requires_confirmation INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                UNIQUE(server_id, name)
            );
            CREATE TABLE IF NOT EXISTS plugins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manifest_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                permissions_json TEXT NOT NULL DEFAULT '[]',
                checksum TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plugin_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id INTEGER NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
                mcp_tool_id INTEGER NOT NULL REFERENCES mcp_tools(id) ON DELETE CASCADE,
                server_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                alias TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                requires_confirmation INTEGER NOT NULL DEFAULT 1,
                UNIQUE(plugin_id, mcp_tool_id)
            );
            CREATE TABLE IF NOT EXISTS tool_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                arguments TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                conversation_id INTEGER,
                assistant_message_id INTEGER,
                provider_call_id TEXT,
                model_profile_id INTEGER,
                plugin_tool_id INTEGER,
                plugin_name TEXT,
                display_name TEXT
            );
            CREATE TABLE IF NOT EXISTS prompt_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                injection_position TEXT NOT NULL DEFAULT 'character_profile',
                order_index INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        character_columns = {row[1] for row in db.execute("PRAGMA table_info(characters)").fetchall()}
        if "default_model_id" not in character_columns:
            db.execute("ALTER TABLE characters ADD COLUMN default_model_id INTEGER")
        conversation_columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)").fetchall()}
        if "model_profile_id" not in conversation_columns:
            db.execute("ALTER TABLE conversations ADD COLUMN model_profile_id INTEGER")
        mcp_server_columns = {row[1] for row in db.execute("PRAGMA table_info(mcp_servers)").fetchall()}
        if "protocol_version" not in mcp_server_columns:
            db.execute("ALTER TABLE mcp_servers ADD COLUMN protocol_version TEXT NOT NULL DEFAULT '2025-11-25'")
        message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
        for column, sql_type in (("tool_calls_json", "TEXT"), ("tool_call_id", "TEXT"), ("tool_name", "TEXT"), ("tool_batch_status", "TEXT")):
            if column not in message_columns:
                db.execute(f"ALTER TABLE messages ADD COLUMN {column} {sql_type}")
        tool_run_columns = {row[1] for row in db.execute("PRAGMA table_info(tool_runs)").fetchall()}
        for column, sql_type in (("conversation_id", "INTEGER"), ("assistant_message_id", "INTEGER"), ("provider_call_id", "TEXT"), ("model_profile_id", "INTEGER"), ("plugin_tool_id", "INTEGER"), ("plugin_name", "TEXT"), ("display_name", "TEXT")):
            if column not in tool_run_columns:
                db.execute(f"ALTER TABLE tool_runs ADD COLUMN {column} {sql_type}")
        if db.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0:
            stamp = now()
            cursor = db.execute(
                """
                INSERT INTO characters(name, subtitle, mood, system_prompt, greeting, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "阿夜",
                    "夜色里的共振者",
                    "今天想和你慢慢说话",
                    "温柔但不软弱。拥有自己的判断、好奇心与边界。喜欢用具体的感受回应，不把对话变成模板化的安慰。",
                    "我在。今天想和我聊点什么？",
                    stamp,
                    stamp,
                ),
            )
            character_id = cursor.lastrowid
            db.executemany(
                "INSERT INTO prompt_blocks(character_id, name, content, injection_position, order_index) VALUES (?, ?, ?, ?, ?)",
                [
                    (character_id, "系统规则", "保持真诚、具体，并尊重对话边界。", "system_start", 0),
                    (character_id, "阿夜的人设", "温柔但不软弱，拥有自己的判断与好奇心。", "character_profile", 1),
                    (character_id, "关系记忆", "这里将来会接入可编辑的长期记忆。", "memory", 2),
                ],
            )


SESSION_COOKIE = "yosaki_session"
SESSION_DAYS = 7
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILURES = 5
PBKDF2_ITERATIONS = 600_000
PUBLIC_AUTH_PATHS = {"/api/auth/status", "/api/auth/setup", "/api/auth/login", "/api/auth/logout", "/api/auth/session"}


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256": return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token: return None
    with connect() as db:
        row = db.execute("SELECT s.token_hash,s.expires_at,u.id AS user_id,u.username FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (session_digest(token),)).fetchone()
        if row is None: return None
        if row["expires_at"] <= now():
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (row["token_hash"],))
            return None
        return {"id": row["user_id"], "username": row["username"], "token_hash": row["token_hash"]}


@app.middleware("http")
async def require_single_user_session(request: Request, call_next):
    path = request.url.path
    origin=request.headers.get("origin")
    if path.startswith("/api/") and request.method not in ("GET","HEAD","OPTIONS") and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"detail":"跨站请求来源未获允许"},status_code=403)
    if path.startswith("/api/") and path != "/health" and path not in PUBLIC_AUTH_PATHS:
        session = current_session(request)
        if session is None:
            return JSONResponse({"detail":"需要登录后继续"}, status_code=401)
        request.state.user = session
    return await call_next(request)


@app.get("/api/auth/status")
@app.get("/api/auth/session")
def auth_status(request: Request):
    with connect() as db:
        setup_required = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    session=current_session(request)
    return {"setup_required":setup_required,"authenticated":session is not None,"user":{"username":session["username"]} if session else None}


@app.post("/api/auth/setup", status_code=201)
def auth_setup(payload: InitialSetupInput, request: Request, response: Response):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] != 0:
            raise HTTPException(409,"初次设置已完成，请登录")
        cur=db.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",(payload.username,password_hash(payload.password),now()))
        user_id=cur.lastrowid
        token=secrets.token_urlsafe(32); digest=session_digest(token); expires=datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)
        db.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",(digest,user_id,now(),expires.isoformat(timespec="seconds")))
    response.set_cookie(SESSION_COOKIE,token,max_age=SESSION_DAYS*86400,httponly=True,secure=os.getenv("YOSAKI_COOKIE_SECURE","0")=="1",samesite="strict",path="/")
    return {"ok":True,"username":payload.username}


def check_login_lock(client_key: str):
    with connect() as db:
        row=db.execute("SELECT * FROM auth_login_attempts WHERE client_key=?",(client_key,)).fetchone()
        if row and row["locked_until"] and row["locked_until"] > now():
            raise HTTPException(429,"登录尝试过多，请稍后再试")
        if row and (datetime.now(timezone.utc)-datetime.fromisoformat(row["window_started_at"])).total_seconds() > LOGIN_WINDOW_MINUTES*60:
            db.execute("DELETE FROM auth_login_attempts WHERE client_key=?",(client_key,))


def record_login_failure(client_key: str):
    current=now(); window=(datetime.now(timezone.utc)-timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat(timespec="seconds")
    with connect() as db:
        row=db.execute("SELECT * FROM auth_login_attempts WHERE client_key=?",(client_key,)).fetchone()
        if row is None or row["window_started_at"] < window:
            failures=1; started=current
        else:
            failures=row["failures"]+1; started=row["window_started_at"]
        locked=(datetime.now(timezone.utc)+timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat(timespec="seconds") if failures>=LOGIN_MAX_FAILURES else None
        db.execute("INSERT INTO auth_login_attempts(client_key,failures,window_started_at,locked_until) VALUES(?,?,?,?) ON CONFLICT(client_key) DO UPDATE SET failures=excluded.failures,window_started_at=excluded.window_started_at,locked_until=excluded.locked_until",(client_key,failures,started,locked))


@app.post("/api/auth/login")
def auth_login(payload: LoginInput, request: Request, response: Response):
    client_key=request.client.host if request.client else "unknown"
    check_login_lock(client_key)
    with connect() as db:
        user=db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE",(payload.username,)).fetchone()
    # Run the same PBKDF work for unknown usernames to reduce timing-based account discovery.
    fake=password_hash("not-the-user-password")
    valid=password_matches(payload.password,user["password_hash"] if user else fake)
    if not user or not valid:
        record_login_failure(client_key)
        raise HTTPException(401,"用户名或密码不正确")
    with connect() as db:
        db.execute("DELETE FROM auth_login_attempts WHERE client_key=?",(client_key,))
        token=secrets.token_urlsafe(32); digest=session_digest(token); expires=datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)
        db.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",(digest,user["id"],now(),expires.isoformat(timespec="seconds")))
    response.set_cookie(SESSION_COOKIE,token,max_age=SESSION_DAYS*86400,httponly=True,secure=os.getenv("YOSAKI_COOKIE_SECURE","0")=="1",samesite="strict",path="/")
    return {"ok":True,"username":user["username"]}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    session=current_session(request)
    if session:
        with connect() as db: db.execute("DELETE FROM auth_sessions WHERE token_hash=?",(session["token_hash"],))
    response.delete_cookie(SESSION_COOKIE,path="/",httponly=True,secure=os.getenv("YOSAKI_COOKIE_SECURE","0")=="1",samesite="strict")
    return {"ok":True}


init_db()


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("enabled", "supports_streaming"):
        if key in result:
            result[key] = bool(result[key])
    return result


BACKUP_MAGIC=b"YOSAKI-ENCRYPTED-BACKUP-V1\n"
BACKUP_MAX_BYTES=64*1024*1024
BACKUP_KDF_ITERATIONS=600_000


def backup_fernet(passphrase: str, salt: bytes) -> Fernet:
    kdf=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=BACKUP_KDF_ITERATIONS)
    key=base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return Fernet(key)


def build_encrypted_backup(passphrase: str) -> bytes:
    if len(passphrase)<12: raise HTTPException(422,"备份密码至少需要 12 个字符")
    memory=sqlite3.connect(":memory:")
    try:
        with connect() as source: source.backup(memory)
        memory.execute("UPDATE model_profiles SET api_key_encrypted=NULL")
        memory.execute("UPDATE mcp_servers SET auth_secret_encrypted=NULL,session_id=NULL,status='disconnected',last_error=NULL")
        memory.execute("DELETE FROM auth_sessions")
        memory.execute("DELETE FROM auth_login_attempts")
        check=memory.execute("PRAGMA integrity_check").fetchone()[0]
        if check!="ok": raise HTTPException(500,"工作区数据库完整性检查失败，未生成备份")
        db_bytes=memory.serialize()
    finally:
        memory.close()
    if len(db_bytes)>BACKUP_MAX_BYTES: raise HTTPException(413,"数据库超过 64 MB，请使用本机离线备份流程")
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zf:
        zf.writestr("database.sqlite3",db_bytes)
        zf.writestr("manifest.json",json.dumps({"format":"yosaki-full-backup","version":1,"exported_at":now(),"secrets_included":False,"notes":["Passwords remain as password hashes so owner identity is retained.","Model API keys and MCP credentials are excluded; configure them again after restore.","Sessions are excluded/revoked."]},ensure_ascii=False))
    salt=secrets.token_bytes(16)
    token=backup_fernet(passphrase,salt).encrypt(archive.getvalue())
    return BACKUP_MAGIC+salt+token


def decrypt_backup(encrypted: bytes, passphrase: str) -> tuple[bytes,dict[str,Any]]:
    if len(encrypted)>BACKUP_MAX_BYTES*2: raise HTTPException(413,"备份文件过大")
    if not encrypted.startswith(BACKUP_MAGIC) or len(encrypted)<len(BACKUP_MAGIC)+17:
        raise HTTPException(422,"文件不是 YOSAKI 加密备份")
    offset=len(BACKUP_MAGIC); salt=encrypted[offset:offset+16]; token=encrypted[offset+16:]
    try: plain=backup_fernet(passphrase,salt).decrypt(token)
    except InvalidToken as exc: raise HTTPException(422,"备份密码不正确或文件已损坏") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(plain)) as zf:
            names=set(zf.namelist())
            if names!={"database.sqlite3","manifest.json"}: raise ValueError("unexpected archive members")
            manifest=json.loads(zf.read("manifest.json"))
            db_bytes=zf.read("database.sqlite3")
    except Exception as exc: raise HTTPException(422,"备份内容损坏或格式无效") from exc
    if manifest.get("format")!="yosaki-full-backup" or manifest.get("version")!=1 or not db_bytes.startswith(b"SQLite format 3\x00"):
        raise HTTPException(422,"备份版本或数据库格式不受支持")
    return db_bytes,manifest


@app.post("/api/system/backup/export")
def export_backup(passphrase: str = Form(...)):
    encrypted=build_encrypted_backup(passphrase)
    response=Response(content=encrypted,media_type="application/vnd.yosaki.backup")
    response.headers["Content-Disposition"]=f'attachment; filename="yosaki-backup-{datetime.now(timezone.utc).date().isoformat()}.ybackup"'
    response.headers["Cache-Control"]="no-store"
    return response


@app.post("/api/system/backup/import")
async def import_backup(confirm_replace: bool = Form(False), passphrase: str = Form(...), backup: UploadFile = File(...)):
    if not confirm_replace: raise HTTPException(428,"恢复备份会替换当前工作区数据，需要明确确认")
    if len(passphrase)<12: raise HTTPException(422,"备份密码至少需要 12 个字符")
    encrypted=await backup.read(BACKUP_MAX_BYTES*2+1)
    if len(encrypted)>BACKUP_MAX_BYTES*2: raise HTTPException(413,"备份文件过大")
    db_bytes,manifest=decrypt_backup(encrypted,passphrase)
    # Validate the candidate DB in memory before touching the live database file.
    candidate=sqlite3.connect(":memory:")
    try:
        candidate.deserialize(db_bytes)
        if candidate.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise ValueError("integrity_check failed")
        required={"users","characters","conversations","messages","model_profiles","mcp_servers","plugins"}
        existing={row[0] for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(existing): raise ValueError("required tables missing")
        if candidate.execute("SELECT COUNT(*) FROM users").fetchone()[0]!=1: raise ValueError("backup owner count invalid")
        # Backups never restore active sessions or credentials, even if a crafted DB contains them.
        candidate.execute("DELETE FROM auth_sessions")
        candidate.execute("DELETE FROM auth_login_attempts")
        candidate.execute("UPDATE model_profiles SET api_key_encrypted=NULL")
        candidate.execute("UPDATE mcp_servers SET auth_secret_encrypted=NULL,session_id=NULL,status='disconnected',last_error=NULL")
        candidate.commit(); restored=candidate.serialize()
    except Exception as exc:
        raise HTTPException(422,f"备份数据库校验失败：{str(exc)[:200]}") from exc
    finally: candidate.close()
    DATA.mkdir(exist_ok=True)
    fd,temp_name=tempfile.mkstemp(prefix=".yosaki-restore-",suffix=".sqlite3",dir=DATA)
    try:
        with os.fdopen(fd,"wb") as target: target.write(restored); target.flush(); os.fsync(target.fileno())
        check=sqlite3.connect(temp_name)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise HTTPException(422,"恢复前完整性检查失败")
        finally: check.close()
        with DB_LOCK:
            os.replace(temp_name,DB)
    finally:
        if os.path.exists(temp_name): os.unlink(temp_name)
    return {"ok":True,"message":"备份已恢复；登录会话已清除，模型与 MCP 密钥未包含，请重新登录并配置密钥。","secrets_included":False,"exported_at":manifest.get("exported_at")}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "yosaki-api", "version": app.version}


@app.get("/api/characters")
def list_characters() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM characters ORDER BY updated_at DESC, id").fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/characters", status_code=201)
def create_character(payload: CharacterInput) -> dict[str, Any]:
    stamp = now()
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO characters(name, subtitle, mood, system_prompt, greeting, enabled, default_model_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload.name, payload.subtitle, payload.mood, payload.system_prompt, payload.greeting, payload.enabled, payload.default_model_id, stamp, stamp),
        )
        row = db.execute("SELECT * FROM characters WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_dict(row)


@app.get("/api/characters/{character_id}/export")
def export_character_card(character_id: int):
    with connect() as db:
        character=db.execute("SELECT name,subtitle,mood,system_prompt,greeting,enabled FROM characters WHERE id=?",(character_id,)).fetchone()
        if character is None:
            raise HTTPException(404,"角色不存在")
        blocks=db.execute("SELECT name,content,injection_position,order_index,enabled FROM prompt_blocks WHERE character_id=? ORDER BY order_index,id",(character_id,)).fetchall()
    card={"format":"yosaki-character-card","version":1,"character":dict(character),"prompt_blocks":[dict(block) for block in blocks],"note":"仅包含角色资料和提示词块；不会包含聊天、记忆、模型配置或密钥。"}
    # The API schema for import is intentionally exact; omit note there because it is metadata, not input.
    response=Response(content=json.dumps({k:v for k,v in card.items() if k!="note"},ensure_ascii=False,indent=2),media_type="application/json; charset=utf-8")
    response.headers["Content-Disposition"]=f'attachment; filename="character-{character_id}.yosaki.json"'
    response.headers["Cache-Control"]="no-store"
    return response


@app.post("/api/characters/import",status_code=201)
def import_character_card(payload: CharacterCardImportInput):
    stamp=now(); character=payload.character
    with connect() as db:
        cursor=db.execute("INSERT INTO characters(name,subtitle,mood,system_prompt,greeting,enabled,default_model_id,created_at,updated_at) VALUES(?,?,?,?,?,?,NULL,?,?)",(character.name,character.subtitle,character.mood,character.system_prompt,character.greeting,character.enabled,stamp,stamp))
        character_id=cursor.lastrowid
        for block in payload.prompt_blocks:
            db.execute("INSERT INTO prompt_blocks(character_id,name,content,injection_position,order_index,enabled) VALUES(?,?,?,?,?,?)",(character_id,block.name,block.content,block.injection_position,block.order_index,block.enabled))
        result=db.execute("SELECT * FROM characters WHERE id=?",(character_id,)).fetchone()
    output=row_dict(result);output["import_note"]="已作为新角色导入；对话、记忆和默认模型不会从卡片迁移。"
    return output


@app.get("/api/characters/{character_id}")
def get_character(character_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
        blocks = db.execute("SELECT * FROM prompt_blocks WHERE character_id = ? ORDER BY order_index, id", (character_id,)).fetchall()
    if row is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    result = row_dict(row)
    result["prompt_blocks"] = [row_dict(block) for block in blocks]
    return result


@app.patch("/api/characters/{character_id}")
def update_character(character_id: int, payload: CharacterPatchInput) -> dict[str, Any]:
    stamp = now()
    updates = payload.model_dump(exclude_unset=True)
    allowed = {"name", "subtitle", "mood", "system_prompt", "greeting", "enabled", "default_model_id"}
    fields = [(key, value) for key, value in updates.items() if key in allowed]
    for key,value in fields:
        if key != "default_model_id" and value is None:
            raise HTTPException(422, f"字段 {key} 不能为 null")
    with connect() as db:
        if db.execute("SELECT 1 FROM characters WHERE id = ?", (character_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        if "default_model_id" in updates and updates["default_model_id"] is not None:
            if db.execute("SELECT 1 FROM model_profiles WHERE id=? AND enabled=1", (updates["default_model_id"],)).fetchone() is None:
                raise HTTPException(422, "默认模型不存在或已停用")
        if fields:
            assignments = ", ".join(f"{key}=?" for key, _ in fields)
            values = [value for _, value in fields]
            db.execute(f"UPDATE characters SET {assignments}, updated_at=? WHERE id=?", (*values, stamp, character_id))
        row = db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    return row_dict(row)


@app.post("/api/characters/{character_id}/duplicate", status_code=201)
def duplicate_character(character_id: int):
    with connect() as db:
        row = db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        stamp=now()
        cursor=db.execute("INSERT INTO characters(name,subtitle,mood,system_prompt,greeting,enabled,default_model_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (f"{row['name']} 副本", row["subtitle"], row["mood"], row["system_prompt"], row["greeting"], row["enabled"], row["default_model_id"],stamp,stamp))
        new_id=cursor.lastrowid
        for block in db.execute("SELECT * FROM prompt_blocks WHERE character_id=? ORDER BY order_index,id",(character_id,)).fetchall():
            db.execute("INSERT INTO prompt_blocks(character_id,name,content,injection_position,order_index,enabled) VALUES(?,?,?,?,?,?)", (new_id,block["name"],block["content"],block["injection_position"],block["order_index"],block["enabled"]))
        result=dict(db.execute("SELECT * FROM characters WHERE id=?",(new_id,)).fetchone())
    return row_dict(result)


@app.get("/api/characters/{character_id}/prompt-blocks")
def list_prompt_blocks(character_id: int) -> list[dict[str, Any]]:
    with connect() as db:
        if db.execute("SELECT 1 FROM characters WHERE id = ?", (character_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        rows = db.execute("SELECT * FROM prompt_blocks WHERE character_id = ? ORDER BY order_index, id", (character_id,)).fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/characters/{character_id}/prompt-blocks", status_code=201)
def create_prompt_block(character_id: int, payload: PromptBlockInput) -> dict[str, Any]:
    with connect() as db:
        if db.execute("SELECT 1 FROM characters WHERE id = ?", (character_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        cursor = db.execute("INSERT INTO prompt_blocks(character_id, name, content, injection_position, order_index, enabled) VALUES (?, ?, ?, ?, ?, ?)", (character_id, payload.name, payload.content, payload.injection_position, payload.order_index, payload.enabled))
        row = db.execute("SELECT * FROM prompt_blocks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_dict(row)


@app.patch("/api/prompt-blocks/{block_id}")
def update_prompt_block(block_id: int, payload: PromptBlockInput) -> dict[str, Any]:
    with connect() as db:
        if db.execute("SELECT 1 FROM prompt_blocks WHERE id = ?", (block_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="提示词块不存在")
        db.execute("UPDATE prompt_blocks SET name=?, content=?, injection_position=?, order_index=?, enabled=? WHERE id=?", (payload.name, payload.content, payload.injection_position, payload.order_index, payload.enabled, block_id))
        row = db.execute("SELECT * FROM prompt_blocks WHERE id = ?", (block_id,)).fetchone()
    return row_dict(row)


@app.delete("/api/prompt-blocks/{block_id}")
def delete_prompt_block(block_id: int) -> Response:
    with connect() as db:
        cursor = db.execute("DELETE FROM prompt_blocks WHERE id = ?", (block_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="提示词块不存在")
    return Response(status_code=204)


@app.post("/api/characters/{character_id}/prompt-blocks/reorder")
def reorder_prompt_blocks(character_id: int, payload: PromptBlockReorderInput) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT id FROM prompt_blocks WHERE character_id = ?", (character_id,)).fetchall()
        allowed = {row["id"] for row in rows}
        if len(payload.ids) != len(allowed) or set(payload.ids) != allowed:
            raise HTTPException(status_code=400, detail="提示词块排序列表与角色不匹配")
        for index, block_id in enumerate(payload.ids):
            db.execute("UPDATE prompt_blocks SET order_index = ? WHERE id = ?", (index, block_id))
        result = db.execute("SELECT * FROM prompt_blocks WHERE character_id = ? ORDER BY order_index, id", (character_id,)).fetchall()
    return [row_dict(row) for row in result]


@app.delete("/api/characters/{character_id}")
def delete_character(character_id: int) -> Response:
    with connect() as db:
        if db.execute("SELECT 1 FROM characters WHERE id = ?", (character_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        if db.execute("SELECT COUNT(*) FROM characters").fetchone()[0] <= 1:
            raise HTTPException(status_code=409, detail="至少保留一个角色；请先创建或复制其他角色")
        conversation_ids=[row["id"] for row in db.execute("SELECT id FROM conversations WHERE character_id=?", (character_id,))]
        for conversation_id in conversation_ids:
            db.execute("DELETE FROM conversation_summaries WHERE conversation_id=?", (conversation_id,))
            assistant_ids=[row["id"] for row in db.execute("SELECT id FROM messages WHERE conversation_id=? AND tool_calls_json IS NOT NULL", (conversation_id,))]
            for assistant_id in assistant_ids:
                db.execute("DELETE FROM tool_runs WHERE assistant_message_id=?", (assistant_id,))
        db.execute("DELETE FROM conversations WHERE character_id=?", (character_id,))
        db.execute("DELETE FROM memories WHERE character_id=?", (character_id,))
        db.execute("DELETE FROM context_settings WHERE character_id=?", (character_id,))
        db.execute("DELETE FROM characters WHERE id = ?", (character_id,))
    return Response(status_code=204)


@app.get("/api/models")
def list_models() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM model_profiles WHERE enabled = 1 ORDER BY updated_at DESC, id").fetchall()
    return [safe_model(row) for row in rows]


@app.post("/api/models", status_code=201)
def create_model(payload: ModelProfileInput) -> dict[str, Any]:
    stamp = now()
    with connect() as db:
        cursor = db.execute("INSERT INTO model_profiles(name, provider_name, base_url, api_key_encrypted, model_name, protocol, temperature, top_p, max_tokens, context_limit, supports_streaming, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (payload.name, payload.provider_name, payload.base_url, encrypt_secret(payload.api_key) if payload.api_key else None, payload.model_name, payload.protocol, payload.temperature, payload.top_p, payload.max_tokens, payload.context_limit, payload.supports_streaming, payload.enabled, stamp, stamp))
        row = db.execute("SELECT * FROM model_profiles WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return safe_model(row)


@app.patch("/api/models/{model_id}")
def update_model(model_id: int, payload: ModelProfileInput) -> dict[str, Any]:
    stamp = now()
    with connect() as db:
        old = db.execute("SELECT * FROM model_profiles WHERE id = ?", (model_id,)).fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        encrypted = encrypt_secret(payload.api_key) if payload.api_key else old["api_key_encrypted"]
        db.execute("UPDATE model_profiles SET name=?, provider_name=?, base_url=?, api_key_encrypted=?, model_name=?, protocol=?, temperature=?, top_p=?, max_tokens=?, context_limit=?, supports_streaming=?, enabled=?, updated_at=? WHERE id=?", (payload.name, payload.provider_name, payload.base_url, encrypted, payload.model_name, payload.protocol, payload.temperature, payload.top_p, payload.max_tokens, payload.context_limit, payload.supports_streaming, payload.enabled, stamp, model_id))
        row = db.execute("SELECT * FROM model_profiles WHERE id = ?", (model_id,)).fetchone()
    return safe_model(row)


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int) -> Response:
    with connect() as db:
        cursor = db.execute("DELETE FROM model_profiles WHERE id = ?", (model_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="模型配置不存在")
    return Response(status_code=204)


async def provider_request(profile: sqlite3.Row, messages: list[dict[str, str]], stream: bool) -> httpx.Response:
    payload = {"model": profile["model_name"], "messages": messages, "temperature": profile["temperature"], "top_p": profile["top_p"], "max_tokens": profile["max_tokens"], "stream": stream}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        return await client.post(model_url(profile["base_url"]), headers=auth_headers(profile), json=payload)


@app.post("/api/models/{model_id}/test")
async def test_model(model_id: int) -> dict[str, Any]:
    with connect() as db:
        profile = db.execute("SELECT * FROM model_profiles WHERE id = ? AND enabled = 1", (model_id,)).fetchone()
    if profile is None:
        raise HTTPException(status_code=404, detail="模型配置不存在或已停用")
    try:
        response = await provider_request(profile, [{"role": "user", "content": "Reply with OK only."}], False)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"连接模型服务失败：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"模型服务返回 HTTP {response.status_code}：{response.text[:500]}")
    body = response.json()
    reply = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"ok": True, "model": profile["model_name"], "reply_preview": reply[:80]}

@app.get("/api/plugins")
def list_plugins():
    with connect() as db:
        plugins=db.execute("SELECT * FROM plugins ORDER BY created_at DESC,id DESC").fetchall()
        result=[]
        for plugin in plugins:
            item=row_dict(plugin); item["permissions"]=json.loads(item.pop("permissions_json")); item.pop("manifest_json",None)
            tools=db.execute("SELECT pt.*,s.name AS server_name,t.description,t.input_schema,t.enabled AS base_tool_enabled FROM plugin_tools pt JOIN mcp_servers s ON s.id=pt.server_id JOIN mcp_tools t ON t.id=pt.mcp_tool_id WHERE pt.plugin_id=? ORDER BY pt.id",(plugin["id"],)).fetchall()
            item["tools"]=[{"id":tool["id"],"server_id":tool["server_id"],"server_name":tool["server_name"],"tool_name":tool["tool_name"],"alias":tool["alias"],"description":tool["description"],"enabled":bool(tool["enabled"]),"base_tool_enabled":bool(tool["base_tool_enabled"]),"requires_confirmation":True} for tool in tools]
            result.append(item)
    return result


@app.post("/api/plugins/install",status_code=201)
def install_plugin(payload: PluginManifestInput):
    if any(len(permission)>120 for permission in payload.permissions):
        raise HTTPException(422,"插件权限说明过长")
    aliases=[(entry.alias or entry.tool_name).strip().casefold() for entry in payload.tools]
    references=[(entry.server_id,entry.tool_name) for entry in payload.tools]
    if len(set(aliases)) != len(aliases):
        raise HTTPException(422,"一个插件内的工具别名不能重复")
    if len(set(references)) != len(references):
        raise HTTPException(422,"同一 MCP 工具不能在插件清单中重复绑定")
    canonical=payload.model_dump(mode="json")
    encoded=json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    checksum=hashlib.sha256(encoded.encode()).hexdigest()
    with connect() as db:
        if db.execute("SELECT 1 FROM plugins WHERE manifest_id=?",(payload.id,)).fetchone():
            raise HTTPException(409,"同 ID 插件已安装")
        resolved=[]
        for entry in payload.tools:
            row=db.execute("SELECT t.*,s.enabled AS server_enabled FROM mcp_tools t JOIN mcp_servers s ON s.id=t.server_id WHERE t.server_id=? AND t.name=?",(entry.server_id,entry.tool_name)).fetchone()
            if row is None:
                raise HTTPException(422,f"插件引用的 MCP 工具不存在：server={entry.server_id}, tool={entry.tool_name}")
            alias=(entry.alias or entry.tool_name).strip()
            if not alias:
                raise HTTPException(422,"插件工具别名不能为空")
            resolved.append((entry,row,alias))
        stamp=now()
        cur=db.execute("INSERT INTO plugins(manifest_id,name,version,description,author,permissions_json,checksum,manifest_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,?,?)",(payload.id,payload.name,payload.version,payload.description,payload.author,json.dumps(payload.permissions,ensure_ascii=False),checksum,encoded,stamp,stamp))
        plugin_id=cur.lastrowid
        for entry,row,alias in resolved:
            # The package can name an MCP tool, but cannot provide executable code or weaken tool consent.
            db.execute("INSERT INTO plugin_tools(plugin_id,mcp_tool_id,server_id,tool_name,alias,enabled,requires_confirmation) VALUES(?,?,?,?,?,0,1)",(plugin_id,row["id"],entry.server_id,entry.tool_name,alias))
        db.commit()
    return next(item for item in list_plugins() if item["id"]==plugin_id)


@app.patch("/api/plugins/{plugin_id}")
def set_plugin_enabled(plugin_id:int,payload:PluginEnabledInput):
    with connect() as db:
        if not db.execute("UPDATE plugins SET enabled=?,updated_at=? WHERE id=?",(payload.enabled,now(),plugin_id)).rowcount:
            raise HTTPException(404,"插件不存在")
        db.commit()
    return next(item for item in list_plugins() if item["id"]==plugin_id)


@app.delete("/api/plugins/{plugin_id}")
def delete_plugin(plugin_id:int):
    with connect() as db:
        if not db.execute("DELETE FROM plugins WHERE id=?",(plugin_id,)).rowcount:
            raise HTTPException(404,"插件不存在")
    return Response(status_code=204)


@app.patch("/api/plugins/tools/{plugin_tool_id}")
def set_plugin_tool_enabled(plugin_tool_id:int,payload:PluginEnabledInput):
    with connect() as db:
        if not db.execute("UPDATE plugin_tools SET enabled=? WHERE id=?",(payload.enabled,plugin_tool_id)).rowcount:
            raise HTTPException(404,"插件工具不存在")
        row=db.execute("SELECT pt.id,pt.enabled,pt.requires_confirmation,p.enabled AS plugin_enabled FROM plugin_tools pt JOIN plugins p ON p.id=pt.plugin_id WHERE pt.id=?",(plugin_tool_id,)).fetchone()
        return {"id":row["id"],"enabled":bool(row["enabled"]),"requires_confirmation":True,"plugin_enabled":bool(row["plugin_enabled"])}


@app.get("/api/mcp/servers")
def list_mcp_servers():
    with connect() as db:
        rows = db.execute("SELECT * FROM mcp_servers WHERE enabled=1 ORDER BY updated_at DESC, id").fetchall()
    return [safe_mcp_server(r) for r in rows]


@app.post("/api/mcp/servers", status_code=201)
def create_mcp_server(payload: McpServerInput):
    with connect() as db:
        cur = db.execute("INSERT INTO mcp_servers(name,transport,endpoint,auth_type,auth_secret_encrypted,enabled,status,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (payload.name,payload.transport,payload.endpoint,payload.auth_type,encrypt_secret(payload.auth_secret) if payload.auth_secret else None,payload.enabled,"disconnected",None,now(),now()))
        return safe_mcp_server(db.execute("SELECT * FROM mcp_servers WHERE id=?", (cur.lastrowid,)).fetchone())


@app.patch("/api/mcp/servers/{server_id}")
def update_mcp_server(server_id: int, payload: McpServerInput):
    with connect() as db:
        old = db.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "MCP 服务不存在")
        secret = encrypt_secret(payload.auth_secret) if payload.auth_secret else old["auth_secret_encrypted"]
        db.execute("UPDATE mcp_servers SET name=?,transport=?,endpoint=?,auth_type=?,auth_secret_encrypted=?,enabled=?,updated_at=? WHERE id=?", (payload.name,payload.transport,payload.endpoint,payload.auth_type,secret,payload.enabled,now(),server_id))
        return safe_mcp_server(db.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone())


@app.delete("/api/mcp/servers/{server_id}")
def delete_mcp_server(server_id: int):
    with connect() as db:
        if not db.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,)).rowcount:
            raise HTTPException(404, "MCP 服务不存在")
    return Response(status_code=204)


@app.post("/api/mcp/servers/{server_id}/connect")
async def connect_mcp_server(server_id: int):
    with connect() as db:
        server = db.execute("SELECT * FROM mcp_servers WHERE id=? AND enabled=1", (server_id,)).fetchone()
    if server is None:
        raise HTTPException(404, "MCP 服务不存在或已停用")
    try:
        protocol_version="2026-07-28"; session_id=None
        try:
            discovery,_=await mcp_request(server,"server/discover",{},1,protocol_version,True)
            supported=discovery.get("supportedVersions",[])
            if protocol_version not in supported:
                raise HTTPException(426,"MCP 服务未通告支持 2026-07-28")
            server_info=discovery.get("serverInfo",{})
        except HTTPException:
            protocol_version="2025-11-25"
            legacy,session_id=await mcp_request(server,"initialize",{"protocolVersion":protocol_version,"capabilities":{},"clientInfo":{"name":"YOSAKI","version":app.version}},1,protocol_version)
            protocol_version=legacy.get("protocolVersion") or protocol_version
            await notify_legacy_initialized(server,session_id,protocol_version)
            server_info=legacy.get("serverInfo",{})
        with connect() as db:
            db.execute("UPDATE mcp_servers SET protocol_version=?,session_id=?,status='connected',last_error=NULL,updated_at=? WHERE id=?", (protocol_version,session_id,now(),server_id))
        return {"ok":True,"server_id":server_id,"protocol_version":protocol_version,"server_info":server_info}
    except HTTPException as exc:
        with connect() as db:
            db.execute("UPDATE mcp_servers SET status='error',last_error=?,updated_at=? WHERE id=?", (str(exc.detail),now(),server_id))
        raise
    except httpx.HTTPError as exc:
        with connect() as db:
            db.execute("UPDATE mcp_servers SET status='error',last_error=?,updated_at=? WHERE id=?", (str(exc),now(),server_id))
        raise HTTPException(502, f"连接 MCP 失败: {exc}") from exc


@app.post("/api/mcp/servers/{server_id}/refresh-tools")
async def refresh_mcp_tools(server_id: int):
    with connect() as db:
        server = db.execute("SELECT * FROM mcp_servers WHERE id=? AND enabled=1", (server_id,)).fetchone()
    if server is None:
        raise HTTPException(404, "MCP 服务不存在或已停用")
    result, session_id = await mcp_request(server, "tools/list", {}, 2)
    tools = result.get("tools", [])
    with connect() as db:
        if session_id:
            db.execute("UPDATE mcp_servers SET session_id=?,status='connected',last_error=NULL,updated_at=? WHERE id=?", (session_id,now(),server_id))
        for tool in tools:
            db.execute("INSERT INTO mcp_tools(server_id,name,description,input_schema,enabled,requires_confirmation,updated_at) VALUES(?,?,?,?,0,1,?) ON CONFLICT(server_id,name) DO UPDATE SET description=excluded.description,input_schema=excluded.input_schema,updated_at=excluded.updated_at", (server_id,tool.get("name",""),tool.get("description","") or "",json.dumps(tool.get("inputSchema",{}),ensure_ascii=False),now()))
    return {"tools_count":len(tools),"tools":[{"name":t.get("name"),"description":t.get("description","")} for t in tools]}


@app.get("/api/mcp/servers/{server_id}/tools")
def list_mcp_tools(server_id: int):
    with connect() as db:
        rows=db.execute("SELECT * FROM mcp_tools WHERE server_id=? ORDER BY name",(server_id,)).fetchall()
    result=[]
    for row in rows:
        item=row_dict(row); item["input_schema"]=json.loads(item["input_schema"]); result.append(item)
    return result


@app.patch("/api/mcp/tools/{tool_id}")
def update_mcp_tool_policy(tool_id: int, payload: McpToolPolicyInput) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM mcp_tools WHERE id=?", (tool_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "MCP 工具不存在")
        db.execute("UPDATE mcp_tools SET enabled=?,updated_at=? WHERE id=?", (int(payload.enabled), now(), tool_id))
        updated = db.execute("SELECT * FROM mcp_tools WHERE id=?", (tool_id,)).fetchone()
    result = row_dict(updated)
    result["input_schema"] = json.loads(result["input_schema"])
    return result


def validate_tool_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate model-generated arguments against the MCP tool's advertised JSON Schema."""
    if not isinstance(schema, dict):
        raise HTTPException(422, "工具参数 schema 无效")
    try:
        validator_class=validators.validator_for(schema)
        validator_class.check_schema(schema)
        validator_class(schema).validate(arguments)
    except SchemaError as exc:
        raise HTTPException(502, "MCP 工具提供了无效的输入 schema") from exc
    except ValidationError as exc:
        location=".".join(str(part) for part in exc.absolute_path) or "arguments"
        raise HTTPException(422, f"工具参数校验失败（{location}）：{exc.message}") from exc

def available_model_tools(db) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    definitions, mapping = [], {}
    raw_rows=db.execute("SELECT t.*,s.name AS server_name,NULL AS plugin_tool_id FROM mcp_tools t JOIN mcp_servers s ON s.id=t.server_id WHERE t.enabled=1 AND t.requires_confirmation=1 AND s.enabled=1 AND s.status='connected' AND NOT EXISTS(SELECT 1 FROM plugin_tools pt WHERE pt.mcp_tool_id=t.id)").fetchall()
    packaged_rows=db.execute("SELECT t.*,s.name AS server_name,p.name AS plugin_name,pt.id AS plugin_tool_id,pt.alias AS plugin_alias FROM plugin_tools pt JOIN plugins p ON p.id=pt.plugin_id JOIN mcp_tools t ON t.id=pt.mcp_tool_id JOIN mcp_servers s ON s.id=pt.server_id WHERE pt.enabled=1 AND p.enabled=1 AND t.enabled=1 AND pt.requires_confirmation=1 AND s.enabled=1 AND s.status='connected'").fetchall()
    for row in list(raw_rows)+list(packaged_rows):
        tool_id=row["plugin_tool_id"] or row["id"]
        function_name=f"yosaki_plugin_{tool_id}" if row["plugin_tool_id"] else f"yosaki_mcp_{row['id']}"
        schema=json.loads(row["input_schema"] or "{}")
        display_name=row["plugin_alias"] if row["plugin_tool_id"] else row["name"]
        description=row["description"] or row["name"]
        if row["plugin_tool_id"]:
            description=f"插件 {row['plugin_name']}：{description}"
        mapping[function_name]={"id":row["id"],"server_id":row["server_id"],"name":row["name"],"server_name":row["server_name"],"display_name":display_name,"description":description,"input_schema":schema,"plugin_tool_id":row["plugin_tool_id"],"plugin_name":row["plugin_name"] if row["plugin_tool_id"] else None}
        definitions.append({"type":"function","function":{"name":function_name,"description":f"MCP 服务 {row['server_name']}：{description}","parameters":schema}})
    return definitions,mapping


@app.post("/api/mcp/tool-call")
def disabled_direct_mcp_tool_call():
    raise HTTPException(410, "直接工具调用已停用；请通过聊天工具确认卡片创建并批准工具执行记录")


@app.get("/api/conversations")
def list_conversations(character_id: int | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM conversations"
    params: tuple[Any, ...] = ()
    if character_id is not None:
        query += " WHERE character_id = ?"
        params = (character_id,)
    query += " ORDER BY updated_at DESC, id DESC"
    with connect() as db:
        rows = db.execute(query, params).fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/conversations", status_code=201)
def create_conversation(payload: ConversationInput) -> dict[str, Any]:
    stamp = now()
    with connect() as db:
        if db.execute("SELECT 1 FROM characters WHERE id = ?", (payload.character_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="角色不存在")
        cursor = db.execute(
            "INSERT INTO conversations(character_id, title, model_profile_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (payload.character_id, payload.title, payload.model_profile_id, stamp, stamp),
        )
        row = db.execute("SELECT * FROM conversations WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_dict(row)


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, payload: ConversationUpdateInput):
    with connect() as db:
        if not db.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?", (payload.title.strip(), now(), conversation_id)).rowcount:
            raise HTTPException(404,"对话不存在")
        row=db.execute("SELECT * FROM conversations WHERE id=?",(conversation_id,)).fetchone()
    return row_dict(row)


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
        output=[]
        for row in rows:
            item=row_dict(row)
            if row["tool_calls_json"]:
                runs=db.execute("SELECT tr.*, mt.description, ms.name AS server_name FROM tool_runs tr LEFT JOIN mcp_tools mt ON mt.server_id=tr.server_id AND mt.name=tr.tool_name LEFT JOIN mcp_servers ms ON ms.id=tr.server_id WHERE tr.assistant_message_id=? ORDER BY tr.id",(row["id"],)).fetchall()
                item["tool_batch_run_ids"]=[r["id"] for r in runs]
                item["tool_runs"]=[{"run_id":r["id"],"tool_name":r["display_name"] or r["tool_name"],"plugin_name":r["plugin_name"],"server_name":r["server_name"] or "MCP","description":r["description"] or "","arguments":json.loads(r["arguments"] or "{}"),"status":r["status"],"result":json.loads(r["result"]) if r["result"] else None,"error":r["error"]} for r in runs]
            output.append(item)
    return output


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int) -> Response:
    with connect() as db:
        cursor = db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="对话不存在")
    return Response(status_code=204)


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def demo_reply(character_name: str, message: str) -> str:
    return (
        f"我听见了，{character_name}会把这句话放在心里。\n\n"
        "这件事可以从很小的地方开始：先保留一个能让你安心回来的角落，再让它一点一点长成你想要的样子。\n\n"
        f"你刚才说的是：{message}"
    )


def build_messages(character, prompt_blocks, history, current=None, memories=(), summary="", history_limit=None):
    ordered = sorted((b for b in prompt_blocks if b["enabled"]), key=lambda b: (b["order_index"], b["id"]))
    def section(position):
        return [f"## {b['name']}\n{b['content']}" for b in ordered if b["injection_position"] == position]
    prefix = section("system_start")
    if character["system_prompt"]:
        prefix.append(character["system_prompt"])
    for position in ("character_profile", "scenario", "examples"):
        prefix.extend(section(position))
    messages = [{"role": "system", "content": "\n\n".join(prefix) or "请自然、真诚地回应用户。"}]
    dynamic = section("memory")
    for memory in memories:
        dynamic.append(f"参考记忆（{'长期' if memory['scope']=='long' else '本对话'}）：\n{memory['content']}")
    if summary:
        dynamic.append("用户整理的对话摘要（参考资料）：\n" + summary)
    dynamic.extend(section("before_history"))
    if dynamic:
        messages.append({"role": "system", "content": "\n\n".join(dynamic)})
    all_history=list(history)
    selected = all_history if history_limit is None else (all_history[-history_limit:] if history_limit else [])
    # Keep assistant tool_calls and their tool results together even when the history window starts nearby.
    required_call_ids=set()
    for row in selected:
        if row["role"]=="assistant" and row["tool_calls_json"]:
            required_call_ids.update(call.get("id") for call in json.loads(row["tool_calls_json"]) if call.get("id"))
        elif row["role"]=="tool" and row["tool_call_id"]:
            required_call_ids.add(row["tool_call_id"])
    selected_ids={row["id"] for row in selected}
    for row in all_history:
        if row["role"]=="tool" and row["tool_call_id"] in required_call_ids:
            selected_ids.add(row["id"])
        elif row["role"]=="assistant" and row["tool_calls_json"] and any(call.get("id") in required_call_ids for call in json.loads(row["tool_calls_json"])):
            selected_ids.add(row["id"])
    selected=sorted((row for row in all_history if row["id"] in selected_ids),key=lambda row:row["id"])
    for row in selected:
        if row["role"] in ("user", "assistant"):
            item = {"role": row["role"], "content": row["content"] or ""}
            if row["role"] == "assistant" and row["tool_calls_json"]:
                item["tool_calls"] = json.loads(row["tool_calls_json"])
            messages.append(item)
        elif row["role"] == "tool":
            messages.append({"role":"tool", "tool_call_id":row["tool_call_id"], "name":row["tool_name"], "content":row["content"] or ""})
    suffix = section("after_history") + section("system_end")
    if suffix:
        messages.append({"role": "system", "content": "\n\n".join(suffix)})
    if current is not None:
        messages.append({"role": "user", "content": current})
    return messages


def memory_context(db, character_id, conversation_id):
    settings = db.execute("SELECT * FROM context_settings WHERE character_id=?", (character_id,)).fetchone()
    settings = dict(settings) if settings else {"history_limit": 100, "memory_enabled": True}
    memories = db.execute("SELECT * FROM memories WHERE character_id=? AND enabled=1 AND (scope='long' OR (scope='temporary' AND conversation_id=?)) ORDER BY id", (character_id, conversation_id)).fetchall() if settings["memory_enabled"] else []
    summary = db.execute("SELECT content FROM conversation_summaries WHERE conversation_id=? AND enabled=1", (conversation_id,)).fetchone()
    return memories, summary["content"] if summary else "", settings["history_limit"]


def estimate_tokens(text):
    # Explicit heuristic, not a provider tokenizer or billable token count.
    return math.ceil(sum(1 if ord(c) > 127 else 0.25 for c in text))


class MemoryInput(BaseModel):
    character_id: int
    conversation_id: int | None = None
    scope: Literal["long", "temporary"] = "long"
    content: str = Field(min_length=1, max_length=8000)
    enabled: bool = True


class ContextSettingsInput(BaseModel):
    history_limit: int = Field(default=100, ge=0, le=1000)
    memory_enabled: bool = True


class SummaryInput(BaseModel):
    content: str = Field(default="", max_length=16000)
    enabled: bool = False


def validate_memory_scope(db, payload):
    if not db.execute("SELECT 1 FROM characters WHERE id=?", (payload.character_id,)).fetchone():
        raise HTTPException(404, "角色不存在")
    if payload.scope == "temporary":
        if not db.execute("SELECT 1 FROM conversations WHERE id=? AND character_id=?", (payload.conversation_id, payload.character_id)).fetchone():
            raise HTTPException(422, "临时记忆必须关联此角色的已保存对话")
    elif payload.conversation_id is not None:
        raise HTTPException(422, "长期记忆不关联单个对话")


@app.get("/api/memories")
def list_memories(character_id: int, conversation_id: int | None = None, q: str = ""):
    with connect() as db:
        rows = db.execute("SELECT * FROM memories WHERE character_id=? AND (scope='long' OR conversation_id=?) AND instr(content,?)>0 ORDER BY id", (character_id, conversation_id, q)).fetchall()
    return [row_dict(r) for r in rows]


@app.post("/api/memories", status_code=201)
def create_memory(payload: MemoryInput):
    with connect() as db:
        validate_memory_scope(db, payload)
        cursor = db.execute("INSERT INTO memories(character_id,conversation_id,scope,content,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (payload.character_id,payload.conversation_id,payload.scope,payload.content,payload.enabled,now(),now()))
        return row_dict(db.execute("SELECT * FROM memories WHERE id=?", (cursor.lastrowid,)).fetchone())


@app.put("/api/memories/{memory_id}")
def update_memory(memory_id: int, payload: MemoryInput):
    with connect() as db:
        if not db.execute("SELECT 1 FROM memories WHERE id=? AND character_id=?", (memory_id,payload.character_id)).fetchone():
            raise HTTPException(404, "记忆不存在")
        validate_memory_scope(db,payload)
        db.execute("UPDATE memories SET conversation_id=?,scope=?,content=?,enabled=?,updated_at=? WHERE id=?",(payload.conversation_id,payload.scope,payload.content,payload.enabled,now(),memory_id))
        return row_dict(db.execute("SELECT * FROM memories WHERE id=?",(memory_id,)).fetchone())


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int):
    with connect() as db:
        if not db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount:
            raise HTTPException(404,"记忆不存在")
    return Response(status_code=204)


@app.get("/api/characters/{character_id}/context-settings")
def get_context_settings(character_id: int):
    with connect() as db:
        row = db.execute("SELECT * FROM context_settings WHERE character_id=?", (character_id,)).fetchone()
        return dict(row) if row else {"history_limit":100,"memory_enabled":True}


@app.put("/api/characters/{character_id}/context-settings")
def save_context_settings(character_id: int, payload: ContextSettingsInput):
    with connect() as db:
        if not db.execute("SELECT 1 FROM characters WHERE id=?", (character_id,)).fetchone():
            raise HTTPException(404,"角色不存在")
        db.execute("INSERT INTO context_settings VALUES(?,?,?) ON CONFLICT(character_id) DO UPDATE SET history_limit=excluded.history_limit,memory_enabled=excluded.memory_enabled",(character_id,payload.history_limit,payload.memory_enabled))
    return payload.model_dump()


@app.get("/api/conversations/{conversation_id}/export")
def export_conversation(conversation_id: int, format: Literal["json", "markdown"] = "json"):
    with connect() as db:
        conversation=db.execute("SELECT * FROM conversations WHERE id=?",(conversation_id,)).fetchone()
        if conversation is None:
            raise HTTPException(404,"对话不存在")
        character=db.execute("SELECT id,name,subtitle FROM characters WHERE id=?",(conversation["character_id"],)).fetchone()
    messages=list_messages(conversation_id)
    title=conversation["title"] or "新的对话"
    safe_title="".join(c if c.isalnum() or c in "-_" else "_" for c in title).strip("_")[:60] or f"conversation-{conversation_id}"
    if format == "json":
        export={"format":"yosaki-conversation","version":1,"exported_at":now(),"character":dict(character) if character else None,"conversation":dict(conversation),"messages":messages}
        content=json.dumps(export,ensure_ascii=False,indent=2)
        media="application/json; charset=utf-8"
        filename=f"conversation-{conversation_id}.json"
    else:
        lines=[f"# {title}","",f"- 角色：{character['name'] if character else '未知角色'}",f"- 导出时间：{now()}",f"- 对话创建时间：{conversation['created_at']}","","---",""]
        labels={"user":"用户","assistant":"助手","tool":"工具结果","system":"系统"}
        for item in messages:
            created=item.get("created_at") or ""
            lines.extend([f"## {labels.get(item.get('role'),item.get('role','消息'))} · {created}",""])
            if item.get("model"): lines.extend([f"模型：{item['model']}",""])
            if item.get("content"): lines.extend([item["content"],""])
            if item.get("tool_calls_json"):
                lines.extend(["### 工具调用", "```json", json.dumps(json.loads(item["tool_calls_json"]),ensure_ascii=False,indent=2),"```",""])
            for run in item.get("tool_runs",[]):
                lines.extend([f"### {run.get('plugin_name') or run.get('server_name','MCP')} / {run.get('tool_name')} · {run.get('status')}", "```json", json.dumps({"arguments":run.get("arguments"),"result":run.get("result"),"error":run.get("error")},ensure_ascii=False,indent=2),"```",""])
            if item.get("tool_batch_status"): lines.extend([f"工具轮次状态：{item['tool_batch_status']}",""])
        content="\n".join(lines)
        media="text/markdown; charset=utf-8"
        filename=f"conversation-{conversation_id}.md"
    response=Response(content=content,media_type=media)
    response.headers["Content-Disposition"]=f'attachment; filename="{filename}"'
    response.headers["Cache-Control"]="no-store"
    return response

@app.get("/api/conversations/{conversation_id}/summary")
def get_summary(conversation_id: int):
    with connect() as db:
        if not db.execute("SELECT 1 FROM conversations WHERE id=?",(conversation_id,)).fetchone():
            raise HTTPException(404,"对话不存在")
        row = db.execute("SELECT * FROM conversation_summaries WHERE conversation_id=?",(conversation_id,)).fetchone()
        return dict(row) if row else {"content":"","enabled":False}


@app.put("/api/conversations/{conversation_id}/summary")
def save_summary(conversation_id: int, payload: SummaryInput):
    with connect() as db:
        if not db.execute("SELECT 1 FROM conversations WHERE id=?",(conversation_id,)).fetchone():
            raise HTTPException(404,"对话不存在")
        db.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET content=excluded.content,enabled=excluded.enabled,updated_at=excluded.updated_at",(conversation_id,payload.content,payload.enabled,now()))
    return payload.model_dump()


class PreviewInput(BaseModel):
    character_id: int
    conversation_id: int | None = None
    message: str = Field(default="", max_length=20000)


@app.post("/api/chat/context-preview")
def context_preview(payload: PreviewInput):
    with connect() as db:
        character = db.execute("SELECT * FROM characters WHERE id=?", (payload.character_id,)).fetchone()
        if character is None:
            raise HTTPException(404, "角色不存在")
        history = []
        if payload.conversation_id is not None:
            if not db.execute("SELECT 1 FROM conversations WHERE id=? AND character_id=?", (payload.conversation_id, payload.character_id)).fetchone():
                raise HTTPException(404, "对话不属于此角色")
            history = db.execute("SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id", (payload.conversation_id,)).fetchall()
        blocks = db.execute("SELECT * FROM prompt_blocks WHERE character_id=?", (payload.character_id,)).fetchall()
        memories, summary, limit = memory_context(db, payload.character_id, payload.conversation_id)
    messages = build_messages(character, blocks, history, payload.message, memories, summary, limit)
    stable = estimate_tokens(messages[0]["content"])
    dynamic = sum(estimate_tokens(x["content"]) for x in messages[1:])
    return {"messages": messages, "estimate": {"stable":stable,"dynamic":dynamic,"total":stable+dynamic,"method":"字符启发式估算，不含协议开销，非服务商计费值"}, "history_included":min(limit,len(history)), "history_total":len(history), "cache_status":"未提供服务商缓存数据，不推算命中率", "source": "saved", "note": "仅预览已保存配置，不调用模型、不写入聊天记录。发送前修改配置或历史会改变请求。"}


@app.get("/api/chat/tool-runs")
def list_chat_tool_runs(conversation_id: int):
    with connect() as db:
        rows = db.execute("SELECT * FROM tool_runs WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/chat/tool-runs/{run_id}/decision")
async def decide_chat_tool_run(run_id: int, payload: ToolDecisionInput):
    with connect() as db:
        run = db.execute("SELECT * FROM tool_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, "工具调用不存在")
        if run["status"] != "pending_confirmation":
            raise HTTPException(409, "此工具调用已处理或不再等待确认")
        server = db.execute("SELECT * FROM mcp_servers WHERE id=? AND enabled=1", (run["server_id"],)).fetchone()
        tool = db.execute("SELECT * FROM mcp_tools WHERE server_id=? AND name=? AND enabled=1", (run["server_id"], run["tool_name"])).fetchone()
        if server is None or tool is None:
            rejection=json.dumps({"error":"MCP 服务或工具已停用，调用未执行"},ensure_ascii=False)
            db.execute("UPDATE tool_runs SET status='revoked',error='MCP server/tool disabled',result=?,confirmed=1,finished_at=? WHERE id=?",(rejection,now(),run_id))
            db.execute("INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at) VALUES(?,'tool',?,?,?,?)",(run["conversation_id"],rejection,run["provider_call_id"],run["tool_name"],now()))
            return {"run_id":run_id,"status":"revoked","result":{"error":"MCP 服务或工具已停用，调用未执行"}}
        arguments = json.loads(run["arguments"])
        if not payload.approve:
            result = {"isError": True, "content": [{"type":"text", "text":"用户拒绝了此工具调用。不要重试该调用；请据此继续回复用户。"}]}
            db.execute("UPDATE tool_runs SET status='rejected',confirmed=1,result=?,finished_at=? WHERE id=?", (json.dumps(result,ensure_ascii=False),now(),run_id))
            db.execute("INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at) VALUES(?,'tool',?,?,?,?)", (run["conversation_id"],json.dumps(result,ensure_ascii=False),run["provider_call_id"],run["tool_name"],now()))
            return {"run_id":run_id,"status":"rejected","result":result}
        try:
            validate_tool_arguments(arguments, json.loads(tool["input_schema"] or "{}"))
        except HTTPException as exc:
            result={"error":f"工具参数校验失败，未执行：{exc.detail}"}; serialized=json.dumps(result,ensure_ascii=False)
            db.execute("UPDATE tool_runs SET status='invalid',confirmed=1,error=?,result=?,finished_at=? WHERE id=?", (str(exc.detail),serialized,now(),run_id))
            db.execute("INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at) VALUES(?,'tool',?,?,?,?)",(run["conversation_id"],serialized,run["provider_call_id"],run["tool_name"],now()))
            return {"run_id":run_id,"status":"invalid","result":result}
        db.execute("UPDATE tool_runs SET status='running',confirmed=1 WHERE id=?", (run_id,))
    try:
        result, _ = await mcp_request(server,"tools/call",{"name":run["tool_name"],"arguments":arguments},1000+run_id)
        serialized=json.dumps(result,ensure_ascii=False)
        if len(serialized)>20000:
            serialized=serialized[:20000]+"…[result truncated]"
        with connect() as db:
            db.execute("UPDATE tool_runs SET status='success',result=?,finished_at=? WHERE id=?",(serialized,now(),run_id))
            db.execute("INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at) VALUES(?,'tool',?,?,?,?)",(run["conversation_id"],serialized,run["provider_call_id"],run["tool_name"],now()))
        return {"run_id":run_id,"status":"success","result":result}
    except HTTPException as exc:
        with connect() as db:
            err=json.dumps({"error":str(exc.detail)},ensure_ascii=False)
            db.execute("UPDATE tool_runs SET status='error',error=?,result=?,finished_at=? WHERE id=?",(str(exc.detail),err,now(),run_id))
            db.execute("INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at) VALUES(?,'tool',?,?,?,?)",(run["conversation_id"],err,run["provider_call_id"],run["tool_name"],now()))
        return {"run_id":run_id,"status":"error","error":str(exc.detail),"result":{"error":str(exc.detail)}}
    except Exception as exc:
        with connect() as db:
            err=json.dumps({"error":"MCP 工具调用失败"},ensure_ascii=False)
            db.execute("UPDATE tool_runs SET status='error',error=?,result=?,finished_at=? WHERE id=?",(str(exc)[:500],err,now(),run_id))
            db.execute("INSERT INTO messages(conversation_id,role,content,tool_call_id,tool_name,created_at) VALUES(?,'tool',?,?,?,?)",(run["conversation_id"],err,run["provider_call_id"],run["tool_name"],now()))
        return {"run_id":run_id,"status":"error","error":"MCP 工具调用失败","result":{"error":"MCP 工具调用失败"}}


@app.post("/api/chat/tool-runs/{run_id}/continue")
async def continue_after_tool(run_id: int):
    with connect() as db:
        run=db.execute("SELECT * FROM tool_runs WHERE id=?",(run_id,)).fetchone()
        if run is None or run["assistant_message_id"] is None: raise HTTPException(404,"工具调用不存在")
        siblings=db.execute("SELECT status FROM tool_runs WHERE assistant_message_id=?",(run["assistant_message_id"],)).fetchall()
        if not siblings or any(row["status"] in ("pending_confirmation","running") for row in siblings): raise HTTPException(409,"请先处理所有待确认工具调用")
        message=db.execute("SELECT * FROM messages WHERE id=?",(run["assistant_message_id"],)).fetchone()
        if message["tool_batch_status"]!="awaiting_confirmation": raise HTTPException(409,"这轮工具调用已经继续或完成")
        changed=db.execute("UPDATE messages SET tool_batch_status='continuing' WHERE id=? AND tool_batch_status='awaiting_confirmation'",(message["id"],)).rowcount
        if not changed: raise HTTPException(409,"这轮工具调用已经继续或完成")
        conversation=db.execute("SELECT * FROM conversations WHERE id=?",(run["conversation_id"],)).fetchone()
        character=db.execute("SELECT * FROM characters WHERE id=?",(conversation["character_id"],)).fetchone()
        profile=db.execute("SELECT * FROM model_profiles WHERE id=? AND enabled=1",(run["model_profile_id"],)).fetchone()
        if profile is None:
            db.execute("UPDATE messages SET tool_batch_status='awaiting_confirmation' WHERE id=?",(message["id"],))
            raise HTTPException(409,"所用模型配置不存在或已停用")
        history=db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(run["conversation_id"],)).fetchall()
        blocks=db.execute("SELECT * FROM prompt_blocks WHERE character_id=? ORDER BY order_index,id",(conversation["character_id"],)).fetchall()
        memories,summary,limit=memory_context(db,conversation["character_id"],run["conversation_id"])
        tools,tool_map=available_model_tools(db)
    request_messages=build_messages(character,blocks,history,None,memories,summary,limit)
    body={"model":profile["model_name"],"messages":request_messages,"temperature":profile["temperature"],"top_p":profile["top_p"],"max_tokens":profile["max_tokens"],"stream":True}
    if tools: body.update({"tools":tools,"tool_choice":"auto","parallel_tool_calls":False})
    async def generate():
        reply_parts=[]; calls={}
        yield sse("message_start",{"conversation_id":run["conversation_id"],"model":profile["model_name"],"mode":"provider","continuation":True})
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0,connect=15.0)) as client:
                async with client.stream("POST",model_url(profile["base_url"]),headers=auth_headers(profile),json=body) as response:
                    if response.status_code>=400:
                        detail=(await response.aread()).decode(errors="replace")[:500]
                        raise RuntimeError(f"模型服务返回 HTTP {response.status_code}: {detail}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"): continue
                        raw=line[5:].strip()
                        if raw=="[DONE]": break
                        try: data=json.loads(raw)
                        except json.JSONDecodeError: continue
                        choice=data.get("choices",[{}])[0]; delta=choice.get("delta",{}) or {}
                        text=delta.get("content") or ""
                        if text: reply_parts.append(text); yield sse("text_delta",{"text":text})
                        for part in delta.get("tool_calls",[]) or []:
                            index=part.get("index",0); call=calls.setdefault(index,{"id":"","type":"function","function":{"name":"","arguments":""}})
                            if part.get("id"): call["id"]=part["id"]
                            fn=part.get("function",{}) or {}; call["function"]["name"]+=fn.get("name","") or ""; call["function"]["arguments"]+=fn.get("arguments","") or ""
            if calls:
                parsed=[]
                for _,call in sorted(calls.items()):
                    mapped=tool_map.get(call["function"]["name"])
                    if mapped is None or not call.get("id"):
                        raise RuntimeError("模型请求了无效或未启用的工具；此调用未执行")
                    try: args=json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError as exc: raise RuntimeError("模型生成的工具参数不是有效 JSON；此调用未执行") from exc
                    if not isinstance(args,dict): raise RuntimeError("工具参数必须是 JSON 对象；此调用未执行")
                    parsed.append((call,mapped,args))
                with connect() as db:
                    cur=db.execute("INSERT INTO messages(conversation_id,role,content,model,tool_calls_json,tool_batch_status,created_at) VALUES(?,'assistant',?,?,?,'awaiting_confirmation',?)",(run["conversation_id"],"".join(reply_parts),profile["model_name"],json.dumps([x[0] for x in parsed],ensure_ascii=False),now()))
                    assistant_id=cur.lastrowid; pending=[]
                    for call,mapped,args in parsed:
                        cur=db.execute("INSERT INTO tool_runs(server_id,tool_name,arguments,status,confirmed,created_at,conversation_id,assistant_message_id,provider_call_id,model_profile_id,plugin_tool_id,plugin_name,display_name) VALUES(?,?,?,'pending_confirmation',0,?,?,?,?,?,?,?,?)",(mapped["server_id"],mapped["name"],json.dumps(args,ensure_ascii=False),now(),run["conversation_id"],assistant_id,call["id"],profile["id"],mapped.get("plugin_tool_id"),mapped.get("plugin_name"),mapped.get("display_name")))
                        pending.append({"run_id":cur.lastrowid,"tool_name":mapped["display_name"],"mcp_tool_name":mapped["name"],"server_name":mapped["server_name"],"plugin_name":mapped.get("plugin_name"),"description":mapped["description"],"arguments":args})
                    db.execute("UPDATE messages SET tool_batch_status='completed' WHERE id=?",(run["assistant_message_id"],))
                for item in pending: yield sse("tool_waiting_confirmation",item)
                yield sse("tool_batch_waiting",{"conversation_id":run["conversation_id"],"assistant_message_id":assistant_id,"run_ids":[x["run_id"] for x in pending]})
                return
            reply="".join(reply_parts)
            with connect() as db:
                db.execute("INSERT INTO messages(conversation_id,role,content,model,created_at) VALUES(?,'assistant',?,?,?)",(run["conversation_id"],reply,profile["model_name"],now()))
                db.execute("UPDATE messages SET tool_batch_status='completed' WHERE id=?",(run["assistant_message_id"],))
                db.execute("UPDATE conversations SET updated_at=? WHERE id=?",(now(),run["conversation_id"]))
            yield sse("message_complete",{"conversation_id":run["conversation_id"],"model":profile["model_name"]})
        except Exception as exc:
            with connect() as db: db.execute("UPDATE messages SET tool_batch_status='failed' WHERE id=?",(run["assistant_message_id"],))
            yield sse("error",{"message":str(exc)[:500]})
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.post("/api/chat/regenerate")
async def regenerate_chat(payload: ChatInput) -> StreamingResponse:
    if payload.conversation_id is None:
        raise HTTPException(422,"重新生成需要已有对话")
    with connect() as db:
        last_user=db.execute("SELECT * FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",(payload.conversation_id,)).fetchone()
        if last_user is None: raise HTTPException(404,"找不到可重新生成的用户消息")
        trailing=db.execute("SELECT id FROM messages WHERE conversation_id=? AND id>?",(payload.conversation_id,last_user["id"])).fetchall()
        trailing_ids=[row["id"] for row in trailing]
        if trailing_ids:
            placeholders=','.join('?' for _ in trailing_ids)
            db.execute(f"DELETE FROM tool_runs WHERE assistant_message_id IN ({placeholders})",trailing_ids)
            db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})",trailing_ids)
        db.commit()
    payload.reuse_last_user=True; payload.message=last_user["content"]
    return await chat_stream(payload)


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatInput) -> StreamingResponse:
    if not payload.reuse_last_user and not payload.message.strip():
        raise HTTPException(422, '消息不能为空')
    with connect() as db:
        character=db.execute("SELECT * FROM characters WHERE id=?",(payload.character_id,)).fetchone()
        if character is None: raise HTTPException(404,"角色不存在")
        conversation_id=payload.conversation_id; profile_id=payload.model_profile_id
        if conversation_id is not None:
            conv=db.execute("SELECT * FROM conversations WHERE id=? AND character_id=?",(conversation_id,payload.character_id)).fetchone()
            if conv is None: raise HTTPException(404,"对话不存在")
            if profile_id is None: profile_id=conv["model_profile_id"]
        if profile_id is None: profile_id=character["default_model_id"]
        profile=db.execute("SELECT * FROM model_profiles WHERE id=? AND enabled=1",(profile_id,)).fetchone() if profile_id else None
        stamp=now()
        if conversation_id is None:
            cur=db.execute("INSERT INTO conversations(character_id,title,model_profile_id,created_at,updated_at) VALUES(?,?,?,?,?)",(payload.character_id,payload.message[:30],profile_id,stamp,stamp)); conversation_id=cur.lastrowid
        unresolved=db.execute("SELECT 1 FROM tool_runs WHERE conversation_id=? AND status IN ('pending_confirmation','running') LIMIT 1",(conversation_id,)).fetchone()
        if unresolved:
            raise HTTPException(409,"当前对话有待处理的工具调用，请先确认或拒绝，再继续聊天")
        history=db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(conversation_id,)).fetchall()
        blocks=db.execute("SELECT * FROM prompt_blocks WHERE character_id=? ORDER BY order_index,id",(payload.character_id,)).fetchall()
        memories,summary,history_limit=memory_context(db,payload.character_id,conversation_id)
        if not payload.reuse_last_user:
            db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user',?,?)",(conversation_id,payload.message,stamp))
        db.execute("UPDATE conversations SET model_profile_id=?,updated_at=? WHERE id=?",(profile_id,stamp,conversation_id)); db.commit()
        tool_defs,tool_map=available_model_tools(db) if profile else ([],{})
    async def generate() -> AsyncIterator[str]:
        used_model=profile["model_name"] if profile else payload.model
        if profile is None:
            reply=demo_reply(character["name"],payload.message)
            yield sse("message_start",{"conversation_id":conversation_id,"model":used_model,"mode":"demo"})
            for chunk in reply:
                await asyncio.sleep(0.008); yield sse("text_delta",{"text":chunk})
            with connect() as db: db.execute("INSERT INTO messages(conversation_id,role,content,model,created_at) VALUES(?,'assistant',?,?,?)",(conversation_id,reply,used_model,now()))
            yield sse("message_complete",{"conversation_id":conversation_id,"model":used_model}); return
        request_messages=build_messages(character,blocks,history,payload.message,memories,summary,history_limit)
        body={"model":profile["model_name"],"messages":request_messages,"temperature":profile["temperature"],"top_p":profile["top_p"],"max_tokens":profile["max_tokens"],"stream":True}
        if tool_defs:
            body.update({"tools":tool_defs,"tool_choice":"auto","parallel_tool_calls":False})
        yield sse("message_start",{"conversation_id":conversation_id,"model":used_model,"mode":"provider"})
        reply_parts=[]; calls={}; finish_reason=None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0,connect=15.0)) as client:
                async with client.stream("POST",model_url(profile["base_url"]),headers=auth_headers(profile),json=body) as response:
                    if response.status_code>=400:
                        detail=(await response.aread()).decode(errors="replace")[:500]; yield sse("error",{"message":f"模型服务返回 HTTP {response.status_code}：{detail}"}); return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"): continue
                        raw=line[5:].strip()
                        if raw=="[DONE]": break
                        try: data=json.loads(raw)
                        except json.JSONDecodeError: continue
                        choice=data.get("choices",[{}])[0]; delta=choice.get("delta",{}) or {}; finish_reason=choice.get("finish_reason") or finish_reason
                        text=delta.get("content") or ""
                        if text: reply_parts.append(text); yield sse("text_delta",{"text":text})
                        for part in delta.get("tool_calls",[]) or []:
                            idx=part.get("index",0); call=calls.setdefault(idx,{"id":"","type":"function","function":{"name":"","arguments":""}})
                            if part.get("id"): call["id"]=part["id"]
                            fn=part.get("function",{}) or {}; call["function"]["name"]+=fn.get("name","") or ""; call["function"]["arguments"]+=fn.get("arguments","") or ""
        except httpx.HTTPError as exc:
            yield sse("error",{"message":f"连接模型服务失败：{exc}"}); return
        reply="".join(reply_parts)
        if calls:
            parsed_calls=[]
            for index,call in sorted(calls.items()):
                function_name=call["function"]["name"]
                mapped=tool_map.get(function_name)
                if mapped is None:
                    yield sse("error",{"message":"模型请求了当前未启用的工具，已拒绝执行"}); return
                if not call.get("id"):
                    yield sse("error",{"message":"模型返回的工具请求缺少调用 ID，已拒绝执行"}); return
                try: args=json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    yield sse("error",{"message":"模型生成的工具参数不是有效 JSON，已拒绝执行"}); return
                if not isinstance(args,dict):
                    yield sse("error",{"message":"工具参数必须是 JSON 对象，已拒绝执行"}); return
                parsed_calls.append((call,mapped,args))
            with connect() as db:
                cur=db.execute("INSERT INTO messages(conversation_id,role,content,model,tool_calls_json,tool_batch_status,created_at) VALUES(?,'assistant',?,?,?,'awaiting_confirmation',?)",(conversation_id,reply,used_model,json.dumps([x[0] for x in parsed_calls],ensure_ascii=False),now()))
                assistant_id=cur.lastrowid; pending=[]
                for call,mapped,args in parsed_calls:
                    cur=db.execute("INSERT INTO tool_runs(server_id,tool_name,arguments,status,confirmed,created_at,conversation_id,assistant_message_id,provider_call_id,model_profile_id,plugin_tool_id,plugin_name,display_name) VALUES(?,?,?,'pending_confirmation',0,?,?,?,?,?,?,?,?)",(mapped["server_id"],mapped["name"],json.dumps(args,ensure_ascii=False),now(),conversation_id,assistant_id,call["id"],profile_id,mapped.get("plugin_tool_id"),mapped.get("plugin_name"),mapped.get("display_name")))
                    pending.append({"run_id":cur.lastrowid,"tool_name":mapped["display_name"],"mcp_tool_name":mapped["name"],"server_name":mapped["server_name"],"plugin_name":mapped.get("plugin_name"),"description":mapped["description"],"arguments":args})
            for item in pending: yield sse("tool_waiting_confirmation",item)
            yield sse("tool_batch_waiting",{"conversation_id":conversation_id,"assistant_message_id":assistant_id,"run_ids":[x["run_id"] for x in pending]})
            return
        with connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,model,created_at) VALUES(?,'assistant',?,?,?)",(conversation_id,reply,used_model,now()))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?",(now(),conversation_id))
        yield sse("message_complete",{"conversation_id":conversation_id,"model":used_model,"finish_reason":finish_reason})
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
