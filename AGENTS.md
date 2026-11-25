## 执行与验收分工原则（Owner 明确要求，2026-11-19）

在既有授权、数据保护与并行修改边界内，由 Codex 连续完成排查、复现、实现、必要测试、实际浏览器验收、技术取证、获准安装和结果汇报。以“定位 → 修复 → 验证 → 已授权加载 → 收尾”为完整交付，不将常规步骤拆为多轮审批，不要求 Owner 代跑终端、辨认协议或抄写日志。

执行和验收须分清证据：冻结被验对象，先验证再加载；分别记录模拟、真实供应商、软件播放和真人听感，保留失败、unknown、可靠用量及已用授权。技术通过不能代替内容确认、发布决定或主观听感；未验证项不得记为通过。

Owner 只参与无法代办的凭据输入／系统人工确认、主观体验与内容取舍，以及超出现有授权的外发、付费、迁移、服务切换或范围变更。确需参与时，一次说明具体阻挡、建议和已准备好的最小执行范围；已获授权的常规步骤不重复确认。遇到阻塞保留准确状态，并继续完成独立事项，不绕过工具审查或数据保护。

冻结候选预检须区分“实际执行源码／前端资源／请求／权限／预算变化”和“工作区其他并行变化”。后者先读取差异，必要时以新鲜数据库副本及假供应商验证模块加载、启动／Schema／读写兼容性和本次路径；证实不影响冻结候选及共享数据后，在既有授权内记录接受的环境基线，不仅因旁支文件哈希变化要求重新批准。端口冲突优先选空闲端口并记录绑定，不接管／关闭其他进程。实际输入、音色、调用量或权限需扩大，或共享兼容问题未解决时才停止；旧批终态、旧收据及原Manifest不得改写，后继批次与环境差异须明确记录。（Owner明确要求，2026-11-25）

交付正文直接说明完成内容、是否生效、验证结果、备份／恢复位置及剩余事项；关键修改同步摘出，不只让 Owner 到文件中查找。Memory 交接仍更新原C1～C10总表，区分09与其余12条及各自证据，不只在末尾补记。以上分工不扩大任何原有授权，不改 Memory／Voice 的来源、展示、权限和计量规则，不覆盖既存未提交工作。

> Memory V2 当前收口以 `docs/MEMORY_V2_EXECUTION_PLAN.md` 为产品与交付基线，每轮完整读取。范围内连续实施、测试和修复，不按旧阶段逐个请求继续。精修正文、单经历梗概与周期摘要必须分开；阿夜记忆通道不得返回 RAW／CLEANED，展开到精修正文。保留来源、权限、unknown、结算和数据保护。交付以 C1～C10 的实际结果为准，不增加未经用户要求的功能或通过门槛。历史状态不代表当前实现；已核实差异在同一任务内修正。不得覆盖既有未提交工作。

> 全界面 UI 基准：修改页面、浮窗、语音控件或阅读界面前，阅读 `docs/UI_DESIGN_STANDARD.md`；复用 `frontend/src/ui/` 的银灰玻璃变量、共享主题和组件。正文保持 17px／1.75，手机不缩字；全功能入口、未保存、录音、确认与错误状态必须保留。改版实施记录见 `docs/UI_REDESIGN_PLAN.md`，实际视觉验收见 `design-qa.md`。此条不改变 Memory V2、Voice、来源、权限和数据保护规范。

# AGENTS.md — YOSAKI 项目协作指南

> 本文件记录当前项目的结构、运行方式、功能边界、安全约束和验证状态，供后续开发、调试、审查和部署使用。
> 适用目录：`D:\AI xiangmu\自建前端`
> 最近Voice现场核对：2026-11-19（Asia/Hong_Kong），第4步限定安装及实际HTTP页面检查见 `docs/VOICE_STEP4_DAILY_INSTALL_20261119.md`。其他模块现场状态不由本次检查扩大。交接见第56节及其后续补记；Memory V2 的产品与交付基线及 C1～C10 实际结果以 `docs/MEMORY_V2_EXECUTION_PLAN.md` 为准，Voice 以 `docs/VOICE_DELIVERY_INDEX.md` 和最新专项记录为准。第 49～55 节保留其当时状态。

## 1. 项目定位与当前状态

- 项目名称：**YOSAKI**。
- 产品定位：本地优先、单用户的个人 AI 工作台，围绕角色「阿夜」组织聊天、角色卡、模型、提示词与记忆、MCP/工具、插件、语音和系统设置。
- 当前性质：**本地开发原型 / MVP**。认证、加密存储和部署骨架已经存在，但还没有完成生产安全审计、真实 VPS 验收或所有第三方服务联调。
- 主产品路径：
  ```text
  欢迎页 / → 导航工作台 /hub → 可组合的浮动功能窗口
  ```
- 当前后端版本：`0.24.0`（`backend/app/main.py` 中的 FastAPI `version`）；前端 `package.json` 版本为 `0.1.0`，两者不代表同一发布版本。
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
- Memory V2：Chronicle 管理、受资格保护的记忆上下文及手动／逐条同意的语义补查已按受控流程加载；日常付费周期加工保持关闭。H5-B 正式库有 130 条有序来源、13 条经历及13份精修正文／13份单经历梗概（09已确认发布，其余12条待审）；05／07 已经独立8192容量版本补齐，原可靠 incomplete 历史保留。按2026-11-19已留存资格核对，仅09当前标题／梗概已确认、精修已确认关联并发布，文档向量已建；该问法手动补搜及展开通过、Hybrid未命中。其余01～08、10～13共12条仍为未发布管理草稿，不能获得正式召回资格；2026-11-25新增限定真实验收：单条12/30秒已加载并经离线及实际界面验证，09一次30秒被动注入通过（首轮仅工具提议），独立明确指令下搜索/确认/完整精修展开/确认/续答通过；不推定自然问法全链、稳定性或Owner质量通过。核价至香港2026-11-26 08:00有效、不自动续期。以执行计划更新后的C1～C10及最终使用说明为准，第55～56节保留历史时点。
- H5 V2 加密大容量备份及独立恢复已在隔离环境实现；HTTP 入口默认关闭，旧上传恢复入口拒绝直接替换活动数据库。
- Voice：档案与告知、HTTP 消息朗读与缓存、主动录音草稿、可选自动朗读及需用户主动确认的日常连续通话已启用；通话默认 HTTP，可手选 TTD WS。生成中按键打断接续、HTTP 播放停音和耳机插话各有范围有限的真实验收。多位置 AI 规划已有一次真实建议贴切和两位置声学单样本认可；普通聊天短日语单请求 HTTP 已于2026-11-19受控安装，用户AI设置仍默认关闭，新的真实产品听评待验；通话、WS与更多语言的新AI表达未接入。长文多请求首段结果 unknown、固定 Benchmark 与跨设备稳定性未完成。以 `docs/VOICE_DELIVERY_INDEX.md` 为当前入口，旧第54节语音故障与额度数字仅作当时记录。
- Docker/Nginx/VPS 部署骨架。

## 2. 目录结构

```text
自建前端/
├─ backend/
│  ├─ app/main.py                 # FastAPI 核心、SQLite 初始化、认证、聊天与模块安装
│  ├─ app/desires.py              # V3 模拟驱力、意愿与手动唤醒 API
│  ├─ app/volition.py             # 欲望系统 Phase 2：参谋、观察、持续意愿
│  ├─ app/autonomy.py             # 欲望系统 Phase 3：自主活动与环境适配器
│  ├─ app/feedback.py             # 欲望系统 Phase 4：体验反馈、预期差与受控调节
│  ├─ app/learning.py             # 欲望系统 Phase 5：长期学习、审阅与证据来源
│  ├─ app/psychology.py           # 欲望系统 Phase 6：情绪、虚拟身体、二阶欲望及关联
│  ├─ app/behavior.py             # Phase 6.5：行为报告、模型调用与资源采样
│  ├─ app/memory_v2_*.py          # M1 索引、M2/M3、A3、条目、年历、导入与元数据
│  ├─ app/memory_authority.py     # 正式记忆权威与上下文边界
│  ├─ app/backup_v2*.py           # H5 V2 备份格式、入口、准入及环境检查
│  ├─ app/archives.py             # 聊天档案批量迁移
│  ├─ app/scheduled_backup.py     # 可选定时备份
│  ├─ app/chatgpt_plan.py         # 本机套餐授权档案
│  ├─ app/background_tasks.py    # 后台任务启动开关
│  ├─ app/voice/                 # 档案、朗读、缓存、Scribe 与连续通话
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
│     ├─ ChronicleApp.vue          # 受控启用的 /chronicle 管理入口
│     ├─ ChronicleEditor.vue       # 条目字段、来源、确认与发布
│     ├─ ChronicleImportWizard.vue # 历史导入向导
│     ├─ chronicle.css             # Chronicle 独立样式
│     ├─ ui/                       # 银灰玻璃变量、主题、地球与共享抽屉
│     ├─ ArchivePanel.vue          # 聊天档案操作
│     ├─ BackupPlanPanel.vue       # 备份计划与 H5 操作
│     ├─ ChatGPTPlanPanel.vue      # 本机套餐连接
│     ├─ voice/VoiceSettings.vue   # Voice 设置、告知与日额度
│     ├─ voice/MessageAudioPlayer.vue # 消息音频播放
│     ├─ voice/ScribeDraftInput.vue   # 主动录音草稿
│     ├─ voice/voiceApi.ts         # 语音 API 客户端
│     ├─ App.vue.js               # vue-tsc 生成/辅助产物，不应手工编辑
│     ├─ main.ts                  # 实际入口，加载共享主题并按路径挂载 App/Chronicle
│     ├─ main.js                  # 生成/备用 JS 入口，当前不是 index.html 的源入口
│     └─ style.css                # 产品基础样式；共享外观见 ui/
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
│  ├─ LEARNING.md                 # Phase 5 证据、审阅、算法与接口
│  ├─ PSYCHOLOGY.md               # Phase 6 事件顺序、规则、迁移与边界
│  ├─ BEHAVIOR_OBSERVATION.md     # Phase 6.5 观察协议和指标
│  ├─ MEMORY_V2_*.md             # M1～M5、P1/P2、历史导入、验收与功能台账
│  ├─ H5*.md                     # V2 备份、安全门槛及 H5-B 样本记录
│  └─ VOICE_*.md                 # Voice 阶段计划、V1-B 零请求测试单与评测规范
├─ scripts/                       # 专用运行入口、隔离验收、迁移和备份/导入工具
├─ plugins/                       # 当前插件相关目录，具体可执行能力仍不开放
├─ tests/test_local_acceptance.py # 隔离 API 与模拟模型/MCP 验收
├─ tests/test_phase65.py          # 多日脚本调度与行为报告隔离验收
├─ tests/test_memory_v2*.py       # Memory V2 隔离契约、顺序、权限与派生测试
├─ tests/test_backup_v2*.py       # H5 备份格式、准入及环境测试
├─ tests/test_voice*.py           # V1-A、备份脱敏及隔离语音测试
├─ README.md                      # 本地运行、产品状态和迭代记录
├─ AGENTS.md                      # 本文件
├─ package.json                   # 根级 npm 脚本和 frontend workspace
├─ package-lock.json              # npm 锁文件
└─ .gitignore                     # 依赖、构建产物、虚拟环境、数据库/密钥忽略规则
```

