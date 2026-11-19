> 历史批准单：下文保留批准时原文。该授权已于2026-11-19执行完成；不构成新的迁移／切换或外发授权。当前状态见[最终交接](CHAT_ENHANCEMENT_HANDOFF.md)，实际加载记录留在本机外部证据目录。

# 日常加载批准单 — 2026-11-19

状态：准备完成，待 Owner 批准执行。批准对象为 `D:/AI xiangmu/YOSAKI-CHAT-LOAD-PREP-20261119` 的最终文件清单与补丁。本单没有执行日常库复制、备份、迁移、服务停止或 Runtime 切换；本轮 Provider／OAuth／网页请求均 0。下述步骤可作为一次完整加载授权，遇明确停止条件即保留当前状态并报告，不机械逐步请求继续。

## 1. 文件与当前基线

- 最终精确文件、加载前日常哈希、加载后哈希、用途见 `evidence/LOAD_FINAL_FILES.json`／CSV；最终补丁 `evidence/chat-daily-load.patch`。文件总数 49（原45＋用量显示函数、其验证脚本、只读兼容导出脚本和本加载单）。不复制独立候选整目录、node_modules junction、测试数据库、截图、凭据或生成的 vue.js。
- 初始以已接受 `REAL_FINAL_FILES.json` 验证旧候选45个文件哈希，再逐一核对此刻日常目标，45个加载前哈希全部相符，原 `real-final.patch` 实际 `git apply --check` 和应用成功。随后只补显示修正和本加载单／回退工具，最终补丁在第二独立目录再次实际应用；见 `FINAL_APPLY_CHECK.json`。
- 非目标文件取自此刻日常工作树，Memory 新条目关键词默认 v3 的6个后端／工作流测试、Voice HTTP阶段比较专项等保留；ChronicleEditor及共享UI与当前日常相同。逐项保留核对见 `PRESERVED_PARALLEL.json`；文档／清单差异与业务源码差异分开，不能说所有文档差异都重新跑了业务测试。
- 执行批准后、任何写入前再核对全部最终目标的加载前哈希。不存在的文件仍须不存在。若有新差异，先在隔离目录合并并按影响验证，不能强制覆盖或整目录替换。非目标源码再次对照本轮基线，保留期间新增工作。不 reset／clean／stash。

## 2. 需要批准的实际操作范围

仅允许先从本机已有隔离包仓或缓存补齐上述固定依赖（不联网下载、不改其他依赖），再按最终清单写入日常源码、对同一路径的活动 SQLite 增量安装聊天 schema、沿原 Development／daily Voice Runtime 模式受控重启，并执行下述零外呼验收。允许在受保护本机目录制作字节备份、一致性 SQLite 快照及加密 .ybackup，并恢复到新隔离目录验证；不外发私人内容、不替 Owner 审核发布 Memory，不调用模型、OAuth刷新、Embeddings、TTS／STT 或网页。

网页 `YOSAKI_CHAT_WEB_ENABLED=0`。不开放套餐视觉；chatgpt_plan 原有图片拒绝／输出硬上限 unknown 保留。API 的历史合成视觉验证不自动写成日常每个档案已确认，不迁移任何合成角色／对话／附件／能力声明或真实测试账目。保留日常角色、记忆开关、模型档案、Memory 发布及权限、Voice告知和原运行模式；自动摘要默认关闭，不为验收启用付费后台。

## 3. 数据库增量（只增结构）

唯一 DDL 来源 `backend/app/chat_schema.py:install`，15张新表：chat_attempts、chat_attempt_workers、chat_usage_receipts、chat_message_state、chat_branches、chat_branch_members、chat_path_exclusions、chat_memory_scope、chat_attachments、chat_message_attachments、chat_quotes、chat_translations、chat_summaries、chat_context_policy、chat_model_capabilities。

新增唯一索引 chat_one_active（prepared／dispatching／streaming 同会话唯一）；兼容安装补 chat_context_policy.summary_model_id、chat_attempts.root_id／dispatched_at 三列，仅存在缺列时添加。没有删除旧表／旧列，没有批量改写历史消息、摘要、Memory、Voice、审核、来源、任务、账目、unknown 或有效预留。旧线性路径在没有分支／隐藏记录时仍按原消息读取，旧手填摘要没有覆盖证明时不自动继承新分支。

迁移函数在独立副本先执行两次，验证幂等、integrity_check=ok、foreign_key_check为空、全部既有表行数和规范化内容哈希不变。新表应为空（除明确初始化结构）；不要把回归夹具或本轮真实测试数据装进日常。实际应用也仅执行 install，不先导入 main 借初始化迁移。之后 Runtime 预检必须通过才允许应用导入。

