# V4 动态桥接 R3：单次真实补测执行单（零外呼冻结）

日期：2026-11-13。状态：**R3 已获单次批准、续期后执行一次，未通过完整动态桥接验收；收据已占用，不再派发。**首次预检因 ChatGPT Plan 凭据过期零派发停止；用户随后在隔离入口完成续期，全部冻结绑定重新通过。实际结果见第 6 节。R2 原收据与失败结论保持不变。

## 1. 本次冻结身份

| 项目 | 值 |
|---|---|
| 运行编号 | `voice-v4-dynamic-20261113-r3` |
| generation ID | `feb67f8db60944e0b707a8583b55bf29` |
| 规范化 manifest SHA-256 | `f6ec161bc6d2e885d2da8421cfc79334b0640de84c768b67e0b6b64101ac90eb` |
| 完整 16 文件运行代码集合 SHA-256 | `f45d512bedd27d7787d9afb958c10bfa5e651f2a357f78db9f074db140f3f6d7` |
| 模型上下文 SHA-256 | `2bef9c7fc35ca6a3af8c9ae557fe18389ba9fe32588d0ede798603812c87c49e` |
| Voice ID SHA-256 | `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767` |

[规范化 manifest 与完整模型请求](VOICE_V4_DYNAMIC_CANDIDATE_R3_MANIFEST.json)由日常库**只读取样**进入新隔离库后，用最终上下文构造函数生成。与 R2 manifest 比较，只有 `run_id`、`generation_id` 改变；完整 `model_request`、上下文指纹、Profile revision 3、告知版本 `2026-11-08-v1`、Voice ID 摘要、代理和预算完全相同。若执行前任一项漂移，停止并重新提交差异，不自动修订此次授权文本。

R2 的 15 文件代码指纹 `165bdea817b85fa836e9a7cb09e5963554307bccf13e81e238be04985772da4f` **不适用于 R3**。新集合增加诊断模块；候选路由、收据文件、桥接／relay、浏览器播放器和候选组件按当前源码重新冻结。候选组件明确导入 `realtimeOutput.ts`：隔离浏览器编译结果指向该 `.ts`，避免同目录旧生成 `.js` 被 Vite 选中。没有手工修改生成 `.js`。当前工作树可能有并行 Memory 变化；执行前仍逐文件重验整个集合，并在新隔离库复核启动行为。

相对 R2 冻结代码的本批差异如下；既存 Memory 文件未纳入本批修改：

| 文件 | 变化 |
|---|---|
| `backend/app/voice/realtime_dynamic_diagnostics.py` | 新增私有分阶段诊断；512 条硬上限、高频事件 480 条预留边界、显式写入／容量错误与状态。 |
| `backend/app/voice/realtime_dynamic_candidate.py` | 语音失败与模型完成分离，保存诊断终态和已观察 usage；结束摘要报告诊断完整性。 |
| `backend/app/voice/realtime_dynamic_candidate_receipt.py` | 将诊断模块纳入运行代码集合，旧 15 文件授权不再匹配。 |
| `frontend/src/voice/realtimeOutput.ts`、`DynamicChatOutput.vue` | 语音失败只静音动态轮次，不误发停止生成；组件显式加载 `.ts` 源文件。 |
| `scripts/voice_v4_dynamic_candidate_browser_stub.py`、`tests/test_voice_v4_dynamic_candidate.py`、`tests/test_voice_v4_dynamic_diagnostics.py` | 扩充仅本地替身与专项断言；替身和测试不属于真实派发代码集合。 |

### 16 文件清单（固定顺序）