### 文件与工作区注意事项

- 当前目录已初始化为 Git 仓库，`main` 跟踪 `origin/main`；推送前仍要检查暂存清单，确保运行数据与密钥未入库。
- 2026-11-08 工作区有大量既存的已修改与未跟踪文件，包含 `AGENTS.md`、Memory V2、H5 和私人样本相关记录。不要把它们视为可清理的生成物，也不要用 `reset`、`clean`、`stash` 或批量覆盖消除他人的工作；修改前后检查 `git status --short` 和目标文件差异。
- `tests/` 提供隔离后端验收测试，根级 `npm run test:backend` 可运行；`backend/__pycache__/` 下的旧字节码不是测试源码。
- `node_modules/`、`.venv/`、`__pycache__/`、`frontend/dist/`、`frontend/tsconfig.tsbuildinfo` 是依赖或生成物，应保持忽略。
- `data/yosaki.sqlite3`、`data/.secret_key`、`.env.production`、API Key、MCP 密钥、session 和备份密码都属于敏感材料；不要提交、复制到日志或写入前端存储。
- 前端业务状态仍集中于大体量 `frontend/src/App.vue`，Chronicle 在 `ChronicleApp.vue`／`ChronicleEditor.vue`，共享 UI 在 `frontend/src/ui/`；先定位所属源组件，不要直接改 `*.vue.js` 等生成产物。
- `README.md` 同时包含按日期追加的实现记录；如果顶部概览与最新日期记录冲突，以当前代码和最新记录为准，并在后续维护中修正文档漂移。
- `文档存档/`、根目录的 `.rar`、仓库外 H5-B 隔离副本及 `docs/H5B_REAL_SAMPLE_REPORT.md` 涉及用户历史样本或其交接记录；不要复制私人正文到测试夹具、日志、AGENTS.md 或公开提交。

## 3. 技术栈与运行方式

### 前端

- Vue 3 + Vite + TypeScript。
- 图标：`lucide-vue-next`。
- Markdown：`marked`；内容清理：`dompurify`。
- 欢迎页与工作台的地球使用 `three`、`three-globe` 和本地资源；按需加载，正文阅读页不持续挂载地球。
- CSS 与主题以 `frontend/src/ui/tokens.css`、`silver.css`、`theme.ts` 为共享来源，基础样式仍在 `frontend/src/style.css`。银灰玻璃的页面与浮窗规范见 `docs/UI_DESIGN_STANDARD.md`；正文 17px／1.75，手机不缩字。当前没有 Tailwind/shadcn 运行依赖。
- 开发端口通常为 `5173`；`frontend/vite.config.ts` 将 `/api` 代理到 `http://127.0.0.1:8000`。
- dev/preview 使用 `--host 0.0.0.0`，意味着可能具备局域网暴露能力，但不代表已经完成 HTTPS、CORS、防火墙和公网安全配置。

### 后端

- FastAPI + Uvicorn + SQLite，推荐 Python 3.12，最低按 README 约定使用 Python 3.11+。
- 依赖以 `backend/requirements.txt` 为准，含 `fastapi`、`uvicorn[standard]`、`httpx`、`cryptography`、`jsonschema`、`python-multipart`、`websockets`、`markdown-it-py` 和 `mdurl`。
- 数据库路径由 `backend/app/main.py` 计算为项目根目录下的 `data/yosaki.sqlite3`。
- 设置 `YOSAKI_DATA_DIR` 时，SQLite 和 `.secret_key` 一起改用该目录；未设置时仍使用项目 `data/`。本地测试必须先设置隔离目录再导入后端模块。
- 后端启动：
  ```powershell
  Set-Location "D:\AI xiangmu\自建前端"
  uv venv .venv --python 3.12
  uv pip install --python .venv\Scripts\python.exe -r backend\requirements.txt
  .\scripts\start_yosaki_development.ps1
  ```
- R2-C 已完成日常库增量初始化、Development 端口交接和真实登录验收，见 `docs/R2C_EXECUTION_RESULT.md`；`docs/RUNTIME_MODES.md` 开头的“等待 R2-C”是历史描述。继续使用专用 Runtime 的路径与 Schema 预检，不用裸 Uvicorn 绕开。日常 Voice 使用 `scripts/start_yosaki_voice_daily.ps1` 及配套前端入口；Chronicle 的 workspace/context 与付费开关也须在进程环境核对。启动脚本或文档中的历史 PID 不代表当前运行实例。
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
  - `npm run test:backend`：运行 `tests/` 下的 Python unittest；完整回归耗时较长，按具体改动先跑专项，再按验收门槛跑全套。
- 健康检查：`GET http://127.0.0.1:8000/health`。

## 4. 前端架构与交互状态

### 入口和认证

- `frontend/src/App.vue` 根据 `window.location.pathname` 区分主要的 `welcome` 与 `hub` 视图；当前主要实际路径是 `/` 和 `/hub`。
- `/chat`、`/characters`、`/models`、`/memory`、`/desires`、`/learning`、`/psychology`、`/mcp`、`/plugins`、`/voice`、`/settings` 已支持全页工作区模式，并与 `/hub?window=<kind>` 浮动窗口模式共用同一功能界面；它们尚未拆分为独立页面组件。
- 路由由 App.vue 读取 `location.pathname`、query 和 History API 管理，没有引入 Vue Router；未知路径目前落入欢迎视图。Nginx 的 SPA fallback 只保证能加载入口文件。
- `/chronicle` 由 `frontend/src/main.ts` 在 `VITE_YOSAKI_CHRONICLE_A0=1` 时加载 `ChronicleApp.vue`；开关关闭时仍进入常规 App。正式入口已受控部署并验证登录、导入、草稿编辑、确认、发布、搜索与重读；后端仍须 workspace／context、权限、来源及 Runtime 门控，不能仅凭页面或表存在推定启用。
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
- **语音**：保留浏览器/系统 `speechSynthesis` TTS 与受同意约束的浏览器 STT；另有 ElevenLabs Voice Profile、第三方告知、V2 HTTP 消息朗读及缓存、Scribe 主动录音草稿、可选自动朗读和需主动确认的两轮连续通话。各真实验收仅覆盖记录中的样本；日常通话修复后的真人复验仍未完成，见第 54 节。
- **系统设置**：布局恢复、加密 `.ybackup` 导出和导入。
- **Chronicle**：正式管理页分别展示梗概、完整第一人称精修和按需原始对话，支持时间树、导入草稿、搜索、修正、确认及独立发布。阿夜记忆通道最终展开到合资格精修，缺失时不得回退 RAW／CLEANED。`scripts/chronicle_a0_demo.py` 仍只是隔离演示；日常状态按唯一执行计划 C1～C10 和第 55 节核对。

## 5. 后端架构和数据模型