## 4. 执行顺序、备份及预计停用

1. **预备，服务仍运行。** 再核对哈希、依赖与构建；本轮 requirements 固定 Pillow==13.0.0／pypdf==7.1.1 等须核对当前环境满足，不把 .packages 路径部署为依赖。记录当前监听端口、服务身份和原启动参数（是否 daily Voice／TTD WS／自动表达等），只记录开关白名单、不记录密钥。确认用户无未保存表单、活动生成、工具执行、录音／播放／连续通话及付费worker；历史 unknown 不清零，也不作为必须清理的门槛。
2. **受保护备份目录。** 拟使用 `%LOCALAPPDATA%/YOSAKI-CHAT-DAILY-LOAD-20261119` 作为本轮目录，若已存在则新建带时间后缀目录。按现有 H5 `backup_v2_environment.validate_work_root` 核对 Windows ACL 与卷／目录加密；必要时先在空目录配置当前 Owner／SYSTEM／Administrators 的受限权限与 EFS，验证成功才复制私人数据。备份密码由 Owner 本机不回显输入，至少12字符，不落盘、不入命令行／日志。空间至少按现有H5计算式及WAL核对，并为原库、一致性快照、恢复库、迁移副本、回退保留足够余量。环境证明／空间不满足则在停服及私有复制前停止。本轮没有声称目标环境已通过这项实机检查。
3. **开始停用窗口。** 用户暂停写入后，正常结束日常后端及相关Voice进程，暂停Vite热加载以免混用新旧前后端；只操作确认属于日常实例的PID，临时验收实例不代替日常。确认无活动数据库连接／worker。保存原启动参数和实际环境白名单，原源码目标逐字节备份，记录本轮实际新建文件名单。
4. **双重备份。** 用 SQLite backup API 在受保护目录制作一致性原库快照，保留原 owner／模型凭据等本机运行态，不公开或放入Git；其权限／加密须同原敏感数据。该本机快照仅用于审计与隔离验证，不能在产生新数据后整库回写。另调用现有 `backup_v2.create_backup` 制作 `.ybackup`，它自带独立解密校验，并按既有规则剔除会话、API/MCP秘密及Voice告知／运行态／缓存等。两种备份用途不同；不把 .ybackup 当作包含所有本机凭据的即插恢复件。
5. **隔离恢复及迁移演练。** 用 `scripts/restore_backup_isolated.py --backup <本轮.ybackup> --target <受保护根下全新restore目录> --work-dir <已验证根>` 恢复；输入密码不回显。核对完整性、FK、表计数／来源／审核及账目摘要。再对恢复库运行 `scripts/chat_schema_candidate.py <恢复库> <全新chat-candidate.sqlite3>`，它拒绝覆盖源库并执行两次安装。由原一致性快照和活动库停服哈希证明源未变化。不要指向HTTP旧import或替换活动库，不启动恢复库的付费功能。脱敏差异按既有规则白名单解释，不要求恢复库有原密钥／session。
6. **加载与活动库增量。** 备份／恢复／副本校验通过后，逐文件写入最终清单；写后哈希相符，再对原活动库同路径显式执行 chat_schema.install 单次事务并校验旧表哈希、FK和integrity。原 data 路径、.secret_key、API凭据及OAuth授权文件不替换；本轮不刷新OAuth。迁移失败立即停止，不启动半迁移服务；保留证据与事务结果。
7. **沿原模式重启。** 使用日常现有专用脚本与刚记录的相同参数：Development 用 start_yosaki_development.ps1；原本daily Voice则使用 start_yosaki_voice_daily.ps1 并保留原有 EnableTtdWs／EnableAutoExpression选择。不要用裸Uvicorn绕过预检。网页设0；后台付费保持原批准关闭状态，不因恢复启动补发旧任务。Vite继续按原地址、代理及Chronicle开关启动。然后执行下一节检查。

预计**实际停用15～30分钟**，受数据库大小、磁盘加密及恢复验证耗时影响；前置构建／依赖／目录加密放在停用前完成。小型合成库的迁移约0.125秒仅证明逻辑，不能作为日常耗时承诺。超过30分钟或出现不一致则维持维护状态并报告，不跳过恢复验证赶时间。

## 5. 加载后的零外呼检查（含退出条件）

