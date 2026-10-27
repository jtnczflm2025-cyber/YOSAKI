# 欲望系统 Phase 4：体验反馈与预期差

Phase 4 只接 Phase 3 的 `free` 自由活动。旧单步唤醒、聊天和 `/api/desires/{角色}/feedback` 保持原有手动路径。反馈是角色的**模拟体验证据**，不测量真实感受或模型私人推理；数值变化不自动触发意愿判断或新活动。

## 数据流

1. 用户手动启动自由活动，可预留最多一次独立的体验回顾模型调用（前端默认 1 次、600 输出 token；旧 API 请求省略字段时为 0，保持兼容）。角色在提出环境或工具动作时可以给出可空的 `expectedEnjoyment`，范围 0～1，操作创建前固定；旧操作保持 `null`。
2. 后端把同一会话的操作按计划归为活动。只有真实返回的操作结果是客观证据；操作成功不等于长期目标完成。`objective_outcome`、会话 `end_reason` 和每项操作结果分开保存。全部操作结果未知时反馈保持 `awaiting_evidence`，不能应用；用户可显式记录独立核实结果，来源标为 `user_attestation`，不冒充工具返回。
3. 会话结束后，后端最多发起一次结构化模拟体验回顾。结果可包含空值。0～1 乐趣评分显示为 0～100；`expectation_gap = enjoyment - expected_enjoyment`，缺任一项则为空。结果超出预期至少 0.15 且事后乐趣至少 0.65 时，可在用户确认后保存「曾低估」兴趣证据。差值不直接成为驱力增量。
4. 确定性调节器计算待审阅变化。用户选择应用或跳过；应用、驱力事件、调节审计、短期冷却和兴趣证据在同一 SQLite 事务提交。长期 baseline 和长期目标不在此处修改。新兴趣留作 Phase 5 证据与下一次意愿判断参考。

## 调节规则

只对回顾中有来源说明的 `affectedDrives` 计算。若无已返回的操作结果、无评价、无相关驱力或可信度为空，则变化为零。关联度 `r`、可信度 `c` 均为 0～1；两小时内同名活动已经应用的次数对应重复系数：首次 1、第二次 0.5、其后 0.25。

```text
strength = r × c × repeat_factor
raw_satisfaction = 0.12 × strength × (2 × fulfillment - 1)  # fulfillment 为空则为 0
raw_activation = strength × (0.12 × novelty + 0.04 × frustration - 0.10 × fulfillment)
raw_activation[rest] += 0.08 × strength × fatigue
raw_inhibition = 0.06 × strength × fulfillment
```

空值在各自项按 0 处理；满意度为空不会凭空减少满意度。每项原始变化限于 ±0.12；正变化乘以 `1-current`，负变化乘以 `current`，结果限制在 0～1；小于 0.002 的变化忽略。满足、激活与短期抑制分开变化，因此「获得满足但发现新问题」「失败但觉得有趣」都可成立。调节器是纯函数，模拟接口只操作内存副本。冷却仅作为下一次规划的建议，不阻止活动，届满也不激活驱力。

## 接口与迁移

`POST /api/desires/{character_id}/awakening` 的 `free` 模式增加 `feedback_review_calls`（0 或 1）和 `feedback_review_tokens`（128～1000）。同一角色下新增：

| 接口 | 作用 |
|---|---|
| `GET /feedback-v4`、`GET /feedback-v4/{id}` | 待审阅与历史，包含客观证据、预期、回顾、变化提案和状态。 |
| `GET /feedback-v4/{id}/reviews` | 回顾尝试、失败和实际／预留输出 token 审计。 |
| `POST /feedback-v4/{id}/review` | 用户明确要求重新评价，需请求 ID、版本和本次 token 上限。 |
| `POST /feedback-v4/{id}/resolve` | 对全部结果未知的活动记录人工核实结果及依据，不重放原工具。 |
| `POST /feedback-v4/{id}/decision` | 应用或跳过，需请求 ID 和版本；冲突返回 409。 |
| `GET /feedback-v4/timeline/events`、`GET /feedback-v4/interests/candidates`、`GET /feedback-v4/goals/events` | 已应用的驱力变化、候选兴趣和意愿重评估建议审计。 |
| `POST /feedback-v4/simulate` | 最多 500 个事件的隔离模拟，不保存结果。 |

迁移为旧 `awakening_sessions` 和 `activity_operations` 补默认值／可空预估字段，新增 `feedback_activities_v4`、`experience_reviews_v4`、`drive_regulation_v4`、`activity_cooldowns_v4`、`interest_evidence_v4`、`goal_feedback_v4`、`feedback_controls_v4` 等角色隔离表。既有 `pending_activity_feedback` 和旧 `/feedback` 保留。新表纳入加密 SQLite 快照；角色删除按外键清理，复制不继承经历。反馈按计划 ID 和请求 ID 去重。模型请求中断后标为评价失败，不自动重放可能已计费的调用。备份恢复前取消本进程中的反馈任务，替换数据库后重新允许扫描。

## 验证与限制

2026-10-26 隔离后端回归 20 项通过；Phase 4 定向检查覆盖正负预期差、空评价、失败与未知结果、确认前状态不变、重复提交、驱力版本冲突、事务回滚、角色隔离、备份恢复及 100／500 次固定序列。模拟检查全体驱力不集体贴近 0 或 1，也未出现持续大幅振荡。Python 编译、前端构建与隔离浏览器闭环通过；浏览器见到 `70→90 / +20`、确认后短期驱力变化和刷新后持久化。真实模型、第三方 MCP、设备和部署仍需分别验收。后台反馈任务和自由活动一样，当前部署使用单个 Uvicorn worker；多进程接管未验收。