- `backend/app/main.py` 集中管理认证、核心数据、聊天与模块安装；欲望系统、聊天档案、定时/H5 备份、Memory V2 和 Voice 分别位于 `backend/app/` 的独立模块。
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
- M1 的 `mv2_*` 索引/任务表由 `main.py` 初始化，索引是可重建派生数据，不是新事实权威。Chronicle workspace/context 已按受控流程增量安装与加载；`YOSAKI_CHRONICLE_WORKSPACE`、`YOSAKI_CHRONICLE_CONTEXT`、`YOSAKI_CHRONICLE_PAID_ENABLED` 分别约束入口、聊天记忆与付费派发。A3 源码、表、页面和正式已审核字段是不同状态；读时继续检查来源、角色、人工审核和权限，阿夜记忆通道不得返回 RAW／CLEANED，展开到合资格完整精修正文。
- Voice 的 Profile/告知、V2 音频缓存/绑定、Scribe 日额和连续通话表已按各自受控记录迁移到日常库；`YOSAKI_VOICE_ENABLED=0` 仍会关闭相关能力，不能从表存在或路由注册推断服务开关、供应商能力或费用。H3.4 标题确认模块与 API 未由正式 `main.py` 安装。
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
| Memory V2／Chronicle | `/api/memory-v2/*`、`/api/memory-v2/workspace/*` | M1 可重建索引及受开关保护的用户管理、条目/时间树与记忆通道；付费任务另有关闭开关和逐次授权，详见执行计划 |
| 欲望与学习 | `/api/desires/{id}*`、`/api/desires/{id}/learning*`、`/api/desires/{id}/psychology*` | 驱力、意愿、自由活动、反馈、长期学习和模拟心理状态 |
| MCP | `/api/mcp/servers*`、`/api/mcp/servers/{id}/tools`、`/api/mcp/tools/{id}` | 服务保存、连接、刷新、工具目录和工具启停 |
| 插件 | `/api/plugins*` | 清单校验、安装、启停、逐工具授权和卸载 |
| 工具确认 | `/api/chat/tool-runs*` | 单次工具批准/拒绝、结果写入和模型继续 |
| Voice | `/api/voice/status`、`/api/voice/privacy*`、`/api/voice/profiles*`、`/api/voice/messages/*`、`/api/voice/scribe-draft/ws`、`/api/voice/calls*`、`/api/voice/realtime/output` | 档案、告知、朗读、草稿和通话；具体入口有独立开关、认证、额度及来源门槛，不能由路由存在推断可用 |
| 备份 | `/api/system/backup/export`、`/api/system/backup/import`、`/api/system/backup/verify` | V1/V2 加密备份与隔离校验；旧 import 返回 409，不直接恢复活动库 |

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
- V1 使用 PBKDF2-HMAC-SHA256、随机 salt 和 Fernet；H5 V2 使用相同 KDF 类别派生密钥，以 AES-256-GCM 对整文件认证加密，且默认关闭。
- 备份保留 owner 账户及其密码哈希；导出/恢复剔除活动 session、登录尝试、模型 API Key、MCP secret 和 MCP session ID，恢复后需用原账户重新登录并重新配置服务密钥。不要把“全量快照”理解为包含密钥。
- 旧 V1 数据库序列化上限为 64 MiB。H5-A 的 V2 大容量路径仍默认关闭；旧上传导入接口现安全拒绝直接替换活动数据库，V1/V2 须先恢复到隔离新目录。正式启用门槛见 `docs/H5A1_TARGET_RECOVERY_GATE.md`。
- 旧 V1 导入接口目前返回 409，不再直接替换活动数据库；V1/V2 都走隔离新目录恢复及校验。不要沿用旧版“写入临时文件并原子替换活动库”的描述执行恢复。
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
- `backend/app/voice/` 与 `frontend/src/voice/` 现覆盖档案与告知、V2 HTTP 消息朗读及缓存、V3 表达候选、Scribe 草稿、自动朗读和连续通话；`frontend/src/App.vue` 集成对应控件。日常库已按受控记录迁移 Voice V1/V2、Scribe 与连续通话所需表，普通 Development 和 Voice 专用入口的实际启用状态必须逐次核对。备份隔离副本剔除告知和运行态/音频缓存，不能把恢复后账户视为已重新同意第三方处理。
- 日常通话默认 HTTP，可手选 TTD WS；用户主动选择的耳机插话默认关闭。生成中打断接续、HTTP 播放停音与耳机插话分别有单次限定真实记录。日语自动语气只开放克制且默认关闭；两位置表达的真实 HTTP 合成获得用户一次“很自然”的声学反馈，真实 AI 规划仍未执行，实验能力未进入日常。两份超过 2000 字的真实长文首段结果 unknown，第二段未派发；不得自动重试或把它写成完整朗读通过。账单、固定 Benchmark、扬声器、跨设备和长用稳定性继续按 `docs/VOICE_DELIVERY_INDEX.md`、`docs/VOICE_FOLLOWUP_ROADMAP.md` 记录，历史故障及当日额度见第 54 节。

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
- Provider 语音与真实麦克风只有限定样本/场景记录；V1-B 整体、固定 Benchmark、扬声器回声、移动端、跨浏览器矩阵和长通话 QA 未完成。
- URL/ZIP 插件包装、插件市场和可执行插件。
- 真实 GitHub 备份与生产恢复演练、部署加固。
- Docker 镜像实际构建、VPS 迁移、HTTPS、反向代理和防火墙验收。
- 独立页面组件拆分；当前 `/chat` 等路径使用共享功能面板的全页模式，另保留 `/hub` + 浮动窗口。
- 角色卡导入的真实浏览器文件选择流程尚未在 QA 中执行；API round-trip/rejection 已有本地测试记录。
- `tests/` 和 `npm run test:backend` 覆盖隔离本地 API 与模拟服务，不能等同于真实第三方服务、设备或部署验收。
- H5 V2 大容量备份的正式启用门槛仍按 H5 专项核对，不能从 Chronicle 的受控增量接入推断整套 H5 已部署。H5-B 现已正式导入单份历史档案、130 条来源及 13 条管理草稿；13 份精修及13份梗概均可审，所有本批条目仍待 Owner 内容确认与发布。不得扩大为私人历史批量导入或已取得正式召回资格。
- Chronicle 的元数据、时间树、检索与页面已有正式样本证据；缺失的真实质量、日常付费加工和 H5-B 本批人工审核按 C1～C10 逐项报告。第 40～55 节中“未注册正式服务”的旧阶段语句不能覆盖当前 Runtime 加载状态。
- `README.md` 顶部与按日期追加的记录可能不同步；判断实现状态以当前源码、最新专项记录和相应隔离验收为准，发现漂移时一并修正。

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

1. **先读实现再修改**：先查 `main.py` 的安装点、对应 `memory_v2_*.py`/欲望系统模块及前端入口；App.vue 与独立 Chronicle 页面并存，避免误改隔离原型或重复逻辑。
2. **优先修改源文件**：改 `frontend/src/*.vue`、`main.ts` 和相应 Python 源文件；不要手工维护 `*.vue.js`、`main.js` 等生成产物。
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

# 后端模块语法检查
.\.venv\Scripts\python.exe -m compileall -q backend\app

# 隔离后端验收（临时 SQLite + 本地模拟服务）
npm run test:backend

# 后端运行后健康检查（另一个窗口）
Invoke-RestMethod http://127.0.0.1:8000/health

# 先看既有改动；不要对未提交工作执行批量清理
git status --short
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

## 30. Phase 6.5 整合测试与长期行为观察 — 2026-10-27

- 后端版本 `0.24.0`。`backend/app/psychology.py` 新增纯局部增量与纯关系传播函数，事件／快照写入仍由事务入口统一负责。`psychology-v2` 不再因工具或环境操作成功而默认给目标进展 .5；没有独立证据时 `perceivedProgress=null`。旧事件保留原评价和规则版本。关系审批最多允许 40 条启用边；超过上限返回 409，不静默截断传播。
- Phase 2／3 组装模型上下文时，已通过普通 `memories` 选中的学习记忆不再从 `longTermLearning.memories` 重复加入。`/api/desires/{id}/behavior/context-preview` 返回所选来源 ID、原因、长度及有界心理／学习参考，方便检查重叠；不会授权活动或工具。
- `backend/app/behavior.py` 提供最长 31 天的只读角色行为报告与 JSON/CSV 导出；模型调用表记录用途、时间、预留与可信实际输出 token，缺失用量保留空值。自由活动结束采样数据库分配字节数和关键表行数。指标展示分子／分母与样本不足状态，诊断提示不改变驱力、目标或偏好。`YOSAKI_OBSERVATION_MODE=1` 仅在隔离观察实例对 Phase 2 观察／判断、Phase 3 判断、Phase 4 回顾、Phase 6 评价设置每角色香港日合计 4 次模型调用上限；普通聊天与旧单步唤醒不在这套计量内。
- 已从正式阿夜数据库创建 `data/phase65-observation/yosaki.sqlite3` 的忽略副本。复制脚本拒绝覆盖，并清除 session、API/MCP 密钥、MCP 连接和备份计划；不复制 `.secret_key` 或 `.chatgpt_plan`。隔离实例需本机重新登录和手动授权套餐模型。绝不把观察副本提交 Git 或拿来替换正式数据库。
- `tests/test_phase65.py` 在临时数据库检验操作进展、跨入口去重、关系上限、学习记忆去重、每日调用上限、隐私报告，以及 30 个脚本模拟日的真实自由活动 worker／环境／反馈／学习路径。脚本模型不证明真实模型长期选择稳定。7 天真实观察需每天手动启动、审阅并报告缺口；目前没有完成七天，不能宣称长期行为验收。协议见 `docs/BEHAVIOR_OBSERVATION.md`。第三方 MCP、多进程调度、真实设备和公网部署仍须独立验收。
- 2026-10-27（香港日期）第 1 个真实观察日已在隔离副本手动完成：两次套餐模型活动判断、两次本地探索动作、一次体验回顾和一次心理评价，合计 4/4 次模型调用，可信实际输出 token 772/预留 2800。owner 已审阅并应用短期反馈；目标只记 `partial`，无自动长期偏好批准。六个观察日仍未完成。资源样本在回顾及反馈决定后更新；完整日记见 `docs/BEHAVIOR_OBSERVATION.md`。
- 2026-10-28（香港日期）第 2 个真实观察日继续同一候选兴趣，执行一次本地地图与笔记对照后自主 `finish`；两次活动判断、一次体验回顾和一次心理评价共 4/4 次调用，可信实际输出 token 827/预留 2800。反馈应用时驱力版本冲突，重新计算经 owner 二次复核后应用；同源驱力调节及心理反馈事件各一条，目标仍为 `partial`，长期偏好未自动批准。余下五个观察日，不能宣称七日稳定性已验收。

## 31. Memory V2 M0.7 最小修复 — 2026-10-28

- Phase 5 已接受学习记忆是权威，普通 `memories` 投影经 `backend/app/memory_authority.py` 校验后才进入聊天、Phase 2／3 和 Phase 6.5 上下文。资格过滤先于排序与截断；合法合并只入选一次，异常绑定仅记录不含正文的诊断。普通记忆接口对有关联的投影返回 409，人工记忆维持原 CRUD；Phase 5 归档仍可用。
- 工具继续入口先验证原用户回合、助手批次、工具调用 ID 与结果，再改变批次状态；有效链使用原用户正文作记忆话题，断链返回 409 且不发起模型请求。此修复不新增表、模型调用、学习审批或工具执行器。
- 回归只在外部源码副本、临时 SQLite 和本地模拟服务中执行。Memory V2 M1 未实施；正式数据库、真实凭据及私人聊天未参与此次测试。

## 32. Memory V2 M1 原生只读档案 — 2026-10-28

