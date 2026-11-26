# YOSAKI Voice V1-B — 真实 API 验收 dry-run 测试单

日期：2026-11-08（Asia/Hong_Kong）  
本地探针与账户截图更新：2026-11-10（Asia/Hong_Kong）；固定请求清单、文本和时长未改。  
最新状态：**r4 Scribe 单项补测已按单次授权完成，收据已使用；当前追加真实请求额度为 0。**r3 空闲 TTD 仍未完成。第 1～11 节保留各阶段当时的准备、执行和诊断记录；r4 结果见第 12 节。

Windows 用户本地凭据与 Voice ID 引导见 `docs/VOICE_V1B_LOCAL_SETUP.md`。用户须自行输入 Key；配置完成、后端重启和本地布尔检查均不构成真实测试授权。

本清单依据 `docs/VOICE_SYSTEM_PLAN.md` V0.3 第 6.5 节。初次准备阶段仅复核本地配置布尔值、Development 日常库的非敏感档案字段及公开文档；后续 r2 单次真实运行及本地补测准备按第 9 节单独记录。未读取或输出密钥值，没有导入会迁移数据库的主应用。

## 1. 当前配置及执行门槛

| 项目 | 只读结果 | 对测试的影响 |
|---|---|---|
| 实际 Voice ID | Development 日常库已有阿夜 `character_id=1` 的 IVC 档案；只显示遮蔽值 `mYj...krd`，`provider=elevenlabs`、`revision=1`、`active=false`；用户确认刷新后仍可读取 | 保留已保存的 ID，不重复 PUT 或增加 revision；不使用 `synthetic_voice_id` |
| ElevenLabs API Key | 本地后端先前返回 `api_key_configured=true`；终端也仅核对到 `configured=true` | 只记录布尔值，不输出 Key；用户已确认 Key 与 IVC Voice ID 同属下述 Workspace，Scribe 权限仍未验证 |
| 账户套餐/积分 | 用户在 r2 前提供的 **ElevenAPI** 订阅截图：Starter、当时已用 **5,432／40,000** 积分；当时剩余 **34,568**。工作区名称 Sigurd's Workspace 由用户报告；没有账户 API 实查 | 截图是当时的账户页面证据，r2 后余额未知；不把各产品“包含用量”相加，也不从积分余额推断单项实际扣额 |
| 语音与后台任务开关 | Development 已验收 `YOSAKI_VOICE_ENABLED=0`、`YOSAKI_BACKGROUND_TASKS_ENABLED=0` | 保持关闭；不靠开启日常服务运行探针 |
| 第三方处理告知 | 正式库 `voice_privacy_consents` 为 **0 条**；关闭状态下本地 `/privacy/consent` 门控拒绝写入 | 不自动确认或修改；真实探针须另获用户主动确认，见第 6 节 |

上述任一必需项未满足，真实探针不得派发。独立 `scripts/voice_provider_probe.py` 已实现、默认 dry-run；正式 `POST /api/voice/profiles/{id}/test` 仍固定拒绝真实请求。本清单不改变该产品门控。

## 2. 固定输入与请求清单

字符数按 Unicode 码点计算，包含标点与空格；以下文本无空格、无 Audio Tag，发送前按相同编码复核。阿夜使用 ElevenLabs **Instant Voice Cloning（IVC）**，参考录音主要语言为日语；所有测试使用已保存的同一个 IVC Voice ID，不使用 Voice Design 或公共声音库，不测试英语；无自动换音色、换模型或添加文本。克隆来源由用户在官方平台确认；若现有元数据响应不能证明来源，不为验证来源新增 API 请求。

