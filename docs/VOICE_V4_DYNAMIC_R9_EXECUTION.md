# V4 动态桥接 R9：单次真实候选执行单

日期：2026-11-13。状态：**已按单次授权执行；R9 收据已占用，不得再次派发。** 下文第 1～4 节保留执行前冻结单，实际结果见第 5 节。R8 的已占用收据、模型完成和可靠 usage 原样保留；不得用于 R9。

## 1. 冻结绑定

| 项目 | 冻结值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r9` |
| generation ID | `4d85f51980f1483d928e8641615e9c61` |
| [完整 manifest 与模型请求](VOICE_V4_DYNAMIC_CANDIDATE_R9_MANIFEST.json) 的规范化 manifest SHA-256 | `9355541853f5791834c57f0be8d404a91b7e5ce61142c7862ced5d503a1d1be5` |
| manifest 文件字节 SHA-256（辅助核对） | `e0b946f9063902f795f5d840feffaba5ba5f21f87b56debfaa61f78d9be0c9bf` |
| 完整 17 文件运行代码集合 SHA-256 | `25cb6ca94d79421f1a9bb682f5186cf081363ab4edbe9dbde9bf933c72792f27` |
| 完整模型上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256，仅核对，不公开原值 | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

规范化 manifest 使用 UTF-8 JSON、`sort_keys=True` 和紧凑分隔符。代码集合使用 `realtime_dynamic_candidate_receipt.CODE_PATHS` 的既定顺序，对每个文件拼接 `路径 空格 文件字节 SHA-256 换行` 再求 SHA-256。冻结时的逐文件值如下；执行前任一文件或绑定漂移即停止，不能现场改码并重算后继续。

| 运行文件 | SHA-256 |
|---|---|
| `backend/app/main.py` | `80419923536aa7aa4674da3bf3ac154775f2352c407209dbe5f147969b3c3c0d` |
| `backend/app/chatgpt_plan.py` | `927c670fbf8f22e42f794fd70d9d0a7c1b371ab57010774cefc78364cfd2ae2b` |
| `backend/app/responses_adapter.py` | `f6bd9b6e233746754d2fa4d65823c9ff89f5ade1c8af2579d4301adc2b5a4f8f` |
| `backend/app/voice/realtime_dynamic_context.py` | `56c947c3e10c4aa393e894e6dd1f47c7f3edb427496e4539fc86672fc389b022` |
| `backend/app/voice/realtime_dynamic_candidate_receipt.py` | `385f0d0631040bb2b4f478058ccb0c32208da8aff734d3450d087641d283adbe` |
| `backend/app/voice/realtime_dynamic_candidate.py` | `88c0b4e10a95966d9e6ea5daa8a2c51f0a32bac0fa2a5d5a16f9a86a1f273d58` |
| `backend/app/voice/realtime_dynamic_diagnostics.py` | `3b9b438de7b8b72aa3c655fe13cb82de53d137e8847cdecd53aa82d744162a52` |
| `backend/app/voice/realtime_answer_display.py` | `37afc6ecb814e9746cdca0950b3afe1a2b733a5f4ffa049c183f6285544f77c6` |
| `backend/app/voice/realtime_chat_bridge.py` | `a14969f23219048fa68ca2a3b3817b1ef345f2183014be133e834570107d57a1` |
| `backend/app/voice/realtime_output.py` | `8c69ccc98565936b3002cebeb027e8db82106bdd8f96d2f4a84afda27c045571` |
| `backend/app/voice/realtime_ttd_connector.py` | `766a0871239b72fbdfa51fdb8c8c3d9d9f6f0e8b268a2099957987f10a85009c` |
| `backend/app/voice/readable_text.py` | `ef2ceb0bd0293faff50c5d2ed7054b2d4c329db281db44a7239015bcfda73e94` |
| `frontend/src/voice/realtimeOutput.ts` | `2da7325a66a3aaf54795bfcdc40b39846e81396633ffafdbb6c8b6a1bb543b73` |
| `frontend/src/voice/DynamicChatOutput.vue` | `ffbde30b9c0328a239b528d228123399294cecd8f77550dcfb9ffaa8cc3ce7a3` |
| `frontend/src/App.vue` | `c2d734a4fe74c0ca077ee51663536ff307b56e6496476f1f21c4cd93830f8b72` |
| `scripts/voice_v4_dynamic_candidate_isolated.py` | `e65c584739294cad506e6af59d95b7b716386fedb4262e0eea1c639472f485d5` |
| `scripts/voice_v4_dynamic_candidate_approve.py` | `4c7994d27df1d7a35531985b1704a161b7da39520a8836094cf1ee2f2c397894` |

R9 相对 R8：manifest **仅**替换运行编号、generation ID；完整模型请求、身份、Profile revision 3、告知版本 `2026-11-08-v1`、Voice ID 摘要、本机代理 `http://127.0.0.1:7897`、模型参数与预算均不变。17 个运行文件中仅 `realtime_answer_display.py` 和 `realtime_dynamic_diagnostics.py` 改变；另改隔离测试、无外呼浏览器替身及主台账，不进入运行集合。R8 真实收据不能转给新代码或新运行。

