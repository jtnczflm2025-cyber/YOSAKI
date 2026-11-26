# 欲望系统 Phase 6：有界模拟心理状态

本层是按角色隔离的**计算性模拟**，不是健康测量或真实主观体验。它只向 Phase 2 意愿判断和 Phase 3 自由活动提供参考，不启动活动、调用工具、修改角色提示词、驱力 baseline 或 Phase 5 正式学习结果。角色仍可不行动、推迟或改变方向。

## 权威来源、顺序与迁移

- Phase 3 `activity_operations` 的 `succeeded`／`failed` 结果是模拟活动成本来源；`running`、`unknown`、未执行计划和重复操作不计成本。Phase 4 `feedback_activities_v4.status=applied` 是模拟体验来源；反馈驱力变化仍由 Phase 4 原有确认事务写入。Phase 5 已批准偏好和审计只登记为来源，不自动转成长期心理因果规则。
- `backend/app/psychology.py` 的单一入口按“验证角色及事件 ID → 读取旧快照并按时间衰减 → 保存结构化情境评价 → 计算情绪与身体变化 → 两层有界关系传播 → 整理冲突 → 同 SQLite 事务保存事件前后快照、规则版本和来源”执行。单角色事件 ID 唯一；重复结果返回未应用。后续事件的时间不能早于已保存快照时间。
- `psychological_snapshots`、`psychological_events`、`psychological_cutovers`、`psychological_conflict_decisions`、`second_order_desires`／历史、`psychological_edges`／控制和 `psychological_appraisal_runs` 由兼容迁移创建，角色删除依外键清理。复制角色不会复制经历。首次访问建立切入时间，不根据更早的反馈猜测心理历史。加密 `.ybackup` 包含这些非密钥表；恢复前停止本进程的评价任务。运行中的评价在重启后标为失败，不自动重放可能计费的请求或外部操作。
- `/psychology/replay` 检验事件保存的前后快照链并重建最后快照，不重新调用模型或执行工具。旧规则版本的事件仍保留当时计算的评价与结果。它是快照链一致性检查，不是跨版本重新推导历史关系的证明。

## 规则与参数 `psychology-v1`

情绪默认值：`valence=0`（范围 -1～1）、`arousal=.25`、`tension=.2`、`senseOfControl=.5`。虚拟身体默认值：`energy=.7`、`fatigue=.2`、`cognitiveLoad=.15`、`socialSaturation=.1`、`recoveryNeed=.2`，范围 0～1。时间衰减使用 `default + (old-default) × 2^(-hours/halfLife)`，单次离线上限 72 小时；各维度半衰期在模块常量 `HALF_LIFE_HOURS` 中分别配置。无事件只向参考值回归，不产生新情绪。情绪假设允许并存，强度与解释置信度分别保存；强度同样随时间衰减。

实际研究操作的单步参考成本为能量 `-.055`、疲劳 `+.04`、认知负荷 `+.06`；探索、创作、社交及真正执行的休息操作各有独立成本表。已知失败操作按该成本的 50% 入账，未知操作为 0；这不是现实消耗。体验评价仅在 Phase 4 反馈确认后入账，单次 valence、tension、arousal 变化分别限于 ±.09、±.07、±.05。缺失乐趣和预期不会被填成负面体验。预期差优先由已保存的实际评分减活动前预估确定，模型输出不得与之矛盾。

自由活动请求可设 `psychology_appraisal_calls` 为 0／1，`psychology_appraisal_tokens` 为 128～1200。界面默认明确选择一次、600 输出 token；旧 API 请求省略字段时为 0 次，避免暗中增加模型调用，独立于活动判断和 Phase 4 回顾预算。会话结束、Phase 4 回顾停止后，后台对本次有已知结果的活动批量调用所选模型；无活动不调用。结构化输出只可提供有证据的评价维度及置信度，不能改客观操作结果。超时、无效输出或预算不足时保留实际事件，使用无模型规则；用量不可靠时按预留额记录。没有配置美元价格，因此不估算费用。一次模型调用的上下文至多 20 个活动和约 24,000 字符，输出按预留 token 封顶。读取心理参考本身不调用模型。

