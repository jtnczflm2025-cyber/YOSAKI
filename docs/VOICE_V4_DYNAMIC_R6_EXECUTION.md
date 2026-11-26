# V4 动态桥接 R6：metadata 控制语义审计与零外呼候选

日期：2026-11-13。状态：**仅冻结工程候选；未获真实执行授权，未创建 R6 真实收据。** R5 旧代码指纹作废；R2～R4 收据及历史观察保持原样。

## 1. 官方源码证据与本地判定

审计基线为 OpenAI Codex 官方仓库提交 **`ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f`**。以下链接固定到该 commit，避免 `main` 后续变化影响本次结论。

| 字段／事件 | 该提交的下游处理证据 | 对本次仅公开正文、无工具的候选判定 |
|---|---|---|
| 事件分解 | [`metadata_events()`](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L228-L250)将同一载体拆为验证、审核、SafetyBuffering 与 Guardian 事件；[SSE 调度](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L670-L710)先发附加事件，正文与完成另行处理。 | 空载体只作通知；不能把整个 `response.metadata` 无条件丢弃或当成完成。 |
| SafetyBuffering | [解析](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L276-L301)读取顶层 `safety_buffering`，或 `metadata.type=safety_buffering`；字段含 use cases、reasons、`retry_model`。[core](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3510-L3523)发出状态事件，[app-server](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/app-server/src/bespoke_event_handling.rs#L432-L447)转为缓冲 UI 通知。这条链未证明客户端会自动换模型。 | 反映供应商缓冲／模型建议，当前 Voice 路径无对应状态；有非空缓冲信息即停，不换模型、不重试。显式 `false`／`null` 不表示缓冲，允许继续。 |
| ModelVerifications | [解析](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L253-L274)识别 `openai_verification_recommendation`；[core](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3488-L3496)仅发一次，[app-server](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/app-server/src/bespoke_event_handling.rs#L412-L421)通知前端。 | 属账户验证建议，不等同供应商拒绝；本候选没有处理验证要求的产品交互，字段一旦出现即明确停止。 |
| 审核／最终答案展示 | [解析](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L263-L274)读取 `openai_chatgpt_moderation_metadata`；[core](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3497-L3504)提取 `final_answer_moderation`，[答案绑定](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/stream_events_utils.rs#L335-L351)附到最终消息；[展示契约](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/protocol/src/items/display.rs#L52-L81)存在 `Show`／`Hide`／`Block`。 | **可能改变答案可展示性**，不能当纯诊断丢弃。当前语音没有该展示契约；遇字段就停语音和本次模型解析，保存已观察部分正文为失败，不把它标成完整。此前已播放片段无法撤回。 |
| GuardianAssessment | [解析契约](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/guardian_assessment.rs#L1-L39)明确它不直接授予执行权限；[core](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3209-L3230)仅在响应和 item 匹配后移交工具，[审批决策](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/guardian/decision.rs#L108-L170)还复核当下权限。 | 本候选不发送工具，理论上不应需要 Guardian 评分；若出现，视为超出固定请求契约并停止，绝不据此自动批准工具。 |
| 有效模型与轮次 header | [事件读取](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L179-L226)可从 header 读取实际模型、turn state；[core 模型处理](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3480-L3487)在不同模型时警示。 | 本候选冻结了模型且未实现 header 契约；`response.metadata` 有非空 header 即停，不自行切模型。 |

本次允许忽略的范围只有空 `response.metadata` 载体与明确无缓冲的 `safety_buffering=false/null`。未知 metadata 键、未知形态及携带其他内容的元数据不声称无害，均明确失败；新模型事件类型仍按原规则失败。**R4 没有保存 metadata 载荷，无法判定它属于上表哪一种；R3 未知类型继续未知。** 由于供应商可能在公开增量之后才给展示限制，已派发的早期语音无法撤回。本候选只保证收到控制字段后不再派发后续语音；若要求“任何后来被隐藏的正文从未被朗读”，还需另行解决首播前的展示确认时序，不能凭本次修复宣称已满足。

## 2. 代码改动与合成验收

[`responses_adapter.py`](../backend/app/responses_adapter.py)检查上述已知 metadata 字段、顶层缓冲、非空 header 及未知载体；仅空载体进入 `notice`。[`realtime_dynamic_candidate.py`](../backend/app/voice/realtime_dynamic_candidate.py)将控制／未知元数据归为安全错误码，停止模型解析与后续语音，保留部分正文、既有预算和终态规则。若终态帧同时带缓冲字段，先保留该终态中可靠的 usage，再因不支持缓冲而失败；不因仅有正文或 metadata 标记完成。诊断只留安全分类、事件类型、来源层、阶段和计数，不写原始载荷。普通 `StreamState.normalize`、聊天、Profile、告知及数据库结构均未变。

合成原始 SSE 经实际 `httpx` 分块解码、候选适配器和产品鉴权 WS 的专项覆盖：空通知重复后完成并携 usage、通知后失败／截断／EOF、其他未知事件；模型验证、审核显示、Guardian、嵌套及顶层缓冲、模型 header、未知键及终态帧带缓冲。拒绝分支有明确错误码、部分文字不冒充完成、语音轮次停止、无自动重试；完成／失败／截断保留原 usage 规则。**9/9 新增及关联定向测试、65/65 受影响 Voice 组合、2/2 普通 Responses／ChatGPT Plan 回归，退出码均为 0。** 所有输入是合成字节与本地假 TTD，不能冒充 R4 原始流或真实供应商验收。本轮未改前端，无需无变化重跑构建。

## 3. 新冻结候选与执行边界

| 冻结项 | R6 值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r6` |
| generation ID | `c0477093b8104420bcefa7372df7bc94` |
| [规范化 manifest](VOICE_V4_DYNAMIC_CANDIDATE_R6_MANIFEST.json) SHA-256 | `2a24e84418d4c31651981b44c99cd9ca9e6fd9178872937c5ce57d2cba918df4` |
| 完整 16 文件代码集合 SHA-256 | `1851f333f14000c8943aa7720d88b62e79977ef5eab0279bbd28b5d511fd14a4` |
| 完整上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256 | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

R6 与 R5 的 `model_request` 完全相同，manifest 仅 `run_id` 与 `generation_id` 不同。16 文件中只有 `backend/app/responses_adapter.py`（SHA-256 `f6bd9b6e233746754d2fa4d65823c9ff89f5ade1c8af2579d4301adc2b5a4f8f`）及 `backend/app/voice/realtime_dynamic_candidate.py`（SHA-256 `64875f46acf4836431e1149f096a83d22e0ffbb48f80924765609400c042b44a`）相对 R5 变化；其余 14 个哈希见 [R5 的完整有序表](VOICE_V4_DYNAMIC_R5_EXECUTION.md#2-冻结身份请求与文件指纹)。集合算法及代码路径仍由 `realtime_dynamic_candidate_receipt.CODE_PATHS` 固定。

模型请求仍为 ChatGPT Plan 档案 5 的 `gpt-6.1-sol`，最多一次生成、128 输出 token、30 秒；完整 system／user 内容及确切请求体见 [R5 第 2 节](VOICE_V4_DYNAMIC_R5_EXECUTION.md#2-冻结身份请求与文件指纹)与 R6 manifest。TTS 沿用原 IVC Voice ID、Profile revision 3、`eleven_v4_turbo`、`pcm_24000`、`language_code=ja`，最多 80 个请求字符、2 片段、2 WS、0 个 ElevenLabs HTTP、1 MiB；每条 WS 30 秒、整轮接收 45 秒、浏览器排空 30 秒、总墙钟 110 秒。无自动重试、补连、补发或换模型。费用仍未知，限额不等于账单硬上限。

**R6 未获真实调用授权，也未创建本次真实收据。** 日后若另行批准，仍须重新核对全部指纹、实际 `.ts` 路径、owner／角色／对话、Profile／告知、代理及凭据剩余至少 90 秒；任何漂移即停。R2～R4 已用收据、R5 历史指纹都不得复用。日常服务、数据库、Profile 与 Memory 保持不变。本候选对晚到的答案展示限制仍有已播片段不可撤回的限制，不能仅凭合成测试宣称真实动态流式语音已安全通过。
