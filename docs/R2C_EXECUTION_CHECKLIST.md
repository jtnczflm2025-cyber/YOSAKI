# R2-C 正式切换执行单（待批准）

日期：2026-11-10（Asia/Hong_Kong）。本轮仅准备和隔离验证；未授权真实日常库初始化或服务切换。

## 1. 基线与隔离证据

Git HEAD：`1e096aeecddda8c5e8989fda33026e4fb658353f`。工作树包含未提交实现，HEAD 不能单独代表验收版本。配套 `R2C_SCHEMA_AUDIT.json` 的 fingerprints 固定全部 backend/app Python 源文件、后端依赖清单及启动入口 Python 的 SHA-256；隔离验证前后指纹一致。该文件同时保存全部新增对象 DDL、列定义、表行数及差异结果，不包含聊天正文、密码、Token、API Key 或 Voice ID。

R2-B 已通过的 348/348 完整回归继续保留，本轮没有无变化重跑。此次指纹是当前代码的新基线；此前没有持久化的完整验收源码指纹，不能证明其他开发在两次验收间完全没有改动。故重新验证了当前初始化链及其数据库效果。正式执行前再次比较指纹；任何差异须定位到导入、初始化或启动钩子的影响并复验，不能沿用旧数量直接执行。

新鲜 SQLite 一致性快照来自项目 data/yosaki.sqlite3，通过只读源连接 backup() 创建于独立临时目录。仅在快照中导入 main，禁用 socket connect，Voice=0，不运行 ASGI lifespan，不启动 HTTP 服务。第二次独立进程重复导入验证幂等性：Schema 一致，既有字段和记录一致。完整性 ok，外键错误 0。

## 2. 精确变化

最新基线：用户 1、角色 1、模型档案 3、对话 3、消息 5；Voice Profile 0、告知 0；backup_plan、awakening_sessions、activity_plans 均为 0。正式执行时必须重新记录数量及全部既有列的逐行摘要，不能把这些数字作为永久常量。

预计新增 38 张表：

- `feedback_activities_v4`
- `feedback_collection_v4`
- `experience_reviews_v4`
- `drive_regulation_v4`
- `activity_cooldowns_v4`
- `interest_evidence_v4`
- `goal_feedback_v4`
- `feedback_controls_v4`
- `learning_evidence`
- `learning_preferences`
- `learning_preference_proposals`
- `learning_preference_links`
- `learning_preference_history`
- `learning_memories`
- `learning_strategies`
- `learning_strategy_uses`
- `learning_goal_notes`
- `learning_controls`
- `learning_batch_runs`
- `learning_subject_suggestions`
- `learning_audit`
- `psychological_snapshots`
- `psychological_cutovers`
- `psychological_events`
- `psychological_conflict_decisions`
- `second_order_desires`
- `second_order_history`
- `psychological_edges`
- `psychological_controls`
- `psychological_appraisal_runs`
- `behavior_model_calls`
- `behavior_resource_samples`
- `mv2_meta`
- `mv2_sources`
- `mv2_terms`
- `mv2_segments`
- `mv2_segment_members`
- `mv2_jobs`

新增列（NULL 表示没有显式默认且允许 NULL）：

| 表 | 列 | 类型 | NOT NULL | 默认值 |
|---|---|---|---|---|
| messages | origin_user_message_id | INTEGER | False | NULL |
| messages | origin_user_content_sha256 | TEXT | False | NULL |
| messages | origin_character_id | INTEGER | False | NULL |
| awakening_sessions | feedback_review_limit | INTEGER | True | 0 |
| awakening_sessions | feedback_review_tokens | INTEGER | True | 600 |
| awakening_sessions | feedback_review_calls_used | INTEGER | True | 0 |
| awakening_sessions | feedback_review_output_used | INTEGER | True | 0 |
| awakening_sessions | psychology_appraisal_limit | INTEGER | True | 0 |
| awakening_sessions | psychology_appraisal_tokens | INTEGER | True | 600 |
| activity_operations | expected_enjoyment | REAL | False | NULL |
| activity_operations | strategy_id | TEXT | False | NULL |

新增 48 个索引对象，其中 SQLite 自动索引体现主键/唯一约束；具体名称和所有显式索引 SQL 见审计 JSON。新增表的外键、CHECK、UNIQUE、NOT NULL 和默认值以 JSON 中完整 CREATE TABLE DDL 为精确清单。没有新增触发器或视图；旧对象仅三张补列表的 DDL 改变，未删除旧对象。

