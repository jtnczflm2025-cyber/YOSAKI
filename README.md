# YOSAKI

<!-- chat-enhancement-current -->
> **聊天增强当前状态（2026-11-19）：已实现并加载日常。** 消息操作、版本／分支、请求详情、附件、上下文与用量统计，以及永久模型回显和单次设置刷新保留已完成。入口：[最终交接](docs/CHAT_ENHANCEMENT_HANDOFF.md)／[当前交付](docs/CHAT_ENHANCEMENT_CANDIDATE.md)／[后续清单](docs/CHAT_ENHANCEMENT_FOLLOWUP.md)。当前8000／5173已恢复为200，原进程退出原因未知；同期Voice接线保留且AI用户开关未开启。历史“仅候选／待加载”按其原版本理解；各轮测试不累计或冒充最新源码全量。Git专项分支是有明确本机基线依赖的增量交付，不是完整日常快照。
<!-- /chat-enhancement-current -->


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

From the project root on Windows, run `npm run test:backend`, `npm run build`, and `.\.venv\Scripts\python.exe -m compileall -q backend\app`. The backend suite uses temporary data directories and local mock services; it needs no real API keys or external endpoint. Passing these checks does not establish real provider, device, Docker, or VPS readiness.

## First-run account setup and security

On the first run, YOSAKI opens a one-time account setup screen. Choose a username and a password of at least 12 characters; there is no built-in/default password. Only one owner account can be created. Passwords are stored as PBKDF2-HMAC-SHA256 hashes, not plaintext. API access requires a server-side session sent as an HttpOnly, SameSite=Strict cookie; logout revokes that session. Login attempts are rate-limited.

For HTTPS behind a correctly configured reverse proxy, set `YOSAKI_COOKIE_SECURE=1` in the backend environment so cookies are marked Secure. Keep `data/.secret_key` and `data/yosaki.sqlite3` private and backed up. Do not expose this dev server directly to the public internet; HTTPS, proxy/firewall configuration, and a deployment review are still required.

## Current implementation status

- Responsive welcome/workspace shell, draggable feature windows, character editing, and chat history.
- FastAPI + SQLite persistence for the single owner account and sessions, characters, prompt blocks, conversations, messages, memories, summaries, model profiles, MCP services/tools, tool runs, and controlled plugin manifests.
- OpenAI-compatible streamed chat with optional MCP function tools. Tool calls pause for explicit chat approval; approve/reject and continuation are persisted. Compatible providers can stream `reasoning_content` and receive an optional `reasoning_effort` setting; role monologue is separately stored and never sent back as history.
- Per-character strict or chunked turn windows, batch conversation archives, and optional encrypted local/GitHub backup plans.
- Per-character opt-in simulated drives, manual awakening, bounded model/tool activity, confirmed feedback, and reviewable preference changes. See [V3 desire system](docs/DESIRES.md).
- Separate read-only state advice, simulated introspection, and persistent candidate interests, pending intentions, and long-term commitments. See [volition Phase 2](docs/VOLITION.md).
- Manually started, backend-run free activity with a persistent local exploration scene, bounded budgets, per-operation permission checks, and cross-awakening experience history. See [autonomous activity Phase 3](docs/AUTONOMY.md).
- Phase 4 feedback for free activity: verified operation evidence, optional simulated review, pre-action enjoyment expectations, reviewable short-term drive changes, and candidate interest evidence. See [feedback and expectation gap](docs/FEEDBACK.md).
- Phase 5 long-term learning: confirmed free-activity feedback becomes traceable evidence; memory, preference, strategy, and goal continuity changes are reviewed before entering future context. See [long-term learning](docs/LEARNING.md).
- Phase 6 simulated psychology: evidence-linked emotion, virtual body load, reflective desires, bounded relations and conflict history. These are references for volition, never execution permissions. See [psychological state](docs/PSYCHOLOGY.md).
- Model, MCP, and plugin secrets are encrypted server-side and are never stored in browser localStorage. The optional ChatGPT OAuth record is encrypted in the backend data directory, outside SQLite backups.
- Demo replies remain available when no model profile is selected. No live third-party model or MCP endpoint was tested in the latest tool-loop/plugin stage.

