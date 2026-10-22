# YOSAKI

YOSAKI is currently a local development prototype. It is **not ready for public internet/VPS exposure**.

## Start frontend (PowerShell)

```powershell
Set-Location "D:\AI xiangmu\自建前端"
npm install
npm run dev
```

Open the `Local` URL printed by Vite (usually `http://localhost:5173/`). If port 5173 is occupied, Vite may select 5174.

## Start backend in a second PowerShell window

Requires Python 3.11 or newer. This machine's `python`/`py` launcher was not functional in our test, so install/fix Python first and then run:

```powershell
Set-Location "D:\AI xiangmu\自建前端"
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend initializes `data/yosaki.sqlite3` and seeds 阿夜. Check `http://127.0.0.1:8000/health`. The Vite development server proxies `/api` to this backend.

For an isolated instance, set `YOSAKI_DATA_DIR` to an absolute directory before starting the backend. Both `yosaki.sqlite3` and `.secret_key` will be created there. With the variable unset, the existing project `data/` directory remains the default.

## Local acceptance checks

From the project root on Windows, run `npm run test:backend`, `npm run build`, and `.\.venv\Scripts\python.exe -m py_compile backend\app\main.py`. The backend suite uses temporary data directories and a local mock model/MCP server; it needs no real API keys or external endpoint. Passing these checks does not establish real provider, device, Docker, or VPS readiness.

## First-run account setup and security

On the first run, YOSAKI opens a one-time account setup screen. Choose a username and a password of at least 12 characters; there is no built-in/default password. Only one owner account can be created. Passwords are stored as PBKDF2-HMAC-SHA256 hashes, not plaintext. API access requires a server-side session sent as an HttpOnly, SameSite=Strict cookie; logout revokes that session. Login attempts are rate-limited.

For HTTPS behind a correctly configured reverse proxy, set `YOSAKI_COOKIE_SECURE=1` in the backend environment so cookies are marked Secure. Keep `data/.secret_key` and `data/yosaki.sqlite3` private and backed up. Do not expose this dev server directly to the public internet; HTTPS, proxy/firewall configuration, and a deployment review are still required.

## Current implementation status

- Responsive welcome/workspace shell, draggable feature windows, character editing, and chat history.
- FastAPI + SQLite persistence for the single owner account and sessions, characters, prompt blocks, conversations, messages, memories, summaries, model profiles, MCP services/tools, tool runs, and controlled plugin manifests.
- OpenAI-compatible streamed chat with optional MCP function tools. Tool calls pause for explicit chat approval; approve/reject and continuation are persisted.
- Model, MCP, and plugin secrets are encrypted server-side and are never stored in browser localStorage.
- Demo replies remain available when no model profile is selected. No live third-party model or MCP endpoint was tested in the latest tool-loop/plugin stage.

## Still pending

URL/ZIP plugin packaging, legacy SSE MCP transport, provider-backed TTS/STT adapters, automated backups, and production VPS deployment/hardening. Recursive tool batches during continuation and stop/regenerate are implemented; real provider/MCP compatibility still needs verification. Authentication exists for local development; do not expose the development service publicly until HTTPS, reverse proxy, firewall, secure cookie configuration, and deployment hardening are reviewed.

## Character editing update (2026-10-23)

- Character fields now show whether they differ from the last saved version. Successful saves clear the unsaved indicator; failed saves retain it.
- Switching characters, creating/importing/duplicating a character, and logging out ask before discarding unsaved character fields. Cancel keeps the current draft. Reloading or closing the browser tab also triggers the browser's unsaved changes warning.
- Verified in an isolated local database through the browser: edit indicator, cancel/confirm switching, and successful save. Prompt blocks still use their existing per-field save behavior.

## Local acceptance and MCP repair (2026-10-23)

- Added optional `YOSAKI_DATA_DIR` for isolated data and secret storage, plus `npm run test:backend` with temporary SQLite databases and loopback mock services.
- The tests exposed and led to fixes for the missing MCP JSON/SSE response parser and missing MCP tool enable/disable API. Tool execution still requires an explicit chat decision.
- Local mocked checks are repeatable; real provider/MCP compatibility, device checks, and deployment validation remain open.
- Browser check against a temporary backend and local mock MCP confirmed connect, tool refresh, and tool enable feedback. The temporary backend, mock service, and database were removed afterward.

