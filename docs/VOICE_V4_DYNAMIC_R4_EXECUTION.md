# V4 动态桥接 R4：单次真实补测候选

日期：2026-11-13。状态：**R4 已获单次批准并执行一次；收据已占用，未通过完整动态桥接验收，不得重复派发。** 执行前冻结绑定与同账户凭据续期通过。R2／R3 的收据和原始结论保持不变。

## 1. 冻结身份与差异

| 项目 | 冻结值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r4` |
| generation ID | `5be20d34d3064f3bb388f34a68e104be` |
| 规范化 manifest SHA-256 | `e8f2f8a81e9750d33fb5d2538924ce800ee07cf676c0126dbc35c2f1828d8094` |
| 完整 16 文件运行代码集合 SHA-256 | `f05f5e8ec376a6a8e19d8e2ba9d491068bd7f8f90c314c9132482a91ad5636d2` |
| 完整模型上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256 | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

[R4 规范化 manifest 与完整请求](VOICE_V4_DYNAMIC_CANDIDATE_R4_MANIFEST.json)来自日常库只读取样及新的临时隔离库。与 R3 比较，`model_request` 完全相同；manifest 除 `run_id`、`generation_id` 外完全相同。当天只读核对仍为 owner 1／角色 1／对话 5／模型档案 5、Profile revision 3、告知 `2026-11-08-v1`、原 Voice ID 摘要及代理 `http://127.0.0.1:7897`。以后执行前仍须重新核对，漂移就停止，不自动更新本单。

相对 R3 的运行代码差异仅在三处：`responses_adapter.py` 增加有界原始 SSE 解码与明确的候选事件分类；`realtime_dynamic_candidate.py` 在最终产品派发路径使用该分类、区分安全通知与正文／终态并保留失败阶段；`realtime_dynamic_diagnostics.py` 为未知类型留安全标识或哈希、来源层及解析分支。其余 13 个运行文件字节哈希与 R3 相同。测试夹具恢复和浏览器假供应商脚本的修改**不属于**真实派发代码集合。旧 R3 授权代码指纹不适用于 R4。

16 文件逐项 SHA-256（固定顺序，路径相对项目根目录）：

| 文件 | SHA-256 |
|---|---|
| `backend/app/main.py` | `80419923536aa7aa4674da3bf3ac154775f2352c407209dbe5f147969b3c3c0d` |
| `backend/app/chatgpt_plan.py` | `927c670fbf8f22e42f794fd70d9d0a7c1b371ab57010774cefc78364cfd2ae2b` |
| `backend/app/responses_adapter.py` | `8214c9d2accbc8cae0606ced3a4e6250be34dbeab23ddfc3c5ef41e0c03c4707` |
| `backend/app/voice/realtime_dynamic_context.py` | `56c947c3e10c4aa393e894e6dd1f47c7f3edb427496e4539fc86672fc389b022` |
| `backend/app/voice/realtime_dynamic_candidate_receipt.py` | `a1d8860a8894ae6dfd5d81637e45f343dc72d5dc796272ec4a9b4e180ff40aaf` |
| `backend/app/voice/realtime_dynamic_candidate.py` | `c1449c85389c4198fa9d1f32eca90956efef5a653f876e4444e123f1378122c9` |
| `backend/app/voice/realtime_dynamic_diagnostics.py` | `9af73c6aa767b9c85ac60aa1f72245e6aa80f95d0d2f330a3c3efd4dd6edb926` |
| `backend/app/voice/realtime_chat_bridge.py` | `a14969f23219048fa68ca2a3b3817b1ef345f2183014be133e834570107d57a1` |
| `backend/app/voice/realtime_output.py` | `8c69ccc98565936b3002cebeb027e8db82106bdd8f96d2f4a84afda27c045571` |
| `backend/app/voice/realtime_ttd_connector.py` | `766a0871239b72fbdfa51fdb8c8c3d9d9f6f0e8b268a2099957987f10a85009c` |
| `backend/app/voice/readable_text.py` | `ef2ceb0bd0293faff50c5d2ed7054b2d4c329db281db44a7239015bcfda73e94` |
| `frontend/src/voice/realtimeOutput.ts` | `de80aac8c847dcf971d9f0d1a71e7a187d931b075d53bc258754093aa35c33ba` |
| `frontend/src/voice/DynamicChatOutput.vue` | `359887a39c5271a8781169474449db39b427a8c69891fabb54ad24e355a5bce3` |
| `frontend/src/App.vue` | `c2d734a4fe74c0ca077ee51663536ff307b56e6496476f1f21c4cd93830f8b72` |
| `scripts/voice_v4_dynamic_candidate_isolated.py` | `0e2a77c913d0032af334ad71bef9b002afdc91f1b237b2ac1068f6ab1a289052` |
| `scripts/voice_v4_dynamic_candidate_approve.py` | `4c7994d27df1d7a35531985b1704a161b7da39520a8836094cf1ee2f2c397894` |

