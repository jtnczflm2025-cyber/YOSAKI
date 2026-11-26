# YOSAKI 当前开发交接

香港时间 2026-11-27。下个窗口先读本页，再按模块进入专项计划。项目仍是本地单用户开发原型。交付的是当前工作区的可发布源码组合，包括此前分散在聊天、Memory、Voice 及工作流专项分支的集成成果。日常正在运行，不需要为了接收本包加载服务。

## 版本、来源与可复现范围

- 仓库 https://github.com/jtnczflm2025-cyber/YOSAKI ，交接分支 `codex/current-handoff-20261127`。下载本次归档时，以 ZIP 文件名中的完整 SHA 锁定版本，也可从 Git ZIP 注释读取。解压根目录含本说明和 AGENTS.md。
- 日常目录 `D:\AI xiangmu\自建前端` 位于 main，开工 HEAD 与远端 main 均为 `32db65a087fc7867fd12c78d732855f0a5168ca9`。原暂存区为空，36 个已跟踪文件有修改，827 个未跟踪文件（逐文件展开，含生成物、历史文档及私有材料）。改动涉及共享入口、各功能模块、依赖、测试和文档。另有聊天、记忆、语音、工作流及经历删除等六个既有专项工作树。各自保留；本交接在独立工作树以既有经历删除专项 072b3a3 为父提交，逐件取日常工作区实际源文件。
- 包含应用源码、静态地球资源、样式、锁文件、数据库增量初始化及独立迁移脚本、部署骨架、测试、合成文本夹具和公开交接材料。AGENTS.md 取自本机现行文件。Memory 执行计划沿用公开历史版，同步当前 C1～C10 表、读取性能修复和本次现场复核。历史私有记录、原始验收资料、浏览器截图、数据库、密钥、凭据、音频、依赖目录均留在原处。
- 产品开发不依赖这些私有材料。专项重演有差异：五份历史 Voice 运行绑定 manifest（scripts 下的 voice_v1b、两份 v1b 补充、v3_fixture、ws_lifecycle）和 tests/fixtures 下的 Scribe WAV 留本机。依赖原始 manifest、音频、截图、执行 JSON、私有账户或数据库的历史验收不能仅凭 ZIP 原样重演。示例 .env.production.example 仅是设置模板。六份 Voice 历史文档的日语听评引文或完整请求体已从交接版移除，原文件留在日常工作区。部分历史文档链接指向受保护的本地证据。
- 运行入口从原目录读代码。产品版本后端 0.24.0、前端 0.1.0。8000 健康回报版本与源码一致，单 worker PID 20264，启动于 11-26 23:21；Vite PID 12372，5173 回环监听。现场 health、openapi.json、/chronicle 均 HTTP 200。后端启动后未发现较新的 backend/app Python 文件。最新 6 个冻结文件逐字节一致；前一性能清单 11 项中 10 项仍一致，ChronicleApp.vue 已由最新冻结清单覆盖。隔离构建重建的 55 份前端派生 JS 与日常同名文件换行归一后全部相同，包括 Vite 实际引用的 .js。日常进程未嵌入 Git SHA，无法证明整个内存映像与此提交逐字节一致；本次建档没有把日常进程切换到交接分支。
- 本次从独立工作树离线安装锁定的前端依赖，开启 Chronicle 构建，vue-tsc 和 Vite 通过，保留地球 chunk 大小警告。隔离数据库运行本地导入任务专项 2 项通过。包的校验以实际解压后与 Git 树逐文件比对为准。本次没有重跑全套业务、真人听评、真实供应商、备份恢复或生产部署。

## 架构和入口

