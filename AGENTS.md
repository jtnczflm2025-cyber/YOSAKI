# AGENTS.md — YOSAKI 项目协作指南

> 本文件记录当前项目的结构、运行方式、功能边界、安全约束和验证状态，供后续开发、调试、审查和部署使用。
> 适用目录：`D:\AI xiangmu\自建前端`
> 最近核对：2026-10-27（Asia/Hong_Kong）。项目整体核对范围见第 11、13 节；欲望系统与套餐模型实测见第 22–29 节。

## 1. 项目定位与当前状态

- 项目名称：**YOSAKI**。
- 产品定位：本地优先、单用户的个人 AI 工作台，围绕角色「阿夜」组织聊天、角色卡、模型、提示词与记忆、MCP/工具、插件、语音和系统设置。
- 当前性质：**本地开发原型 / MVP**。认证、加密存储和部署骨架已经存在，但还没有完成生产安全审计、真实 VPS 验收或所有第三方服务联调。
- 主产品路径：
  ```text
  欢迎页 / → 导航工作台 /hub → 可组合的浮动功能窗口
  ```
- 当前后端版本：`0.23.0`（`backend/app/main.py` 中的 FastAPI `version`）；前端 `package.json` 版本为 `0.1.0`，两者不代表同一发布版本。
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
- 欲望系统 Phase 1～6：驱力、意愿与未决池、有界自由活动、经确认的体验反馈、可追溯长期学习、模拟心理状态。
- 本机可选的 ChatGPT 套餐 OAuth 模型档案；在隔离角色中实测了欲望系统的结构化调用及本地环境操作。
- Docker/Nginx/VPS 部署骨架。

## 2. 目录结构

```text
自建前端/
├─ backend/
│  ├─ app/main.py                 # FastAPI 单文件后端、SQLite、认证和全部业务 API
│  ├─ app/desires.py              # V3 模拟驱力、意愿与手动唤醒 API
│  ├─ app/volition.py             # 欲望系统 Phase 2：参谋、观察、持续意愿
│  ├─ app/autonomy.py             # 欲望系统 Phase 3：自主活动与环境适配器
│  ├─ app/feedback.py             # 欲望系统 Phase 4：体验反馈、预期差与受控调节
│  ├─ app/learning.py             # 欲望系统 Phase 5：长期学习、审阅与证据来源
│  ├─ app/psychology.py           # 欲望系统 Phase 6：情绪、虚拟身体、二阶欲望及关联
│  └─ requirements.txt            # Python 后端依赖
├─ frontend/
│  ├─ index.html                  # Vue 入口 HTML
│  ├─ package.json                # 前端依赖和脚本
│  ├─ vite.config.ts              # Vite、开发端口、/api 代理
│  ├─ tsconfig.json               # Vue + TypeScript 配置
│  └─ src/
│     ├─ App.vue                  # 当前主要前端源代码，状态、逻辑和模板集中在此
│     ├─ DesirePanel.vue          # V3 欲望与意愿面板
│     ├─ VolitionPanel.vue         # Phase 2 观察、判断与未决意愿池
│     ├─ FreeActivityPanel.vue     # Phase 3 自主活动控制与时间线
│     ├─ FeedbackPanel.vue         # Phase 4 体验反馈审阅
│     ├─ LearningPanel.vue         # Phase 5 长期学习与偏好时间线
│     ├─ PsychologyPanel.vue       # Phase 6 模拟心理状态与冲突来源
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
│  ├─ CHAT_ARCHIVES.md            # 第二版聊天档案格式与示例
│  ├─ PLUGINS.md                  # 受控插件工具包 v1 说明
│  ├─ CHARACTER_CARDS.md          # 角色卡 JSON v1 格式与导入导出边界
│  ├─ DESIRES.md                  # V3 欲望系统与授权边界
│  ├─ VOLITION.md                 # Phase 2 意愿状态与接口
│  ├─ AUTONOMY.md                 # Phase 3 活动、环境与恢复
│  ├─ FEEDBACK.md                 # Phase 4 反馈规则、预期差与接口
│  └─ LEARNING.md                 # Phase 5 证据、审阅、算法与接口
│  └─ PSYCHOLOGY.md               # Phase 6 事件顺序、规则、迁移与边界
├─ plugins/                       # 当前插件相关目录，具体可执行能力仍不开放
├─ tests/test_local_acceptance.py # 隔离 API 与模拟模型/MCP 验收
├─ README.md                      # 本地运行、产品状态和迭代记录
├─ AGENTS.md                      # 本文件
├─ package.json                   # 根级 npm 脚本和 frontend workspace
├─ package-lock.json              # npm 锁文件
└─ .gitignore                     # 依赖、构建产物、虚拟环境、数据库/密钥忽略规则
```