## Product shell direction

YOSAKI 的主页面不是聊天窗口，而是：

```text
欢迎页 → 导航工作台 → 聊天 / 角色卡 / 模型 / 记忆 / MCP / 插件 / 语音 / 设置
```

桌面端功能页面可以作为浮动窗口打开，支持关闭、最小化、放大、拖动、缩放和多窗口层级；功能路由也可作为全页工作区打开，手机端优先使用全页模式。真实设备验收仍待完成。完整更新计划见 `docs/PLAN.md`。

## Implemented workspace shell (2026-10-13)

The frontend now starts on `/` with a welcome screen and enters `/hub` on button click. The hub offers navigation cards for chat, character cards, models, memory, MCP/tools, plugins, voice and settings. Chat and character editing run inside floating windows; other feature cards show explicit development placeholders. Windows support focus, drag, minimize, maximize, close and native resize, with layout state in `localStorage` (`YOSAKI.window-layout.v1`). This is a UI foundation, not a completed model/MCP/voice implementation.
## Model profiles and provider boundary (2026-10-13)

- Added SQLite `model_profiles` with OpenAI-compatible base URL, model parameters and encrypted API key storage using Fernet. Keys are never returned by API responses; only `api_key_configured` is exposed.
- Added `/api/models` CRUD plus `/api/models/{id}/test`. Model test makes a real non-streaming provider request. Chat uses the selected/default profile and streams OpenAI-compatible `data:` SSE deltas; when no profile is configured it retains explicit demo mode.
- Frontend model window now edits/saves profiles, tests real connections, and chat windows can switch among saved profiles. The model window remains a draggable/resizable workspace window.
- Local backend environment is now reproducible with `uv` and `.venv`; do not commit `data/.secret_key`, API keys, or `data/yosaki.sqlite3`.

## Prompt blocks and context assembly (2026-10-13)

- Added editable prompt-block APIs under `/api/characters/{id}/prompt-blocks` and `/api/prompt-blocks/{id}`, including reorder support.
- Character window now lists, edits, saves and creates prompt blocks with injection positions such as `system_start`, `character_profile`, `memory` and `system_end`.
- Provider-backed chat assembles the character system prompt plus enabled prompt blocks in stable injection order before conversation history. Demo mode remains available when no model profile is selected.

## Memory and context controls (2026-10-14)

- Added character-scoped long-term memories and conversation-scoped temporary memories. Original messages remain unchanged.
- Added memory search, enable/disable, edit/delete APIs and UI controls.
- Added per-character history-limit and memory-enabled settings.
- Added manual conversation-summary storage; summaries are optional reference material and do not replace raw history.
- Added context preview token estimates using an explicit character heuristic. These are estimates only, not provider billing tokens or cache-hit measurements.
- Provider chat now includes enabled memories, optional summaries, and only the configured number of recent history messages.
## MCP connector foundation (2026-10-14)

- Added encrypted MCP server configuration and redacted server listing APIs.
- Added Streamable HTTP `initialize`, `tools/list`, and `tools/call` JSON-RPC handling with session-header retention. Legacy SSE can be stored but returns an explicit unsupported-adapter message instead of pretending to connect.
- Added tool catalog persistence and execution audit records.
- Tool execution is fail-closed: `/api/mcp/tool-call` returns HTTP 428 until the caller supplies `confirmed: true`.
- Added a draggable MCP configuration/tool window for endpoint, transport, auth, connection test, and tool refresh. This is a connector foundation; the chat model has not yet been given autonomous tool schemas or an in-chat confirmation loop.

## Chat-triggered MCP tool loop (2026-10-14)