| 入口 | 职责与依赖 |
|---|---|
| frontend/index.html → src/main.ts | Vue 3 + TypeScript + Vite。/ 与 /hub 进入欢迎页与浮窗工作台，/chat 等全页复用 App.vue。VITE_YOSAKI_CHRONICLE_A0=1 时 /chronicle 单独挂载 ChronicleApp.vue。UI 主题及抽屉在 src/ui，编辑、导入、批审和周期阅读分别有组件。 |
| backend/app/main.py | FastAPI 的认证、角色、模型档案、上下文、MCP/插件及模块装配入口。前端通过 /api 和受保护的 WS 访问；聊天文本使用 SSE。共享 SQLite、角色权限、审阅版本和开关一起决定可用能力。角色卡不携带历史聊天。 |
| chat_*.py、responses_adapter.py、chatgpt_plan.py、frontend/src/chat | 请求、消息版本/分支、附件、统计和供应商协议。聊天构造上下文时查询合资格记忆，工具运行待逐次决策；Voice 绑定实际完成的消息/请求。 |
| memory_authority.py、memory_v2_*.py | 有序来源 → 导入/分段 → 字段版本和精修 → 分别确认、发布 → 时间投影、词法/语义检索和完整精修展开。索引可重建，来源和人工决定独立。周期摘要也有自己的授权与读时资格。 |
| backend/app/voice、frontend/src/voice | 档案/告知、朗读及音频缓存、主动录音草稿、连续通话、AI 表达规划、计量和播放。Voice 自有状态与限额，依赖聊天完成回执、用户设置和实际传输协议。 |
| desires → volition → autonomy → feedback → learning；psychology、behavior | 驱力/观察形成意愿；活动在批准的环境和预算内推进；反馈经复核入库，形成学习证据。模拟心理与可追溯行为报告供判断参考。Chronicle、角色笔记与学习证据分别维护。 |
| archives、scheduled_backup、backup_v2*；scripts、deploy、tests | 聊天档案迁移、计划备份、加密快照及隔离恢复。scripts/yosaki_runtime.py 执行启动守卫。迁移脚本在 scripts/migrate_*.py 与模块 schema/init_db。deploy 含 Nginx、Docker 与 VPS 指南。 |

## 功能进度与证据

| 功能 | 已实现、日常加载与实际验证 | 仍有限制 |
|---|---|---|
| 聊天、角色、模型、MCP/插件 | 日常已装载消息操作、版本/分支、快照、附件、上下文统计；永久模型回显、单次设置跨刷新已修复。聊天专项记录有 6 次真实 API、1 次真实套餐调用。认证、角色卡、工具发现/参数校验/逐次确认和受控 JSON 插件有隔离验收。 | 网页默认关，公开 GET 的旧试验在准入处停止；真实系统截图/剪贴板、精确 token、长前缀缓存效果、套餐视觉/硬上限、完整心理状态注入仍分项待验。 |
| Chronicle、记忆 | 日期精度和人工优先、批量审核、逐次工具确认和续答、后台本地归档、按需加载精修、单条删除均已接入。现用产品的 read_draft / p2Projection 函数在只读日常库逐条复核。H5-B 为 130 来源、13 发布，09 单独有文档向量、手动补搜、完整展开、30 秒被动单次注入证据；自然问法普通 Hybrid 原样未命中。其余 12 已发布，向量及聊天命中未逐条重验。 | 套餐导入的 898 来源批 59 条现均可投影；509 来源批原 60 段，现 59 条可投影、1 条归档。银叶馆合成验收条目已归档。早期“其余 12 待审”和“59 均待审”仅是当时状态。未扩展自然问法稳定性、逐条内容质量或半年/年度真实质量结论。阿夜展开到合资格精修，RAW/CLEANED 仅按管理权限访问。 |
| Voice | 日常有 HTTP 消息朗读、缓存、主动 Scribe、可选自动朗读和需确认的连续通话；通话默认 HTTP，可选 TTD WS。普通聊天短日语 R2 的两自然段、多位置表达在同一 HTTP 请求中生成一份音频，真实供应商、软件播放和 Owner 听感分别通过。日常规划能力已加载，现场用户 AI 启用记录为零。 | 单样本结论。长文多请求首段 unknown、后段未发；AI 表达到通话、WS、其他语言及长文、跨设备稳定性和固定 Benchmark 尚未完整验收。不要为交接重发。 |
| 欲望/意愿与自主活动 | Phase 1～3 驱力、内部观察、未决意愿和有界本地环境操作在源码与日常接口中。隔离角色有套餐真实决策和本机环境操作；控制请求体及状态冲突/重启专项已验证。 | 日常真实活动频率、第三方 MCP、物理设备和并发自主执行没有本轮验证。 |
| 反馈、学习、心理及行为观察 | Phase 4 反馈复核应用，Phase 5 证据、偏好/策略与目标，Phase 6 心理状态和关联图已实现并接入。6.5 记录两日隔离角色真实观察，有经 Owner 确认的反馈与心理评价。30 次脚本调度是隔离工程证据。 | 长期偏好需审核。七日真实观察未完成，不补记；心理模型的长期效果没有据此验证。 |
| 备份与部署 | 本机 V2 加密备份、独立恢复有专项证据；旧导入入口拒绝覆盖活动库。日常备份计划 UI 可读状态，排程依赖后台配置。Docker/Nginx 配置已提供。 | 本次未执行云备份；VPS、生产安全审计、跨设备恢复未在本交接验收。 |