| 路径 | SHA-256 |
|---|---|
| `backend/app/main.py` | `80419923536aa7aa4674da3bf3ac154775f2352c407209dbe5f147969b3c3c0d` |
| `backend/app/chatgpt_plan.py` | `927c670fbf8f22e42f794fd70d9d0a7c1b371ab57010774cefc78364cfd2ae2b` |
| `backend/app/responses_adapter.py` | `311e68ae86d85ab4428ee8f8db3e9be67a1055f85df8d8651e66d5d93afe440d` |
| `backend/app/voice/realtime_dynamic_context.py` | `56c947c3e10c4aa393e894e6dd1f47c7f3edb427496e4539fc86672fc389b022` |
| `backend/app/voice/realtime_dynamic_candidate_receipt.py` | `a1d8860a8894ae6dfd5d81637e45f343dc72d5dc796272ec4a9b4e180ff40aaf` |
| `backend/app/voice/realtime_dynamic_candidate.py` | `268f5f8f898f011a4441329f65cced2188e841520d4866b9e52019234e5aad63` |
| `backend/app/voice/realtime_dynamic_diagnostics.py` | `01cbf5b4470ac923459e02b897392b900058b9b02bf049dda5e219f343b27f52` |
| `backend/app/voice/realtime_chat_bridge.py` | `a14969f23219048fa68ca2a3b3817b1ef345f2183014be133e834570107d57a1` |
| `backend/app/voice/realtime_output.py` | `8c69ccc98565936b3002cebeb027e8db82106bdd8f96d2f4a84afda27c045571` |
| `backend/app/voice/realtime_ttd_connector.py` | `766a0871239b72fbdfa51fdb8c8c3d9d9f6f0e8b268a2099957987f10a85009c` |
| `backend/app/voice/readable_text.py` | `ef2ceb0bd0293faff50c5d2ed7054b2d4c329db281db44a7239015bcfda73e94` |
| `frontend/src/voice/realtimeOutput.ts` | `de80aac8c847dcf971d9f0d1a71e7a187d931b075d53bc258754093aa35c33ba` |
| `frontend/src/voice/DynamicChatOutput.vue` | `359887a39c5271a8781169474449db39b427a8c69891fabb54ad24e355a5bce3` |
| `frontend/src/App.vue` | `c2d734a4fe74c0ca077ee51663536ff307b56e6496476f1f21c4cd93830f8b72` |
| `scripts/voice_v4_dynamic_candidate_isolated.py` | `0e2a77c913d0032af334ad71bef9b002afdc91f1b237b2ac1068f6ab1a289052` |
| `scripts/voice_v4_dynamic_candidate_approve.py` | `4c7994d27df1d7a35531985b1704a161b7da39520a8836094cf1ee2f2c397894` |

集合算法：逐路径计算文件字节 SHA-256，按上表顺序拼接 `路径 空格 哈希 换行`，再对拼接结果计算 SHA-256。测试用浏览器替身脚本不属于真实执行集合，不能充当真实连接器。

## 2. 模型与语音派发范围

绑定 owner 1、阿夜角色 1、对话 5、模型档案 5。聊天供应商为本机 ChatGPT Plan 授权的 `gpt-6.1-sol`，Responses 请求体严格为：`model=gpt-6.1-sol`、`input=[{role:user,content:<下列 User 全文>}]`、`instructions=<下列 System 全文>`、`store=false`、`stream=true`、`max_output_tokens=128`；不传工具、历史、记忆、摘要、欲望建议或独白。模型档案的 `temperature=0.8`、`top_p=1.0` 保留为档案值，但**不在本次请求体中发送**。

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

ElevenLabs 只接收本轮**动态生成的助手公开正文**经既定过滤及稳定分句后的片段，不接收内部推理、独白、工具数据或原文标签。原 IVC Voice ID、Profile revision 3、`eleven_v4_turbo`、`pcm_24000`（24 kHz、16-bit、单声道 PCM）、`language_code=ja`；本机代理固定 `http://127.0.0.1:7897`。实际送读文本无法预先冻结，因此每段派发前再次核对来源位置、字符数与整轮累计预算，并写入私有诊断。实际请求字段仍为先发 `{"voices":[<原 Voice ID>]}`，再按片段发 `{"inputs":[{"text":<过滤后片段>,"voice_id":<原 Voice ID>}],"flush":true}`；保活及结束控制按冻结连接器执行。密钥只由后端环境读取，不进入 manifest、浏览器或日志。

| 独立上限 | 数值 |
|---|---|
| 模型 | 最多 1 次生成、128 输出 token、30 秒 |
| TTD | 最多 80 实际请求字符、2 片段、2 条 WS、0 次 ElevenLabs HTTP、累计 1 MiB 音频 |
| 时间 | 每条 TTD WS 30 秒；整轮语音接收 45 秒；接收后浏览器播放排空 30 秒；整轮墙钟 110 秒 |

