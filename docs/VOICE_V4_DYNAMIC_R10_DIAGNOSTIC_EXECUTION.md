# V4 动态桥接 R10：单次协议诊断候选执行单

日期：2026-11-13。状态：**已按单次授权执行；R10 收据已占用，不得再次派发。** 第 1～4 节保留执行前冻结内容及当时的“待批准”措辞；实际结果见第 5 节。R9 根因仍未知，R9 收据未复用。

## 1. 冻结绑定与相对 R9 的变化

| 项目 | 冻结值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r10` |
| generation ID | `78aa18a04bdd4d24b5ebf792e4774508` |
| [规范化 manifest 与完整模型请求](VOICE_V4_DYNAMIC_CANDIDATE_R10_MANIFEST.json) SHA-256 | `9f9410860ed2dd90e27c3dc67760947fd186180cd4831bee16244197aeafa7b9` |
| `CODE_PATHS` 既定顺序的完整 17 文件代码集合 SHA-256 | `5c04cfb51db7fb308a3fb784afceb00ed782ec7fbd2de948b7d9c3b8f928c427` |
| 完整模型上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256，仅核对 | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

规范化 manifest 采用 UTF-8 JSON、`sort_keys=True`、紧凑分隔符；代码集合对 `realtime_dynamic_candidate_receipt.CODE_PATHS` 中每个文件按“相对路径、空格、文件字节 SHA-256、换行”拼接后求 SHA-256。相对 R9，运行编号与 generation ID 更新，模型上下文、身份、Profile revision 3、告知、Voice ID 摘要、代理、请求参数与限额不变。17 个运行文件中仅以下两份变化；其余 15 份逐文件 SHA-256 与 [R9 执行单第 1 节](VOICE_V4_DYNAMIC_R9_EXECUTION.md#1-冻结绑定)一致：

| 运行文件 | 本候选 SHA-256 | 变化 |
|---|---|---|
| `backend/app/voice/realtime_answer_display.py` | `0d1b1f9eca601440e09fb53e1ce70e73f938b46e41204171bd575dac9d944bd2` | 按层级记录未知键哈希／类型／数量，并独立解析已知决定；未知仍限制 |
| `backend/app/voice/realtime_dynamic_diagnostics.py` | `4ab8b6633dc9efdc5fd985ddd44e63310b48885d21c1725fa349bdcc24a48754` | 新诊断字段白名单与结构校验，不允许原始未知键名或值混入 |

另改隔离测试和主台账；它们不在 17 文件运行集合内。任何运行代码、manifest、上下文、身份或有效 Profile 漂移均停止，不能现场重算指纹后沿用此单。

## 2. 固定请求及独立限额

模型为现有 ChatGPT Plan 档案 5 的 `gpt-6.1-sol`，请求 `POST https://api.openai.com/v1/responses`。请求体固定：`model=gpt-6.1-sol`、`input=[{role:user,content:<下方 User 全文>}]`、`instructions=<下方 System 全文>`、`store=false`、`stream=true`、`max_output_tokens=128`；不发送工具、历史、记忆、摘要、欲望建议或独白。档案中的 temperature／top_p 不进入本次请求体。完整请求结构已冻结在 manifest 的 `model_request`。

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

语音只接收**模型明确完成、流 EOF、唯一 `final_answer` ID 与正文匹配、展示门控允许后**的过滤公开正文。原 IVC Voice ID、`eleven_v4_turbo`、`pcm_24000`（24 kHz／16-bit／单声道）、`language_code=ja`。供应商 TTD WS：`wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`；注册字段 `{"voices":[<原 Voice ID>]}`，每片段字段 `{"inputs":[{"text":<过滤片段>,"voice_id":<原 Voice ID>}],"flush":true}`。供应商凭据只由后端读取，不进入前端、manifest 或诊断。

| 资源 | 单次上限 |
|---|---|
| 模型 | 1 次生成、128 输出 token、30 秒 |
| ElevenLabs | 实际请求字符 80、2 片段、2 条 WS、0 次供应商 HTTP、累计音频 1 MiB |
| 时间 | 每条 WS 30 秒、整轮语音接收 45 秒、接收后播放排空 30 秒、整轮墙钟 110 秒 |

上述是请求及时间上限，**不是费用硬上限**。实际费用和未返回的用量保持未知。当前真实额度为 **0**；此文件不构成批准。

## 3. 前置核对、执行和停止规则（获单独批准后才适用）