## 自动任务、人工决定与费用

- 本地归档从一次明确的启动意图继续，依靠持久游标、回执和幂等保护跨关页/重启。隔离进程写完第 100 条后中断，可恢复完成 450 条且不重复。暂停、取消、失败均保持原状态。检索与状态重读可以自动进行。发送、系统选屏、录音许可、内容确认/发布和主观听评仍按产品入口处理。
- **周期摘要常设授权**现场 enabled=true、revoked=false、recall=true。香港 2026-11-25 22:26:07 至 2026-12-25 22:26:07，阿夜当前及今后合资格的已发布经历，只允许已确认标题、单经历梗概及起止日期，gpt-6.1-sol Standard。每篇 USD 0.15、UTC 日 USD 1、期间 USD 10。11-27 只读账本累计估算 USD 0.211268、预留 USD 0、剩余 USD 9.788732。原文档 USD 9.995846 是较早快照。发布、来源变化、启动和香港零点可触发维护；跨日须重新核价，校验失败或超限则暂缓。召回仍检查成员集合、材料版本、权限和预算。
- **聊天补查**独立核价止于香港 11-26 08:00，启动记录保存的核价日仍为 UTC 11-25。再次使用前核实当日费率、账户和加载配置；每条显式同意上限 USD 0.037，聊天生成另计。12/30 秒仅是等待上限，迟到结果不注入。通用 Chronicle PAID 开关为 0。
- **其他外发**仅沿用已限定账户、材料和单次授权。选档预览与本机归档零外呼，套餐加工须由对应入口授权；新增私人材料独立许可。旧 Voice、聊天及 embedding 验收身份、次数和预算均不结转。未见下一功能的通用外发额度。运行开关、Voice 日限额和套餐连接都不构成新授权。本轮没有发起任何付费测试。

## 日常启停、候选加载与恢复

1. 新窗口先运行 `git status --short --branch`、`git rev-parse HEAD`、`git remote -v`、`git worktree list`。新开发在交接分支对应 SHA 建独立工作树，先核对当前任务范围及与日常源码的差异。以包内 package-lock.json 安装，前端 `npm ci`，再设 `VITE_YOSAKI_CHRONICLE_A0=1` 后 `npm run build`。Python 3.12 环境依 backend/requirements.txt 安装。
2. 初验使用 scripts/start_yosaki_test.ps1 或隔离数据目录。导入 backend.app.main 前先指向新的 YOSAKI_DATA_DIR，关闭后台及外部派发。原型的模块导入会初始化表。前端单独使用空闲端口，并将 YOSAKI_API_PROXY 指向同一隔离后端。端口冲突换端口。
3. 日常组合在原目录运行。现场后端实际由 `%LOCALAPPDATA%/YOSAKI/voice-runs/voice-ai-daily-install-20261125/load_daily.py` 读取相邻 runtime-flags.json，进入受保护的 development + daily Voice + TTD WS + AI expression 路径，workspace/context 开。实际前端为已配置的 Vite 5173。恢复原模式优先在原目录复用既有本机启动配置；仓库入口为 scripts/start_yosaki_voice_daily.ps1（对应 -EnableTtdWs -EnableAiPlanning）及 scripts/start_yosaki_voice_daily_frontend.ps1。前端需继承已批准的 Chronicle/Voice VITE 配置；独立源码检出不含本机启动配置、授权文件或密钥。
4. 需要加载时先冻结源码、前端资源、请求及权限。读取并行差异，在新鲜 SQLite 一致性副本做 Schema/行数/外键及目标路径验证，用假供应商走实际入口。确认任务、通话和写入空闲，留一致性备份及独立恢复证据。只在授权加载窗口按监听 PID、命令行、根目录辨认实例，在原控制台 Ctrl+C 正常停止并等监听释放，再启动同一受保护配置。不要按文档旧 PID 停其他进程。核对 /health、/openapi.json、登录门槛、实际路由、前端转换资源、业务路径与加载清单。
5. 代码回退逐文件核对当前字节与加载清单，已变化则反向合并。加密备份经 scripts/restore_backup_isolated.py 恢复到独立目录，比对 integrity_check、foreign_key_check、Schema 和必要行摘要。保留当前来源、分支、审核、claim、可靠用量和 unknown 预留；不能用历史快照覆盖业务库。证据丢失不自动对账，未证明未派发不得重复提交。停止/耗尽的旧批保留原 manifest、回执和账目。