新增记录只有 mv2_meta 一行：character_id=1、schema_version=2、active_generation=0、order_revision=0、state=needs_rebuild，updated_at 为初始化时刻。其余 37 张新表为空。既有表全部旧列逐行摘要保持一致，包括 sqlite_sequence；不输出其敏感字段。需要区分 SQL 执行与实际影响：初始化仍执行 INSERT OR IGNORE、条件 UPDATE 等语句，本次旧数据未匹配更改条件，不代表所有输入库都无写入。

已检查的条件写入包括：缺少角色时创建阿夜及提示词；角色缺少 context_settings 时补设置；Memory V2 创建 meta、将 running 索引任务暂停、旧顺序契约升级时使任务失效并标记重建。本快照只产生上述 meta 行。导入还可能在缺少本地 Fernet 文件时创建 .secret_key；正式执行前必须确认现有密钥材料及作用域，不能让初始化替换密钥。不得输出其内容。

## 3. 业务后台任务启动门控

2026-11-10 已实现 `YOSAKI_BACKGROUND_TASKS_ENABLED`：仅接受 0/1；0 在创建任务前跳过反馈、心理状态、定时备份三个 startup 轮询入口；未设置默认 1，保留既有启动策略；非法值拒绝启动。Development 专用入口在导入 main 前强制设置进程内值为 0，即使继承值为 1 也不启用。Preview、Test 与直接 Uvicorn 未显式设置时保持原行为；需要禁用时显式传 0。没有修改 Windows 用户或系统环境变量。

全量搜索 backend/app 的 on_event/lifespan 注册点，仅发现上述三个 startup 入口及对应 shutdown；自主活动另有 shutdown 清理，没有 startup worker。生命周期保留，未使用 --lifespan off；关闭分支仍执行安全 shutdown。此开关只控制自动启动轮询，不撤销用户显式业务 API 的能力；切换验收不得主动调用索引、学习、心理状态采集或自主活动接口。

专项在已完成导入初始化的临时库上记录完整数据库逻辑基线，再进入/退出真实 lifespan：关闭分支任何 create_task 都令测试失败，网络 connect 被禁止，退出后数据库完全一致；开启及默认分支使用模拟任务截获，不运行真实 worker。测试结果见末尾验收记录。原 348/348 作为历史记录，不能代替本次验证。

## 4. 受控增量初始化顺序（待授权后执行）

1. 记录 Git 状态和文件指纹；确保目标绝对路径为 D:\AI xiangmu\自建前端\data\yosaki.sqlite3，确认没有进程正在写入该库；核对文件存在、SQLite 实际路径及新鲜结构。任何新增差异先停止。
2. 完成下述一致性备份和独立恢复验证；记录角色、模型、对话、消息及所有旧表旧列的摘要和行数。确认 Voice 两表仍为空、Voice=0。
3. 在独立的一次性管理进程中明确设置 YOSAKI_DATA_DIR 为目标绝对目录、Voice=0，禁用对外网络；再次校验实际路径和已批准的前置 Schema 指纹。该进程只执行与隔离演练相同的 main 导入初始化，不启动 Uvicorn、ASGI lifespan 或前端，不临时关闭 Development 保护。
4. 初始化使用现有 CREATE IF NOT EXISTS / ADD COLUMN 逻辑，不能覆盖或重建数据库。executescript 链不保证整个初始化原子提交；异常时可能留下部分新增结构，立即停止，保存错误和现场，不自动重试或回滚覆盖。
5. 关闭一次性进程后以只读连接核对实际路径、DDL 差异、完整性、外键、旧列逐行摘要、新增记录及 Voice 状态；差异必须严格落在已批准清单内。
6. 只有结构核验通过、后台任务门控已获批并验收后，才运行 Development 专用入口。预检保持启用，应因结构已准备而自然通过。当前阶段不执行这些步骤。

## 5. 备份、恢复与失败处理

