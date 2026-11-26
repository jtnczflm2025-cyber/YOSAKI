# YOSAKI 产品与开发计划（更新版）

> 更新日期：2026-10-28
>
> 当前项目已落地“欢迎页 → 工作台 → 可组合浮动窗口”的本地 MVP，并具备单用户认证、角色卡、模型、聊天历史、记忆上下文、MCP/受控插件、语音和加密备份等功能。规划中的功能路径现已支持全页工作区模式，并与浮动窗口共用功能界面；目前尚未拆分为独立页面组件。手机端、真实 provider/MCP、设备和 VPS 仍需单独验收。本文后续阶段章节保留为产品方向，实际完成状态以文末按日期追加的 Implementation progress 和代码为准。

## Memory V2 后续路线（2026-10-28）

- **总体职责**：Memory V2 由 Chronicle／溯时流源（事件与表达证据年历）、Personal Notebook／心潮念（独立的主动个人笔记）和 Experiential Memory（只读经验关联视图）三个关联但权威独立的领域组成。统一跨域来源引用与读时校验，不统一原文、主观表达、模拟状态或正式学习审批的权威。完整职责矩阵与目标契约见 [Memory V2 架构与 M4/M5 路线](MEMORY_V2_ARCHITECTURE.md)。
- **M1** 已在隔离环境完成双来源只读档案；正式数据库启用仍需独立备份、恢复、迁移和权限验收。
- **M2** 已在隔离副本建立只读召回候选、可信过滤、冷却与有界上下文影子编译，并完成 R2.1 持久锚点后的合成验收。未来充当跨域召回协调层，按用途、权限、可信标签与 Token 预算读取各域，不取得写入或审批权。影子结果未进入现有正式模型请求；正式迁移和旁路仍须另行批准。验收边界见 `MEMORY_V2_R21_M24.md`。
- **M3／Chronicle** 按已批准的忠实精修方案实现相互正交的 `RAW`、`CLEANED`、`NARRATIVE`、`DIGEST` 文本视图。后面三类是带版本、可重建的派生文本；第一人称记事须保留有序来源、发言归属、否定、条件、时间与事实忠实性，不代阿夜解释未记录的内心。时间 L0–L5 指时间粒度，不指文本视图。长期学习结论仍由 Phase 5 审批；本次架构修订不暂停或扩大 M3。
- **M3.5** 隔离工程验收完成，正式模型能力待验证；当前只在隔离环境使用本地模拟适配器。**M3.6 零付费工程验收完成**：未注册的 Responses 适配、合成评测基础与预算账本通过隔离回归；真实模型精修质量、用量和费用仍待验证，未达到正式部署就绪状态。付费试运行需单独批准确切模型、凭据、样本和预算。见 [M3.6 契约](MEMORY_V2_M36.md)及[零付费验收](MEMORY_V2_M36_ZERO_PAY_ACCEPTANCE.md)。
- **M4／Experiential Memory Bridge** 待另行批准后，审计 Phase 1～6.5 权威记录，建立目标、决策、执行、结果、反馈与学习的可验证关联，并经 M2 供 Phase 2／3 按需参考。区分原系统明确记录、时间接近、未经核实的候选和明确确认的关联；时间顺序或模型推断不构成确定因果。不建立平行欲望、心理、审批、执行或召回系统。
  M4 的计划审核与隔离开发不依赖 M3.6 真实模型上线，但仍须单独授权。
- **M5** 在 Vue 工作台提供时间树、四视图切换、原文与精修并排对照、逐句来源高亮、版本／失效状态与召回原因；增补同一事件的决策、执行、反馈和已批准学习关联视图。清楚区分 Chronicle 证据、经验关联与个人笔记；心潮念只展示当前明确允许引用的内容，不默认合并、改写或摘要。

心潮念保持独立；未来连接器须分别授权关联、查看、召回与上下文注入，且可撤销。旧关联不授予永久全文访问；撤权或来源失效后不得返回旧正文，也不默认加入 Chronicle 摘要或 Phase 5 学习。M2 不创建 M3 精修表、摘要任务或 M5 界面，也不替换旧记忆注入链。正式数据库部署或本地正式服务的旁路须单独批准。

## 一、产品入口结构

YOSAKI 不以聊天窗口作为主页面，而是以一个可以逐步展开的个人 AI 工作空间为核心。

```text
欢迎页
  ↓ 点击进入 YOSAKI
导航页 / 工作台首页
  ├─ 聊天窗口
  ├─ 角色卡管理
  ├─ 模型配置
  ├─ 提示词与记忆
  ├─ 欲望与意愿（V3 驱力 + Phase 2 未决意愿 + Phase 3 自主活动 + Phase 4 反馈；角色独立启用）
  ├─ 长期学习（Phase 5：经历、偏好、策略和目标进度的审阅与时间线）
  ├─ 心理状态（Phase 6：模拟情绪、虚拟身体、二阶欲望和冲突）
  ├─ MCP / 工具
  ├─ 插件管理
  ├─ TTS / STT 语音
  └─ 系统设置
```

### 1. 欢迎页

欢迎页负责建立产品氛围和入口，不直接显示聊天记录。

首版内容：

- YOSAKI 标志和简短介绍
- “进入我的空间”按钮
- 最近一次进入时间或欢迎语
- 本地数据状态提示
- 后续可增加阿夜的动态问候，但不能阻塞进入主界面

欢迎页默认路由：

```text
/
```

### 2. 导航页 / 工作台首页

导航页是登录或进入后的默认主页面，承担功能总览和快捷入口。

首版卡片：

- 与阿夜聊天
- 角色卡
- 模型配置
- 提示词与记忆
- MCP 与工具
- 插件
- 语音
- 设置

每张卡片显示：

- 功能名称
- 一句当前状态
- 最近更新时间或连接状态
- 打开按钮
- 可选快捷操作

导航页默认路由：

```text
/hub
```

导航页不能假设用户下一步一定要聊天；用户可以自由进入角色、模型、记忆或工具页面。

## 二、功能页面与浮动窗口模型

功能页同时支持两种打开方式：

1. 作为完整页面打开，适合集中编辑和手机端使用。
2. 作为浮动功能卡片打开，叠加在当前页面上，适合边聊天边调整模型/角色/工具。

例如：

```text
在聊天窗口中
  → 点击“模型”
  → 模型配置以浮动窗口出现
  → 调整完成后保存
  → 关闭窗口并回到聊天
```

### 1. 窗口能力

桌面端浮动窗口需要支持：

- 打开
- 关闭
- 最小化
- 放大/还原
- 拖动
- 调整大小
- 置顶到当前活动层
- 多个窗口同时存在
- 同一类型窗口最多允许一个实例，避免重复配置页面
- 刷新后恢复窗口位置和大小

每个窗口至少保存：

```ts
type WindowInstance = {
  id: string
  kind: 'chat' | 'characters' | 'models' | 'memory' | 'mcp' | 'plugins' | 'voice' | 'settings'
  title: string
  x: number
  y: number
  width: number
  height: number
  zIndex: number
  minimized: boolean
  maximized: boolean
}
```

### 2. 窗口管理器

前端新增统一的 `WindowManager`，不要让每一个页面自己实现拖动和层级逻辑。

建议组件结构：

```text
AppShell
├─ WelcomePage
├─ HubPage
└─ WorkspaceShell
   ├─ NavigationRail
   ├─ WindowManager
   │  ├─ FloatingPanel
   │  ├─ ChatWindow
   │  ├─ CharacterWindow
   │  ├─ ModelWindow
   │  └─ ...
   └─ Toast / StatusLayer
```

建议状态接口：

```ts
openWindow(kind, options?)
closeWindow(id)
focusWindow(id)
minimizeWindow(id)
maximizeWindow(id)
moveWindow(id, position)
resizeWindow(id, size)
restoreLayout()
saveLayout()
```

### 3. 桌面端与手机端差异

桌面端：

- 卡片可拖动和缩放
- 多个功能窗口可以并列或重叠
- 当前活动窗口提高 z-index
- 导航栏可以收起

手机端：

- 浮动窗口自动转为全屏页面或底部抽屉
- 保留关闭、返回和保存按钮
- 不强行提供精确拖动/缩放，避免触摸误操作
- URL 或浏览器返回键可以恢复到导航页

## 三、更新后的开发阶段

### 阶段 0：应用壳与路由

在现有聊天界面基础上增加：

- 欢迎页
- 导航页
- 工作区壳
- 路由或等价的视图状态管理
- 桌面端窗口管理器
- 手机端全屏功能页模式
- 统一的页面打开/关闭动画

验收标准：

- 首次打开显示欢迎页，不直接进入聊天
- 点击进入后显示导航页
- 从导航页可以打开各个功能入口
- 聊天、角色、模型至少可以作为独立页面显示
- 聊天中可以打开模型配置浮动窗口

### 阶段 1：聊天窗口 MVP

保留现有聊天能力，但将其从主页面改为 `ChatWindow`：

- 角色选择
- 对话列表
- 气泡消息
- SSE 流式回复
- 新建对话
- 历史消息
- 关闭或最小化聊天窗口后回到工作台

### 阶段 2：角色卡功能页

角色卡作为独立页面和浮动窗口共用同一个组件：

- 多角色列表
- 新建、复制、删除角色
- 编辑人设、场景、开场白
- 提示词块排序
- 提示词注入位置
- 保存状态和未保存提示

### 阶段 3：模型配置功能页

- OpenAI 兼容接口配置
- API Key 仅由后端保存
- 模型连接测试
- 模型列表和启用状态
- 对话内切换模型
- 从聊天窗口打开模型浮窗

### 阶段 4：提示词、记忆与缓存

- 稳定提示词前缀
- 动态上下文
- 最近消息窗口
- 记忆摘要
- Token 估算
- 缓存命中记录
- 从聊天窗口打开记忆窗口并查看当前上下文

### 阶段 5：工具、MCP 与插件

- MCP 服务管理页面
- 工具清单和权限
- 逐次确认卡片
- 工具执行记录
- 受控工具包插件
- 从导航页或聊天浮窗打开工具管理

### 阶段 6：语音能力

- TTS 配置页面
- STT 配置页面
- 统一服务适配器
- 聊天消息朗读
- 语音输入转文字
- 移动端权限与失败提示

### 阶段 7：本地、局域网与 VPS

- 本地一键启动
- 局域网访问
- 窗口布局和用户配置备份
- HTTPS、认证、密钥加密
- VPS 部署
- 数据库和配置备份恢复

## 四、前端路由建议

```text
/                 欢迎页
/hub              导航工作台
/chat             聊天完整页面
/characters       角色卡完整页面
/models           模型配置完整页面
/memory           提示词与记忆完整页面
/desires          欲望与意愿完整页面
/learning         长期学习完整页面
/psychology       模拟心理状态完整页面
/mcp              MCP 与工具完整页面
/plugins          插件管理完整页面
/voice            语音配置完整页面
/settings         系统设置完整页面
```

浮动窗口不一定需要单独的 URL，但建议同步到 query 或可恢复状态：

```text
/hub?window=chat
/hub?window=models
/hub?window=characters
```

这样刷新页面后可以恢复用户刚刚打开的功能，也方便以后分享或调试具体页面。

## 五、需要持久化的布局状态

第一版可以先保存在浏览器 `localStorage`：

```text
YOSAKI.window-layout.v1
YOSAKI.last-route
YOSAKI.last-open-windows
```

登录和后端账户完成后，再把以下内容同步到 SQLite：

- 最近打开的页面
- 窗口位置和大小
- 导航卡片排序
- 卡片隐藏/显示状态
- 工作台主题
- 桌面端与移动端偏好

不要把 API Key、MCP 密钥或模型密钥存进 localStorage。

## 六、更新后的首版边界

首版优先实现完整的产品入口和可交互工作台，而不是继续把聊天窗口当作唯一页面：

1. 欢迎页
2. 导航页
3. 聊天完整页面
4. 角色卡完整页面
5. 模型配置完整页面
6. 桌面端浮动窗口基础能力
7. 手机端全屏功能页能力
8. 现有聊天 SSE 接口和 SQLite 数据继续复用

MCP、插件、TTS/STT 仍然按后续阶段加入，但它们从一开始就使用同一套导航卡片和窗口管理架构，不再为每个功能单独设计一套入口。

## Implementation progress — 2026-10-14

- MCP chat tool calls now pause for a per-call user decision, persist runs, validate arguments, and continue the model after tool results.
- Plugin v1 imports JSON metadata manifests only; packages bind existing MCP tools and contain no executable code. New plugin/tool grants default off.
- Voice v1 uses browser/system TTS and guarded browser STT. Remote browser recognition requires an in-app consent step; no audio is routed through the YOSAKI backend.
- Remaining: authenticate the app before public hosting, test with real configured providers/MCP servers, provider-backed TTS/STT adapters, legacy SSE MCP, recursive tool batches, and deployment hardening.
## 阶段 7：生产化与部署验证（更新）

- 单用户认证、HttpOnly 会话、加密备份与部署骨架已实现。
- 下一步先在本地做真实账户登录、加密备份恢复和模型/MCP smoke test，再进行 VPS 迁移。
- Docker 未安装在当前开发机；`deploy/` 文件需要在 VPS 或安装 Docker 的环境构建验证。
## Implementation progress — 2026-10-21 (workspace routing and full-page mode)

- Completed the application-shell route increment: `/chat`, `/characters`, `/models`, `/memory`, `/mcp`, `/plugins`, `/voice` and `/settings` now render a full-page workspace mode using the shared feature surface; `/hub?window=<kind>` continues to open the floating-window mode.
- Floating-window headers provide an explicit “作为独立页面打开” action; full-page mode replaces floating controls with a return-to-workspace action.
- Browser history, refresh and window close/focus keep the active mode URL synchronized; layout restoration validates persisted records, enforces one window per kind, clamps off-screen windows into the viewport, stores recovery keys, and captures CSS-resized dimensions on pointer release.
- Validation: frontend production build passed, backend syntax compilation passed, local `/health` returned version `0.17.0`, and authenticated local browser checks verified direct page rendering, switching and history restoration.
- Still not proven: login/setup flow itself, real provider/MCP calls, mobile viewport/device QA, Docker/VPS deployment and production hardening.


## Implementation progress — 2026-10-21 (full-page feature surfaces)

- Feature routes now open the corresponding tool as a full-page workspace surface while reusing the same form/chat implementation and state. This adds page mode without duplicating business logic or breaking the floating-window workflow.
- Added a per-window page-mode action and return control; direct links, browser history and refresh preserve the selected mode. On narrow initial viewports, the navigation rail starts collapsed so it does not cover the full-page surface.
- Validation: production build passed; browser verified direct `/models`, floating-to-page `/chat` switching and back navigation. Authenticated feature contents rendered, with no frontend console errors. A 390×844 mobile viewport check covered chat and character pages with no horizontal overflow; physical-device and assistive-technology QA remain outstanding.

## Implementation progress — 2026-10-23 (character editing state)

- Role profile fields now display saved/unsaved state. Switching roles or starting an operation that selects another role asks before discarding a draft; cancelling keeps it. Browser reload/close uses an unsaved changes warning.
- Verified the edit, cancel switch, confirm switch, and save paths in a temporary local database through the browser. Prompt-block edits retain their existing per-field save behavior.

## Implementation progress — 2026-10-23 (stage 7 local acceptance)

- Added an opt-in data-directory override and isolated automated tests for auth, demo chat, encrypted backup restore, mocked model streaming, MCP discovery and tool decisions.
- Fixed two MCP integration gaps discovered by the tests: response parsing for JSON/SSE and the tool policy update route. Mocked local validation is not real provider or VPS acceptance.
- An isolated browser check confirmed the MCP page can connect to the mock service, refresh its tool list, and enable a tool through the restored API.

## 第二版进度 — 2026-10-23

- 已接入旧设置兼容的严格/分段对话轮次窗口、上下文预览、兼容接口公开推理文本、Responses API 公开推理摘要与独立角色内心独白。Responses 适配器使用 API Key，通过模拟服务验证工具确认与续接；独白仅保存展示，不回传后续模型历史。
- 已接入 JSON/Markdown/TXT 聊天档案批量 ZIP 导出、导入预览、重复文件跳过和阅读版清理选项；浏览器保存清理模板。导入按文件处理并限制单文件 8 MiB、整批 64 MiB、单文件 20000 条消息。
- 已接入本地同步目录和私有 GitHub 专用仓库的加密备份计划。密码与 token 仅从后端环境变量读取；定时默认关闭。GitHub 短暂上传失败最多尝试三次，两个目标分别记录结果。真实 GitHub 上传、Docker 和 VPS 尚待验收。
- ChatGPT 套餐授权仍是独立试验：当前源码仓库为 private，官方公开接入范围针对开源且本地托管的应用；目前未取得 OAuth 客户端或真实账户权限，也没有改变仓库可见性。现有 Responses 适配器使用单独 API Key，不代表 Plus 套餐可用。
- 隔离后端 9 项测试、前端构建和本地浏览器检查已通过；浏览器实际选取示例 JSON，预览并导入，验证阅读版称呼清理预览、模型协议选项和备份设置表单。真实模型、GitHub、设备验收未完成。

## 第二版验收加固 — 2026-10-23

- 源码仓库经 GitHub 核对仍为 private；项目负责人选择继续保持私有。官方 ChatGPT 套餐授权文档针对开源、本地托管应用，故暂缓 OAuth 接入试验，继续使用现有 API Key 模型通道和其它本地验收。
- 本地定时备份写入成功但清理旧份失败时，任务现记录 `retention_failed`，明确保留新备份成功这一事实；此路径补充隔离测试。真实第三方、设备、Docker 和 VPS 验收仍分别进行。
- TXT 导入现在逐条检查整份文件的消息边界，拒绝合法消息后夹杂非法标记的文件；Markdown/TXT 正文起始的边界标记可转义后往返。隔离后端 9 项测试、前端构建、后端编译和临时服务 `/health` 均通过；当前机器未配置真实备份环境凭据，也没有 Docker。

## 第二版 ChatGPT 套餐接入试验 — 2026-10-23