## Still pending

URL/ZIP plugin packaging, legacy SSE MCP transport, provider-backed TTS/STT adapters, and production VPS deployment/hardening. Recursive tool batches during continuation and stop/regenerate are implemented; broader real provider/MCP/Responses compatibility and real GitHub backup credentials still need verification. A local ChatGPT account sign-in and one model stream were tested on 2026-10-23; this does not verify other accounts or models. Authentication exists for local development; do not expose the development service publicly until HTTPS, reverse proxy, firewall, secure cookie configuration, and deployment hardening are reviewed.

## V3 desire system (2026-10-24)

Open **欲望与意愿** at `/desires` or in a floating workspace window. It starts disabled for every character. Once enabled, twelve simulated drives can receive explicit events and confirmed activity feedback; ordinary chat only reads a short relevant advisory. Debug simulation never writes the character's state. State snapshots can be saved and restored.

Manual awakening uses a selected configured model profile. Each session has a deadline and model/tool call limits; it never starts on a schedule or resumes execution automatically after a server restart. The model returns a validated public decision summary and may choose to rest, defer, reject, draft an activity, or request an allowed MCP tool. Tools marked and selected as read-only by the owner can execute within that session; all other requests wait for an individual decision. YOSAKI cannot verify arbitrary third-party MCP side effects, so only mark a tool read-only after checking it yourself. Chat tool approval remains unchanged. Positive repeated feedback may create a small baseline change suggestion; the owner must apply it and may roll it back.

V3 was checked with isolated API tests and a temporary browser instance, including enabling a character, viewing all drives, isolated simulation, explicit event recording, a mocked model/tool awakening cycle, policy revocation and preference rollback. Real autonomous provider/MCP behavior, physical devices and production deployment remain unverified.

## Desire system Phase 2 (2026-10-24)

In **欲望与意愿**, the new observation and pure decision buttons use the selected model profile once per request. They save public, simulated descriptions and decisions; they do not execute activities or call MCP tools. Candidate interests, deferred intentions, and explicit long-term commitments persist across awakenings. Deferred intentions have no automatic deadline, urgency growth, or failure state. The existing manual awakening remains separate and receives these intentions as context. The backend version is `0.19.0`. See [Phase 2 interfaces and migration](docs/VOLITION.md).

## Desire system Phase 3 (2026-10-24)

In **欲望与意愿 → 自主活动**, select a model, the local exploration scene, a real-time window, model/action call limits, a cumulative output token limit, and any MCP tools allowed for this session. Then click **开始自由活动**. The backend proceeds step by step while it remains running, even if the page closes. There is no scheduled wake-up. A backend restart pauses the session; an in-flight operation with no confirmed result becomes unknown and is never replayed automatically. Pausing does not extend the deadline.

The local scene stores inspected objects and discoveries per character. Future environments use the `EnvironmentAdapter` interface; the first scene is example simulation content, not a claim about real geography. Activity plans, actual operation results, simulated remarks, and unresolved interests remain distinguishable and available to later awakenings. A finished session does not automatically complete a lasting goal. MCP calls still require the same explicit tool selection and, unless a verified read-only policy applies, per-call confirmation. New pending feedback events do not change drives until a later confirmed feedback flow. See [Phase 3 APIs, migration and recovery](docs/AUTONOMY.md). Backend version: `0.20.0`.

## Desire system Phase 4 (2026-10-26)