- Enabled connected, explicitly enabled MCP tools as OpenAI-compatible function tools in model requests.
- Model tool-call deltas are persisted and shown in chat as confirmation cards with server, tool, description, and arguments.
- Every model-originated call pauses before execution. The user can approve or reject each call; approved arguments are validated against the MCP tool's advertised JSON Schema. Rejections and execution errors are returned to the model as tool results.
- After all calls in a batch are resolved, the user explicitly resumes the model. Tool-call/result messages and pending decisions are persisted and shown after chat reload.
- A new user turn is blocked while tool calls are awaiting decisions. MCP-discovered tools now default disabled and must be enabled in MCP settings before being exposed to a model.
- Scope limitation: a single confirmation batch can include multiple calls; after their results, the continuation is a normal text turn (no recursive second tool batch during that continuation yet). Live third-party MCP and provider testing still requires the user's configured service and model; local end-to-end tests used mocked services.

## Chat tool-confirmation loop (2026-10-14)

- Connected/enabled MCP tools are exposed to the selected OpenAI-compatible model as function tools.
- Streamed tool-call requests are saved in the conversation and displayed with server/tool identity, description, and arguments.
- MCP-discovered tools default disabled. The user must enable each tool, then separately confirm every model-requested execution in chat.
- Rejected calls are returned to the model as tool results; approved calls execute server-side after JSON Schema validation. Results/errors are persisted and the user explicitly resumes the model.
- A conversation with unresolved tool approvals blocks new user turns. Pending tool cards and resolved outcomes are reconstructed from SQLite after reload.
- Continuation currently completes one tool batch and requests a normal text reply; recursive tool batches within that continuation are not yet enabled.
- Provider/MCP round-trip tests use mocks. No live remote provider or MCP endpoint has been exercised in this stage.
## Controlled plugin tool packs (2026-10-14)

- Added local JSON manifest import, checksum, install/enable/disable/uninstall, and per-tool grants.
- Plugin tools can only bind to an already discovered MCP server/tool. The importer forbids extra fields and packages cannot include executable entrypoints, frontend code, Python, or shell commands.
- Newly installed plugins and their tools default off. Enabling a plugin does not enable its tools, and enabled tools still go through the per-call chat confirmation flow.
- When a tool is packaged by a plugin, the raw MCP listing is suppressed for model discovery so there is one managed route. Uninstall removes only plugin metadata/bindings; it does not remove MCP servers, underlying MCP tools, chats, or messages.
## Browser voice baseline (2026-10-14)

- Added a voice settings window for browser/system TTS voice selection, language, playback rate, test speech and stop controls.
- Assistant messages have a local speak control. The composer voice button uses browser SpeechRecognition when available and inserts recognized text into the draft without auto-sending.
- SpeechRecognition requests local/on-device processing when the browser reports it available. Otherwise an in-app disclosure requires the user to choose whether to use the browser recognition service; the app does not start remote recognition by default.
- No audio file is stored or sent through the YOSAKI backend in this baseline. Provider-backed cloud/local model adapters, credentials and live device/microphone tests remain future work.


## Browser speech baseline (2026-10-14)

- The voice window uses browser/system speech synthesis for reading assistant replies, with locale, available voice, and playback-rate preferences stored in this browser.
- Composer voice input uses SpeechRecognition when available and places recognized text in the draft; it never auto-sends.
- YOSAKI prefers browser-reported on-device recognition. If that is unavailable, it displays a consent dialog before starting the browser's recognition service because audio may be handled by the browser/platform vendor.
- No audio file is saved or sent to the YOSAKI backend in this baseline. Provider-backed TTS/STT adapters and real microphone/device QA are still pending.

## Single-user authentication (2026-10-14)

- Added one-time owner setup, login, logout, and session status endpoints.
- All application API endpoints except health and auth entrypoints require a valid server-side session. Session IDs are random, stored as SHA-256 digests in SQLite, revoked on logout, and delivered in HttpOnly/SameSite=Strict cookies.
- Passwords use PBKDF2-HMAC-SHA256 (600,000 iterations); setup enforces a 12-character minimum; login attempts are rate-limited per client address.
- Frontend presents a first-run setup or login screen and a workspace logout action. User credentials and session tokens are not persisted in localStorage.
- Set `YOSAKI_COOKIE_SECURE=1` when serving through HTTPS. This is not yet a production deployment/security audit.
## Backup and restore (2026-10-14)