- 项目负责人改变仓库可见性决定：授权将源码仓库公开，但暂不添加许可证。公开可见不等于满足官方“开源且本地托管”接入条件，需以真实授权结果为准。
- 增加默认关闭的 `YOSAKI_CHATGPT_PLAN_ENABLED=1` 本机 OAuth 流程：动态客户端注册、PKCE/state/nonce、ID token 验证、加密的后端凭据、刷新与撤销。模型目录及独立 Responses 通道不使用 API Key；流式模型和 MCP 工具沿用逐次确认，不自动回退付费 API。
- 本地模拟授权、模型、刷新、撤销、工具续接已通过隔离测试。真实 ChatGPT 账户授权、套餐额度和可见模型未测；如果授权无法取得套餐 scope 或官方判定应用不符合条件，保持试验状态并记录具体结果。
- 仓库公开前扫描 Git 历史和待提交文件，确保真实数据库、Fernet 密钥、OAuth 文件、备份密码及服务凭据未入库。公开仓库不代表应用已可部署到公网；Docker、设备和 VPS 验收继续独立进行。

## ChatGPT 套餐本机实测 — 2026-10-23

- 首次授权在等待用户确认期间超过原有 10 分钟回调期限，浏览器返回 `127.0.0.1` 拒绝连接。已把等待时间延长为 60 分钟，并在回调成功页从浏览器地址栏移除一次性授权码。
- 用户亲自在官方页面确认 YOSAKI 可使用其 ChatGPT 套餐额度。回调成功后，本机 UI 显示账户已授予所需 scope，模型目录列出 `gpt-6.1-sol` 等模型。添加 `GPT-6.1-Sol` 档案后，真实连接测试得到 `OK`；聊天流式回复返回 `YOSAKI 已连接。`，显示供应商用量为输入 116、输出 11、缓存 0 token。
- 这次仅验证当前账户、当前本机实例和所选模型。未核对 ChatGPT 用量后台账单，也未验证其它模型、真实 MCP 工具、设备、Docker 或 VPS。源码仓库仍按用户决定公开可见但不添加许可证。

## 后续独立项目登记 — 2026-11-07

- **`CHRONICLE_RUNBOOK`：阿夜专用的记忆系统使用与维护说明。** 后续单独设计 Chronicle 的查阅、来源核对、派生版本审核、失效诊断与维护操作指引。说明文档不赋予阿夜额外的写入、审批或工具执行权限；当前 H5-B H3.1 仅提供未确认的边界建议。
- **`AI_SCHEDULER`：统一日程系统。** 后续单独规划主人安排、系统维护、阿夜自主安排与一次性任务。数据与 UI 必须区分**任务安排**、**唤醒或提醒**和**实际执行授权**；阿夜可提出周期性维护任务，但真正执行仍受现有角色权限、工具确认、预算、任务状态和相关 Phase 权威边界约束。此项仅登记路线，不新建调度表、后台任务或执行入口。
# P1-A2 渐进条目实施记录（2026-11-08）

七项补充契约已落实到 `MEMORY_V2_P1A2.md`：timeEvidenceToken、DIGEST profile、独立词库版本、分用途资格、Tag/Keyword 证据区别、高保真纪事与日期分存、owner 质量门槛。隔离实现和专项验收不构成正式接入；H3.2 排序修复及真实13条草稿创建仍是后续步骤。

## P1-A3.0 源码合入收口（2026-11-10）

- 原隔离副本的 `CONTINUATION5_FROZEN_SOURCE.json` SHA-256 为 `a1e8e9bdd16a09d87aa63f8131f3c1bf5347309ed99dc2854672385f0d81b83d`；验收报告位于 `C:/Users/ASUS/AppData/Local/YOSAKI-A30-b0a477d2c8fe4f7eb17397ede8efb6a8/A3_CONTINUATION5_ACCEPTANCE_REPORT.md`。该阶段完整隔离回归为 430/430。
- 当前源码集成副本的 `INTEGRATION_FROZEN_SOURCE.json` SHA-256 为 `2e164895ef92983a25c406d149f88dbe5b82cd33aca1d0818a289d4682522909`；报告位于 `C:/Users/ASUS/AppData/Local/YOSAKI-A30-INTEGRATION-36cb3340ddbc41adb32f39ac8330a275/A3_INTEGRATION_ACCEPTANCE_REPORT.md`。在该副本上完整后端回归 438/438、0 跳过；普通和 Chronicle 两种前端配置均通过类型检查与构建。
- Owner 已授权限定源码合入。四个既有 A3 文件、两个 processing 模块及十一个 A3 测试文件与候选逐字节一致；另一个入库门控测试删除了对外部旧执行器快照的依赖，其余三项在外部验证 3/3。18 个文件均通过不导入应用的静态语法检查。逐文件前后 SHA-256、字节备份和运行隔离检查见 `C:/Users/ASUS/AppData/Local/YOSAKI-A30-MERGE-9c0bc7d7f6a6436fbe07e7c1d5946906/SOURCE_MERGE_REPORT.md`。**438/438 属于合入前的集成副本，不是整理门控测试后在正式工作区重跑的结果。**
- 状态为「隔离验收及当前源码集成演练通过，限定源码合入完成；真实数据库未迁移，运行实例未切换，功能未新增启用」。进程退出后的可靠对账恢复、无身份历史 hold 解除仍不支持；旧路径缺精确 scope 时可能按角色保守阻塞。真实 Provider、私人历史加工和模型质量未验收。正式迁移、旧 worker 隔离和运行启用各需独立决定，源码合入不触发这些动作。

## P1-A3.1 待 Owner 审核的最小实施方案（2026-11-10）

**依据与目标。** 此前获批的 P1-A3 最终计划将 A3.1 定义为 Categories：以已确认边界及有序 RAW、角色内受控词库为输入，提出带直接来源证据的多维分类候选，验证否定、引述、玩笑、条件、未知 ID 与跨角色拒绝。仓库内未找到单独的 `P1-A3_IMPLEMENTATION_PLAN.md`，因此本节依据此前 Owner 在对话中批准的最终计划、`MEMORY_V2_P1A2.md` 和当前源码；不改变 A3.2 Tag、A3.3 Lexical 等后续顺序。

**使用者及权威。** Owner 用于整理 Chronicle 经历并审阅分类。RAW 及 M1 读时来源校验是表达证据；已确认的条目边界与来源顺序来自 P1-A2；角色词库的稳定分类 ID、维度及版本来自 `memory_v2_vocabulary.py`，现有种子定义仅重复显示名称，具体语义仍待 Owner 确认。分类候选不是阿夜个人认定、心理事实或 Phase 5 学习。输出为条目的 `categories` 字段候选，逐项绑定词库 ID／版本（从中取得维度）、触发规则版本和 RAW 精确证据映射；没有充分证据时维持 missing 或待核对，不填充猜测。Owner 经可信会话对具体字段 token 审核；条目发布另行确认。A3.1 不赋予候选召回或正式上下文资格。

**最小增量。** 新增纯规则模块 `backend/app/memory_v2_category_rules.py`，只读有序、获权的 message 正文并产出受控 ID 与证据；解释性 dynamic 分类及歧义案例保留待核对。扩展 `backend/app/memory_v2_processing.py` 的确定性本地 step：通过 A3.0 的角色、语义工作范围、readSet/writeSet 和取消检查，在短写事务中复核来源、词库版本、字段 token 与人工锁定后，调用既有 `memory_v2_metadata.revise()` 保存候选并结算任务；不走远端派发，也不创建模型子批次。必要时只对 `memory_v2_metadata_jobs.py` 增加受控的 Categories 任务接线，不改其既有 Metadata/Mock 行为；词库和字段审核只复用既有接口，发现需要改它们的公开契约时停下重新审查。新增 `tests/test_memory_v2_a3_categories.py` 及合成金标准分类案例，不改正式 `main.py`、正式 API、前端或 Phase 模块。

**分步验收。** 先冻结允许源码并在新外部副本、临时 SQLite 与合成角色／消息中验证规则和证据，再验本地任务的幂等、范围冲突、来源/词库/人工修订竞争、暂停取消与崩溃回滚，最后运行 A3、P1-A2、M1～M3.6、P2、Runtime、Voice 和 Phase 的相关完整隔离回归。测试覆盖同一条经历多维/同维多选、明确讨论与否定/引用/玩笑/条件的区分、日中混合、未知或停用词库 ID、跨角色来源、重命名仅改变展示、语义定义变更导致依赖失效，以及已审核标题/人工分类不被覆盖。A3.1 不开放自动发布，未获 owner 审核的规则候选变成可用字段的次数必须为零；规则不能可靠判定时不写分类字段，只记录待核对原因。Mock 只验工程协议，不证明自由语言分类质量。

**失败与限制。** 本地规则无远端费用，确定失败可按 A3.0 契约记录 failed；已持久化候选不等于已审核。若以后接入远端请求，任何 possibly_dispatched/unknown 仍需保留预算与语义阻塞，不能让本地 step 或 Scheduler 绕过；取消或来源失效后不得写入/发布候选。来源越权、人工修订覆盖、unknown 绕行、错误自动审核、旧业务回归失败时立即停止并保留副本。失败时只停用新分类入口，保留字段版本、审核与证据；不以整库覆盖回退。

**授权边界。** A3.1 可以先用合成数据和纯规则/Mock 完成隔离开发，不需要真实数据库迁移、Provider、API Key 或 H5-B 私人正文。真实模型分类质量与费用、私人历史批量加工、正式库迁移、服务启用和上下文接入分别申请批准。进程退出后的远端对账恢复及历史 hold 解除不阻塞这一步纯本地隔离开发，但阻止宣称远端任务完整恢复；旧 scope 同角色保守阻塞须在竞争测试中保留。A3.1 编码本轮未获授权，待 Owner 审核本方案后另行决定。

**待定及交付。** 开工前把可自动提出候选的规则白名单、对应中日语言范围及负例金标准定版；对于“技术讨论”等话题词和“相互支持／和解”等解释性分类，不能仅凭词面命中自动确认为经历性质。当前 `tests/a3_gold.py` 的 32 条是协议占位合成例，不是分类质量金标准；A3.1 须新建可人工核对的期望标签与拒绝理由。交付源代码差异、候选及证据示例、字段审核前后状态、冲突与回滚测试和完整隔离回归。未来要自动发布分类，仍需按字段／规则版本与范围另行取得 Owner 授权。

### A3.1 开工前分类规则与验收样例（待 Owner 确认；2026-11-10）

**已确认与待决定的界线。** `MEMORY_V2_P1A2.md`、原 P1-A3 任务书和 `memory_v2_metadata_contract.CATEGORIES` 一致规定三维、16 个初始显示名称、可多选或留空、按角色隔离。`memory_v2_vocabulary.seed()` 用角色 ID、维度和固定位置生成 UUIDv5；重命名不改变 ID。当前种子 `definition` 仅复制显示名称，**没有经过 Owner 确认的可操作语义定义**。下表的定义和白名单是本轮**建议**，不是已发布的产品语义；Owner 确认定义之前不得运行自动分类规则。分类不是重要性、心理状态或外部事件真实性证明。不得新增关系状态或心理状态维度。

| 维度／初始分类 | 建议的操作定义；状态 | 足以提出待审候选的表达 | 不足以支持的表达 | 首版规则／语言范围 | 证据要求 |
|---|---|---|---|---|---|
| form／日常交流 | 日常安排或近况的实际交谈；定义待确认 | 双方围绕吃饭、天气、行程等具体日常事项接续交流 | 仅一句问候；提到“日常”二字 | **暂不自动**：范围过宽，短消息易误判 | 需多轮连续 RAW，保留双方发言 |
| form／深入讨论 | 围绕同一问题展开理由、追问或修正；定义待确认 | 多轮明确追问、理由和回应 | 对话篇幅长；说“聊得很深” | **暂不自动**：深度判断缺可靠阈值 | 多轮顺序及具体理由映射 |
| form／技术讨论 | 讨论技术机制、实现或故障的具体内容；定义待确认 | “这个接口返回 409 是因为游标过期吗？”／“是，来源版本变了” | 只提软件名称；明确说“不是技术讨论” | **建议白名单**：中、日文原句直接描述具体机制且有接续回应；不做词面匹配 | 至少两条同条目有序 RAW，分别定位问题与实质回应 |
| form／创作讨论 | 讨论作品的写法、结构、修改或选择；定义待确认 | “把结尾改成留白好吗？”／“这样能接上前一段” | 只提作品名称；引用别人对作品的评价 | **建议白名单**：中、日文有具体创作决策及接续回应 | 定位提议和回应的原句 |
| dynamic／约定 | 双方明确形成某项安排；定义待确认 | 双方说出并接受同一安排 | 单方愿望、假设、否定或玩笑 | **暂不自动**：成立条件与取消语义待 Owner 定义 | 若将来启用，需双方原句及先后顺序 |
| dynamic／分歧 | 双方对同一事项明确表达不同立场；定义待确认 | 各自陈述不一致的决定或判断 | 提问、引用第三人立场 | **暂不自动**：不能把讨论误写成分歧 | 双方具体立场及归属 |
| dynamic／矛盾 | 双方明确表达实际冲突；定义待确认 | 双方确认发生了冲突 | 仅有否定、语气强烈或冲突设想 | **暂不自动**：解释性强 | 实际事件表达，不推测关系 |
| dynamic／和解 | 双方明确确认已完成和解；定义待确认 | 双方明确说“已经和好”并有对应回应 | “讨论和解，但没有和解”；条件或愿望 | **暂不自动**：否定与条件使词面命中危险 | 若将来启用，需双方确证及时间顺序 |
| dynamic／相互支持 | 双方实际提供并确认支持；定义待确认 | 双方具体说明支持行为与接受 | 引述“谢谢你支持我”；单向礼貌话 | **暂不自动**：不得推断感受或关系 | 行为及回应分别对应 RAW |
| dynamic／协作 | 双方明确分工推进同一任务；定义待确认 | 明确任务、分工和协同动作 | 一方提出合作设想；同时谈同一话题 | **暂不自动**：行动与意向须分清 | 分工及实际推进来源 |
| domain／生活 | 生活事项是对话主题；定义待确认 | 连续讨论做饭、购物或出行的具体事项 | 随口提“生活” | **建议白名单**：中、日文具体事项及接续回应 | 指向主题表达，不据此推断事情已完成 |
| domain／工作 | 工作任务或工作安排是主题；定义待确认 | 明确讨论工作任务、排期、交付 | 提到“工作”但实际只谈别的事 | **建议白名单**：中、日文具体工作问题及回应 | 原句与发言人，不推断雇佣事实 |
| domain／技术 | 技术机制、实现或故障是主题；定义待确认 | 讨论 API 返回值、代码或故障原因 | 只提软件名称；否定“技术讨论” | **建议白名单**：可与 form／技术讨论同证据，但各自独立核验 | 具体机制的 RAW 片段 |
| domain／兴趣 | 明确作为兴趣活动讨论；定义待确认 | “我最近在练吉他，和弦怎么按？”并继续讨论 | 仅提歌曲名或作品名 | **建议白名单**：中、日文明确兴趣活动及回应 | 兴趣表述和实际讨论映射 |
| domain／关系 | 人际关系本身是主题；定义待确认 | 双方明确讨论相处方式及界限 | 单句称呼或被引述的第三方话 | **暂不自动**：避免推断关系性质 | 若将来启用，仅记讨论主题，不记关系结论 |
| domain／世界观 | 价值、身份或世界观的概念是主题；定义待确认 | 双方持续讨论某一明确观念及理由 | 单个抽象词；随口比喻 | **暂不自动**：边界与语义待定义 | 多轮论点与回应，不归因心理状态 |

**首版建议白名单 `category-direct-evidence-v1`。** 仅以上标“建议白名单”的 6 类可在 Owner 确认各自定义后进入实施：form／技术讨论、创作讨论；domain／生活、工作、技术、兴趣。候选仍一律待 owner 审核。规则必须检查一组有序、相互接续的原始发言，验证主题确实被讨论；分类名、别名或孤立关键词出现均不构成规则命中。中、日文只覆盖明确列入合成金标准的词形和句式，混合文本逐段按原文证据判断；不做自动翻译、语义猜测或跨语言别名推断。无法可靠识别讨论行为时记录歧义原因，不写 Category。其余 10 类首版不自动提出候选，Owner 可继续手工分类。规则版本不得从测试名称或种子 `definition=name` 推导语义。

**话题与事件性质。** “我们讨论了和解，但没有和解”只能证明对话提及和解及明确否定已和解；不得给 dynamic／和解。只有周围真实多轮 RAW 确实持续讨论相处方式时，才可在将来经 Owner 定义后考虑 domain／关系。“她引用别人说的‘谢谢你支持我’”是第三方引述，不证明双方相互支持，不能给 dynamic／相互支持。“如果以后能和好就好了”是条件愿望，不是已发生和解；单句也不足以确认关系主题。“这次不是技术讨论，只是提到了一个软件名称”明确否定讨论性质，不能给 form／技术讨论或 domain／技术。否定可排除事件发生，却不一概排除“讨论某话题”；引述可证明引述行为，却不证明引文所述行为发生；条件可成为讨论主题，却不构成事件完成。玩笑同理需保留说话语境，不能一律肯定或一律删除。

**候选、状态和人工保护。** 可靠命中、ID 有效且来源和词库可核验时，写 `categories` 字段新修订，`completionStatus=candidate`、`lifecycleStatus=draft`、`validationStatus=pending`、`reviewStatus=needs_review`；不插入 `mv3_entry_field_checks`，不显式发布条目。无命中保持原字段不变；从未写过的字段继续 `missing`，绝不把 `[]` 当作“已确认无分类”或清除旧值。歧义与证据不足只记任务诊断，不写字段；若需持久、可分页的“待核对原因”列表，现有字段状态不能表达，需在 A3.1 实施前单独审查最小审计字段。已存在人工修订、抑制或审核结果时，不自动替换、合并或删除；当前 `revise()` 仅直接阻挡 `locked` 修订，实施须在同一写事务补充已审核字段保护并复核目标 token，不能假定现有检查已经覆盖全部情形。分类候选不自动进入有效字段搜索、候选召回或正式上下文。