### 文件与工作区注意事项

- 当前目录已初始化为 Git 仓库，`main` 跟踪 `origin/main`；推送前仍要检查暂存清单，确保运行数据与密钥未入库。
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
- `/chat`、`/characters`、`/models`、`/memory`、`/desires`、`/learning`、`/psychology`、`/mcp`、`/plugins`、`/voice`、`/settings` 已支持全页工作区模式，并与 `/hub?window=<kind>` 浮动窗口模式共用同一功能界面；它们尚未拆分为独立页面组件。
- 路由由 App.vue 读取 `location.pathname`、query 和 History API 管理，没有引入 Vue Router；未知路径目前落入欢迎视图。Nginx 的 SPA fallback 只保证能加载入口文件。
- 首次进入调用认证状态接口：
  - 没有 owner：显示一次性账户初始化。
  - 有 owner 但未登录：显示登录。
  - 已登录：允许进入欢迎页和工作台。
- 退出登录后，后端撤销 session，前端清空窗口和消息状态。

### 工作台和窗口

- `features` 包含 11 个入口：聊天、角色卡、模型配置、提示词与记忆、欲望与意愿、长期学习、心理状态、MCP 与工具、插件、语音、系统设置。
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
- **模型配置**：模型档案 CRUD、OpenAI-compatible 或 Responses API Key 协议、Base URL、模型名、temperature、max tokens、可选推理强度；API Key 只提交后端；真实非流式连接测试；聊天中切换模型。
- **提示词与记忆**：长期/临时记忆、搜索、启用/停用、编辑/删除、历史条数、记忆开关、摘要和上下文预览。
- **欲望与意愿**：角色独立驱力、内部观察、未决意愿、手动自由活动和经确认的体验反馈。
- **长期学习**：来源证据、待审经历记忆、偏好估计与时间线、条件化策略、目标进度参考及手动模型整理。
- **心理状态**：可追溯的模拟情绪、虚拟身体、二阶欲望、内部冲突和有界关联图；只供意愿参考。
- **MCP 与工具**：保存服务、认证方式、连接、刷新工具、查看 schema、工具启停和确认策略。
- **插件**：导入 JSON 清单、启停插件、逐工具授权、卸载；单文件前端上限 256 KB。
- **语音**：浏览器/系统 `speechSynthesis` TTS；STT 优先本机识别，不可用时先弹出远程浏览器识别同意框。
- **系统设置**：布局恢复、加密 `.ybackup` 导出和导入。

## 5. 后端架构和数据模型

- `backend/app/main.py` 仍集中管理认证、核心数据和聊天；欲望系统的各阶段实现已拆到 `desires.py`、`volition.py`、`autonomy.py`、`feedback.py`、`learning.py`、`psychology.py`。
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
  - `drive_states`、`volition_items`、`activity_plans`、`feedback_activities_v4`
  - `learning_evidence`、`learning_preferences`、`learning_memories`、`learning_strategies`、`learning_goal_notes`
  - `psychological_snapshots`、`psychological_events`、`second_order_desires`、`psychological_edges`
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
| 欲望与学习 | `/api/desires/{id}*`、`/api/desires/{id}/learning*`、`/api/desires/{id}/psychology*` | 驱力、意愿、自由活动、反馈、长期学习和模拟心理状态 |
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
- 第二版已加入可选的本地目录/GitHub 定时备份，但尚未用真实 GitHub 凭据或生产环境做恢复演练。

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

