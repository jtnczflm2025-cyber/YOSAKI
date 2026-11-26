# Voice V2-A 最终隔离验收与正式启用准备

日期：2026-11-10（Asia/Hong_Kong）  
状态：**第 1～11 节按发生时点保留历史结论；最新日语执行结果见第 12 节。V2-A「中文短消息手动朗读」仅就消息 3 的端到端验收通过。消息 10 的日语短消息生成、播放及刷新后缓存复用通过；用户认为发音、音色、音高、停顿和连贯性符合预期，但情绪表现偏平。长文本、固定 Voice Benchmark、表达风格和实时连接尚未验收；V2 整体未通过。实际费用未知。**

## 1. 冻结候选与指纹

原 V2-A 候选在复制前、复制后以及测试结束后逐文件核对 SHA-256。候选固定于 `C:\Users\ASUS\AppData\Local\Temp\yosaki-v2a-final-candidate-1p2w2jbf`，完整文件清单另存为 [VOICE_V2A_FINAL_MANIFEST.json](VOICE_V2A_FINAL_MANIFEST.json)。清单本身 SHA-256：`8927a54db79317329da7c994a5aa16a14ab434c0bde034ec8e1bb32619390`。下表是**入口增量实施前**的冻结指纹；当前受影响文件的新指纹见第 6 节。

| 清单 | 文件数 | 汇总 SHA-256 |
|---|---:|---|
| 全候选源码（后端、运行脚本、前端手写源码） | 113 | `fb37c33093ea0f3577de4fcecd5faeaa78e5232def56122fbe099f47260e2adc` |
| 声明的依赖与前端锁文件 | 4 | `4d47405e446fd8b87166622880fbdb7713bd62715c418b84037f8991584d8456` |
| 全后端测试与夹具 | 59 | `7399fc17fb3d5eaf38e178f6888341d63a470322005dcefb6d67f9c08c8604f3` |
| Voice 相关源码子集（含 App.vue、style.css） | 19 | `5ad6e9699277c911cafae96daeb9fd1bc87411981254bf9d5a6d5bc010371a4e` |
| Voice 测试子集 | 7 | `e12f383726cf5e7d87fc3497f79ab5e1793e8c24e3ad355ba75a2f8c59b1ea11` |

清单只包括源码、声明依赖、测试和合成测试夹具；不包含 `data/`、密钥、备份、`.env`、虚拟环境、生成的 Vue JS 或私有历史样本。隔离 Python 3.12.13 环境的 `uv pip check` 检查 32 个包并通过；安装包列表 SHA-256 为 `669bf8c5eaebd6b6ee82cb0020941e000c1a55f56a3105cc16db5f7bee13dcb0`。V2-A 新声明依赖为 `markdown-it-py==4.2.0` 和 `mdurl==0.1.2`；没有新增前端运行依赖。

## 2. 本次测试与历史证据的范围

| 证据 | 结果 | 适用范围 |
|---|---|---|
| 冻结副本完整后端回归：`python -m unittest discover -s tests -q` | **507/507，通过，失败 0，退出码 0，852.745 秒** | 入口及预览提示增量之前的 59 个测试文件；不是单独的 Voice 测试数，也不能称为增量后重跑。日志：`C:\Users\ASUS\AppData\Local\Temp\yosaki-v2a-final-candidate-1p2w2jbf\full_backend_regression.log`，SHA-256 `3b0b89cffa6ee223867f77dc66e94899cb3b1e3be1b722c165a977b90cca19bf`。 |
| 同一冻结副本的 `tests.test_voice_v2` 与 `tests.test_voice_v2_backup` | **15/15，通过，失败 0，退出码 0，7.116 秒** | V2-A 公开文本、门控、任务、缓存、绑定和双备份路径。 |
| 追加的隔离边界探查 | 20 个同键并发 claim 中 1 个 `new`、19 个 `busy`，缓存行 1；模拟 Windows junction 被拒绝 | 只读冻结源码、临时 SQLite 与假路径；未增补仓库测试文件，也未派发供应商请求。 |
| 此前第二次完整回归 | **506/506，通过** | 当时可变工作区的历史结果；随后 Voice 解析/Unicode 边界有小修，不能替代本次 507/507。 |
| 此前前端构建及隔离浏览器 | 构建通过；Chromium／Edge 全页和浮窗、播放控制与设备语音已有记录 | 见 [VOICE_V2_IMPLEMENTATION_PLAN.md](VOICE_V2_IMPLEMENTATION_PLAN.md) 第 6.2 节。增量前端修改已重新构建；本次未重复浏览器操作，**原生 Chrome 未验收**。 |
| 受控入口与预览提示增量 | **32/32 专项通过，退出码 0；前端 `vue-tsc -b && vite build` 通过** | 四项精确补证修复前的阶段结果；本轮修复后相关专项见第 6.4 节。没有无变化地重跑完整 507 项。 |

完整回归进程使用候选目录为工作目录和 `PYTHONPATH`，`YOSAKI_DATA_DIR` 指向候选内独立临时库，`YOSAKI_VOICE_ENABLED=0`，`ELEVENLABS_API_KEY=synthetic-test-key`。为保持既有定时备份测试的 startup 语义，**仅测试进程**设置 `YOSAKI_BACKGROUND_TASKS_ENABLED=1`；日常 Development 的后台任务开关没有改变。测试完成后进程已退出。日志存在不影响退出码的 ResourceWarning/DeprecationWarning；本轮未调用 ElevenLabs。

## 3. 获批实施单的四项核对

