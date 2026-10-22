# AGENTS.md — YOSAKI 项目协作指南

> 本文件记录当前项目的结构、运行方式、功能边界、安全约束和验证状态，供后续开发、调试、审查和部署使用。
> 适用目录：`D:\AI xiangmu\自建前端`
> 最近核对：2026-10-23（Asia/Hong_Kong）。项目整体核对范围见第 11、13 节；本日增量见第 15 节。

## 1. 项目定位与当前状态

- 项目名称：**YOSAKI**。
- 产品定位：本地优先、单用户的个人 AI 工作台，围绕角色「阿夜」组织聊天、角色卡、模型、提示词与记忆、MCP/工具、插件、语音和系统设置。
- 当前性质：**本地开发原型 / MVP**。认证、加密存储和部署骨架已经存在，但还没有完成生产安全审计、真实 VPS 验收或所有第三方服务联调。
- 主产品路径：
  ```text
  欢迎页 / → 导航工作台 /hub → 可组合的浮动功能窗口
  ```
- 当前后端版本：`0.17.0`（`backend/app/main.py` 中的 FastAPI `version`）；前端 `package.json` 版本为 `0.1.0`，两者不代表同一发布版本。
- 默认种子角色：阿夜（夜色里的共振者）。首次初始化数据库时会创建阿夜、提示词块和基础上下文设置。
- 产品原则：保留中文界面、阿夜的自主性、用户逐次选择和状态/可能性展示；不要在没有产品授权时把工作台强行改成单一路径聊天页。

### 当前已落地的重点能力

- 欢迎页、工作台导航和桌面端浮动窗口。
- 单用户初始化、登录、登出、服务端 session 和登录失败限流。
- 角色卡的多角色创建、复制、切换、局部编辑、默认模型和删除。
- 角色资料的已保存/未保存状态提示；切换角色或离开前可确认放弃草稿。
- `yosaki-character-card` JSON v1 角色卡导出/导入。
- 对话历史搜索、切换、重命名、删除以及 JSON/Markdown 导出。
- 演示模式聊天、OpenAI-compatible 流式聊天、停止生成和重新生成。
- MCP 工具发现、启停、JSON Schema 参数校验、逐次确认、工具结果回传和递归工具批次。
- 受控 JSON 插件清单：只能绑定已经发现的 MCP 工具，不执行插件代码。
- 记忆、摘要、上下文设置和上下文预览。
- 浏览器/系统 TTS，以及带远程识别明确同意步骤的 STT。
- 加密 `.ybackup` 全量 SQLite 快照导出/导入。
- Docker/Nginx/VPS 部署骨架。

## 2. 目录结构

```text
自建前端/
├─ backend/
│  ├─ app/main.py                 # FastAPI 单文件后端、SQLite、认证和全部业务 API
│  └─ requirements.txt            # Python 后端依赖
├─ frontend/
│  ├─ index.html                  # Vue 入口 HTML
│  ├─ package.json                # 前端依赖和脚本
│  ├─ vite.config.ts              # Vite、开发端口、/api 代理
│  ├─ tsconfig.json               # Vue + TypeScript 配置
│  └─ src/
│     ├─ App.vue                  # 当前主要前端源代码，状态、逻辑和模板集中在此
│     ├─ App.vue.js               # vue-tsc 生成/辅助产物，不应手工编辑
│     ├─ main.ts                  # 实际入口，挂载 App.vue 和 style.css
│     ├─ main.js                  # 生成/备用 JS 入口，当前不是 index.html 的源入口
│     └─ style.css                # 当前产品的主要手写 CSS
├─ data/
│  ├─ yosaki.sqlite3              # 本地运行数据库，敏感运行数据
│  ├─ .secret_key                 # 本地 Fernet 密钥，敏感文件
│  ├─ test_encrypted_backup.sqlite3# 旧的本地备份测试产物；新测试不用它
│  └─ .gitkeep
├─ deploy/
│  ├─ Dockerfile.backend          # FastAPI 镜像
│  ├─ Dockerfile.frontend         # Vue 构建 + Nginx 静态服务镜像
│  ├─ docker-compose.production.yml# backend/frontend 生产骨架
│  ├─ nginx/default.conf          # /api、/health 反向代理和 SPA fallback
│  ├─ .env.production.example     # 生产变量示例
│  ├─ .dockerignore               # 构建时排除密钥、数据库、依赖等
│  └─ DEPLOY_VPS.md               # HTTPS、防火墙、备份、升级和回滚要求
├─ docs/
│  ├─ PLAN.md                     # 产品入口、窗口模型和阶段计划
│  ├─ PLUGINS.md                  # 受控插件工具包 v1 说明
│  └─ CHARACTER_CARDS.md          # 角色卡 JSON v1 格式与导入导出边界
├─ plugins/                       # 当前插件相关目录，具体可执行能力仍不开放
├─ tests/test_local_acceptance.py # 隔离 API 与模拟模型/MCP 验收
├─ README.md                      # 本地运行、产品状态和迭代记录
├─ AGENTS.md                      # 本文件
├─ package.json                   # 根级 npm 脚本和 frontend workspace
├─ package-lock.json              # npm 锁文件
└─ .gitignore                     # 依赖、构建产物、虚拟环境、数据库/密钥忽略规则
```