**证据和失效。** 候选记录绑定角色 ID、稳定 entry UUID、确认边界版本、有序 `sourceRefs` 与 source token、每个来源当前状态／内容指纹、分类词库 UUID 与语义 token、别名／规范化版本、规则版本、生成前目标字段 token。每项分类保存一个或多个 `{'kind','id','start','end'}`；偏移是 RAW 原文中的半开区间，精确截取应等于候选声明的证据片段，不能以 NFKC 结果替代原文。必要时同一候选用多个来源片段证明双方接续；保留说话人和 source ordinal 的独立映射。前端展示名不作为证据。现有 `revise()` 验证偏移落在来源中，却未验证“截取内容满足具体分类规则”，A3.1 须先在规则层重算证据，再交给既有来源核验。重命名只改显示 token，不使语义证据失效；语义定义、确认别名、规范化规则或停用变化使旧候选／已用字段重新校验，停用词项不可继续作为有效分类；来源修改或撤权、边界来源集合改变使旧候选失效，不返回旧正文。角色或条目不符安全拒绝，不跨角色复用 ID。原始文件不在场时只验证持久来源及收据，不宣称复验了文件字节。

**合成验收集（提议，人工可逐条复核）。** 下列 `u/a` 是合成 owner／阿夜发言；`m1、m2…` 是合成来源 ID，顺序即 source ordinal。证据栏保留原句中的精确子串，实施测试时须核对原文 `[start,end)` 偏移、角色、来源 token 和词库 token。预期分类的名称仅用于阅读，实际写入必须解析当前角色的稳定词库 UUID；未核实定义前只作为预期方案，不执行规则。

| 例 | 合成有序 RAW（来源／角色／正文） | 预期候选或不生成 | 精确证据与理由 |
|---|---|---|---|
| C1 多维 | m1/u「这个 API 为什么返回 409？」；m2/a「因为游标绑定的来源版本变了。」 | form／技术讨论＋domain／技术 | m1「API 为什么返回 409」与 m2「游标绑定的来源版本变了」；具体机制问答，不从软件名推断 |
| C2 同维多选 | m1/u「先讨论脚本的错误怎么修。」；m2/a「检查请求体的字段。」；m3/u「再讨论短篇的结尾怎么改。」；m4/a「把最后一段留白。」 | form／技术讨论＋创作讨论；domain／技术 | m1「脚本的错误怎么修」＋m2「检查请求体的字段」；m3「短篇的结尾怎么改」＋m4「把最后一段留白」；同一经历内两个清楚的主题段 |
| C3 日文 | m1/u「API の応答が 409 になる理由を調べたい。」；m2/a「参照元の版が変わったためです。」 | form／技术讨论＋domain／技术 | 两条原文分别定位「409 になる理由」和「参照元の版が変わった」；保留日文，不翻译证据 |
| C4 混合 | m1/u「今晚做咖喱，味付けはどうする？」；m2/a「先放少量盐，再尝味道。」 | domain／生活 | m1「今晚做咖喱，味付けはどうする？」与 m2「先放少量盐」；讨论生活事项，不据此声称已做完咖喱 |
| C5 工作／兴趣 | m1/u「明天的工作报告要改排期。」；m2/a「先核对交付日期。」；m3/u「下班后我想练吉他，和弦怎么按？」；m4/a「先练这两个指位。」 | domain／工作＋兴趣 | m1「工作报告要改排期」＋m2「核对交付日期」；m3「练吉他，和弦怎么按」＋m4「练这两个指位」；只说明讨论主题 |
| C6 否定 | m1/u「这次不是技术讨论，只是提到了一个软件名称。」；m2/a「那就先记软件名字。」 | 不生成技术类 | m1「不是技术讨论」否定讨论性质；“软件”不能单独支撑技术类 |
| C7 事件否定 | m1/u「我们讨论了和解，但没有和解。」；m2/a「知道了，不把它写成已经和好。」 | 不生成 dynamic／和解；domain／关系暂不自动 | m1「但没有和解」与 m2「不把它写成已经和好」；讨论对象与事件完成不同 |
| C8 引述 | m1/u「她引用别人说的‘谢谢你支持我’。」；m2/a「这是引述，不能当作我们做过的事。」 | 不生成 dynamic／相互支持 | m1「引用别人说的」；引文不能证明双方支持行为 |
| C9 条件 | m1/u「如果以后能和好就好了。」；m2/a「现在没有这样的确认。」 | 不生成 dynamic／和解 | m1「如果以后」与 m2「没有这样的确认」；未来愿望不等于已发生 |
| C10 玩笑与短句 | m1/u「哈哈，给我们颁个‘最佳协作奖’吧。」；m2/a「只是开玩笑。」 | 不生成 dynamic／协作；其他类无充分依据 | m1「颁个‘最佳协作奖’」与 m2「只是开玩笑」；没有任务分工或推进 |
| C11 未知／停用 ID | m1/u「这个 API 的状态码是什么？」；m2/a「409 表示当前游标已过期。」；测试词库 ID 不存在或已停用 | 拒绝写入；不以新词项或展示名替代 ID | RAW 可定位，但词库 UUID 或状态不合格；不生成字段版本 |
| C12 跨角色 | 角色甲的 m1/u「API 为什么返回 409？」；角色乙的 m2/a「因为来源版本变了。」；请求把两条绑定为角色甲的经历 | 权限拒绝，无候选或诊断正文 | 跨角色来源不能组合为条目证据；先核验角色再读取正文 |
| C13 人工已有值 | m1/u「要改工作报告的排期。」；m2/a「先核对交付日期。」；`categories` 已有人工作为分类并审核 | 保持原字段 token、审核及关联，不自动合并或替换 | m1「工作报告的排期」虽可支持工作主题，人工结果优先 |
| C14 无命中／空字段 | m1/u「嗯。」；m2/a「知道了。」；原字段为 missing 或已有人工值 | 无新修订；missing 仍为 missing，人工值不清空 | 没有能证明任一受控类的原文片段；空结果不是否定分类 |

以上样例是拟实施的质量金标准草案，**不是已运行测试结果**；原 `tests/a3_gold.py` 的 32 条仍只是协议夹具。实施时需补充边界挪动、来源改动、别名／语义版本变更、取消竞争及候选幂等的工程断言，不把质量样例替代工程回归。

**本地任务事务与待补契约。** 分类规则在内存中计算，不创建 `mv3_model_chunks`、远端 request/attempt 身份或费用账本。领取、恢复与创建均复用 A3.0 语义范围阻塞；未知远端请求或迁移 hold 存在时不开始同范围本地工作。最终短写事务重新核验角色、来源及边界快照、词库语义／别名／规范化 token、预期字段 token、人工锁定与审核、取消意图及任务租约，然后一次提交候选字段修订、字段集合引用、任务结果引用和任务成功状态。提交前崩溃全部回滚；提交后调用方未收到返回，按任务幂等键和已持久化结果返回原字段集合，不追加新修订。无命中或歧义时任务可记录确定性完成与诊断，但不得写 `categories=[]`。当前 A3.0 `processing.py` 尚无 Categories 本地 step；`jobs.execute_claimed()` 拒绝 A3 契约任务，未来需新增受控本地领取／提交入口。现有字段状态不存歧义原因，现有 `revise()` 对已审核但未锁定字段也没有完整保护；这是 A3.1 开工前需解决的**最小实现缺口**。若必须修改 A3.0 公开契约、词库公开接口或正式初始化，先提交差异并另行审核，不在本轮改动。

**本轮状态。** 仅文档定稿提案；未修改源码、数据库、测试、服务或私人历史。Owner 尚需确认上表 6 类建议定义与规则范围、其余 10 类暂不自动候选，以及歧义诊断是否需要持久管理队列。确认后再单独授权 A3.1 隔离编码；真实 Provider、正式迁移和运行启用继续独立审批。

### A3.1 v2 源码合入状态（2026-11-10）

Owner 接受外部集成副本 `YOSAKI-A31-INTEGRATION-cf42afece56e4c7389e58fc5c6f5329e` 的限定验收：`A31_INTEGRATION_FINAL_SOURCE.json` SHA-256 为 `00C183AB1ACAC0361C11702F61C1D5117CE86C9A8FEDD557F15D2EDD096BEAD3`，该副本完整后端回归 492/492、0 失败、0 错误、0 跳过；前端未修改，构建未执行。本轮经运行隔离与字节核对，将两个既有模块及三个新增规则／合成测试文件精确合入正式源码。492/492 是外部集成副本的结果，并非正式工作区重跑。真实数据库未迁移，运行实例未切换，分类任务未新增启用；六类仅覆盖有限句式和相邻 user→assistant 直接回答，自动结果仍是待审候选。A3.0 的跨进程 Provider 对账与无身份历史 hold 解除仍不支持。A3.2 尚未授权。
### A3.1 v2 集成验收适用范围补充（2026-11-10）

`A31_INTEGRATION_BASELINE.json` 与最终 `A31_INTEGRATION_FINAL_SOURCE.json` 中，`backend/requirements.txt`、六个既有 Voice 后端文件（`config.py`、`elevenlabs_adapter.py`、`manager.py`、`router.py`、`schemas.py`、`storage.py`）以及 `frontend/src/App.vue`、`frontend/src/style.css`、`frontend/src/voice/VoiceSettings.vue` 的哈希相同。当前工作区这些文件已与最终清单不同；此外新增 Voice 后端 `audio_cache.py`、`audio_jobs.py`、`readable_text.py`、`speech_planner.py`，前端 `MessageAudioPlayer.vue`、`voiceAudioPlayer.ts`，两份 `test_voice_v2*.py` 及若干生成的 `.js` 文件，均不在最终 173 文件清单。哈希只能证明当前状态与集成快照分叉，不能给出各次编辑的精确时间或中间安装记录。原 492/492 仅适用于清单 SHA-256 `00C183AB1ACAC0361C11702F61C1D5117CE86C9A8FEDD557F15D2EDD096BEAD3`，不能覆盖这些后续并行状态。

依赖声明相对验收候选只增加 `markdown-it-py==4.2.0`、`mdurl==0.1.2`；本次只读检查时项目 `.venv` 两者均未安装。缺少此前环境包清单，不能断定安装环境何时变化。A3.1 的 processing、metadata 与分类规则均不导入这两个包，`main.py` 也未安装 A3.1 入口，因此没有发现它们对 A3.1 本身的直接依赖。当前 Voice 源码却存在 `main → voice.router → voice.manager → speech_planner → readable_text → markdown_it` 的静态导入链；若以后使用这个缺包的 `.venv` 重新导入应用，可能在 A3.1 执行前就失败。这是并行 Voice/环境的启动门槛，未通过本轮启动测试，也未作为 A3.1 回归通过。正式运行切换前须另行核对所用解释器和依赖，不为此改动业务或安装包。

### P1-A3.2 Tags 最小实施方案（待 Owner 审核；2026-11-10）

**原定目标与使用者。** 按此前获批的 P1-A3 阶段顺序，A3.2 是 Tags：现有角色词库匹配、新实体建议和关联级人工保护。Owner 用它审阅经历中有 RAW 直接证据的实体／主题，阿夜将来只能经独立发布与用途资格读取。它不承担 Keywords 语义扩展、Lexical 排序、向量召回或个人重要性认定。仓库未找到独立的 `P1-A3_IMPLEMENTATION_PLAN.md` 文件；本节沿用已批准阶段定义、`MEMORY_V2_P1A2.md` 与现有源码，不把 A3.1 有限分类规则改造成 Tag 语义前提。

**输入、输出与权威。** 输入仅为同角色已确认边界内的有序 RAW、当前可读来源指纹、角色内 `mv3_vocabulary` 的有效 Tag ID/名称/已确认别名及其语义、别名和规范化版本，以及现有 Tag 字段 token、审核与人工抑制。A3.1 无候选时仍独立运行；“无分类命中”不等于“没有主题”。输出分开：已存在 Tag 的直接证据匹配产生待审字段候选；新实体仅作带语言、原文位置与不确定原因的待核对建议，不自动创建词库 ID 或写成已确认 Tag。无可靠命中不写 `tags=[]`，不清除原值。Owner 通过已有可信会话审核具体字段修订；自动任务不审核、发布、召回或注入。

**现有契约缺口。** `memory_v2_metadata_jobs.py` 已能按名称/别名在 RAW 中查找现有 Tag，但 `METADATA` 路径无命中时仍生成 `tags=[]`，且不能安全给新实体建受控 ID；不得把此旧路径当作 A3.2 已完成。`memory_v2_metadata.py` 以字段列表存 Tag 并支持整字段 `None` 抑制，尚无单个 Tag 关联的人工删除/保留决策；`mv3_a3_audit` 仅存事件与证据哈希，不能作为可复核的新实体建议明细。A3.2 隔离实施需以最小增量补关联级决策及本地建议结果存储，均引用现有条目、字段与任务，不建立第二套 Tag 权威；正式数据库迁移另行审批。若执行前发现现有结构足以等价表达，应先证实并缩小迁移。

**拟改文件与事务。** 新增 `memory_v2_tag_rules.py` 和合成专项；定向扩展 `memory_v2_processing.py` 的 A3 本地任务类型与 Tags 执行、`memory_v2_metadata.py` 的关联保护与读时失效，必要时扩展 `memory_v2_vocabulary.py` 的只读匹配接口、`memory_v2_metadata_jobs.py` 对旧 METADATA 写入的兼容门槛，以及显式隔离 schema 模块。沿用 A3.0 scope、unknown/hold、租约、取消及幂等保护，A3.1 Categories 路径保持原样。规则计算不创建远端请求、模型子批次或费用；短写事务重新核验 owner/角色、边界、来源、词库、字段 token、人工关联决策、取消与领取代次，原子提交候选字段修订、建议/诊断引用及任务结果。提交前失败回滚；提交后重试返回同一结果。词库改显示名只更新展示；语义、别名或规范化变化、停用、来源/边界变化使实际依赖候选失效，人工历史仍保留。

**合成验收与停止。** 正例覆盖中文、日文及混合 RAW 中的已确认名称/别名、同条目多 Tag、相同词多来源；反例覆盖纯提及而无可靠实体/主题依据、否定/引用/玩笑、同形歧义、未确认跨语言等价、未知/停用或跨角色词库。新实体建议与正式 Tag 分开核对；无命中不清空，人工移除某一关联后重建不恢复它，人工分类/标题不变。测试来源修订、词库改名与语义变更、并发人工编辑、取消、过期 worker、崩溃与重试，以及 unknown/hold 和费用零增量。先专项，再在冻结外部副本上完整隔离回归；出现越权、人工结果覆盖、旧入口绕过 scope、自动审核/发布或既有路径回归即停止。回退关闭新任务入口、保留审计和人工决策，不覆盖整库或恢复旧失效结果。

**授权与启用边界。** 开发可只用外部源码副本、合成角色/RAW、临时 SQLite 与本地规则；不需要真实 Provider、H5-B 私人正文或正式服务。隔离 schema 扩展、测试与规则实施须 Owner 另行授权；真实数据库迁移、私人历史加工、自动发布和正式上下文接入再分别审批。A3.2 当前尚未编码或启用。
**A3.2 首组合成验收样例（拟定，非已运行结果）。** 下列命中均须绑定精确 RAW 偏移、source ID 与词库版本；候选仍待 Owner 审核。

| 合成 RAW（按角色及顺序） | 前提 | 预期 |
|---|---|---|
| 用户「我最近在学陶艺。」→阿夜「你想先做哪种陶艺作品？」 | 同角色有效 Tag「陶艺」 | 可提出「陶艺」待审候选；证据分别指向原文，不能推断课程或作品已完成 |
| 用户「俳句を作っているよ。」→阿夜「俳句の季語は決めた？」 | 同角色日文 Tag「俳句」 | 可提出「俳句」待审候选，保留日文原词与偏移 |
| 用户「YOSAKI の API を調べたい。」→阿夜「YOSAKI の API の仕様を確認しよう。」 | 已确认别名映射到同角色 Tag「YOSAKI」 | 可提出该稳定 ID 的候选；不附送技术讨论 Category 或语义扩展 Keyword |
| 用户「别人说过‘我在学陶艺’。」→阿夜「先回到刚才的排期吧。」 | 有效 Tag「陶艺」 | 引述中的词面不足以证明本段以陶艺为主题；保守不写 Tag，记录原因 |
| 用户「我没在学陶艺，只是提到这个词。」→阿夜「明白，继续说工作安排。」 | 有效 Tag「陶艺」 | 不写 Tag：双方明确只提词并转向工作；理由是主题证据不足，不是否定本身。不得写成“学过陶艺” |
| 用户「最近开始做陶艺。」→阿夜「你想做什么器物？」 | 角色词库没有「陶艺」 | 仅提出带原文证据的新实体建议，不创建 Tag ID、不写入 `tags` 字段 |
| 用户「我最近在学陶艺。」→阿夜「说说你做的东西。」 | Owner 已抑制该条目的「陶艺」关联 | 重算不得恢复该关联；其他未被抑制的 Tag 可独立评估 |
| 角色甲用户「我在学陶艺。」→角色乙阿夜「聊聊陶艺。」 | 来源跨角色 | 权限拒绝；不拼接证据，不返回正文 |

**草案收口。** 首版只使用受控词库的明确名称／已确认别名和可验证的主题表达；单条明确主题表述可独立支持候选，相邻轮次本身不构成关联，非相邻交流也不被产品语义排除。新实体建议限于明确原文词面，广泛实体识别、跨语言近义合并和自由主题推断留待后续评测。关联级抑制、建议明细和旧入口兼容的具体契约见下节；建议记录不是第二套有效 Tag 权威。

### A3.2 数据契约与旧入口兼容定稿提案（待 Owner 审核；2026-11-10）

本节细化上面的 A3.2 草案，不实施代码或迁移。**Tag 的唯一有效值仍是 P1-A2 `mv3_entry_fields(name='tags')` 的当前有效字段修订及其审核绑定。** Tag 候选只表示“本经历涉及该实体／主题”；不证明人物属性、行为已完成、引文中事件真实发生或关系结论。任务建议表只供获权管理审核，不进入有效 Tag 搜索、M2、P2 或正式上下文。

