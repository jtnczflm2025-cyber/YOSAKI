# P1-A2 渐进条目契约

状态：2026-11-08，**P1-A2 定向权限修复与隔离工程验收完成**。最终源码专项 26/26、完整后端隔离回归 320/320、前端类型检查与构建通过。正式入口不注册；正式数据库迁移、上下文接入及真实 H3.2 均未执行。

## 时间与依赖

`timeEvidenceToken` 覆盖有序来源身份、source ordinal、原始时间字符串、occurred_at、occurred_date、时间精度、时区依据。`timeToken` 由该证据和显式投影时区生成。recorded_at 仅作记录时间，不参与发生时间或年龄推断。未知本地时间不得触发年龄换档。

独立 `digestToken` 绑定输入 profile、实际来源、成员集合、下级摘要及实际使用字段。保留 raw_sources_v1；synopsis_v1 和 narrative_v1 分别增加对应字段依赖，不因其他字段修改无条件失效。

## 字段和词库

Categories 是受控多维分类；Tags 优先是 RAW 中有直接证据的实体与主题；Keywords 是检索表达，原文词项与语义扩展分别标记。三者分别绑定词库、别名、规范化规则和版本。显示名称修改只改变展示版本；语义定义、别名映射、规范化规则改变须重新检查依赖。不得由分类、词频或相似度推导个人重要性。

字段修订不可变，缺失、候选和失效分别表示；人工修正锁定，删除关联保留抑制记录。审核者来自服务端 owner 会话，不能客户端赋值。模型自由改写初期仍待人工质量审核；人工质量工作不交给阿夜逐条承担。

## 分用途资格

独立返回 browse、search、candidateRecall、p2Projection、formalContext。草稿可获权浏览和管理搜索，候选只使用已发布且实时有效的字段。formalContext 在本增量始终关闭。原有 eligibleForRecall 只作兼容输出，不成为新的授权依据。

## 文体与个人策展

NARRATIVE 沿用用户的《记忆条目编写规则》和精修范例：高保真对话纪事、我说／她说、用户指代为她、保留日语和轮次顺序及自然前缀。括号翻译仅在能确认冗余时去除；保留含新事实、否定、条件的括号和有意义停顿。不能全局删除省略号，不生成混合语言的中文引述前缀。原始证据不改写。

标题文本不嵌入机器生成日期；时间独立保存，界面明确依据后组合。未知时间显示未核实，不补日期。

Importance、Foundational、Permanent、Personal Notes 属于未来个人策展，不自动生成，不建表，不连接心潮念。

## 验收和授权

只使用外部源码副本及合成数据库；不改 H5-B RAW 或快照。先契约测试，再存储、任务、检索和完整回归。真实模型、私人历史加工、正式迁移和上下文接入须分别批准。

## 实际实现与兼容

新增源码：`memory_v2_metadata_contract.py`、`memory_v2_metadata.py`、`memory_v2_vocabulary.py`、`memory_v2_metadata_api.py`、`memory_v2_metadata_jobs.py`、`memory_v2_metadata_search.py`；新增测试 `test_memory_v2_metadata_contract.py`、`test_memory_v2_metadata.py`。修改既有 entries、jobs、contract、recall、context、time_projection 模块，以及隔离 demo 与 ChronicleApp.vue。正式 main.py、来源适配器、M1 排序、Phase1～6.5 本轮未修改。