- `backend/app/memory_v2_schema.py`、`memory_v2_sources.py`、`memory_v2_index.py`、`memory_v2_api.py` 实现双来源派生索引。原始消息和活动操作继续权威；M1 只存可重建哈希、词项、时间片段及任务游标。首次索引必须显式 `rebuild`，后续可分批 `incremental`，不在聊天读取时隐式写入。
- 只读 API 每次核对原始来源的角色、状态和指纹；不索引推理、独白、工具参数或结果载荷。导入消息发生时间未知；活动未知结果不推断成功。时间片段不是语义话题。旧注入链和 Phase 1～6.5 的模型、工具及审批行为不变。
- 未来 cleaned、阿夜第一人称 narrative 与分级 digest 使用有序原始来源引用，属于 M3 预留，本阶段没有精修表或 LLM 任务。完整契约及回滚见 `docs/MEMORY_V2_M1.md`；M1 隔离测试见 `tests/test_memory_v2.py`。

## 33. Memory V2 M2 隔离召回 — 2026-10-28

- M2 新模块仅提供内部只读召回、候选来源校验、安全原文节选和影子对照；没有在 `main.py` 注册正式请求旁路或模型调用。人工长期／本会话临时记忆及 Phase 5 有效投影仍是正式参考，M1 消息与活动是历史证据，不因召回而获得学习审批。
- 仅 `RAW` 视图在 M2 实际产生；候选契约预留四视图和有序来源。被动冷却由调用方临时模拟，不写数据库。M3 精修与 M5 可视化仅在 `docs/PLAN.md` 排期，未实现。
- M2 验证只在外部源码副本、临时 SQLite 与本地模拟请求中进行；正式数据库启用、真实用户对话旁路和正式上下文切换均须另行批准。固定评测和后续切换门槛见 `docs/MEMORY_V2_M2.md`。
- M2.3 扩展测试曾发现删除真实用户回合消息后，旧 R2 `verified_tool_turn()` 可能回退到更早用户消息；R2.1 已在隔离副本修复并重新验收。此前未覆盖此夹具的 46 项通过记录不能替代修复后的回归。

## 34. Memory V2 R2.1 持久锚点与 M2.4 隔离验收 — 2026-10-28

- 工具助手批次在 `messages` 中持久保存原用户消息 ID、正文 SHA-256 与角色 ID。首次工具批次与工具运行同事务写入；后续批次继承并重新验证同一来源。旧待继续批次没有可证明锚点时返回 409，不能从剩余历史推断；已完成旧历史仍能展示。
- 继续前验证来源、角色、会话、正文、调用 ID、结果及消息链；SQLite 即时事务与条件状态更新阻止双请求同时占用。继续期间，应用删除对话／角色、重新生成和插入新用户回合被拒绝；发起模型流前再次核对锚点。拒绝场景不新增模型或工具请求。
- 外部源码副本、临时 SQLite、本地模拟服务中的完整回归为 58/58 通过；M2.4 固定 10 场景评测为 5 个预期命中均命中，0 误命中、0 漏命中，Token 仅是字符估算。测试范围及正式切换方案见 `docs/MEMORY_V2_R21_M24.md`。
- 正式数据库仍未迁移或部署。新版后端导入会运行 `init_db()` 补列，因此启动正式服务前须完成独立加密备份、恢复、迁移幂等、权限及回退验收。M2 模块没有注册正式旁路，M3 未开发。

## 35. Memory V2 三域架构与 M4／M5 路线 — 2026-10-28

- [三域架构与未来路线](docs/MEMORY_V2_ARCHITECTURE.md) 是目标设计，不代表已经实现。Chronicle／溯时流源管理事件与表达证据的索引及忠实派生视图；Personal Notebook／心潮念是独立的主动个人笔记；未来 Experiential Memory 仅关联 Phase 1～6.5 原权威记录。M2 作为跨域只读召回协调层，Phase 5 继续批准正式长期学习。
- M3 继续既定 RAW／CLEANED／NARRATIVE／DIGEST 忠实精修，不推断阿夜未记录的内心，也不凭第一人称文风认定亲笔笔记。本次修订不扩大 M3，不改 M1/M2、R1/R2.1 或 Phase 1～6.5 行为。
- M4 关联须保留关系类型、来源证据、验证状态；原系统显式关联、时间接近、未经核实候选与明确确认分别标注。发生顺序或模型推断不能自动成为确定因果。未来来源契约还须分开表示来源性质、执行结果、模拟／现实边界、批准状态及来源有效性。
- 心潮念连接器尚不存在。未来关联、查看、召回、上下文注入分别授权并可撤销；旧关联不授予永久全文权限，失权或来源失效后不得返回旧正文。笔记不默认进入 Chronicle 摘要、训练或 Phase 5 学习。M4/M5、正式数据库迁移和正式上下文切换均须另行批准。

## 36. Memory V2 M3 隔离派生模块 — 2026-10-29

- 当前源码新增派生生命周期、认证 API 安装器、保守 CLEANED、可追溯第一人称引述、时间投影/摘录摘要和显式本地任务。此前“M3 未开发”是旧阶段记录；当前能力与限制见 [M3 契约](docs/MEMORY_V2_M3.md)。
- `main.py` 未注册 M3 API，也未调用派生/任务 schema 初始化。不得直接启动正式服务代替隔离验收，不得把 M3 接入正式上下文或调用付费模型。
- validation、review、lifecycle 分开；NARRATIVE/DIGEST 必须服务端身份绑定的人工审核。人工审核不提升为亲笔笔记或 Phase 5 正式学习。CLEANED 自动激活须重新运行版本化规则校验正文，不能用状态字符串替代凭据。
- 测试使用外部源码副本、合成临时库和模拟服务。未知翻译、无法证明的自由改写和超预算摘要保守拒绝或保留原文；目前不具备通用语义精修、真实模型费用控制或 M5 界面。正式迁移、部署、上下文切换及 M4/M5 仍需独立批准。

## 37. Memory V2 M3.5 隔离模型适配 — 2026-10-29

- M3.5 仅在未注册的安装式 API 显式开启任务入口，并使用本地 `LocalMockAdapter`。任务持久保存请求身份、来源引用、输入指纹、逐批状态、模拟 token/费用预算与私有派生输出；不保存第二份 RAW。可能派发后失联为 unknown，绝不自动重试。
- 模型 NARRATIVE/DIGEST 只产生 needs_review 草稿。来源映射不能证明自然语言忠实；人工审核仍绑定正文及原始来源，系统派生内容不能成为心潮念亲笔或 Phase 5 正式学习。当前 tokenizer/费用单位仅用于合成工程测试，不得报告为真实供应商费用。
- 未接真实 Provider、正式数据库、聊天上下文或 M4/M5。正式启用前继续执行独立备份恢复、迁移、权限及费用验收门槛，见 `docs/MEMORY_V2_M3.md`。

## 38. Memory V2 M3.6 零付费适配 — 2026-10-29

- M3.5 已隔离工程验收，真实模型能力待验证。M3.6 仅在未注册模块中增加固定 OpenAI Responses 协议适配、严格结构化输出、官方输入计数预检契约及独立美元预算账本；零付费测试使用 HTTPX `MockTransport`，没有读取正式 API Key 或访问 Provider。真实传输配置零自动重试，但未获收费运行授权。
- 原插件提示词保留 speaker 与第一人称核心规则，私人示例由虚构示例取代；原插件自由文本变体只评测，不写可激活派生版本。M3.5 变体仍须人工忠实性审核。详见 `docs/MEMORY_V2_M36.md` 与评审模板。
- 正式数据库、真实聊天、付费调用、上下文切换、M4/M5 均未启用。M4 经验关联桥接不依赖 M3.6 真实模型上线，但实施仍需独立授权。
- 2026-10-30 首轮真实质量测试仅完成待审核准备：合成 N1、同模型两次串行生成、Standard 服务层级与 standard/medium 推理模式、建议 $0.10 总上限；更新后的外部副本 M3.6 专项 15/15 通过。没有执行真实输入计数或生成。详见 `docs/MEMORY_V2_M36_FIRST_TRIAL.md`；确切模型、测试 Key 范围、样本和预算须另获明确批准。

## 39. Memory V2 N1 真实试运行与 NARRATIVE 文体修订 — 2026-10-31

- 上节记录的是试运行前状态。获单独授权后，仅合成 N1 在外部隔离副本完成 2 次输入计数及 2 次 `gpt-6.1-sol` Standard/medium 生成；费用为基于 Provider 用量和价格快照的 USD 0.005830 保守估算，不是最终账单。新版产物仍是 `needs_review`，未进入正式数据库或上下文；详情见 `docs/MEMORY_V2_M36_FIRST_TRIAL.md`。
- 根据原插件编写规则，M3.5 NARRATIVE 的目标修正为阿夜第一人称高保真对话纪事。模型输出仍按轮保存有序来源映射，但可见正文不嵌 `[message:id | speaker]`。当前 v3 提示词及无模型引述只用“我说／她说”归属，派生正文拒绝中日混合前缀；RAW 原文不改。详细正文优先保留表达、发言归属、日语、条件、否定和更正；梗概和时间 DIGEST 独立评审压缩。旧 v1 真实输出不追溯更改；修订的 v3 只通过合成无网络回归，尚未经过真实模型质量验收。
- 2026-10-31 的测试在外部源码副本和临时 SQLite 运行：新文体专项 5/5、Memory V2 回归 143/143、R2 工具回合 10/10。`main.py` 未注册 M3 任务或正式旁路；本次没有新表、正式迁移、额外付费请求或 Phase 1～6.5 行为修改。历史段落如与此节冲突，以当前源码和本节为准。

## 40. Memory V2 P1-A 单经历元数据隔离增量 — 2026-10-31

- 新增 `memory_v2_entries.py` 和未注册的 `memory_v2_entry_api.py`，在 `memory_v2_derivation_schema.py` 显式初始化条目、元数据版本、来源快照、人工审核和拆合谱系表。条目 ID 不依赖可重建时间片段；NARRATIVE 正文仍由 M3 版本持有，条目仅存受来源约束的引用。无模型任务、正式路由或旧注入链改动。
- 元数据审核必须绑定版本、来源与纪事；正文或来源失效、角色失权、纪事版本替换时，正式条目读取与管理预览拒绝旧内容。标题、关键词、标签、梗概、来源、纪事和时间各有独立依赖 token；当前没有把元数据写入旧索引或摘要。拆分／合并先提出新条目，所有子条目经审核激活后才显式停用父条目；取消保留谱系审计。
- `docs/MEMORY_V2_FEATURE_PARITY.md` 是 28 项功能的持续台账，分别记录设计、隔离测试、用户可见验收及正式接入。P2、M4/M5、正式数据库迁移、上下文切换和付费／向量服务未获本次授权。测试须先设置临时 `YOSAKI_DATA_DIR`，不得让 `main.py` 导入正式数据目录。