#### 规则 `tag-direct-topic-v1`

- 输入限本条目已经确认的有序 RAW；双方发言都可提供证据，单条明确的主题表达也可成立。首版可核验的表达为「聊／讨论／关于／想了解＋名称」「名称＋的具体问题／是什么」「名称について／を話す／の具体问题」，以及「我在学／我想做／最近关注＋名称」等明确话题陈述。行为句仅证明聊天涉及该主题，不证明行为完成。非相邻消息如各自明确谈同一名称可分别取证；仅相邻、代词接续或词频高均不足以合并话题。若实现只能识别更窄句式，要在规则版本与质量报告中标明实现限制，不能把 user→assistant 相邻写成 Tag 语义定义。
- 先在同角色、有效 Tag 词库中按条目语言与词库 `language` 匹配名称及**已确认** `aliases_json`；当前别名是字符串数组，没有逐别名语言字段，因此跨语言别名不自动匹配。NFKC＋casefold 只生成比较键，原文偏移和片段仍指向未改写 RAW；归一化后不能无歧义回映原文时拒绝。拉丁字母／数字／下划线要求完整词边界；中日文只接受明确句式与可判定的词形边界，不把「陶艺」作为「陶艺馆」的子串命中。重叠时只考虑完整、唯一的词形；同一词形或别名对应多个活动 ID 时记录歧义，绝不任意选一个、按 ID 排序选一个或跨语言猜同义。
- 作用域在具体表达：仅提一次词或引用后转题，证据不足；否定「学过 X」但继续问 X 的性质，可以支持“讨论 X”，不能支持“学过 X”；引述后主动讨论引文中的 X，也可支持主题，但不能证明引文事件真实发生。玩笑同理只判断是否确实围绕主题交流，不推导行为或情绪。全程不跨条目拼接，不依据 A3.1 分类命中与否决定 Tag。
- 无已存在 ID 的新实体建议仅从上述显式主题句式抽取有限长度的原文词形，语言无法核定时标 `und` 并待核对；不自动建词库、同义词或跨语言关系。规则版本、词库语义／别名／规范化 token、条目来源 token 与人工决策版本进入 A3.0 readSet 和幂等输入。规则升级不把旧建议或旧审核自动升级。

#### 最小显式 schema：`a32-tags-v1`

当前 `mv3_entry_fields` 是整字段值，`mv3_entry_field_checks` 只核验整个字段，手动 `tags=None` 只表达整字段抑制；`mv3_a3_audit` 不含可复核建议明细。因此仅靠既有表不能同时表达单 ID 人工决定和新实体建议。建议显式隔离迁移新增三表，不修改旧字段值、审核记录或正式初始化：

```sql
CREATE TABLE mv3_a32_schema (
  version TEXT PRIMARY KEY CHECK (version='a32-tags-v1'),
  schema_hash TEXT NOT NULL, applied_at TEXT NOT NULL
);
CREATE TABLE mv3_entry_tag_decisions (
  id TEXT PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES mv3_vocabulary(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL CHECK (revision>0),
  action TEXT NOT NULL CHECK (action IN ('keep','suppress','revoke')),
  source_token TEXT NOT NULL,
  basis_field_id TEXT REFERENCES mv3_entry_fields(id),
  owner_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(character_id,entry_id,tag_id,revision)
);
CREATE TABLE mv3_tag_proposal_revisions (
  id TEXT PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
  proposal_key TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision>0),
  tag_id TEXT REFERENCES mv3_vocabulary(id) ON DELETE CASCADE,
  surface TEXT NOT NULL CHECK (length(surface) BETWEEN 1 AND 80),
  language TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  source_token TEXT NOT NULL,
  dependency_json TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','rejected','handled','reopened')),
  origin_job_id TEXT REFERENCES mv3_jobs(id) ON DELETE SET NULL,
  origin_job_hash TEXT NOT NULL,
  reviewer_id INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(character_id,entry_id,proposal_key,revision)
);
CREATE INDEX ix_mv3_tag_proposals_entry
  ON mv3_tag_proposal_revisions(character_id,entry_id,created_at,id);
```

`mv3_a32_schema` 是独立迁移版本和 DDL 指纹门槛，避免以表存在代替完整迁移。决策行按 `(character_id,entry_id,tag_id)` 取最高 revision，是不可变的 Owner 操作历史；稳定 ID 不随展示名变化。来源 token 与 basis 字段标明当时依据，Owner 身份不能由客户端传入。建议行的 `tag_id` 可空：有 ID 但字段受保护时记待审关联；无 ID 时只记词面建议。`evidence_json` 仅含有序 source kind/ID、原文半开偏移与当前来源指纹，不复制整段 RAW；`dependency_json` 包含词库匹配集/目标 Tag 的语义、别名与规范化版本及预期字段、决策版本。决策表的 UNIQUE 索引已支持按条目／Tag 读取最新版本，无需重复建索引；建议表的额外索引用于受限分页。`origin_job_id` 指向当前任务；`origin_job_hash` 在任务行后来清理时仍保留来源身份，使 Owner 的拒绝记录不因任务删除而消失。合成验收须覆盖角色删除级联。服务端在同一写事务验证 entry/Tag 均属该角色、Tag 类型及启用状态；迁移中增加 INSERT 归属触发器拒绝跨角色或非 Tag ID，不能仅靠单列外键声称归属安全。应用不 UPDATE/DELETE 历史修订。

#### Owner 决定优先级与状态转换

| 最新决定 | 含义及允许转换 | 自动任务效果 |
|---|---|---|
| 无／`revoke` | 无活跃单 ID 指令；Owner 可新建 `keep` 或 `suppress`。`revoke` 保留历史，**不**批准标签或立即恢复旧关联 | 仅在来源、词库、字段状态均合格时重新提出待审候选 |
| `keep` | 仅保护**已有且有当前来源证据**的该 ID；可转 `suppress` 或 `revoke`，不能凭决定创建 Tag、审核字段或救回失效来源 | 重建不能无故删除该 ID；来源／边界或 Tag 语义改变时 keep 休眠，须 Owner 重核 |
| `suppress` | 阻止该稳定 ID 自动重提；可转 `revoke`。若当前字段含此 ID，决定与移除该 ID 的新字段修订须同事务写入 | 不因任务 ID、规则、别名或重建变化恢复；合并重定向到其他 ID 时先阻止自动写入并请 Owner 处理 |

优先级为：实时角色／来源／边界有效性 → 整字段 `None` 抑制、人工锁定或已核验字段保护 → 最新单 ID `suppress` → 有效 `keep` → 自动待审候选。整字段抑制下 `keep` 不能使字段复活；Owner 若要再次加入已抑制 ID，须显式撤销抑制并经现有带证据的人工修订。`suppress` 对同一稳定条目与 ID 在来源变化后仍阻止自动重加，但依据标为需复核；`keep` 在来源 token 改变后不授予有效性。Tag 重命名只改展示；停用或语义改变使自动候选失效，人工记录保留；别名改变须重新核验证据。拆分／合并产生的新条目不自动继承决定，需 Owner 按谱系明确处理。

字段保护采取保守首版：整字段 `None` 抑制时，自动任务不改字段、不提出单 ID 建议，只留不含词面的抑制诊断；其他已有人工、锁定或 `mv3_entry_field_checks` 审核/核验字段，自动任务也**不改** `tags`，但新的可靠匹配 ID 可写入 `mv3_tag_proposal_revisions` 供管理审核。未受保护的旧自动草稿可在重新核验后保留有效旧 ID、加入新 ID，但每次值变化都创建新 field ID/token 与新 field-set version，旧审核绑定绝不复制；没有变化复用原结果。Owner 执行 suppress 且当前字段含该 ID 时，原子创建手动移除修订，原 active version 不再是当前 draft，旧索引读时校验不得返回旧 Tag。单 ID 决定不能让已失权来源恢复，也不能自动发布整个字段。

#### 建议去重、状态与内部读取

`proposal_key=SHA-256(character_id,entry_id,language,tag_id或完整规范词形,有序来源ID/原文偏移/证据内容指纹)`；它不包含 task ID 或规则版本，也不把相似拼写、同名不同 ID 或不同来源处的词认作同一实体。相同 key、相同依赖与规则的重试或不同任务复用现有最新修订；规则/依赖变化只在显式重算时增 revision，读取只呈现最新且仍有效的版本。Owner `rejected` 后，同一证据不能因换任务、别名或规则版本自动重提；新可验证证据或 Owner 显式写入 `reopened` 修订才允许新建议，`reopened` 自身不批准 Tag。Owner `handled` 只表示已人工处理，不能自动创建 Tag。词库后来有匹配 ID 时，旧无 ID 建议标为需重核，不能自动提升；新任务须重新验证唯一 ID、来源和抑制后才能提出待审关联。

内部只读 `list_tag_proposals(db,character_id,entry_id,owner_identity,limit,cursor)` 先在同一一致性读事务确认 Owner、角色和条目，再查建议；逐条重验 RAW、偏移、来源/依赖与词库。无效来源只能返回不含词面、证据位置或旧正文的安全失效诊断；跨角色或撤权在任何计数/分页前拒绝。首版仅供隔离合成测试和后续受控管理适配，不安装公开路由、不写普通日志或浏览器存储。

#### 旧 `METADATA` 与 A3.2 的兼容矩阵

| 场景 | 未迁移旧契约 | 显式迁移 `a32-tags-v1` 后的有意调整 |
|---|---|---|
| 无命中 | 旧 `METADATA` 仍可能写 `tags=[]`；A3.2 入口拒绝运行 | 旧本地 METADATA 只处理原 Keywords，跳过 Tag 写入并记录 `legacy_tags_deferred`；新 Tags 无命中保持原字段；不得写空列表 |
| 人工值／整字段抑制／已审核字段 | 旧锁只阻止部分覆盖，已核验但未锁字段仍有风险 | 旧路径不再写 Tag；新路径将新增匹配放管理建议，不替换受保护字段，Owner 主动修订仍可用 |
| 单 ID 抑制 | 旧结构无此能力 | 新路径按稳定 ID 阻断；旧路径不写 Tag，不能通过别名或规则版本绕过 |
| 旧 Metadata Mock 输出含 `tags` | 旧 Mock 按既有流程结算及待审写入 | 拒绝 Tag 字段发布/写入，可靠用量仍按 A3.0 结算并记录明确原因；其他字段不得悄悄冒充完整成功 |
| 新旧任务并发 | 仅既有版本冲突保护 | 旧路径不写 Tag；新路径复核 A3 scope/unknown/hold、Tag 决定 revision 和字段 token，冲突拒绝而非覆盖；旧任务的 Keywords 保持自身版本检查 |
| 来源／字段 token 变化或 unknown／hold | 旧派发契约独立处理；没有 A3.2 保证 | 两入口都沿用 A3.0 未结算保护；新 Tag 最终提交必须重验，旧任务即使迁移前排队也不能写 Tag；无法安全转换的旧在途任务继续 unknown/hold，不假定未派发 |

该调整**只在显式 A3.2 schema 存在时**生效，属于待 Owner 批准的旧入口行为改变；不通过修改旧测试预期掩盖差异。迁移前行为不被冒称符合新保护。旧任务可保留原行及审计；无安全证明的在途任务继续 A3.0 unknown/hold。新旧 Tag 写入必须由同一迁移门槛控制，不能只在 UI 隐藏旧入口。若旧 Mock 中 `tags` 与其他字段混合且不能安全拆分，则整体拒绝产物、保留可靠用量，不能部分成功却报告原契约完整成功。

#### 样例增补、文件与验收边界

| 合成 RAW 与词库条件 | 预期 |
|---|---|
| 用户「我没学陶艺，不过想聊聊陶艺的釉料。」；阿夜「釉料配方可以讨论。」；有效 Tag「陶艺」 | 提出“涉及陶艺”候选；不声称用户学过陶艺，否定不是全局排除开关 |
| 用户「她说‘我在学陶艺’，我们来讨论这句话里的陶艺课程。」；阿夜「可以谈课程内容。」 | 可因引文之外的当前讨论提出主题候选；不证明第三人真的学过 |
| 用户仅写「陶艺」或引述完整陶艺问答后转向排期 | 证据不足，诊断而不写 Tag；相邻引文问答不算当前双方讨论 |
| 中文词库有 Tag「陶艺」和「陶艺馆」，RAW「陶艺馆」 | 只匹配完整且唯一的「陶艺馆」；不取词中「陶艺」 |
| 两个未合并的有效 Tag ID 同名，或旧数据存在别名碰撞 | 歧义拒绝并诊断，不能靠 UUID/创建时间选 ID |
| 有效 Tag「API」，RAW「ＡＰＩ 的状态码？」 | 仅在完整词边界和原文位置可回映时命中；证据保留「ＡＰＩ」原始偏移 |
| 中文 Tag 的别名数组含日文词形，但无逐别名语言依据 | 不自动当作已确认跨语言映射；等待人工词库契约，不推断同义 |
| 用户先说「聊陶艺」；中间出现其他短消息；阿夜后来明确问「陶艺的釉料？」 | 分别用各自明确表述判断；不靠相邻要求或时间接近推断接续 |

拟修改文件限 `memory_v2_tag_rules.py`（新增有限句式与原文偏移映射）、`memory_v2_tag_schema.py`（新增显式版本迁移）、`memory_v2_processing.py`（本地 Tags 任务及统一 scope/租约/取消）、`memory_v2_metadata.py`（受控字段提交、Owner 决定、读时失效）、`memory_v2_metadata_jobs.py`（迁移后旧 METADATA Tag 写入门槛）、必要的 `memory_v2_vocabulary.py` 只读唯一匹配与合成专项；不改正式 `main.py`、公开 API、前端、模型适配器或 Phase。迁移前以外部副本验证 FK、角色删除级联、表清单与已有字段/审核 hash；迁移后核对幂等和数据未变。回滚停用 A3.2 新入口，保留新增历史和人工决定，不整库覆盖或将旧错漏标签重新标为有效。

专项须覆盖以上正反例、同名/别名/Unicode/跨语言边界、整字段与单 ID 优先级、旧审核不继承、新建议重试/拒绝/词库新增、来源撤权安全读取、旧入口前后矩阵、unknown/hold、并发取消和领取代次、提交/恢复崩溃、零远端请求/费用/Phase 事件。需另行授权隔离编码与显式迁移；真实数据库、H5-B 私人 RAW、模型和正式服务仍不在范围。本节仍需 Owner 决定：是否接受首版保守的“受保护字段只出建议”、旧 METADATA 在新 schema 下停止写 Tag、以及拒绝建议须新证据或显式重开才可再次提出。推荐接受三项，以避免审核继承和人工抑制被旧入口绕过。

### A3.2 源码合入状态（2026-11-11）

Owner 已接受外部集成清单 `INTEGRATION_FINAL_SOURCE_V3.json`（SHA-256 `403E7D8E80F4322159D1F1800246F467232090E9AFC7F18357E412377916CB9E`）对应的限定验收：外部后端回归 557/557，0 失败、0 错误、0 跳过；前端构建未执行。七个获批 A3.2 源码及测试文件已与该候选逐字节核对并合入当前工作区；该 557/557 不是正式工作区重跑结果。本轮未迁移真实数据库、未切换运行实例、未新增启用 Tags 功能。正式迁移、运行启用、私人历史处理和 A3.3 均须分别审核；有限句式、2048 节点上限、混合自动字段整体 stale 及 A3.0 跨进程对账与历史 hold 恢复限制继续保留。

### A3.3 Keywords／Lexical 最小实施方案（待 Owner 审核；2026-11-11）

**原路线与现状。** 已批准的 P1-A3 顺序将 A3.3 定为 Keywords／Lexical：独立检索表达、有效字段搜索、草稿管理搜索和符合用途资格的候选检索。`MEMORY_V2_P1A2.md` 已区分 Keywords 的原文词项与语义扩展。当前 `memory_v2_metadata_jobs.py` 的旧 `METADATA` 本地任务从 RAW 生成 `literal` 词项，`memory_v2_metadata_search.py` 只有按词项数排序的管理／候选内部函数；没有 ANY／ALL、别名与扩展匹配说明、分类／Tag 过滤、版本化权重、草稿搜索或分页快照。现有词项生成先规范化再用原文 `.find()` 定位，不能据此保证 Unicode 变体的证据偏移。`memory_v2_metadata_api.py` 尚无搜索路由，`main.py` 未安装 A3.2 或 A3.3。仓库仍无独立 `P1-A3_IMPLEMENTATION_PLAN.md`；本节依据 Owner 已批准的最终 A3 计划、P1-A2 契约及当前源码，不重定已验收阶段。

**问题、用户和职责。** Owner 需要按可解释的检索词找到经历、查看草稿缺项和待审核词项；M2 将来只接收已发布且通过来源与字段资格的 Lexical 候选。Category 是结构化分类与过滤，Tag 是有直接主题／实体证据的稳定关联；Keyword 是独立的检索表达，可保存原文词形或明确标记的语义扩展。三者互不代写、互不证明记忆重要性。A3.3 不做向量、Hybrid、普通记忆浮现或正式上下文注入；`formalContext=false`。

**输入与输出。** 输入仅来自同角色、已确认边界的有序 RAW、P1-A2 当前字段修订及审核、角色内有效词库与已确认别名。词库候选、A3.1 分类或 A3.2 Tag 未命中不妨碍从 RAW 提取 literal Keyword；未审核的 Category／Tag 不成为有效搜索事实。新本地任务仅提出 `keywords` 待审字段，沿用现有 `{text,language,kind,weight,field,evidence,vocabularyId?}` 值结构和不可变字段修订；`literal/raw` 的每个证据引用必须精确映射原文偏移。`semantic_expansion` 必须另列输入字段、规则或词库依据，初版不自动从 RAW 推断新概念，也不能伪称直接说过。无可靠命中保持原字段不变；缺失仍是 missing，失效不返回旧词面；人工修订、整字段抑制、锁定及已审核字段不被本地任务覆盖。

