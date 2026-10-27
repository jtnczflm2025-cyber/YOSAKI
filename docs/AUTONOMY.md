# 欲望系统 Phase 3：自主活动与连续经历

Phase 3 在现有驱力和 Phase 2 意愿之上增加**手动启动**的自由活动。角色可以探索本地环境、使用本次授权的 MCP 工具，也可以推迟、拒绝、休息或提前结束。驱力和未决意愿只是参考；时间用尽、会话结束和长期目标完成是不同的状态。公开的第一人称备注是模拟描述，不是意识测量或模型隐藏推理。

## 使用方式与边界

在「欲望与意愿」启用当前角色后，选择已启用的模型、时长、模型调用上限、环境及工具动作上限、累计输出 token 上限，以及本次允许的 MCP 工具，再按「开始自由活动」。默认不选任何 MCP 工具。活动由后端运行；关闭浏览器页面不会停止存活的后端任务。没有定时唤醒。重启后活动暂停，用户必须手动恢复；暂停不延长原截止时间。

当前后台任务在运行该会话的 Uvicorn 进程内执行，部署时应使用单个后端 worker。SQLite 租约保护会话写入，但多进程调度、进程迁移和故障接管尚未验收。

首版环境 `local-studio-v1` 是每角色隔离的本地探索室，含地图页、旅行笔记和线索板。对象状态及发现保存到 SQLite，下一次唤醒可重新看到。场景文本是示例模拟资料，不代表现实世界事实。`EnvironmentAdapter` 协议定义 `snapshot`、`available_actions` 和 `execute`；动作缺省要求逐次确认，本地场景已声明无外部副作用的动作才可在本次活动内执行。新增适配器需要在后端注册，并明确声明能力和动作授权策略。

自由活动的 MCP 请求仍检查角色、会话工具清单、服务器与工具启用状态、配置指纹和 JSON Schema。用户已核实并标记的只读工具才可能免逐次确认；带路径参数的调用仍需逐次确认，路径中的上级目录跳转被拒绝。其余工具每次展示具体参数等待批准。第三方 MCP 的实际副作用无法由 YOSAKI 自动证明。模型不能扩张授权，也不能把请求当作执行成功。

## API 与状态

路径以 `/api/desires/{character_id}` 为前缀，所有业务请求沿用 owner session。

| 接口 | 说明 |
|---|---|
| `POST /awakening` | `mode="free"` 启动后端活动；省略 `mode` 时保持旧单步唤醒。自由模式须提供唯一 `request_id`，以及 `model_profile_id`、`environment_id`、`duration_minutes`、`max_model_calls`、`max_tool_calls`、`max_output_tokens`、`allowed_tool_ids`。同一请求 ID 与配置重复提交返回原会话。 |
| `GET /awakening`、`GET /awakening/{session_id}` | 查看本角色会话；详情包含当前计划、版本、预算和结束原因。旧单步接口继续可用。 |
| `POST /awakening/{session_id}/pause|resume|cancel|stop` | 自由模式控制须提交 `schemaVersion: 3`、`requestId`、`expectedRevision`；版本竞争返回 409。`stop` 是正常提前结束，`cancel` 是取消。 |
| `GET /environments`、`GET /environments/{environment_id}` | 查看已注册环境及当前持久化状态；不调用模型。 |
| `GET /awakening/{session_id}/timeline|checkpoints|operations` | 查看事实、模拟备注、计划、未知结果、检查点和操作记录。 |
| `POST /awakening/{session_id}/operations/{operation_id}/decision` | 对待确认的单次操作批准或拒绝；额外提交 `approve`。配置变化后原批准失效。 |
| `GET /activity-feedback` | 查看待 Phase 4 消费的幂等反馈事件；不会自动更新驱力。 |

状态按 `awakening → observing → deliberating → planning → acting → reviewing` 逐步推进；复查后可再次判断。工具待确认时为 `awaiting_confirmation`。`paused` 可以恢复；`completed`、`cancelled`、`failed` 为终止状态。每一步最多派发一次模型或操作请求。角色可以返回 `no_action`、`undecided`、`rest` 或 `finish`，直接正常结束。模型失败或输出无效时暂停并保留已保存状态，不切换模型。

资源由真实截止时间、模型次数、环境及工具动作次数、累计输出 token 预留额共同限制。每次模型请求先预留本次最大输出额，供应商提供可信输出 token 用量时才释放未使用部分。输入 token 和美元费用没有统一可靠定价，因此不显示未经证实的费用限制。预算耗尽时用结构化日志结束，不再为总结额外调用模型。

## 数据、恢复与连续性

兼容迁移给 `awakening_sessions` 增加模式、请求 ID、环境、token 预算、revision、结束原因和租约等字段。新表保存环境状态与发现、活动计划与步骤、操作、检查点、时间线、待反馈事件和控制请求去重。旧引导式会话及 `/goals` 保持兼容。角色删除通过外键清理这些记录；复制角色不复制经历。加密 `.ybackup` 包含新 SQLite 表，仍按既有流程剔除 session 与服务密钥。

启动或恢复数据库时，自由活动中的会话转为暂停；执行中但没有持久化结果的操作转为 `unknown`，不会自动重放。用户可查看当前环境与日志后恢复，但未知操作不被写为成功、失败或反馈事件。正常返回的环境及 MCP 结果分别记为事实来源；模型自述单独标为模拟反思。计划与发现可跨唤醒引用，未完成兴趣不会因时间经过自动增压、失败或完成。[Phase 4](FEEDBACK.md) 读取这些记录并在用户审阅后更新短期驱力；Phase 3 本身不另建反馈算法。

## 验证边界

2026-10-24 隔离后端验收 16 项通过，使用临时数据库、模拟模型和本地 MCP，覆盖环境探索、跨唤醒、重复请求、预算、逐次确认、重启未知结果、备份和角色隔离。前端构建、Python 编译及隔离浏览器交互检查通过；浏览器检查从自由活动页面启动模拟模型，完成环境探索，并在刷新后看到活动历史和发现。真实模型、多种 MCP、设备和 VPS 部署仍需单独验收。