按 [VOICE_V2_IMPLEMENTATION_PLAN.md](VOICE_V2_IMPLEMENTATION_PLAN.md) 第 3.1～3.4 节逐项核对：

| 要求 | 实现位置 | 针对性证据与结果 |
|---|---|---|
| ① 公开正文过滤与预览 | `backend/app/voice/readable_text.py:17` 只取安全 Markdown 文本并记录跳过类别；`speech_planner.py:80` 定长分句；`manager.py:16` 仅接合格助手公开 `content`；`router.py:94` 返回文本、字符数、请求数、跳过类别与预览指纹。 | `test_public_text_and_plan_are_conservative`、`test_gates_and_preview_have_zero_provider_calls`、`test_preview_changed_source_rejected_before_dispatch` 与新增 `test_preview_reports_skips_without_exposing_hidden_text` 通过：代码/HTML/图片/链接目标/工具结构不进入预览或供应商；变更后来源 409、零派发。原缺少的**跳过内容提示**已补齐，只返回类别，不返回被过滤原文。 |
| ② HTTP 合成与播放控制 | `elevenlabs_adapter.py:44` 固定 v4/MP3、有界响应且无自动重试；`audio_jobs.py:78` 单 owner 顺序任务与取消；`router.py:110` 仅由带指纹的显式 POST 派发；`MessageAudioPlayer.vue:85` 缓存/连续播放与 `:104` 停止；`voiceAudioPlayer.ts` 单实例状态机；`App.vue:145` 与设备朗读互斥。 | `test_http_generation_cache_hit_and_binding_invalidation`、`test_definite_rejection_and_unknown_do_not_retry`、`test_cancel_inflight_request_stops_job_without_retry`、超时/503 测试通过。此前 Chromium／Edge 已验预览确认、播放/暂停/继续/停止、全页/浮窗和设备语音；新增跳过提示只完成 API 专项与前端构建，未伪称新浏览器验收。 |
| ③ 缓存去重、失效与文件安全 | `audio_cache.py:104` 的事务 claim、`:141` 完整 MP3 原子发布、`:179` 清理和 `:227` 孤立文件扫描；`audio_jobs.py:152` 按当前来源/档案验证清单、`:176` 鉴权音频；`storage.py:24` 两张 V2 表及外键/唯一约束。 | 缓存命中、跨消息复用、遗留生成转 unknown、来源/档案/告知失效、过期/孤立文件测试通过；另在临时 SQLite 验证同键 20 路 claim 为 1 new/19 busy/1 行，模拟 Windows junction 被拒绝。跨 worker 共享缓存未验收，仍以单 worker 为边界。 |
| ④ V1/V2 备份排除与恢复 | `backend/app/voice/backup_policy.py:5` 只处理隔离快照；调用点 `backend/app/main.py:812`、`backend/app/backup_v2.py:210`；`tests/test_voice_v2_backup.py:91`、`tests/test_voice_backup.py`。 | 两条真实备份代码路径对合成库导出并恢复到新目录：Profile/原消息保留，告知、缓存索引和绑定为空；源库哈希与源音频文件清单不变，恢复库完整性/外键检查通过，音频文件未入备份。通过。 |

上述四项中，预览缺少实际跳过类别提示，已在 V2-A 范围内修复并增补针对性测试；其余实现沿用已通过证据，另对并发 claim 与 junction 拒绝补做隔离探查。Chrome 原生设备验收、真实 HTTP 产品链路和跨 worker 并发均不属于本次“通过”的范围。

## 4. 候选与工作区对照

原冻结开始前 `git status --porcelain=v1 -uall` 有 214 条既存状态，完整回归结束后仍是同一 214 条，逐条无差异。当时的候选清单与工作区源码、依赖和测试 **变更 0、新增 0、缺失 0**。冻结时已存在的 Memory 分支文件一并纳入全后端回归；507/507 只说明该冻结组合的后端回归通过，不把 Memory 的独立阶段验收归于 Voice。此后受控入口及预览提示增量只改第 6 节列出的 Voice/运行入口文件和文档，未修改、清理、暂存或覆盖 Memory 及其他既存工作。

## 5. 后续执行单（准备完毕，未执行）

### 5.1 正式库两张 V2 表的受控迁移

1. 另获正式迁移授权后，再冻结待执行源码指纹、检查 Git 状态、确认 8000 进程与实际 `PRAGMA database_list` 为 `D:\AI xiangmu\自建前端\data\yosaki.sqlite3`，并记录切换前表、索引、旧数据摘要。若 `main.py`、初始化模块、Voice Schema 或运行入口相对第 6.1 节指纹发生变化，先在新快照重做受影响审计，不沿用本次启动结论。Voice 和后台任务保持 0。
2. 停止日常后端写入，在同一 SQLite 一致性视图下备份日常库；验证备份可读、`integrity_check=ok`、`foreign_key_check` 为空，并在**新隔离目录**完成恢复演练。保留原库与备份，不用旧备份覆盖日常库。
3. 使用不导入 `backend.app.main` 的受控一次性进程，先以 SQLite 连接的 `PRAGMA database_list` 复核目标绝对路径。`storage.init_db()` **自身不执行 `BEGIN` 或 `COMMIT`**；调用方须用 `isolation_level=None`、显式 `BEGIN IMMEDIATE`、调用 `storage.init_db(db)`、在同一事务内核对 DDL 和旧数据摘要，完全匹配后才 `COMMIT`，否则 `ROLLBACK`。预期新增 `voice_audio_cache`、`voice_message_audio` 两表、`voice_audio_cache_expiry`、`voice_message_audio_cache` 两条显式索引，以及 SQLite 为 `cache_key UNIQUE` 和 `(message_id,sequence) UNIQUE` 生成的 `sqlite_autoindex_voice_audio_cache_1`、`sqlite_autoindex_voice_message_audio_1` 两条自动索引；V2 表初始均为 0 行。既存 V1 两表只接受 `IF NOT EXISTS`。不能用导入 `main.py` 代替受控迁移，因其 `executescript()` 不提供这六个对象的单独原子边界。
4. 迁移后复核表结构、唯一约束、外键、重复初始化、完整性、旧记录摘要及备份兼容；Voice 仍为 0。使用原受保护 Development 入口恢复服务和 health，检查实际路径、两个关闭开关及无额外业务写入。此阶段不创建缓存、激活 Profile 或确认告知。

