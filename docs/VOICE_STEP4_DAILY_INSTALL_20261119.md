# 第4步：短日语 HTTP AI 表达规划日常安装结果

日期：2026-11-19（Asia/Hong_Kong）。安装编号：`voice-ai-http-daily-install-20261119-r1`。

## 已生效的范围

受保护的日常 8000／5173 已加载新版。只开放普通聊天中，真实完成、流结束、回答绑定和展示核验通过的短日语单请求 HTTP AI 表达规划。入口为「语音 → AI 表达规划（普通聊天 · 短日语 HTTP）」。

服务能力已启用，用户开关仍关闭，规划档案仍未选择，revision 仍为 0；读取设置不会创建设置行。本次没有启用用户 AI、自动朗读或麦克风，没有更改 Profile、模型档案、告知或额度。档案 5 `gpt-6.1-sol` 可在列表中明确选择。旧消息没有新完成回执，不能据正文或 completed 补造 AI 许可；普通预览、普通朗读入口与已有缓存继续可用。

本次模型、OAuth、模型目录、HTTP TTS、Scribe、TTD WS 调用均为 0，未创建收费批准记录。实际套餐授权状态只核对本地 connected／plan_allowed；这不是凭据有效期或真实模型可用性证明，未用请求试探或刷新。

## 目标合并与并行工作保护

先保存当前源文件及哈希，再在固定隔离副本合并已审阅的 13 个目标。`App.vue` 当前已有的模型设置和聊天面板修改保留，不整文件覆盖。Memory、模型设置、依赖锁文件和启动脚本原有 Memory 价格日期读取均保留。最终 449 文件源集合核对：16 个安装目标之外的全部原有复制源文件字节均未变化。

13 个审阅目标：

- 后端：`backend/app/main.py`、`backend/app/voice/annotation_store.py`、`annotation_http_product.py`、`backup_policy.py`、新增 `ai_daily.py`。
- 前端：`frontend/src/App.vue`、`frontend/src/voice/VoiceSettings.vue`、`MessageAudioPlayer.vue`、新增 `AIExpressionSettings.vue`、`AIMessageAudio.vue`。
- 仅用于隔离验收：`scripts/voice_ai_chat_candidate_fixture.py`、`scripts/voice_ai_chat_browser_fixture.py`、`tests/test_voice_ai_daily_candidate.py`。这些脚本没有被日常入口安装为假供应商路由，也没有在日常进程运行。

必要的受保护加载补充：`scripts/start_yosaki_voice_daily.ps1` 增加显式 `-EnableAiPlanning`；`scripts/yosaki_runtime.py` 增加 `--ai-expression`、精确数据库路径与六表/索引 DDL 预检，必须配合 daily Voice；新增 `tests/test_voice_ai_runtime_entry.py`。没有绕开启动保护或在导入时迁移新表。

安装后发现现有进程并未启用旧 V3 手选表达能力，已保留该配置：`MessageAudioPlayer.vue` 的 thoughtful 选项按后端能力显示，避免展示不能使用的选项；没有擅自开启其他表达功能。此修正已同步隔离副本、完成最终构建并由实际页面验证。

`AGENTS.md` 已合并「执行与验收分工原则」：Codex 连续完成已授权技术操作与验证；Owner 参与主观体验、无法代办的人工确认及范围外决定。保留原 Memory、UI、权限、unknown 和数据保护规则。

## 备份、独立恢复与精确迁移

私有证据目录（限制为本机用户、SYSTEM 和 Administrators）：

`C:/Users/ASUS/AppData/Local/YOSAKI/voice-runs/voice-ai-http-daily-install-20261119-r1/`

一致性备份为该目录下 `backup/yosaki.sqlite3`，独立恢复为 `restore-verified/yosaki.sqlite3`。两处均保留 `voice/audio/` 的 19 个旧音频文件。数据库备份 SHA-256：`cda1da7a07db010c2fee3caf4c85dcdce2d77de253050fa068bd497530f881e2`。

业务写入在维护窗口暂停；通过 SQLite backup API 制作新鲜快照。独立恢复逐表行/结构摘要一致，integrity 为 ok、外键错误 0，19 个音频文件逐个哈希一致。没有把真实凭据文件复制到备份；这是 SQLite 与音频的恢复验证，不宣称包含 OAuth／密钥恢复。

