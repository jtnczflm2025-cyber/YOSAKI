# YOSAKI 产品与开发计划（更新版）

> 更新日期：2026-10-21
>
> 当前项目已落地“欢迎页 → 工作台 → 可组合浮动窗口”的本地 MVP，并具备单用户认证、角色卡、模型、聊天历史、记忆上下文、MCP/受控插件、语音和加密备份等功能。规划中的功能路径现已支持全页工作区模式，并与浮动窗口共用功能界面；目前尚未拆分为独立页面组件。手机端、真实 provider/MCP、设备和 VPS 仍需单独验收。本文后续阶段章节保留为产品方向，实际完成状态以文末按日期追加的 Implementation progress 和代码为准。

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