## 41. Memory V2 P2 Chronicle 时间树隔离增量 — 2026-10-31

- P1-A 最终源码实施前完整隔离回归 201/201。P2 在未注册的 `memory_v2_time_projection.py`、`memory_v2_time_api.py` 中统一实现 browse 与内部 `context_projection` 的 L0～L5 时间视角；不修改 M1 来源、M3 派生正文或 Phase 1～6.5 权威状态。只在显式隔离初始化时添加 `mv3_time_digest_bindings`，保存摘要范围指纹与现有 M3 DIGEST 版本的关联，不存第二份原文或摘要正文。
- 动态换档使用原插件 7／30／90／180／365 天边界和固定时区偏移的 ISO 周；父级摘要只覆盖本级成员，下级摘要为显式依赖。只有来源、角色、审核、版本与范围都有效的摘要可进入只读投影。没有摘要则明确标记并退到已审单经历梗概／标题；历史 RAW 可逐页回查。GET 不写库、不自动排任务或调用模型。
- P2 仅处理已经由未来 M2 选定的候选，不能主动检索、浮现、执行或更新冷却。`main.py` 未注册 P2；正式数据库、上下文切换、M5-A 前端及付费／向量服务仍未启用。接口、合成时间树与边界见 `docs/MEMORY_V2_P2.md`，28 项状态见 `docs/MEMORY_V2_FEATURE_PARITY.md`。
- P2 专项隔离测试 23/23，最终 `unittest discover -s tests -q` 全套 224/224 通过。初版测试断言及夹具存在三处与实际时间／选择规则不符，已修正并重测；摘要后续页另补版本 ID 锚定。测试期间正式 SQLite 文件修改时间保持不变，未调用付费模型或向量服务。

## 42. Memory V2 M5-A0 只读年历隔离原型 — 2026-11-01

- `frontend/src/main.ts` 仅在 `/chronicle` 且 `VITE_YOSAKI_CHRONICLE_A0=1` 时加载 `ChronicleApp.vue`。独立测试脚本 `scripts/chronicle_a0_demo.py` 在系统临时目录初始化合成 SQLite，并显式安装 P2 与 `memory_v2_calendar_api.py` 的只读路由；正式 `main.py` 不注册年历 API。不得用该脚本连接正式数据库。
- 页面可浏览 L0～L5、已审周期 DIGEST、单经历标题／日期／关键词／梗概、已审 NARRATIVE/CLEANED 与 RAW 并排、关键词／时间筛选及长文分页。GET 每次核查当前角色、来源和派生版本；未审核草稿不进入正式读取，版本变化后的后续页返回 409。隔离 demo 中的登录账户和内容均为合成数据。
- P2 的 `context_projection` 仅在 M2 已选中周期全部成员时选周期 DIGEST。未来 P3/P4 若要让摘要参与浮现，必须由 M2 明确选取周期候选并完成相关性、权限、去重和预算检查；P2 不得自行扩源。本原型不接正式上下文、付费模型、向量服务或 Phase 1～6.5。运行与剩余管理能力见 `docs/MEMORY_V2_M5A0.md`。
- M5-A0 新增 API 专项 6/6，通过完整隔离后端回归 230/230；开启／关闭页面开关的前端构建均通过。合成浏览器中已验证六档、长 RAW 分页、来源修改后 409 清屏和 390×844 视口；正式 SQLite 修改时间未变。此验收不能代替真实用户数据、正式数据库迁移或正式服务权限验收。

## 43. Memory V2 M5-A1 隔离年历阅读与投影预览 — 2026-11-07

- 在现有 `/chronicle` 可关闭页面中增加年／月／周期层级及 ISO 周日期范围。规则式 DIGEST 仍是「摘录式摘要」；隔离 API 只有在当前来源与派生正文逐字匹配时才把阅读文字与可展开的机器来源元数据分离。来源失效计数移到状态诊断区，当前查看项失效仍须显著提示并清空旧正文。
- 新增只读计算 `POST /api/memory-v2/calendar/projection-preview`，只接收显式选中的条目，直接复用 P2 `context_projection()`。它返回来源／版本、资格、摘要退路、估算 Token 与预算拒绝原因；不得称为正式聊天请求预览，不写冷却，不接正式模型。安装式接口仍只由临时 SQLite 演示应用注册，正式 `main.py` 不安装。
- 功能、截图、隔离测试与剩余风险见 `docs/MEMORY_V2_M5A1.md`。28 项台账仍独立记录设计、隔离测试、用户可见原型和正式接入；A1 不宣称已完成向量、正式浮现或管理功能。正式数据库、私人聊天、付费模型、P3/P4/M4 仍未获本轮授权。
- A1 专项文件 17/17（含继承基线），完整隔离后端 247/247；Python 编译和前端开关开启／关闭构建通过。合成浏览器实际验证层级展开、来源元数据、三条候选形成两块投影及明暗模式。正式 SQLite 修改时间保持 `2026-10-26T12:13:36.0827151Z`。

## 44. Memory V2 H1～H4 合成历史聊天导入流水线 — 2026-11-07

- 新增隔离安装式导入预览、来源收据、原子分批 RAW 导入、人工边界、显式索引和 P1-A 草稿映射，并在可关闭的 Chronicle 页面加入待整理 RAW 与导入向导。正式 `main.py` 不安装新 API，也不自动执行 `memory_v2_import_schema.init_db()`。测试必须先设临时 `YOSAKI_DATA_DIR`；不得拿正式 `data/` 演练。
- 历史对话 `archive_only=1` 的服务端消费者审计见 `docs/MEMORY_V2_IMPORT_AUDIT.md`。普通聊天列表及按 ID 的读取、发送、重新生成、工具继续、旧导出拒绝归档会话；Phase 1～6.5 不从归档消息生成事件。来源收据保留外部／本地 ID 区别、时间原文／精度、文件 SHA、解析器版本与转换说明；仅日期与无依据的时区不伪装精确时刻。MD/TXT 明示有损，ZIP 有路径和容量限制。
- 未整理 RAW 可独立查看，人工片段只建立草稿；P1-A 原有 NARRATIVE 审核激活门槛保持。大规模正式导入仍须另批通过 H5-A1 容量、安全恢复、迁移、权限与回滚验收。详见 `docs/MEMORY_V2_HISTORICAL_IMPORT.md` 与 28 项功能台账。真实私人聊天、正式数据库与付费模型均未参与。
- 最终合成验收：外部源码副本专项 14/14、临时数据库全套后端 261/261、Chronicle 开关两种前端构建，以及 localhost 浏览器预览→RAW→索引→两条稳定草稿的交互流程。导入 schema 不改变原生消息 M1 指纹；无时区时间不会按导入日进入事件年龄档位。正式数据与密钥文件修改时间未变。

## 45. Memory V2 H5-A 大容量备份隔离开发 — 2026-11-07

- `backend/app/backup_v2.py` 只通过 SQLite 在线快照读取源库，快照副本内剔除凭据和活动会话，产生整文件认证的 V2 `.ybackup`；`scripts/restore_backup_isolated.py` 只能恢复到新目录，不能替换活动库。旧 V1 文件仍可在独立目录验证。不要将备份密码放在命令行参数、普通日志或备份文件旁。
- 新 V2 API 仅在 `YOSAKI_H5A_ENABLED=1` 时开放，正式服务与数据库未获启用授权。旧 `/api/system/backup/import` 返回 409；备份后的新数据不可被整库回退覆盖。定时本地大容量备份与浏览器原生下载仍须在正式部署前验证存储空间、权限、磁盘加密与恢复演练。详情见 `docs/H5A_LARGE_BACKUP.md`。
- H5-B 真实历史小样本仍待单独授权；不得因 H5-A 代码存在而读取私人聊天或启动正式历史导入。

## 46. Memory V2 H5-A1 目标环境安全门槛 — 2026-11-07

- 在仓库外冻结 76 个 Python 与 28 个前端源文件；完整隔离后端回归 273/273 和前端构建通过。合成库可在新目录、新测试密钥下恢复并读取普通业务数据；恢复库哈希只对应脱敏后快照，不代表原库逐字节副本。
- 默认新建 H5 工作目录 ACL 受保护，但已有宽权限目录不会自动收紧。正式启用还缺上传/验证配额、并发和超时、多 worker 票据、目标容量与空间演练，以及同进程暂停后反馈扫描器恢复检查。详情与 H5-B 最小数据范围见 `docs/H5A1_TARGET_RECOVERY_GATE.md`。
- H5-A1 不批准正式数据库部署、活动库切换、私人历史导入、模型调用或向量服务；H5-B 仍需独立授权。

## 47. Memory V2 H5-A2 隔离准入加固 — 2026-11-07

- H5 V2 入口默认关闭。启用后，预存工作目录也必须通过 Windows ACL 与加密卷检查；不可证明时拒绝。备份请求在 multipart 解析前限额，手动和定时重型操作共用配额，且下载票据仅允许单 worker、同 session、单次 POST 使用。准备和验证支持取消与超时；不安全的 503 不降级到 V1。
- `backup_v2.py` 中 16 GiB 是格式理论界限；当前 H5-A2 入口临时限制为数据库含 WAL 1 GiB、上传文件 1 GiB + 64 MiB。没有可用加密测试目录，目标卷容量及正式启用**未通过**。隔离恢复脚本要求受保护工作目录和其直接子目录中的新目标；活动库替换仍禁止。验收及剩余风险见 `docs/H5A2_FORMAL_GATE.md`。
- H5-B 私人历史文件读取、正式库迁移与恢复、模型或向量服务、正式上下文切换均未授权。Phase 6.5 同进程暂停后恢复仍为独立待办。

## 48. Memory V2 H5-B 首份文件 H1 只读适配 — 2026-11-07