- 真实模型覆盖仍不完整：本机授权的 `gpt-6.1-sol` 套餐档案已通过欲望系统隔离调用；其他模型、不同 provider 和第三方 MCP endpoint 的完整联调未完成。
- 上下文预览中的 token 数是字符启发式估算，缓存状态没有服务商数据支撑；不能据此报告真实计费 token 或缓存命中率。
- legacy SSE MCP 的完整适配；当前可保存该 transport，但不应把部分兼容逻辑当作全部通过。
- provider-backed TTS/STT、真实麦克风、移动端和跨浏览器 QA。
- URL/ZIP 插件包装、插件市场和可执行插件。
- 真实 GitHub 备份与生产恢复演练、部署加固。
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

## 17. Repository note — 2026-10-23

- The local directory is now a Git checkout tracking the private GitHub repository `jtnczflm2025-cyber/YOSAKI` on `main`. The initial project upload excludes SQLite databases, `.secret_key`, generated frontend JavaScript, dependencies, build output, and production environment secrets.

## 18. V2 local increment — 2026-10-23

- Backend adds legacy-compatible strict/chunked turn windows, persisted public reasoning text and character monologue, and actual provider usage display when present. The API Key based Responses adapter streams public reasoning summaries and keeps opaque continuation items server-side for approved tool turns; neither is hidden chain-of-thought access. Monologue is excluded from subsequent model context.
- Batch archive endpoints live under `/api/archives`: JSON v2 is the migration format and includes historical tool-run details; Markdown/TXT are readable strict formats. Imports append inert historical messages and tool cards, preview before import, skip duplicate hashes, and enforce 8 MiB per file, 64 MiB per batch, and 20,000 messages per file. Browser stores only non-secret cleaning templates.
- `/api/system/backup-plan` manages local and private GitHub encrypted backups. Scheduler defaults off. GitHub uploads retry transient failures at most three times; local and GitHub results are recorded separately, including a distinct cloud retention failure after successful upload. `YOSAKI_BACKUP_PASSWORD` and `YOSAKI_BACKUP_GITHUB_TOKEN` are backend environment variables and must never enter SQLite, exports, browser storage, logs, or Git. Use a dedicated backup repository. Git history persists after visible-file pruning.
- The repository is currently private; ChatGPT plan OAuth eligibility, account authorization and real Responses provider verification remain pending. The current Responses profile uses an API Key and does not confer Plus entitlement. Do not claim hidden chain-of-thought access.
- Run `npm run test:backend`, `npm run build`, and `.\.venv\Scripts\python.exe -m compileall -q backend\app`. The 9 backend tests use temporary data. An isolated browser run selected and imported `tests/fixtures/sample-conversation.json`, checked cleanup preview, model protocol controls, and backup settings. A second isolated run imported `tests/fixtures/sample-rich-conversation.json` and checked the separate collapsible reasoning and monologue areas plus an inert historical tool card without approval controls. The project SQLite and `.secret_key` SHA-256 hashes matched before and after these checks. Real provider/GitHub credentials, physical devices, Docker and VPS remain unverified.

## 19. V2 acceptance hardening — 2026-10-23

- The owner chose to keep the source repository private. The official ChatGPT plan usage path is documented for open-source, locally hosted apps, so OAuth work is paused pending an eligible route. Do not change repository visibility or imply the API Key Responses adapter consumes Plus allowance.
- A successful local scheduled backup whose old-file pruning fails now reports `retention_failed` instead of `failed`; the new encrypted file remains present and the separate outcome is covered by an isolated test.
- TXT archive imports now reject any unrecognized message boundary in the file, including after valid messages. Markdown/TXT export escapes body lines that begin with their format's boundary marker, and isolated round-trip checks cover both cases. The 9 backend tests, frontend build, backend compilation and isolated HTTP `/health` passed. No real backup environment credentials or Docker are available on this machine.

## 20. ChatGPT plan trial and repository visibility — 2026-10-23