Free activity now reserves an optional separate experience-review call when it starts. The character may record a nullable enjoyment expectation before an action. After the session, the backend groups actual results by plan and generates a short simulated review when authorized. The feedback panel shows evidence, expectation gap, calculated short-term drive changes and interest clues. None of these changes affects formal drives until the owner reviews and applies them. A confirmed feedback transaction is idempotent; stale drive versions require a fresh review of the recalculated proposal. Long-term baseline and goals are not changed by this pipeline. Old guided awakening and chat feedback retain their previous flows. See [Phase 4 rules and API](docs/FEEDBACK.md). Backend version: `0.21.0`.

## Desire system Phase 5 (2026-10-27)

The new `/learning` page and `hub?window=learning` panel show traceable activity evidence, reviewable memories and preferences, a preference history chart, conditional strategies, and goal progress references. Phase 4 confirmation collects evidence without a model call; complex consolidation is a separate manual, budgeted model request. Formal long-term learning enters future volition and free-activity context only after review. It never edits character prompts or drive baselines, and it never starts an activity. See [Phase 5 design and API](docs/LEARNING.md). Backend version: `0.22.0`.

Isolated backend regression passed 22 tests, with targeted Phase 4/5 checks after the latest changes. Python compilation and frontend production build passed. An isolated browser session verified preference approval, manual correction, source-linked history, rollback, and both page and floating-window modes. Real model providers, third-party MCP, devices, Docker and VPS remain unverified.

## Desire system Phase 6 (2026-10-27)

The new `/psychology` page and `hub?window=psychology` panel show simulated emotion, virtual energy and load, lasting reflective tensions, bounded directed relations, conflict decisions and event sources. Completed operations update virtual load once; applied Phase 4 feedback updates emotion using a validated appraisal or deterministic fallback. At free activity start, a separate Phase 6 allowance reserves at most one appraisal model call and 128–1200 output tokens. The estimate is a cap, not a dollar price. No psychological field can execute tools, alter a drive baseline or approve long-term learning. See [Phase 6 rules, API and migration](docs/PSYCHOLOGY.md). Backend version: `0.23.0`. Local acceptance: 25 isolated backend tests, Python compilation, frontend build and isolated browser full-page/window checks passed; real provider, MCP, device and deployment checks remain separate.

Isolated validation passed 20 backend tests, Python compilation and the frontend production build. A temporary browser instance completed a mock free activity, displayed `70 → 90` as a `+20` expectation gap, applied feedback only after confirmation, and retained the record after switching to the full page and refreshing. Real provider/MCP, device and VPS acceptance remain separate.

### Live ChatGPT plan check for the desire system — 2026-10-27

The previously authorized local `gpt-6.1-sol` profile was tested with a temporary SQLite database and role. The desire system's shared model entry now consumes the required streamed Responses events; it waits for `response.completed`, reads output text deltas, and caps output with the selected profile. Schema-guided output is used for structured decisions. Live observation and pure volition both passed validation. A bounded free session chose a local environment action, received a successful result, and recorded one psychological operation event. A confirmed feedback record fed a completed manual Phase 5 consolidation; a separate Phase 6 appraisal completed. One Phase 4 review validly returned no subjective evaluation, so that run made no invented drive change. Temporary test data was discarded; the existing authorization was only read or refreshed. This check does not establish third-party MCP, other models, devices, or deployment compatibility. [Official plan inference guidance](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) requires `store=false`, `stream=true` and a terminal completion event.

## V2 local features (2026-10-23)

