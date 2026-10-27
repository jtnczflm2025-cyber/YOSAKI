# 欲望系统 Phase 2：内部观察与未决意愿池

本阶段在 V3 驱力系统上追加内部判断。数值、第一人称描述和目标均为**模拟状态**，不代表模型具有真实主观体验。角色须先单独启用欲望系统。新接口不会调用 MCP 工具，也不会自动执行活动；原有手动唤醒保持独立。

## 数据与状态

- `desire_advisories` 保存参谋报告及其源驱力快照。趋势比较两个带真实时间戳的报告；间隔不足五分钟时标为未知，变化小于 0.05 标为稳定。近期事件 ID 提供证据。规则检测先标为“潜在张力”，不把数值同时偏高当作确定冲突。
- `introspection_records` 保存三种手动观察模式的公开第一人称模拟描述、二阶意愿与未解决问题。空描述有效。观察本身不修改驱力，不形成执行请求。
- `volition_items` 保存跨唤醒的意愿。`stage` 为 `candidate`（候选兴趣）、`pending`（决定后推迟）、`committed`（明确长期承诺）；`status` 为 `active`、`archived`、`rejected` 或 `completed`。时间流逝不改变意愿的阶段、紧迫度或状态。
- `volition_decisions` 保存纯判断，包括创建、选择、维持、推迟、修改、拒绝、不行动及尚未决定。`volition_item_history` 保存每次重要状态变更的前后值。用户可归档、放弃、重新考虑和标记完成；重新考虑会回到候选兴趣。

旧 `volition_goals` 表和 `/api/desires/{id}/goals` 响应保留。首次读取新意愿时，旧记录按旧 ID 幂等映射到新表；旧 `chosen` 不被推定为长期承诺。旧唤醒的新判断继续写兼容历史，并链接至持续意愿。角色删除通过外键清理新记录，复制角色不复制记录。加密 `.ybackup` 包含这些 SQLite 表；恢复旧备份后 `init_db()` 会建立缺失表。

## API

所有路径均以 `/api/desires/{character_id}` 为前缀，沿用 owner session。模型请求必须指定已启用的模型档案，不自动切换模型。写请求使用 `schemaVersion: 2` 与唯一 `requestId`；同一请求重复提交返回已有结果。状态变更须提供 `expectedVersion`，旧版本返回 409。

| 接口 | 作用 |
|---|---|
| `GET /advisory`、`GET /advisories`、`GET /advisories/{advisory_id}/source` | 当前、近期报告及其源驱力快照；不调用模型 |
| `POST /observations`、`GET /observations` | 手动观察与历史；输入含 `modelProfileId`、`mode`、可选目标及记忆开关 |
| `POST /decisions`、`GET /decisions` | 纯意愿判断与历史；输入可指定目标、观察记录、记忆及当前情境 |
| `GET /intentions`、`GET /intentions/{item_id}/history` | 持续意愿与单项变更历史 |
| `POST /intentions/{item_id}/transition` | 用户显式 `archive`、`abandon`、`reconsider`、`complete` |

模型输出必须为 `schemaVersion: 2` 的结构化 JSON。观察结果包含 `selfDescription`、`observedDriveIds`、`metaDesires`、`unresolvedQuestions`。判断结果包含 `type`、可选 `targetItemId` 或 `proposedItem`、`reasonSummary`、`selfNote`。服务端校验目标归属、驱力 ID、内容长度、状态转换与版本；模型失败或格式无效时不保存意愿改变。模型请求没有工具列表，也不调用工具执行端点。

## 手动唤醒衔接与限制

手动唤醒会读取最多 20 条活动意愿，先列长期目标，再列未决意愿与候选兴趣。模型可在旧决策格式中附上 `targetItemId`，服务端核对角色归属后更新对应意愿；没有 ID 的旧输出仍按目标名称兼容映射。模型可以忽略、维持、推迟或改变这些意愿；未决项不是待办事项。旧唤醒仍沿用其已有的预算和逐次工具确认链。真实第三方模型、MCP、设备与生产部署验证须分别进行，不能由隔离模拟测试推断通过。
