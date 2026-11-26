# Memory V2 M3.6 — Responses 隔离适配与零付费验收

状态：**零付费隔离工程验收完成；合成 N1 的两次真实生成已完成，质量待人工评审；正式部署未获批准。** M3.5 的状态为**隔离工程验收完成，正式模型能力待验证**。零付费阶段结果见[验收记录](MEMORY_V2_M36_ZERO_PAY_ACCEPTANCE.md)，N1 实际用量与输出见[首轮记录](MEMORY_V2_M36_FIRST_TRIAL.md)。N1 之后没有处理私人聊天或额外付费样本。

## 官方能力与实际边界

- 当前候选 `gpt-6.1-sol` 的官方模型页列出 Responses 和 Structured Outputs 支持。确切模型、定价、模式及账号资格须在付费试运行前重新确认：[模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)。
- Responses 支持 `store=false`、`background=false`、`truncation=disabled`、`text.format` 的严格 JSON Schema；返回状态与 `usage` 需分别验证：[创建响应](https://developers.openai.com/api/reference/resources/responses/methods/create)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)。结构化输出只约束形状，不证明叙事忠实。
- `/responses/input_tokens` 按请求输入形状计算输入 token，包括格式开销；输出 `usage.output_tokens` 可包含不可见 token，不能以正文长度计算费用：[Token 计数](https://developers.openai.com/api/docs/guides/token-counting)。实际发票仍须用当时[价格表](https://developers.openai.com/api/docs/pricing)核对。
- 本实现不引入 Python OpenAI SDK，使用固定 `https://api.openai.com/v1` 的 HTTPX，真实传输显式 `HTTPTransport(retries=0)`；零付费测试只接受 `MockTransport`。模块导入不联网、不读环境凭据；正式 `main.py` 没有注册入口或任务调度。

## 请求与状态契约

生成请求显式 `service_tier=default`、`reasoning.mode=standard`、`reasoning.effort=medium`、`store=false`、`background=false`、`stream=false`、`truncation=disabled`，不包含 `tools`、`conversation` 或 `previous_response_id`；返回的模型或服务层级不符即停止后续派发。M3.5 变体用受支持的严格 JSON Schema 返回有序 `items`；原插件风格变体为了保留原来的自由文本行为使用 text 输出，**仅供评测**。这是两种输出包装的已知差异，不能称为完全未修改的原插件提示词。原插件的 speaker 判归属、分批、第一人称与日语约束保留；内嵌示例换成合成示例，不额外生成标题或全文润色。

`mv3_trial_budgets` 是试运行的串行预算账本；现有 `mv3_model_chunks` 只增加 trial ID、提示词变体、Provider 响应 ID、Provider 用量、输入预检 token、美元预留及基于用量的美元估算字段。已有模拟 token/费用单位不与美元混用。按现有 `mv3_jobs` 创建任务，并通过原始来源 ID、角色及指纹重新校验。试运行的全部批次须先通过输入计数并在单一事务中完成预算预留，任何一项失败都不派发生成。每次生成前再次核对当前来源、全局预算及其他批次状态，并在事务提交 `possibly_dispatched` 后才发送。

`completed` 且用量齐全时才结算；拒绝、失败、取消、不完整或忠实性校验失败会记录实际返回的用量估算并停止。连接超时、无法确定结果或用量缺失标为 `unknown`，保留预留并停止；不自动重试。来源在请求中改变时保存费用证据但不创建版本。原插件评测输出仅留在隔离任务记录；M3.5 变体只创建 `needs_review`、`system_derived_text` 草稿，不能无审核激活。模型版本幂等键同时纳入输出指纹，避免相同来源但不同模型正文被折叠成旧版本。

## 合成金标准与拟议试运行

`tests/m36_gold.py` 固定 6 组 NARRATIVE、2 组 DIGEST，其中 N5 依原 6000 字批次目标分成两批。合计每种提示词 9 批、最多 **18 次生成请求**。样本覆盖中日文、归属、否定、条件、更正、括号新事实、阿夜原句中的感受、跨批连贯性、冗余及结果未知。`scripts/m36_offline_trial.py` 只核对这份清单，输出 `providerCallsMade=0`，无网络和密钥读取路径。

未来试运行的**拟议**止损目标为 5 美元。逐批采用官方输入计数、实际 `usage` 与价格快照；按未缓存输入和最大输出加 20% 预留。不可见输出 token 包含在最大输出和 Provider 用量内。价格、具体模型、测试凭据、合成样本及 5 美元预算仍需一次单独批准。若全套预留超过预算，整套停止而不临时删减样本；正式派发串行执行。Provider 的结算可能滞后，本地预算是停止门槛，不保证账单硬封顶。

人工评审逐例使用 [评审模板](MEMORY_V2_M36_REVIEW_TEMPLATE.md)，把详细对话纪事的轮次、表达、人称、日语、括号、条件、否定、更正、来源映射及自然度，与梗概／时间摘要的忠实压缩分开。详细 NARRATIVE 不以正文压缩率为主要门槛；模型自评无审核效力。重复召回的节省只能按 1／3／10 次做假设计算；当前没有 M3 正式上下文切换。

## 尚未授权及回退

N1 的两次真实生成不能替代全部样本和人工质量验收，费用估算也不是实际账单。没有正式数据库迁移或私人聊天处理。后续收费试运行仍须单独批准模型、凭据范围、样本和预算。关闭未注册的试验入口即可回到 M3.5；保留请求与未知账目，不删除原始来源、已审核版本或重新派发未知请求。M4 经验关联桥接独立于真实模型上线，仍须自己的计划及实施授权。

首轮仅用 N1 的两次真实生成、独立 $0.10 上限与实际结果见[执行清单与结果](MEMORY_V2_M36_FIRST_TRIAL.md)。此后 NARRATIVE 提示词修订为 `ssly-dialogue-chronicle-m35-v3`，派生发言归属统一为“我说／她说”。历史 N1 使用的 v1 提示词输出保持原记录，不追溯改写；v3 尚未通过真实模型质量验收。