这些是本地派发及时间上限，不是供应商账单硬上限。两侧实际用量与费用可能未知，不填零、不据线性标价推定扣款。

## 3. 故障与停止语义

- **语音失败、断线、发送结果未知或超出语音预算**：停止当前及后续语音派发并隔离迟到音频；已尝试发送的片段不重试、不补连、不补发。已启动的模型可在原 30 秒／128 token／整轮 110 秒边界内继续到终态，正常完成时保存完整文字和可见 usage。语音失败不把本可完成的文字改为 `failed`。
- **用户只停止语音**：立即静音和清队列，文字生成继续；记录 `user_stop_voice`。
- **用户停止生成**：取消模型任务并同步停止语音；部分公开正文按原机制保存为 `stopped`，记录 `user_stop_generation`。
- **模型明确失败、流结束缺完成事件、输出截断、解析或供应商错误**：分别记 `failed` 或 `incomplete` 及具体安全类别；停止语音，保留已观察的正文和 usage，不再发起新模型或 TTD 请求。不能因收到两句话就推定完成。
- **试验整轮墙钟超时、授权撤销或诊断写入失败**：按记录的来源停止相应派发；已经占用的收据不释放。诊断失败时禁止后续语音连接；如果模型已经派发，仍尽可能完成并保存文字，浏览器摘要标记 `diagnostic_status=failed`，不能宣称日志完整。

私有诊断按同一 `run_id`／`generation_id` 和单调相对时间记录。高频模型事件／音频计数最多占用前 480 条；达到时写 `diagnostic_limit_reached`，给终态留空间。总量超过 512 条或磁盘写入失败显式报错并停止，绝不静默声称完整。`text_send_return` 只证明本地 `send` 返回，不证明供应商接收；保留连接尝试、建立、片段文本与来源、发送、首音频、累计字节、模型终态、usage、保存结果和收尾。无原始协议帧、密钥或内部推理。

## 4. 实际入口与执行前门槛