关系采用有向、版本化的内置边和审阅后启用的新边。当前可计算的边只接受驱力、情绪和模拟身体节点；目标及二阶欲望的关系留待具备明确语义的后续版本，避免保存无效边。关联学习证据时，该证据须已进入正式获批的偏好判断。每轮从同一旧快照计算，路径不重复节点，最多两层、每层衰减 50%，单边 ±.04、目标合计 ±.08；指向驱力的短期提示限制在 ±.1，绝不写入 `drive_states`。条件和延迟边在本版不可创建，避免保存不能执行的规则。用户可停用或重新启用已批准的边；单次相关性不会自动变为因果。隔离模拟调用相同事件更新器处理最多 500 个事件，并在内存数据库中运行，不改正式角色。

## 二阶欲望、冲突与接口

Phase 2 观察原有 `metaDesires` 可映射为二阶倾向；观察与意愿输出还可包含经 schema 和角色目标 ID 校验的 `reflections`。同一目标的认同和抵触都可保存，不覆盖一阶驱力；仅显式归档。单次模型表达的强度和置信度分别限制在 .6 和 .65。二阶倾向不能批准 Phase 5 长期结论。冲突整理仅在对同一目标存在相反反思，或高模拟疲劳与高探索倾向确实可能竞争活动时间时建立记录；选项交给意愿系统，决策和后续操作结果另行追踪。

新增同角色 API：`GET /psychology`、`/psychology/events`、`/psychology/context`、`/psychology/conflicts/history`、`/psychology/edges`、`/psychology/appraisals`、`/psychology/replay`；`POST /psychology/tick`、`/psychology/second-order/{id}/transition`、`/psychology/edges`、`/psychology/edges/{id}/decision` 和 `/psychology/simulate`。正式写请求带 `schemaVersion=6`、`requestId` 和相关的期望版本；冲突返回 409。心理关联图操作需要用户明确提出和批准，不能借此绕过工具确认。全页 `/psychology` 与浮窗 `/hub?window=psychology` 共用 `PsychologyPanel.vue`。

Phase 7 可读取 `/psychology/context` 中有界的结构化参考与 `/psychology/events` 的来源、规则版本；不得把模拟自述当作事实，也不得直接用心理数值启动活动。

## 验收边界

隔离测试使用临时 SQLite 和本地模拟模型，覆盖状态去重、二阶矛盾、审批、版本冲突、模型失败与合法结构化评价、备份恢复、角色复制／删除及 100／500 次混合事件。后端完整回归、Python 编译、前端构建和浏览器交互结果应按每次实际运行记录。真实第三方模型、MCP、设备和 VPS 仍须单独验收。



## 套餐模型通道实测

套餐模型档案与 API Key 档案共用上述本地预算，但套餐通道要求 `store=false`、`stream=true`。欲望模块等待流式终止事件后才接受结构化文本；缺失终止事件或 schema 校验失败不写入决定。2026-10-27 使用本机已授权账户在隔离角色上实测了一次 Phase 6 评价，状态为 completed；这不证明其他账户、模型或第三方 MCP 可用。

## Phase 6.5 规则补充

新事件采用 `psychology-v2`：成功或失败的操作只证明执行结果，没有独立目标证据时 `perceivedProgress=null`。原 `psychology-v1` 事件的保存值与规则版本不修改。局部更新、衰减和关系传播现可独立于 SQLite 调用；数据库仍在同一事务内写入事件和快照。审批时最多启用 40 条边，超出返回冲突，不静默丢弃边。行为观察协议、隔离副本、来源去重和只读报告见 [Phase 6.5](BEHAVIOR_OBSERVATION.md)。
