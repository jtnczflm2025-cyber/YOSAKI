# YOSAKI

<!-- chat-enhancement-current -->
> **聊天增强当前状态（2026-11-19）：已实现并加载日常。** 消息操作、版本／分支、请求详情、附件、上下文与用量统计，以及永久模型回显和单次设置刷新保留已完成。入口：[最终交接](docs/CHAT_ENHANCEMENT_HANDOFF.md)／[当前交付](docs/CHAT_ENHANCEMENT_CANDIDATE.md)／[后续清单](docs/CHAT_ENHANCEMENT_FOLLOWUP.md)。当前8000／5173已恢复为200，原进程退出原因未知；同期Voice接线保留且AI用户开关未开启。历史“仅候选／待加载”按其原版本理解；各轮测试不累计或冒充最新源码全量。Git专项分支是有明确本机基线依赖的增量交付，不是完整日常快照。
<!-- /chat-enhancement-current -->


Memory V2 M3 隔离开发状态（2026-10-29）：已提供派生版本与审核、保守清理、可追溯第一人称引述、时间投影/摘录摘要及显式本地任务。详见 [M3 能力与验收契约](docs/MEMORY_V2_M3.md) 和 [三域权威架构](docs/MEMORY_V2_ARCHITECTURE.md)。M3 路由与迁移未接入正式后端，不改变聊天上下文；不等同于自由模型精修或正式部署完成。