### 5.2 受控 Voice 启用

当前普通 `scripts/start_yosaki_development.ps1` 仍强制 Voice=0。新增的 `scripts/start_yosaki_voice_acceptance.ps1` 在 `scripts/yosaki_runtime.py` 中显式传入 `--voice-acceptance`：只允许 Development；先核对固定 `data/yosaki.sqlite3` 路径、继承的环境覆盖值、旧 Development Schema 和两张 V2 表及四条索引的实际 DDL，**再导入 `main.py`**；启动后复核实际 SQLite 路径，并强制 `YOSAKI_BACKGROUND_TASKS_ENABLED=0`。传入环境 `YOSAKI_VOICE_ENABLED=1` 不能替代入口，且会被预检拒绝；Preview/Test 也拒绝该验收参数。

正式启用另获授权且完成迁移验收后，在独立 PowerShell 窗口**现场**核对 8000 监听进程的命令行、启动时间和数据目录；只停止核实归属的当前后端，不使用历史 PID，也不停止 5173 前端。确认端口空闲后，执行以下命令并保持窗口打开：

```powershell
Set-Location 'D:\AI xiangmu\自建前端'
Remove-Item Env:YOSAKI_DATA_DIR -ErrorAction SilentlyContinue
Remove-Item Env:YOSAKI_VOICE_ENABLED -ErrorAction SilentlyContinue
.\scripts\start_yosaki_voice_acceptance.ps1
```

该脚本默认使用 8000；隔离验收使用过 `-Port` 指定空闲端口。核对日志中的 Mode、Database Path、Voice Enabled=true、Background Tasks Enabled=false，访问 `/health`，再次复核实际连接与迁移后数据摘要。单纯设置环境变量或裸 Uvicorn 均不能代替该入口。任何失败停止本次验收实例并恢复普通 Development Voice=0，保留迁移表和现场，不回退覆盖数据库。

随后仍需分别授权用户在产品界面主动确认第三方处理告知、将阿夜 Profile 从 `active=false` 改为 `true`，逐步核对角色 1、原 Voice ID 与 revision，不自动覆盖现有档案或外呼。受控入口的代码与隔离启动通过**不等于**本次已启用日常服务。

### 5.3 固定真实 HTTP 消息朗读

产品路径真实测试另拟一次性测试单：由用户选定一条已保存、非归档、可朗读的阿夜助手消息；先用只读预览冻结 `message_id`、公开过滤后逐段文本与跳过类别、准确字符数、请求数、`eleven_v4`、`mp3_44100_128`、各段 30 秒/16 MiB 与任务 260 秒上限。再次确认告知和 Profile 后，获得独立授权才执行一次手动 POST；401/403/429、超时、断线、**请求结果未知**或音频校验失败立即停止，不重试、不换模型、不追加请求。实际账单/积分扣减是独立信息，**无论请求成功、失败或结果未知，都不能因本地状态推断为 0**，须另以供应商账户记录核对。此单不复用 V1-B 收据，也不包含 Benchmark。

V1-B 继续记为分项通过、未整体通过：累计实际派发 **5 HTTP、4 WS、78 TTS 字符、5.805 秒 STT**，实际费用未知；空闲 TTD WS 原因未确认。它暂不阻塞 V2-A 的 HTTP 隔离验收，但必须在依赖实时连接生命周期的阶段前处理。

## 6. 受控入口、账单措辞与日常库快照隔离验收（2026-11-10）

### 6.1 本次实际改动与新指纹

- 新增 `scripts/start_yosaki_voice_acceptance.ps1`，修改 `scripts/yosaki_runtime.py`：显式 `--voice-acceptance` 仅限 Development，路径和完整 V2 DDL 预检在导入 `main.py` 前生效；默认 Development、Preview/Test 均保持 Voice=0。`--port` 仅供该显式验收入口选择隔离端口。后台业务任务在 Development 仍强制为 0。
- 新增 `tests/test_yosaki_voice_acceptance.py`；未修改既有运行模式测试。修复了新测试中 SQLite 连接未关闭导致 Windows 临时目录清理失败的问题，最终连同原测试通过。
- 修改 `backend/app/voice/readable_text.py`、`speech_planner.py`、`manager.py`、`router.py`、`frontend/src/voice/MessageAudioPlayer.vue`：预览返回被过滤内容的**类别**，不返回被过滤原文；新增 `test_preview_reports_skips_without_exposing_hidden_text`。
- 修改 `backend/app/voice/elevenlabs_adapter.py` 和播放器提示：`unknown` 描述本次请求/缓存结果不能确认，**不表示账户账单的状态**。实际扣费对于本地所有状态仍须以供应商账户记录核对。更新本实施单及阶段计划的对应文字。
- 未修改 `storage.init_db()`、`main.py`、备份实现、数据库结构定义或依赖声明。未修改 Memory、心理、聊天或其他既存分支文件。