1. 复核规范化 manifest、17 文件代码集合、实际 `.ts` 加载路径、owner 1／角色 1／对话 5／模型档案 5、完整上下文、Profile revision 3、原 Voice ID 摘要、告知版本 `2026-11-08-v1`、本机代理 `http://127.0.0.1:7897`、展示门控及两侧凭据。ChatGPT Plan 凭据至少余 90 秒。若需同账户续期及最多一次独立模型目录 GET，须在单次批准中**另列账户请求额度**；凭据有效时不刷新、不查询。任何绑定不符停止。
2. 仅在所有核对通过且获得此运行编号的独立授权后，创建一次性持久 `real_approved` 收据，在首次模型派发前原子占用。使用新建最小隔离库与隔离产品实例：后端 `127.0.0.1:18786`、前端 `127.0.0.1:15188/voice`；日常库只读取样，日常 8000／5173 不切换。必须经产品控件、鉴权 WS、relay 和浏览器播放器；不以独立探针替代。
3. 未知 envelope 字段、非法决定、明确 `hide`／`block`、不支持附属要求、回答绑定不符、重复或冲突决定继续限制文字与 TTS。已知决定可**独立记录存在状态和解析结果**，不因解析出 `show` 而越过未知字段。未知字段诊断逐键记录哈希、层级、值类型和键数；只显示固定官方源码确认的公开字段名，不存原始审核载荷、私有字段名或值。若未知键超过 24 个，记录总数及截断标记并保持受限。
4. 模型失败、截断、缺完成事件、解析错误、模型或整轮超时按原规则终止并保留可靠 usage。语音侧失败只停止当前及后续语音，已启动模型可在原预算内完成并按展示规则保存文字。已发送或结果未知的文本不重试、不补连、不补发。整轮授权撤销及用户停止生成按原范围取消。收据占用后不因重启、并发、失败或未知结果恢复额度。
5. 分别报告模型终态与可靠 usage、决定字段存在和解析状态、展示／保存结果、每个未知键的受限结构、实际 TTS 字符／片段／连接／音频字节、首播与完成先后、排空及真人听感。发送返回不等于供应商确认接收。结束后关闭隔离实例、撤销临时浏览器登录会话；对初始化会话单独核验，不把残留哈希视为有效 Cookie。保留收据和脱敏证据；清理受阻时如实记录，不换方法绕过。

## 4. 协议边界、模拟证据与仍可能缺失的信息