- New characters default to the strict 50-turn context window. Existing character rows retain their old message-count setting until changed. Chunked mode retains a stable history segment between periodic trims. The context preview shows the selected messages; provider usage is shown only when returned.
- Model profiles can select OpenAI-compatible Chat Completions or the API Key based OpenAI Responses protocol. Responses streams public reasoning summaries separately from spoken text, preserves opaque reasoning continuation data server-side for tool turns, and uses the existing per-call approval chain. The opaque state is omitted from ordinary message and archive APIs. This local adapter has only been checked against a mock endpoint.
- Chat exports support versioned JSON or readable Markdown/TXT in a ZIP, date/turn filters, display names, multiple text replacements, paragraph and time controls, and selectable code/tool/reasoning/monologue output. Templates stay in browser localStorage. Import previews each file and adds conversations to the chosen character; duplicate file hashes are skipped. JSON preserves tool run details as inert history cards with no approval actions. Imports limit each file to 8 MiB, each batch to 64 MiB, and each file to 20,000 messages; Markdown/TXT require the default role markers shown in [chat archive examples](docs/CHAT_ARCHIVES.md).
- Settings can configure a local sync directory and a separate private GitHub repository for encrypted `.ybackup` files. Scheduled runs default off; when enabled they default to 03:00 Asia/Hong_Kong, keeping seven visible files. Set `YOSAKI_BACKUP_PASSWORD` (12+ characters) and, for GitHub, `YOSAKI_BACKUP_GITHUB_TOKEN` in the backend environment. Keep both out of source control. The GitHub token needs access to repository metadata and Contents write permission on the dedicated repository. Transient GitHub upload errors receive at most three attempts; a failed upload leaves a successful local backup in place. Retention runs only after upload succeeds and reports a separate cleanup failure state. Restore downloads a remote file for the existing confirmed import flow. Deleting old GitHub files does not erase Git history.
- ChatGPT plan usage has an opt-in local OAuth and direct Responses adapter with encrypted backend credential storage, scope checks, token refresh/revocation, a model catalog and the existing tool confirmation chain. A real account granted the plan scope and completed a model test and chat stream on this development machine on 2026-10-23. The separate API Key Responses profile does not consume ChatGPT Plus allowance.
- Local validation uses `npm run test:backend`, `npm run build`, and backend compilation. An isolated browser run selected, previewed, and imported the example JSON file, checked the cleanup preview and the model/backup controls. The real database and Fernet key hashes were unchanged.

## ChatGPT plan connection trial

The [official preview](https://developers.openai.com/siwc/token-sharing-open-source) describes plan usage for eligible open-source, locally hosted applications and accounts. This repository is publicly visible but has no license at the owner's request. The account used in the 2026-10-23 local test obtained the plan scope and a completed response; this does not establish general eligibility or production support. The feature is disabled by default and must be used from a local YOSAKI instance; do not enable it on a public/VPS host.

Set `YOSAKI_CHATGPT_PLAN_ENABLED=1` in the backend environment, restart the backend, then open **模型配置 → ChatGPT 套餐接入 → Continue with ChatGPT**. The system browser handles account authorization, returns to a temporary `127.0.0.1` callback, and YOSAKI lists models only after the required plan scope is granted. Add a listed model and use **测试真实连接** before chatting. This action requires a real ChatGPT account and consent; local mock tests do not establish eligibility or Plus billing behavior. If the account is ineligible or reaches its limit, YOSAKI reports the failure and does not silently use a paid API Key.

OAuth tokens are encrypted in `YOSAKI_DATA_DIR/.chatgpt_plan` (or `data/.chatgpt_plan` by default). Keep this file and `.secret_key` private. It is excluded from `.ybackup`, Git, and Docker build context. Disconnect attempts remote revocation and removes local tokens. Authorization waits up to 60 minutes for its loopback callback; if it expires, start a new attempt from model settings. The feature follows the [official sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in) and [Responses limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) documented for this preview.

The 2026-10-23 live check used the account's visible `gpt-6.1-sol` model: **测试真实连接** returned `OK`, and a new chat completed with `YOSAKI 已连接。` and provider usage of 116 input, 11 output and 0 cached tokens. This verifies the local OAuth and streamed Responses path for that account at that time; it does not verify other models, tools, devices, billing records, or VPS deployment. The first authorization attempt returned after the former 10-minute callback window and failed to connect. The callback window is now 60 minutes, and the success page removes the one-time code from its visible URL.

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
