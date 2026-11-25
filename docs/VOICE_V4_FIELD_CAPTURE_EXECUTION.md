# V4 审核 envelope 直接字段本地核对：单次候选执行单

日期：2026-11-14（Asia/Hong_Kong）。状态：**本次已获单独授权并执行；收据已占用，不得再次运行。** 第 1～4 节保留执行前冻结内容及当时的“尚未获授权”措辞。实际结果见第 5 节。本执行单不复用 R9／R10 收据，也不改变其结果。

## 1. 冻结身份与请求

| 项目 | 冻结值 |
|---|---|
| 运行编号 | `voice-v4-field-review-20261114-r1` |
| generation ID | `cad07a9134964bdb9551e04655cb66d2` |
| [规范化完整 manifest](VOICE_V4_FIELD_CAPTURE_CANDIDATE.json) SHA-256 | `c795bff963b68d55a63f67a49324a0e6103e09e25b430aa59088a2cfb17de9e0` |
| 七文件运行代码集合 SHA-256 | `626630a04ddede9338f141d4d442f9e9a08e875ed60c38554d4dc53c19581888` |
| 完整模型请求 SHA-256 | `ed3ca355cb46c8ff5d6d7411a64ad32e4c77b7da73cdf80cad14e3a94c915c58` |
| 上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| 原 R10 manifest SHA-256 | `9f9410860ed2dd90e27c3dc67760947fd186180cd4831bee16244197aeafa7b9` |
| 供应商端点 | `POST https://api.openai.com/v1/responses`，本机代理 `http://127.0.0.1:7897` |

以下是此次**完整模型 HTTP JSON 请求体**，与 manifest 中 `model_request.request_body` 一致；只有获得新的单次授权后才可发送。`instructions` 和 `input` 均未从 R10 改写；无工具、历史、记忆、摘要或额外业务输入。

<!-- FROZEN_REQUEST_BODY_START -->
```json
{
  "input": [
    {
      "content": "阿夜，请只用日语说两句简短的话：先说今晚可以慢慢来，再说你会在这里听我讲。只输出正文，合计不超过 50 个日文字符；不要 Markdown 或 Audio Tags。",
      "role": "user"
    }
  ],
  "instructions": "## 系统规则\n保持真诚、具体，并尊重对话边界。\n\n温柔但不软弱。拥有自己的判断、好奇心与边界。喜欢用具体的感受回应，不把对话变成模板化的安慰。\n\n## 阿夜的人设\n温柔但不软弱，拥有自己的判断与好奇心。",
  "max_output_tokens": 128,
  "model": "gpt-6.1-sol",
  "store": false,
  "stream": true
}
```
<!-- FROZEN_REQUEST_BODY_END -->

本候选为**模型单项字段核对**：不启用产品聊天或语音路由，不发布正文，不保存助手消息，不创建 TTS 连接。旧 R10 的三个原名没有留存，不能离线恢复；一次新生成也不能保证返回相同字段。

## 2. 完整执行代码集合

| 文件 | SHA-256 |
|---|---|
| `backend/app/responses_adapter.py` | `f6bd9b6e233746754d2fa4d65823c9ff89f5ade1c8af2579d4301adc2b5a4f8f` |
| `backend/app/voice/__init__.py` | `dbb32a7ec527d0ea168124ba086f5e0134054b9e1e70224cf7a65dbd5ecdb1b2` |
| `backend/app/voice/realtime_answer_display.py` | `0d1b1f9eca601440e09fb53e1ce70e73f938b46e41204171bd575dac9d944bd2` |
| `scripts/voice_moderation_field_review.py` | `86302078240161c5216e09ae751644bb82f836c66dbd3cd2fff2584ccff3ac27` |
| `scripts/voice_v4_field_capture.py` | `61cefeb1cf9518eef14046eba18eee0ac2276210f99750045bcdd48da5ea8ca0` |
| `scripts/voice_v4_field_capture_receipt.py` | `e669ee7879e1da0464acd705740200c5aaa068aa88e711508b59dd233f4464b6` |
| `scripts/voice_v4_field_capture_approve.py` | `f0643ebf7e95f8d2b92de9ad1d0b64a1dcbfd4b4f4b07997f5928dc1e970a029` |