**首版提取与版本。** 仅实现版本化的本地 `keyword-literal-raw-v1`：支持明确分词边界的原文词形、现有中日双字词及 ASCII 词项，使用保留原始偏移的 NFKC／大小写折叠映射；每个条目至多 32 项。不能证明偏移或词形归属时保守不提。词频、相邻消息或 Category／Tag 候选不产生语义扩展。规则版本、规范化版本、来源 token、字段 token、实际使用的词库语义／别名 token 写入 readSet 和字段依赖；更改规则后旧自动候选须读时失效或显式重核，不能只换任务 ID 复用。人工历史不因规则升级自动改写。

**三种搜索及排序。** 在显式选择的新 `metadata_lexical_v1` 契约中，Management Search 只查有效字段；Draft Management Search 明确标管理用途，可查获权草稿、候选／待审词项及 missing 状态，不混入有效词项索引；Candidate Recall 仅限已发布、来源与字段依赖有效且 `lexicalCandidateRecall` 合格的条目，M2 保留 `experienceKey` 去重和自己的最终协调排序。查询 `ANY` 命中任一规范项，`ALL` 要求全部去重项命中（可跨字段），默认 ALL；短语按一个查询项。初始实验权重 Title=4、Tags=3、Keywords=2、Synopsis=1；每查询项每字段只计最佳命中，精确规范词／已确认别名／已核验语义扩展系数依次为 1／0.75／0.5，Keyword 再乘已存检索权重。先按直接命中项数、别名命中项数、扩展命中项数、累计分数降序，再按 entry UUID 升序；配置独立版本化，分数不表示重要性。Category 同维 OR、异维 AND；Tag 过滤支持 ANY／ALL，默认 ALL 可调整。结果列出命中字段、匹配类别、证据引用、字段 token、分数贡献与配置版本，不返回失效来源旧词面。

**投影、分页与权限。** 现有 `mv3_metadata_terms` 可继续服务旧调用，但不能直接充当新搜索的完整依据。建议显式隔离迁移新增可重建的 `mv3_metadata_lexical_terms_v1`（角色、entry、字段修订／token、规范词、匹配类别、词库 ID、证据引用摘要、索引代的复合唯一键和角色／词项索引）及每角色 `mv3_metadata_lexical_state`（活动代、修订号、排序配置版本）；权威仍是 RAW、字段和词库，不复制正文。新代完整重建并核验后原子切换，旧代及旧搜索行为保留；迁移不挂入 `main.init_db()`。搜索先核验 owner、角色及来源，再计算数量／分页。签名游标只含规范查询与筛选指纹、角色、用途、排序版本、索引代／修订、最后排序键及有界来源／字段快照指纹，不含查询原文或正文；稳定服务端域隔离密钥缺失时拒绝续页。篡改返回无效游标，来源／字段／词库／索引改变返回 409；超出安全快照预算时拒绝续页，不跳过校验。20,000 条性能上限仍未验收。

**任务与旧入口兼容。** 新 `local_keywords_v1` 复用 A3.0 语义范围锁、unknown／迁移 hold、领取代次、取消与幂等；本地计算不创建 request、attempt、模型子批次或费用。提交短写事务重新核验角色、来源／边界、规则、词库、字段与人工修订、取消、租约及 scope，并原子保存候选修订、任务结果与完成状态；提交前崩溃回滚，提交后返回丢失时重试复用持久结果。为避免双写，建议仅在完整显式 A3.3 迁移后让旧本地 `METADATA` 跳过 Keywords，保留其未迁移行为；旧 Metadata Mock 的可靠结果仍结算已知用量，但不把延期的 Keyword 当作完成，结果或用量未知时仍保留 unknown 与预算。混合输出不能安全拆分时整体拒绝业务产物。新旧任务、Scheduler 和人工 resume 共用范围阻塞；跨进程 Provider 对账及无身份历史 hold 解除不作为本阶段能力。

**拟改范围与验收。** 在新的外部源码副本内拟新增 `memory_v2_keyword_rules.py` 与显式 `memory_v2_lexical_schema.py`；定向修改 `memory_v2_processing.py`（本地任务）、`memory_v2_metadata.py`（证据与字段保护／读时规则版本）、`memory_v2_metadata_jobs.py`（旧入口门槛）、`memory_v2_metadata_search.py`（新投影、三用途查询与排序）、`memory_v2_metadata_api.py`（仅隔离安装式搜索契约）及必要的 `memory_v2_recall.py`／契约适配，另加合成专项。保持旧调用默认语义和 M2 默认生成器不变；不修改正式 `main.py`、前端、Phase 或模型适配器。若实际实现证明无需新表即可满足可解释排序、稳定分页及原子索引切换，应先以专项证据缩小迁移范围，不擅自扩大公开契约。

| 合成输入及状态 | 首版预期 |
|---|---|
| 用户 RAW「ＡＰＩ の仕様を調べる」；来源有效 | 原文偏移指向「ＡＰＩ」；规范词可匹配 `api`，结果仍展示原始词形，不把规范化串当原文 |
| 用户 RAW「俳句の季語を探す」；无分类、无 Tag | 可以提出有偏移的 literal「俳句」「季語」；不依赖 A3.1／A3.2 命中 |
| 用户 RAW「没去陶艺课，想查釉料」 | 可提直接出现的词；不得生成“上过陶艺课”或未出现的语义扩展 |
| RAW 只谈「陶艺」，查询「手工艺术」且无已确认映射 | 不凭相似意思命中；若后来由 Owner 建立扩展映射，命中须显式标 semantic_expansion 和版本 |
| 已确认别名「ＡＰＩ」→同角色规范词「API」；另有冲突别名 | 唯一已确认别名可命中；冲突时不任选词库 ID，给保守诊断 |
| 有效 Title 命中甲词、有效 Keyword 命中乙词；查询模式 ALL／ANY | ALL 要求甲乙均命中，ANY 至少一项；同项重复不刷分，命中解释显示各字段贡献 |
| 草稿有待审 Keyword，或已有人工抑制／审核 Keyword | 仅 Draft Management Search 可查看待审状态；本地任务不覆盖人工决定；Candidate Recall 不因草稿词项放行 |
| 来源撤权、字段 token 变化、旧任务计算后被取消 | 读时不返旧词面，提交拒绝或安全终止；无新字段版本、无费用或 Phase 事件 |

专项覆盖日中混合、NFKC 偏移、词中子串、引用与否定不造事实、无命中不清空、规则升级、词库改名／语义和别名变更、角色撤权、来源失效、人工竞争、旧入口迁移前后、取消／过期 worker／崩溃重试、unknown／hold、游标篡改与稳定排序，以及 M2 去重与正式上下文始终关闭。随后在冻结外部副本运行相关专项与完整后端隔离回归，记录跳过与依赖环境；Mock 和有限规则只证明工程契约及声明的词形范围，不证明通用多语言质量。

**停止与后续决定。** 越权、旧正文泄漏、人工结果覆盖、语义扩展冒充原文、unknown／预算绕行、候选越过发布门槛、不可恢复事务或旧业务回归即停止并保留合成现场。回退关闭新本地任务、搜索 profile 和安装式路由，保留字段历史、人工审核和旧投影；不得以旧执行器继续写入受保护字段或整库覆盖。A3.3 的隔离编码与显式合成迁移尚需 Owner 授权；真实数据库备份／迁移、私人历史加工、真实 Provider、向量、自动发布与正式上下文各自另行决定。推荐首版保持纯本地 literal 生成，语义扩展仅作为明确核验的检索映射或待审人工字段，待真实质量评测后再评估自动生成。

**A3.3 限定源码合入状态（2026-11-11）。** `keyword-literal-raw-v1` 的本地 Keywords 待审任务与三用途内部 Lexical 搜索已在外部隔离副本验收；八个获批源码／测试文件已按候选字节合入当前工作区。验收绑定外部 `A33_INTEGRATION_FROZEN_SOURCE.json`，SHA-256 `576CDEC76DF06D2985BB1C1919CECC65200CC60C7AB84BEBC539CC9052EDAC51`，专项 55/55、完整后端 636/636，均为 0 失败、0 错误、0 跳过；这不是正式工作区重新运行应用测试的结果。合入记录保存在仓库外 `C:/Users/ASUS/AppData/Local/YOSAKI-A33-MERGE-e9e7af8029334a9cae04c4f7e01d57a0`，原集成报告及日志留在 `C:/Users/ASUS/AppData/Local/YOSAKI-A33-INTEGRATION-15e5414add5b4d16a7c8446326ae2a63`。

本轮未迁移真实数据库、切换运行实例或新增启用 Keywords／Lexical。正式 HTTP 搜索、M2 默认接入和正式上下文仍未接线。原万条窄合成夹具首页约 33.9 秒仅为原版本测量，不是交互性能通过。逻辑资源上限与协作式截止已实现；进程 RSS 硬上限、单次阻塞调用抢占和复杂真实负载尚未验收。A3.0 跨进程 Provider 对账、无身份历史 hold 解除及旧路径同角色保守阻塞限制继续保留。正式迁移、服务切换、私人历史加工、性能优化、HTTP／M2 接入和 A3.4 均须另行审核。

### A3.4 单经历 Synopsis 最小隔离实施方案（待 Owner 审核；2026-11-11）

**路线依据与现状。** 原 P1-A3 任务书第 3.4 节和已批准的 A3.0～A3.7 顺序均将 A3.4 定为“单经历梗概”：简洁、自然、忠实，覆盖整段经历及其转折、条件和明确表达；不推导感受、动机、关系或助手自述的外部真实性，区别于详细 NARRATIVE 和周期 DIGEST。当前 `memory_v2_metadata_jobs.execute()` 的本地 `SYNOPSIS` 仅把完整短消息逐条拼接，超过 3000 字拒绝；`dispatch_mock()` 可存一个合成梗概字符串，但没有逐陈述证据和全范围忠实性验收。`memory_v2_fidelity.validate_chunk()` 只处理 NARRATIVE／DIGEST；已有 `review_field()` 可由 owner 审核具体字段 token。A3.3 Lexical 内部搜索能消费已有效 Synopsis，但不能生成或审核它。仓库没有独立的 `P1-A3_IMPLEMENTATION_PLAN.md`；本节依据原任务书、已批准计划、`MEMORY_V2_P1A2.md` 和现有源码，不把旧摘录称为自然梗概，也不改 A3.5／A3.6 的职责。

**目标、使用者与边界。** Owner 需要一段可独立阅读的单经历概括，用于审核 Chronicle 条目；经独立字段审核及条目发布后，既有管理读取、内部 Lexical 和 P2 才可按各自用途资格使用。未来 P3 向量仍要求有效 Title＋有效 Synopsis。首期只完成全范围输入、候选、证据、失效和 Mock 工程链，不宣称自然语言质量已由 Mock 证明。A3.4 不生成 NARRATIVE、周期 DIGEST、时间事实、心理解释或 Importance；`formalContext=false`。不接正式 HTTP、M2 默认路径、聊天上下文或真实 Provider。A3.3 万条搜索延迟不阻塞本阶段的候选写入；正式检索启用仍受其性能门槛约束。

**权威输入与版本化输出。** 唯一内容权威为当前角色下、已确认边界所引用的完整有序 RAW：逐项核对来源 ID、speaker、内容／状态指纹及边界快照。无确认边界的普通 v2 草稿仍使用其已保存的有序来源集合，不能跨条目拼接或以相邻时间推定话题。默认不以 Title、Categories、Tags、Keywords、已有 NARRATIVE 或旧梗概作为概括事实来源；这些字段缺失、无命中或失效都不阻止从有效 RAW 计算。输入 `readSet` 绑定角色、稳定条目 UUID、字段预期版本／token、有序来源 token、边界身份（存在时）、输入分批配置及规则／校验版本；发生时间未经核实则保持未知，不用 `recorded_at` 补造。输出只是一项不超过现有 3000 字上限的 `synopsis` 字段修订，初始为待审核候选；不复制 RAW 到新权威表，不自动审核、发布或提升召回资格。

推荐新 profile `synopsis-full-entry-mock-v1`：输入清单必须覆盖该条目的每一条有序来源，输出 `{text, units, sourceCoverage}`。每个 `unit` 含梗概中的原文字符区间及至少一个 `{kind,id,start,end}` RAW 证据区间；`sourceCoverage` 按输入顺序列出全部已检查来源及其引用／省略状态，省略短应答可以接受但必须可供 owner 对照。证据区间使用原始 RAW 偏移，不用规范化文本索引，不能指向条目外来源。字段仍用现有不可变 `mv3_entry_fields.value_json/evidence_json/dependencies_json` 和 `mv3_entry_field_links` 保存，`value_json` 保持现有字符串形状；新结构仅存入版本化的 `evidence_json`，不改变旧字段读取契约。每个语义句段须有证据，但引用存在仅证明可回查，不能自动证明改写忠实。任务和分批复用现有 `mv3_jobs`、`mv3_model_chunks`、`mv3_job_field_results`；首版不要求新增表或正式 schema 迁移。若实际编码发现既有列不能原子保存覆盖、证据或结果引用，须提出精确 schema 差异，不能用无依据的 JSON 伪装已验收契约。

**生成与审核取舍。** 保留旧 `metadata-extractive-v1` 的短消息逐条摘录作为可明确区分的保守路径；它不冒充新的概括能力。新路径使用仅注入合成输出的本地 Mock 验证全范围协议，不连真实模型。超过单次输入预算时按有序来源确定性分批，先验证每批范围和映射，再合成最终候选；批次中间文本不是已审核字段，最终候选仍须回指 RAW。单条消息或整段经历无法在既定分批与输出上限内安全处理时明确拒绝，不截断、跳首尾或仅概括前一批。结构校验覆盖输入覆盖率、speaker、引述与否定／条件／更正、未知执行结果、输出长度及证据偏移；检测到来源外事实或歧义时拒绝或标记待核对。自由改写的语义忠实性、重要内容遗漏和自然度由 owner 对完整 RAW 审核，模型自评、来源 ID 列表或结构校验均不授予审核效力。后续真实 Provider 质量试运行须单独批准模型、Key、合成／私人样本范围和预算。

**任务、并发及旧入口。** 使用 A3.0 的 `synopsis` 语义工作范围、不可变派发身份、预算预留、unknown／迁移 hold、租约和取消意图。Mock 的模拟用量与真实美元账本分开；结果或用量不明继续 unknown 并保留预留，绝不自动重派。确定结果但来源、边界、字段 token、人工修订或取消意图已变化时，结算已知用量而拒绝业务写入。最后的权限、来源、预期字段、人工锁定／抑制／已审核保护、取消及领取代次复核，与候选字段修订、结果引用和任务终态处于短写事务；同一输入重试复用持久结果，提交前失败回滚，提交后返回丢失不得多建字段版本。旧本地／Mock `SYNOPSIS` 入口保留既有短摘录与合成行为，但也须通过同一个 `synopsis` 范围及人工保护检查；旧任务在新任务期间不能覆写，旧 unknown／hold 不能被新任务绕过。若旧入口无法安全共存，先保守拒绝冲突写入并以合成回归确定最小兼容改动，不静默改变旧审核。Owner 主动修订和审核仍走既有可信操作，审核绑定具体字段 token；已审核 Synopsis、人工锁定和手动 null 抑制均不被自动任务覆盖。

**失效与用途。** 来源撤权、角色停用、边界或内容／状态指纹变化，使候选在读时失效且不返回旧词面；在途计算提交拒绝。输入 profile 或忠实性校验版本发生不兼容变化时，旧未审候选不得冒充新规则结果，旧在途任务不得由新执行器提交；已由 owner 审核的历史修订及凭据保留，发现校验缺陷时显式标记受影响版本失效，不原地改写审核。Synopsis 修订只使实际依赖其 token 的 Lexical 投影、未来向量输入或 P2 profile 失效；`raw_sources_v1` 周期 DIGEST 不因梗概改动无条件失效。待审候选仅供获权草稿管理查看，不能进入 Management Search 的有效词项、Candidate Recall、P2 已审核文本或正式上下文。索引重建是后续显式操作，不是任务成功副作用。

**建议文件范围及一次性隔离实施。** 从授权时当前源码建立外部副本；新增 `backend/app/memory_v2_synopsis.py`（输入覆盖、结构和 RAW 证据校验），定向修改 `memory_v2_processing.py`（版本化 `synopsis` 任务与 A3.0 生命周期）、`memory_v2_metadata_jobs.py`（旧 SYNOPSIS 共存保护）、`memory_v2_metadata.py`（新证据格式与读时失效）、`memory_v2_fidelity.py`（梗概结构／保守忠实性检查）及 `memory_v2_model_adapter.py`（仅 Mock 所需的有界分批和输入／输出协议，不加 Provider 传输）。新增 `tests/test_memory_v2_a34_synopsis.py` 与纯合成验收夹具；需要时最小复用 `tests/a3_gold.py`，不把私人文本加入夹具。优先复用既有表与内部入口，不新增公开 API、前端、依赖、`main.py` 安装点或 Phase 修改。实施时先专项，再对相关并行漂移作集成审查，冻结候选后只跑一次最终完整后端隔离回归；无相关变化不重复复制相同副本或重跑相同版本。