- 用户已单独授权读取其指定的一份历史 Markdown，仅用于 H1 只读预览。新增 Chronicle 专用括号角色标记解析器；旧 `archives.py` 行为不变。预览与未来暂存共享版本化解析结果，确认需匹配来源、角色和预览指纹。
- 隔离 H1 预览读出 130 条，时间均无时区依据，第 2→3 条倒序保留；没有 RAW 写入或正式库访问。H2、真实历史批量导入、正式迁移、模型与上下文切换均未获本轮授权。详情见 `docs/MEMORY_V2_HISTORICAL_IMPORT.md`。
# P1-A2 补充（2026-11-08）

角色停用后的列表分页信息泄漏已定向修复：列表先在同一读事务中核对 owner、角色存在性和启用状态，再查询和分页；详情入口也在条目读取前校验。保留原失败测试，新增停用／不存在／跨角色／撤权分页回归。最终源码专项 26/26、完整后端隔离回归 320/320、前端构建通过；早期309项不作为最终验收依据。正式 main 未注册该路由。

渐进元数据实现见 `docs/MEMORY_V2_P1A2.md`。只在外部合成数据库显式调用 metadata.init_db；不要因导入模块或启动正式 main 自动迁移新表。v2 字段集合属于既有 mv3_entries UUID，v1 审核不转授给新字段。时间证据、发生时间与记录时间分开；formalContext 始终关闭。真实 H5-B 库和快照不作为测试夹具。完整回归结果以本阶段文档记录为准。

## 49. 当前项目复核 — 2026-11-08

- 工作区 `main` 有大量未提交、未跟踪的 Memory V2、H5、Chronicle、脚本、测试及文档改动。本节按当前工作树核对，不表示这些改动已提交、部署或获正式启用批准。运行数据和私人样本不得被测试、清理或提交操作误触。
- `main.py` 当前版本仍为 `0.24.0`，启动时执行 `memory_v2_schema.init_db()`，并安装 `memory_v2_api` 的 M1 显式索引任务及来源读取接口。M2 召回、M3 派生、P1-A/P1-A2 元数据、P2 年历及历史导入仍需对应隔离安装与数据门槛；正式聊天上下文没有切换到 Memory V2。`frontend/src/main.ts` 仅在 `VITE_YOSAKI_CHRONICLE_A0=1` 时加载 `/chronicle` 独立页面，合成演示脚本为 `scripts/chronicle_a0_demo.py`。
- H5 V2 备份入口由 `YOSAKI_H5A_ENABLED=1` 控制，目标工作目录需通过 ACL 与静态加密验证；目前的门槛、容量上限和独立恢复约束以 `docs/H5A2_FORMAL_GATE.md` 为准。旧 `/api/system/backup/import` 现返回 409，禁止直接替换活动库。`docs/H5B_REAL_SAMPLE_REPORT.md` 已记录单份私人文件的 H1 预览、H2 隔离 RAW 导入和 H3.1 人工区间确认；H3.2 在真实隔离库遇到 M1 成员顺序问题而停止。当前源码已针对顺序与游标添加修正和合成回归，但本次没有重跑真实 H3.2，不得写成已通过或已经生成 P1-A 草稿。
- P1-A2 角色撤权分页修复按 `docs/MEMORY_V2_P1A2.md` 记录完成隔离工程验收；其元数据 API 未注册正式 `main.py`。`README.md` 的 H5-B 小节仍写成仅 H1 预览、H2 待审，属于旧阶段叙述；较新的 H5-B 报告与源码优先，后续维护应同步修正该处。第 45～48 节是当时的阶段记录，不能覆盖本节的后续状态。
- 本次重新运行 `npm run build`、`.venv\Scripts\python.exe -m compileall -q backend\app` 和 `.venv\Scripts\python.exe -m unittest tests.test_memory_v2_order -q`：前端构建、后端编译及 M1 顺序专项 5/5 均通过。未运行耗时较长的完整 `npm run test:backend`，也未重做真实 H3.2、正式数据库/密钥、浏览器、第三方 provider/MCP、目标加密卷或 VPS 验收。此前 320/320 等数字属于各阶段的隔离验收记录，不应作为本次复跑结果。

## 50. M1 历史来源顺序隔离修复 — 2026-11-08

- 本节更新第 49 节的测试状态：在外部源码副本、合成 SQLite、清除凭据并禁止外部网络的运行器中，最终 M1 顺序专项 **7/7**、P1-A2 元数据专项 **22/22**、完整后端隔离回归 **327/327** 通过；`npm run build` 与相关后端编译通过。此前一次完整回归为 326/327：收据原始时间被修改后，M1 先按状态指纹返回 `source_missing_or_stale`，旧测试预期 P1-A2 的 `time_evidence_changed`。确认 RAW 仍存在且旧索引失效后，只增强该测试的独立断言，未降低业务来源校验；最终全量重跑通过。
- M1 schema v2 对已完成 H2 文件核验批次清单、文件身份、SHA、解析器、单一会话、收据数量及连续 ordinal；同一文件／会话按整数 ordinal 构建片段，异常收据和未完成文件暂停索引。倒退的可信精确时刻记冲突，不重排、不切段；未知发生时间不借导入时间计算间隔。M1/M2 搜索相关性不采用 ordinal。
- M1 搜索与片段游标采用服务端稳定密钥的域隔离 HMAC；续页重新核验来源快照。旧 v1 索引对新顺序读取显示 `needs_rebuild` 并安全拒绝；旧库升级只能显式调用迁移，再显式重建，普通 `main.init_db()` 不自动升级旧排序 schema。
- 本次没有打开、迁移或重建 H5-B 真实隔离库及其快照，没有重新导入私人 RAW 或创建真实 P1-A 条目。H3.2、正式数据库迁移和上下文接入继续等待独立审核。契约见 `docs/MEMORY_V2_M1.md`，现场状态见 `docs/H5B_REAL_SAMPLE_REPORT.md`。
## 51. Voice V1-A 当前代码状态（2026-11-08）

`backend/app/voice/` 已提供默认关闭的配置、角色 Voice Profile、第三方处理确认和两条备份路径共用的隔离副本剔除策略；`frontend/src/voice/` 提供配置界面，原设备语音保留。当前仅做隔离数据目录下的 V1-A 代码验收，未授权真实 ElevenLabs API 测试、正式库迁移、消息朗读或实时通话。`POST /api/voice/profiles/{id}/test` 目前拒绝真实调用。任何测试导入 `backend.app.main` 前必须设置临时 `YOSAKI_DATA_DIR`；完整边界见 `docs/VOICE_SYSTEM_PLAN.md`。

## 52. H5-B H3.2 真实样本隔离草稿 — 2026-11-08

- 本节更新第 49、50 节当时的 H3.2 待办状态。只从 SHA-256 为 `EE30A47AE8F5513EF3B9CDF213D29014F3DD8AFAEBD8F47F28AFC5C8243835D4` 的 H3.2 执行前快照以 SQLite 在线备份建立新工作库；旧 H2 库、第一次错序现场及全部历史快照保持不变。工作库位置及 13 个 UUID 见 `docs/H5B_REAL_SAMPLE_REPORT.md`。库中含真实私人 RAW，不能复制进仓库、测试夹具或日志。
- 仅在新工作库显式运行 M1 schema v2 迁移和完整 rebuild：130/130 来源有效，130 个成员按原始 ordinal 1～130 排列；全部 `occurred_at=NULL`，未用导入时间进行 30 分钟发生时间切分。绑定经 owner 本轮明确确认的 13 个区间和当前来源快照，在单事务内创建 13 个 P1-A2 草稿。标题保留系统建议属性和待审状态，梗概／纪事／分类／Tag／Keyword／周期摘要缺失；不具备正式召回或上下文资格。来源映射及指纹保存在 ACL 保护的工作目录，不含私人正文。
- `backend/app/memory_v2_import.py` 的 H3.2 专用操作核对完成文件、收据、角色、来源和索引指纹；`memory_v2_import_schema.py` 记录边界确认审计；`memory_v2_metadata.py` 区分系统建议标题与人工标题。上述操作未注册正式 `main.py`。重复确认返回原 13 个 UUID；合成事务异常测试验证整组回滚。H3.1 旧快照指纹作为审计出处，实际本轮有效性绑定新工作库的完整来源快照；未重新读取或校验原 Markdown 字节。
- 本轮停止在 H3.2。不得自动加工元数据、精修、摘要、向量、正式召回或迁移。最终源码在外部隔离副本通过 H3.2 专项 3/3、完整后端隔离回归 337/337、前端构建与后端编译；20,000 条游标快照性能仍未验收。这些结果不等于正式数据库或用户可见浏览器验收，详情见 H5-B 报告。

## 53. 当前项目复核 — 2026-11-09

- 仍以当前未提交的工作树为准：`AGENTS.md`、README、核心后端/前端和大量 Memory V2、H5、Voice 文件尚未提交。只核对、编辑本指南；不要整理或覆盖既存改动，私人 H5-B 数据与正式 `data/` 不作测试夹具。正式后端版本仍为 `0.24.0`。
- H5-B 已按第 52 节在独立工作副本完成 H3.2 的 13 个渐进草稿；原 H2 库和旧快照未被替换。新 `memory_v2_h34.py`／`memory_v2_h34_api.py` 提供 **H3.4 标题确认的隔离实现**：要求当前 owner session、已确认边界和来源快照、13 个预期版本/字段 token，以单一事务确认并检查幂等。正式 `main.py` 未安装该 API；本次只跑合成测试，没有把真实 13 个标题标为已审，也没有开展后续纪事、摘要或正式召回。
- Voice V1-A 后端 `/api/voice/*` 已在正式应用注册，但 `YOSAKI_VOICE_ENABLED=0` 时不迁移 Voice 表；设置页与原设备语音并存。`ELEVENLABS_API_KEY` 的本地布尔状态已有记录为已配置，默认 Voice ID 仍为空，真实 ElevenLabs 请求为零。V1-B 固定测试单、IVC 日语/中文评测、账户费用和授权条件见 `docs/VOICE_V1B_DRY_RUN.md` 与 `docs/VOICE_V1B_LOCAL_SETUP.md`；目前没有授权执行真实探针。`backend/requirements.txt` 已加入 `websockets==17.2`，部署示例新增默认关闭的 Voice 环境变量。
- 本次重新运行 `npm run build`、`.venv\Scripts\python.exe -m compileall -q backend\app`、Voice 专项 `test_voice_v1.py` **9/9**、H3.4 专项 `test_memory_v2_h34.py` **5/5**，均通过。未复跑完整后端套件，未启动真实供应商请求，也未重做 H5-B 私人数据、浏览器设备、VPS 或生产恢复验收。第 49～52 节与 README 中的历史阶段记录按其日期理解；较新的状态以本节和对应专项文档为准。