## 2. 完整模型请求与动态送读范围

模型供应商为现有 ChatGPT Plan 档案 5 的 `gpt-6.1-sol`，`POST https://api.openai.com/v1/responses`。请求体固定为 `model=gpt-6.1-sol`、`input=[{role:user,content:<下方 User 全文>}]`、`instructions=<下方 System 全文>`、`store=false`、`stream=true`、`max_output_tokens=128`。不发送工具、历史、记忆、摘要、欲望建议或独白。档案中的 `temperature=0.8`、`top_p=1.0` 不进入这次实际请求体；完整结构及启用提示块详见上方 manifest 文件的 `model_request`。

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

ElevenLabs 只接收**本轮模型完成、流 EOF、展示允许且正文与唯一 final_answer 绑定核对后**，经既定过滤的动态公开助手正文。原 IVC Voice ID、`eleven_v4_turbo`、`pcm_24000`（24 kHz、16-bit 单声道）、`language_code=ja`。TTD WS 为 `wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`；连接注册 `{"voices":[<原 Voice ID>]}`，每片段 `{"inputs":[{"text":<过滤后片段>,"voice_id":<原 Voice ID>}],"flush":true}`。凭据仅由后端读取，不进入 manifest、前端或诊断。

## 3. 预算、停止与证据

| 独立限额 | 上限 |
|---|---|
| 模型 | 1 次生成、128 输出 token、30 秒 |
| ElevenLabs | 80 个实际请求字符、2 片段、2 条 WS、0 次供应商 HTTP、累计 1 MiB 音频 |
| 时间 | 每条 WS 30 秒、整轮语音接收 45 秒、接收后播放排空 30 秒、整轮墙钟 110 秒 |

数量和时间不是费用硬上限；实际费用或未返回的用量保持未知。当前真实请求额度为 **0**，必须取得此 R9 的单独批准，才可创建仅 R9 有效的持久一次性 `real_approved` 收据，并在首次模型派发前原子占用。进程重启、并发、失败、取消或结果未知均不恢复额度。

1. 事前复核两项冻结哈希、实际 `.ts` 加载路径、owner 1／角色 1／对话 5／模型档案 5、完整上下文、Profile revision 3、Voice ID 摘要、告知、代理、展示门控以及两侧凭据。ChatGPT Plan 凭据至少余 90 秒。任一差异停止，不现场改请求或指纹。
2. 使用新建最小隔离 SQLite，单 worker 后端 `127.0.0.1:18786`，仅代理该后端的前端 `127.0.0.1:15188/voice`。日常库只读取样；日常 8000／5173 不切换。经隔离产品登录、鉴权 WS、relay 与浏览器播放器执行一次，不退回独立探针。
3. 仅在模型 `response.completed`、流 EOF、唯一 `final_answer` ID／正文匹配后决定展示。有有效 `show` 且附属要求可履行时允许；**无显式决定**时仅在无其他限制下按固定公开协议默认展示，诊断写 `decision=none` 和 `protocol_default_display_no_explicit_decision`，不伪造 `show`。字段缺失、显式 null 分别记 `field_missing`、`explicit_null`；非法已提供决定、`hide`／`block`、未知控制、附属要求不支持、重复或冲突决定、回答绑定错误均限制。限制后不得发布文字或继续排队音频，TTS 文本发送 0。
4. 这是**生成完成后流式合成播放**，不是边生成边播。模型失败、截断、解析错误、缺完成事件、模型或整轮超时按原规则停止并保留可靠 usage。语音侧失败只停止当前及后续语音；已启动模型在原预算内可完成并按展示规则保存文字。已尝试发送的文本不重试、不补连、不补发，不重新生成聊天回复。用户停止生成和整轮撤权按原范围终止。
5. 报告模型终态与可靠 usage、展示决定及诊断的 `decision_presence`、文字保存、实际送读文本／来源、连接与发送阶段、音频字节、首播与模型完成顺序和播放排空。发送返回不等于供应商确认；真人听感未测则记未测。结束后停止隔离服务、撤销临时登录会话，保留收据和受限诊断；清理受阻如实记录。

如执行前凭据过期，允许另行按用户批准的**同账户** OAuth 续期流程，其中最多一次独立模型目录 GET；该账户请求单独计数，不计入模型生成或 ElevenLabs 限额。凭据有效时不刷新、不查询；交互登录由用户完成。续期若改变冻结身份或请求绑定，停止。