| 顺序 | 请求及固定输入 | 精确字符数 / 次数 | 音频秒数 | 单次时长上限 |
|---|---|---|---|---|
| 1 | `GET /v1/models`，核对 `eleven_v4`、`eleven_v4_turbo`、`scribe_v2_realtime` 目录与账户权限 | 0 字符；1 次 HTTP GET | 输入 0；输出 0 | 15 秒 |
| 2 | `GET /v1/voices/{id}` 查询已保存 Voice ID 的元数据与使用权限 | 0 字符；1 次 HTTP GET | 输入 0；输出 0 | 15 秒 |
| 3 | `eleven_v4` 中文 HTTP TTD：**你好，阿夜。现在请用自然的语气读出这句话。**；请求 PCM 16 kHz | **21 字符；1 次** | 输入 0；输出时长未知，收到后按 PCM 样本数记录；超过 10 秒作为验收失败 | HTTP 30 秒 |
| 4 | `eleven_v4` 日语 HTTP TTD：**こんにちは、阿夜。自然な声でこの文を読んでください。**；请求 MP3 | **26 字符；1 次** | 输入 0；输出时长未知，解码后记录；超过 10 秒作为验收失败 | HTTP 30 秒 |
| 5 | `eleven_v4_turbo` TTD WebSocket，PCM 24 kHz，单连接依次发送两段：① **你好，阿夜。我们开始测试实时语音。** ② **今夜もよろしくお願いします。**；按官方协议验证 flush、`keep_alive`、最终 turn 标记及关闭 | **17 + 14 = 31 字符；1 条连接、2 个文本片段** | 输入 0；输出时长未知，按 PCM 样本数记录；累计超过 12 秒作为验收失败 | 连接最长 55 秒；片段串行，保活跨过官方 20 秒空闲窗口 |
| 6 | 独立 TTD WebSocket 连接，首帧注册后不发送合成文字，也不发送保活，观察服务商空闲关闭 | **0 字符；1 条连接** | 输入 0；输出 0 | 连接最长 30 秒；服务商若未按文档关闭则记录差异并本地关闭 |
| 7 | `scribe_v2_realtime` WebSocket，发送下述本地合成 WAV 的 16 kHz、16-bit、单声道 PCM 数据，检查 partial、final 与格式 | **0 文本字符；1 条连接、1 段音频** | **输入 5.805 秒**；输出 0 | 连接最长 20 秒；音频最多发一次，不补传 |

合计：**78 个合成文本字符**，4 次 HTTP（2 次元数据、2 次 TTD）、3 条 WebSocket 连接、2 个 Turbo 文本片段、1 段 5.805 秒 STT 输入音频。HTTP 单项期限合计 90 秒，WebSocket 连接期限合计 105 秒；全部串行时本地总墙钟预算至多 195 秒，另加固定本地整理时间。任何超额或结构不匹配立即停下，不扩大请求数。