固定官方 Codex 提交 `ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f` 的[原始 SSE 提取](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/codex-api/src/sse/responses.rs#L264-L273)传递完整审核对象；[core](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/turn.rs#L3497-L3503)只取 `final_answer_moderation` 作最终回答决定，同时调用 [`emit_turn_moderation_metadata`](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/core/src/session/mod.rs#L4797-L4804)转发原对象。[app-server](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/app-server/src/bespoke_event_handling.rs#L422-L430)继续以 `JsonValue` 通知转发；[TUI](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/tui/src/chatwidget/protocol.rs#L270-L274)将它交给 [safety recourse 解析器](https://github.com/openai/codex/blob/ea1b6ad1cf2c39dacf10bf1f2667d9c6f7e7eb3f/codex-rs/tui/src/chatwidget/safety_recourse/metadata.rs#L53-L64)，后者读取 `safety_block`／`safety_recourse` 并依轮次、终端策略错误决定 UI 行为。因此“core 不读取某字段”不等于“所有客户端可忽略它”。固定公开源码也不能证明所有未知字段没有控制语义；本候选保持未知即限制。

合成原始 SSE 经实际解码、适配器、产品 WS 的受影响专项 **72/72，通过，退出码 0**，涵盖逐键哈希／层级／类型／数量、24 键上限、独立的缺失／show／hide／非法决定诊断、外层及展示层未知键、原有播放和限制行为。历史隔离浏览器的 `generation`＋缺失决定、`generation`＋`hide` 结果适用于此前窄兼容代码；本批没有改变派发／播放分支，产品路由测试使用本批最终解析与诊断代码。上述全是合成输入与本地假供应商，不是 R9 真实流回放。

**事先说明的诊断缺口：**逐键哈希、层级和值类型可用于与以后核实的公开字段名比对，但若字段名称不在公开协议、或同名字段的语义取决于未保留的私有值，它们仍不能证明字段可忽略。超过 24 个未知键时只保留前 24 个哈希和明确截断标记。遇到这两类情形，此单必须停止并保持受限；后续需要权威协议资料，或另行批准仅在隔离环境人工核对最小字段名／结构的专门方案，不能从哈希猜测或自动追加真实请求。

## 5. 单次真实执行结果（2026-11-13）

执行前规范化 manifest、17 文件代码、generation ID、完整上下文、日常库只读取样重算的 Profile revision 3、Voice ID 摘要、告知版本和本机代理均匹配。模型凭据起初已过期；依获批账户额度在独立 18787 入口完成**一次同账户 OAuth 刷新、一次独立模型目录 GET**，两者均成功，目标模型在目录中，发布后的凭据余约 3,526 秒。无额外账户请求或测试生成。新鲜隔离库的零外呼预览与冻结 manifest／代码／上下文匹配，后续才创建 R10 专用收据。实际前端 `.vue` 导入 `./realtimeOutput.ts`，Vite 返回的模块包含该 `.ts` 路径且无旧 `.js` 导入。首个隔离前端进程遗漏候选显示开关；在**收据未占用、真实请求为零**时仅重启 15188 前端并设置既定隔离显示开关，无源码或冻结绑定变化。隔离后端 18786 的实际库为 `C:\Users\ASUS\AppData\Local\Temp\yosaki-v4-candidate-tcbj33ly\data\yosaki.sqlite3`；日常库仅被读取样，8000／5173 未切换。

隔离产品页面登录后，候选预览为 `real_approved`；控件只启动一次。持久 `.used` 收据状态为通用保守标记 `occupied_outcome_unknown`，不恢复额度；实际运行结果以独立诊断为准。模型请求尝试 **1 次**，模型终态 `completed`，可靠 usage **138 输入／72 输出／210 合计 token**。自诊断单调时钟起约 **5,219 ms** 见审核事件，约 **5,234 ms** 记录 usage，约 **5,297 ms** 记录模型和展示终态。费用未知，未查询账单。

审核事件在 `moderation_envelope` 层共有 **4 个键**，其中 **3 个本地未识别、值类型均为 `object`**，逐键 SHA-256 为：

```text
4415b797b463e5f1b770f10e0b5d7b608a465c4571e705819b76066ddde0fa88
99bd30a8e4c3e58fc1defdf5626b019fc311b42fae21005a5b4b551c2442ee54
cf07194ee232eb531e15f690000d19846dea69cf05504782658afcfacb9228a2
```

逐键记录未截断；三者都不匹配当前固定官方源码已核实的公开 envelope 字段名。旧整组哈希仍为 `8adb95b764c08d09008456fe40d031e7df09c0eb8b04313d88871acdbd51f8f5`，与 R9 记录相同；这与相同额外键集合一致，**不能据此推定各键名称或控制语义**。`final_answer_moderation` 为 `field_missing`，独立已知决定解析为 `absent`／`optional_decision_absent`，未伪造 `show`。由于未知字段存在，展示终态为 `restrict`／`moderation_field_unrecognized`，回答绑定标记 `not_checked_restricted`；不能宣称绑定已通过。文字未发布，隔离库只有一条本轮用户消息、**无助手消息保存**。TTS **0 字符／0 片段／0 条 WS／0 次 HTTP／0 音频字节**，无首次播放、接收完成或播放排空；真人听感未测。语音停止记录里的 `generation_incomplete`／`user_stop_voice` 是展示受限后本地停语音清理标签，不代表模型未完成或用户额外点击停止。

浏览器已显示登录页并关闭隔离标签。浏览器登出后，隔离库 `auth_sessions` 从 2 行降至 **1 行**；剩余初始化会话摘要检查时未到期，余约 **167.94 小时**。服务端还要求匹配 Cookie 才会接受，留有摘要不能证明 Cookie 仍被持有，也不能写成“全部会话已撤销”。18786／15188／18787 均已关闭；日常 8000／5173 仍为原 PID 10180／20856。日常 Voice Profile revision 3、active、Voice ID 摘要与告知版本经只读复核未变。R10 临时隔离数据库目录仍存在，未覆盖或删除；OAuth 暂存目录已清理。持久收据及 **42 条受限诊断记录**保留在 `%LOCALAPPDATA%\YOSAKI\voice-runs\voice-v4-dynamic-20261113-r10\`。既存 R2～R9 证据与结论未改。

**结论：**本次取得可定位的三条逐键结构，模型完成但展示条件不满足，动态语音验收未通过。现有公开协议资料不足以说明三键语义；继续维持文字与 TTS 限制。不自动追加请求，不冻结下一轮真实重试。后续若需辨认字段，须先取得权威协议依据，或另行审阅仅针对最小字段名／结构的本地诊断方案；本次没有保存私有名称或原始审核载荷。