只新增下列对象：

| 表 | 自动索引数 | 来源 |
|---|---:|---|
| `annotation_plans` | 2 | 文本主键及 owner/input 唯一约束 |
| `annotation_operations` | 1 | 复合主键 |
| `annotation_revisions` | 1 | 复合主键 |
| `annotation_audio` | 1 | preview 主键 |
| `voice_ai_completion_receipts` | 1 | generation 唯一约束；整数消息主键使用 rowid |
| `voice_ai_owner_settings` | 0 | 整数 owner 主键使用 rowid |
| **合计** | **6** | **无显式索引，无旧表变更** |

正式迁移由不导入 `main.py` 的一次性进程执行 `BEGIN IMMEDIATE`。提交前比较完整 DDL 差异、152 张旧表逐表行摘要、外键和新表为空，全部符合才提交。运行中的正式路径为 `D:/AI xiangmu/自建前端/data/yosaki.sqlite3`。

新鲜隔离恢复库上先完成同一迁移及完整 main 导入、受保护启动和 lifespan 检查；限制网络只可本机，并监测 SQLite 路径，未触碰日常库。首次演练被测试侧 file URI 路径规范化检查拦截，保存失败副本后修正该验收检查，第二次完整演练通过；不是生产数据库缺陷，也未触发正式迁移。安装工具最初的新增测试文件字节校验还遇到 Git CRLF 规范化差异，已确认仅测试换行不同，运行代码字节一致，没有据此跳过运行代码校验。

## 验证结果及适用范围

| 检查 | 实际结果 |
|---|---|
| 本轮固定隔离候选后端组合 | 17/17：13 项 AI 产品测试与 4 项新增受保护启动检查，51.154 秒 |
| 已有 runtime 受影响回归 | 7/7，8.683 秒；与前项分开记录 |
| 最终前端类型检查与构建 | 通过；日常 flags 构建及安装后 `npm run build` 通过。原有大 chunk 提示保留，不扩展重构 |
| 新鲜快照完整启动 | 迁移后六表正确；main 导入/lifespan 无其他结构或业务写入；后台关闭；无测试路由；Provider 0 |
| 正式迁移 | 152 旧表结构和行摘要不变；恰好 6 新表、6 自动索引；新表空 |
| 实际 HTTP 产品页 | Edge 访问日常 `/voice`、`/chat`，61 个实际静态资源加载；未替换产品页面、未注入日常假供应商 |
| 登录与设置 | 使用短期本地验收会话检查真实登录态及 API 鉴权；不是重新验证用户密码。AI available=true、enabled=false、revision=0、档案=null |
| 旧消息和缓存 | 实际消息 59 普通预览 HTTP 200：45 字、单请求、none；普通朗读和缓存按钮可见。原绑定 15 音频 GET 200、audio/mpeg、153434 字节；只读取，没有合成或播放听评 |
| 实际页面写入 | API 写请求 0；没有保存/启用新用户设置、生成聊天、规划或合成 |
| 收尾数据复核 | 与迁移提交后相比，158 张表结构和行摘要全相同；152 张旧表及 19 份音频不变；六张新表仍空，integrity ok、外键错误 0 |
| 临时登录清理 | 浏览器退出成功，专用会话已不存在；私有 cookie 状态文件已删、专用 Edge 已关闭。原有 25 条会话保留，不称为全部会话撤销 |

假模型/假 TTS 的设置保存刷新、冲突、规划失败只回退一次、unknown 不追加、编辑版本、缓存重播、取消及模式切换迟到隔离，沿用对应固定候选的 [产品页证据](VOICE_STEP4_CHAT_PAGE_ACCEPTANCE.md)。这些是隔离证据，不称为日常真实供应商成功。

## 实际运行与回退

当前后端单 worker、8000 及前端 5173 健康。后台任务为 0；Memory workspace/context 原值 1、付费原值 0；代理和供应商密钥配置只在本机比较，均未变。现有 WS 和耳机插话前端能力保留，不因此扩大新 AI 模式范围。

实际后端沿 `start_yosaki_voice_daily.ps1 -EnableTtdWs -EnableAiPlanning`，前端沿既有 `start_yosaki_voice_daily_frontend.ps1 -ExperimentalWsAndBarge`。运行环境以本批私有 DPAPI 保护的配置记录为准，不把密钥写入文档或启动命令。没有使用裸 Uvicorn 绕过保护。