### 文件与工作区注意事项

- 当前目录在本次检查时**不是 Git 仓库**；不要假设可以使用 `git status`、分支、提交或回滚命令。
- `tests/` 提供隔离后端验收测试，根级 `npm run test:backend` 可运行；`backend/__pycache__/` 下的旧字节码不是测试源码。
- `node_modules/`、`.venv/`、`__pycache__/`、`frontend/dist/`、`frontend/tsconfig.tsbuildinfo` 是依赖或生成物，应保持忽略。
- `data/yosaki.sqlite3`、`data/.secret_key`、`.env.production`、API Key、MCP 密钥、session 和备份密码都属于敏感材料；不要提交、复制到日志或写入前端存储。
- 当前前端仍是大体量单文件组件；修改功能时优先定位并修改 `frontend/src/App.vue`，不要直接改 `App.vue.js`。
- `README.md` 同时包含按日期追加的实现记录；如果顶部概览与最新日期记录冲突，以当前代码和最新记录为准，并在后续维护中修正文档漂移。

## 3. 技术栈与运行方式

### 前端

- Vue 3 + Vite + TypeScript。
- 图标：`lucide-vue-next`。
- Markdown：`marked`；内容清理：`dompurify`。
- CSS：`frontend/src/style.css` 手写样式；当前没有 Tailwind/shadcn 运行依赖，不要引入全局 reset 或替换现有主题而不做视觉验证。
- 开发端口通常为 `5173`；`frontend/vite.config.ts` 将 `/api` 代理到 `http://127.0.0.1:8000`。
- dev/preview 使用 `--host 0.0.0.0`，意味着可能具备局域网暴露能力，但不代表已经完成 HTTPS、CORS、防火墙和公网安全配置。

### 后端

- FastAPI + Uvicorn + SQLite，推荐 Python 3.12，最低按 README 约定使用 Python 3.11+。
- 依赖：`fastapi`、`uvicorn[standard]`、`httpx`、`cryptography`、`jsonschema`、`python-multipart`。
- 数据库路径由 `backend/app/main.py` 计算为项目根目录下的 `data/yosaki.sqlite3`。
- 设置 `YOSAKI_DATA_DIR` 时，SQLite 和 `.secret_key` 一起改用该目录；未设置时仍使用项目 `data/`。本地测试必须先设置隔离目录再导入后端模块。
- 后端启动：
  ```powershell
  Set-Location "D:\AI xiangmu\自建前端"
  uv venv .venv --python 3.12
  uv pip install --python .venv\Scripts\python.exe -r backend\requirements.txt
  .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
  ```
- 前端启动：
  ```powershell
  Set-Location "D:\AI xiangmu\自建前端"
  npm install
  npm run dev
  ```
- 根级脚本：
  - `npm run dev`：转发到 `frontend` 的 Vite dev server。
  - `npm run build`：执行 `vue-tsc -b && vite build`。
  - `npm run preview`：运行前端预览服务。
