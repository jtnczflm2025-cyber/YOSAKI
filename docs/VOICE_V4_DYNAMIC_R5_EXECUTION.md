# V4 动态桥接 R5：零外呼冻结候选

日期：2026-11-13。状态：**历史零外呼候选，已由 R6 替代，不得按此指纹创建收据或派发。** R5 真实收据未创建，真实模型与 ElevenLabs 请求均为 0。后续源码审计发现 metadata 可能携带输出或访问控制，修复及新指纹见 [R6 执行单](VOICE_V4_DYNAMIC_R6_EXECUTION.md)。R4 收据已占用，不可复用；R2～R4 原始观察和收据不改写。

## 1. 本轮修复依据与适用范围

R4 私有诊断确认：公开正文增量之后，原始供应商 SSE JSON 中出现 `type=response.metadata`；冻结适配器将它送入 `unsupported`，导致本地 `model_event_unknown`。R4 只保留安全事件类型、来源层和解析分支，没有保存该事件载荷；其具体内容和供应商当轮后续终态无法恢复。**R3 的未知事件类型也不能由 R4 倒推。**

[Responses 公共流事件参考](https://developers.openai.com/api/reference/resources/responses/streaming-events)列明 `response.completed` 等明确终态，但未列 `response.metadata`。[OpenAI Codex 的 Responses SSE 实现](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/sse/responses.rs)认识 `response.metadata`，把它作为模型验证、审核、保护等附加信息的载体，并将正文与完成／失败放在其他事件分支。由此只对本候选增加**一个明确事件类型**的兼容：它不产生公开正文、语音片段、终态或可靠 usage；继续等待明确的完成／失败／截断事件。该处理不解释或认可 metadata 载荷中的任何安全结论，也不放宽其他未知类型。

相对 R4 的 **16 个运行文件只改变 `backend/app/responses_adapter.py`** 的候选通知白名单；`StreamState.normalize`、普通聊天调用路径、模型上下文、Profile、预算和产品 UI 均未改变。合成测试与浏览器假供应商脚本另有测试性修改，不纳入真实派发代码集合。

## 2. 冻结身份、请求与文件指纹

| 项目 | R5 冻结值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r5` |
| generation ID | `31718d0a744d44119ed98616b7bb7156` |
| [规范化 manifest](VOICE_V4_DYNAMIC_CANDIDATE_R5_MANIFEST.json) SHA-256 | `949d72b644dc3dbf78d459abb75570170db65b713f082cbe02936cda4b6a2045` |
| 完整 16 文件运行代码集合 SHA-256 | `8b8897cf9fa24d74359cfbbde501a5f390b16a1bda2674fff791613dd5b54aca` |
| 完整模型上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256 | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

文件指纹依 `realtime_dynamic_candidate_receipt.CODE_PATHS` 顺序，取原始文件字节 SHA-256，再对每行 `路径 空格 哈希 换行` 求集合 SHA-256：

| 运行文件 | SHA-256 |
|---|---|
| `backend/app/main.py` | `80419923536aa7aa4674da3bf3ac154775f2352c407209dbe5f147969b3c3c0d` |
| `backend/app/chatgpt_plan.py` | `927c670fbf8f22e42f794fd70d9d0a7c1b371ab57010774cefc78364cfd2ae2b` |
| `backend/app/responses_adapter.py` | `a70950161728ea51a6e4fee181b7e28ab2dcaeb43b9742c5e3f64bab39da53d9` |
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

规范化 manifest 是按键排序、无多余空格的 UTF-8 JSON 摘要，不等于 manifest 文件本身字节哈希。R5 与 R4 的 `model_request` 完全相同；manifest 只改变 `run_id` 和 `generation_id`。

模型供应商：本机 ChatGPT Plan 档案 5 的 `gpt-6.1-sol`，`POST https://api.openai.com/v1/responses`。请求体仅包含 `model=gpt-6.1-sol`、`input=[{role:user,content:<User 全文>}]`、`instructions=<System 全文>`、`store=false`、`stream=true`、`max_output_tokens=128`；无工具、历史、记忆、摘要、欲望建议及独白。档案中的 `temperature=0.8` 与 `top_p=1.0` **不进入本次请求体**。

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

ElevenLabs 仅接**动态生成且经既定过滤的公开助手正文**，每段派发前核对来源及累计预算。使用原 IVC Voice ID、Profile revision 3、`eleven_v4_turbo`、`pcm_24000`（24 kHz、16-bit 单声道）、`language_code=ja`；连接注册 `{"voices":[<原 Voice ID>]}`，每段 `{"inputs":[{"text":<过滤后片段>,"voice_id":<原 Voice ID>}],"flush":true}`。不把 Key 放入 manifest、浏览器或日志；本机代理沿用 `http://127.0.0.1:7897`。

| 独立限额 | 数值 |
|---|---|
| 模型 | 最多 1 次生成、128 输出 token、30 秒 |
| ElevenLabs | 最多 80 个实际请求字符、2 片段、2 条 WS、0 次供应商 HTTP、累计 1 MiB 音频 |
| 时限 | 每条 WS 30 秒；整轮语音接收 45 秒；接收后浏览器排空 30 秒；整轮墙钟 110 秒 |

本地限额不是供应商费用硬上限；未知用量或账单必须留为未知。

## 3. 停止规则、授权及隔离入口

- 语音失败、未知事件、断线或超语音预算：停止当前和后续语音并隔离迟到音频；已尝试文本不重试、不补连、不补发。已启动模型可在原预算内完成并保存文字。
- 模型明确失败、截断、解析错误或 SSE EOF 缺 `response.completed`：按对应终态保存已观察正文及可靠 usage，停止语音；不能凭收到正文宣称完成。
- 用户停止语音只停播放与语音任务；用户停止生成同步取消模型和语音，部分公开正文按原规则保存。整轮超时与整轮授权撤销终止对应任务。诊断失败须留下不完整标记，不能突破原有期限。
- 验收分别报告模型终态、可靠 usage、文字保存与片段来源对账、供应商连接和文本尝试、音频字节、软件首播和最终排空。软件首播不等于人耳听见。

**当前未获 R5 真实派发授权，未创建本运行的真实批准收据。** 后续若单独批准，派发前逐项复核四项绑定摘要、16 文件实际字节、实际 `.ts` 加载路径、owner 1／角色 1／对话 5／档案 5、Profile revision 3、告知版本 `2026-11-08-v1`、Voice ID、代理和 ChatGPT Plan 凭据剩余至少 90 秒。任何漂移即停，不现场改码、换模型或更新 manifest；R2～R4 已用收据不能复用。真实收据须在首次模型请求前原子占用，重启、并发或结果未知均不恢复额度。

最终产品入口沿用新临时隔离库、单 worker 后端 `127.0.0.1:18786` 与仅代理它的前端 `127.0.0.1:15188/voice`。日常库只读取样；通过隔离产品登录、真实控件、鉴权 WS、relay 与浏览器播放器执行。结束后关闭隔离服务与临时登录，保留脱敏诊断及收据，不影响日常 8000／5173。

如果届时凭据过期，同账户既有续期页的 `/refresh` **主动执行 OAuth 刷新和模型目录 GET `/models`**，不是旧缓存，也不属于上表那一次模型生成额度或 ElevenLabs 额度。续期所需的账户请求应独立列入届时授权与记录；本轮没有调用续期页或模型目录。

## 4. 零外呼验收与未决事实

- 合成原始 SSE 经 `httpx` 分块、实际 SSE 解码、候选分类与产品 WS：正文 → 两次 `response.metadata` → `response.completed`（携可靠 usage），正文不重复、metadata 不送读、不覆盖 usage；metadata 后明确 `response.failed`、`response.incomplete`、EOF 缺完成、其他未知事件均保留原停止规则。专项 **5/5**；受影响 Voice 候选、诊断及产品测试 **57/57，退出码 0**。
- 共享适配器的普通 Responses／ChatGPT Plan 受影响回归 **2/2，退出码 0**；`StreamState.normalize` 未改。前端源代码未变，不以旧构建冒充本轮新构建。
- 隔离产品浏览器经实际控件、鉴权 WS、relay、假 TTD 和播放器显示消息 2 `completed`、2 片段／2 连接、音频接收及播放队列排空；本机合成首个非零样本进入播放链约 102.8 ms。私有合成诊断 37 条，可靠**模拟** usage 15/15，26 个送读字符、9,600 音频字节、`browser_playback_ended=true`；证据保存在 `%TEMP%\yosaki-v4-r5-browser-evidence\normal\normal.diagnostic.jsonl`。这是模拟软件观察，不代表真实模型、真实 TTD 或真人听感。浏览器已退出，假服务已停止。
- R4 真实 `response.metadata` 载荷未留，无法核验当次元数据具体含义；R4 未观察到完成和 usage。R3 的未知事件仍未知；历史空闲 TTD 原因仍未知。R4 模型派发及 WS 连接尝试、已用收据保持原记录，不能把本轮合成通过追认为 R4 成功。

本轮只做代码兼容、合成测试与冻结候选；未创建 R5 真实收据，未请求模型、ElevenLabs、OAuth 或账户目录，未切换日常服务、迁移数据库、修改 Profile 或 Memory。