**公开协议复核：**[模型目录](https://elevenlabs.io/docs/overview/models)和[模型选择指南](https://elevenlabs.io/docs/eleven-api/choosing-the-right-model)仍列出 `eleven_v4`、`eleven_v4_turbo`、`scribe_v2_realtime`；[HTTP TTD](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert/)与[TTD WS](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket)仍对应上表路径，[Scribe Realtime 指南](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/transcripts-and-commit-strategies)确认 `pcm_16000` 为 16-bit little-endian 单声道输入。HTTP TTD 默认 `mp3_44100_128`，明确要求高等级的是 `mp3_44100_192`（Creator 起）及 44.1 kHz PCM/WAV（Pro 起）；固定单使用 `pcm_16000`、`pcm_24000` 和普通 MP3，未要求受限高质量格式，但 Starter 实际权限仍需账户验证。固定单中“MP3”在实现前必须明确锁为默认 `mp3_44100_128`，不改变字符或请求数。[官方 WS 指南](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd)说明 20 秒空闲断开和 `keep_alive`；短文本可能需要 `flush`。网页文档对 v4 可注册声音数量存在不同表述，本单只注册**一个** Voice ID，不依赖该差异。[v4 公告](https://elevenlabs.io/v4)提示早于 v4 发布的 IVC 可能需要更新才能有效使用；本单不自动重训或更换已绑定音色。

**IVC 声音验收矩阵（使用上表同一次请求，不增加调用）：**日语（ja）是克隆音色基准；中文普通话（zh）是跨语言测试。下面的顺序号只引用已冻结的请求，不改变派发顺序或连接期限。

| 模型 | 日语基准 ja | 中文跨语言 zh |
|---|---|---|
| `eleven_v4` | 顺序 4 的 26 字符，单独评估 | 顺序 3 的 21 字符，单独评估 |
| `eleven_v4_turbo` | 顺序 5 第 2 段 14 字符，单独评估 | 顺序 5 第 1 段 17 字符，单独评估 |

四格分别记录该 Voice ID 与模型组合是否可用，并按 1～5 分人工听感记录音色辨识度、发音自然度、节奏与韵律、表达一致性、音频质量；同时记下口音、语速、音调稳定性及明显失真，必要时标注字词和音频时间点。日语基准优先检查音色、口音、语速、韵律和自然度；中文跨语言优先检查发音清晰度、自然度及基础音色能否辨识。每格标注“通过/失败/证据不足”，不能以中文推定日语、以 v4 推定 Turbo，或用两段合并评分掩盖某一语言问题。

本单只含四个短文本片段，足以发现明显兼容或质量问题，**不足以直接判断 IVC 克隆的整体质量**。主观听评保留评测人、环境、参照说明及音频指纹；没有可用参考录音时音色辨识度记为“证据不足”，不编造分数。人工 1～5 分不是客观声纹相似度或相似度百分比。具体维度与后续固定语料见 `docs/VOICE_BENCHMARK.md`。若一格证据不足，不自动新增请求；提出新的固定测试单。

Scribe 仅评价识别链路、事件格式、最终转写和所发 5.805 秒音频的识别表现。它的转写准确度**不参与**四格 TTS 克隆音色质量评分；同一音频样本也不当作克隆音色参考录音。

**Scribe 测试音频：**`tests/fixtures/voice_v1b_scribe_zh.wav`，本轮使用 Windows 本地 `Microsoft Huihui Desktop` 语音、通过 `System.Speech` 离线生成，原始文字为“你好，我是阿夜。现在开始语音识别测试。”（19 字符）。WAV 为 16,000 Hz、16-bit、单声道，92,880 帧、**5.805000 秒**，文件 185,806 字节，SHA-256 `7f4323a61dbf8ceb939837a634f76dee8378fbc432d97f7aeafa59a95fd39952`。执行前复核哈希并只传 PCM 音频帧；此文件不是用户麦克风录音，也不含私人语音。最终转写可能与合成原文不完全一致，按实际事件与内容评审。

## 3. 账户页面证据、标价与账单疑点

用户提供 2026-11-10 的 ElevenAPI 订阅及计价截图（`20261110-25502.jpg`、`25512.jpg`、`25529.jpg`）：**Starter，已用 5,432／40,000 积分，页面剩余 34,568**；同页列出 v4 **US$0.08／1,000 字符**、v4 Turbo **US$0.04／1,000 字符**、Scribe v2 Realtime **US$0.39／小时**。工作区名称 **Sigurd's Workspace** 由用户报告，截图没有证明本地 Key 与已保存 IVC Voice ID 均属于该 Workspace。公开 [API 定价页](https://elevenlabs.io/pricing/api)列出相同模型标价；本单以用户账户页面为当前价格证据，但没有查询实际账户 API 或账单。

| 固定输入 | 线性标价计算 | 参考值 |
|---|---:|---:|
| v4 47 字符 | `47 × 0.08 / 1000` | US$0.00376 |
| v4 Turbo 31 字符 | `31 × 0.04 / 1000` | US$0.00124 |
| Scribe Realtime 5.805 秒 | `5.805 × 0.39 / 3600` | US$0.000628875 |
| **合计** | 固定 78 字符、5.805 秒 | **US$0.005628875，约 US$0.00563** |

上述数值只是**按截图标价线性计算**，不是实际扣款、积分换算或供应商硬上限。截图另显示 v4、Turbo、Scribe 各自的“包含字符数／小时数”；这些是产品栏位，不相加为账户总积分，也不能据此确定这 7 项请求会怎样扣减余额。此前“约 110 credits”及“拟议 200 credits”均撤下，不再作为预算。当前探针只记录实际派发字符、音频秒数、请求与连接次数、输出音频字节；真实 billed credits/USD 保持 `null`，待账户账单证据核对。

| 仍待确认 | 当前处理 |
|---|---|
| 本地 Key 与阿夜 IVC Voice ID 是否都属于 Sigurd's Workspace | 用户在 ElevenLabs 页面自行核对，不增加账户 API 请求，不显示 Key 或完整 Voice ID |
| 账户积分与 API 美元标价的实际结算关系 | **未知**；既不推定必然另行美元扣款，也不把美元标价擅自换算为 credits |
| 最低计量和舍入、空闲 TTD WS、元数据 GET、失败/未知请求是否计费 | **未知**；不能由线性值推出费用硬上限；[STT 说明](https://elevenlabs.io/docs/overview/capabilities/speech-to-text)确认发送的静音也按时长计量 |
| 当时的真实调用授权（初次 dry-run） | **0 次 HTTP、0 条 WS、0 次合成**；截图和余额不是授权 |

单独批准真实测试时，明确认可的费用条件须与**可强制执行的输入／请求／时间上限**分开记录。探针批准收据中的 `reviewed_linear_reference_usd=0.005628875` 只确认审阅过上述参考标价，不能限制供应商实际账单；真实计费不明时由用户决定是否接受该不确定性，不能在代码或报告中伪称硬封顶。

## 4. 停止、失败及未知结果

- 所有真实请求默认关闭；测试脚本和服务不在启动、保存档案或点击普通设置时自动探测账户。
- 真实执行若获批准，HTTP/WS/SDK 自动重试均设为零；不自动更换模型、Voice ID、输出格式或文本。
- 401/403/429、额度不足、模型/音色不可用、响应格式错误、音频超过验收界限时停止对应及后续付费测试，记录实际返回，不尝试替代请求。
- 超时、断线、取消或未收到最终事件时把已派发请求记为 `unknown`，计费预留不返还，不重发文本或音频；关闭连接不等于取消已发生费用。
- 元数据读取不得输出 Key；响应、日志、报告和测试文件均不得保存 Key、Token 或原始供应商鉴权帧。
- TTD WebSocket 的 20 秒空闲、`{"keep_alive": true}`、flush 和最终标记依据[官方实时指南](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd)及[TTD WebSocket 参考](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket)；真实行为若与文档不符，记录差异并停止依赖该行为的测试。

## 5. 探针实现与模拟验收

`scripts/voice_v1b_manifest.json` 固定四段文本、模型、音频格式、Scribe 夹具哈希、4 次 HTTP／3 条 WS、逐项期限及总墙钟 195 秒。`scripts/voice_provider_probe.py` 是独立进程入口：**默认 dry-run 且不读取 Key 或数据库**；真实分支须显式 `--execute-real`，并给出运行编号、只读数据库路径、结果目录和一次性批准收据。收据必须匹配 manifest SHA-256、角色与 Profile revision、Voice ID SHA-256、所有输入/连接/时间限制、本次第三方处理主动确认及计费不确定性确认；批准文件旁的 `.used` 标记阻止同一收据再次使用。未创建此批准收据，本轮不能进入真实分支。

运行时只读查询 `voice_profiles`，要求角色 1 的 ElevenLabs 档案非活动；Key 仅在通过门控后从后端环境变量读取，用于 HTTP/WS 认证，绝不进入结果文件或标准输出。HTTP 使用 `httpx` 无重定向、无代理继承、无重试；WS 依官方 TTD/Scribe 帧格式收发，PCM 按采样数计算时长，MP3 解析帧长。结果目录保存完整/部分音频、事件摘要、实际派发用量和 `unknown` 失败状态，不保存原始鉴权帧或供应商完整响应。元数据、两次 HTTP TTD、一次 Turbo 双片段 WS、一次空闲 TTD WS、一次 Scribe WS 顺序执行；异常立即停止。

隔离模拟测试 `tests/test_voice_provider_probe.py` 使用假 Key、`httpx.MockTransport` 和 **127.0.0.1 本地假 WS**，覆盖正常 78 字符与 5.805 秒、401、429、HTTP 超时、WS 断线、迟到音频、Scribe 中途限流、门控与预算拒绝、一次性批准复用拒绝、零自动重试及结果脱敏。专项 **12/12**，连同既有 Voice V1-A／备份专项 **22/22** 通过；模拟不访问 ElevenLabs。真实协议对短句 `flush` 和 turn final 的实际响应仍待单独授权后验证；模拟通过不能替代真实供应商验收。正式 `POST /api/voice/profiles/{id}/test` 仍固定拒绝，不靠打开产品总开关运行探针。

## 6. 独立执行与用户确认

日常 Development 保持 `YOSAKI_VOICE_ENABLED=0`、`YOSAKI_BACKGROUND_TASKS_ENABLED=0`；真实探针若获**另一次明确授权**，由单独进程执行冻结 manifest，结束后退出。它不启动 Voice 服务，也不调用日常 `/api/voice/profiles/{id}/test`。探针经 SQLite 只读连接获取已保存 Voice ID，在内存中与批准收据的 SHA-256 比较；不在命令行、报告或文件中输出完整 ID，也不修改日常库。

现有 Voice Settings 的正式第三方告知在 Voice 总开关关闭时无法通过 `/privacy/consent` 写入，正式库仍为 0 条。因此在保持开关关闭的探针流程中，必须在执行前由用户**另行主动确认**：将固定的 78 字符合成文本、已保存 IVC Voice ID 及本地 5.805 秒合成音频交给 ElevenLabs，接受账户实际留存规则和获批费用上限。确认仅作用于这一次探针运行；不能伪称正式 `voice_privacy_consents` 已接受，也不能把余额信息当成同意。若产品要求正式告知表先确认，则需要另行审阅最小接口调整，在此之前保持零请求。

用户可在 ElevenLabs 网页中自行检查：当前选择 **Sigurd's Workspace**，并依据自己创建和复制 Key 时的记录确认本机配置的 Key 来自该 Workspace；仅有 `configured=true` 无法证明归属，不能要求显示完整 Key。再在 Voices / My Voices 中检查阿夜的 **IVC**，由用户本地比对完整 Voice ID 与已保存档案（本报告只保留遮蔽值），核对该 Voice 对 v4 的可用状态及 Billing / Usage 的实际 API 扣费方式。若用户无法证实 Key 归属或 v4 可用性，保持“未核实”；不为此增加账户 API 请求。

## 7. 本轮结果及放行条件

本轮本地只读复核：阿夜 Profile `revision=1`、`active=false`，告知 0 条；Scribe 夹具仍为 92,880 帧、5.805 秒且 SHA-256 匹配；四段字符数仍为 21/26/17/14。独立探针编译与模拟专项通过；真实 ElevenLabs HTTP **0**、WebSocket **0**、音频生成 **0**。未修改 Profile、数据库、服务开关或产品告知接口，未开始 V2。

最终默认 dry-run 输出：

```json
{"mode":"dry-run","manifest_id":"yosaki-v1b-fixed-2026-11-10","manifest_sha256":"31fe2c3fe135d9626299ed3280ae2dbe15b8147867f79e0f55d445e57fa6ed8c","real_requests":0,"http_requests_limit":4,"ws_connections_limit":3,"tts_characters_limit":78,"stt_audio_seconds_limit":5.805,"provider_billing":"unverified","status":"no_network"}
```

**真实执行仍待：**用户核实 Key/IVC 同属 Workspace、审阅最低计量和账单不确定性、主动确认本次第三方处理、批准固定运行编号及费用条件。当前不生成批准收据，真实请求额度保持 0。

## 8. 首次真实运行收据与下一次授权范围（2026-11-10）

用户后来确认 Key 与 IVC Voice ID 属于 Sigurd's Workspace，并单独批准运行 `v1b-20261110-041651`。该次只派发 `GET /v1/models`：HTTP 200、约 1.015 秒，目录含 `eleven_v4`、`eleven_v4_turbo`，没有 `scribe_v2_realtime`。旧探针将 Scribe 缺项误判为全局 `models_missing`，因此在第 1 项停止；第 2～7 项均未派发。实际 1 次 HTTP、0 条 WS、0 TTS 字符、0 秒 STT、0 音频文件；供应商实际计费未知。原结果保留在 `C:\Users\ASUS\AppData\Local\YOSAKI\voice-v1b-runs\v1b-20261110-041651\result.json`，同名批准收据的 `.used` 标记仍在，不复用。修复后只读复核：结果文件 SHA-256 `17c2011ba64b02b6b73ea80290fafbc4b5322fb91ef037246ed793027143e0e7`，`.used` 文件 SHA-256 `83384c5ccae44b411c670c721ada20b6008ce724409f905933e44be95dd8c850`。

修订的预检只要求两个目标 **TTS** 模型出现在目录。Scribe 若未列出，事件明确记为 `not_listed_unverified`；其权限与协议可用性只由本清单最后的 Scribe WS 独立判断，不记为已支持。TTS 缺项、401、429、超时仍按原停止规则处理。当时模拟专项 **14/14** 通过，包含目录只有两个 TTS 模型仍继续 Voice ID 与后续请求的反例。r2 执行时 `scripts/voice_provider_probe.py` 文件 SHA-256 为 `832ec45073c5a764f2fedbfac80e5b1d4c789dca17bfbc7c274147b9e6902f68`；manifest **内容未变**，规范化 SHA-256 仍为 `31fe2c3fe135d9626299ed3280ae2dbe15b8147867f79e0f55d445e57fa6ed8c`。

**下一次独立运行重新执行模型目录 GET，不复用上次目录结果。**如获新的单独授权、创建新运行编号与一次性收据，请按第 2 节原序列执行：

| 顺序 | 请求 | 固定上限 |
|---|---|---|
| 1 | `GET /v1/models`；只核对 v4、v4 Turbo，Scribe 缺项标未验证 | 1 次 HTTP，15 秒 |
| 2 | `GET /v1/voices/{id}`，核对已保存的阿夜 IVC Voice ID | 1 次 HTTP，15 秒 |
| 3 | v4 中文 HTTP TTD，原 21 字符、`pcm_16000` | 1 次 HTTP，30 秒 |
| 4 | v4 日语 HTTP TTD，原 26 字符、`mp3_44100_128` | 1 次 HTTP，30 秒 |
| 5 | v4 Turbo TTD WS，原中文 17＋日语 14 字符、`pcm_24000` | 1 条 WS，55 秒 |
| 6 | TTD 空闲连接，0 字符 | 1 条 WS，30 秒 |
| 7 | Scribe Realtime，原 5.805 秒本地合成 PCM | 1 条 WS，20 秒 |

**本次新运行自身**最多 4 次 HTTP、3 条 WS、78 字符、5.805 秒音频，HTTP 期限合计 90 秒、WS 105 秒、全程 195 秒；不是把上次已派发的 1 次 GET 暗中算作本次额度。任一失败或未知立即停止，不重试、不换模型、不补发。下一次真实请求须新授权；本轮脚本修复没有派发任何真实请求，也没有创建下一次批准收据。

## 9. r2 结果、离线诊断修复与最小补测候选（2026-11-10）

用户单独批准的 `v1b-20261110-r2` 已执行并在第 6 项停止。原始结果 `C:\Users\ASUS\AppData\Local\YOSAKI\voice-v1b-runs\v1b-20261110-r2\result.json` 保持不变，SHA-256 `a7b1b6e84259fc0d3a94ed05ca3757e5fb0a09b8ce373c2d0d290f94ae823a9c`；已使用收据 `.approval.json.used` 保持不变，SHA-256 `4be459f65750ab8dce07ce4682ceffed84711d87c096b0ef62e83dfcadcf6256`。以下分类以机器结果为准，不把音频生成成功写成人工音色验收通过。

| 分类 | 分项结果 |
|---|---|
| 成功 | 模型目录 HTTP 200，含 `eleven_v4`、`eleven_v4_turbo`；Voice ID 元数据 HTTP 200，类别 `cloned`；v4 中文 21 字符、v4 日语 26 字符均返回完整音频；Turbo 中文 17 字符与日语 14 字符均收到最终片段标记，连接中有两次 keep-alive |
| 短样本已听评；正式对照待完成 | 用户的无数值主观观察见第 9.1 节；同文本、同设置的日语基准与跨段对照仍待 V2，短句音频不能单独证明整体克隆质量 |
| 未知 | 空闲 TTD WS 收到未预期事件，旧探针没有保留其类型，结果为 `ttd_idle_unexpected_event`／`unknown`；供应商实际扣除的 credits 和美元金额未知；Scribe 权限仍未验证 |
| 未执行 | Scribe WS 未连接，5.805 秒合成音频未发送；其识别结果与计费均无本次实测 |

r2 实际派发：**4 次 HTTP、2 条 WS、78 个 TTS 字符、0 秒 STT 音频**；四份完整音频共 506,661 字节。原 PCM、MP3 与结果文件未改动。三份原 PCM 已额外封装为本地 WAV：`v4_zh_playback.wav` 为 16 kHz，`turbo_zh_playback.wav` 与 `turbo_ja_playback.wav` 为 24 kHz；均为 16-bit 单声道。隔离复核表明 WAV 音频 payload 与对应 PCM **逐字节相同**，未重采样、未重新合成。

离线诊断修复只更改探针与模拟测试：未知 TTD／Scribe WS 事件记录固定字段 `event_type/type_source/error_code/elapsed_seconds`，只允许预定义协议标签和错误码；另记录 `close_code/close_reason_present`，绝不保存自由文本关闭原因、完整帧、鉴权头、Key 或 Voice ID。未知、无效 JSON 和迟到事件仍立即停止，不视为正常事件。假凭据、Mock HTTP 与本地假 WS 专项 **18/18 通过，退出码 0**；本轮没有真实 ElevenLabs 请求。修复后探针文件 SHA-256 为 `b1cb78902dcd3a2679bdd377e503871604bb354da1c4b18bcaf226851cb6952e`，manifest 规范化 SHA-256 仍为 `31fe2c3fe135d9626299ed3280ae2dbe15b8147867f79e0f55d445e57fa6ed8c`。r2 收据不能复用。

**仅供另行审阅的最小补测候选，尚未授权执行：**独立新运行只含①空闲 TTD WS 一条、0 字符、最多 30 秒；若其按协议正常结束，再含②Scribe Realtime WS 一条、固定 5.805 秒本地 PCM、最多 20 秒。上限 **0 次 HTTP、2 条 WS、0 个 TTS 字符、5.805 秒 STT 音频、总墙钟 50 秒**。任何失败、预期外事件或未知结果即停止；不重试、不更换模型、不补发。已成功的四段 TTS **不再生成**。本候选使用 r2 已保存的两项元数据成功结果，并在执行前只读核对 Profile revision／Voice ID 摘要、原结果与音频指纹、当前代理和新探针代码；不需额外元数据 GET。若这些前提有变化，先停止并重新提交范围，不隐含增加 HTTP 请求。实际计费继续保持未知，不把输入限制写成账单硬上限。

### 9.1 用户短样本主观听评

用户已试听 r2 四段音频，主观认为音色像阿夜、发音自然，中文未发现明显问题。两段日语语调差异明显，一段较高、一段相对低沉；整体低沉程度尚未达到用户预期。**目前没有确认高／低分别对应哪一个音频文件**，不得据此比较两个模型或两种传输路径的优劣。本条不填写 1～5 分，也不判定 IVC 整体质量通过；Scribe 尚未执行，不参与此听评。V2 使用既定同文本、同有效设置的对照语料复核日语音高、韵律及跨段一致性；V3 再评估低沉、慵懒表达。当前不改变 Profile、克隆素材或参数，不追加 TTS 合成。

### 9.2 补测入口离线准备

公开 [TTD WebSocket 接口文档](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket)列出错误帧会在连接关闭前发送，[实时指南](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd)说明约 20 秒无客户端消息时连接结束、`keep_alive` 可重置计时。r2 未保存第 6 项收到的原始帧，**不能倒推它是错误帧、正常完成帧或其他事件**；补测仅记录有界脱敏类型和关闭信息，继续对未知事件停止。

新增 `scripts/voice_v1b_ws_supplement.py` 和固定 `scripts/voice_v1b_ws_supplement_manifest.json`。入口默认 dry-run，输出 `real_requests=0`；真实分支须另行批准新运行编号并创建只使用一次的收据，收据绑定补测 manifest、两份探针源码哈希、r2 原结果、原批准收据及已用标记的哈希、Profile revision／Voice ID 摘要、当前代理、第三方处理确认与计费不确定性确认。独立进程不导入 `main.py`，只读校验 r2 与正式 Profile；补测 Runner 明确拒绝 HTTP、TTS 合成及第三条 WS。r2 原结果和音频仅作来源验证，不加入新结果目录或重新生成。

固定补测 manifest 规范化 SHA-256 为 `6d2cef35221d210940179c6847a372568eca8c05fd585ead6c78a7abfbf851ce`；补测脚本文件 SHA-256 为 `3a1f119094e1e050e754a988e799c5a443ed6608abb036a1550cb64a2eb6b2cc`，共用探针文件 SHA-256 为 `b1cb78902dcd3a2679bdd377e503871604bb354da1c4b18bcaf226851cb6952e`。合成来源夹具与本地假 WS／Mock HTTP 专项合计 **26/26 通过，退出码 0**，包括新收据只能使用一次的隔离 CLI 测试；真实 r2 文件只读来源验证通过，dry-run 仍为零请求。本轮**没有创建补测批准收据、没有连接 ElevenLabs**。上述指纹只用于下一次审阅，任何源码或 manifest 变化都需重新核对。真实补测仍待用户单独批准。

## 10. 单次 WS 补测 r3 实际结果（2026-11-10）

用户另行批准 `v1b-20261110-ws-r3`，执行前核对补测 manifest 规范化 SHA-256 `6d2cef35221d210940179c6847a372568eca8c05fd585ead6c78a7abfbf851ce`、补测脚本文件 SHA-256 `3a1f119094e1e050e754a988e799c5a443ed6608abb036a1550cb64a2eb6b2cc`，并只读验证 r2 原结果、音频、收据、Profile revision 及本机代理。新的一次性收据已使用，不复用。空闲 TTD WS 在注册 Voice ID 后、没有发送合成文字的情况下，于连接约 **20.984 秒**收到一条含 `error` 字段的帧。`unrecognized` 是**本地白名单归类标签，不是已知的供应商原始错误码**：探针当时未保存原始码值、码所在字段、错误结构或消息，均已无法从 r3 记录恢复。连接结束记录为约 **22.016 秒**、清理后 `close_code=1011`、`close_reason_present=false`。该关闭码是在探针遇到错误帧并退出连接上下文后取得，**不能作为服务商主动关闭的证据**；当时没有单独记录接收远端关闭帧。观察结论固定为：**约 21 秒收到错误事件，具体原因及关闭发起方未确认**。不根据到达时间推定为空闲超时，空闲 TTD 验收仍未完成，不为此重复真实测试。

探针按未知事件停止，结果 `status=unknown`、`error_code=ttd_idle_unexpected_event`；实际派发 **0 次 HTTP、1 条 WS、0 个 TTS 字符、0 秒 STT 音频、0 音频字节**。Scribe WS **未连接**，仍不能判断账户的 Scribe 权限、协议或识别表现。实际 billed credits／USD 未获得，保持 `null`，不能写成零。原 r2 文件、音频与已用收据的哈希复核不变；日常 Profile 仍为 `revision=1/active=false`，正式告知记录仍为 0，日常健康检查 200。没有修改数据库、Profile、参数、服务开关或克隆素材，没有重做 TTS。r3 结果文件 `C:\Users\ASUS\AppData\Local\YOSAKI\voice-v1b-runs\v1b-20261110-ws-r3\result.json` SHA-256 为 `a72e84236b828e7ae93650bb39975d05769e56c98bfb59510fdd7aa1e356149c`；新批准收据及 `.used` 标记保持原样。任何后续真实补测均需重新制定范围并单独批准。

## 11. r3 本地诊断审计与 Scribe 单项准备

只读回查 r3 原结果与当时探针源码：`ws_event_diagnostic()` 将原始 `error_code`、`code` 或嵌套 `error.code` 的**非白名单值**归类为 `unrecognized`。因此该词是本地标签，不能当作供应商原始码。r3 未保存原码值、具体来源字段、类型、错误正文及是否随后收到远端关闭帧；这些信息已无法恢复。旧 `connection_end.close_code=1011` 是退出 WebSocket 上下文**之后**读取的连接状态，不证明服务商发起关闭。空闲 TTD 的状态保持：**观察到约 21 秒错误事件；原因及关闭发起方未确认；验收未完成**。不按时间推定为空闲超时，不重复空闲 TTD 真实测试。

本地修复将错误码的已知白名单值与未知码的字段来源、类型及短指纹分开记录，不保存未知原码或自由文本。关闭诊断将实际收到的远端关闭帧、事件后的本地清理、无关闭帧的传输中断分别标识；清理后码另命名为 `post_cleanup_close_code`，不再冒充远端码。假凭据和本地假 WS 定向覆盖三种关闭路径以及未知码脱敏；合并 Scribe 单项入口后专项 **35/35 通过，退出码 0**。本次没有新的真实供应商请求，r2/r3 文件及已用收据保持原样。

Scribe 未执行部分已单列为 `docs/VOICE_V1B_SCRIBE_R4.md`：拟议编号 `v1b-20261110-scribe-r4`，**0 次 HTTP、最多 1 条 Scribe WS、原 5.805 秒夹具、连接／全程最多 20 秒、零 TTS、零重试**。独立入口默认 dry-run，尚未创建真实批准收据；须用户另行批准。

## 12. Scribe 单项补测 r4 实际结果（2026-11-10）

用户随后单独批准第 11 节拟议的固定范围。执行前校验 manifest `65bb35650628ff660ef0a0e6f5d29507cb3fd0bca19a9eff40e7b4f3db1cca6a`、Scribe 脚本 `a26881eb089d4e6f760fa26a6dd3734fcd477c209e2d2fc843a6f3002a98a4d8`、共用探针 `88543704a16edeb167dbf641d114db26c573d6bbf1143522c8c6c53e054322cc`，以及原夹具和 r3 来源指纹，均匹配。一次性收据已使用，不能重放。

运行 `v1b-20261110-scribe-r4` 返回 `completed`：**0 次 HTTP、1 条 Scribe WS、0 个 TTS 字符、5.805 秒音频，连接 7.906 秒**。收到 session started、5 条 partial 和一条 committed transcript；最终转写为“你好，我是阿夜。现在开始语音识别测试。”，与原固定合成语音文字一致。结果文件 SHA-256 `a5b43c8c4af2b05080766c55cd9a584c0be3ff573251f29409e4512bbafca5dc`，详细记录及路径见 `docs/VOICE_V1B_SCRIBE_R4.md`。本次不评估 TTS 音色，也不将一段合成短音频推及真实麦克风识别准确率。

供应商实际扣费仍不可见，billed credits／USD 保持 `null`，不能报告为零。r3 的空闲 TTD 错误原因与关闭发起方仍未确认，本次未重试。没有改动日常数据库、Profile、正式告知、服务开关或原有 TTS 音频；不进入 V2。