集合算法：每个文件取原始字节 SHA-256，依上表顺序拼接 `路径 空格 哈希 换行`，再计算 SHA-256。规范化 manifest 使用按键排序、无多余空格的 UTF-8 JSON 摘要；它不同于整个 JSON 文件的字节哈希。

## 2. 两侧的精确数据与预算

模型供应商：本机 ChatGPT Plan 授权的 `gpt-6.1-sol`，`POST https://api.openai.com/v1/responses`，最多一次。请求体固定为 `model=gpt-6.1-sol`、`input=[{role:user,content:<下列 User 全文>}]`、`instructions=<下列 System 全文>`、`store=false`、`stream=true`、`max_output_tokens=128`。不传工具、聊天历史、记忆、摘要、欲望建议或独白。档案中 `temperature=0.8`、`top_p=1.0`，但**本次请求体不发送这两个字段**。

System 全文：

```text
## 系统规则
保持真诚、具体，并尊重对话边界。

温柔但不软弱。拥有自己的判断、好奇心与边界。喜欢用具体的感受回应，不把对话变成模板化的安慰。

## 阿夜的人设
温柔但不软弱，拥有自己的判断与好奇心。
```

User 全文：

```text
阿夜，请只用日语说两句简短的话：先说今晚可以慢慢来，再说你会在这里听我讲。只输出正文，合计不超过 50 个日文字符；不要 Markdown 或 Audio Tags。
```

ElevenLabs 只接本轮**动态产生的公开助手正文**，经过既定过滤与稳定分句。正文无法预先冻结，每段派发前核对来源、字符数与累计预算。原 IVC Voice ID、Profile revision 3、`eleven_v4_turbo`、`pcm_24000`（24 kHz、16-bit、单声道）、`language_code=ja`；其余供应商设置维持冻结请求。连接先发 `{"voices":[<原 Voice ID>]}`，每片段发 `{"inputs":[{"text":<过滤后片段>,"voice_id":<原 Voice ID>}],"flush":true}`；保活与关闭沿用冻结连接器。凭据仅由后端读取，不写入 manifest、浏览器或日志。

| 独立上限 | 数值 |
|---|---|
| 模型 | 最多 1 次生成、128 输出 token、30 秒 |
| ElevenLabs | 最多 80 个实际请求字符、2 片段、2 条 WS、0 次供应商 HTTP、累计 1 MiB 音频 |
| 时间 | 每条 WS 30 秒；整轮语音接收 45 秒；接收后浏览器排空 30 秒；整轮墙钟 110 秒 |

预算限制本地派发，不保证供应商账单上限。两侧已尝试请求的真实用量与费用可能未知，不能填零，也不能因本地 `send` 返回就声称供应商确认接收。

## 3. 停止与完成规则

- 语音失败、未知事件、断线或超语音预算：停止当前及后续语音，隔离迟到音频；已尝试发送的片段不重试、不补连、不重发。已启动模型仍可在原模型／整轮预算内完成并保存文字，语音故障不把本可完成的文字改为失败。
- 用户停止语音：立即清播放队列，文字继续。用户停止生成：取消模型并同步停止语音，部分正文按原规则保存为 `stopped`。
- 模型明确失败、截断、解析错误或流结束却无 `response.completed`：分别记 `failed` 或 `incomplete`，停止语音；保留已观察的正文与可靠 usage。正文到达不代替完成事件。
- 试验整轮超时、整轮授权撤销：按来源终止相应任务；占用的收据不释放。诊断失败禁止后续语音派发，已派发模型尽可能在原预算内保存文字，并标明诊断不完整。
- 完整验收分别报告文字终态、正文与送读对账、语音接收与浏览器软件播放排空；软件播放不代替真人听感。首播若晚于模型完成，按实记录，不称为边生成边播通过。