## 4. 协议依据、零外呼证据与待批准边界

固定官方提交 `ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f`：原始 metadata 提取见 [SSE 解析](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L2636-L2653)，缺少 `final_answer_moderation` 的 envelope 见[官方测试](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L1795-L1816)；[core 可选决定解析](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3497-L3503)与[回答绑定](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/stream_events_utils.rs#L2054-L2079)说明 `None` 不附加审核决定；[AgentMessage 可选字段](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/protocol/src/items.rs#L2407-L2439)及[app-server 展示对象映射](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/app-server-protocol/src/protocol/v2/item.rs#L973-L987)保留正文；[Show/Hide/Block 与附属要求](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/protocol/src/items/display.rs#L553-L590)仍按显式决定处理。公开源码未证明所有闭源前端的行为；这里的默认展示是针对 YOSAKI 严格完成及绑定门控的协议兼容判断。

合成原始 SSE 每 3 字节拆块，经实际解码、适配器和产品 WS 的受影响专项 **64/64 通过，退出码 0**；诊断模块专项 **4/4 通过，退出码 0**。覆盖字段缺失、显式 null、非法、show/hide/block、重复／冲突、绑定、未知控制以及完成／失败／截断/usage 的原有规则。无外呼产品浏览器：缺失决定时文字保存、2 段本地假音频播完，非法决定时文字受限、0 片段／0 连接。私有脱敏证据在 `%LOCALAPPDATA%/YOSAKI/voice-runs/r9-local-display-20261113/`，不是 R8 原始载荷回放。冻结时当前日常库只读取样的零外呼预览与 R9 manifest、完整模型请求、代码哈希均一致。真实模型、账户目录、OAuth 与 ElevenLabs 请求均为 **0**，未签发真实收据；历史 R8 的字段缺失／显式 null 无法由留存记录进一步区分，历史结果不追认成功。

## 5. 单次真实执行结果（2026-11-13）

执行前，规范化 manifest 与 17 文件代码指纹均与第 1 节一致；日常库只读取样重算的完整模型请求、Profile revision 3、Voice ID 摘要、告知版本和代理均匹配。展示门控由隔离入口显式启用，`DynamicChatOutput.vue` 实际导入 `./realtimeOutput.ts`。ChatGPT Plan 凭据余约 1,548 秒，超过 90 秒，因此没有 OAuth 续期或模型目录 GET；ElevenLabs Key 仅检查已配置。新临时 SQLite 的隔离产品后端为 `127.0.0.1:18786`，前端为 `127.0.0.1:15188`，日常 8000／5173 未切换。创建 R9 专用收据，并在首次模型请求前原子占用；`.used` 状态为 `occupied_outcome_unknown`，额度不得恢复。

浏览器产品控件只启动一次。脱敏记录含 1 次 `model_request_attempt`、明确 `model_status=completed`、可靠 usage **input 138／output 67／total 205**。约 5,188 ms 收到审核 metadata；约 5,235 ms 记录模型与展示终态，约 5,250 ms 记录整轮摘要。模型请求是否产生实际账单仍未知。

**展示受限，语音未启动。** 解析器记录 `moderation_control_unsupported`，路径 `metadata.openai_chatgpt_moderation_metadata.unknown_field`、期望 `known_fields`、实际 `object`，未知字段名仅保留 SHA-256 `8adb95b764c08d09008456fe40d031e7df09c0eb8b04313d88871acdbd51f8f5`，不保存原始载荷。`required_fields_present.final_answer_moderation=false`，但因同一 envelope 存在未知控制，`decision_presence=not_checked`、回答绑定未进入检查；**不能把它归为默认展示或伪造 `show`**。页面显示“此回答未获可展示确认”，未保存助手文字；TTS **0 字符、0 片段、0 条 WS、0 次 HTTP**，接收音频 **0 字节**，无播放／排空或真人听感。此结果不构成动态语音通过，也不追加真实请求。

收据、预检与诊断留在 `%LOCALAPPDATA%/YOSAKI/voice-runs/voice-v4-dynamic-20261113-r9/`。隔离浏览器已退出登录，18786／15188 端口已关闭；日常 8000／5173 仍由原进程监听。此次临时数据库目录 `C:\Users\ASUS\AppData\Local\Temp\yosaki-v4-candidate-9b5s2sy_` 仍存在：进程退出后未自动删除，针对该**精确隔离路径**的递归清理被自动审批审查以 `blocked by policy` 拒绝，未改用其他方法绕过。只读检查发现隔离库 `auth_sessions` 仍有 **1 条初始化时建立的会话哈希**；浏览器登录会话已在页面登出并被删除。该目录含隔离角色配置，应按敏感临时数据保留现场，后续清理需单独处理。历史 R2～R8 的收据和结论均未改变。