M3.5 已追加可选的**隔离模拟模型草稿**：保序分批、逐批请求身份、预算预留、来源校验和人工审核门槛。M3.6 的 Responses 适配器已通过零付费隔离工程验收；真实 Provider 的精修质量与费用尚未验收，正式入口仍关闭。详见 [M3.5 契约](docs/MEMORY_V2_M3.md#m35-隔离模型草稿2026-10-29)与 [M3.6 验收](docs/MEMORY_V2_M36_ZERO_PAY_ACCEPTANCE.md)。

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
.\scripts\start_yosaki_development.ps1
```

R2-A 启动入口在导入 `backend.app.main` 之前检查运行模式和实际数据库路径。当前正式日常数据库仍需 Schema 初始化，因此 Development 入口会拒绝启动，须等待 R2-C 单独批准。Preview 使用 `scripts/start_yosaki_preview.ps1`（后端 8001）及 `scripts/start_yosaki_preview_frontend.ps1`（前端 5174）；Test 使用 `scripts/start_yosaki_test.ps1` 和独立临时数据库。详情见 [运行模式说明](docs/RUNTIME_MODES.md)。不要直接运行旧的 Uvicorn 导入命令连接日常库。

`YOSAKI_DATA_DIR` 若已存在且与所选模式不符，启动入口会拒绝启动。启动脚本只在当前子进程内设置正确目录，不更改 Windows 用户或系统环境变量。

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
- Phase 6.5 adds source-aware context checks, a read-only behavior report and a local seven-day manual observation workflow. Its scripted runs verify mechanics; the real observation remains a separate daily process. See [behavior observation](docs/BEHAVIOR_OBSERVATION.md).
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

## Phase 6.5 integration and behavior observation (2026-10-27)

Backend `0.24.0` separates operation completion from goal progress (`perceivedProgress=null` without independent goal evidence), enforces a 40-edge psychology graph limit, and avoids injecting an accepted learning memory twice when it is already selected from ordinary memories. Old psychology event records retain their original rule version. The `/psychology` page and workspace window now show a read-only 1–31 day report with daily gaps, model-call timing and token evidence, resource samples, behavioral ratios and source-linked diagnostic flags; JSON and CSV exports omit raw prompts, model replies and credentials.

The ignored `data/phase65-observation/` copy was prepared from the current 阿夜 SQLite database after stopping the normal backend. It contains the existing character history but no login sessions, model/MCP secrets, active MCP permissions, backup schedule, `.secret_key` or OAuth file. To use it, run `scripts/start_phase65_observation.ps1` in PowerShell; it binds the isolated backend to `127.0.0.1` with the observation and plan-model flags. Log in and authorize the plan profile anew. The observation mode limits the Phase 2–4/6 model entrances to four calls per character per Hong Kong day. It does not count ordinary chat or legacy guided awakening, so the seven-day protocol uses only the listed entrances. See [setup, limits and metrics](docs/BEHAVIOR_OBSERVATION.md).

Thirty scripted free sessions were run through the actual local worker in an isolated database, with controlled psychological event times, three verified environment operations, a reviewed and confirmed Phase 4 feedback, and Phase 5 evidence collection. This checks mechanism continuity rather than real model choice over 30 days. The real seven-day observation still requires seven separate user-started days; no unattended wake-up is enabled. Third-party MCP, multi-process scheduling, device and public deployment checks remain separate.

The first real observation day (2026-10-27, Hong Kong) completed one manually started session on the isolated 阿夜 copy: two plan-model decisions, two verified local environment actions, one experience review and one psychology appraisal. The owner approved the proposed short-term feedback. The shared daily limit recorded 4/4 model calls, 772 trusted actual output tokens against 2,800 reserved; six real observation days remain. See the dated log in [behavior observation](docs/BEHAVIOR_OBSERVATION.md).

The second Hong Kong observation day (2026-10-28) completed one further manually started session: the model revisited the prior candidate interest, compared the local map and travel notes, then chose to finish early. It used 4/4 model calls and 827 trusted output tokens against 2,800 reserved. A drive version conflict prompted recalculation; the owner reviewed and approved the revised short-term feedback. Five real observation days remain.

## V2 local features (2026-10-23)

- New characters default to the strict 50-turn context window. Existing character rows retain their old message-count setting until changed. Chunked mode retains a stable history segment between periodic trims. The context preview shows the selected messages; provider usage is shown only when returned.
- Model profiles can select OpenAI-compatible Chat Completions or the API key-based OpenAI Responses protocol. Responses streams public reasoning summaries separately from spoken text, preserves opaque reasoning continuation data server-side for tool turns, and uses the existing per-call approval chain. The opaque state is omitted from ordinary message and archive APIs. This local adapter has only been checked against a mock endpoint.
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

## Memory authority and tool continuation guard (2026-10-28)

- Phase 5 accepted memories retain authority over their ordinary-memory projections. Ordinary edit, toggle and delete return 409 for linked projections; Phase 5 archive remains available. Invalid or stale projections are excluded before context ranking and limits across chat, Phase 2/3 and Phase 6.5.
- Tool continuation validates the conversation, assistant batch, tool call IDs, results and original user turn before changing batch state. A valid continuation uses that user message as the memory topic. Broken chains return 409 before a model request. No new database schema or Memory V2 M1 index was added.

## Memory V2 M1 read-only archive (2026-10-28)

- M1 adds explicit, rebuildable SQLite indexing for visible chat messages and safe Phase 3 operation metadata. Source text stays in the original tables; search, temporal segments, details and unfold are authenticated read-only APIs under `/api/memory-v2`.
- M1 does not call a model, rewrite text, create summaries, approve learning, or enter the existing context injection chain. Future cleaned, first-person and digest views will use ordered original source references and are reserved for M3. See `docs/MEMORY_V2_M1.md`.

## Memory V2 M2 isolated recall (2026-10-28)

- M2 adds a read-only generator interface, conservative lexical/entity/explicit candidate retrieval, live provenance and approval checks, passive-only simulated cooldown, and bounded RAW context compilation with traceable exact excerpts. Formal manual and Phase 5 approved memories remain separate from historical archive evidence.
- Shadow comparison runs only on copied source with temporary SQLite and simulated local requests; no M2 route or live request hook is registered. M2 does not call a model, execute tools, change existing injection, or implement M3 text views. Synthetic evaluation and deployment gates are in `docs/MEMORY_V2_M2.md`.
- 2026-10-28 R2.1 isolation update: a synthetic M2 regression found that deleting the actual user-turn message could attach a pending tool batch to an earlier message. Tool batches now carry a persisted original user message ID, content SHA-256 and character ID. Old unanchored pending batches reject continuation; completed history remains readable. The former failing case and 58 isolated M1/M2/Phase 1–6.5 tests now pass. M2.4 fixed synthetic evaluation is complete; formal database migration, real shadow routing and context switching remain unapproved. See `docs/MEMORY_V2_R21_M24.md`.

## Memory V2 architecture and future route (2026-10-28)

- [Architecture and M4/M5 route](docs/MEMORY_V2_ARCHITECTURE.md) separates Chronicle evidence and faithful derived views, the independent Personal Notebook, and a future Experiential Memory association view. M2 remains the read-only recall coordinator; Phase 5 retains formal learning approval. A first-person narrative is not proof of self-authored notes or a new fact authority.
- Future M4 will trace Phase 1–6.5 goals, decisions, actions, outcomes, feedback, and learning with evidence-qualified links. Temporal proximity and model suggestions do not establish causality. M5 will show these links alongside the time tree and source comparison, while honoring revocable, use-specific Notebook permissions.
- This update changes documentation only. M3 keeps its approved faithful-refinement scope; no Notebook connector, M4/M5 implementation, formal database deployment, or context switch is enabled.

## Memory V2 M3.5 isolated model drafts (2026-10-29)

- An opt-in, unregistered M3 installer supports local mock NARRATIVE and DIGEST draft jobs. Ordered source batches, persistent request IDs, source revalidation, synthetic token and cost reservations, and unknown-result refusal remain isolated from live chat. A single message may occupy a batch beyond the normal target up to hard character and token limits.
- Model output remains `needs_review` and `system_derived_text`; checked source IDs do not prove factual fidelity. Existing model-free CLEANED, quoted NARRATIVE, and extractive DIGEST remain available. No real Provider, formal database migration, or context injection is enabled. See [M3 contract](docs/MEMORY_V2_M3.md) and [M3.5 acceptance](docs/MEMORY_V2_M35_ACCEPTANCE.md).

## Memory V2 M3.6 zero-payment adapter (2026-10-29)

- M3.5 is accepted as isolated engineering; real model quality and cost remain unverified. M3.6 zero-payment engineering acceptance passed 178/178 isolated regression tests. It adds an unregistered Responses adapter, separate USD trial ledger, eight fictional gold cases and human review template. No API credential was loaded, no real Provider request was sent, and the existing chat/Phase 1–6.5 path remains unchanged. See [M3.6 contract](docs/MEMORY_V2_M36.md) and [zero-payment acceptance](docs/MEMORY_V2_M36_ZERO_PAY_ACCEPTANCE.md).
- A future paid pilot still requires separate approval of the exact model, test key, full synthetic set and proposed USD 5 cap. M4 experiential linking can proceed through its own review independently of M3.6 live model activation.
- The proposed first live quality check is separately limited to synthetic N1, two serial generations, and a USD 0.10 recommended cap; see the [review-only execution checklist](docs/MEMORY_V2_M36_FIRST_TRIAL.md). No live input count or generation request has been made.

## Memory V2 N1 trial and dialogue chronicle correction (2026-10-31)

- After separate authorization, synthetic N1 completed two real `gpt-6.1-sol` Responses generations in an external isolated copy, with two required input counts and an estimated USD 0.005830 generation cost. The M3.5 output remains unreviewed and unavailable for formal recall; see [N1 results](docs/MEMORY_V2_M36_FIRST_TRIAL.md). The older “no live request” note above describes the prior preparation state.
- NARRATIVE now targets 阿夜第一人称的高保真对话纪事. The current v3 prompt uses only “我说／她说” for attribution; the generated text rejects mixed language attribution. RAW evidence remains unchanged, while model dialogue bodies omit machine source headers. Ordered source IDs and fingerprints remain in mapping metadata, and all drafts still require human fidelity review. A detailed dialogue is not judged primarily by compression. Existing DIGEST time summaries remain separate; a versioned title, keywords and per-experience synopsis are not yet implemented. No further paid request, formal database migration, context switch or M4/M5 work occurred in this correction.

## Memory V2 P1-A isolated experience entries (2026-10-31)

- P1-A adds an unregistered, source-bound entry contract with stable IDs, versioned title, keywords, tags, per-experience synopsis, and a reference to an already approved NARRATIVE version. It keeps RAW and the detailed narrative in their existing authorities. Dates are derived from live sources; edits, lost access, and narrative replacement invalidate the affected entry read.
- Human review, selective dependency tokens, and staged split/merge lineage are tested with synthetic messages and temporary SQLite. The earlier “not yet implemented” statement describes the state before P1-A. No production schema migration, context switch, model call, vector API, or M5 UI is enabled. See [the 28-feature parity ledger](docs/MEMORY_V2_FEATURE_PARITY.md).

## Memory V2 P2 isolated Chronicle projection (2026-10-31)

- P2 adds an unregistered, read-only L0–L5 time tree and `context_projection` for already selected Chronicle entries. The shared view checks current M1 sources, P1-A metadata and M3 review/dependencies on every read. Valid L1–L4 DIGEST versions require an explicit source-scope binding; missing or stale summaries fall back to an approved entry synopsis or title. Full RAW remains available through source-bound pages at every age.
- P2 neither retrieves candidates nor injects the tree into chat. It does not call a model, vector service or external tool, create summaries on GET, or change the Phase 1–6.5 path. The installable browse API is not registered in `main.py`; formal database migration and M5-A UI remain separate decisions. See [P2 contract](docs/MEMORY_V2_P2.md) and [the 28-feature ledger](docs/MEMORY_V2_FEATURE_PARITY.md).
- Isolated P2 tests passed 23/23; the final backend regression passed 224/224. The current code has not been checked against a live database, real provider or browser UI.

## Memory V2 M5-A0 isolated read-only calendar (2026-11-01)

- An optional `/chronicle` Vue page now connects to an independent synthetic SQLite API. It browses the L0–L5 tree, reviewed period DIGEST and per-experience metadata, switches approved NARRATIVE/CLEANED/RAW, compares RAW beside the chronicle, and pages long sources. Keyword/date search, previous/next, missing/stale/draft warnings, and light/dark mode work in the isolated browser demo.
- Set `VITE_YOSAKI_CHRONICLE_A0=1` only in the test frontend and run `scripts/chronicle_a0_demo.py` for a temporary localhost database. `main.py` does not install the new read-only routes. No formal database, chat context, model, vector or Phase 1–6.5 behavior is changed. See [M5-A0 runbook and limits](docs/MEMORY_V2_M5A0.md) and [the 28-feature ledger](docs/MEMORY_V2_FEATURE_PARITY.md).
- P2 still uses a period DIGEST only when M2 has selected all of its members. Future P3/P4 may let M2 explicitly select a period candidate after relevance, permissions and budget checks; P2 must not expand the selected source set by itself.
- M5-A0 API tests passed 6/6; the full isolated backend suite passed 230/230. Both flag-on and flag-off frontend builds passed. Browser checks covered L0–L5, digest and RAW paging, source-change 409 clearing, light/dark mode and a 390×844 viewport. These are synthetic isolated results, not formal deployment acceptance.

## Memory V2 M5-A1 isolated calendar reading and projection preview (2026-11-07)

- The same optional `/chronicle` page now has a collapsible year/month/period hierarchy, ISO-week date ranges, and a diagnostics area. Reviewed rule-based period DIGEST is explicitly labeled extractive; its source IDs, roles and states sit in expandable metadata rather than the main reading text. RAW/NARRATIVE comparison, paging and light/dark styling remain.
- Explicitly selected synthetic experiences can be previewed at a chosen observation time and estimated Token budget. The unregistered read-only endpoint calls P2 `context_projection()` directly and reports the chosen version, current source qualification, fallback and rejection reasons, and cross-view deduplication. It neither selects the whole archive nor sends memory to the formal chat model.
- See [M5-A1 isolated contract and screenshots](docs/MEMORY_V2_M5A1.md) and the [28-feature ledger](docs/MEMORY_V2_FEATURE_PARITY.md). The formal database, M2/P4 injection path, models, tools and Phase 1–6.5 remain untouched.
- A1 targeted tests passed 17/17 (including inherited baseline cases); full isolated backend regression passed 247/247. Python compilation and both calendar-flag-on/off frontend builds passed. Browser checks covered the year/month/week hierarchy, source metadata disclosure, period preview, and light/dark presentation with synthetic data.

## Memory V2 H1–H4 synthetic historical import pipeline (2026-11-07)

- An installable, isolated Chronicle import API and optional calendar wizard now preview YOSAKI JSON v1/v2, strict Markdown/TXT and bounded ZIP files; Markdown/TXT are marked lossy. RAW import is explicit, batched at 100 messages per transaction, resumable and receipt-backed. Imported `archive_only` conversations are rejected by live chat/list, regenerate, tool and legacy export paths. Date-only and unverified local times remain imprecise; import time is never presented as event time.
- After RAW, the owner may explicitly index safe messages, revise 30-minute boundary suggestions, and create stable P1-A draft entries. Unorganized RAW is browsable without a reviewed NARRATIVE; drafts do not become formal memories or bypass existing review. The wizard remains isolated; `main.py` does not install the import routes or run the import migration.
- See [historical import contract and capacity gate](docs/MEMORY_V2_HISTORICAL_IMPORT.md), [consumer audit](docs/MEMORY_V2_IMPORT_AUDIT.md), and [28-feature ledger](docs/MEMORY_V2_FEATURE_PARITY.md). The V1 64 MiB `.ybackup` limit motivated H5-A; V2 remains blocked from formal use by the H5-A1 target-environment gate. No private history, formal database or paid model was used in this increment.
- Final synthetic verification: external source-copy import tests 14/14, full temporary-DB backend regression 261/261, both Chronicle flag builds, and a localhost browser import through two stable draft entries. The 5,000-message benchmark and known limits are recorded in the import contract.

## Memory V2 H5-A isolated large-backup work (2026-11-07)

- V2 adds a default-off encrypted backup path, bounded-memory file processing, and independent restoration of V1/V2 files into a new directory. The old upload route now refuses to replace the active database. `YOSAKI_H5A_ENABLED=1` is for isolated validation only; formal migration and H5-B private-history testing remain separate approvals.
- The V2 file uses SQLite online backup, redaction only in the snapshot, and whole-file AES-256-GCM authentication. Browser V2 download uses a native file download; upload verifies an isolated copy. With H5-A enabled, local scheduled backup uses V2, while files above 64 MiB are not uploaded through the GitHub Contents API. See [H5-A format and recovery contract](docs/H5A_LARGE_BACKUP.md).

### H5-A1 target recovery gate

- Frozen-source isolated regression passed 273/273, including the final temporary-directory marker change. Synthetic restore under a new data directory and new key read ordinary business data; credential fields remained excluded. The H5-A1 review found remaining formal activation gates for existing work-directory ACLs, API quotas/timeouts and multi-worker tickets, 16 GiB capacity, and same-process feedback-worker resume. See [H5-A1 validation and H5-B boundary](docs/H5A1_TARGET_RECOVERY_GATE.md). No formal database restore or private-history import was performed.

### H5-A2 isolated admission hardening

- H5 V2 now refuses broad work-directory ACLs and unverified encryption, checks upload size before multipart parsing, reserves combined temporary space, serializes heavy backup operations, and uses a single-worker process lock. Download tickets moved to one-use POST forms; running prepare/verify can be cancelled and have deadlines. The browser falls back to V1 only when V2 is explicitly disabled. The format's 16 GiB limit is theoretical; the temporary 1 GiB operational cap and target encrypted volume remain unaccepted for formal use. See [H5-A2 gate and test record](docs/H5A2_FORMAL_GATE.md). H5-B private-history files remain out of scope.

### H5-B first-file isolation and M1 order repair

- The authorized Chronicle Markdown adapter preserved the specified historical file's 130 messages, original ordinal and local timestamp text. H2 RAW import took place only in a retained isolated database. H3.1 boundaries were confirmed. The first H3.2 attempt stopped on M1 segment ordering; after the isolated order repair, a **new work database made from the pre-H3.2 snapshot** was explicitly migrated and rebuilt, then 13 progressive P1-A2 drafts were created from the confirmed ranges. Titles remain system suggestions; the drafts have no synopsis, narrative, review or formal recall eligibility. The original H2 database and snapshots remain untouched. See [H5-B report](docs/H5B_REAL_SAMPLE_REPORT.md).
- M1's isolated order repair validates a completed H2 file's full receipt manifest and orders its messages by integer ordinal within file and conversation scope. It preserves native-message and activity ordering, keeps search relevance separate, signs pagination cursors, and requires explicit migration and rebuild of old M1 indexes. The repair is not a formal database migration or H3.2 completion. See [M1 contract](docs/MEMORY_V2_M1.md).
- Final synthetic isolated verification: M1 order tests 7/7, P1-A2 metadata tests 22/22, full backend regression 327/327, and frontend type check/build passed. The real H5-B database and snapshots were not opened during this repair.
# P1-A2 隔离增量（2026-11-08）

草稿列表的撤权分页问题已完成定向修复；最终源码专项 26/26、完整后端隔离回归 320/320、前端类型检查与构建通过。P1-A2 仅完成隔离工程验收，尚未正式启用；详见契约文档的复现和修复记录。

渐进条目支持仅来源与标题的草稿、独立字段修订、角色词库、显式加工任务及元数据候选适配。契约和验收边界见 [P1-A2](docs/MEMORY_V2_P1A2.md)。正式 main.py 不注册新 API；H5-B 真实 RAW 和快照保持原状。个人策展、真实模型、正式迁移和上下文接入继续另行审批。
# Voice V1-A（2026-11-08）

已加入默认关闭的 ElevenLabs 声音档案基础设施：角色 Voice ID、声音人格与配置版本、首次第三方处理告知和撤销、后端密钥状态、隔离快照剔除确认记录，以及保留原设备语音的设置界面。`YOSAKI_VOICE_ENABLED=1` 只应在隔离数据目录验证；正式库迁移另行批准。真实 ElevenLabs API 测试（V1-B）尚未授权，应用中的测试端点明确拒绝真实请求，诊断脚本默认为零请求 dry-run。V2 消息朗读、V4 实时通话均尚未实现。完整阶段边界见 `docs/VOICE_SYSTEM_PLAN.md`。


R2-C 准备更新（2026-11-10）：`YOSAKI_BACKGROUND_TASKS_ENABLED=0` 在任务创建前关闭反馈、心理状态、定时备份的自动 startup 轮询；未设置默认 1 保持既有入口兼容，非法值拒绝。Development 专用入口强制设置进程内值 0；生命周期及 shutdown 保留。此开关不禁止用户显式业务 API。专项 4/4、完整隔离后端 349/349 通过；正式日常库初始化与服务切换尚未执行。执行与回退顺序见 `docs/R2C_EXECUTION_CHECKLIST.md`。

### Work 本机交接探针（2026-11-14）
仅供无模型本机连通性验证，尚未接入桌面会话，说明见 [探针记录](scripts/work_handoff_probe/README.md)。



### Memory V2 当前收口依据

统一执行与C1～C10实际状态见 [MEMORY_V2_EXECUTION_PLAN](docs/MEMORY_V2_EXECUTION_PLAN.md)。原始规则和前后样例见 [MEMORY_ENTRY_ORIGINAL_RULES](docs/requirements/MEMORY_ENTRY_ORIGINAL_RULES.txt)；这是本地需求材料，不能自动作为公开夹具或外发。梗概与完整精修对话独立。第三轮已受控加载 http://127.0.0.1:5173/chronicle ，付费加工关闭；真实梗概/周期/向量/聊天验收仍有缺口，不等于C1～C10全部完成。

## 2026-11-18～19 聊天增强隔离候选

P1～P4 的请求记录、保留候选的重生成、消息修订／分支、引用／翻译、附件及可选上下文预算已在外部候选实现。默认保留旧角色策略，自动摘要与网页关闭，套餐输出硬上限未核验。当前未迁移日常库或切换服务。

请从 `docs/CHAT_ENHANCEMENT_CANDIDATE.md` 查看逐项行为与证据，从 `docs/CHAT_ENHANCEMENT_LOAD_CHECKLIST.md` 查看实际加载和真实合成验证清单。原有 Memory、Voice 和备份准入继续适用。