- The owner has superseded the private-repository decision above: publish `jtnczflm2025-cyber/YOSAKI` without adding a license. Earlier private/OAuth-paused notes are historical. Public visibility alone does not establish eligibility for the documented open-source, locally hosted app route or prove Plus allowance works.
- `backend/app/chatgpt_plan.py` provides an opt-in local OAuth trial. Set `YOSAKI_CHATGPT_PLAN_ENABLED=1` and restart the backend; connect from model settings. It uses dynamic registration, PKCE, state, nonce, ID-token validation, refresh and revocation, plus a separate Responses adapter without an API Key. A real account sign-in still requires the owner's consent in the browser.
- OAuth credentials are encrypted in `.chatgpt_plan` under the selected data directory, outside SQLite and `.ybackup`; they are excluded from Git and Docker build context. The plan model uses the existing per-call MCP approval chain and never falls back to a paid API Key.
- Isolated mock tests cover OAuth, refresh, models, stream, tool continuation and revocation. Real account eligibility, allowance, provider compatibility, GitHub backup, devices, Docker and VPS remain separate acceptance tasks. Do not report Plus as verified from mock results.

## 21. Local ChatGPT plan acceptance — 2026-10-23

- A real sign-in was completed by the owner through OpenAI's consent page on this local machine. The returned scope included `chatgpt.tokens.use.direct`; YOSAKI listed account-visible models, stored the OAuth credentials in the ignored encrypted `.chatgpt_plan` file, and added a `gpt-6.1-sol` profile. The real model test returned `OK`. A new chat streamed `YOSAKI 已连接。` and provider usage of 116 input, 11 output and 0 cached tokens. This validates this account's local plan route at this time, not other models or production deployment.
- The initial browser callback arrived after the former 10-minute listener expiry and failed with connection refused. The listener lifetime is now 60 minutes, and the callback page removes its one-time code from the visible URL after processing. If authorization expires, start a fresh attempt from model settings.
- A SQLite integrity-checked safety snapshot of the pre-connection database and Fernet key was saved under the ignored `data/safety-snapshot-chatgpt-20261023/` directory before starting the real backend. Do not commit, publish, or log it. Real GitHub backups, MCP/provider compatibility, devices, Docker and VPS remain unverified.

## 22. V3 原生欲望系统 — 2026-10-24

- `backend/app/desires.py` 提供每角色默认关闭的模拟驱力、事件去重、隔离模拟、快照、手动唤醒和偏好建议；入口位于 `/desires` 与工作台浮窗。后端版本为 `0.18.0`。技术与 API 边界见 `docs/DESIRES.md`。
- 聊天只读取话题相关的简短驱力参考；正式事件和活动反馈需要明确提交。意愿由选定模型生成结构化公开摘要，不是模型私人推理，也不自动决定工具权限。
- 唤醒仅手动启动，按会话限制时长、模型和工具调用。只读工具的分类由 owner 明确确认，第三方 MCP 的副作用无法自动证明；配置变化使预授权失效。其他工具仍逐次确认，普通聊天确认链不变。进程重启不会自动续跑会话。
- 长期 baseline 只在多次正向反馈后提出小幅建议，由 owner 审核并可回滚。隔离后端测试与临时浏览器检查覆盖主要路径；真实外部服务自主活动、设备及生产部署未验证。不要把这些模拟状态描述为 AI 的真实生理需求。

## 23. 欲望系统 Phase 2 — 2026-10-24

- `backend/app/volition.py` 增加确定性的状态参谋、手动模拟自我观察和纯意愿判断。新接口只保存公开描述和意愿，不调用 MCP 或自动执行活动；原有手动唤醒仍可使用工具。
- 持续意愿分为候选兴趣、未决意愿和明确承诺的长期目标。未决意愿没有时间驱动的紧迫度、失败或自动清理；用户可显式归档、放弃、重新考虑。旧 `volition_goals` 仍作兼容历史，并以幂等链接映射到新意愿表。
- 新记录跟随角色隔离、删除和加密 SQLite 备份；角色复制不继承。普通聊天和已有记忆数据格式不变。具体 API、迁移和状态流转见 `docs/VOLITION.md`。后端版本为 `0.19.0`。

## 24. 欲望系统 Phase 3 — 2026-10-24