| 本机恢复位置 | 内容 |
|---|---|
| %LOCALAPPDATA%/YOSAKI/workflow-audits/chronicle-durable-20261126/ | before、installed-hashes.json、handoff-before.sqlite3、handoff-restore |
| %LOCALAPPDATA%/YOSAKI/chronicle-performance-1795701346916/ | before、FINAL_FROZEN_FILES.json 及独立副本 |
| %LOCALAPPDATA%/YOSAKI/chronicle-entry-fix-1795705940474/ | 最近六文件 frozen.json、before、before.sqlite3 与演练库 |
| %LOCALAPPDATA%/YOSAKI/voice-runs/voice-ai-daily-install-20261125/ | before-source、handoff-backup.sqlite3、handoff-independent-restore.sqlite3 及既有启动配置 |
| docs/CHAT_ENHANCEMENT_HANDOFF.md | 聊天增量迁移、加密备份和只读兼容回退的本机定位 |

## 开发顺序与阅读入口

1. 首读 AGENTS.md、本文和 docs/PLAN.md。Memory 专项通读 docs/MEMORY_V2_EXECUTION_PLAN.md，当前结论看首份 C1～C10 表和本页现场数据。架构分域见 docs/MEMORY_V2_ARCHITECTURE.md。UI 改动先读 docs/UI_DESIGN_STANDARD.md，再看 docs/UI_REDESIGN_PLAN.md、design-qa.md。减少人工操作与持久任务的证据在 docs/WORKFLOW_AUTOMATION_AUDIT_20261126.md。
2. 聊天读 docs/CHAT_ENHANCEMENT_HANDOFF.md、CHAT_ENHANCEMENT_FOLLOWUP.md 和根目录 v0.2 计划。Voice 从 docs/VOICE_DELIVERY_INDEX.md、VOICE_FINAL_ARCHIVE_20261125.md、VOICE_NEXT_EXECUTION.md 进入。驱力到心理链依次见 docs/DESIRES.md、VOLITION.md、AUTONOMY.md、FEEDBACK.md、LEARNING.md、PSYCHOLOGY.md、BEHAVIOR_OBSERVATION.md。运行与备份读 docs/RUNTIME_MODES.md、R2C_EXECUTION_RESULT.md、H5A_LARGE_BACKUP.md。
3. 优先处理与下个功能相交的契约差异。已知 Memory 元数据旧测试仍把无时区日期的 ageEligible 视为不可用，与已交付的来源日历日规则有出入。跨进程供应商对账、长文本诊断封包容量、关页后的永久文字差异记录仍有限。旧 OpenAPI ForwardRef 500 已修复且本次现场 HTTP 200；Chronicle 时间树的重复扫描超时已修复并加载，留存正式浏览器约 1.26 秒、相关回归 82 项。二者均不应再作为本次阻塞。
4. 依次验证受影响的隔离专项、共享入口和用户可见页面，再考虑真实服务的最小必要验证。聊天的自然问法、长期行为趋势、长文多请求音频与跨设备听感各自有独立证据门槛。内容取舍、主观质量、凭据、系统人工确认及扩大外发/付费范围由 Owner 决定，其余定位、恢复和验收由 Codex 完成。