- 原五表和 v1 审核绑定保留。`mv3_entries` 新增 contract_version（旧记录默认 1）与 draft_version_id。v2 字段集合保存在 mv3_entry_field_sets，字段值仅保存在 mv3_entry_fields，集合用 mv3_entry_field_links 引用不变字段；mv3_entry_field_checks 保存核验绑定。来源快照仅保存身份、角色及指纹，不复制 RAW 正文。v1/v2 以条目 contract_version 分派，不能让旧整版审核自动批准新字段。
- mv3_metadata_idempotency 绑定角色、请求键、输入和条目 UUID；输入冲突拒绝。草稿可只有有序来源和标题；Synopsis/NARRATIVE 缺失返回 missing。手动 null 是禁止自动重建覆盖的抑制记录。
- mv3_vocabulary 与不可变版本表分别保存 category/tag/keyword 的语义、别名、展示指纹及规范化版本。受控分类种子 ID 根据角色、维度和固定种子位置确定；词库需显式初始化。改名不改语义 token，改定义或停用会使关联字段重新校验。别名冲突拒绝；合并只指向同角色、同种类、同维度的有效词项，保留证据和旧 ID。
- 分类维度初始值：form（日常交流、深入讨论、技术讨论、创作讨论）；dynamic（约定、分歧、矛盾、和解、相互支持、协作）；domain（生活、工作、技术、兴趣、关系、世界观）。解释性分类只提供候选和 owner 核对，不自动从语气判定。
- 安装式 `/api/memory-v2/entry-drafts` 支持创建、获权分页读取、修订、字段审核、显式发布与本地任务入队；词库管理位于该前缀下 `/vocabulary/save`。所有路由要求 owner session、写请求来源检查和 no-store；客户端不能指定审核身份、生成来源或校验状态。
- 单字段修订共用来源校验；旧字段值不受无关字段修订影响。字段引用的语义／别名 token 或实际输入字段改变时，旧值不返回。当前 v2 新草稿版本在显式发布前暂停整条候选资格，仍允许管理查看有效字段。
- `METADATA` 本地任务生成直接词项与现有 Tag 词库的原文匹配；`SYNOPSIS` 本地任务使用完整短消息摘录，不作自由概括。自动核验会重算摘录和证据，不信任传入的 rule 字符串。长消息超过梗概上限时明确拒绝。
- MockMetadataAdapter 只含预置合成输出，无 URL、Key 或 HTTP 客户端。模型字段经 mv3_jobs、mv3_model_chunks 派发状态及预算保护，永远先保持草稿。元数据真实 Provider 适配和自动自由分类／新主题发现未在本轮验证，不能视为正式模型能力。
- 元数据词项在 mv3_metadata_terms 中显式重建；读取重新验证字段 token、RAW 和角色。MetadataGenerator 仅显式传入现有 M2 协调器时运行，不改变默认生成器。多来源候选按同一 experienceKey 去重，RAW 按来源分别保留偏移；整组超预算时拒绝并提示需要 P2 粒度选择，不伪造跨来源逐字偏移。
- P2 继续只读取已发布条目；发生时间未知不按导入时间换档。raw_sources_v1 输出增加 digestInputProfile/digestToken，绑定实际输出版本及下级依赖。Synopsis/NARRATIVE profile 具备输入契约，但当前周期生成仍沿用已验证的 RAW profile。
- 合成年历 demo 显式安装新管理 API；前端仅增加待补齐草稿的只读查看与缺失梗概说明，不开放新的编辑工作台。formalContext 始终 false。真实 H5-B 数据库未创建任何新草稿。

## 测试证据与限制

外部基线和源码副本位于 `C:\Users\ASUS\AppData\Local\YOSAKI-P1A2-b414cca958864495a4c079f203d97de0`。专项日志为 final/specific.txt；前端日志为 frontend-final/build-retry.txt。最初构建失败源于隔离目录未同时映射 workspace 的根级与前端依赖；修正外部依赖路径后 vue-tsc 与 Vite 通过，未安装或升级依赖。

专项覆盖标题单独保存、旧审核兼容、字段精确失效、来源删除／修改、角色拒绝、伪造规则、API 认证和额外字段拒绝、人工锁定、词库名称与语义变化、任务幂等／暂停续跑、未知结果不重派、模型请求期间来源变化、日中双字检索、M2 多来源与去重、P2 读取，以及合成130条来源按13个区间一次覆盖。真实文件未用于测试夹具。

本阶段不修复 H3.2 的 M1 导入排序，也不写入真实13个经历。后续先单独验收 source ordinal 排序与续跑游标，再从执行前快照创建新工作副本。新条目草稿仍须经过真实来源范围核对和幂等验收。