- 健康检查：`GET http://127.0.0.1:8000/health`。

## 4. 前端架构与交互状态

### 入口和认证

- `frontend/src/App.vue` 根据 `window.location.pathname` 区分主要的 `welcome` 与 `hub` 视图；当前主要实际路径是 `/` 和 `/hub`。
- `/chat`、`/characters`、`/models`、`/memory`、`/mcp`、`/plugins`、`/voice`、`/settings` 已支持全页工作区模式，并与 `/hub?window=<kind>` 浮动窗口模式共用同一功能界面；它们尚未拆分为独立页面组件。
- 路由由 App.vue 读取 `location.pathname`、query 和 History API 管理，没有引入 Vue Router；未知路径目前落入欢迎视图。Nginx 的 SPA fallback 只保证能加载入口文件。
- 首次进入调用认证状态接口：
  - 没有 owner：显示一次性账户初始化。
  - 有 owner 但未登录：显示登录。
  - 已登录：允许进入欢迎页和工作台。
- 退出登录后，后端撤销 session，前端清空窗口和消息状态。

### 工作台和窗口

- `features` 包含 8 个入口：聊天、角色卡、模型配置、提示词与记忆、MCP 与工具、插件、语音、系统设置。
- `WindowInstance` 字段包括 `id`、`kind`、`title`、`x`、`y`、`width`、`height`、`zIndex`、`minimized`、`maximized`。
- 窗口操作集中在 App.vue：同类窗口复用、聚焦、关闭、最小化、最大化、拖动和布局恢复。
- 布局写入 `localStorage`：`YOSAKI.window-layout.v1`；语音偏好写入 `YOSAKI.voice.locale`、`YOSAKI.voice.rate`、`YOSAKI.voice.voiceURI`。
- 路由/恢复键还包括 `YOSAKI.last-route`、`YOSAKI.last-open-windows`；活动角色 ID 保存于 `YOSAKI.active-character`。这些都是当前浏览器的 UI 偏好，不是跨设备同步状态。
- 不要把 API Key、MCP secret、密码或 session 写进 localStorage。
- 拖动和高亮应保持稳定坐标；使用边框、背景、阴影、动画等方式表达状态，不要用 `transform: scale()` 破坏窗口位置。

### 当前已实现的窗口功能

- **聊天**：角色/模型选择、对话历史、搜索、重命名、切换、删除、JSON/Markdown 导出、Markdown 渲染与清理、SSE 流式输出、演示回复、停止生成、重新生成、工具确认卡片和工具后续继续。
- **角色卡**：多角色列表、创建、复制、切换、删除、启用/停用、局部编辑、默认模型、提示词块添加/删除/启用/排序、角色卡 JSON 导出和导入。
- 角色资料字段有未保存状态跟踪；提示词块仍沿用字段变更时分别保存。
- **模型配置**：模型档案 CRUD、OpenAI-compatible Base URL、模型名、temperature、max tokens 等参数；API Key 只提交后端；真实非流式连接测试；聊天中切换模型。
- **提示词与记忆**：长期/临时记忆、搜索、启用/停用、编辑/删除、历史条数、记忆开关、摘要和上下文预览。
- **MCP 与工具**：保存服务、认证方式、连接、刷新工具、查看 schema、工具启停和确认策略。
- **插件**：导入 JSON 清单、启停插件、逐工具授权、卸载；单文件前端上限 256 KB。
- **语音**：浏览器/系统 `speechSynthesis` TTS；STT 优先本机识别，不可用时先弹出远程浏览器识别同意框。
- **系统设置**：布局恢复、加密 `.ybackup` 导出和导入。

## 5. 后端架构和数据模型

- `backend/app/main.py` 是约 1800 行的单文件 FastAPI 应用；数据库初始化、兼容迁移、业务逻辑和 API 路由集中在同一文件。
- SQLite 连接通过全局 `RLock` 串行化，并启用外键；`init_db()` 会创建缺失表并为旧数据库补列。
- 当前主要表：
  - `users`、`auth_sessions`、`auth_login_attempts`
  - `characters`、`prompt_blocks`
  - `conversations`、`messages`、`conversation_summaries`
  - `memories`、`context_settings`
  - `model_profiles`
  - `mcp_servers`、`mcp_tools`
  - `plugins`、`plugin_tools`
  - `tool_runs`