本节 2026-11-10 当时的受影响清单包含源码 24、依赖 3、测试 9 个文件；四项精确补证修复后的**当时** SHA-256 为 `135a68aa4789e14f940efb7b70e9cc8c0d07f0f9abb39c37a54a866910eff2d1`。源码、依赖、测试清单的当时汇总 SHA-256 分别是 `b2bfde49f783ab46fd3f4dd005b7586c8cde67b848834183e7e3d4c4b2e6200d`、`8b3924bbf825a9b9137885b56aa5cb80e485a9f18a18eb5e5cc1a4fd4c43f00b`、`c28cf15b0850b276f893286ee869d963e1e2f70ce5f71b609c6c3f60ae4e529e`。当前 [VOICE_V2A_ENTRY_MANIFEST.json](VOICE_V2A_ENTRY_MANIFEST.json) 已因 2026-11-11 获批的方括号处理变更更新，现行指纹见 [正式迁移记录](VOICE_V2A_FORMAL_MIGRATION_20261111.md)。第 1 节完整回归的旧指纹保持原样，**不冒充本次增量后的完整回归**。

与 507/507 冻结清单逐文件比较，仅 8 个既存文件发生预期变化：Voice 的 `elevenlabs_adapter.py`、`manager.py`、`readable_text.py`、`router.py`、`speech_planner.py`、前端 `MessageAudioPlayer.vue`、`scripts/yosaki_runtime.py`、`tests/test_voice_v2.py`；另新增受控 `.ps1` 和入口测试。旧清单中的 78 个 Memory 相关文件哈希均未变化。前端构建生成的 JS 辅助文件不作为手写源码或本次验收指纹。

### 6.2 专项结果

- 最终专项命令包含 Voice V1、V2、备份、原运行模式及新受控入口测试：**32/32 通过，失败 0，退出码 0**。新增测试分别证明 V2 表/索引缺失时显式入口拒绝、默认 Development 不因缺 V2 而隐式启用、Preview/Test 与错误数据目录拒绝、继承的 Voice=1 覆盖值拒绝、受控导入后 Voice=1/后台任务=0 且合成库 Schema/记录不变。
- `npm run build`（含 `vue-tsc -b`）通过；后端 Voice 与新入口编译通过。没有重复 507 项完整回归。Chrome 原生浏览器仍未验收；旧 Chromium/Edge 浏览器证据不覆盖新增跳过提示的可视检查。
- Windows PowerShell **5.1.19041.6456** 实际执行隔离项目中的完整 `.ps1`，路径含中文和空格，受控入口使用空闲端口 59191；输出 `Mode=development`、隔离数据库绝对路径、`Voice Enabled=true`、`Background Tasks Enabled=false`，`/health` 返回 200。Ctrl+C 使外层命令以信号退出码 1 结束，但 Uvicorn 记录完整 shutdown、端口监听 0；这不是启动或健康检查失败。没有停止或切换现有 8000/5173 服务。

### 6.3 新鲜日常库快照：事务、DDL 与完整启动

只用 `mode=ro` 连接当前 `D:\AI xiangmu\自建前端\data\yosaki.sqlite3`，核对 `PRAGMA database_list` 后用 SQLite `backup()` 生成一致性快照；快照 SHA-256 为 `ab6b5e36cf701b19deb30d04363e17a284976355d530cb13a4b69cbc5d0f0bef`，源快照有 84 张表、168 个 Schema 对象，完整性 `ok`、外键错误 0。隔离目录 `C:\Users\ASUS\AppData\Local\YOSAKI\v2a-voice-entry-4019ece5d4114bc780efccb0275d51ac` 仅授权当前用户及 SYSTEM；其中的数据库含私人数据，不加入 Git 或测试夹具，也不输出 Voice ID、密码或密钥。

`storage.init_db()` 本身只是顺序执行六条 `CREATE ... IF NOT EXISTS`，**没有事务起止**。在快照副本中以 `isolation_level=None` 和外层 `BEGIN IMMEDIATE` 先验证 `ROLLBACK` 可撤销全部 DDL，再在另一次显式事务中调用并提交：只新增两张表、两条显式索引和两条 SQLite 自动索引（共六个对象），旧对象 DDL 和全部旧表逐行摘要不变，新表均 0 行；重复初始化没有任何差异。迁移后为 86 张表、174 个对象，完整性 `ok`、外键错误 0。原始快照哈希不变。

未迁移的原始快照用显式 Voice 入口预检时，**在导入 `main.py` 前拒绝且文件哈希不变**。同一原始快照另用普通 Development 运行器（Voice=0、后台任务=0）在隔离端口 59192 完成 `main.py` 完整导入、ASGI startup、`/health` 200 和 shutdown；前后 168 个 Schema 对象、84 张表的记录摘要完全一致，没有自动补迁移或业务写入。

已准备 V2 Schema 的隔离副本则使用当时最新的 90 个后端/脚本源码文件，通过 Windows PowerShell 5.1 的受控 `.ps1` 完成 `main.py` 完整导入与 ASGI startup/shutdown；只调用一次 `/health`。启动前后 174 个 Schema 对象完全一致、86 张表的记录摘要全部一致，数据目录无新音频或其他文件；实际 SQLite 路径为该隔离副本。审计摘要保存在上述受控目录的 `migration_audit.json`、`raw_startup_audit.json`、`startup_audit_final.json`，没有把私人记录写入项目文档。