- Added authenticated JSON backup export/import in the System Settings window.
- Export includes characters, prompts, chats, messages, memories, summaries, model metadata, MCP metadata, and plugin bindings.
- Password hashes, sessions, model API keys, MCP secrets, and active MCP sessions are excluded. After restore, secrets must be entered again.
- Restore requires an explicit confirmation and replaces the application workspace data while keeping the owner account/session intact. Invalid or incomplete backups roll back through a SQLite transaction.
- Backup is a local development feature; VPS deployment still needs scheduled off-machine backups and a restore rehearsal.

## Encrypted backup/restore (2026-10-14)

- System Settings exports a full SQLite snapshot as an encrypted `.ybackup` file. The user supplies a backup passphrase (minimum 12 characters); YOSAKI does not store it.
- AES-backed Fernet encryption key is derived from that passphrase with PBKDF2-HMAC-SHA256 and a random per-backup salt. Restore rejects wrong passwords and invalid SQLite archives before replacing the live DB.
- Export removes active session/login-attempt records, model API keys, MCP secrets, and MCP session IDs. Restore also strips those fields defensively, atomically replaces the SQLite file, and requires the owner to log in again.
- Local backup export/import and wrong-password/replace/session-invalidation tests passed. Off-machine scheduled backup and production restore rehearsal remain future work.

## Encrypted full backup update (2026-10-14)

- Replaced the previous plaintext JSON data export with an encrypted `.ybackup` full SQLite snapshot. A user-entered passphrase (minimum 12 chars) derives an AES/Fernet key via PBKDF2-HMAC-SHA256 with per-file random salt; the passphrase is not stored.
- Restore validates the passphrase, archive members, SQLite signature/integrity, and required tables in memory before writing a temporary database and atomically replacing the database file.
- Export/restore strip active sessions, login-attempt state, model API keys, MCP secrets, and MCP session IDs. After restore, login is required again and provider secrets must be re-entered.
- The Settings UI exports/downloads binary encrypted backups and imports only `.ybackup` files with a typed passphrase plus explicit replace confirmation.
- Isolated authenticated export/restore, wrong password, confirmation required, data replacement, and session invalidation tests passed.

## Production deployment scaffold (2026-10-14)

- Added `deploy/Dockerfile.backend`, `deploy/Dockerfile.frontend`, `deploy/docker-compose.production.yml`, Nginx routing, and `.env.production.example`.
- Added `deploy/DEPLOY_VPS.md` with HTTPS reverse-proxy, firewall, persistent volume, backup, health-check, update and rollback requirements.
- Docker is not installed on this development machine, so container image builds are not locally verified. Frontend build and backend compile remain verified with the local toolchain.
- The deployment compose file binds its HTTP entrypoint to localhost and expects host-level HTTPS termination; it does not claim that a VPS, domain, certificate, or provider/MCP endpoint is configured.
## Production deployment scaffold (2026-10-14)

- Added deploy/Dockerfile.backend for FastAPI and deploy/Dockerfile.frontend for the compiled Vue app served by Nginx.
- Added deploy/docker-compose.production.yml with a persistent backend data volume and localhost-only HTTP binding.
- Added deploy/.env.production.example and deploy/DEPLOY_VPS.md covering HTTPS termination, secure cookies, origin allow-list, firewall, backup/restore, health checks and rollback.
- Added deploy/.dockerignore so local secrets, databases, virtual environments and node modules are not sent into builds.
- Docker is not installed on the development machine, so image builds and VPS reachability are not verified. Local frontend build and backend compile pass.
## Recursive MCP tool batches (2026-10-15)

- Tool continuation now supports another model-generated tool batch after a tool result, instead of forcing a final text-only reply.
- Each recursive batch creates a new persisted assistant tool-call message and new per-call confirmations. The existing approval, rejection, JSON Schema validation, pending-turn lock, and plugin/MCP routing rules still apply.
- Local mocked end-to-end test passed: model tool request → approval → second model tool request → approval → final text reply. Live provider/MCP recursive behavior remains unverified.

## Chat controls: stop and regenerate (2026-10-15)