- 角色删除必须清理关联的对话、消息、工具运行、记忆、摘要、提示词块和上下文设置；后端拒绝删除最后一个角色。
- 角色复制只复制角色资料和提示词块，不复制聊天、记忆、摘要、工具运行或模型绑定。
- `init_db()` 在模块导入时执行；未设置 `YOSAKI_DATA_DIR` 就导入后端模块或启动 Uvicorn 会初始化/迁移真实数据库。测试必须先设置隔离目录，不能直接拿 `data/yosaki.sqlite3` 做恢复或清空试验。

## 6. 关键 API 分组

| 分组 | 主要接口 | 用途 |
|---|---|---|
| 健康/认证 | `/health`、`/api/auth/status`、`/api/auth/session`、`/api/auth/setup`、`/api/auth/login`、`/api/auth/logout` | 健康检查、一次性 owner 创建、登录、会话和登出 |
| 角色 | `/api/characters`、`/api/characters/{id}` | 角色 CRUD、局部更新、复制/删除相关操作 |
| 角色卡 | `/api/characters/{id}/export`、`/api/characters/import` | `yosaki-character-card` JSON v1 导出/导入 |
| 提示词 | `/api/characters/{id}/prompt-blocks`、`/api/prompt-blocks/{id}` | 提示词块 CRUD、排序和注入位置 |
| 模型 | `/api/models`、`/api/models/{id}/test` | 模型档案 CRUD 和真实连接测试 |
| 对话 | `/api/conversations`、`/api/conversations/{id}`、`/messages`、`/export` | 对话列表、重命名、消息、删除和 JSON/Markdown 导出 |
| 聊天 | `/api/chat/stream`、`/api/chat/regenerate` | 演示/供应商流式聊天和重新生成 |
| 记忆/上下文 | `/api/memories*`、`/api/characters/{id}/context-settings`、`/api/conversations/{id}/summary`、`/api/chat/context-preview` | 记忆、摘要、历史限制和上下文预览 |
| MCP | `/api/mcp/servers*`、`/api/mcp/servers/{id}/tools`、`/api/mcp/tools/{id}` | 服务保存、连接、刷新、工具目录和工具启停 |
| 插件 | `/api/plugins*` | 清单校验、安装、启停、逐工具授权和卸载 |
| 工具确认 | `/api/chat/tool-runs*` | 单次工具批准/拒绝、结果写入和模型继续 |
| 备份 | `/api/system/backup/export`、`/api/system/backup/import` | 加密 `.ybackup` 导出/恢复 |

## 7. 认证、密钥和备份安全边界

### 认证

- `/health` 和认证入口不要求登录；其它应用 API 由 HTTP middleware 要求有效的服务端 session。
- 仅允许一个 owner：SQLite 唯一表达式索引 `one_yosaki_owner` 限制 `users` 只有一行。
- 密码使用 PBKDF2-HMAC-SHA256、600,000 次迭代和随机 salt；初始设置至少 12 字符。
- session 原 token 不入库，只保存 SHA-256 摘要；原 token 通过 HttpOnly、SameSite=Strict cookie 返回。
- session 默认有效期为 7 天；`Secure` 属性仅在 `YOSAKI_COOKIE_SECURE=1` 时启用。登录失败按客户端地址在 15 分钟窗口内累计，达到 5 次会临时锁定。
- 中间件对带有 `Origin` 的非读取类 `/api/` 请求检查 `YOSAKI_ALLOWED_ORIGINS`，并用服务端 session 保护业务 API；这不能替代部署侧 HTTPS、来源和代理配置。不要在日志、API 响应、普通导出或前端暴露密码、token 或原始密钥。

### 模型和 MCP 密钥