- 在健康／只读验收期间对本进程启用外部派发拒绝或本机网络出口监控，确认模型／OAuth／embedding／Voice供应商／网页新增请求均0。保持这些动作未触发：发送、重生成、翻译、摘要、模型测试、补查、录音、朗读、连续通话、Memory加工、网页确认。不用真实请求证明可用。
- Runtime输出数据库绝对路径与原路径相同，required tables／columns预检、health=200、登录维持预期；已有会话数量、最新原消息、消息详情／统计和仅只读 context-preview可开。对实际私人内容只在本机看，不复制进验收日志；日志只保留ID／哈希／计数／状态。
- 只读核对Memory当前发布与精修资格、Chronicle入口、Voice原档案／告知和空闲状态，保持正文来源与权限。工具确认卡存在但不批准、不续答。套餐视觉继续拒绝，网页目录未暴露工具，自动摘要未自行开启。
- 比较加载前后旧消息／来源／审核／账目／unknown／预留的表级哈希；认证session／正常启动运行态按事先白名单单列，不把白名单扩大到业务内容。新表结构、活动锁及无意外attempt分别核对。
- 未通过则不解除维护写入，按下节兼容回退。通过后交回本轮逐文件写入、实际迁移、Runtime参数、零外呼、数据保护哈希和健康结果，再解除维护。后续日常用户主动发送不属于本次零外呼验收，也不复用旧测试额度。

## 6. 新分支数据产生后的兼容回退

**禁止整库覆盖回退，禁止旧线性读取器接管新分支。** 一旦 chat_branches／message_state／exclusions／attachments／attempts 等有新业务数据，原加载前数据库不能恢复到活动路径。旧渲染器可能重新显示隐藏候选，旧发送器可能拼入其他路径；不能以“表是附加的”认定旧程序兼容。

- 界面故障：保留当前 branch-aware 后端、schema、Memory来源适配和Voice路径消费，仅回退到已验收的P5分支兼容组件／样式；必须重合并期间新UI，不能直接恢复整个旧App.vue。先在隔离副本核对当前路径和账目；没有验证过的组件不直接上线。
- 后端故障：停止全部新派发和写入，保留当前库／unknown／回执／附件及本轮字节。进入维护只读：运行冻结脚本 `scripts/chat_readonly_archive.py <最新一致性库> <受保护全新readonly目录>`，产生本地 index.html 与 paths.json。它不导入main、不联网、SQLite mode=ro＋query_only，只按 chat_history.rows 读取每个分支的当前可见顺序；保留主库全部旧候选和账目。结果含私人内容，限制本机受保护目录，不放Git或公开静态服务器。该方案提供历史阅读，不伪称继续聊天／Voice可用。
- 修复后用支持现有新schema的版本重新校验并恢复服务；数据库始终向前兼容，不 DROP新表、不把unknown结算成0、不删除冲突回执、不解开预留。仅当没有任何新业务写入且逐表哈希证明完全相等时，才可逐源码目标退回仍为本轮字节的原文件；若已有其他并行修改，先合并。
- 本轮合成验证：迁移旧结构→保留原路径分叉点后内容→创建新分支新内容→加密备份恢复→新／恢复两库只读导出一致，total_changes=0、network_calls=0；见 SCHEMA_BACKUP_ROLLBACK_REHEARSAL.json。此证据不替代批准后的日常恢复验证。

## 7. 验收身份与已知限制

`evidence/ACCEPTANCE_VERSION_BINDINGS.json` 精确关联 1509 项全量的原版本、后续91项关键词建议同步和最终43项统计预览／单GET变更；计数不相加。当前加载候选又保留后来日常Memory默认v3与Voice专项，本轮先补54项差异验收；期间新增Voice适配器修正后再补7项阶段比较及15项适配器时序／入口专项（组合有重叠，不相加），另有10条显示断言、三组真实组件合成回显、最终构建及合成迁移／兼容演练，没有宣称对最新合并版本再跑1509全量。

首次D原attempt、详情、汇总与外部0传输依据按ID关联在 `evidence/D_FIRST_ATTEMPT_AUDIT.json`；原unknown不删除、不写零usage。已知OpenAPI文档入口500未扩展修复，不影响此批准单的健康／聊天只读验收范围。首次B真实识图只对合成图样本成立，不开放套餐视觉，不导入合成能力声明。

## 可批准范围

批准本单即允许按最终哈希清单备份、隔离恢复演练、增量迁移、沿原模式重启及零外呼检查；允许按上述条件进入兼容只读回退。所有动作均本机执行，不含新增模型、OAuth、网页或Voice供应商请求，不含Memory内容审核／发布或数据重加工。当前回合停在执行前。