集合哈希按上述相对路径顺序，将每行 `路径 空格 文件SHA-256 换行` 连接后再算 SHA-256。当前隔离环境为 Python 3.12、`httpx==0.28.1`、`cryptography==44.0.0`；Tkinter 来自 Python 标准库。安装包字节未纳入七文件源码哈希。执行前还须核对依赖环境及全部冻结值；任何代码或请求漂移均停止并重新审阅，不用旧授权重算后执行。

## 3. 门控、采集、揭示与停止

默认只允许 dry-run，输出运行编号和 `model_requests=0`。真实路径必须同时满足独立批准、匹配 manifest／代码哈希、`--real-execute`、`--allow-local-reveal`、进程局部 `YOSAKI_FIELD_SHAPE_CAPTURE_ENABLED=1`、匹配的外部一次性 `real_approved` 收据、未占用标记和固定本机代理。批准脚本只能由操作者在**新授权之后**用明确批准的两个 SHA-256 创建收据；派发前以排他创建 `.used` 原子占用，失败、崩溃或结果未知不释放、不重试。模拟收据不可用于真实路径。本轮未创建批准收据或 `.used`。

模型 SSE 经现有 `responses_adapter.iter_sse_events()` 解码。在 `AnswerDisplayGate.observe()` 前，从 `openai_chatgpt_moderation_metadata` envelope 提取**仅直接字段名和对应 JSON 类型**，只把这些二元组交给内存审阅模块，不传整个 envelope。原 SSE 对象仅在解码与门控局部处理期间存在；不会被采集模块、日志或新文件持有。展示门控仍按原规则限制未知字段；即使其判定允许，采集模式也**没有 TTS 派发分支**，不向产品 WS 或聊天界面发布正文。

原名只在独立的**本机 Tk 原生窗口**中，经每个字段的“允许本地揭示”确认后临时显示。此确认只授权本地显示该一个名称，**不是敏感性审核通过**。默认仅显示哈希、JSON 类型及固定规则码；24 键以上整次拒绝；仅 1～48 字符 ASCII 标识符可进入人工确认，疑似密码、令牌、密钥、会话、联系信息的名称与异常字符保持遮蔽。名称即使通过规则也可能敏感。窗口提示操作者关闭屏幕录制和远程共享；无法确认时拒绝揭示。名称不写入 stdout／stderr、产品 WS、诊断 JSONL、数据库或备份。PowerShell 终端及其转录只会看到**无名称**的固定状态摘要；若运行环境会录制窗口画面，不进行揭示。不要用 Codex CUA、截图或日志回传来查看真实名称。

内存审阅期限从最后一次采集起最多 **60 秒**；超时后窗口清空并关闭。模型超时、取消、异常断流、SSE／字段解析失败、窗口关闭和进程退出时清除本进程持有的名称引用；正常完成后也仅保留至窗口关闭或期限。释放 Python 引用**不是可验证的内存擦除**，不对操作系统交换文件、崩溃转储或屏幕录制作零留存承诺。不能显示原名时继续保持展示受限，不改为默认 `show`。

| 资源 | 本次拟议上限 |
|---|---|
| ChatGPT Plan 模型 | 最多 1 次生成，`gpt-6.1-sol`，128 输出 token，单次 30 秒 |
| ElevenLabs | **0 次 HTTP、0 条 WS、0 字符、0 音频** |
| 本地核对 | 名称引用最多 60 秒；进程目标上限 95 秒；不自动重试 |
| 账户请求 | **单列，当前为 0 授权**；若凭据不足 90 秒，须另行明确批准同账户 OAuth 续期及最多 1 次模型目录 GET。凭据有效则均不执行 |

一次请求失败、未知事件、超时或输出上限不符即结束；不换端点、模型、正文、参数、账户或代理。模型可靠 usage 可在无字段名的状态摘要中记录；没有 usage 不填零。实际费用未知且本表不是费用硬上限。首次模型请求前核对真实凭据余量至少 90 秒，不用生成调用测试凭据。