- 模型 API Key 和 MCP auth secret 使用 Fernet 加密后保存。
- API 响应只返回 `api_key_configured` 或 `auth_configured`，不返回明文密钥。
- `data/.secret_key` 或环境变量 `YOSAKI_SECRET_KEY` 用于 Fernet；生产部署必须使用稳定且私密的 key。
- 不要把密钥写入 localStorage、角色卡、对话导出、普通 JSON 备份或日志。

### `.ybackup` 加密备份

- 备份密码至少 12 字符，由用户输入且不由 YOSAKI 保存。
- 使用 PBKDF2-HMAC-SHA256 + 每文件随机 salt 派生 Fernet key。
- 备份保留 owner 账户及其密码哈希；导出/恢复剔除活动 session、登录尝试、模型 API Key、MCP secret 和 MCP session ID，恢复后需用原账户重新登录并重新配置服务密钥。不要把“全量快照”理解为包含密钥。
- 数据库序列化上限为 64 MiB；导入会替换当前数据库文件，即使有格式、完整性和确认检查，也应先留存并校验当前数据的独立备份。
- 恢复前在内存校验密码、归档成员、SQLite integrity check 和必需表，然后写入临时文件并原子替换数据库。
- 这是本地备份能力，不等于已经完成异地定时备份和生产恢复演练。

## 8. 聊天、MCP 和插件执行链路

### 模型聊天

1. `/api/chat/stream` 验证角色、对话和可选模型档案。
2. 没有启用模型档案时使用 `YOSAKI · demo` 演示回复，并把结果写入 SQLite。
3. 有模型档案时，按角色系统提示、启用的提示词块、记忆、摘要和历史限制组装上下文。
4. 向 OpenAI-compatible `/chat/completions` 发起请求，解析 `data:` SSE 文本增量。
5. 前端可以停止 fetch；已收到的部分回复保留为中断内容。`/api/chat/regenerate` 会删除最后一个助手/工具结果回合，并复用最后一条用户消息，不重复插入用户消息。

### MCP 工具确认

1. 只有已连接、已启用且需要确认的 MCP 工具会作为模型 function tools 暴露。
2. 模型工具调用增量会持久化到 `messages` 和 `tool_runs`。
3. 前端显示服务器、工具、描述和参数确认卡片。
4. 每个调用必须由用户批准或拒绝；批准前按 MCP 工具提供的 JSON Schema 校验参数。
5. 所有同批调用处理完成后，用户显式继续模型；继续过程中允许模型提出新的工具批次。
6. 待确认或执行中的工具会锁住当前对话的新用户回合。
7. `/api/mcp/tool-call` 是故意停用的直接执行入口，不能绕过聊天确认流程。

### 受控插件

- v1 插件是 JSON manifest，不是 ZIP、URL 市场或可执行代码包。
- 导入不会运行 JavaScript、Python、shell、entrypoint 或前端扩展；只能绑定已经发现的 MCP 工具。
- 插件和插件工具默认关闭/需授权；工具执行仍需聊天内逐次确认。
- manifest 使用 Pydantic `extra="forbid"`；服务端验证 server/tool 引用并计算 SHA-256 checksum。
- 单文件最大 256 KB；同一插件 ID 更新采用卸载后重新导入，不覆盖已有安装。
- 插件安装时插件和插件工具都写为停用。模型工具目录只纳入已连接、已启用且要求确认的 MCP 工具；已被插件引用的源工具通过插件授权链暴露，不按独立原始工具重复暴露。

## 9. 角色卡与对话导出边界

### 角色卡 `yosaki-character-card` v1

- 包含：角色名称、简介、心情、系统提示、人设开场白、启用状态和提示词块。
- 不包含：聊天/消息、工具运行、记忆、摘要、模型绑定、API Key、MCP 凭证、账户和 session。
- 导入永远创建新角色，不覆盖已有角色；用户界面要求明确确认。
- 只接受 JSON；前端文件上限 256 KiB；后端拒绝未知字段、错误版本和不支持的提示词注入位置。
- 角色卡是未加密的可移植文本，不能把密钥或敏感信息写进角色提示词。

### 对话导出

- 支持 JSON 和 Markdown。
- 包含消息、时间、模型名称、工具调用元数据、工具结果、插件标签和工具状态。
- 不包含模型 API Key、MCP secret、密码 hash 或 session。

