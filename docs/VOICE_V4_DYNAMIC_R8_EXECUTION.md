# V4 动态桥接 R8：单次真实验证执行单

日期：2026-11-13。状态：**最终零外呼候选，尚未批准真实派发；未创建 R8 一次性收据。** 本单只用于下一次单独授权。R2～R7 的已占用收据及历史结果不复用、不改写。

## 1. 冻结身份与请求

| 项目 | 冻结值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r8` |
| generation ID | `e1345e9edd1ebe48042045ff3767013a` |
| [完整 manifest 与模型请求](VOICE_V4_DYNAMIC_CANDIDATE_R8_MANIFEST.json) 的规范化 manifest SHA-256 | `2ef000f62cc90049c9b1f33bb4d566bfc593eb5558f020aba1e49e3cc5fa9373` |
| manifest 文件字节 SHA-256（辅助核对） | `4f6e69ac10fe1bb170fc143439decfb6e8068ec3b9285790cf9eb580530170e9` |
| 完整 17 文件运行代码集合 SHA-256 | `12ae895ce52920ee487ef738be808b11968cf1a815317e12beff38282a8c4255` |
| 完整模型上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256，仅用于比对，不公开 ID | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

规范化 manifest 按 `sort_keys=True`、无多余空格的 UTF-8 JSON 求 SHA-256；代码集合按 `realtime_dynamic_candidate_receipt.CODE_PATHS` 的顺序，将每个 `路径 空格 文件字节哈希 换行` 拼接后再求 SHA-256。完整当前文件指纹如下。任何一项变化均使本候选失效，不能在执行现场改码或重算指纹继续。

| 运行文件 | SHA-256 |
|---|---|
| `backend/app/main.py` | `80419923536aa7aa4674da3bf3ac154775f2352c407209dbe5f147969b3c3c0d` |
| `backend/app/chatgpt_plan.py` | `927c670fbf8f22e42f794fd70d9d0a7c1b371ab57010774cefc78364cfd2ae2b` |
| `backend/app/responses_adapter.py` | `f6bd9b6e233746754d2fa4d65823c9ff89f5ade1c8af2579d4301adc2b5a4f8f` |
| `backend/app/voice/realtime_dynamic_context.py` | `56c947c3e10c4aa393e894e6dd1f47c7f3edb427496e4539fc86672fc389b022` |
| `backend/app/voice/realtime_dynamic_candidate_receipt.py` | `385f0d0631040bb2b4f478058ccb0c32208da8aff734d3450d087641d283adbe` |
| `backend/app/voice/realtime_dynamic_candidate.py` | `88c0b4e10a95966d9e6ea5daa8a2c51f0a32bac0fa2a5d5a16f9a86a1f273d58` |
| `backend/app/voice/realtime_dynamic_diagnostics.py` | `ee56b49084bfe73c444881a9aa0425d17b4f275d12082d9137e4a782df96d2c1` |
| `backend/app/voice/realtime_answer_display.py` | `8fc9a7d95f896965974b9b1fe0a1033c7e74bbbe49e8eb2f35867801aa4f5032` |
| `backend/app/voice/realtime_chat_bridge.py` | `a14969f23219048fa68ca2a3b3817b1ef345f2183014be133e834570107d57a1` |
| `backend/app/voice/realtime_output.py` | `8c69ccc98565936b3002cebeb027e8db82106bdd8f96d2f4a84afda27c045571` |
| `backend/app/voice/realtime_ttd_connector.py` | `766a0871239b72fbdfa51fdb8c8c3d9d9f6f0e8b268a2099957987f10a85009c` |
| `backend/app/voice/readable_text.py` | `ef2ceb0bd0293faff50c5d2ed7054b2d4c329db281db44a7239015bcfda73e94` |
| `frontend/src/voice/realtimeOutput.ts` | `2da7325a66a3aaf54795bfcdc40b39846e81396633ffafdbb6c8b6a1bb543b73` |
| `frontend/src/voice/DynamicChatOutput.vue` | `ffbde30b9c0328a239b528d228123399294cecd8f77550dcfb9ffaa8cc3ce7a3` |
| `frontend/src/App.vue` | `c2d734a4fe74c0ca077ee51663536ff307b56e6496476f1f21c4cd93830f8b72` |
| `scripts/voice_v4_dynamic_candidate_isolated.py` | `e65c584739294cad506e6af59d95b7b716386fedb4262e0eea1c639472f485d5` |
| `scripts/voice_v4_dynamic_candidate_approve.py` | `4c7994d27df1d7a35531985b1704a161b7da39520a8836094cf1ee2f2c397894` |

R8 与 R7 的 manifest **只有**运行编号和 generation ID 不同；`model_request`、身份、上下文、Voice ID 摘要、Profile revision 3、告知版本 `2026-11-08-v1`、代理 `http://127.0.0.1:7897` 及预算完全相同。R7 至本次零外呼修复仅修改上述运行集合中的 `realtime_answer_display.py` 和 `realtime_dynamic_diagnostics.py`；新增测试与主台账记录不在运行集合内。R7 留有集合哈希但没有逐文件表，因此不能用旧集合哈希单独证明其余文件逐字节相等；R8 已将全部当前字节重新列明。