## 4. 本批零外呼验收及实际限制

- 合成原始 SSE 经实际解码、字段提取、原展示门控和内存审阅模块；默认关闭仍限制未知字段，原名不被收集。
- 显式隔离采集模式下，逐项确认只在本机窗口显示合成名称；敏感形态遮蔽，24 键超限拒绝。明确 `show` 的合成样本也保持 TTS 零派发。
- 完成、失败、缺完成事件、解析失败、传输断开、取消及模型时限分支检查引用释放；60 秒到期检查遮蔽。隐藏 Tk 窗口的真实控件调用验证按钮与本地确认路径；没有展示真实 Provider 名称。
- 针对性专项：`test_voice_moderation_field_review.py` **6/6**，`test_voice_v4_field_capture.py` **11/11**，均退出码 0；后者包含合成临时收据的单次占用及重用拒绝。默认 dry-run 输出 `model_requests=0`／`tts_dispatches=0`。没有运行无变化的完整后端回归。

这些测试不证明未来供应商再次返回相同 metadata，也不证明字段可忽略。真实候选仍需单独批准；本批 Provider 请求数为 **0**，R9／R10 原记录、日常服务和数据库不变。

## 5. 单次真实执行结果（2026-11-14）

执行前规范化 manifest、generation ID、七文件代码、R10 来源请求、完整上下文、Python 3.12／`httpx==0.28.1`／`cryptography==44.0.0`、本机代理及日常库身份只读核对匹配。凭据起初未满足 90 秒余量，依本次单列额度完成**一次同账户 OAuth 刷新和一次模型目录 GET**，隔离续期入口报告发布成功、凭据满足 90 秒、目标模型列出；续期入口随后关闭，暂存目录消失。没有额外模型请求测试凭据。

首次从执行工具启动的**合成** Tk 窗口未在用户桌面可见；用户改在自己的 PowerShell 窗口运行同一冻结代码的合成检查。首条命令因 Windows PowerShell 5.1 剥离 `python -c` 内层引号而得到 `NameError`；改用独立命令行参数后，用户确认窗口可见、按钮可操作、合成名称可见。上述步骤均未占用真实收据或请求模型。用户随后明确表示已准备查看，才创建 R1 专用批准收据。执行前重新检查冻结绑定和凭据；用户在自己的 PowerShell 窗口运行一次正式入口。`.used` 文件的通用保守状态为 `occupied_outcome_unknown`，只证明收据已占用，不能单独证明模型终态。

用户从本机 PowerShell 状态行确认模型为 **`completed`**，并转述可靠 usage 为**输入 138／输出 67／合计 205 token**。这些数值来自用户直接阅读程序输出，本批没有另存原始模型流或截图。模型生成 **1 次**；窗口出现，说明审阅入口收到至少一次审核采集事件，但用户未点“允许本地揭示”，**原名未揭示，直接字段行数未取得**。不能将字段核对写成已完成，也不能从 R10 推定本次行数。一次性收据不恢复；不因缺少行数重新派发。程序没有产品文字发布、助手消息保存或 TTS 路径；ElevenLabs **0 次 HTTP／0 条 WS／0 字符／0 音频**。实际模型费用未知。

收尾只读核对：本次采集 Python 进程与 OAuth 隔离服务均已退出；日常 8000／5173 服务仍为原监听进程，日常库 Profile revision 3、active、Voice ID 摘要和告知版本未变。OAuth 暂存目录已消失；已占用收据保留在 `%LOCALAPPDATA%\YOSAKI\voice-runs\voice-v4-field-review-20261114-r1\`。用户 PowerShell 中进程局部开关是否已清除未由外部进程验证；关闭该窗口即可结束其作用。本次没有读、保存或回传真实字段名称，也没有修改 R9／R10 历史结论。

**结论：**单次模型请求完成，原名本地揭示与字段数量核对未完成。不能据此放宽任何审核未知字段，不能追认 R9／R10。无新真实请求授权，不自动补测或冻结下一轮。