关闭安装式 API 和显式任务可停用本增量；保留新字段、任务及核验记录，不降级改写旧审核、不删除 RAW。回滚只撤回本增量源码差异，不用整库覆盖方式回退新增数据。正式迁移仍需独立备份恢复演练。

## 停止记录：草稿列表撤权分页泄漏

**已复现**：创建两个合成条目后停用角色，请求 `GET /api/memory-v2/entry-drafts?characterId=<该角色>&limit=1`，实际返回 `200 {"items":[],"nextOffset":1,"managementOnly":true}`。逐条检查拦住了标题和正文，但分页信息在角色资格校验前计算，仍透露存在后续记录。预期应在数据库列表查询前拒绝角色，且不返回任何分页信息。

代码位置：`memory_v2_metadata_api.py` 的 `browse()`；复现：`test_revoked_role_list_rejects_without_pagination_metadata`。日志：外部目录 `permission-check/result-corrected.txt`。首次单测运行器误阻断 Windows asyncio 的本地 socketpair，未触达 API；恢复与其他隔离测试一致的仅回环许可后，得到上述真实断言失败。未放开外部网络。

依据已批准计划的权限停止条件，保留该失败测试，不在本轮继续修改业务实现。建议的定向修复：列表入口先验证角色存在且启用，再执行分页查询；同一读取事务内完成权限和来源核对。补齐禁用角色、不存在角色、跨角色、空结果及分页测试后，重新运行专项与最终完整回归。

验收分层：A2.1 契约测试通过；A2.2～A2.4 已有隔离实现但未获完整验收；A2.5 暂停；A2.6 的合成130条/13段测试通过，不代表真实 H3.2 已执行。早期源码快照完整回归 **309/309**；最终源码专项 **22/22**；随后新增安全反例 **0/1**。最终完整回归因停止门槛主动中断，不能用早期309项替代。前端类型检查及构建通过，新增界面的真实浏览器验收尚未完成。

H5-B 数据库 SHA-256 仍为 `8768A7B19CC2D0B27D9F82090DD97A9E046F6632F96FA0E4E7A52A3304651B67`；执行前快照仍为 `EE30A47AE8F5513EF3B9CDF213D29014F3DD8AFAEBD8F47F28AFC5C8243835D4`。未读取私人正文，未触碰正式库或正式密钥，未运行真实模型／向量服务。

## 定向权限修复与最终隔离验收（2026-11-08）

上述停止记录保留为修复前复现证据。`memory_v2_metadata_api.py` 的草稿列表现于同一 SQLite 读事务中先核对 owner、角色存在性及启用状态，再查询条目和计算分页。条目详情和同一安装式 Metadata API 的写入入口复用此前置角色检查；不可访问角色统一返回 `404 role_unavailable`，不返回记录数、`items` 或 `nextOffset`。详情的跨角色条目仍经条目归属检查拒绝，不返回正文。此修复未注册到正式 `main.py`。

保留原撤权失败测试，并新增停用角色有／无数据、不存在角色、错误 owner、正常空列表、正常分页、跨角色详情、第一页后撤权再访问下一页的隔离回归。标题字段经审核发布、Synopsis／NARRATIVE 缺失时，条目可浏览、可按有效标题管理检索及用于 P2 安全投影；`candidateRecall=false`、`formalContext=false`。缺失字段不会被伪造为已审核正文。

最终外部源码副本：`C:\Users\ASUS\AppData\Local\YOSAKI-P1A2-permission-3bd4e3ad3770480cb49230f26b8d9640`。专项 **26/26**；完整后端隔离回归 **320/320**，耗时 535.010 秒；Python 编译、前端 `vue-tsc -b && vite build` 均通过。首次完整回归副本漏复制 `scripts/`，导致一个测试导入错误；补齐白名单测试依赖后该测试 **3/3**，并从头重跑取得上述 320/320。未用早期 309/309 作为最终证据。浏览器实际操作、正式服务和正式数据库仍未验收；A2.6 仅合成 130 条／13 区间，真实 H3.2 未执行。