| 纯合成经历／状态 | 预期候选与验收重点 |
|---|---|
| 用户「周六想看电影。」；阿夜「可以先选片。」；用户「其实周六要加班，改到周日。」 | 梗概保留原计划及更正，不把周六观影写成已发生；三条有序输入均进入覆盖清单，关键句段有 RAW 证据。 |
| 用户「如果明天不下雨，再去公园。」；阿夜「先看天气。」 | 保留条件，不写成已去公园；没有可信发生时间时不写精确日期。 |
| 用户「我没有接受那份工作，只是讨论岗位要求。」；阿夜「我们可以列出要求。」 | 可概括为讨论岗位要求，不写成已入职；不从否定词推断关系或情绪。 |
| 用户「友人は『完成した』と言ったが、私はまだ確認していない。」；阿夜「確認してから記録しよう。」 | 中日混合保留引述与未核实状态；不将第三人自述变成已验证完成。 |
| 第一批谈旅行准备，第二批更正行程，第三批决定暂缓；超出单批输入 | 完整分批和最终证据覆盖三批，梗概保留转折；缺一批、重复来源、错序或超限均拒绝，不只概括首尾。 |
| 多条短回应夹在两个主题间，但只属于同一已确认条目 | 不凭相邻或时间推断因果；允许省略短回应，覆盖清单仍列出，Owner 可核对省略是否改变含义。 |
| 梗概声称「她因此很安心」但 RAW 只讨论排期 | 拒绝无来源心理结论；即使引用合法片段，也不得自动通过语义忠实性审核。 |
| 来源、边界、角色、字段审核或手动抑制在 Mock 计算中变化；另有 unknown 批次 | 结果不得覆盖人工或失效来源；可靠用量只结算一次，任一未知批次使父任务维持 unknown，预算不释放。 |
| 提交前崩溃、提交成功但返回丢失、过期 worker 迟到提交 | 回滚或复用同一持久结果，拒绝旧领取代次；不生成第二字段修订、第二派发身份或重复费用。 |

**验收、停止和回退。** 专项需断言全来源输入覆盖、逐句证据、误归属／否定／条件／更正／日语、长输入分批、超限拒绝、旧本地摘录及 Mock 共存、人工审核保护、来源／角色撤权安全读取、规则升级失效、unknown／hold、并发取消与结算、幂等及零真实 Provider／网络／Phase 事件。Mock 只证明协议和状态，不证明自然语言梗概质量；真实模型忠实性单列待验。任何越权、旧正文泄漏、人工结果覆盖、无依据事实发布、unknown／预算绕行或既有业务回归，保留合成现场并停止。回退停用新 `synopsis-full-entry-mock-v1` 入口，保留字段修订、审核和任务账本，不恢复可绕过保护的旧执行器、不整库覆盖。正式数据库迁移、服务切换、真实模型、私人历史加工、正式检索／上下文接入及 A3.5 分别审批。

**推荐与 Owner 决策。** 推荐首批隔离授权一次包含：当前源码外部副本、全范围 Mock 工程实现、专项、并行差异核对、冻结候选及一次最终全量回归；真实模型和 H5-B 不在其中。产品语义只需 Owner 决定一项：是否接受“每个梗概句段可回查 RAW、但结构校验不能代替 owner 忠实性审核”的首期审核门槛。推荐接受，以保留自然概括空间且不把机械证据映射当作事实证明。是否扩大自动发布、真实 Provider、私人样本与正式接入均为后续独立授权，不阻塞合成工程实施。

### A3.4 单经历 Synopsis 源码合入状态（2026-11-11）

Owner 已接受外部冻结清单 `A34_FROZEN_SOURCE.json`（SHA-256 `8926C9CB2C4C1A273239CC19385034D936E7621E110329EEBA69EFD32615BB58`）对应的 Mock 工程验收：Synopsis 专项 22/22、完整后端隔离回归 673/673，均为零失败、零错误、零跳过。七个获批源码及测试文件已逐字节核对并合入当前工作区；673/673 是外部冻结副本的结果，不是正式工作区重跑。

新路径只接受可核实的 H3.2 确认边界，以完整有序 RAW 生成确定性有界批次，最终合成输入由批次产物和来源身份组成并另受大小及预算限制，候选逐句段回指 RAW，保持待审并复用 A3.0 统一结算。来源覆盖清单只证明输入已提供给计算，不证明模型理解或无重要遗漏。旧摘录及 NARRATIVE／DIGEST 契约保留。真实模型自然度、忠实性与压缩遗漏未验收；普通来源列表不能代替确认边界。A3.0 跨进程对账及无身份历史 hold 解除仍不支持。本轮未迁移真实数据库、切换运行实例、注册正式入口、调用真实模型或新增启用 Synopsis 功能；正式接入和 A3.5 继续分别审核。

### A3.5 第一人称详细 NARRATIVE：开工前最小方案（待 Owner 审核；2026-11-11）

**原定目标与阶段边界。** 原 P1-A3 任务书第 3.5、10 节把 A3.5 定为“第一人称详细对话纪事的真实模型精修质量验证与发布”；已批准的最终 A3 计划则把 A3.5 工程部分与真实质量试运行分开。两者并不冲突：下一次可一次授权的隔离编码单元只完成可追溯、可审核、可失效的 v3 NARRATIVE 工程链及合成质量基线；A3.5 的真实模型质量与可发布性结论仍须另行批准模型、测试凭据、样本、费用及人工评审。Owner 阅读 RAW 与详细纪事并决定是否审核；阿夜未来只通过独立用途资格使用已确认版本。Synopsis 是单经历压缩梗概，NARRATIVE 保留双方表达和轮次细节，L1～L4 DIGEST 是已选周期的压缩投影；三者不得互代或自动升级权威。

**已具备与准确缺口。** `memory_v2_model_adapter.py` 已有 `ssly-dialogue-chronicle-m35-v3`、有界逐轮分批和本地 Mock；`memory_v2_fidelity.py` 检查来源覆盖、speaker、部分否定／条件／更正／日语及明显无来源主观词，不能证明自由改写忠实；`memory_v2_jobs.py`、`memory_v2_processing.py` 已提供派发身份、预留、unknown、取消意图、范围锁和结算；M3 `mv3_versions` 已存待审正文、有序来源、映射、人工审核和激活；P1-A2 的 `narrative` 字段只引用**已激活且来源顺序一致**的 M3 版本。M3.6 N1 真实输出使用旧 v1，不能证明 v3 质量。缺口是：现有 M3.5 按任意来源集合建任务，不绑定已确认条目边界及其版本；逐轮映射只证明身份和输出区间，`rawStart/rawEnd=NULL` 的自由改写尚须人工核对；现有 A3.4 待审 Synopsis 不是 NARRATIVE 的事实权威；没有经当前 v3 真实评测的发布依据。正式 `main.py` 未安装新路径。

**输入、输出和依赖。** 新的条目级路径只接受同角色、可实时核实的 H3.2 确认边界与该条目完整有序 RAW；普通持久来源列表不能代替确认。A3.4 `confirmed_snapshot()` 可复用其确认和收据检查，但不读取待审 Synopsis 作为事实或生成输入；若将来显式选用已审核派生字段，须新 profile 绑定其字段 token、审核及来源失效，不能复用本版 RAW profile。冻结的 `readSet` 至少含 entry UUID、角色、边界方案／来源快照绑定、有序来源身份及 speaker、内容／状态指纹、当前 narrative 字段 token／人工抑制状态、提示词 v3、规则／校验版本、批次边界和预算；原始本地时间未知时仍未知，不以记录时间补造发生时间。输出为一份 M3 `needs_review` NARRATIVE 版本、逐轮有序 `mapping_json`、诊断与任务结果引用；不复制 RAW 到新权威表，不生成 Synopsis、DIGEST、重要性或心理结论。每条模型输出仍按输入每轮一项，正文可在轮内自然分段，来源 ID 只存映射而不写进可读正文；不得伪造自由改写的逐字 RAW 偏移。

**边界绑定与旧记录兼容。** 推荐不增加表：为**新规则版本** `model-narrative-a35-v1` 的每个既有映射项增加同一个 entryId、sourceToken、boundaryBinding 与校验版本；保留旧 `mapping_json` 数组形状及旧 M3.5／M3.6 的读取算法。新版本的 M3 读时、审核及激活前由版本专用校验核对这些绑定、当前条目有序来源与确认状态；映射连同来源、正文、规则／提示词进入既有审核 binding 和 `input_hash`。旧版本没有此标记，不追溯改写、伪造确认或继承审核。若实施验证发现现有映射无法在**所有**审核和读取路径可靠承载此绑定，应先提交必要的最小显式 schema 差异，不能仅把令牌写进任务 payload 后声称版本具备读时失效。来源、边界或权限变化使新候选不可审核、不可激活、不可返回旧正文；提示词／校验版本变化使旧在途任务和未审候选不能冒充新版。无关 Title、Tag 或 Synopsis 变化不使 RAW 输入的纪事失效。

**人工决定与用途资格。** 生成成功只代表请求结算及待审版本持久化。Owner 经现有可信身份入口对完整 RAW、正文、发言归属、日语、括号、条件、否定、更正和可能遗漏逐项核对；审核绑定版本及当前来源后才可激活 M3 纪事。P1-A2 字段目前拒绝引用待审 M3 版本，因此**不在生成时写入 narrative 字段**；只有 M3 审核激活后，Owner 才能通过受控字段修订关联该版本，并单独完成字段审核与条目发布。新字段集合不继承旧审核；人工修订、锁定和手动 null 抑制不得由自动任务覆盖。原有已审 Title 和其他字段保持原修订。未审纪事不能进入有效管理搜索、P2 已审文本、候选召回或正式上下文；`formalContext=false`。即使纪事有效，没有有效 Synopsis 也不取得初始 Vector 资格。真实模型质量通过也不自动授予激活或自动发布。

**执行、故障和恢复。** 推荐新建条目级协调模块，复用 A3.0 `processing.create` 的 `writeSet=['narrative']`、统一 `begin_dispatch/settle_dispatch/recompute_parent` 及 M3.5 有界分批、v3 适配协议；不再建任务表或结算系统。每批在派发前事务中核验角色、边界、来源、租约、字段保护、范围锁、不可变 request/attempt 身份和预算预留，提交后才调用注入的本地 Mock；输出和模拟用量在短写事务中结算。全部批次确定且复核当前 `readSet`、取消、领取代次、审核保护后才原子创建一个待审 M3 版本与任务结果引用。单批或多批缺失、超限、错序与重复均拒绝；不截断 RAW，也不把 A3.4 的中间梗概当作遗漏轮次的替代。确定用量遇来源修订或取消仍如实结算，拒绝业务产物；任一请求结果或用量不明则父任务保持 unknown 和有效预留，不重派或派发后续批次。只有已证明无未结算请求的本地任务可安全暂停／恢复；迟到结果须持原派发凭据，失去领取资格的旧 worker 不能提交候选。提交前崩溃回滚；提交成功而返回丢失按任务、来源、规则及输出绑定复用同一版本。跨进程可靠对账和无身份历史 hold 解除仍不支持，不能以此作为恢复路径。

**拟修改文件与迁移。** 推荐新增 `backend/app/memory_v2_narrative_processing.py` 负责边界 readSet、任务和版本专用校验；定向修改 `memory_v2_processing.py`（显式新 executionKind）、`memory_v2_model_adapter.py`（v3 请求约束）、`memory_v2_fidelity.py`（只增强 NARRATIVE 合成校验，不改变 DIGEST）、`memory_v2_derivation.py`（新 profile 的读时／审核／激活绑定检查）、`memory_v2_metadata.py`（仅在需要时验证新 M3 引用的字段依赖），必要时复用并最小调整 `memory_v2_jobs.py`。旧 `memory_v2_m36_trial.py` 和 Responses 传输协议保持不变；现有 M3.5、M3.6 入口原样兼容。新增 `tests/test_memory_v2_a35_narrative.py` 和纯合成评测样例。首选复用现有 `mv3_jobs`、`mv3_model_chunks`、`mv3_versions`、`mv3_source_refs`、`mapping_json`、`mv3_reviews`、P1-A2 字段表，**不预设新 schema 或公开 API**；若验证证明需要新结构或公开契约，先报告精确差异，不挂入普通初始化。正式迁移、路由、前端、M2 默认路径和聊天上下文均不在隔离编码单元。

**合成验收集与通过门槛。** 正例：同一确认条目内中日交替、双方多轮短消息、跨三批更正、条件与否定、引述归属、含新事实的括号、有意义的省略号；按逐轮来源和原文核对输出、映射、诊断及待审状态。反例：待审／失效 Synopsis 被当成事实、缺确认边界、漏轮／换序／重复／跨角色、编造内心或已完成事件、丢日语／否定／条件、混合语言发言前缀、单条或总预算超限、来源或边界在计算中变化。并发与回滚：人工锁定或抑制、撤权、取消、双任务范围冲突、过期 worker、重复回调、迟到确定结果、多 unknown、结算后业务写入失败及提交后返回丢失；断言有效预留、费用、任务状态、版本数量和旧审核不变。保留 M3.5/M3.6、A3.0～A3.4、P1-A2、P2、M2、Runtime、Voice 与 Phase 的适用回归。开发副本取执行时当前允许源码；专项通过并核对相关并行漂移后冻结同一源码指纹，仅运行一次最终完整后端隔离回归。Mock 的工程通过与合成句式检查**不等于**真实模型自然度、逐条忠实性或重要内容遗漏通过；后者须另批有界真实模型试运行和 Owner 人工评审，N1 v1 不计为 v3 证据。

**停止、回退和待决事项。** 权限／来源越权、旧正文泄漏、自动审核或激活、人工结果覆盖、unknown／预算绕过或既有路径回归立即保留现场并停止。回退停用新条目级任务入口和新版本读取资格，保留已结算用量、历史版本与审核，不整库覆盖或让旧执行器领取新任务。推荐保持“RAW 唯一事实输入、每轮一项、Owner 忠实性审核后再激活”的首版取舍，避免从 A3.4 的 Mock 梗概传递未经审核的事实。需 Owner 单独决定的只有后续真实试运行范围（模型、专用 Key、样本、预算和质量门槛）以及将来是否扩大自动发布；推荐先做合成工程验收，再以零关键事实／归属错误的真实样本人工评测决定是否扩大，不预设通过。本轮仅计划，未授权编码、真实 Provider、私人历史、正式数据库迁移、服务切换或 A3.6。


### A3.5 修复版源码合入状态（2026-11-11）

Owner 接受外部冻结清单 `A35_SETTLEMENT_FINAL_SOURCE.json`（SHA-256 `EC14FE4CD48800A5BF7961B1394EF36BB7A81AB92F6C4B2F2DBB406E85CA78E7`）对应的 Mock 工程验收：A3.5 专项 32/32、同一冻结源码完整后端隔离回归 715/715，均为零失败、零错误、零跳过。五个获批源码及测试文件已逐字节合入当前工作区；这些测试结果属于外部冻结候选，并非包含后续 Voice 并行变化的正式工作区全量回归。旧 24/24、707/707 仅保留为旧候选的历史证据。

新路径以完整有序 RAW 为事实输入，执行有界 Mock 派发与可靠模拟用量结算，只生成 M3 待审 NARRATIVE 版本。输出持久化明确失败且原事务已回滚后，合法原派发身份可在独立事务结算已确认用量；缺失持久输出的批次不得重派或用于组装纪事。只有结果与用量均已持久化的批次，才可按受控恢复规则继续组装。存储无法提交或核实时，已观察用量仍可能无法落库，任务保持 unknown／hold 与有效预留；跨进程 Provider 对账及无身份历史 hold 解除仍不支持。生成、Owner 审核、M3 激活、字段关联、字段审核和条目发布分别受控。有限合成检查不证明真实模型自然度、忠实性、重要内容无遗漏或可发布性。此轮仅源码合入，未迁移真实数据库、调用真实 Provider、读取私人历史、切换运行实例、注册正式入口或新增启用 NARRATIVE 功能；真实质量与运行接入另行审核。外部验收及合入证据位于 `C:/Users/ASUS/AppData/Local/YOSAKI-A35-054da75856dc4686a9939e4369489611/A35_SETTLEMENT_FINAL_REPORT.md` 和本轮仓库外合入记录。

### A3.6 Temporal DIGEST 自动维护与上下级失效：隔离实施方案（待 Owner 审核；2026-11-11）

**原定目标及职责。** 原 P1-A3 任务书第 3.6 节及阶段表规定 A3.6 为“Temporal DIGEST 自动维护及上下级失效”：L1 周、L2 月、L3 季度、L4 半年逐层折叠，L0 保持详细经历，L5 只作年度标题索引；以真实下级和来源依赖支持 P2 对**已选**记忆的时间粒度投影，未来降低阿夜上下文负担。Categories 是结构化分类，Synopsis 是单经历梗概，NARRATIVE 是逐轮详细纪事；它们都不能代替周期 DIGEST。M2／P3 负责候选检索，P2 不扩大候选范围；本阶段不注册正式聊天上下文、普通浮现、向量或新 UI。

**现有基础与明确缺口。** M1 有来源身份、状态及有序 RAW；P1-A2 有已发布条目、独立字段审核、`timeEvidenceToken`／`timeToken` 与有限 `digest_token()`；M3 有待审 DIGEST 版本、有序来源、子版本依赖、人工审核、激活与读时失效；P2 有 L0～L5 树、`raw_sources_v1` 摘录、已审核版本绑定、缺失时退到有效单经历文本及 RAW 回查；A3.0 有范围锁、租约、预算、不可变派发身份、unknown 和结算；A3.5 已修复确定用量与结果持久化、最终业务提交的边界。这些均复用。当前 P2 绑定表的 `input_profile` CHECK 仅接受 `raw_sources_v1`，`digest_token()` 实际也仅实现 RAW／Synopsis／NARRATIVE 单一字段 profile；虽然 A3 契约列有 `child_digests_v1`、`composite_v1`，尚无逐成员实际输入清单、自底向上任务、混合依赖或新 profile 的 M3 读时校验。旧 `digest.projection()`／P2 `_members()` 还会以 `recorded_at` 换年龄档，与较新的 P1-A2 时间契约冲突；应在本阶段做有版本边界的定向修正及旧行为回归，不把旧记录时间推断为发生时间。旧 RAW profile 版本和审核不原地改写。

