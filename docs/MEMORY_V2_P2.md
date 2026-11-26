# Memory V2 P2：Chronicle 时间树与只读记忆投影

> 2026-10-31 隔离开发。当前代码未在 `main.py` 注册，未迁移正式数据库，未连接正式聊天上下文。P1-A 最终源码的实施前完整回归为 201/201。P2 只把**已验证来源和已审核派生**转换为不同阅读粒度，不生成候选、不写正式冷却，也不调用模型或网络。

## 原插件行为与本次适配

原插件 `src/core/time.ts:24-48` 以当前时刻减条目 `createdAt` 的实际天数换档，默认阈值 7／30／90／180／365；`periodFor` 用配置的固定时区偏移计算当地日、ISO 周、月、季度、半年、年。`src/core/digests.ts:12-48` 按父节点本次成员递归构造下级摘要，指纹纳入成员及子节点，避免跨月周把范围外经历混入月摘要。`src/core/archive.ts:128-139,376-406,642-650,791-799` 在读取时投影，缺摘要显示标题及“可展开”，后台任务显式生成 DIGEST；`unfold` 可回看 RAW，但原插件在首个展开页还会写访问计数。

P2 保留上述分级、父级范围、缺摘要退路和 RAW 回查。YOSAKI 的调整是：原始权威仍在 M1 来源，条目身份是 P1-A UUID；M3 DIGEST 必须经服务端人工审核、来源及依赖校验后才能显示；`browse`／`context_projection`／展开一律只读，不复制原插件 GET 写访问计数的副作用。原插件世界书是单独的宿主投影，P2 不实施世界书同步、检索、动态向量被动浮现或“重要记忆常驻”。

## 数据与依赖契约

- `memory_v2_time_projection.init_db(db)` **仅在隔离迁移或未来获批迁移时显式调用**，新增 `mv3_time_digest_bindings`：角色、临时节点 ID、L1～L4、期间、时间依据、输入 profile、范围指纹、M3 DIGEST 版本 ID。表不存摘要正文，也不更改 M1/M3/P1-A 权威表。旧库只有 P1-A 表而无此表时摘要结果为 `missing`；连 P1-A 表都没有的旧备份只读返回空树与 `needs_schema`。GET 不隐式建表。
- 每次读先调用 P1-A `read_entry()` 和 M1 实时来源校验；失权、源修订、未激活条目、过期 NARRATIVE 不进入节点集合。档位采用条目最早的有凭据发生时间；只有记录时间时明确标为 `recorded_at`；时间混合、缺失或不可解析时列为未定时间，不冒充 L0。条目跨多个日期时以起始时刻分组，完整日期范围仍留在 P1-A 元数据。
- 树节点是**可重建视图**。L0 为单经历详细纪事，L1 周、L2 月、L3 季、L4 半年，L5 年标题索引。L5 也可按需展开为半年、季、月、周，再回到条目 RAW；父节点递归生成仅含自身成员的下级节点。跨月同 ISO 周的两个范围有不同节点 ID。节点 ID 不能用于永久来源引用，永久引用仍是条目 UUID 和来源 ID。
- `raw_sources_v1` 范围指纹包含节点成员身份、有序 RAW 来源内容及状态指纹、下级节点范围；不包含标题、关键词、单经历梗概或 NARRATIVE 正文。当前本地摘录 DIGEST 和现有 M3.5 模型 DIGEST 均以来源正文为输入，后者只允许在已获 M3 人工审核后由可信内部流程显式绑定。源修订、成员变化、下级摘要替换使旧摘要不可用；单独标题／关键词／梗概修订不会无端使 RAW 摘录失效。未来若摘要实际读取条目梗概，须使用新的输入 profile，把该字段纳入指纹，不能伪称仍为 `raw_sources_v1`。
- 摘要选择还要求 M3 `validity()`、`human_checked`、匹配的 `rule_version`、当前来源集合与下级 DIGEST 版本依赖。未绑定的历史模型摘要或草稿不自动推断范围。`summaryStatus` 可为 `available`、`missing`、`stale_or_unreviewed`、`child_digest_missing`；`taskStatus` 独立报告当前 M3 任务的排队、失败、未知或过期。读取不补任务。
- `create_extractive_digest()` 是唯一新增的本地规则式显式产物路径；它创建现有 M3 DIGEST 草稿和范围绑定，仍须执行 M3 的人工审核／激活。`bind_reviewed_digest()` 只绑定已审核且范围匹配的现有版本，不调用模型、不扩大模型授权。

## 两个只读入口及预算

| 入口 | 输入 | 输出与拒绝 |
| --- | --- | --- |
| `browse(db, character_id, now, offset_minutes, levels, limit, cursor)` | 已认证角色、带时区的冻结时间视角、固定时区偏移、筛选和页大小 ≤50 | 动态根节点、有效标题／单经历梗概、摘要状态、任务状态、子节点及来源引用；游标绑定角色、时间视角、偏移、筛选、M1 索引代和当前成员／版本／摘要快照。变化后 `cursor_view_changed`，不返回旧标题页。 |
| `expand_node(...)` | 节点 ID、同一时间视角、摘要正文页及成员游标 | 一层子节点、经校验摘要的原样分页、成员页；摘要后续页须携带上一页 `digestVersionId`，版本变化返回 `digest_page_changed`；成员游标绑定角色、节点、时间视角、时区、索引代与元数据版本。 |
| `unfold_raw(...)` | 稳定条目 UUID、来源序号、字符偏移、后续页内容指纹 | 每页最多 4096 字符，带角色、证据类型、来源 ID、完整长度和下一页偏移；无论条目年龄多久均可主动回查。来源改变或失权时拒绝旧页。 |
| `context_projection(db, character_id, selected_entry_ids, now, offset_minutes, budget_tokens, max_blocks)` | **M2 已选择**的条目 ID，估算预算 ≤4096，块数 ≤20 | L0 选已审 NARRATIVE；L1～L4 仅在摘要范围的全部成员都已入选时用已审 DIGEST；否则退单经历梗概，再退标题；L5 标题索引。输出来源集合、粒度、时间依据、明确的估算 token 与拒绝原因。未接正式上下文，`eligibleForFormalRecall=false`。 |