## 4. 执行入口与前置门槛

**当前不创建 R4 的真实批准收据，也不连接供应商。** 若用户另行批准，先按本单复核 16 文件代码指纹、规范化 manifest、完整上下文、角色／会话／Profile／告知、Voice ID 摘要、代理、后端 Key 仅配置状态及 ChatGPT Plan 凭据剩余至少 90 秒。任何一项漂移即停，不现场重写冻结内容；模型目录查询也不隐含加入本单。

批准后才在 `%LOCALAPPDATA%\YOSAKI\voice-v4-dynamic-20261113-r4\` 创建本次唯一 `real_approved` 持久收据，在首个模型请求前原子占用。重启、重复点击、并发、崩溃或结果未知都不恢复额度。R2／R3 `.used` 继续保留且不可复用。

最终产品入口固定为新临时隔离库、单 worker 后端 `127.0.0.1:18786`、仅代理该后端的前端 `127.0.0.1:15188/voice`。日常数据库只读取得角色／Profile／上下文；登录隔离产品页面后，由“启动一次性真实候选”控件发起，经后端鉴权 WS、relay 与浏览器播放器。保持日常 8000／5173、日常数据库、Profile、告知和 Memory 工作不变。完成后关闭隔离实例、清理临时登录会话，保留已占用收据与脱敏证据，不覆盖正式库。

## 5. 零外呼验收与历史限制

原失败的最小三项顺序复现，Voice 测试夹具恢复 `main` 模块、环境与应用生命周期后转为 3/3；原 **107 项组合重跑 107/107、退出码 0**。共享适配器的既有 `payload`、`endpoint`、`StreamState`、`input_items`、`preview_text` 未改变；原有聊天／Responses／ChatGPT Plan 测试包含在该组合内。额外 V4 产品测试 15/15 通过，其重复启动测试只等待明确的拒绝事件，不假定中途没有正常派发事件。

浏览器用合成原始 SSE 字节、本地假 TTD 与隔离账户经过实际产品页面：正常序列显示消息 2 `completed`、2 片段／2 连接、软件播放队列排空；本机 in-app 浏览器记录启动至首个非零样本进入播放链约 6256 ms，此数值仅属模拟环境。未知 `response.future_notice` 序列显示消息 2 `failed`、语音停止，0 音频；私有诊断记录 `provider_sse_json`、`unsupported` 和安全事件类型，已尝试的假 TTD 文本不重发。合成序列不是 R3 原始流，不能反推出 R3 具体事件。历史 R2 及空闲 TTD 未知项不改写；真实动态桥接仍未通过。

## 6. R4 单次真实执行结果（2026-11-13）

用户以本单冻结的运行编号、generation ID、四项 SHA-256 与原预算授权一次。初检发现原 ChatGPT Plan 凭据已过期，未创建收据、未派发模型或 TTD。随后沿用已授权的同账户隔离 OAuth 续期；页面返回凭据已发布、有效期超过 90 秒、模型目录列出 `gpt-6.1-sol`。续期后只读复核凭据尚余约 3,571 秒；完整模型请求、规范化 manifest、16 文件代码、上下文、Voice ID 摘要、Profile revision 3、告知、代理及隔离来源均匹配。隔离 Vite 编译的候选组件引用 `realtimeOutput.ts`，未引用同名旧 `.js`。

**模型目录请求范围更正：**第 4 节的“模型目录查询也不隐含加入本单”只描述候选派发预算，未完整描述既有续期页面行为。本次对本地续期页 `/refresh` 调用一次；其已冻结实现先通过 `chatgpt_plan.access_token()` 刷新过期凭据，再**主动调用** `chatgpt_plan.models()`，后者以新凭据对模型供应商执行 HTTP GET `/models`，没有缓存分支。页面返回 `catalog_ok=true`、目录 8 项且目标模型已列出。因此它不是旧缓存，也不是动态候选模型生成请求，而是本次续期流程中额外的账户模型目录查询；以前关于必要 OAuth 续期及模型目录请求的授权覆盖该流程。没有为本次追查再次请求账户。未来执行单须把续期时的目录 GET 单列，不能隐含在生成额度内。

在 `%LOCALAPPDATA%\YOSAKI\voice-v4-dynamic-20261113-r4\` 创建本次唯一真实收据后，以新临时 SQLite、`127.0.0.1:18786` 后端及 `127.0.0.1:15188/voice` 产品页面登录。仅点击一次“启动一次性真实候选”；后端在模型派发前原子占用 `.used`，其状态为 `occupied_outcome_unknown`，不恢复额度。

| 观察项 | 本次结果 |
|---|---|
| 模型派发、事件与终态 | 1 次模型请求尝试。观察到 `response.created`、`response.in_progress` 和公开正文增量；约 4.109 秒时，来自 `provider_sse_json` 的事件类型 `response.metadata` 进入 `unsupported` 分支，冻结代码报告 `model_event_unknown`／`ValueError`。这是**本地适配器分类失败**的证据，不等于供应商报告模型失败。没有观察到 `response.completed`，模型终态按本次执行规则记 `failed`；可靠 usage 未观察到。 |
| 文字保存与对账 | 隔离库助手消息 ID 2 保存公开正文 29 字，`generation_status=failed`，`usage_json` 为空；完整性检查 `ok`。诊断在外呼前保存片段 1 的 12 字文本、来源 `0..12` 和 SHA-256，与保存的公开正文该范围一致。已播放内容为零；不能把剩余正文或部分文本标成完整回复。 |
| TTD 派发 | 预算计入 1 个片段、12 字、1 条 WS **连接尝试**。无 `connection_ready`、`text_send_attempt`、`text_send_return`，不能证明供应商连接已建立或接收文本；已确认文本发送尝试为 0。模型解析失败后语音停止，未补连或补发。 |
| 音频与浏览器播放 | 首音频事件 0、观察音频 0 字节；浏览器未记录首个非零样本进入播放链，`browser_playback_ended=false`。软件播放未通过，真人听感未测。 |
| 诊断与费用 | 私有诊断 JSONL 共 38 条，以 `turn_summary` 收尾；本次未观察到记录容量截断。受限事件类型、来源层与解析分支已留存。模型与 ElevenLabs 的实际计费均未知，不能填零。 |

后续只读核对了公开 [Responses 流事件表](https://developers.openai.com/api/reference/resources/responses/streaming-events)与 [OpenAI Codex SSE 适配源码](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs)：前者没有列出 `response.metadata`，后者明确把它作为可携带模型／审核等附加信息的事件，正文与完成／失败由其他事件处理。这支持在候选适配器中把该类型视为**非正文、非终态**，但不证明 R4 当时的 metadata 载荷内容无害；旧诊断没有保存该载荷，也不能据此追认 R3 的未知事件类型。修复与模拟结果另见主台账后续节，R4 原观察及收据不改写。

持久证据为同目录 `.approval.json`、`.used`、`.diagnostic.jsonl` 与 `observation.json`；后者保留公开文字、片段来源对账和脱敏计数。隔离账户已在浏览器退出，浏览器标签与隔离 18786／15188 服务已关闭。**隔离进程被中止后，临时目录没有自动清理；两次针对已核实的 `%TEMP%\yosaki-v4-candidate-3gtv7tpb\` 的删除操作均被执行环境的自动审批策略拒绝（仅提示 `blocked by policy`）。** 因而该目录中仍有本次隔离 SQLite 和初始化时产生的 1 条合成账户 session 摘要；浏览器登录产生的 session 已通过登出撤销。该隔离目录不被日常服务使用，后续须在允许的环境中定点清理，不得把它写成已清理。日常 8000／5173、正式 SQLite、Profile 与 Memory 工作保持不变；除本次获准的本地 ChatGPT Plan 凭据续期外，没有修改日常数据。**R4 收据不可复用，本次失败不自动追加下一轮。** R3 的具体未知事件及历史空闲 TTD 问题继续保留原结论。