本次未在真实日常库执行迁移或导入 `main.py`，未确认第三方告知、未激活 Profile、未派发任何 ElevenLabs HTTP/WS，也未改变日常 Voice/后台任务开关。正式步骤仍须单独批准。

### 6.4 四项精确补证与修复（本轮）

| 问题 | 状态、代码位置与针对性断言 |
|---|---|
| 正文 Audio Tags 与普通方括号 | **已实现。** `readable_text.py` 用明确列出的来源标签词表整项剔除 `[whispers]`、`[laughs]` 等，并把「来源 Audio Tags」记为跳过类别；剩余 ASCII 方括号符号移除，其中普通文字保留。`test_source_audio_tags_removed_and_bracket_text_retained` 断言 `[版本说明]` 成为「版本说明」，标签不出现在发送片段；`test_preview_and_dispatched_text_share_bracket_filter` 断言预览与 Mock HTTP 请求文本相同、数据库原正文不变。V2 不给来源标签表演权限；词表外的新标签词本身会作为普通文字保留，但方括号不会作为标签指令发送。 |
| 预览指纹及旧预览门控 | **已实现。** `speech_planner.digest` 包含 message ID、完整正文、生成状态、Profile revision、Voice ID SHA-256、实际模型 `eleven_v4`、格式 `mp3_44100_128`、语言、分段及 Planner 版本。当前 HTTP 请求不发送 Stability/Similarity，故没有这两个有效参数。`router.speak` 在 `jobs.submit` 前重新计算并比较；`test_preview_binds_profile_and_effective_request_parameters` 逐项改变 revision、Voice ID、语言、模型、格式、Planner 版本，旧预览均为 409、Mock HTTP 调用 0；模型/格式变为不支持值时先被配置门控 409 拒绝。 |
| 旧 `generating` 与取消迟到结果 | **单 worker 边界内已实现。** `VoiceJobs.active_attempts` 只保存当前执行实例在内存中的随机 attempt ID；`audio_cache.claim` 对同实例 token 返回 busy，对不在当前集合的遗留 `generating` 置 unknown，不重试。`Job.cancel_requested` 与 job lock 在取消、发布和绑定之间封口；`audio_cache.publish`/`mark` 还要求 ID、attempt ID、`generating` 三项同时匹配。`test_abandoned_generation_is_unknown_without_dispatch` 断言 active→busy、遗留→unknown、旧 attempt 不能发布；`test_cancelled_adapter_late_result_cannot_publish` 模拟 Provider 吞掉取消后返回音频，仍无文件/绑定、零外呼。跨 worker 活动判断未验收，当前只支持单 worker。 |
| 已绑定缓存与容量预留 | **已实现。** `audio_cache.prune`/`claim` 保留未过期绑定，优先按 LRU 淘汰未绑定 ready；过期或用户清空可解除绑定。`claim` 在外呼前用 `audio_bytes=16 MiB` 为每个 `generating` 行登记逻辑容量，并检查磁盘可用空间；不足抛 `CacheCapacityError`，任务记录 `failed/cache_capacity`，零外呼。`test_bound_cache_is_not_capacity_evicted_and_full_capacity_rejects_before_http`、`test_unbound_cache_is_evicted_to_reserve_inflight_response`、`test_disk_space_rejects_before_http` 分别断言绑定保留、未绑定淘汰、在途预留和请求数不增加。可用空间会被其他进程改变，预检不构成物理磁盘空间硬保留。 |

上述新增修复后运行 `test_voice_v2.py` **21/21**，并联跑 Voice V1/V2、双备份和运行模式相关专项 **38/38，通过、退出码 0**；未重复第 1 节旧 507/507 完整回归。日常库、运行服务及真实供应商均未触及。

## 7. 2026-11-11 后续授权执行记录

本文件第 1～6 节记录当时未授权正式迁移的阶段结果。用户后续批准第 5.1 节正式库受控迁移及剩余 ASCII 方括号处理；实际迁移、备份、独立恢复、普通 Development 恢复、隔离 22/22 专项和当前源码指纹详见 [VOICE_V2A_FORMAL_MIGRATION_20261111.md](VOICE_V2A_FORMAL_MIGRATION_20261111.md)。第 1～6 节的旧结果及 507/507 基线不随这次授权追认为新一轮完整回归。

## 8. 语言与短消息分段修正（2026-11-11）

本节是获批的 V2-A 增量，不追改第 1～7 节的历史判断。阿夜角色 1 的 IVC 参考录音语言仍记录为 `ja`；先前真实中文朗读将该值直接作为 TTD `language_code`，并把 75 个非空白字符拆成 3 次 HTTP 请求。三段均成功生成；用户确认播放、暂停、继续、停止和刷新后缓存复用均可用，但三段语气、语调不同且衔接不够自然。未单独证明语言参数或分段是听感差异的原因，实际费用仍未知。

