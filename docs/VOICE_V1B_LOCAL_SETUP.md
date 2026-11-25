# YOSAKI Voice V1-B — Windows 本地凭据与 Voice ID 配置引导

日期：2026-11-09（Asia/Hong_Kong）；Development 状态更新：2026-11-10。  
状态：**既有配置记录；不授权真实 ElevenLabs API、再次迁移日常库或 V2 开发。**

## 1. 已核对的启动与密钥边界

- 日常后端已切换到 Development 专用入口 `D:\AI xiangmu\自建前端\scripts\start_yosaki_development.ps1`；它在导入 `backend.app.main` 前保护数据库路径。**不要改用旧的直接 Uvicorn 命令绕过预检。**R2-C 已验证 8000 的 Development 实例连接 `D:\AI xiangmu\自建前端\data\yosaki.sqlite3`，Voice 和业务后台任务开关均为 0；实际进程状态仍以现场检查为准。
- `backend/app/voice/config.py` 通过 `os.getenv('ELEVENLABS_API_KEY')` 判断是否配置；项目未发现 `.env` 自动加载机制。根目录和 `backend/` 的 `.env` 虽由 `.gitignore` 排除，但单纯写入该文件不会让当前启动方式读取密钥，因此本阶段**只选 Windows 用户环境变量**，不让用户配置第二份密钥。
- 配置前的检查只返回布尔值：当时的终端进程、Windows 用户和系统范围均为 `configured=false`。用户随后按本引导设置，Windows 用户范围已变为 `true`；本地后端已重启、health 通过，登录后本地状态接口确认 `api_key_configured=true`。全过程没有读取或输出密钥内容。若由 IDE 启动后端，IDE 及其终端也需重启，才能把新环境传给后端。
- `.gitignore` 已排除 `.env`、`backend/.env`、`deploy/.env.production` 和 `data/*`。生产环境示例 `deploy/.env.production.example` 只是模板，不应填入真实 Key。Key 不进入 SQLite、Voice Profile、Vue、浏览器存储、前端请求或 Git。

## 2. 用户自行设置 API Key（仅一处）

1. 在 Windows 打开**开始菜单**，搜索并打开“**编辑账户的环境变量**”。
2. 在“**用户变量**”区域选择“**新建**”。变量名填 `ELEVENLABS_API_KEY`；变量值由用户**在 Windows 设置窗口自行粘贴**自己的 ElevenLabs API Key。不要在 Codex 对话、命令行、截图或测试报告中粘贴 Key。
3. 保存并关闭环境变量窗口。关闭启动 YOSAKI 后端的旧 PowerShell 终端；若使用 IDE 的终端/运行按钮，也关闭并重新打开 IDE。
4. 仅在确实需要重新启动时，在**新打开**的独立 Windows PowerShell 中按 Development 专用入口启动后端：

   ```powershell
   Set-Location "D:\AI xiangmu\自建前端"
   .\scripts\start_yosaki_development.ps1
   ```

   此步骤已经由用户完成，**当前无需再次重启**。将来重启前先核对 8000 实际监听者并按 R2-C 交接流程停止对应实例，不按历史 PID 批量停进程。仅重启热加载的子进程不足以确保父进程取得新环境。
5. 不使用 `echo`、`Get-ChildItem Env:`、日志或调试打印查看 Key。重新登录现有 YOSAKI 后，可由只读 `GET /api/voice/status` 的 `api_key_configured` 布尔字段核对。该路由不访问 ElevenLabs；确认前不要点击真实测试入口。

若布尔值仍为 `false`，只核对是否在“用户变量”中保存、是否重新打开启动后端的终端/IDE、是否重启的是正在监听 8000 的后端。不要把 Key 改放到更多文件或发送给 Codex。必要时由用户登出并重新登录 Windows，让新用户变量被新应用进程继承。

YOSAKI 网页还需要**单独的前端 Vite 服务**。后端的 8000 端口只提供 API，不会自动打开工作台。若 `http://127.0.0.1:5173/` 无法访问，另开一个 PowerShell，在项目根目录运行 `npm run dev`，并使用它打印的 `Local` 地址；不要关闭正在运行的后端窗口。2026-11-09 的本地核对中，后端已在 8000 正常运行，网页打不开是前端 5173 未运行；启动前端后两个地址均返回 200。若前端端口被占用，以 Vite 实际打印的地址为准。

## 3. 阿夜 IVC Voice ID 绑定

Voice ID 是角色声音档案配置，**不是 API Key**。R2-C 之后，用户已在正式 Development 日常库为阿夜 `character_id=1` 保存 IVC Voice ID；只读核对为 `provider=elevenlabs`、`revision=1`、`active=false`，告知 0 条，页面刷新后仍能读取。**现在不要再次保存、提高 revision、开启总开关或重复迁移。**

以下是当时的绑定步骤，**已完成，仅供核对历史过程，不需重做**：

1. 先在**角色卡**核对当前角色为“阿夜”，并确认其角色 ID 与当时的后端记录一致；不要仅凭页面上一个空的 Voice ID 输入框推断角色归属。若角色列表发生变化，重新核对 ID，不沿用上述 `1`。
2. 进入**语音**全页或浮窗；在总开关关闭时保持第三方处理告知未确认。
3. 在 Voice Settings 的 **Voice ID** 栏填入用户自己准备的 ElevenLabs **IVC** Voice ID。不要使用隔离测试的 `synthetic_voice_id`，也不要把 Voice ID 写进 `ELEVENLABS_API_KEY` 环境变量。
4. 当时核对角色、Voice ID 和非活动状态后保存；日语基准和中文跨语言测试不会自动改写档案。已有档案不得自动覆盖。
5. 保存后只读检查 `/api/voice/profiles?character_id=<阿夜实际ID>` 的 `character_id`、`voice_id`、`active` 和 `revision`，确认确实绑定到阿夜。该 GET 不访问供应商；Voice ID 可显示给用户，API Key 不可显示。

## 4. 用户完成后的零供应商请求核对

用户完成第 2 节并重启后端后，先报告“**已设置并重启**”，**不要附上 Key**。后续只通过本地环境布尔检查和已登录的 YOSAKI 只读接口核对：

| 检查 | 判定依据 | 当前状态 |
|---|---|---|
| 后端读取 API Key | `/api/voice/status` 的 `api_key_configured=true/false` | 已登录本地接口确认 `true`；供应商权限未验证 |
| Voice ID 与阿夜绑定 | 实际角色 ID、`/api/voice/profiles` 的 ID/版本/启用状态 | 已保存至日常库，遮蔽值 `mYj...krd`，revision=1、active=false |
| 语音总开关 | `/api/voice/status` 的 `enabled` | 已登录本地接口确认 `false` |
| 第三方告知 | `/api/voice/privacy` 的 `accepted` 与页面一致 | 正式告知表 0 条；总开关关闭，accepted=false |
| 原设备语音 | 语音页仍显示并可使用浏览器设备朗读 | 正式语音页控件仍可用；实际播放待设备侧检查 |

所有检查均不调用 ElevenLabs。用户已提供 ElevenAPI Starter 账户页面截图：已用 5,432／40,000 积分；v4、v4 Turbo 和 Scribe Realtime 的美元模型标价已写入 `docs/VOICE_V1B_DRY_RUN.md`。实际最低计量、失败扣费和积分结算关系仍待确认。完成本地配置也**不等于**批准 V1-B 真实 HTTP/WS 测试。