## 10. 语音和部署边界

### 语音

- TTS 使用浏览器/操作系统的 `speechSynthesis`，不保存音频，也不经 YOSAKI 后端发送。
- STT 优先尝试本机识别；本机不可用时必须先展示远程浏览器识别的隐私说明和同意操作。
- 当前没有 provider-backed TTS/STT 适配器，也没有真实麦克风、移动端权限和设备 QA。

### 部署

- `deploy/docker-compose.production.yml` 启动 backend + frontend，命名卷保存 `/app/data`。
- 前端镜像构建 Vue 后由 Nginx 提供静态文件；`/api/` 和 `/health` 反代到 backend；其它路径回退 `index.html`。
- compose 只把 HTTP 入口绑定到 `127.0.0.1:8080`；外层必须配置 HTTPS 反向代理，不能直接暴露 backend 8000。
- 生产变量重点：`YOSAKI_ALLOWED_ORIGINS`、`YOSAKI_COOKIE_SECURE=1`、稳定的 `YOSAKI_SECRET_KEY`。
- 当前开发机没有 Docker；镜像构建、VPS 可达性、域名、证书、防火墙和外部 provider/MCP 健康状态均未在这里证明。

## 11. 当前未完成事项与验证边界

以下事项仍不能宣称已经完成：

- 真实第三方模型和 MCP endpoint 的完整联调，以及不同 provider 的兼容性覆盖。
- 上下文预览中的 token 数是字符启发式估算，缓存状态没有服务商数据支撑；不能据此报告真实计费 token 或缓存命中率。
- legacy SSE MCP 的完整适配；当前可保存该 transport，但不应把部分兼容逻辑当作全部通过。
- provider-backed TTS/STT、真实麦克风、移动端和跨浏览器 QA。
- URL/ZIP 插件包装、插件市场和可执行插件。
- 自动异地备份、定时备份、生产恢复演练和部署加固。
- Docker 镜像实际构建、VPS 迁移、HTTPS、反向代理和防火墙验收。
- 独立页面组件拆分；当前 `/chat` 等路径使用共享功能面板的全页模式，另保留 `/hub` + 浮动窗口。
- 角色卡导入的真实浏览器文件选择流程尚未在 QA 中执行；API round-trip/rejection 已有本地测试记录。
- `tests/` 和 `npm run test:backend` 覆盖隔离本地 API 与模拟服务，不能等同于真实第三方服务、设备或部署验收。
- `README.md` 顶部 “Still pending” 是旧阶段文字，仍将已实现的递归工具批次和停止/重新生成列为待办；判断实现状态以当前代码及较新的日期记录为准，维护 README 时应一并修正。

必须区分以下验证层级：

```text
代码存在
→ 类型检查/编译
→ 前端构建
→ 后端启动/health
→ 本地 HTTP/API 测试
→ 浏览器交互验证
→ 真实模型/MCP/provider 验证
→ 真实设备验证
→ VPS/生产部署验证
```

不要把 `vue-tsc`、`vite build` 或 Python 编译通过写成“系统已生产可用”。

## 12. 开发约定与修改风险

1. **先读实现再修改**：后端是单文件，前端是单个大组件；先定位对应状态、模板、API 和数据库操作，避免重复逻辑。
2. **优先修改源文件**：改 `frontend/src/App.vue` 和 `frontend/src/main.ts`；不要手工维护 `App.vue.js`、`main.js` 等生成产物。
3. **保护密钥边界**：API Key、MCP secret、session token、密码和备份密码不得进入 localStorage、普通导出、响应体或日志。
4. **遵守人类确认在环**：不要绕过 MCP 工具确认，不要让插件变成任意代码执行器，不要自动批准工具调用。
5. **保持数据库兼容**：修改 schema 时同步考虑 `init_db()` 的旧库补列和现有 `data/yosaki.sqlite3`；不要未经扫描、备份和验证删除运行数据。
6. **维护角色/对话隔离**：复制角色不得复制聊天和记忆；删除角色必须清理关联数据且保留至少一个角色。
7. **保持中文和产品交互意图**：不要把“用户可选择的可能性”改成强制推荐路径；新增确认、删除、远程语音等高风险操作时必须有明确说明。
8. **前端验证**：至少运行 `npm run build`；涉及认证、SSE、窗口、角色卡文件、语音或历史操作时，要补浏览器或 HTTP 验证。
9. **后端验证**：至少运行 Python 编译检查和 `/health`；涉及 auth/backup/model/MCP/tool-loop 时覆盖成功、拒绝、错误和持久化路径。
10. **文档同步**：新增功能要同步 `README.md`、相关 `docs/*.md` 和本文件，避免顶部状态与最新迭代记录冲突。
11. **不要默认使用破坏性命令**：清理、迁移、恢复或替换数据库前先扫描、dry-run、备份和验证；不要使用强制依赖升级作为默认修复。