`browse` 和 `context_projection` 使用同一 `_tree`、`_digest` 及 M1/M3/P1-A 资格检查。单经历梗概作为 `projectionKind=entry_synopsis`，不是时间 DIGEST，也不伪称 RAW 或逐字引述。较大正文不会被临时模型压缩；预算不足时按已审核粒度降级，连标题都放不下则跳过。Token 是 M2 的本地启发式估算，不是 Provider 计费。主动检索可在未来 M2 生成候选后使用这个投影；被动浮现、权重与实际投递仍留给 P4。P2 不自动把整树放进上下文。

**后续架构待办（P3/P4）**：当前只有某周期的全部成员都已由 M2 选中，`context_projection` 才能使用该周期 DIGEST；部分成员入选时退回单经历梗概。未来 M2 可将周期摘要本身作为明确的候选类型，使分级摘要参与相关性浮现。候选范围须由 M2 在角色权限、当前来源与摘要依赖、相关性、去重及 Token 预算检查后决定；P2 不得为使用 DIGEST 自行扩大来源集合。本次 M5-A0 只读页面不改变该规则或正式召回路径。

隔离安装式 HTTP 入口位于 `memory_v2_time_api.py`：`GET /api/memory-v2/chronicle/tree`、`/nodes/{nodeId}`、`/entries/{entryId}/raw/{ordinal}`，要求当前 owner session，统一 `Cache-Control: no-store`。内部 `context_projection` 没有正式 HTTP 入口，避免提前接聊天旁路。

## 使用合成来源实际得到的时间树

冻结时钟为 `2026-10-31T12:00:00Z`，UTC+8；六条已审合成经历分别恰好处于 0、7、30、90、180、365 天边界。显式创建、人工审核 L1～L4 的本地摘录 DIGEST 后，隔离读取得到：

```text
Chronicle
├─ L0 2026-10-31   E0    详细 NARRATIVE／RAW 可回查
├─ L1 2026-W43     E7    DIGEST available
├─ L2 2026-10      E30   DIGEST available → 本范围 L1 子节点
├─ L3 2026-Q3      E90   DIGEST available → 本范围 L2 → L1
├─ L4 2026-H1      E180  DIGEST available → 本范围 L3 → L2 → L1
└─ L5 2025         E365  标题索引 → 本范围 L4 → L3 → L2 → L1 → RAW
```

`E0`～`E365` 是示例标签；运行时身份为各自稳定 UUID。相同六条候选在 `context_projection` 下依次产生 `detailed_narrative`、四个 `period_digest` 和 `title_index`。若 E7 的 DIGEST 不存在，E7 返回已审单经历梗概；若其来源编辑，E7 连标题与旧摘要一起退出投影，直到新来源、纪事和元数据版本重新获审。L5 仍可通过 `unfold_raw` 按页读回原始消息，没有年份检索屏障。

另一项隔离用例固定**同一个 E0 与同一来源 ID**，将观察时刻逐次推进 0／7／30／90／180／365 天；在明确创建并审核相应档位的摘要后，实际输出依次是 `NARRATIVE → L1 DIGEST → L2 DIGEST → L3 DIGEST → L4 DIGEST → 标题索引`。六次 `sourceRefs` 一致，六次主动 RAW 回查得到同一原文。若同期间还有未被 M2 选择的其他经历，P2 不会为了使用整组摘要把它们带入上下文，而会退到该条目的梗概。

## 隔离验收与剩余边界

专项 23/23 通过，覆盖精确六档边界、自动换档、跨午夜和 ISO 周跨月范围、有效／缺失／草稿／失效摘要、下级依赖、来源修订后的重新审核、标题与梗概选择性失效、RAW 全文分页、预算与已选候选范围、角色隔离、拆合后去重、根与成员游标失效、旧库缺表和 GET 零写入。最终完整隔离回归 224/224 通过；未运行正式服务、真实模型、浏览器 UI 或正式数据库迁移。

本阶段不提供向量检索、正式浮现、时间树 UI、自动生成任务、真实 Provider、动态时区夏令时处理或正式聊天接入。时区遵循原插件的**固定分钟偏移**；日后若支持 IANA 时区，需要另行定义跨夏令时的日期归属。M5-A 只读 UI 最小需要树根分页、节点／成员逐层展开、条目详情与四视图入口、RAW 来源分页、摘要／任务／失效状态；四视图仍沿用 M3 已审派生的权限与来源校验。

M5-A 只读接入的最小后端清单：本阶段的 `GET /api/memory-v2/chronicle/tree`、`GET /api/memory-v2/chronicle/nodes/{nodeId}`、`GET /api/memory-v2/chronicle/entries/{entryId}/raw/{ordinal}`；既有 `GET /api/memory-v2/entries/{entryId}`、`GET /api/memory-v2/derivations/{derivationId}/versions` 与版本来源映射、`GET /api/memory-v2/search` 和 `/status`。接入时必须复核各入口的 owner session、当前角色、来源失效和 `no-store`，并单独验收长时间树性能及旧备份恢复。P2 的 `context_projection` 仍是内部隔离契约，不属于 M5-A 页面请求接口。
