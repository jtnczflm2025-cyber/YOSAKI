# V3 原生欲望系统

持续意愿、模拟自我观察和未决意愿池见 [欲望系统 Phase 2](VOLITION.md)；后端自由活动与环境适配见 [Phase 3](AUTONOMY.md)；自由活动的体验反馈与预期差见 [Phase 4](FEEDBACK.md)。

欲望系统是**模拟的角色内部状态**，不代表真实生理需求或模型的私人推理。每个角色独立启用，初始关闭；角色复制不继承状态、事件或活动。角色删除会清理相应记录。普通聊天只在话题相关时读取简短状态参考，不会自行修改驱力。

## 数据与计算

- 后端模块 `backend/app/desires.py` 管理 8 个领域的 12 项驱力。SQLite 保存 baseline、activation、satisfaction、inhibition、最近满足时间、更新时间和版本。
- 正式事件需唯一 `event_id`，重复提交不会重复入账。激活受 baseline 调节；近期正向满足会短暂降低再次激活的增量。时间衰减按真实经过时间计算，单次最多补算 72 小时。低满足度不直接产生惩罚或激活。
- `POST /api/desires/{character_id}/simulate` 使用隔离副本计算，不写正式状态。快照可供明确的人工恢复；恢复操作留事件记录。
- 正向反馈累积到三次时可生成小幅 baseline 变更建议。建议保留最近证据事件 ID，需用户批准；原值和新值可追溯，后续基线未变化时可以撤销。同一活动只能记录一次反馈。

## 手动唤醒

用户选择模型、时长、模型调用上限、工具调用上限及允许的 MCP 工具，再启动会话。每次点击“进行一次意愿判断”最多调用模型一次；模型必须返回结构化公开选择。休息或不行动会结束会话。没有可用模型时不会用演示回复假装完成意愿判断，也不会自动切换到付费 API Key。

只读标记由用户确认。启动时服务端记录所选工具和配置指纹；工具 schema、服务配置或启用状态变化后，原预授权失效。未标为只读的工具即使已被选中，仍需对具体参数逐次批准。所有调用再次经过现有 MCP JSON Schema 校验。第三方工具的副作用无法由名称或描述证明，用户不得把写入、发送、删除、付费等工具标记为只读。普通聊天的工具确认链不变。

会话提供暂停、恢复、取消及超时；进程重启时处理中会话转为暂停，不自行继续。工具结果与失败留在活动记录中。反馈由用户明确提交，可改变短期驱力，但不会直接覆盖人格基线。

## API 概览

| 分组 | 接口 |
|---|---|
| 状态 | `GET /api/desires/{id}`、`PUT /enabled`、`POST /events`、`POST /feedback`、`POST /simulate` |
| 追溯 | `GET /events`、`GET /snapshots`、`POST /snapshots`、`POST /snapshots/{snapshot_id}/restore` |
| 偏好 | `GET /preferences`、`POST /preferences/{change_id}/decision`、`POST /preferences/{change_id}/rollback` |
| 目标 | `GET /goals`，查看选择、推迟、拒绝和已完成的目标 |
| 唤醒 | `GET/POST /awakening`、`GET /awakening/{session_id}`、`POST /step`、`POST /pause`、`POST /resume`、`POST /cancel`、`POST /tools/decision` |
| 工具 | `GET /tools`、`PUT /tools/{tool_id}/policy` |

所有业务接口使用现有 owner session。备份仍为加密 SQLite 快照，并剔除既有的 session 与服务密钥。隔离测试先设置 `YOSAKI_DATA_DIR`，不得在真实数据库上做恢复试验。