## 13. 推荐快速检查清单

```powershell
Set-Location "D:\AI xiangmu\自建前端"

# 前端类型检查与生产构建
npm run build

# 后端语法检查
.\.venv\Scripts\python.exe -m py_compile backend\app\main.py

# 隔离后端验收（临时 SQLite + 本地模拟服务）
npm run test:backend

# 后端运行后健康检查（另一个窗口）
Invoke-RestMethod http://127.0.0.1:8000/health

# 仅在确认目录已有 .git 后再使用 Git 检查
Test-Path .git
```

2026-10-22 本次文档核对运行 `npm run build`（`vue-tsc -b` 与 Vite 均通过）和 `.venv\Scripts\python.exe -m py_compile backend\app\main.py`（通过）。检查时 `127.0.0.1:8000` 没有运行服务，因此本次没有取得 `/health` 响应，也未重做浏览器、业务 API、真实 provider/MCP、设备或 VPS 验证。2026-10-21 的既有本地 HTTP/浏览器验证记录见下节与 README；不要把它们记为本次重新验证。

## 14. Recent implementation note — 2026-10-21

- The shared workspace supports both full-page feature routes (`/chat`, `/characters`, `/models`, `/memory`, `/mcp`, `/plugins`, `/voice`, `/settings`) and floating-window routes under `/hub?window=<kind>`. Both modes reuse the same feature surface and state. Floating-window headers link into page mode; its return control goes back to the workspace.
- Window focus/open/close synchronizes the active mode with browser history. `YOSAKI.last-route` and `YOSAKI.last-open-windows` persist alongside `YOSAKI.window-layout.v1`. Restored layouts are schema-checked, de-duplicated and clamped into the current viewport; CSS-resized dimensions are captured on pointer release. Narrow initial viewports start with navigation collapsed.
- Validation completed on October 21, 2026: `npm run build`, `python -m py_compile backend/app/main.py`, local `GET /health`, direct `/models` page rendering, floating-to-page `/chat` switching, browser-back restoration, and refresh recovery. 390×844 viewport checks for chat and character pages passed without horizontal overflow. Provider/MCP, physical-device, assistive-technology and VPS verification remain pending.

## 15. Recent implementation note — 2026-10-23

- Character profile fields now show saved/unsaved state. Role switching, creating/importing/duplicating another role, and logout ask before dropping an unsaved profile draft. Cancel preserves the draft; browser reload/close uses a native warning.
- Browser interaction checks ran against a temporary copied backend and SQLite database: unsaved indicator, cancelled switch, confirmed switch, and save. The frontend production build passed. This did not exercise real provider/MCP, device, or VPS behavior.

## 16. Recent implementation note — 2026-10-23

- `YOSAKI_DATA_DIR` isolates the database and Fernet key for local tests. `npm run test:backend` runs auth, backup, demo chat, mock model and MCP tool-loop checks without touching project data.
- Local acceptance exposed a missing MCP response parser and tool policy update route; both are now implemented. Real provider/MCP, device, Docker and VPS acceptance remain pending.
- Isolated browser verification covered MCP connect, refresh and enable. The temporary services and database were removed. On this run, `npm run test:backend` (3 tests), `npm run build`, Python compilation and isolated `/health` passed; SHA-256 hashes of the project SQLite database and `.secret_key` matched before and after testing.