模型供应商为现有 ChatGPT Plan 档案 5 的 `gpt-6.1-sol`，请求为 `POST https://api.openai.com/v1/responses`。请求体固定包含 `model=gpt-6.1-sol`、`input=[{role:user,content:<下方 User 全文>}]`、`instructions=<下方 System 全文>`、`store=false`、`stream=true`、`max_output_tokens=128`；不发送工具、聊天历史、记忆、摘要、欲望建议或独白。档案 `temperature=0.8` 和 `top_p=1.0` 不进入本次实际请求体。

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

ElevenLabs 只接收**这轮模型生成、完成且确认可展示后，经既定过滤的动态公开助手正文**。调用原 IVC Voice ID、`eleven_v4_turbo`、`pcm_24000`（24 kHz、16-bit 单声道）、`language_code=ja`。TTD WS 端点为 `wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input`；连接注册 `{"voices":[<原 Voice ID>]}`，每片段发送 `{"inputs":[{"text":<过滤后片段>,"voice_id":<原 Voice ID>}],"flush":true}`。供应商凭据仅由后端读取，不写入 manifest、前端或诊断。

## 2. 请求预算、门控与停止规则

| 独立限额 | 本次上限 |
|---|---|
| 模型 | 1 次生成；128 输出 token；30 秒 |
| ElevenLabs | 80 个实际请求字符；2 片段；2 条 WS；0 次供应商 HTTP；累计音频 1 MiB |
| 时间 | 每条 WS 30 秒；整轮语音接收 45 秒；接收后播放排空 30 秒；整轮墙钟 110 秒 |

本地限额不是费用硬上限；两侧实际扣费或未返回的用量保持未知，不填零。**R8 当前真实派发额度为 0**，直至用户单独批准并签发仅 R8 有效的一次性收据。

