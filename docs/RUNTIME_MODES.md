# YOSAKI 本地运行模式（R2-A）

R2-A 提供三个后端启动入口。当前日常数据库尚缺当前代码启动时会自动创建的表和列，Development 入口会在导入 `backend.app.main` **之前**拒绝启动；正式库的增量初始化与服务切换须等 R2-C 单独批准。

| 模式 | 数据库 | 后端端口 | 入口 |
|---|---|---:|---|
| Development | `D:\AI xiangmu\自建前端\data\yosaki.sqlite3` | 8000 | `scripts/start_yosaki_development.ps1` |
| Preview | `%TEMP%\yosaki-preview\yosaki.sqlite3` | 8001 | `scripts/start_yosaki_preview.ps1` |
| Test | 每次新建的 `%TEMP%\yosaki-runtime-test-*\yosaki.sqlite3` | 8002 | `scripts/start_yosaki_test.ps1` |

Preview 前端单独运行 `scripts/start_yosaki_preview_frontend.ps1`，监听 127.0.0.1:5174，并将 `/api` 转到 127.0.0.1:8001。普通 `npm run dev` 仍将 `/api` 转到 127.0.0.1:8000。

入口先解析绝对路径，拒绝继承的 `YOSAKI_DATA_DIR` 与所选模式冲突，也拒绝 Development 指向系统临时目录、Preview/Test 指向 Development 数据库。通过预检后才为**当前进程**设置数据目录和关闭状态的语音开关；不会修改 Windows 用户级或系统级变量。导入应用后再用实际 SQLite 连接的 `PRAGMA database_list` 复核路径。成功日志只显示 Mode、Database Path、Voice Enabled。

Test 会创建独立临时目录，入口不删除测试数据库。请勿把测试目录作为日常数据，也不要从当前仍设置了预览目录的 PowerShell 会话启动 Development。R2-C 前不要直接使用旧的 `python -m uvicorn backend.app.main:app` 命令连接日常库，因为导入 `main.py` 会立即运行 `init_db()`。

Development 入口还强制设置进程内 `YOSAKI_BACKGROUND_TASKS_ENABLED=0`，禁止三个业务 startup 轮询任务。此变量未设置时默认 1，保持 Preview、Test 及旧启动方式的兼容行为；仅接受 0/1。它不关闭 ASGI 生命周期或用户显式业务 API。

## Voice V2-A 受控验收入口（2026-11-10）

普通 `scripts/start_yosaki_development.ps1` 继续强制 `YOSAKI_VOICE_ENABLED=0`。新增 `scripts/start_yosaki_voice_acceptance.ps1` 只在另获正式迁移、启用授权后使用；当前仅在含日常库新鲜快照的隔离项目副本、Windows PowerShell 5.1 和空闲端口完成测试。该入口显式传入 `--voice-acceptance`，不能通过单纯设置环境变量启用。

受控入口沿用 Development 固定数据目录、继承变量冲突检查和导入后的 `PRAGMA database_list` 复核；在导入 `backend.app.main` **之前**，还会与当前 `voice.storage.init_db()` 在内存生成的 V2 Schema 比较两张表、两条显式索引及两条 SQLite 自动索引。未迁移或结构不符时拒绝启动；Preview/Test 拒绝该参数。成功启动时强制 `YOSAKI_BACKGROUND_TASKS_ENABLED=0`，日志显示 Voice 与后台任务状态，不打印密钥。正式日常库尚未执行 V2 迁移或使用该入口切换服务，具体步骤与隔离证据见 `docs/VOICE_V2A_FINAL_ACCEPTANCE.md` 第 5～6 节。