- Added an abortable frontend stream controller and a Stop button that cancels the current fetch while preserving already received text as an interrupted partial reply.
- Added `POST /api/chat/regenerate`: removes the trailing assistant/tool-result turn from the selected conversation, reuses the last user message, and generates a replacement without duplicating the user message.
- Empty new messages are rejected server-side, while regenerate requests can use the internal empty payload safely.
- Local isolated regenerate test passed; frontend build and backend compile pass. Live provider stream cancellation still depends on the provider honoring the closed HTTP stream.

## Chat history management (2026-10-16)

- Added conversation rename (`PATCH /api/conversations/{id}`), with a trimmed title and updated timestamp.
- Chat history strip now supports search by title, date display, switching conversations, double-click/pen rename, and confirmed deletion.
- Switching a conversation reloads persisted messages, tool cards and memory context; deleting removes its messages, tool runs and summary through the existing cascade.
- Local frontend build, backend compile, and browser verification of history rendering and switching passed.
## Conversation export (2026-10-16)

- Added per-conversation JSON and Markdown export through `GET /api/conversations/{id}/export?format=json|markdown`.
- Export includes role messages, timestamps, model names, tool-call metadata, tool results, plugin labels and tool statuses; it does not export API/MCP secrets.
- Chat history chips now show compact JSON/Markdown export actions; the download uses a safe generated filename.
- Conversation rename, search, switch, export and confirmed delete are all available in the floating chat window.
## Conversation export and history controls (2026-10-16)

- Added authenticated JSON and Markdown conversation export with messages, model names, tool-call metadata, tool results, plugin labels, timestamps and statuses.
- Added conversation search, rename, switch, date display, compact export actions and confirmed deletion in the floating chat window.
- Export does not include model API keys, MCP secrets, password hashes or sessions.
- Local API tests and frontend build pass; browser verification covered rendering, history listing and switching.
## Multi-character management (2026-10-20)

- Added character creation, duplication, switching, partial profile update and confirmed deletion.
- Character duplication copies the profile and prompt blocks only; it deliberately excludes conversations, tool runs, memories and summaries.
- Character deletion explicitly clears that character's conversations/messages/tool runs, prompt blocks, memories and context settings while retaining at least one role by recreating 阿夜 if the last role is removed.
- Character list now includes enabled/disabled roles so local management state is not silently hidden.
- Local isolated API tests and frontend build pass; browser UI changes are visible in the character window.
## Multi-character management (2026-10-21)

- Added role-management controls to the Character window: create, duplicate, switch, set a per-character default model, and delete with a second confirmation.
- Character duplication copies the profile and prompt blocks only; it does not copy chats, memories, summaries or tool runs.
- Character deletion explicitly purges that role's conversations, messages, tool runs, memories, summaries and context settings, and the backend refuses to delete the last remaining role.
- Character list now includes enabled/disabled roles and character updates are safely partial so editing one field does not erase the others.
- Local isolated role CRUD/duplicate/delete tests and frontend build pass. The browser surface is ready for real-user interaction verification.
## Character card portability (2026-10-21)

- Character window can export/import `yosaki-character-card` JSON v1.
- Import always creates a new role and requires user confirmation; server validates strict fields/version and prompt-block positions.
- Cards include role fields and prompt blocks only; chats, memories, summaries, model bindings, credentials and sessions are excluded.
- Local round-trip and rejection tests passed; browser shows export/import controls. Real local file selection was not executed during QA.
## Workspace routes and layout recovery (2026-10-21)

- Added full-page workspace mode for `/chat`, `/characters`, `/models`, `/memory`, `/mcp`, `/plugins`, `/voice` and `/settings`, reusing the existing feature surfaces; `/hub?window=...` remains the floating-window route.
- Floating-window headers now offer “作为独立页面打开”; page mode has a clear “返回工作台” control and browser back/forward restores the prior route and window mode.
- Window focus/open/close stays URL-synchronized. `YOSAKI.last-route` and `YOSAKI.last-open-windows` persist with the existing layout key; restored layouts are validated, de-duplicated and clamped to the viewport, and CSS-resized dimensions are captured on pointer release.
- Verified with `npm run build`, backend `py_compile`, `/health`, direct-route browser checks, mode switching and browser-history restoration. Viewport QA additionally covered 390×844 responsive chat and character pages with no horizontal overflow; physical-device and assistive-technology QA remain outstanding.