R2-C 准备更新（2026-11-10）：`YOSAKI_BACKGROUND_TASKS_ENABLED=0` 在任务创建前关闭反馈、心理状态、定时备份的自动 startup 轮询；未设置默认 1 保持既有入口兼容，非法值拒绝。Development 专用入口强制设置进程内值 0；生命周期及 shutdown 保留。此开关不禁止用户显式业务 API。专项 4/4、完整隔离后端 349/349 通过；正式日常库初始化与服务切换尚未执行。执行与回退顺序见 `docs/R2C_EXECUTION_CHECKLIST.md`。

## Work 本机交接探针 — 2026-11-14
独立合成探针位于 scripts/work_handoff_probe/；仅回环访问、固定测试文件和内存收件，浏览器往返已验证。尚未连接当前桌面会话，不得将回执称为 Codex 收件。无模型、语音或日常数据库操作；细节见该目录 README.md。

## 54. 当前项目复核 — 2026-11-16

- **工作树与运行边界**：本次核对时仓库仍有大量既存已修改、未跟踪的源码、测试及文档；以当前文件和带日期的验收记录判断，不能用 Git `HEAD` 或旧 README 顶部概览代替。`backend/app/main.py` 的 FastAPI 版本仍是 `0.24.0`。`main.py` 导入即执行 `init_db()`；后端测试先设独立 `YOSAKI_DATA_DIR`，运行服务使用 `scripts/yosaki_runtime.py` 对应的受保护入口。R2-C 的日常库初始化、Development 切换和真实登录已于 11 月 10 日完成；旧“等待 R2-C”的句子仅是历史。当前运行实例是否加载最新源码须按进程命令、环境、数据库实际路径和健康检查重新确认，不能从文件修改时间推断。
- **Memory V2**：正式安装点仍只有 M1 索引与只读来源 API。`backend/app/memory_v2_*.py` 与 `tests/test_memory_v2_*` 已扩至 A3 分类、Tag、词项、梗概、多批最终整合、限定词校验和维护接口；`docs/PLAN.md` 11 月 13～16 日记有多次获批候选的限定源码合入及外部冻结副本回归。尤其 A3.4 的 v3 限定词策略合入只做正式工作区字节/静态检查，外部专项 139/139 不是当前全部工作树的回归。新 profile 的真实成功端到端、正式路由启用、私人历史加工、人工审核、激活和发布仍无对应通过记录；原真实失败/拒绝任务和未知费用保留原状态。H5-B 13 个 H3.2 草稿、H3.4 标题确认隔离实现不自动升级为正式 Chronicle 或聊天记忆。
- **Voice 数据与功能**：V1/V2、Scribe 及连续通话表已有各自受控日常库迁移记录。`main.py` 无条件安装 V4 输出 WS 路由，动态聊天、候选、Scribe 和连续通话由各自环境开关控制；“路由注册”不等于功能可用。`scripts/start_yosaki_voice_daily.ps1` 与配套前端脚本显式启用日常主动录音、自动朗读能力和连续通话，同时保持后台业务任务关闭。用户的自动朗读选择仍默认不勾选；连续通话须主动确认最终转写自动发送，单通话最多两轮、180 秒，完整回复后才 HTTP 合成播放，不是边生成边播。每日额度由服务端账本约束，缓存播放不派发新合成。
- **Voice 最新实测和故障**：V2-A 已保存消息的中文/日语朗读、V3 一条 `thoughtful` 日语消息、独立实例的一批两轮连续通话各有范围有限的真实记录。日常连续通话随后有一次用户消息已保存但助手未完成：Voice 日常启动脚本漏设 `YOSAKI_CHATGPT_PLAN_ENABLED=1`；11 月 16 日零外呼修复后受保护服务已交接，但没有修复后的真人通话复验。该对话不自动补发；服务端在未完成回复的对话及当日 Scribe 额度不足时阻止新通话。当时 Scribe 当日 10 次/10 连接/150 秒预留已用尽，自动朗读已提至每日 20 任务/40 HTTP/4000 字且当时剩余 16/36/3907；这些是历史快照，下一次操作先读实时额度。详情以 `docs/VOICE_SYSTEM_PLAN.md` 第 0.56～0.68 节为准。真实账单、V1-B 整体、V2 七段 Benchmark、扬声器回声及 V5 稳定性仍待验证。
- **本次检查与验收读法**：重新运行 `npm run build`、`.venv\Scripts\python.exe -m compileall -q backend\app`、隔离 `test_voice_continuous_daily.py` **8/8** 和 `git diff --check -- AGENTS.md`，均通过。各专项数字、冻结 SHA 与外部副本结果只对其当时清单负责；本次文档更新不重演私人 H5-B、真实模型/ElevenLabs 请求、正式库迁移、设备或 VPS 验收。`docs/RUNTIME_MODES.md`、`README.md` 与 `docs/VOICE_SYSTEM_PLAN.md` 含按时间累积的历史段落，阅读时优先最新执行记录和实际安装点；发现冲突须修正文档，而不是扩大验收结论。


### Memory V2 第3轮接入状态（2026-11-16）
已按Owner受控授权执行一致性备份、隔离恢复、40表增量安装、33文件集成与Runtime加载。正式入口 `http://127.0.0.1:5173/chronicle` 已核验日常登录、空年历及搜索；Memory workspace/context开启，paid关闭，Voice保持原日常配置。重启环境与条件回退见唯一计划 `docs/MEMORY_V2_EXECUTION_PLAN.md`，不得直接绕过Runtime门控。备份 `C:/Users/ASUS/AppData/Local/YOSAKI-MEMORY-R3-DEPLOY-20261116-223755`。C1～C10未全部通过；真实梗概超时unknown保留，不自动重试或清预留。3轮已用完，未经Owner主动延长不继续新一轮开发或付费验证。


## 55. Memory V2 当前交接与暂停 — 2026-11-18

> 历史快照：后续授权及当前H5-B资格、核价和交付状态见统一执行计划已更新的C1～C10总表；本节的11/13、全批未发布及暂停措辞不得作为当前状态沿用。

### 暂停与工作区边界

- Owner已明确暂停。除本次指南更新及限定Git同步外，不继续开发、付费请求、私人补生成、数据写入或服务切换；恢复须由Owner主动指示。本轮未用额度不构成下一次外发或补跑授权。
- 唯一产品及交付依据仍为 `docs/MEMORY_V2_EXECUTION_PLAN.md`。源码存在、隔离通过、正式加载、真实工程成功、Owner内容确认及发布分别记录；不能用测试数或合入替代内容质量验收。
- 当前日常入口为 `http://127.0.0.1:5173/chronicle`。已按Runtime门控加载Chronicle与受资格保护的记忆上下文，日常付费周期加工关闭；手动补搜和聊天自动补查仍需其各自逐次／逐条同意及有效费率门控。Voice与UI并行成果保留。PID只是前次现场快照，任何后续重载均重新核查，不能直接沿用旧PID。
- 本机有大量既存未提交／未跟踪源码、测试、文档及并行Voice成果。本次Git同步只提交 `AGENTS.md`；不将本机运行版本描述为远端完整可复现版本，不批量add、reset、clean、stash或覆盖。完整执行计划、本地私人评审记录及外部证据未因本次指南同步一并上传。

### C1～C10 的实际边界

| 项目 | 当前证据与保留缺口 |
|---|---|
| C1 来源与导入 | 正式H5-B批次 `9b40b734`：130条有序来源、收据和确认边界、13条管理草稿；重复导入保护已验。发生时间未核实，不能借记录时间补造。 |
| C2 精修与单条梗概 | 13份完整第一人称精修保留；01～04、06、08～13共11份梗概可审，05／07仍缺完整产物。Owner未确认／发布本批；不能宣称H5-B全组内容验收通过。 |
| C3 字段与人工保护 | 字段编辑、修正、依赖及人工保护有隔离证据；银叶馆正式保存、确认、发布和重读已验。新生成不能覆盖人工修订、锁定或null抑制。 |
| C4 时间树 | 正式六档展示、边界、缺失降级和展开完整精修已有证据；有真实已确认周摘要，没有据此宣称月／季／半年质量通过。 |
| C5 检索 | 正式关键词、手动真实语义补搜及混合去重已有证据；失败保持普通结果，只有当前有效且已确认的标题／梗概可外发判定，RAW留在本地。 |
| C6 聊天 | 隔离真实聊天召回、工具展开和续答通过，正式加载及零外呼入口检查通过。额外自动补查仍须逐条同意、预算与等待上限。 |
| C7 常驻 | 已有同一无关问法在默认／开启／取消时的真实请求对照；常驻设置不由普通评分自动授予。 |
| C8 自动维护 | 正常维护、批准任务恢复、依赖失效、幂等和unknown保护已有隔离证据；正式日常付费派发继续受关闭开关限制。 |
| C9 页面 | 正式浏览、搜索、滚动、导入、确认／发布与刷新重读已有对应样本证据；本批13条可在原同页对照、待Owner审核。 |
| C10 接入与恢复 | 已有受控加载、备份与隔离恢复／开关回退证据；不得用旧库覆盖新增数据。服务运行状态、当前开关和并行差异须每次重新核对。 |

### 已加载的流式v4与可靠结算