1. 执行前复核 manifest 及完整 17 文件代码哈希、实际 `.ts` 加载路径、owner 1／角色 1／对话 5／模型档案 5、完整上下文、Profile revision 3、Voice ID 摘要、告知状态与版本、代理及两侧凭据。ChatGPT Plan 凭据须至少剩余 90 秒；任何漂移即停止，不改冻结请求。无批准收据不得启动。
2. 获单独批准后，才允许在独立私有收据目录创建 R8 `real_approved` 收据；首次模型请求前原子占用。重启、并发、取消或结果未知都不释放额度，R7 `.used` 不复用。
3. 从**新建的最小隔离 SQLite**、单 worker 后端 `127.0.0.1:18786` 和只代理它的前端 `127.0.0.1:15188/voice` 进入隔离产品控件，经实际登录鉴权 WS、relay 与浏览器播放器执行。日常 `data/yosaki.sqlite3` 只读选取身份与配置；日常 8000／5173 不停止、不切换。
4. 模型必须明确 `response.completed`、流 EOF、最终回答 ID／全文对账通过，并取得可履行的 `show` 决定，才发布正文与启动 TTS。这是**生成完成后流式合成播放**，不是边生成边播。缺少许可、`hide`／`block`、未知版本、不支持的附属要求、格式或绑定错误均限制文字展示，TTS 文本派发为 0。不能凭暂时未收到限制就推定允许。
5. 模型失败、截断、SSE 解析错误、缺完成事件、模型或整轮超时：保留可靠 usage 和安全诊断，停止对应任务，不能按完成入库或送读。语音自身失败、断线、未知结果或预算耗尽：停止本轮当前与后续语音，隔离旧音频；已启动的模型若仍在预算内可完成并按展示规则保存文字。已尝试发送的文本不重试、不补连、不补发，也不重生聊天回复。用户停止生成或撤销整轮授权时按范围终止模型与语音。
6. 诊断记录失败阶段、模型终态与可靠 usage、展示状态、文字保存、片段来源／字符／哈希、连接与本地发送阶段、音频字节、浏览器首播和排空。**本地发送返回不表示供应商确认接收。** 若审核再次受限，报告固定字段路径、预期／实际类型、必要字段存在位、限定版本与安全决定枚举、回答绑定结果；未知字段只报哈希，不留载荷、正文、推理或鉴权。语音结果和展示结果分别记录。
7. 完成后停止隔离服务、撤销临时登录会话，保留收据与脱敏证据；R7 临时目录清理仍属独立受阻事项，不改用其他方法绕过。软件播放和真人听感分别报告。

必要时，同账户已有 OAuth 续期入口可执行授权刷新，并伴随**最多一次模型目录 GET `/models`**；这属于独立的账户请求，不计入上表的一次模型生成或 ElevenLabs 数量。凭据足够有效时不做刷新或目录查询。需要交互登录时由用户自行操作；不换账户、不撤权、不输出 token，不额外请求模型来测试凭据。若续期改变冻结身份或请求绑定，停止而不自动更新 manifest。

## 3. 已复用的零外呼证据与最终一致性

§0.32 的受影响后端组合 **62/62**、后补单项 **1/1** 均通过；二者均针对本次相同的展示门控 `8fc9a7d95f896965974b9b1fe0a1033c7e74bbbe49e8eb2f35867801aa4f5032` 和诊断模块 `ee56b49084bfe73c444881a9aa0425d17b4f275d12082d9137e4a782df96d2c1`。新增的只是测试反例，无运行源码变化。隔离浏览器产品入口使用无外呼能力的假模型和假 TTD：`show` 可保存文字、2 段本地音频播完；`hide` 不发布正文，0 次 TTS 文本发送。模拟诊断位于 `%LOCALAPPDATA%/YOSAKI/voice-runs/r7-local-diagnostic-20261113/`；不是 R7 的原始审核载荷或真实重放。没有变化的模拟与浏览器验收没有在 R8 冻结时重复运行。

本次只读连接日常库的 `PRAGMA database_list` 与目标路径相符；角色／对话／模型档案、启用提示块、Voice ID 摘要、revision、告知和由当前行构造的完整模型请求与 R7 冻结内容一致，重算上下文 SHA-256 也一致。当前代理与 R7 一致。R8 manifest 经现行校验器验证；冻结后再次核对文件哈希与内嵌摘要。没有创建收据，没有调用模型、账户目录、OAuth 或 ElevenLabs。R7 原始审核结构缺失，仍无法判断当次具体失败字段；本候选具备下次失败时可定位的安全诊断，**不等于 R7 根因已确定或真实路径已经通过**。