**输入选择和资格。** 只从同角色、当前已发布且来源有效的条目以及经过完整审核、激活且读时有效的派生字段取输入。L1 对每个实际成员依序选有效且已审核 NARRATIVE，缺失时选已审核 Synopsis，再缺失可用经验证的有序 RAW；全成员分别为同一种输入时用 `narrative_v1`、`synopsis_v1` 或保留的 `raw_sources_v1`，混用时必须用 `composite_v1`。L2～L4 默认要求**直接下级**每个子范围完整、有效、已审核且其成员并集恰好等于本节点成员，使用 `child_digests_v1`；任一下级缺失先生成下级维护提案并阻止上级任务。只有 Owner 对当前节点／范围显式选择降级，才以 `composite_v1` 对缺失子范围逐成员选 NARRATIVE → Synopsis → RAW，保存授权者与所选范围；不借跨月完整周摘要纳入父范围外来源，不重复或遗漏成员。L0 不造 DIGEST，L5 不造语义摘要。待审 A3.4／A3.5 Mock 产物不能作为有效派生输入；人工审核、M3 激活、P1-A2 字段关联与审核分别核验。所有新自动周期任务须有核实的发生日期／时刻及其精度、时区依据；日精度可按保守年龄边界入周期，但不得据此算精确间隔或推断同日先后。仅有 `recorded_at`、未经核实本地时间或时间未知时不换档、不生成 L1～L4；H5-B 130 条 `occurred_at=NULL` 来源不会因 A3.6 自动进入周期任务。跨文件只有同依据可信精确时间或人工确认顺序才能写连续叙事；稳定 UUID 排序只是确定性寻址。

**实际依赖与最小数据契约。** 每份新 DIGEST 的版本化输入清单逐项保存：角色、稳定条目 UUID、成员范围与顺序、RAW 来源身份及内容／状态指纹、每项实际输入类型和字段修订 ID／token／审核绑定、M3 子 DIGEST 版本／输出 hash／审核绑定、来源组及可确认顺序、时间精度／`timeEvidenceToken`／投影时区与 `timeToken`、profile、规则／提示词／校验版本及授权引用；清单不复制 RAW 或派生正文。`inputToken` 从规范编码的实际清单计算，`digestToken` 再绑定已审核输出版本；只改变实际使用的依赖才失效，例如仅改 Title／Tag 不使 RAW profile 失效。建议**显式隔离迁移**新增 `mv3_time_digest_bindings_v2`，列为 `id` 主键、`character_id` 外键、`node_id`、`level`、`period`、`time_basis`、`offset_minutes`、`input_profile`（上述五种枚举）、`scope_hash`、`input_manifest_json`、`manifest_hash`、`input_token`、`time_evidence_token`、`time_token`、`digest_version_id` 外键且唯一、`created_at`，索引 `(character_id,node_id,created_at)`；`manifest_hash` 与清单规范编码及 M3 新规则版本互验。旧 `mv3_time_digest_bindings` 及 `raw_sources_v1` 读取保持可识别；新表只承载新版本绑定，不创建第二套正文或审核权威。表缺失、部分安装、结构不符时新路径安全拒绝，普通 `main.init_db()` 不执行迁移。M3 新规则版本无论映射项是否缺失，均必须在直接读取、预览、审核、激活及 P2 投影前核验 v2 绑定和全部实际依赖，不降级走旧校验；旧版本继续旧算法。新旧有效摘要选择须明确返回实际 profile／版本，不能以过期新摘要冒充有效结果。仅内部安装式契约增加 profile／状态；不新增正式 HTTP 路由。

**维护、执行与恢复。** 新协调层先只读列出 missing、stale、child_missing 和可维护的节点，按 L1→L4 拓扑次序提出任务；在隔离环境显式领取执行，不自行启动后台 Scheduler。A3.0 语义范围取角色＋稳定周期键（层级、期间、时间依据、投影时区）＋`digest`，不以可变节点 hash、任务 ID、提示词或成员版本绕过 unknown；为 period 目标定向接受 `digest` 写集合，不把它伪装成 P1-A2 条目字段。冻结完整清单后分批、有界输入和输出，Mock 请求只使用已核验字段／下级文本，最终产物仍回指原始 RAW；中间摘要不是新事实权威。复用统一身份、预留、结算、父任务重算及租约；任何可能派发或结果／用量不明保持 unknown 与有效预留，阻止后续批次和重派。取消只记意图；原派发者租约过期仍能结算可靠模拟用量，失去业务领取资格不得提交 DIGEST。正常批次把确定用量与结果原子持久化；输出明确写入失败并回滚后，以原身份独立结算可靠用量，缺输出批次终止组装；无法提交或核实时保留 unknown／hold 并明确标记已观察用量未持久化。全部批次确定持久化后，短写事务重查成员、来源、字段及子版本、时间 token、Owner 降级授权、取消和人工审核，再原子写一份 M3 `needs_review` 版本、v2 绑定和任务结果引用；失败回滚半成品而不抹去已结算用量。提交成功但回执丢失按持久身份／输入 token 复用，零重复派发。M3 审核与激活仍须 Owner 对照下级和 RAW 决定，生成成功不等于可用于 P2。跨进程 Provider 对账与无身份历史 hold 解除不在本阶段。

**建议修改文件与兼容范围。** 新增 `backend/app/memory_v2_temporal_digest.py`（输入规划、任务、结果绑定）、`memory_v2_temporal_digest_schema.py`（只在合成库显式迁移）；定向修改 `memory_v2_time_projection.py`（新 profile 的读时选择、维护状态、时间资格）、`memory_v2_metadata_contract.py`（新版本清单 token，保留旧 helper 语义）、`memory_v2_processing.py`（period／digest executionKind 与范围保护）、`memory_v2_derivation.py`（新规则读时及直接下级依赖校验）、`memory_v2_model_adapter.py` 与 `memory_v2_fidelity.py`（版本化有界 Mock 输入和有限结构核验）。`memory_v2_digest.py` 仅为修正现有 `recorded_at` 年龄回退作定向修改并验证旧 RAW 行为；`memory_v2_jobs.py` 仅在统一协调入口无法覆盖时作最小调整，不能复制生命周期。保留旧 M3.5 DIGEST、A3.4／A3.5、P2 `raw_sources_v1` 审核绑定、M2 候选与预算语义；不改 Responses 传输、正式 `main.py`、前端或依赖。新增 `tests/test_memory_v2_a36_temporal_digest.py` 与 `test_memory_v2_a36_migration.py`，全部合成数据。若现有 M3 映射与新绑定无法在各读取入口可靠核验，先报告精确 schema／公开契约缺口，不用任务 payload 冒充持久依赖。

**验收、停止和回退。** 正例：L1 统一 NARRATIVE、统一 Synopsis、RAW 与三者混合，L2～L4 完整下级；跨月 ISO 周仅父范围成员；核实日期精度在 7／30／90／180／365 天边界保守换档，L5 只显示标题，任意年龄 RAW 可回查。反例：缺下级默认阻塞、显式 composite 的逐成员授权与依赖、待审或失效字段／DIGEST、来源撤权、成员拆合／重排、词库无关变更、输出版本替换、跨角色／跨文件无依据顺序、`recorded_at` 和 H5-B 式未知时间不生成周期任务。故障：来源或人工审核在计算／提交间变化、多 unknown、取消、旧 worker、输出写入失败、可靠用量结算失败、最终版本写入失败、提交回执丢失、重复任务和迁移失败回滚；断言父子状态、有效预留、版本数、旧审核、RAW 及 Phase 事件不变。新版本直接／间接读、审核、激活和 P2 资格一致；缺摘要继续安全降级而不伪造内容；`formalContext=false`。先受影响专项，核对相关并行漂移，冻结外部源码指纹后一次完整后端隔离回归；不把 Mock 或合成句式当真实摘要忠实性、遗漏率、费用与缓存验收。越权、旧正文泄漏、人工结果覆盖、unknown／预算绕过、录入时间伪造发生时间、旧业务回归即停止。回退关闭新任务和 v2 绑定读取，保留所有 M3 版本、审核、账本和原 RAW，不整库覆盖、不由旧执行器重派未知请求。

**质量前置与后续决定。** 推荐先授权纯合成＋Mock 的 A3.6 工程实现，不必等待 A3.4／A3.5 全局真实模型质量评测；测试中“已审核”只表示合成测试夹具的权限状态。真实数据使用时每一项 Synopsis／NARRATIVE／下级 DIGEST 都须独立经 Owner 审核且当前有效，上级 DIGEST 本身仍待审核；批量私人历史加工、自动发布和正式时间树／上下文启用须另行授权并提供真实模型质量证据。A3.6 默认不开放缺子摘要的自动 composite 降级，只有 Owner 对具体范围显式选择才可建任务。唯一需 Owner 在下一次实施授权中明确接受的行为调整是旧 P2 以 `recorded_at` 换年龄档的定向修正：推荐按较新的 P1-A2 发生时间契约修正，保留旧版本与审核历史，但不再把它们当作可信发生时间的有效周期投影。若不批准这项修正，新 A3.6 可保持隔离工程验收，却不能声称整个 P2 时间树符合当前时间契约，也不能正式启用。

### A3.6 Temporal DIGEST 限定源码合入状态（2026-11-11）

Owner 接受外部冻结清单 `A36_FROZEN_SOURCE_FINAL.json`（SHA-256 `B3E77EE7CC20C8225650FF9F2478294BA559438924EC4EF04A7F2655418F3E60`）的合成／Mock 工程验收：A3.6 专项 27/27、显式迁移专项 4/4、同一冻结源码完整后端隔离回归 774/774，均为零失败、零错误、零跳过。12 个获批源码与测试文件已逐字节合入；774 项是外部候选的结果，不代表含最新并行 Voice 变化的正式工作区重新回归通过。另有合成库 43 表前后指纹检查，属于独立补充证据，不计入 774 项。验收及合入记录分别见 `C:/Users/ASUS/AppData/Local/YOSAKI-A36-185573caff794bf496230985453604db/A36_IMPLEMENTATION_ACCEPTANCE.md` 和仓库外 A3.6 合入备份目录。

A3.6 只增加待审 Temporal DIGEST 的 Mock 工程链和显式 `mv3_time_digest_bindings_v2` 迁移入口；普通初始化与模块导入不安装新表。未安装 v2 表时，新任务安全拒绝，不依赖该表的既有合法读取仍按旧契约工作。跨月 ISO 周按父范围分别绑定成员，已选子集不得取得未选成员正文；最终结果由已结算批次在本地组装，无独立最终模型请求。发生时间资格修正已进入源码，未来加载后 `recorded_at` 不再作为事件发生时间换档或赋予旧周期投影资格；未知时间记忆仍可按既有权限回查与检索。当前运行实例未切换，本轮未迁移真实数据库、启用 Scheduler／正式路由／聊天上下文、调用真实 Provider 或处理私人历史。

Mock 与有限合成检查不证明真实摘要忠实性、重要内容遗漏率或正式使用资格。A3.0 跨进程 Provider 对账、无身份历史 hold 解除，以及存储无法提交时已观察用量可能无法持久化的限制继续成立。后续源码回退须保留新版本读时校验与历史版本、审核、预算及任务，不得让旧读取器或执行器绕过新规则或 unknown 保护；真实迁移、服务切换与数据加工分别审批。

### A3.6 后续：原定 A3.7 工程收口与独立验收（2026-11-11；待 Owner 审核）

**路线依据与完成条件。** 原 P1-A3 任务书的阶段表明确列有 A3.7：字段级索引一致性、任务恢复及统一维护接口；此前获批的最终 A3 计划把它具体化为维护提案、进度、索引、恢复和对账状态的隔离工程验收。A3.7 不是新造阶段。现有 A3.0 生命周期、A3.3 Lexical 投影重建、A3.6 只读 maintenance_plan、M3/P1-A2 字段审核与读时失效均已存在；缺少统一的获权维护状态与受控动作入口，以及从字段依赖变化到索引、任务、周期摘要的端到端一致性验收。A3.7 的工程完成条件是：上述入口复用既有权威和状态机、在合成库中能识别失效并安全建议或执行明确获权的确定性维护、unknown/hold 不被绕过、专项与最终完整隔离回归通过。工程完成不代表真实模型质量、正式迁移或运行启用完成。

| 事项 | 当前状态与已有证据 | 未完成及前置依赖 |
| --- | --- | --- |
| A3.0～A3.6 源码与隔离工程 | 已限定合入；A3.6 冻结候选 Temporal 27/27、迁移 4/4、完整后端 774/774，均为合成／Mock 外部证据 | A3.7 统一维护与全链一致性尚未实现；同一冻结结果不等于含并行变化的正式工作区回归 |
| 正式数据库迁移与运行加载 | A3 迁移均为显式入口；正式 main 仍仅安装现有 M1，A3.6 新表未在真实库安装 | 另批备份、结构审计、迁移与旧 worker 隔离、服务切换审批；不能因源码合入自动执行 |
| 真实 Provider 与质量 | A3.4 Synopsis、A3.5 v3 NARRATIVE、A3.6 DIGEST 仅 Mock 工程和有限合成检查；旧 M3.6 N1 是旧 v1 提示词 | 当前模型／提示词路径的独立授权、专用凭据、样本、费用与人工评测；旧输出不能代替 |
| 人工审核、激活、字段关联与发布 | M3 和 P1-A2 有独立审核及版本机制；H3.4 已确认的 13 个标题属于独立隔离结果 | 新自由文本仍待各自 Owner 忠实性审核、M3 激活、字段关联与字段审核；批量发布和真实数据操作另批审批 |
| P2 投影与正式上下文 | P2 已有已选范围投影及安全降级；A3.6 修正发生时间资格并增加新依赖绑定 | 真实库迁移、有效已审输入、时间依据、正式接线与上下文切换均未批准；未知时间不能伪造周期 |

**推荐的唯一下一执行单元：A3.7 合成／Mock 维护一致性工程。** 从下一次执行时的当前源码建立外部副本。新增 backend/app/memory_v2_maintenance.py 作为统一内部服务；在 backend/app/memory_v2_metadata_api.py 增加仅隔离显式安装的获权维护状态、计划及受控动作入口，不注册正式 main。优先直接复用 memory_v2_processing.py、memory_v2_jobs.py 的范围锁、取消、恢复、对账与父子状态，memory_v2_lexical.py 的权威快照／重建，以及 memory_v2_temporal_digest.py 的维护提案；这些既有模块只在具体测试证明接口缺口时作最小定向调整，不改变生命周期决策、检索排序或已审字段语义。新增合成专项 tests/test_memory_v2_a37_maintenance.py 和 tests/test_memory_v2_a37_api.py。默认不新增 schema、依赖、模型适配或后台 Scheduler；若确需结构或公开契约变化，先报告精确差异。正式路由、前端、M2 默认接入、上下文、P3/P4/Permanent/M4/M5-A 不在本单元。

维护读取以当前角色权限、来源、字段修订及审核、词库、索引权威快照、任务／批次账本、v2 DIGEST 绑定为依据；输出有界的字段、索引、任务、周期状态及稳定原因码，不返回失效旧词面或正文。计划只建议缺失／失效的确定性动作及其依赖顺序；执行须显式获权、重新核验 readSet、范围、租约、取消和目标版本。unknown 或迁移 hold 仅展示阻塞与必要证据，不生成替代派发身份、不解除预留；普通本地安全恢复与远端结果未知严格区分。真实 Provider 对账、无身份历史 hold 解除和同角色旧范围保守阻塞均保留为限制。

验收覆盖字段修订、审核、来源、词库与发布变化后的索引失效和重建；已选周期下级缺失及上级失效；重启、重复动作、并发改动、取消、迟到可靠结算、缺用量、过期 worker、未知批次和 hold；权限先于计数与诊断、无旧正文泄漏；无 Phase 1～6.5 事件增量、零外部调用。使用未迁移、完整显式合成迁移及损坏结构三类库核对旧入口行为；专项稳定后冻结候选，只对最终指纹运行一次完整后端隔离回归。失败、越权、人工结果覆盖、资格或 unknown／预算绕过即停止并保留现场。回退关闭新维护入口，保留字段历史、索引代、审核和账本，不整库覆盖或改写 unknown。交付候选清单、差异、专项／全量原始结果、限制与精准合入方案；本单元不授权正式合入或启用。

**质量验收另行处理。** 将来真实评测须分别比较单经历 Synopsis/NARRATIVE 与完整 RAW，以及每层 DIGEST 与实际下级输入和原始 RAW 的累积压缩误差。A3.6 最终只是本地拼接批次文本，需专门核查连贯性、重复、相互矛盾和跨批更正，不预设新增最终模型合成调用。当前不选择模型、不读取凭据／私人样本、不发请求；真实质量试运行的模型与接口、专用 Key、样本来源、次数与预算、受保护输出位置、人工标准及 unknown 处理须单独提交审批。

### A3.7 请求键修订版限定源码合入状态（2026-11-12）

Owner 接受外部冻结清单 `A37_KEY_V2_FROZEN_SOURCE.json`（SHA-256 `76D4D3B8590CF41985D787F53C46FF9360674E539737857F93E7BD86201C3C0B`）的隔离工程验收：专项 27/27、同一冻结源码完整后端回归 818/818，均为零失败、零错误、零跳过。五个获批源码与测试文件已逐字节合入；818 项是外部候选的结果，不是正式工作区重新运行的结果。修订前反例、字节备份和验收记录保留在外部 A3.7 证据目录及本轮仓库外合入备份目录。

`a37-maintenance-v2` 由服务端为每个签名计划动作生成绑定请求键；执行前校验 Owner、角色、动作、目标、计划及当前权限和依赖。旧 v1 计划不被 v2 执行器接受。请求键只标识该计划动作，不是客户端任意键的全局唯一账本；重复执行仍须当前鉴权。统一维护读取、计划、Lexical 重建、取消意图及受控本地恢复的隔离工程已合入源码，但正式 `main.py` 未安装维护入口，真实数据库未迁移，运行实例未切换，也未新增启用维护功能。

本地恢复仍限 Categories、Tags、Keywords 的既有安全路径；unknown／hold 与预算保护不变。跨进程 Provider 对账、无身份历史 hold 解除、真实模型质量、正式迁移与运行启用、Lexical 日常性能和进程 RSS 硬限制继续待独立验收。A3.7 工程源码合入不代表整个 A3 已完成。

### A3.4 Synopsis 首轮真实质量评测准备（2026-11-12；待 Owner 审核）