- `backend/app/autonomy.py` 增加 `free` 模式后端逐步活动；原有 `guided` 单步唤醒保持兼容。仅由用户手动启动，不设定时唤醒。页面关闭后后端进程仍可在原授权时窗内继续，重启后暂停等待用户恢复。
- 首版 `EnvironmentAdapter` 接入每角色隔离的本地探索室。对象和发现是真实持久化的**场景内模拟状态**，不能描述成现实世界资料。跨唤醒读取意愿、近期计划与发现；不自动增加未决意愿紧迫度，也不把会话结束当成长期目标完成。
- 通过时间、模型次数、环境及工具动作次数和累计输出 token 预留额限制资源；无可靠价格数据时不估算美元成本。MCP 仍需明确选中，未核实只读、路径参数或其他敏感调用逐次确认。工具超时或重启时结果未知的操作不得自动重放或记为成功。
- 活动计划、操作、时间线、检查点与待 Phase 4 消费的反馈事件保存于 SQLite，角色删除清理，复制不继承，加密备份纳入。Phase 3 不自动更新驱力或偏好。前端在 `/desires` 及浮窗共用 `FreeActivityPanel.vue`。接口、迁移和恢复规则见 `docs/AUTONOMY.md`；后端版本为 `0.20.0`。
- 当前自由活动任务运行在单个 Uvicorn worker 内；多进程调度和故障接管未验收。隔离后端验收 16 项、Python 编译、前端构建及隔离浏览器交互通过；浏览器中验证了自由活动启动、环境探索和刷新后历史保留。真实模型、第三方 MCP、设备与部署仍需分别验收。

## 25. 欲望系统 Phase 4 — 2026-10-26

- `backend/app/feedback.py` 按 Phase 3 自由活动中的计划归集实际操作证据，分开保存操作结果、目标结果和会话结束原因。角色可在行动前给出可空乐趣预估；事后模拟体验产生可空评分，预期差只作证据，不直接成为驱力增量。
- 自由活动启动时可单独预留最多一次体验回顾模型调用和输出 token；前端默认开启，旧 API 省略参数时关闭。模型异常或中断保留客观记录，不自动重放可能已计费的请求。新反馈由 owner 逐项确认，才通过欲望模块受控函数和同一 SQLite 事务更新短期驱力、事件与审计。旧单步／聊天反馈保持原路径。
- 确定性调节只影响相关驱力的短期激活、满足、抑制，含单次限幅、边界减弱和重复活动衰减；冷却是规划参考而非禁令。长期 baseline、长期目标不自动更改，新兴趣只是候选证据。接口、迁移、公式及 Phase 5 读取边界见 `docs/FEEDBACK.md`。真实模型、MCP、设备和部署仍需单独验收。
- 2026-10-26 隔离后端回归 20 项通过，另有最新未知结果与恢复变更的定向测试；Python 编译和前端构建通过。隔离浏览器检查了自由活动启动、70→90 的 `+20` 预期差、确认前后驱力变化、浮窗转全页和刷新后持久化。真实服务、设备与 VPS 未验证。

## 26. 欲望系统 Phase 5 — 2026-10-27

- `backend/app/learning.py` 在 Phase 4 反馈正式应用时同事务、无模型地归集证据；旧已应用反馈在读取长期学习页时分批补录。同来源幂等，每会话同主题最多贡献一次偏好权重。没有体验评分不构成负向证据。新表按角色隔离并纳入加密 SQLite 备份，角色复制不继承。
- 长期偏好和策略评价先形成建议，owner 审阅后才进入 Phase 2／3 的有界上下文。偏好与驱力 baseline 独立；候选经历记忆接受后写入现有 `memories`，并保留来源、事实与模拟解释。撤销偏好派生更新会从仍有效证据重算，原始活动不删除。策略只有明确关联的操作才进入使用统计；目标进度参考不自动改变意愿目标状态。
- `frontend/src/LearningPanel.vue` 提供全页 `/learning` 和 `/hub?window=learning` 浮窗，包含证据、记忆、偏好时间线、策略和目标进度审阅。复杂整理只能用户手动选择模型及输出 token 上限后发起一次请求；失败不改变正式学习状态。接口、规则、迁移和 Phase 6 读取边界见 `docs/LEARNING.md`。后端版本为 `0.22.0`。真实模型、MCP、设备及部署仍需单独验收。
- 隔离后端完整回归 22 项通过，最新 Phase 4／5 变更又做定向复测；Python 编译和前端构建通过。隔离浏览器验证了 `/learning` 全页／浮窗、证据追溯、偏好审批、手动修正和回滚时间线。真实模型、MCP、设备和 VPS 尚未验收。