待用户另行批准后，才可在 `%LOCALAPPDATA%\YOSAKI\voice-v4-dynamic-20261113-r3\` 建立**仅本次**的 `real_approved` 收据。使用独立临时数据库和单 worker 后端 `127.0.0.1:18786`、产品页 `127.0.0.1:15188/voice`；前端 `/api` 只代理隔离后端。日常 8000／5173 及日常库保持运行。由隔离产品页面的“启动一次性真实候选”控件触发，经过产品鉴权 WS、relay 和浏览器播放器；不以独立探针代替。

创建收据和任何真实请求前，重新核对本文件所有指纹、实际 Vite 解析路径、日常库只读取样得到的角色／对话／Profile／告知／上下文、代理、Key 仅是否配置及 ChatGPT Plan 凭据剩余有效期。任何不符先停止；不得自动更新 manifest、模型、参数或收据。派发前原子占用收据；崩溃、并发、结果未知均不恢复额度。R2 `.used` 与原始观察仍保留且不可复用。执行后关闭隔离实例并清除其临时登录 session；保留脱敏诊断，不覆盖日常库。

## 5. 执行前零外呼验收证据与限制

- 受影响后端专项 **62/62**，退出码 0；前端 `vue-tsc --noEmit` 和 Vite 生产构建通过。模拟覆盖语音先失败而模型完成／usage 后到、无完成事件、模型截断、解析错误、语音断线、保存失败、超时、停止／迟到隔离、512 条日志边界及写入失败。没有为凑全量数字重跑无关后端测试。
- Edge Headless CDP 的实际隔离产品页使用合成账户、假模型和本地假 TTD，无外呼能力。**正常场景**：页面显示消息 2 `completed`、2 片段／2 连接、“整轮音频已接收并播完”；软件首播记录 99.1 ms。诊断有两段发送前文本、两次连接／发送、音频字节、模型完成和 usage。**语音失败场景**：假 TTD 返回 `quota`，页面显示语音错误，同时消息 2 `completed`、1 片段／1 连接；诊断保留 12 字片段、发送尝试／返回、0 音频字节、模型完成和 usage。这里的 `quota` 仅是本地模拟错误，不代表真实账户状态。
- 证据位于 `%TEMP%\yosaki-v4-r3-browser-evidence\normal-final\` 与 `failure-final\`：各有浏览器截图和私有诊断 JSONL。浏览器记录证明软件播放链行为，不代替真人听感。先前浏览器发现旧生成 `.js` 抢先解析，已通过候选组件显式 `.ts` 导入修复；修复前的模拟观察另存，不混入最终通过证据。
- 上述准备批次的真实供应商请求数、真实收据数均为 0；其后 R3 获单次批准并执行，结果见第 6 节。R2 历史失败根因仍未知。

## 6. R3 单次真实执行结果（2026-11-13）

用户以本文件冻结的运行编号、generation ID、四项 SHA-256 与原预算授权一次。首次预检发现 ChatGPT Plan 凭据已过期，在创建收据前停止，外呼为 0。随后使用已有隔离 OAuth 续期入口：仅对现有账户执行刷新，隔离页显示 `published=true`、`fresh_90s=true`，日常凭据只读复核剩余约 3,533 秒；没有撤销授权或生成聊天。再次核对 manifest、16 文件代码、完整模型请求、上下文、Voice ID 摘要、Profile revision 3、告知、角色／对话／模型档案、代理与 API Key 是否配置均匹配。隔离 Vite 实际编译的候选组件加载 `/src/voice/realtimeOutput.ts`，未加载旧 `.js`。

创建本次 `real_approved` 收据后，以独立临时 SQLite、`127.0.0.1:18786` 后端和 `127.0.0.1:15188/voice` 产品页面启动。页面经过登录鉴权，预览返回 `real_approved`、正确运行编号及上下文指纹。只对“启动一次性真实候选”发送一次浏览器可信点击；后端在首次模型请求前占用收据，`.used` 记录为 `occupied_outcome_unknown`。未重试、补连、补发或更换请求。

| 观察项 | 结果 |
|---|---|
| 模型派发与终态 | 1 次请求尝试；收到 `response.created` 和公开正文增量。约 4.079 秒时遇到本地解析器不识别的模型事件，诊断为 `model_event_unknown`／`ValueError`，终态 `failed`。未知事件的实际供应商 `type` 未被旧白名单日志保留，不能追溯具体事件或称为供应商报错。未观察到 `response.completed` 或可靠 usage。 |
| 文字保存 | 隔离库助手消息 ID 2 保存 25 字公开正文 `今夜はゆっくりでいいよ。ここであなたの話を聞くよ。`，`generation_status=failed`；`usage_json` 为空。数据库完整性检查为 `ok`。不把已收到的两句文字标为完整生成。 |
| 语音片段与派发 | 片段 1 在外呼前记录 `今夜はゆっくりでいいよ。`，来源 `0..12`，12 字。记录 1 次 TTD WS **连接尝试／预算占用**；无 `connection_ready`、`text_send_attempt` 或 `text_send_return`。因此不能证明 WS 已建立或供应商接收了文字；已确认的文本发送尝试为 0。第二片段未派发。 |
| 音频与播放 | 首音频事件 0、已观察音频 0 字节；首次非零样本进入播放链未观察到，浏览器播放结束为 `false`。软件播放未通过；真人听感未测。 |
| 诊断与费用 | 私有 JSONL 共 37 条，末条 `turn_summary`，没有观察到容量截断或写入错误。R3 的模型解析失败可明确分类；未知事件原始类型、供应商 WS 连接是否建立、两侧实际计费仍未知。R2 的历史根因不据此改写。 |

持久证据在 `%LOCALAPPDATA%\YOSAKI\voice-v4-dynamic-20261113-r3\`：`.approval.json`、`.used`、`.diagnostic.jsonl`、脱敏 `observation.json` 与产品页面截图 `product-final.png`。隔离后端、前端、OAuth 页面及浏览器均已关闭；隔离 SQLite 和临时登录数据库已随进程清理。隔离浏览器的 Cookie 从 1 条清到 0 条，站点数据已清；递归删除整个临时浏览器目录被执行环境拒绝，因此仅保留无登录会话的配置目录。日常 8000／5173、正式 SQLite、Profile revision 3、告知、Voice 与后台任务配置及 Memory 工作未变；唯一获准更新的是日常数据目录中的本地 ChatGPT Plan 加密凭据。**R3 已用收据不得复用，不能因本次失败自动补测。**
