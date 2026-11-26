# 第2步限定收口：AI质量确认与OAuth边界核对

日期：2026-11-18（香港）。本轮新增Provider请求0，不读真实凭据、不创建真实收据、不重新规划或TTS，不修改生产业务代码、Profile、日常服务或Memory。

## 规划结果质量

Owner已审阅消息10的真实标注，原话：“本条 AI 标注经审阅，语义安排合理，可记录为单样本建议贴切；不扩大为跨文本稳定性或新音频听感通过。”因此本条单样本语义建议限定通过。

第一段thoughtful、第二段restrained，可信映射均是thoughtful标签，原正文154字、仅预览180字；没有生成这份AI预览的新音频。既有35字普通版/56字两位置版的声学认可属于另一组冻结文本，不能拼成同一条“AI规划→TTS→听评”的端到端结论，也不证明跨文本稳定性、标签精确结束/重置或日常已启用。

真实结果、1099输入/104输出/1203总token、原耗时口径、已用收据、既有音频和实际费用未知全部保留。新确认独立写入运行目录quality-confirmation.json，不改原result.json或最初pending审计。

## 账户续期核对结论

“账户续期1次”确属本次关联流程，不是旧历史累计误入。但它属于**后来追加批准的独立账户步骤**，不是原模型执行阶段自行刷新。原manifest的OAuth=0保持原字节，额外1次与其差额明确单列。

授权顺序可以由本对话与独立记录核对：

1. 原单冻结OAuth=0。20:10及20:37预检发现access token到期，模型未派发，收据未创建。
2. 助手明确询问“是否允许追加最多1次同账户OAuth续期？成功后重核原冻结条件并继续原先已批准的一次规划，不查目录、不生成音频。”
3. 用户随后回复“允许”。这是追加1次账户请求的依据，不是事后从原OAuth0清单推导许可。
4. 20:45:22.232～20:45:23.891（香港）独立续期包装调用access_token(allow_refresh=True)，记录成功，1.656秒，同账户身份一致，发布新有效期。原工具调用记录及oauth-refresh-once.json、account-request-authorization.json均保留；没有目录GET。
5. 20:46:12再次只读预检通过，才创建模型收据。随后冻结CLI模型入口执行一次；它自身始终以allow_refresh=False取现有token。

本地保留的是一次续期函数执行及成功发布凭据的脱敏记录，没有保留原始OAuth帧或Provider请求ID；本轮没有额外联网查账或补抓认证数据。

修订后的分账是：

| 范围 | OAuth刷新 | 模型生成 | 目录GET | TTS/Scribe/语音WS |
|---|---:|---:|---:|---:|
| 原冻结模型执行阶段 | 0 | 1 | 0 | 0 |
| 后续单独授权的账户续期阶段 | 1 | 0 | 0 | 0 |
| 关联流程合计 | 1 | 1 | 0 | 0 |

因此不应把总续期次数改成0，也不应把追加授权续期说成未授权偏差或allow_refresh=False失效。前次合并汇报容易造成阶段归属歧义，本次增加分账修订；旧报告和原始计数不删除、不覆盖。

## 调用链与实际门控

账户步骤：独立的一次性外部编排 → backend/app/chatgpt_plan.py::access_token(allow_refresh=True) → 过期分支的单次POST auth.openai.com/api/accounts/oauth/token → 本地加密凭据更新。

模型步骤：scripts/voice_annotation_planning.py --execute-real → execute → ProfilePlanningAdapter.generate → existing_plan_token → chatgpt_plan.access_token(allow_refresh=False) → 读取有效旧token → 单次Responses请求。False约束这一调用，不是对后来独立批准的账户步骤的全局禁令。

原67文件逐字节核验一致。existing_plan_token调用点仍显式传False；chatgpt_plan.access_token在凭据不足且False时，于构造HTTP客户端之前抛出401。没有发现此门控被绕过，无需改动生产函数或重冻/重跑已用模型候选。

## 假凭据/假传输补验

新增tests/test_voice_annotation_boundary_audit.py，**4/4，1.258秒**。真正进入冻结模型execute及existing_plan_token；只替换凭据读取为合成记录，绝不读取真实.secret_key/.chatgpt_plan。断言真实access_token调用参数为allow_refresh=False。

- 过期凭据：OAuth客户端构造0，模型派发0，凭据写入0，结果not_dispatched。
- 缺scope及缺凭据：同样零OAuth/模型，安全类别为planning_credential_unavailable_no_refresh。
- 有效凭据：仅一次Mock Responses请求，实际body与冻结body一致，OAuth0、不写凭据。

HTTP客户端即使误建也会被测试拦下；有效路径所有HTTP强制MockTransport，不具真实网络能力。模拟marker/库均在临时目录，结束后清理，不影响真实已用收据。日志在运行目录no-refresh-boundary-tests-final.log；新增测试指纹及保护快照见account-boundary-audit.json。

## 限定收口

第2步按现有证据限定收口：隔离多位置规划/编辑/缓存/取消、一条冻结两位置声学听评、一条真实AI标注结构及单样本语义贴切分别成立。保持默认关闭、不接日常，日常接入仍第4步；跨文本稳定性、AI本条新音频、其他标签/语言/路径仍未证明。

第3步本轮只读准备见VOICE_STEP3_SCOPE.md，不修改分段/Adapter/播放器，不派发历史unknown请求或旧第二段，也没有新收费执行单。