- 批次profile为 `synopsis-full-entry-responses-chronicle-quotes-v4-stream-v1`，validator为 `synopsis-explicit-speakers-unique-quotes-v4`，RAW引文定位规则为 `raw-quote-unique-codepoints-v1`。模型给来源ID、speaker及逐字quote，由程序只在对应RAW中唯一定位Unicode区间；歧义／改字拒绝，不以猜测裁剪、模糊搜索或删事实制造通过。提示、RAW、人物归属与正文3000字符限制保留。
- 流式接收已接入实际Responses适配器和A3任务链。派发身份、Owner绑定和预留提交后才外呼；完整产物校验后保存needs_review并读时复核。公开事件、终态usage、原始／规范化结构及哈希留存；私有推理正文、凭据和鉴权头不留日志。部分文本只作诊断，不能成为可用字段或后续合成输入。
- `create_stream_replacement_trial()` 使用 `synopsis_stream_replacement_chain_v2`，绑定具体条目、当前来源、全部相关旧请求／attempt／Owner证明、版本及一次性预算。旧两层unknown逐项核验，不能换任务ID绕过其他锁或人工保护；重复／并发仅创建一个新任务。旧原证明仍可结算可靠迟到用量，但旧请求无权覆盖新候选。unknown／hold不因替代关系解除。
- 最新累计9文件（6业务、3测试）已精准合入并受控加载；无schema、依赖、路由或后台任务新增。外部相关回归136/136、执行器Mock4/4分别记录，不相加，不冒充整个正式工作区全量回归。当前正式目录未重跑应用测试。
- 对应外部清单 `CHAIN_FROZEN_SOURCE.json` 的SHA-256为 `5C1D81F044C5EB7C7CF4BA6943AE1074C12253F5DBC601E385383B531FC6364B`。受保护证据与字节／一致性库备份位于本机 `C:/Users/ASUS/AppData/Local/YOSAKI-H5B-H2-4d9d19e6e1da4e238d0ff0cc03878132/stream-chain-20261118`；最终全批证据为 `FINAL_AUDIT_V2.json`、`FINAL_REPORT.md`、`FINAL_EXECUTION_EVIDENCE.json`，`FINAL_AUDIT.json`仅代表07后暂停时点，不能误作全批结果。上述私人输入、公开模型原文、库、claim及密钥不入Git。

### 最近一次实际补生成结果与剩余事项

- 先执行07；它因 `max_output_tokens` 返回可靠incomplete终态。Owner随后明确批准在原整组次数／费用上限内继续未派发的六条，原07及claim不重开。本轮总计7次计数、7次生成、0embedding，零重试；新增可读待审候选为02、04、11、12、13，共5份，连同旧6份现为11/13。正式Edge整页刷新并从原批次逐条重读，13份精修和原标题、来源、未核实时间保留；未自动确认、激活或发布。
- 本轮可靠费用估算USD0.366636，新有效预留0；估算不等于供应商最终实付账单。旧unknown的身份、历史结算及USD1.105920、USD0.860160、更早独立USD0.122880预留分别保留，不扫描清理、不写成零费用、不把余额当新授权。输入的缓存读写包含于总输入，输出包含推理，禁止重复计费。
- 仅05、07仍待补：两条总输出均触及6144token，完整证据JSON未形成；只留原公开输出和诊断，没有候选或成功引用。05的真实流式终态约63秒到达，覆盖了一次超过59秒的实际等待，但不证明此前网络故障根治。不能把incomplete误判成断线、无费用或文本忠实性失败。
- 若Owner恢复这两条，最短路径是先用已有材料本地核验容量，单独版本化调整总输出预算，保留正文／证据要求，再明确批准两次新尝试、私人材料再次外发和可能重复计费。8192总输出的现有保守预算示例为每条USD0.147456／两条USD0.294912，尚未批准或执行，不能用本轮剩余额度自动补发。旧任务和全部替代链继续绑定，不恢复旧claim。
- 本地逐条来源核对不代替Owner质量决定；阅读时可修正措辞，内容确认与发布仍由Owner在原01～13同页入口操作。生成、确认、发布及上下文资格彼此独立；H5-B草稿不自动获得召回资格。
- 回退优先停止新付费执行器并保留新版本读取校验、字段、审核、来源和账本。源码只在仍为本轮字节且无后续修改时逐文件处理；已有v4数据不能直接撤掉新校验让旧读取器接管，不整库恢复、不删除历史unknown或释放其预留。

## 56. 当前项目再核对 — 2026-11-18

- 本轮任务仅更新本指南。第 55 节记录的 Owner 暂停仍适用于 Memory V2 开发、私人补生成、付费请求和日常服务切换；此次文档复核不构成恢复这些操作的指令。当前工作树仍有大量既存未提交和未跟踪的 Memory、Voice、UI、测试及文档，修改前后均按目标文件核对，不整理、覆盖或批量暂存。
- Memory V2 每轮完整读取 `docs/MEMORY_V2_EXECUTION_PLAN.md`，其 C1～C10 为唯一交付结果表。`main.py` 现按 `YOSAKI_CHRONICLE_WORKSPACE` 安装 workspace，聊天构造受 `YOSAKI_CHRONICLE_CONTEXT` 约束；付费 Provider 还受单独开关和任务预算保护。H5-B 正式130条有序来源、13条经历及13份完整精修／13份梗概（05／07已补齐，历史incomplete保留）；截至2026-11-19的后续记录，09已确认关联精修、确认标题／梗概并发布；其余12条仍未发布，不再将全批描述为均未确认发布。NARRATIVE 完整精修、单经历 Synopsis、Temporal DIGEST 分字段与资格管理；阿夜记忆通道不得返回 RAW／CLEANED，旧记忆最终展开到合资格精修正文。unknown、旧预留、来源撤权与人工审核不得因替代任务或文档更新被清除。
- UI 的银灰玻璃共用主题已写入工作区：`frontend/src/main.ts` 加载 `ui/tokens.css`、`silver.css`，`frontend/src/ui/` 包含主题、玻璃表面、地球和聊天抽屉；`ChronicleApp.vue` 与 `ChronicleEditor.vue` 承担已受控接入的管理页。UI 改动先读 `docs/UI_DESIGN_STANDARD.md`，保留正文 17px／1.75、手机字号、全部功能入口及录音、未保存、确认、错误状态。`design-qa.md` 记录了桌面/手机/平板与全能力聊天布局实测，也明确角色草稿“取消放弃”分支、语音告知焦点循环、真实设备和跨浏览器等未覆盖项；构建与截图不能代替这些验收。
- Voice 的当前入口改为 `docs/VOICE_DELIVERY_INDEX.md`，并参照 `docs/VOICE_FOLLOWUP_ROADMAP.md`。基础朗读、录音草稿、自动朗读和连续通话已有日常能力；HTTP 默认、TTD WS 可手选，耳机插话须单次主动勾选。对话 18 的生成中接续、对话 20 的 HTTP 停音及独立耳机插话各有范围有限的真实证据。新的段/句多位置表达规划只在隔离实验中实现；56 字两位置音频获一次用户听评认可，真实 AI 规划仍是待批准候选、日常未启用，超过 2000 字的两次长文首段 unknown 且第二段未发。不能沿用第 54 节的旧额度快照或把 Voice 第 2 步样本听评写成判断质量通过。
- 本次没有导入 `backend.app.main`、触碰 `data/`、重启服务、发起 Provider 请求或执行付费／私人数据测试。历史测试和外部冻结副本的计数只对原清单负责；当前实例、开关、余额与文件字节下一次操作时均须重新核对。


### 2026-11-18 Memory 审核关联与常驻保留当前补记

- 以统一执行计划末尾同日“审核关联、发布版本及常驻保留修正”及源码为准：确认精修核对当前M3并原子关联／审核P1 narrative；旧窗口拒绝覆盖，重复确认幂等；新Chronicle发布要求当前可见精修与梗概已确认。没有代Owner确认或发布H5-B。
- Owner批准显式常驻／固定跨时间档保留当前完整已确认单经历梗概，共用1024估算token／5块，全文按需展开；重要／奠基不自动常驻，不新增重要约定抽取权威。超预算不截断正文，取消下一次请求生效。
- 本轮6文件限定加载、外部相关组合87/87及前端构建通过；正式13份梗概＋13份精修只读重核。0Provider／embedding／凭据读取，无迁移，旧32份请求与预算及130条RAW不变。日常付费关闭，Voice／UI并行成果保留。旧55节等11/13为历史时点，不得据其重跑已补齐任务。
- H5-B全批向量尚未建立；现有打包入口8192 UTF-8字节保守界，13条标题＋梗概16540字节，后续须先做明确分组／增量建索引再批准外发，不能把银叶馆证据当作本批已有向量。

## Chat Enhancement 当前交接 — 2026-11-19

消息操作、版本／分支、快照、附件、上下文与统计均已实现并加载日常；永久模型配置回显、单次设置跨刷新保留已修复并加载。单次设置只恢复已应用且未消耗值，绑定当前用户／角色／会话／模型版本，切换清除；后端接受后消耗，HTTP拒绝保留，在途未知不自动重放，不修改模型默认值。

当前入口、真实API六次／套餐一次的限定结果、测试版本、备份和兼容回退统一见 docs/CHAT_ENHANCEMENT_HANDOFF.md 与 docs/CHAT_ENHANCEMENT_CANDIDATE.md；八类缺口见 docs/CHAT_ENHANCEMENT_FOLLOWUP.md。49文件加载后15张chat表已安装；不得沿用“本候选未接入日常”。1509／91／43及后续专项分别绑定原冻结版本，不合并计数。网页默认关，套餐视觉／硬上限状态保留。

Git同步使用专项分支，独立聊天源码与公共文件精确补丁分开，不能把当前未提交Memory／Voice／UI整份混入。日常工作树保留并行改动。Codex完成技术操作、浏览器与已授权加载，Owner只参与必要决策及人工交互。

聊天收尾现场：55文件起点核对后，保留同期main／App／Runtime的Voice差异并独立补验5项及构建，不混入聊天Git补丁。原进程退出原因未知；Owner允许恢复后端口守卫发现服务已恢复，未重复启动／迁移。最终8000／5173为200，当前并行AI入口存在但用户开关未启用；158表中的6张AI表属于并行安装。见最终交接，不沿用旧PID或声称全工作树进入本批提交。