兼容回退：结束经过确认的当前进程，以原受保护配置去掉 `-EnableAiPlanning` 加载（前端原配置不变）。这会使 AI 服务能力关闭，保留普通聊天、普通朗读及旧缓存；新增表、后续记录、音频和历史用量保留，不 DROP、不用旧备份覆盖活动库。`code-before/` 保存本批修改前的目标源文件，`final-effective-install.patch` 为对当前并行基线的最小差异，不能用整目录覆盖 Memory 工作。此次安装成功，未执行回退。

## 最终版本与证据

- 最终 16 个安装目标集合：`4d6c1b2395bd709cff7773ba2f258b999b86a8d29eaf8dc022b729ec87ecfc0f`。
- 最终实际 449 文件源集合：`925a607478efc9a1c133fcf29169844f979dd318deb0241c8b50faddd4a6b9a9`，含当前并行源码，完整路径/哈希见私有目录 `full-installed-source.json`；不是将旧 343 文件集合冒称为当前版本。
- 最终对本次当前基线的补丁：`b29ce8248be91f30c73519f721156e77cabba24b89a73617afff5cb02b1e5f15`。原审阅候选及原补丁保留，不覆盖为新授权。
- 依赖未由本批修改；实际锁文件及 requirements 哈希在 `full-installed-source.json`。
- 原始证据：`backup-verification.json`、`rehearsal-startup.json`、`formal-migration.json`、`formal-before.json`／`formal-after.json`、`installed-runtime.json`、`http-page-check-r4.json`、`http-validation-logout.json`、`validation-cleanup.json`、`final-audit.json`。之前失败的页面驱动记录原样保留，不冒称生产故障。
- 页面截图仅保留 AI 设置区域，没有 Voice ID、密钥或真实正文。临时目录历史清理继续独立管理，不绕过审批或清理其他工作。

## 下一次最小真实产品听评提案（未执行、待决定）

目标是在最终日常产品入口验证「真实聊天完成回执 → 一次 AI 规划 → 一次短日语 HTTP → 缓存播放」，不重复旧音频或历史 unknown。

建议优先使用安装后由用户正常完成的一条短日语回复，必须由真实聊天流程取得新回执。当前六表为空，尚无这样的新样本；旧消息 10／59 不补造许可。如果准备阶段已有合格的新回复，听评只需 **1 次规划模型（档案 5 `gpt-6.1-sol`，512 输出 token、20 秒总期限、零重试）+ 1 次 HTTP TTS（含标签最多 120 个实际送读字符、30 秒／16 MiB）**。规划允许零标注。用户可明确选其他档案，但届时须重新冻结该实际请求。

若仍无合格新回复，建议同一批准批次额外允许 **1 次普通聊天生成（档案 5、沿用当前已核对角色/展示流程、2048 输出 token 上限）**，用新空对话请求：「日本語の短い二文で、夜更けに静かに話しかけてください。全体を六十文字以内にしてください。」字符要求是内容约束，不当作可靠硬限；完整模型上下文、实际派发参数和有效凭据须在这次批准前补齐冻结，不自动截断人格或正文。当前仅为预算方案，不是已冻结可执行的收费候选。

总预算因此为有合格消息时 1 次模型／512 输出 token + 1 HTTP／120 字；需新回复时 2 次模型（聊天 2048、规划 512）+ 1 HTTP／120 字。Scribe、语音 WS、OAuth 和目录请求均为 0，凭据不足就停。先检查规划预览及实际字符数，超过 120 字或不能单请求时停止，不自动缩写、截断或改参数。只合成 AI 版，不补做普通版；原音频若可用仅作历史参照，不称严格同批 A/B。

试听前必须明确批准选档、上下文和这次真实预算；本次安装不创建收费收据、不自动开启用户 AI 设置。日额和批次额度共同限制；失败、unknown、取消不重发，usage 与费用分别记录。实际费用目前无法可靠换算，不填零。听感由用户在正常产品界面确认，刷新和重播仅使用已存音频。

连续通话、WS、更多语言、新标签效果稳定性和真实跨段长文继续独立待办。历史 unknown 与费用未知不变，安装成功不扩大为第4步全范围或整个语音系统通过。