- 在正式写入前，从只读源连接使用 SQLite backup() 生成唯一命名的一致性备份；不直接复制可能带 WAL 的主文件。保存到用户认可、受访问控制且容量充足的目录，保留绝对路径、时间、大小、SHA-256、完整性与外键检查结果。
- 此备份是含凭据密文、账户与会话的敏感运维快照，不等同于应用脱敏 .ybackup；不得提交 Git。现有 Fernet 密钥材料单独受控保全，不输出到报告。不能依赖脱敏 .ybackup 保留全部运行凭据。
- 用备份恢复到另一个全新目录，验证文件哈希、完整性、表结构及旧字段逐行摘要。验证连接保持只读，不导入 main，不运行用户任务；不得覆盖源库。可用隔离初始化副本重复演练，但与原样恢复目录分开。
- 保留当前预览库及其启动方式、目录、端口和进程证据。切换前更新现场记录，不以历史 PID 为操作依据。直到日常服务的非写入验证完成才安排现有预览服务的停止/端口交接，相关操作仅在正式授权后进行。
- 启动或真实登录失败：停止仅属于本次切换的 Development 进程，保留日常库及日志现场；恢复原预览入口和原代理设置。若原预览仍在则无需重启。不得自动用备份覆盖日常库，不修改真实密码；需要数据修复或恢复时另报方案。

## 6. 切换验收与停止条件

- 日志 Mode=development、Voice Enabled=false；通过实际 SQLite 连接 PRAGMA database_list 确认为目标绝对路径。
- integrity_check=ok、foreign_key_check 无错误；新增对象、列、记录严格符合最新清单；全部既有字段与行数保持，包括用户凭据摘要、角色及模型档案。登录会新增 session，必须在结构一致性验收之后执行并单独记录，不能混入初始化差异。
- 当前基线是 3 段对话、5 条消息；正式切换前重新核数。检查 ID、归属及原字段摘要，不在报告输出正文或凭据。
- 用户自行输入真实密码完成登录；不要求把密码发给助手，不代改密码。登录失败不自动重试到锁定。
- Voice Profile 不绑定、不激活，告知不确认，Provider 不测试，Voice 总开关关闭。Memory V2 保持 needs_rebuild，不自动索引或接入聊天上下文；不触发学习、自主活动、心理模型评估、备份定时任务或其他外部请求。
- 备份不完整、指纹漂移、数据库额外写入、路径冲突、后台门控未完成、真实登录失败时均停止切换并报告。

执行单状态：可审阅；后台任务门控已实现；正式切换仍须单独批准。真实日常库未初始化、未切换服务，未进入 V1-B/V2/R2-C 正式执行。

## 7. 8000 端口交接与失败回退（仅执行单，尚未操作）

1. 正式授权后先记录 8000 的实际监听 PID、父进程、工作目录、预览数据绝对路径、启动命令及 5173 代理配置；不能直接照抄旧 PID。确认用户没有进行中的操作。
2. 原预览服务保持在线，先完成日常库备份、受控一次性初始化和只读验收。期间不运行第二个占用 8000 的服务。
3. 全部前置验收通过后，优雅停止确认属于预览的 Uvicorn 及 reload 父进程；等待 8000 释放，确认不会自动拉起。不要杀死未核实归属的进程，不删除预览库。
4. 从无冲突数据目录覆盖值的独立终端运行 Development 专用入口，绑定 127.0.0.1:8000；入口强制 Voice=0、业务后台任务=0。保持现有前端 5173 代理到 8000，无需改代理。检查实际 SQLite 路径、health、关闭状态，再由用户输入密码登录。
5. 任一失败：停止并确认只停止此次 Development 进程；保留日常库和错误现场，等待 8000 释放。按步骤 1 捕获的原预览启动方式在 8000 恢复原预览目录和原有后台策略，保留 Voice=0；不得恢复到日常目录或因回退初始化其他库。
6. 回退确认 8000 实际 SQLite 路径为原预览目录、health 正常、5173 仍指向 8000；用户自行登录。若无法确认原命令或父进程归属，停止并报告，不猜测。不得自动用备份覆盖日常库。

## 8. 后台门控验收（2026-11-10）

专项：`tests.test_background_tasks` 与 `tests.test_yosaki_runtime`，4/4，通过。完整隔离后端：349 项通过、0 失败、退出码 0、885.194 秒；此前 348/348 保留为历史结果。前端未改，未重复构建。

首次专项网络拦截误挡 Windows asyncio 内部 socketpair；修复为事件循环建立后、进入 lifespan 前拦截 socket connect。没有放宽业务网络限制或更改业务通过条件。关闭分支验证无业务任务创建、无网络、无数据库逻辑变化与安全退出；开启/未设置分支使用模拟任务，不执行真实 worker。

测试前后固定源码指纹一致。回归进程已退出；原预览 8000 PID 10796、前端 5173 PID 696 保持不变。没有进行真实日常库初始化、服务切换、密码或密钥修改、Voice 绑定/确认、ElevenLabs 请求或正式 Memory V2 任务。等待正式切换批准。