## 27. 欲望系统 Phase 6 — 2026-10-27

- `backend/app/psychology.py` 独立保存角色模拟情绪、虚拟身体状态、二阶欲望、冲突与有界有向关系。只有已返回的 Phase 3 操作结果形成活动负荷；Phase 4 已确认反馈形成体验影响。事件 ID 去重，保存前后快照、结构化评价、规则版本和来源；旧反馈不推断补造心理历史。
- 自由活动可独立预留最多一次心理情境评价模型调用与 128～1200 输出 token，默认 600。模型失败或无效输出只用确定性规则；评价不能改客观结果。Phase 2／3 只读取有界参考，心理状态不调用工具、不改驱力 baseline 或正式长期学习。二阶认同和抵触可并存，长期化仍走 Phase 5 审阅。
- `/psychology` 和 `/hub?window=psychology` 共用 `PsychologyPanel.vue`，显示模拟状态、来源、二阶欲望、冲突、关联审阅和隔离模拟。新表纳入加密 SQLite 备份；角色复制不继承经历，删除按外键清理。关系计算最多两层且逐项限幅，驱力目标只生成短期提示。接口、参数和已知限制见 `docs/PSYCHOLOGY.md`。后端版本 `0.23.0`。本次通过 25 项隔离后端测试、Python 编译、前端构建及隔离浏览器全页／浮窗检查；真实模型、MCP、设备和部署仍未验收。
- `tests/test_phase6.py` 在临时数据库与模拟模型中覆盖去重、矛盾、关系审批与环路、模型评价成功／失败、备份和角色隔离、100／500 次稳定性模拟。真实第三方模型、MCP、设备、Docker 与 VPS 仍需分别验收。

## 28. 欲望系统套餐模型实测 — 2026-10-27

- 真实账户的 `gpt-6.1-sol` 套餐模型在隔离 SQLite／临时角色上验证。欲望系统原先以非流式请求调用该通道，真实接口返回 `Stream must be set to true`；`backend/app/desires.py` 现按官方要求以流式 Responses 读取文本增量并等待 `response.completed`，没有自动切换到付费 API。结构化输出按 Pydantic 模型提供 JSON Schema，仍保留本地校验；自由活动进一步限制当前可引用的意愿、环境动作和工具 ID。
- 真实调用已通过 Phase 2 观察与纯意愿判断、一次 Phase 3 本地环境成功操作、Phase 4 用户确认反馈后的 Phase 5 手动整理，以及 Phase 6 一次评价。一次 Phase 4 体验回顾返回合法空结果，因此未推断主观评分或驱力增量。真实测试未涉及第三方 MCP、其他模型、真实设备或部署。测试进程的业务数据位于临时目录；正式数据库未用于活动测试，套餐授权文件可能按正常流程刷新。

## 29. 当前项目快照与交付检查 — 2026-10-27

- 主分支准备发布的是现有本地工作台及欲望系统 Phase 1～6 的源码、隔离测试和设计文档。产品入口为 `/`、`/hub` 和 11 个可独立全页打开的功能区；欲望、长期学习、心理状态还共用工作台浮窗。`backend/app/main.py` 的版本号为 `0.23.0`，前端 package 仍为 `0.1.0`，均不表示生产发布。
- 当前授权边界：角色默认不启用欲望系统；用户手动唤醒并指定时长、模型与操作额度；模型可不行动。MCP 沿用用户确认。正式短期反馈和长期学习建议分别经过用户审阅；模拟心理状态只提供参考，不能直接执行工具或改人格／baseline。
- 2026-10-27 已通过 25 项隔离后端测试、Python 编译、前端构建、Phase 6 全页／浮窗本地浏览器检查，以及单独的套餐模型真实调用。测试与临时 OAuth 调用不构成生产验收；第三方 MCP、其他模型、真实设备、Docker／VPS 和公网安全仍需单独验证。
- Git 上传前只暂存源码、测试、README、AGENTS 和设计文档。`data/yosaki.sqlite3`、`.secret_key`、`.chatgpt_plan`、安全快照、日志、`.venv`、依赖、构建和生成的 `.vue.js` 均须保持忽略；推送后检查远端 commit 与本地一致。