**原路线与下一执行单元。** 原 P1-A3 任务书第 3.4、10、11 节要求单经历梗概覆盖完整经历、保留转折与条件，并把真实模型试运行单独审批。A3.0～A3.7 已验收的是隔离工程，其中 A3.4～A3.6 的新模型路径只有合成／Mock 证据；A3.4 真实质量尚未验收。下一次推荐先授权**外部隔离接线工程和 MockTransport 验证**，完成后再单独批准下述最多四次有界付费生成；本段不是付费调用授权。A3.5 仍须以完整 RAW 独立评审 v3 NARRATIVE；A3.6 须同时对照直接下级输入和 RAW，另查本地拼接的连贯性、重复、矛盾、跨批更正与逐层压缩误差。真实 A3.6 输入仍须逐项有效且已审核；通过 Synopsis 质量评测不会自动授予字段审核或下级事实权威。

**接线核对。** `memory_v2_synopsis.py` 的 `run_job()` 只接受 `MockSynopsisAdapter`，执行种类固定为 `mock_synopsis_v1`；`memory_v2_processing.py` 也只允许该 Mock 种类。当前 Synopsis 预留的是模拟 token／费用单位。`memory_v2_responses_adapter.py` 的 opt-in Responses 传输、严格 JSON、`/responses/input_tokens`、无自动重试及用量归一化，和 `memory_v2_m36_trial.py` 的显式美元预算表与派发前预留可作为基础，但其请求种类、输出 schema、提示词和试运行任务目前只覆盖 NARRATIVE／DIGEST，不能把真实 handler 注入 Mock 路径即视为已接线。下一工程单元限于新版本 `synopsis-full-entry-responses-v1`、对应执行种类／提示词／严格输出 schema、完整请求计数与美元预留、统一派发身份、Provider 状态／用量核验、Synopsis 证据校验、结算与业务提交的故障边界，以及合成 MockTransport 专项。新 profile 将批次字符上限与最终合成字符上限分别绑定，E2 拟用批次 1400 字符、最终 4096 字符，均须另过实际 token 计数；旧 Mock profile 的单一 `inputCap` 不改。优先定向修改 `memory_v2_synopsis.py`、`memory_v2_processing.py`、`memory_v2_responses_adapter.py`、`memory_v2_m36_trial.py`；`memory_v2_jobs.py` 仅在统一接口确有缺口时最小修改。新增独立合成专项，不改正式 `main.py`、路由或默认初始化；若既有显式试运行表不足以保存身份、结果或美元账本，先报告精确 schema 差异。尤其验证输出持久化失败已回滚后可靠用量可独立结算，缺失持久输出不得重派或组装；不能沿用 A3.5 已修复即推定 A3.4 自动具备同样保证。

**拟议真实试运行。** 仅用 [A3.4 非私人合成评测材料](A34_SYNOPSIS_REAL_EVAL_SYNTHETIC.md) 的 E1 单批和 E2 恰好两批加一次最终合成；最终输入是已持久化的两批产物和来源身份，不声称再次读取全部 RAW。派发前在合成确认边界上完成确定性分批、请求体与源码指纹冻结；若批数、最终请求大小或输出预算不符，零付费停止。推荐单一模型 `gpt-6.1-sol`、OpenAI Responses Standard、strict JSON Schema、`store=false`、`background=false`、`stream=false`、`truncation=disabled`、无工具、无自动重试或自动换模。账号资格、具体模型版本、价格与服务层级在付费审批前重新确认：[模型](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[价格](https://developers.openai.com/api/docs/pricing)。2026-11-12 方案计算暂按短上下文未缓存输入 USD 2／百万 token、输出 USD 10／百万 token；每个生成请求输入实测不超过 4096 token、输出上限 2048 token，最多 4 次生成及 4 次输入计数，共 8 次 Provider HTTP 请求。四次生成的理论上限约 USD 0.114688，按 20% 预留约 USD 0.137626，建议总预算硬上限 USD 0.20；若计数接口产生费用、费率或路由不同，则重新报价，不派发。单请求连接 10 秒、总超时 60 秒，整次试运行 12 分钟；串行执行，不补发、不修复后追加请求。最终请求须在子批结果产生后单独计数、核对预留及上限。Provider 实际用量与价格快照记录为美元估算，未知费用记 unknown，绝不写零；Mock 费用不入美元账本。

**凭据、结果和停止。** 未来获单独批准时使用限额专用 Key，经受控进程环境或系统密钥容器只交给隔离 runner；不从正式库、聊天、日志或仓库读取，不在本轮读取任何凭据。隔离根目录建议 `C:/Users/ASUS/AppData/Local/YOSAKI-A34-REAL-EVAL-<UUID>/`，仅存合成 SQLite、脱敏请求／响应、Provider response ID、状态与用量、价格快照、源码／提示词／输入哈希和人工评分表，限制本机访问并禁止普通日志记录 Key。请求结果、用量、存储提交任一不明即保持 unknown／预算预留并停止；来源或取消变化只拒绝候选，不能抹去可靠用量。跨进程可靠对账与无身份历史 hold 解除尚不支持，故任何未知请求不得自动重试。质量结果仅是隔离待审样本，不产生 M3 审核、激活、字段审核、发布、正式检索或上下文资格。出现归属／否定／条件／更正关键错误、无来源事实、重要遗漏、预算超限、身份或 unknown 绕过、输出／证据泄漏即停止，不追加修正请求。工程通过与真实内容通过分开报告。

### A3.4 Responses 接线及撤权结算兼容限定源码合入（2026-11-13）

Owner 接受外部冻结清单 `A34_OWNER_BINDING_FROZEN_SOURCE_V3.json`（SHA-256 `A9D9C5F52C072E0674220FD068F9E3671930C8C9D7990692EF915A33931B68E3`）的零外呼工程验收：同一冻结源码完整后端隔离回归 869/869，零失败、零错误，未报告跳过。五个既有 A3.4 接线／共用结算文件及一个新增专项测试已按候选字节限定合入。869 项仅属于外部冻结候选，正式工作区未重跑应用测试；源码合入不代表当前运行实例已经加载新代码，也不代表真实模型内容质量通过。外部报告与日志保留在 `C:/Users/ASUS/AppData/Local/YOSAKI-A34-WIRING-31293ff0f8c14fbc833c126a06394f5f/`。

旧 M3.5、M3.6、Metadata 等普通派发路径维持原身份与权限契约，不自动取得撤权结算资格。新 `synopsis-full-entry-responses-v1` 路径在派发前核验并持久绑定创建者 Owner、任务、批次、request、attempt 和原证明；缺失或冲突时不发生成请求。角色撤权后，原派发者凭有效原证明仅可结算已知可靠用量和预留，不保存可用 Synopsis、不创建成功引用或继续派发；缺用量、提交不明继续保留 unknown 与有效预留。跨进程证明丢失后的 Provider 对账及无身份历史 hold 解除仍不支持。

本轮只移入源码与测试，未迁移真实数据库、重启或切换服务、安装正式入口、读取私人历史或真实凭据，也未调用真实 Provider。E1/E2 的合成请求参数、模型和价格仍是待复核的试运行方案；未来付费调用须单独冻结实际请求与预算并获授权。真实 Synopsis 自然度、忠实性、遗漏率、人工审核及发布资格均未验收。

### A3.4 E1／E2 可复用改进限定源码合入（2026-11-15）

Owner 批准外部候选 `A34_QUALITY_INTEGRATION_FROZEN_SOURCE_V2.json`（SHA-256 `74B2DEB16E26E40FB70913774099D8933D2FA703917AE21B7705B9202E4AF543`）。外部副本完整后端隔离回归 973/973，零失败、零错误、零跳过；正式工作区没有重跑应用测试。六个获批业务／测试文件已按候选字节合入；本轮未迁移真实数据库、切换运行实例、注册路由、启用任务、调用 Provider 或处理私人历史。外部验收与合入记录分别位于 `C:/Users/ASUS/AppData/Local/YOSAKI-A34-QUALITY-INTEGRATION-acc445e7dd264f9998348cb5c54893ce/` 与 `C:/Users/ASUS/AppData/Local/YOSAKI-A34-QUALITY-MERGE-ab2d9149303840b3b6c818523fa9b768/`。

本次源码纳入有界公开诊断、分类费用估算和保守预留、可靠用量结算后独立留存诊断、句界与有限证据校验，以及 E1 v2／E2 v5 的版本化请求、readSet 和读时校验；旧 profile 的默认提示、上限和派发身份契约不变。付费 runner、claim、运行库与固定历史 job 的 v5→v6 最终重写流程继续留在外部。已移入的 v6 最终提示构造及有限证据 helper **不是通用可执行任务**；原 E1／E2 固定样本的质量结论不自动转授给本次新集成版本。

**下一项功能缺口：多批 Synopsis 的通用最终整合任务尚未实现。** 最小补齐范围是从当前有效且完整持久化、已可靠结算的有界批次产物构造独立最终任务，绑定来源／确认边界、批次输出哈希、规则与提示版本、字段 token 和人工保护；复用 A3.0 的 Owner 授权、范围锁、request／attempt、原始派发证明、预留、取消、unknown、迟到结算和幂等，并在最终提交时重新核验依赖与证据。不可用固定六来源或历史 job／claim 的 runner 充当产品入口；是否需要新增持久结构须在下一次隔离方案中以现有结构核验结果决定。该缺口未获本轮编码或启用授权。

### A3.4 通用多批最终整合限定源码合入（2026-11-15）

Owner 批准外部 V4 清单 `A34_GENERIC_FINAL_FROZEN_SOURCE_V4.json`（SHA-256 `327B29D9E51DA2BF3564B19BADC7457D55AB7512CB70C0B2075FD2F316C18DF7`）。外部冻结候选的完整后端隔离回归为 1014/1014，零失败、零错误、零跳过；这不是包含正式工作区后续并行修改的重跑结果。获批七文件已按候选字节限定合入：A3.4 业务／专项五文件，以及分别记录的两份 Voice 测试契约兼容修正。五份补齐的 Voice 合成 manifest 仅是外部回归依赖，未覆盖正式文件。仓库外备份及合入记录位于 `C:/Users/ASUS/AppData/Local/YOSAKI-A34-GENERIC-MERGE-8e13e92aa6914aaebc508d6f6cd1b829/`。

通用多批最终整合工程实现已合入，真实模型质量尚待验证。新 `synopsis-full-entry-responses-final-general-v1` 受控任务以同一条目、来源和已可靠结算的完整批次产物为输入，另行绑定身份、预算、提示与证据版本；不采用固定历史 job 的 v5→v6 runner 作为产品入口。源码合入未执行真实数据库迁移、运行切换、正式入口启用、Provider 请求或人工审核／发布；unknown、hold 和 A3.0 跨进程对账限制仍成立。

**下一受控合成样本质量试运行准备。** 优先复用既有非私人 E2 六条 RAW、确认边界和隔离设施，但须冻结本次实际源码与 profile、完整两批生成和独立最终整合的调用链，确认旧来源任务不会再额外派发其内置最终请求。目标上限为两次批次输入计数／生成，加一次最终输入计数／生成，即最多三次计数、三次生成；最终请求只从已持久化且已结算的批次产物及来源绑定构造。按当前分离任务上限，来源任务最高 USD 0.30、最终任务最高 USD 0.12，合计拟议硬上限不超过 USD 0.42；正式执行前须以冻结请求、当日费率、计数收费规则、账户资格和最坏输入／输出重新核价，不能把该拟议值视作付费授权。人工评审逐项对照完整 RAW、两批产物与最终文本，检查归属、否定、条件、跨批更正、最终暂缓、装置归属、关键遗漏、重复合并、自然度和压缩程度，并定位错误发生层；结构校验与 Owner 质量决定分开。准备和未来执行均不得复用旧付费 claim／账本或自动审核、激活、发布。当前仅记录下一步方案，不发送请求。


## A3.4 双批交接与限定词策略源码合入（2026-11-16）

双批交接与 v2 限定词校验的版本化业务实现已按 `PROPOSED_MERGE_LIST.json` 的八文件范围合入；外部组合专项 155/155 通过，对应冻结清单 `CUMULATIVE_FINAL_FROZEN_SOURCE.json`，SHA-256 `25D46EB39F16AE8C1EA35B096A9429D3465F30367C3793E9933016177A49E71C`。这是外部组合验收，正式工作区未重跑应用测试。旧 v1、原真实 failed／rejected 运行和既有审核历史不被本次升级；新版本真实成功创建待审候选尚未验证。本轮未调用 Provider、操作真实数据库、迁移、启用入口、审核、激活或发布字段；当前运行实例未切换。写入、备份和条件回退记录保存在仓库外 `YOSAKI-A34-QUALIFIER-MERGE-1a0d9a6a14164c1b8b53416bff360162`。


## A3.4 有界标点规范化与未派发预留释放限定源码合入（2026-11-16）

新批次 profile 的有界标点规范化，以及确定未派发请求的预留释放已按八文件清单限定合入；冻结清单 SHA-256 为 `6A37DC99E0ADFA39A251965BABED6E762822E7B707667E7D601139881ACAF2BA`。外部相关回归 98/98 通过，正式工作区仅完成字节及静态检查，未重跑应用测试。旧 profile 保持原校验行为；新规则的原始输出、规范化产物、调整记录和版本绑定保留，最终 v2 只读取已验证的持久批次产物。

预留释放仅适用于可证明从未派发的 prepared 请求，并与派发互斥；unknown、possibly_dispatched、已有 attempt／用量或不满足证明条件的历史 hold 不在此范围。原真实运行仍为 failed／rejected，其 USD 0.098304 预留本轮未处理，未自动扫描或释放历史预留。更新后的真实端到端成功路径尚未验证。本轮未调用 Provider、操作或迁移真实数据库、切换运行实例、启用入口，亦未审核、激活或发布字段；字节备份与条件回退记录保存在仓库外 `YOSAKI-A34-PUNCT-MERGE-BACKUP-9cbd8a218af14895b331da5f15462f20`。

## A3.4 批次与最终 v3 限定词策略源码合入（2026-11-16）

批次与最终 v3 共用限定词策略已按八文件清单精准合入；外部 A3.4 专项 139/139 通过，零失败、零错误、零跳过。冻结清单 SHA-256 为 `471879471F1DB8EB001842351A493F3446A878D94FF2BD20E902975CE8DDE271`。相同输入的最终 v2／v3 生成请求字节一致；此前完整最终输出及宽引用、相邻单元承载更正的改写在 v3 离线校验仍被接受。正式工作区仅做字节和静态检查，未重跑应用测试；外部回归不等于当前全部并行改动已验收。

旧 profile、原真实 failed／rejected 运行、claim、结算和预留保持不变。新 v3 完整真实成功路径尚未验证；有限规则不证明通用忠实性。未调用 Provider、操作真实数据库、迁移、启用入口、审核、激活或发布字段，运行实例未切换。外部备份及合入记录位于 `C:/Users/ASUS/AppData/Local/YOSAKI-A34-QUALIFIER-MERGE-2a1dfa8bea1d4f5d9c3eb10aa2d21239/`；候选建立时没有复制前独立源端快照，不追溯补造。

## 减少人工操作与改善阅读体验（2026-11-26）

| 使用流程 | 以前的操作 | 当前操作及验收范围 |
| --- | --- | --- |
| 只在本机整理历史 | 选择文件、预览、暂存、进入批次再启动整理 | 选择文件并点一次本机导入。自动校验、归档、索引、分段，阅读草稿留在同页。处理期间保持页面打开。套餐梗概沿用另一条按批授权的后台路径。 |
| 混合导入 | 遇到重复来源后重新选文件 | 隔离浏览器实测两份有效档案和一份重复档案，正常项完成并显示跳过原因。含冲突成员的 ZIP 按整包跳过。 |
| 阅读和修订 | 展开草稿区再点读取，改日期后点筛选，逐条开合全文 | 打开草稿直接读取，日期变化即筛选。批审可一次展开所选精修，本页保存的文字按字段显示前后对照。长精修滚到分页处可继续加载。 |
| 审核与检索 | 修改后手动刷新审核清单，发布过程中快速选搜索结果可能读到旧经历 | 保存后刷新清单。合成案例从编辑、对照、批量发布、按新标题搜索走到完整精修。批次有回执后才报成功。 |
| 聊天、语音与设置 | 聊天准备中可以误点发送，本条记忆补查选择可能提前消失 | 等待设置就绪后可发送，进入发送流程才消费本条选择。浏览器测了演示聊天、设备语音偏好保存并重读、恢复布局。 |
| 失败恢复与维护 | 临时进度读取失败后要手动查看，周期读取失败会显示默认预算状态 | 仅网络错误或服务端暂时失败自动补读进度，最多两次。模拟 503 后恢复到 unknown，写请求为零。重新联网或返回页面重读周期状态，读取失败时显示原因。 |

浏览器使用独立数据库和合成材料。手机 390px 下实测梗概 17px、行高 29.75px，无页面横向溢出。专项后端 99 项通过，覆盖跨日、重启、费用上限及未知请求。日常已登录的两个 Chronicle 窗口加载前端后分别显示周期正文、新的本机导入入口和原批次暂停状态。正式库未执行审阅、发布、重放或迁移。本轮无新增供应商测试费用。真实模型自然问法与跨设备听感继续沿用原计划的证据范围。

专项补验（2026-11-26）。从导入结果进入“审核本批”只显示该批条目，可显式切回全部经历。隔离浏览器实际完成本批差异对照、发布与检索。长精修在滚到页尾时自动续读，保留手动入口。相同合成库停止并重新启动隔离服务后，浏览器重读及滚动续页成功。模拟授权到期时，保持打开的周期面板自动更新，写请求为零。跨期预算及派发保护另有后端专项。页面内差异只保留当前会话最近一次文字修改；关页后的永久历史对照尚未实现。
聊天记忆工具仅在当前批最后一个待判断的 Chronicle 搜索或展开操作上提供“确认并交给当前模型续答”。也可只确认、先读结果或拒绝。一次选择覆盖该工具与一次后续模型调用，新提议仍需判断。浏览器以合成工具响应验收，搜索与展开各一次确认，后续各一次续答并显示最终回答；真实自然提问命中率没有新增证据。