- 现有 Profile JSON 的 `language` 字段保持 schema v1、值和 revision 不变，明确作为**克隆参考录音语言**。`speech_planner.request_language` 仅从过滤后的公开朗读文本确定本次请求：有明确简体中文特征且无其他语言特征时使用 `zh`；含日文假名且无中文特征时使用 `ja`；中日混合、含拉丁词的混合文本、共享汉字而无法可靠判定的文本不强制语言，在 TTD 请求中省略可选 `language_code`。这里的“不强制”不保证供应商一定正确识别。官方 [TTD 请求文档](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert)将该参数列为可选，并说明其用于指定语言和文本规范化。
- `speech_planner.plan` 先安全过滤正文，再在 2,000 字符单请求上限内合并短自然段，以 `\n\n` 保留段落；长文本优先在句末或分句标点处切分，Unicode 安全硬切为最后手段。最大总量 8,000 字符、最多 8 段仍保持。Planner 版本改为 `v2a-plain-2`。
- 预览现在返回实际 `language_code`（未强制时为 `null`）、参考录音语言、判定原因、准确请求字符数及非空白字符数。前端对缺少这些字段的旧后端预览拒绝显示生成确认，避免在当前尚未重启的服务中误认为已使用新版语言规划。`voice_profiles` 和 V2 缓存表的结构均未改变。
- 预览摘要纳入新 Planner 版本、合并后的实际发送文本、有效语言参数及参考语言；缓存键纳入新 Planner 版本、实际文本与有效语言。旧预览在模型派发前返回 409，旧三段缓存不会被新计划误命中。原三份 MP3 及试听记录保留，另在用户本机受保护目录保存独立证据副本；本轮未清理或重新合成。

**模拟验收：** 使用假 Key、Mock HTTP 和临时 SQLite 的 Voice V1/V2 相关测试 **35/35 通过**，前端 `vue-tsc -b && vite build` 通过。针对性测试覆盖参考语言 `ja` 的简体中文仍发送 `zh`、日文发送 `ja`、混合文本省略 `language_code`、75 个非空白字符的三段合并为 1 次请求、旧预览 409 与旧缓存隔离；未重跑历史 507 项完整后端回归。以日常库的新鲜隔离快照调用新路由，只读预览消息 3 得到 **1 段、79 个实际请求字符（含两个段落间隔共 4 个换行字符）、75 个非空白字符、`language_code=zh`、`eleven_v4`、`mp3_44100_128`**；旧指纹 POST 返回 409，Mock Provider 实例化 0 次，旧缓存索引 3 条与绑定 3 条未变化。隔离快照随后移除，日常库没有写入。

修正后的候选预览只供审阅，**尚未获真实对照试听授权**。当前日常 Development 后端保持原进程和旧规划器，不在其中执行新计划；对照试听须在另一次受控切换及明确批准后进行。旧 `VOICE_V2A_ENTRY_MANIFEST.json` 保留为此前入口的历史指纹；本次文件指纹单独记录，不冒充旧冻结基线。

## 9. 受控切换与消息 3 的真实对照朗读（2026-11-11）

本节记录第 8 节之后另获授权的执行结果，不改变其当时的未授权状态。切换前核对语言候选源码、依赖和测试指纹，并在日常库的新鲜隔离快照上完整启动当前 `main.py`。隔离启动与退出后，85 张表的结构和记录摘要均未变化，`integrity_check=ok`、外键违规 0；日常库启动后也没有额外 Schema 或业务记录写入。并行 Memory 文件未纳入本次修改。

派发前发现 `httpx.Timeout(30)` 只限制网络阶段，不能限制整次流式响应的墙钟时间。仅在 `backend/app/voice/audio_jobs.py` 为单次 TTD 调用增加 `asyncio.timeout(30)`；模拟慢响应超时后标记结果未知、缓存不发布、再次提交不重试。Voice 相关专项 **36/36 通过，退出码 0**。更新后的候选清单为 `VOICE_V2A_LANGUAGE_MANIFEST.json`；不将旧 507/507 回归写成这次修改后的完整回归。补丁后再次创建新鲜隔离快照并完整启动，结构和记录摘要仍未变化。

受控入口启动的实际路径为项目 `data/yosaki.sqlite3`，单 worker、`Voice Enabled=true`、`Background Tasks Enabled=false`；5173 前端保留。服务返回的零外呼预览与授权完全一致：消息 3、原 Voice ID、Profile revision 3、`eleven_v4`、`mp3_44100_128`、`language_code=zh`、79 个请求字符、1 段、预览 SHA-256 `75f3750439c991dbf6297ad3adc54c79fcc3a9fc4d11c07a22e0527979a8c354`。

随后仅提交 **1 次**产品朗读任务；生成完成，新缓存对象 4 为 `ready`，262,103 字节，SHA-256 `915eb299bb2ef90a55fce256f5e1acecbb86f4cc35c8523a19ff00632447df80`，消息 3 绑定 1 段。原 3 个 `ja`／`v2a-plain-1` 缓存对象及其 MP3 文件均保留，受保护证据副本仍为 3 份。页面刷新后重新进入对话，显示“播放缓存”；播放时出现暂停／停止控件。服务日志在刷新及再次缓存播放后仍只有 **1 次** `POST /api/voice/messages/3/speak`，缓存对象总数仍为 4，未再派发合成任务。

用户对这段新音频的主观听评为：整体连贯性、段落停顿、音色和音高稳定性均“很不错，没有问题”。这是单条消息的听感观察，不能把改善单独归因于语言参数或合并分段，也不代表 V2 整体通过。实际账单及积分扣减**未知**。V1-B 空闲 TTD WS 原因仍未确认；Chrome 原生浏览器验收和更广泛消息／设备回归仍待后续阶段。

## 10. V2-A 中文短消息手动朗读限定验收与后续门槛（2026-11-11）

