# YOSAKI Voice V1-B — Scribe 单项补测执行单

日期：2026-11-10（Asia/Hong_Kong）  
状态：**单次真实补测已完成；收据已使用；不得再次派发。**第 4 节保留执行前的模拟验收与授权文本，第 5 节记录实际结果。

## 1. 前次结果与边界

r2 的四段 TTS 音频保留，不再生成。r3 空闲 TTD WS 在约 21 秒观察到错误事件；其原始错误码、字段来源及关闭发起方已无法从旧记录恢复。空闲 TTD 仍为**未完成**，不因本单重试。r3 的 Scribe 项未连接、未发送音频，因此本单仅验证 Scribe 的权限、握手、固定 PCM 输入及最终转写事件。Scribe 识别结果与 TTS 音色听评分开记录。

## 2. 冻结输入及上限

| 项目 | 固定值 |
|---|---|
| 拟议运行编号 | `v1b-20261110-scribe-r4` |
| 接口 | `wss://api.elevenlabs.io/v1/speech-to-text/realtime`；`scribe_v2_realtime`、`pcm_16000`、`commit_strategy=manual`、`language_code=zh` |
| 音频 | `tests/fixtures/voice_v1b_scribe_zh.wav` 中的原固定 PCM；16 kHz、16-bit little-endian、单声道、92,880 帧、**5.805 秒**；WAV SHA-256 `7f4323a61dbf8ceb939837a634f76dee8378fbc432d97f7aeafa59a95fd39952` |
| 请求上限 | **0 次 HTTP、最多 1 条 Scribe WS、0 个 TTS 字符、最多 5.805 秒音频、连接／全程最长 20 秒** |
| 失败处理 | 401/403/429、其他握手或 Provider 错误、超时、断线、无最终转写或未知计费结果均立即停止；不重试、不补发、不换模型或格式 |
| 结果 | 新目录保存受限事件摘要、实际派发音频秒数、最终转写及错误状态；实际 billed credits／USD 未实查时保持 `null` |

音频是此前 Windows 本地离线合成的测试夹具，不是用户麦克风录音。此 Scribe 请求只发送 API Key 作为服务端认证及固定音频；**不发送阿夜 Voice ID、聊天正文或 TTS 文字**。Key 只从执行进程环境读取，不写入命令行、结果文件或报告。Scribe 官方 [WebSocket 参考](https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime)列出本单使用的模型、格式和事件；账户实际权限仍须由获批后的请求判断。

## 3. 实现与执行前校验

- 固定 manifest：`scripts/voice_v1b_scribe_manifest.json`，规范化 SHA-256 `65bb35650628ff660ef0a0e6f5d29507cb3fd0bca19a9eff40e7b4f3db1cca6a`。
- 独立脚本：`scripts/voice_v1b_scribe_probe.py`，文件 SHA-256 `a26881eb089d4e6f760fa26a6dd3734fcd477c209e2d2fc843a6f3002a98a4d8`；共用探针脚本文件 SHA-256 `88543704a16edeb167dbf641d114db26c573d6bbf1143522c8c6c53e054322cc`。
- 源证据：r3 `result.json` SHA-256 `a72e84236b828e7ae93650bb39975d05769e56c98bfb59510fdd7aa1e356149c`，原批准收据 SHA-256 `629d535d3d94e8c9d66318a8577802411cbfe1180fc9aabdfedcd10ed122be53`，已使用标记 SHA-256 `2b92b4d6672f28dbf10bfa0c89545229bf681bd02fe31d9ee8a70cbfb42cafba`。脚本只读核对这些来源，确认 r3 的 Scribe 派发量为 0。
- 真实分支须显式 `--execute-real`，并匹配**新**运行编号、manifest／两份源码／夹具／来源指纹、本机代理、第三方处理确认及计费不确定性确认的一次性收据。收据一经使用即落 `.used` 标记，不能复用。执行前若任一指纹或代理不符，停止，不自行修补或扩大请求。
- 脚本不导入 `backend.app.main`、不读取或迁移 SQLite、不调用日常 Voice API；产品总开关与后台任务开关维持关闭。代码层拒绝 HTTP、TTD 与第二条 WS。

## 4. 本地验收与剩余授权

假凭据、Mock HTTP 和 `127.0.0.1` 假 WS 的探针专项合计 **35/35 通过，退出码 0**，覆盖零请求 dry-run、manifest／收据门控、来源校验、正常最终转写、限流、断线、单连接预算及零重试。真实 r3 来源只读校验通过。尚未创建 r4 批准收据，也没有进行新的 ElevenLabs API 请求。输入、连接与时间上限可由脚本限制；供应商实际计费方式、最低计量及账单硬上限仍未知，不能把参考美元标价当作实际扣款。

**待用户单独批准文本：**

> 我批准按本执行单以运行编号 `v1b-20261110-scribe-r4` 执行一次 Scribe 单项补测，并创建仅本次有效的一次性收据。授权绑定 manifest SHA-256 `65bb35650628ff660ef0a0e6f5d29507cb3fd0bca19a9eff40e7b4f3db1cca6a`、Scribe 脚本 SHA-256 `a26881eb089d4e6f760fa26a6dd3734fcd477c209e2d2fc843a6f3002a98a4d8` 与共用探针 SHA-256 `88543704a16edeb167dbf641d114db26c573d6bbf1143522c8c6c53e054322cc`，执行前不一致则停止。我同意将固定 5.805 秒本地合成音频交由 ElevenLabs 处理，接受实际计费仍不确定。最多 0 次 HTTP、1 条 Scribe WS、20 秒；不做 TTS、空闲 TTD 重试或任何追加请求。失败或未知结果立即停止，不重试、不换模型。保持日常服务、数据库、Profile 与开关不变。

上文保留执行前的待批准文本；实际授权和结果见第 5 节。

## 5. r4 单次真实执行结果

用户已按第 4 节的固定文本批准运行 `v1b-20261110-scribe-r4`。执行前，manifest、两份探针源码、音频夹具及 r3 来源指纹均匹配；本机代理与 r3 一致。一次性收据已创建并使用，批准收据 SHA-256 为 `13233867b3fda1fa02d2e5d940863b45b181af73614e7819e585314b59ad2d67`，已用标记 SHA-256 为 `f3a93f7fe061ab8f53b45b1e766c4f999fc322618e61fb50500a82f46bef8910`。不得复用。

探针返回 `completed`，结果文件为 `C:\Users\ASUS\AppData\Local\YOSAKI\voice-v1b-runs\v1b-20261110-scribe-r4\result.json`，SHA-256 为 `a5b43c8c4af2b05080766c55cd9a584c0be3ff573251f29409e4512bbafca5dc`。实际派发 **0 次 HTTP、1 条 Scribe WS、0 个 TTS 字符、5.805 秒固定合成音频**；分为 12 个音频块。观察到 `session_started`、5 条 partial transcript 和一条最终 `committed_transcript`；连接记录耗时 **7.906 秒**，低于 20 秒上限。最终转写为“你好，我是阿夜。现在开始语音识别测试。”，与夹具原文一致。该结果只证明本次固定合成短音频的识别，不代表真实麦克风或广泛准确率验收。

结果中的 `provider_billed_credits`、`provider_billed_usd` 均为 `null`：没有账户账单实查，实际计费继续记为**未知**，不写成零。没有产生 TTS 音频，没有重试、换模型或测试空闲 TTD。r3 原结果、批准收据及已用标记的 SHA-256 复核不变。探针未导入日常应用、访问 SQLite 或更改 Profile、告知、Voice／后台开关。空闲 TTD 仍是未完成项。