用户进一步确认：新版消息 3 音频的自然度、整体连贯性、段落停顿、音色及音高稳定性均符合**这条消息**的预期。因此将 **「V2-A 中文短消息手动朗读」本次端到端验收记为通过**。适用对象仅为阿夜已保存助手消息 3、当前 IVC Voice ID、Profile revision 3、`eleven_v4` HTTP TTD、`mp3_44100_128`、`language_code=zh`、`v2a-plain-2` 的 79 字符单段计划。首次旧版三段试听和本次新版一段试听都是人工听感，不构成模型间或 IVC 整体质量的客观比较；本次改善不能单独归因于语言提示或分段变化。

执行证据保持可追溯：授权预览 SHA-256 为 `75f3750439c991dbf6297ad3adc54c79fcc3a9fc4d11c07a22e0527979a8c354`；产品日志记录 1 次朗读提交，刷新后缓存清单/音频读取继续发生，提交数仍为 1，缓存仍为 4 个 `ready` 对象。新版 MP3 共 262,103 字节，SHA-256 为 `915eb299bb2ef90a55fce256f5e1acecbb86f4cc35c8523a19ff00632447df80`。旧三段的 `v2a-plain-1`／`ja` 缓存对象和文件仍完整，三份独立保护副本均通过哈希核对；新 `v2a-plain-2`／`zh` 音频另存保护副本。两个版本的音频与脱敏执行摘要分别保存在本机 `C:\Users\ASUS\AppData\Local\YOSAKI\v2a-language-fix-20261111\prior_three_segment_audio\` 和 `C:\Users\ASUS\AppData\Local\YOSAKI\v2a-language-fix-20261111\comparison_message3_20261111\`，后者 `evidence.json` 的 SHA-256 为 `2b5286afd771206afecc7f0f648d18ef4b771f91eeee5041fc3908a36d5ff833`。证据不含聊天正文、完整 Voice ID 或 API Key，也不纳入 YOSAKI 备份。源码、依赖和测试指纹见 `VOICE_V2A_LANGUAGE_MANIFEST.json`；单请求 30 秒总时限模拟在第 9 节记录。实际账单与 credits 扣减仍未知。

剩余工作沿既定阶段推进，**本次不派发新请求**：

| 阶段 | 待验收内容 | 当前边界 |
|---|---|---|
| V2 消息朗读 | 日语基准、中文长文本与多段边界，以及中日混合文本的语言选择、请求切分、停顿和缓存绑定；原生 Chrome 与更多设备/消息回归 | 本次单条中文短消息不覆盖这些场景；真实生成需另行冻结文本、请求数与费用并批准 |
| V2 固定 Voice Benchmark | `VOICE_BENCHMARK.md` 中日语四类、中文两类、中日混合一类；同文本比较 v4 HTTP 与 Turbo WS，先做无 Audio Tags 基线，可选盲听 | 七段均为短样本，不覆盖长文本；保留相同 Voice ID 与共同有效参数，分别记录部署路径，不把路径差异全归因于模型，不复用本次朗读额度 |
| V3 表达风格 | 慵懒、克制、冷幽默、无奈、低语、轻笑的普通文本与经验证 Audio Tags 配对，检查克隆音色身份和表达一致性 | 另行核对标签协议、固定语料和真实请求预算；不因本次自然听感判定通过 |
| V4 实时连接及 V5 | 先查明空闲 TTD WS 错误与关闭发起方，再验收保活、心跳、重连、连接代次、Scribe/麦克风、打断；V5 测端到端延迟、长通话和设备矩阵 | 空闲 TTD 未完成不阻塞已验证的 HTTP 手动朗读，但在依赖实时连接生命周期的阶段前必须解决；不自动启用实时语音 |

## 11. 日语短消息准备与独立结果记录（2026-11-11）

只读核对当前受控 Development 实例所连接的日常库：阿夜角色 1 目前只有两条已保存助手回复，消息 3 为已验收中文正文，消息 5 也不是日语。没有合适的已保存日语助手消息可供用户选择，故**本轮未调用产品朗读预览，也未形成日语文本、请求字符数、语言参数或预览指纹**；不创建消息、不调用聊天模型、不生成音频。待用户以后选定确实存在的日语助手消息，才按同一 Voice ID、`eleven_v4`、`mp3_44100_128`、无 Audio Tags、目标单次 HTTP 的边界读取零外呼预览；如实际预览超过一段或参数不符，应停止并重新审阅，不能强行合并或直接生成。

今后的每次执行单和结果分别记录 `request_outcome` 与 `billing_observation`：前者区分未派发、完成、明确失败、结果未知；后者记录账户已核实的金额/积分或未知。**请求结果未知不等于已扣费，也不等于零扣费；请求成功也不能把实际账单填写为零。**消息 3 本次真实生成的 `request_outcome=completed`、`billing_observation=unknown`；本轮日语准备的 `request_outcome=not_dispatched`，没有新增供应商请求，未查询账户账单。此前中文短消息限定验收继续保持通过，V2 整体状态不变。

### 11.1 用户提供原文后的聊天尝试

用户随后允许通过现有聊天界面请阿夜原样复述两段日语。新对话 5 和用户消息 8 已保存，但没有助手回复记录。选中的 ChatGPT 套餐模型在当前受控运行实例中报告 `enabled=false`；该模型的本地开关在获取令牌及外呼前检查，因此本次聊天未产生可供朗读的日语助手消息。未自动切换其他模型或修改服务门控；此前 Voice 预览仍未执行，ElevenLabs 请求数不增加。后续需用户选择允许的聊天方式，或提供之后已保存的日语助手消息 ID，才可继续零外呼预览。

### 11.2 临时启用套餐聊天后的实际日语预览

用户单独批准临时启用 ChatGPT 套餐聊天。先在日常库新鲜隔离快照中以 `YOSAKI_CHATGPT_PLAN_ENABLED=1` 完整启动受控 Voice 入口，健康检查通过；退出后 85 张表的 Schema 与记录摘要均不变。日常实例临时启用期间，保持固定 Development 数据路径、单 worker、Voice 验收入口及后台任务关闭。对话 5 补充一条引用上一条原文的用户消息 9，阿夜生成并保存助手消息 **10**；其 `generation_status=completed`，正文与用户给出的两段日语逐字相同，SHA-256 为 `eaf95812c549153f682d0003d7045b9d1f7b70b08e051436b16a0fbf43b56537`。没有切换到其他聊天模型。随后受控重启，ChatGPT 套餐聊天状态恢复 `enabled=false`，Voice 和后台任务状态保持不变；重启前后 Schema 与业务记录摘要一致。

在恢复后的真实产品界面对消息 10 执行**零外呼预览**：过滤后恰为两段原文，80 字符与 72 字符，中间两个换行，总计 **154 个请求字符、152 个非空白字符、1 段、计划最多 1 次 HTTP TTD**；跳过类别为空，未加入 Audio Tags。使用角色 1 的原 IVC Voice ID、Profile revision 3、`eleven_v4`、`mp3_44100_128`，参考录音语言 `ja`，实际 `language_code=ja`。预览 SHA-256 为 `14f530eeb59d9e2c48e08a18f29aa18564fa2ed73542cc51761e5e8e3f9cff14`。本地脱敏收据位于 `C:\Users\ASUS\AppData\Local\YOSAKI\v2a-ja-chat-isolation-06dfd3188f5d\ja_message10_preview_summary.json`，文件 SHA-256 为 `1abea50286185db9054b6e2b65593855f4d608c1f156991e51277d786c452700`；仓库文档不复制私人正文或完整 Voice ID。

**待单独批准的试听范围：**仅消息 10 的上述固定预览，最多 1 次 ElevenLabs HTTP TTD、154 字符、单请求 30 秒总时限及 16 MiB 音频上限；不得重试、换模型、改音色参数或追加文本。成功后试听日语发音、参考音色辨识、音高稳定、两段之间的停顿和整段连贯性；刷新后检查缓存复用。预览不一致或请求结果未知即停止。当前 `request_outcome=not_dispatched`，实际未来费用仍未知；本节不把聊天模型回复或零外呼预览记为日语 TTS 验收通过，也不纳入七段固定 Benchmark。

## 12. 消息 10 日语短消息受控真实朗读（2026-11-11）

用户针对第 11.2 节冻结的预览单独批准一次真实执行。派发前再次核对受控 Development 进程、日常数据库路径、源码与依赖指纹、角色 1 的原 IVC Voice ID 哈希、活动 Profile revision 3、正式告知、消息 10 的公开正文及预览 SHA-256；授权预览指纹仍为 `14f530eeb59d9e2c48e08a18f29aa18564fa2ed73542cc51761e5e8e3f9cff14`。计划值保持 `eleven_v4`、`mp3_44100_128`、`language_code=ja`、两段之间两个换行、154 个请求字符和 1 个片段。请求总时限为 30 秒，音频上限 16 MiB。

仅在产品界面点击一次“确认发送并生成”。服务日志记录 **1 次** `POST /api/voice/messages/10/speak`，响应 `202 Accepted`；没有第二次提交、重试、模型或参数替换。任务完成后，消息 10 新增序号 0 的音频绑定，缓存对象 5 为 `ready`，生成 442,661 字节 MP3，SHA-256 为 `15f2922133e78f23dc34c98a71f968b9a21389bdfff53d375f1da0acba0d746a`。数据库完整性和外键检查通过；原缓存对象 1～4 及其文件仍完整。`request_outcome=completed`；实际账单和 credits 扣减未查询，`billing_observation=unknown`。

原始缓存文件和受保护副本分别位于 `D:\AI xiangmu\自建前端\data\voice\audio\78\78691ed5c6e1ccf72409b768dbfd63a2e430ec32579a801b2e2522e141574424.mp3` 和 `C:\Users\ASUS\AppData\Local\YOSAKI\v2a-ja-chat-isolation-06dfd3188f5d\japanese_read_20261111\message10_ja_v2a_plain_2.mp3`；同目录 `evidence.json` 保存脱敏执行摘要，不含正文、完整 Voice ID 或密钥。刷新浏览器后，该消息仍显示“播放缓存”；点击后出现“暂停／停止”，服务日志显示缓存清单及音频读取，而 `POST /api/voice/messages/10/speak` 累计仍为 **1 次**。此项证明产品路径可以在刷新后从既有绑定启动缓存播放，不代替用户的实际听感确认。

用户实际试听后报告：**日语发音准确，阿夜音色准确，音高稳定，两段间停顿自然，整体连贯；情绪与语气变化偏少**。用户刷新页面后再次听到缓存音频；本地浏览器也观察到刷新后“播放缓存”可启动播放，服务日志在两次刷新后的播放期间仍累计只有 1 次生成提交。消息 10 的日语短消息生成、设备播放和缓存复用在本次范围内通过；情绪表达列为待改善，不由这段短样本推断 IVC 整体质量或 V2 全阶段通过。未改 Profile、参考录音、模型或合成参数，也未产生 Benchmark 或 V3 请求。
