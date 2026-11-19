> 实施状态（2026-11-19）：P1～P5与设置修复已完成并加载日常。以下保留v0.2实施基线；现行单次刷新保留、验收版本和缺口以 docs/CHAT_ENHANCEMENT_HANDOFF.md 为入口。

# YOSAKI 对话功能增强实施稿 v0.2

日期：2026-11-18（Asia/Hong_Kong）  
状态：P0 源码核实完成；P1～P4 已形成隔离候选。下文原始审计保留为 2026-11-18 的 P0 记录；当前行为、证据与缺口见 `docs/CHAT_ENHANCEMENT_CANDIDATE.md`。真实验证和日常接入尚未执行。  
基线：本机未提交工作树。保留 v0.1 原文，本文修正其现状假设并具体化实施方式。v0.1 第 3～11、13 节的产品与安全要求继续适用；出现现状描述冲突时，以本文的源码核实为准。

本轮结论：已有聊天、模型档案、工具确认、上下文预览、公开推理和 Voice 可复用，但距离完整聊天增强仍有数据层缺口。应先建立独立请求记录与统一组装入口，再实现不可变消息修订和分支；不能先堆消息按钮，也不能直接把现有 `usage_json` 相加称为可靠会话统计。

日常角色实际使用 **legacy 最近 100 条消息**，不是 strict 50 回合，也没有自动摘要。重生成仍删除旧答案；套餐普通请求未发送档案中的输出上限；Responses incomplete 用量会丢失；历史重生成还会重复传入最后用户输入。这些应纳入第一批本地修复。

## 1 本轮范围与证据

已通读当前 769 行 `AGENTS.md`、808 行 `docs/MEMORY_V2_EXECUTION_PLAN.md`、v0.1 全文、UI 标准、Voice 交付索引，并核对 R2.1 工具链、插件约束及相关源码。没有导入 `backend.app.main`，没有执行初始化、迁移、模型、embedding、语音、OAuth 刷新或 MCP 请求。

本轮检查包括：

- `rg` 定位与目标源码阅读；查看既有 Git 差异，不用远端 HEAD 替代工作树。
- 对日常 SQLite 使用 `mode=ro`、`query_only=ON` 和只允许读取的 authorizer。仅查询 schema、上下文参数、模型参数、角色默认模型、会话配置分组、摘要行数及 MCP 目录；不查私人消息、提示词正文、摘要正文或凭据列。
- 从 AST 提取现有纯函数，在内存合成输入上执行；不导入应用、不建立测试数据库。结果用于核实组装公式与参数序列化，不称为完整隔离回归。
- 读取监听与 Runtime 进程的限定元数据；访问本机 `/health`，返回 HTTP 200、`yosaki-api`、`0.24.0`。未启动、停止或切换服务。
- 查阅公开官方接口资料，仅用于能力约束；没有认证请求或供应商账户查询。

本轮没有运行 build、完整后端回归或浏览器交互测试，因为没有修改业务代码。历史验收只引用其原限定范围，不追认为本轮结果。

### 1.1 比 AGENTS 和 v0.1 更新的情况

| 项目 | 当前依据和对本计划的影响 |
| --- | --- |
| Memory 索引与预算预览 | 执行计划末尾已记录分批增量索引、日常流式 v4 的 6144／8192 容量选择及常驻预算预览受控加载。AGENTS 末尾“分批工程待补”已落后。聊天应复用 `ContextMemoryPreview.vue` 和 `memory_context(preview=...)`，不另造常驻预算系统。 |
| H5-B | 13 份精修及 13 份梗概可审，05／07 已补齐；内容确认／发布仍待 Owner。本文未重新读取私人内容，状态依据最新执行记录。当前没有本批可执行的真实向量外发范围，不能把旧“三批”设想当授权。 |
| Voice AI 规划 | Voice 索引最新记录已经有一次真实规划及单样本“语义安排合理”反馈，早期“真实规划从未执行”已过时；仍未日常启用。第 3 步最新真实两段任务首段 unknown、第二段未发。聊天增强保留这些状态，不重做语音实验。 |
| 正文渲染 | AGENTS 概览提到 Markdown 渲染；当前 `App.vue:930` 实际按换行输出 Vue 文本插值，没有 `marked`／`DOMPurify`／`v-html` 调用。依赖已安装不等于当前聊天在渲染 Markdown。复制原正文仍可直接实现，不借此扩展一次全面渲染改版。 |
| 文档历史 | `docs/PLUGINS.md` 末尾“尚未登录认证”也是历史描述；当前主应用已有 session middleware。本轮只在此记录相关漂移，不改动并行维护的主指南。 |

### 1.2 当前运行与保存配置

只读查询的数据库为 `D:/AI xiangmu/自建前端/data/yosaki.sqlite3`。8000 监听 PID 14316，命令所属本项目 Runtime `development --daily-voice --daily-ttd-ws --port 8000`，与最新 Memory 加载记录对应。5173 同时存在 127.0.0.1 的 PID 23628 和 0.0.0.0 的 PID 9288；两者均未操作。未读取完整进程环境，不能据启动脚本断言所有实时开关值或凭据有效期。

| 配置 | 当前保存值 | 作用域与含义 |
| --- | --- | --- |
| 角色 | ID 1，enabled=1，default_model_id=null | 不回传角色私有资料；没有角色默认模型绑定。 |
| 历史模式 | legacy；history_limit=100 | 按消息行计数，工具关联补齐可能超过 100。 |
| strict／chunked 参数 | window_size=50，window_keep=40 | 已保存，但 legacy 当前不使用这两个字段。 |
| 记忆 | memory_enabled=1 | 每角色；Chronicle 还受进程开关及当前资格限制。 |
| 角色独白 | monologue_enabled=1；回答指导长度 0 | 独白提示正文未读取。 |
| 会话摘要 | conversation_summaries 为 0 行 | 日常目前没有保存或启用的会话摘要。 |
| 模型绑定 | 普通会话 16 个绑定档案 5、1 个绑定档案 4、1 个无绑定；另有 2 个归档会话 | 只查分组数量，不查询标题或消息；浏览器当前选中会话未知。 |
| MCP | 0 个服务、0 个工具 | 目前没有配置可用网页读取／搜索 MCP。Chronicle 内置工具不是网页工具。 |

| 档案 ID | 协议 | 保存的模型名 | 输出上限 | 保存的上下文窗口 | 推理强度 |
| --- | --- | --- | ---: | ---: | --- |
| 3 | openai_compatible | gpt-6-sol | 1024 | 32768 | 未指定 |
| 4 | openai_compatible | gpt-6-sol | 2048 | 32768 | 未指定 |
| 5 | chatgpt_plan | gpt-6.1-sol | 2048 | 32768 | 未指定 |
| 6 | openai_responses | gpt-6.1-sol | 2048 | 32768 | medium |

四个档案都保存为启用、支持流式；这些是本地配置，不是服务商能力或当前账户可用性证明。32768 是人工档案值，不是已查证模型窗口。套餐的 2048 当前没有进入普通聊天请求；专用 Voice 验收额外插入上限是另一条限定路径。

## 2 现有能力矩阵

状态以 v0.1 的目标定义衡量。“部分”表示已有可复用基础；“缺失”表示在当前聊天 UI、API、存储与序列化路径中未实现。所有用户可见能力的本轮证据均为源码核对，未重新做浏览器验收。

| 编号 | 能力 | 状态 | 当前实现与要补的部分 |
| --- | --- | --- | --- |
| R1a | 复制单条消息 | 缺失 | 没有消息复制操作。保留正文换行，默认不附推理、独白或工具载荷。 |
| R1b | 详细信息 | 部分 | `App.vue:930` 的折叠模型详情只显示模型及输入／输出／缓存值。缺逐请求身份、可靠终态、配置、耗时、快照及汇总。 |
| R1c | 单条重新生成 | 部分 | `regenerateLast`／`regenerate_chat` 仅末轮；实际删除旧消息与工具运行，没有答案版本。 |
| R1d | 修改消息 | 缺失 | 无消息编辑 API、不可变修订或人工修改归属。角色卡编辑不是消息编辑。 |
| R1e | 单条删除与撤销 | 缺失 | 只有整会话删除；没有软删除、撤销或分支依赖处理。 |
| R1f | 引用与局部引用 | 缺失 | 无引用卡、修订身份或发送时固化的引用数据块。 |
| R1g | 任意位置分支 | 缺失 | conversations 无父分支、分叉点或路径成员；按消息 ID 读取整个会话。 |
| R1h | 翻译 | 缺失 | 无辅助任务、目标语言缓存或独立计量。 |
| R2 | 真实用量与耗时 | 部分 | 助手行 `usage_json` 保存部分成功路径的供应商原始 usage；无独立请求表、可靠性标记、时钟与迟到结算。 |
| R3 | 预览与实际快照 | 部分 | `context_preview` 重算当前已保存配置，有 Chronicle 预算明细；没有最终请求体快照，预览也不含工具定义和 Responses 不透明续答项。 |
| R4 | 快捷模型与思考设置 | 部分 | 聊天可切模型，模型页可保存 effort low／medium／high；无每次覆盖、能力映射或参数生效差异。 |
| R5 | 图片／文档／截图 | 缺失 | ChatInput 仅文本；无附件表、消息内容块、上传入口、截图或原生视觉重放链。现有导入／备份文件输入不算聊天附件。 |
| R6 | 网页 | 框架部分，日常能力缺失 | MCP 确认与续答框架可复用；日常目录为空。没有 URL 读取器、hosted web search 配置或登录浏览器控制链。 |
| R7 | 上下文及累计统计 | 部分 | 只有预览估算和单助手 usage；没有持久会话／分支累计、覆盖率或辅助任务归集。 |
| R8 | 混合上下文与缓存 | 部分 | legacy／strict／chunked、手填摘要、合资格记忆已存在；无 token 驱动淘汰、增量摘要、输出预留和前缀变化诊断。 |
| R9 | 公开推理与角色独白 | 部分 | Responses 摘要、compatible reasoning_content、角色 inner／say 分区可保存展示；UI 没使用 reasoning_source 区分来源；没有完整内部思考访问能力。 |
| 保留项 | 工具、Memory、Voice | 有现有实现及专项记录 | 复用 R2.1、当前记忆资格与音频正文哈希绑定，随着新修订／分支加入兼容测试；不另建执行器、学习或语音系统。 |

关键源码定位：

- [聊天输入与模型字段](<D:/AI xiangmu/自建前端/backend/app/main.py:123>)、[模型档案](<D:/AI xiangmu/自建前端/backend/app/main.py:186>)、[消息 schema 及旧库补列](<D:/AI xiangmu/自建前端/backend/app/main.py:388>)。
- [历史读取](<D:/AI xiangmu/自建前端/backend/app/main.py:1455>)、[重生成](<D:/AI xiangmu/自建前端/backend/app/main.py:2282>)、[普通发送](<D:/AI xiangmu/自建前端/backend/app/main.py:2310>)、[工具续答](<D:/AI xiangmu/自建前端/backend/app/main.py:2094>)。
- [前端消息类型](<D:/AI xiangmu/自建前端/frontend/src/App.vue:35>)、[消息加载](<D:/AI xiangmu/自建前端/frontend/src/App.vue:722>)、[流与发送](<D:/AI xiangmu/自建前端/frontend/src/App.vue:783>)、[消息 UI](<D:/AI xiangmu/自建前端/frontend/src/App.vue:930>)。

## 3 当前请求链

### 3.1 普通发送和三种通道

`App.vue.send → sendText → POST /api/chat/stream → chat_stream`：校验会话及工具／Voice 状态，按“请求 model_profile_id → 会话绑定 → 角色默认”选择模型。无有效启用档案时进入 demo，没有独立系统默认模型层；前端 `loadModels` 首项选择不能当成后端配置优先级。

后端先读取历史、提示块、记忆和手填摘要，调用 `desires.chat_advisory`，再保存用户消息。随后 `build_messages` 组装，按通道序列化。普通生成没有持久请求占位；前端 `isStreaming` 不等于跨窗口／跨请求锁。

| 通道 | 序列化与派发 | 当前可观察差异 |
| --- | --- | --- |
| Chat Completions API | main 内构造 body → `model_url` → HTTPX stream | 发 model/messages/temperature/top_p/max_tokens/stream；有 effort 则直接发送，没有按模型能力验证。没有 `stream_options.include_usage`。 |
| Responses API Key | `responses_adapter.payload` → endpoint → HTTPX stream | 发 input/max_output_tokens/stream/store=false；effort 存在才发 reasoning summary=auto 及 encrypted_content include。温度／top_p 未序列化。 |
| ChatGPT 套餐 | `chatgpt_plan.payload` → API_BASE/responses → `access_token` → HTTPX stream | 所有 system 合并为 instructions；其余转 input；store=false、stream=true，工具包装为 yosaki namespace。普通路径不传 max_output_tokens、temperature、top_p。没有 API Key 回退。 |

三个通道都发送本地重新组装的选中历史，不是只发送新一条。Responses 两条通道会重放存于 `provider_state_json` 的 opaque reasoning items；没有使用 `previous_response_id` 或持久服务商 conversation handle。它们包含不可读状态，不能将可见文字称为全部模型输入。

成功路径：解析增量 → 分离公开推理／角色独白／正文 → 保存助手消息及 usage → 发保存凭证。工具提案另存助手批次及 tool_runs。`generation_id` 当前在内存和 SSE 中生成，没有持久化为请求身份。异常路径并非每次都会保存助手行或已知用量。

日常 Voice 的展示门控还可能缓冲套餐文字直到完成且核验允许后才发布。因此后续计时应同时记录“首个供应商正文增量”和“首个可公开正文事件”；不能把前者一律标为用户已经可见。

### 3.2 重生成

`regenerateLast → /api/chat/regenerate` 找最后用户消息，删除所有后续 messages 及其 tool_runs，置 `reuse_last_user=True` 后复用 `chat_stream`。旧答案、旧 usage、工具执行证据可能随之消失。

当前重生成历史仍包含最后用户消息，`build_messages(..., current=payload.message)` 又追加同一正文。内存合成核对得到 **同一用户输入 2 份**。应在统一组装器中明确“历史截止点”与“当前输入”，每次只包含一次。删除前的角色归属和整个生成占位也要进入同一服务端事务校验，不能先删除后由发送入口再拒绝。

### 3.3 工具确认和续答

`available_model_tools → pending_confirmation → decide_chat_tool_run → verified_tool_turn → continue_after_tool`。确认入口保留 schema 校验；续答以 `BEGIN IMMEDIATE` 和条件更新占用批次，再次核对原用户 ID、正文 SHA-256、角色、call ID、结果与历史连续性。Chronicle 工具只保存引用收据，在下次请求前 `refresh_history` 重新展开当前有资格的内容。

续答用原工具运行绑定的模型档案和当前配置重新组装，不能认为用了原请求参数快照。它调用 `memory_context`，但 **未调用／传入 `desires.chat_advisory`**。与普通发送分叉的派发、解析和保存逻辑很多，应复用一个执行入口，同时保持逐次批准和“全部处理后显式继续”。

### 3.4 额外请求

聊天语义补查是发送前的可选 A3 路径，按本条同意、独立预算与等待上限执行；预览、重生成、工具续答和 Voice 不自动另触发。未来 attempt 总览只引用它已有的任务／请求／用量，不将账本搬到普通聊天表后再计一遍。

套餐 `access_token()` 默认允许过期刷新，限定 Voice 验收使用 `allow_refresh=False`。本轮没有调用它。后续测试清单必须分别记录模型、OAuth 刷新及目录调用，不能把真实测试零模型等同零网络。

## 4 当前上下文的准确行为

### 4.1 默认和计数

[select_history](<D:/AI xiangmu/自建前端/backend/app/main.py:1538>) 提供三种策略：

- legacy：先取最后 N 条消息，再补选中工具调用的助手／结果伙伴，因此可能超过 N；没有保证该工具所属用户回合也被补入。合成 N=1 的工具结果案例得到 assistant＋tool，没有 user。
- strict：按每条 user 开始划分完整回合，保留最后 window_size 个历史回合。没有 user 的开头片段也会成为一个块。普通新输入在裁剪后追加，50 是历史回合数，不含本次新输入。
- chunked：使用回合数阶梯删头，没有生成摘要。现公式在历史回合数 50／51／59／60／61 时分别保留 50／40／48／49／40；window_keep=40 不是任何时点都精确保留 40。未来明确测试这些边界，不能只把控件改叫“压缩”。

新建角色和种子角色写 strict；旧库补行及字段默认 legacy；`ContextSettingsInput` 省略字段默认也为 legacy；GET 缺行 fallback 却是 strict。因此必须保留已有角色值，迁移不能无条件把所有人切到 strict 或混合策略。

### 4.2 实际注入顺序

[build_messages](<D:/AI xiangmu/自建前端/backend/app/main.py:1573>) 当前顺序如下：

1. 首个 system：system_start 提示块 → 角色 system_prompt → character_profile → scenario → examples；每类块按 order_index／id 排序。回答指导与角色独白格式要求追加在这个首 system 内。
2. 第二个可选 system：memory 提示块 → 人工长期／当前会话临时记忆 → 已批准学习记忆投影 → Chronicle 合资格投影 → 启用的手填会话摘要 → 简短驱力参考 → before_history 提示块。
3. 裁剪后的 user／assistant／tool 历史。角色独白和公开推理文本不从其独立字段加入。Responses 可额外带不透明 reasoning items；导入工具卡不变成可执行工具链。
4. 可选 system：after_history → system_end。
5. 当前 user 输入；工具续答没有新增 user。
6. 工具 schema 在 body 的 tools 字段，和 messages 分开。当前 MCP 查询没有显式 ORDER BY，稳定工具顺序须补。

套餐 adapter 将上述所有 system 收拢为单个 instructions，因此 after_history 在最终 wire 中已不处于历史后面。预览顺序只是组装中间态，不能当套餐最终顺序。

普通聊天没有直接注入完整 `volition.context_items`、心理状态、学习策略或“决策模块”。`desires.chat_advisory` 只按五组话题词命中，最多两项衰减后的驱力 activation；不写驱力。Phase 2／3 与行为预览使用的结构化心理／学习上下文不能当普通聊天已有接线。相关产品选择见第 10 节。

### 4.3 记忆、摘要和预算

[memory_context](<D:/AI xiangmu/自建前端/backend/app/main.py:1619>) 读取人工 long／当前会话 temporary，及经 `memory_authority.eligible_rows` 批准的学习投影（按话题匹配取最多 3）。Chronicle 使用有效缓存检索，合并显式常驻／固定及本条获准补查结果，去重后共用 **1024 估算 token／5 块**。显式选择候选超过 50 会关闭该可选通道；不会回退 RAW。

普通时间投影沿用 L0～L5；常驻／固定跨年龄保留完整已确认单经历梗概，缺失时只允许合资格标题降级。1024／5 仅是 Chronicle 子预算，不是整个模型上下文预算。

会话摘要是 `conversation_summaries(content, enabled, updated_at)`，用户手填并显式保存；没有范围 ID、版本或自动模型生成。它与 Synopsis／Temporal DIGEST 分开。没有历史删除后的摘要覆盖范围验证，新增编辑与分支不能继续盲用旧摘要。

`estimate_tokens` = ceil(每个非 ASCII 字符按 1、ASCII 按 0.25 累加)。预览只计算可见 content，不含工具 schema、工具参数、不透明状态、图片或协议开销。首 system 为 stable，其他一律归 dynamic，不能当精细来源 token 分析。

`context_limit` 与 `supports_streaming` 当前保存／展示但没有进入普通聊天预算决策；没有全请求输入＋输出预留、token 超限截断或压缩。当前序列化也未明确设置 truncation 策略，服务商超限行为未本轮真实验证。不能声称模型窗口受控或服务商一定不截断。

### 4.4 预览边界

[context_preview](<D:/AI xiangmu/自建前端/backend/app/main.py:1874>) 读取已保存配置，重新选记忆及重读 Chronicle 工具收据，返回 messages 和估算。本路径源码未见模型派发、冷却消费或活动事件写入；仍应在 P1 用数据库不变断言验证整个调用图。

预览不接受模型档案／每次覆盖，不带工具定义和 opaque state，也不模拟 Voice 的 history 过滤及展示路径。因此 P1 必须区分逻辑预览、模型最终序列化预览、已派发快照三者；旧历史不能回填“当时实际请求”。

## 5 优先修复的已核实差异

| 问题 | 当前证据 | 实施处理 |
| --- | --- | --- |
| 旧答案与已发生用量被重生成删除 | main:2282 直接 DELETE 后续 messages/tool_runs | P1 先停止破坏式重生成；建立候选身份，P2 接完整版本与任意位置路径。 |
| 重生成重复用户输入 | main:2428；现有纯函数合成执行得到 2 份 | 统一历史截止点与 current 规则；正常发送／重生成／续答分别断言。 |
| incomplete 的可靠 usage 丢失 | responses_adapter.StreamState:195 仅 completed 提取 usage；合成 incomplete 有 usage，normalize 返回 null | 统一终态解析，完整性与可靠用量分开；failed/incomplete/取消均可保留可信部分。 |
| 取消和错误没有独立请求计量 | main:2210、2521 的 stopped INSERT 无 usage_json；多个错误分支仅发 SSE 错误返回 | 派发前持久化 attempt；用量独立于候选保存；缺失为 unknown，不按 0 入账。 |
| 终态完整性判断不一致 | 普通 API EOF 没有与套餐相同的完成检查；compatible 的 length 只记录 finish_reason；reply_events 还可能覆盖 generation_status | 规范化 provider_terminal、display_status 和 request_status，不能拿正文标签是否闭合覆盖供应商 incomplete。 |
| 配置看似生效但没发送 | 套餐 payload 无 max_output_tokens；Responses 不发温度／top_p；context_limit 未参与预算 | 按通道能力验证、显示请求值和 wire 值；不能单靠改 UI 标签宣称修复。 |
| 普通发送与续答注入差异 | 续答 build_messages 未传 desire_advisory | 共用组装器，使用原用户 topic 与当前有资格参考，不新增活动事件。 |
| 普通生成缺跨客户端占位 | chat_stream 没有普通生成持久租约；isStreaming 仅前端状态 | 同一分支单活动生成，expected_head／修订校验；取消后旧流不得提交到新路径或自动朗读。 |
| 模型返回身份与统计不完整 | messages.model 保存请求模型；usage 未保存响应模型、请求时钟或可靠性 | attempt 分别存 requested／returned model；历史缺字段明确“未记录”。 |
| 缓存布局没有真实观测 | 动态记忆／驱力位于历史前；套餐合并 system；无前缀诊断 | 先记录实际请求，再按通道调整稳定顺序；不为了缓存推迟撤权或状态更新。 |

以上为本轮发现和复现，尚未修复。不会用“P0 已完成”暗示这些问题已消除。

## 6 最小兼容数据与接口方案

### 6.1 保留的权威结构

`messages.id` 继续是原消息身份，原内容与工具锚点不就地覆写。现有 `reasoning_content`、`monologue_content`、`reasoning_source`、`generation_status`、`usage_json`、`provider_state_json` 和三个 origin 字段保留。`assistant_completion_receipt` 的正文版本哈希可做兼容校验，但它不是可切换的消息修订记录。

M1 的 `memory_v2_sources.source` 直接从 messages 取正文、角色、状态及指纹。新分支引用原消息时不得复制来源或自动失效旧审核；新修订则必须获得独立版本身份，不能借旧消息审核成为已审内容。

### 6.2 拟新增的数据

以下是实施建议，不是已存在 schema；P1 完成身份设计，按依赖逐批显式安装，不机械一次创建所有表。

| 数据单元 | 最小内容与约束 |
| --- | --- |
| `chat_attempts` | UUID、owner／character／root conversation／branch／turn、purpose、origin revision、候选引用、协议／档案及请求与返回模型、有效参数、派发状态、UTC 和单调时钟差值、可靠性、原始安全 usage、规范化 usage、mapping version、结算指纹。请求实际派发前写记录；候选为空也能保留费用事实。 |
| `chat_request_snapshots` | attempt 唯一绑定，最终序列化的可检查 JSON、来源块 manifest、原顺序、组装版本、请求哈希。凭据、签名地址和不透明状态内容不进入展示快照；后者只显示安全类型／哈希与不可读标记，原受控存储继续负责重放。 |
| `chat_message_revisions` | message_id＋不可变 revision_id、父修订、正文或原消息不可变引用、作者类别（模型／人工／导入）、attempt、时间、状态与正文哈希。旧消息初始修订不必复制正文；必须能恢复实际版本，不能仅存哈希。 |
| `chat_branches` 与路径成员 | 根会话、父分支、分叉 revision、当前 head／并发版本；有序成员引用 message revision，选中候选、删除标记及撤销信息。共同祖先共享引用，不复制请求或账目。 |
| 活动生成占位 | 与 branch 关联的 attempt／租约／状态及 expected_head，可放在 branches／attempts；无需新独立调度系统。进程丢失后可能已派发记 unknown，不能自动重放。 |
| `chat_quotes`、`chat_translations` | 引用绑定实际源修订和发送时片段；译文按修订、语言、模型、提示版本缓存并关联辅助 attempt。来源改变显示过期，不悄悄替换原文。 |
| 附件与修订绑定 | attachment ID、角色／权限、真实 MIME、字节／内容哈希、保存位置、转换及页码信息；revision 引用与 refcount。文件正文不进入普通日志。 |
| 连续性摘要版本 | branch、覆盖的精确 revision 集合、来源指纹、内容、版本、生成 attempt／人工修改、失效状态。旧手填摘要继续保留为人工参考，不猜覆盖范围。 |

生成候选可复用 revision＋attempt 关系，不单独再建一套生成生命周期。普通聊天需要独立 attempt 记录，因为 A3 的 readSet／field-write／Owner 派发证明是记忆加工协议，强塞普通流会改变既有账本；只复用可靠用量规范化、幂等和 unknown 原则。Memory／Voice 辅助消耗通过外部引用关联，保留各自权威账本。

### 6.3 接口与语义

保留 `/api/chat/stream`、`/regenerate`、工具决定／继续等既有入口，增加可空 `branch_id`、`expected_head`、源 revision、候选和 request override。旧客户端仅能操作兼容的旧线性会话；新格式启用后不能让省略分支参数的旧客户端读取所有候选或修改错误路径。

拟新增只读 attempt／快照／会话统计端点，以及修订、软删除／撤销、分支、引用、翻译和附件端点。每个写操作检查 owner、角色、会话、归档状态、修订及活动生成／工具状态。具体路由名称服从现有风格，UI 不暴露内部表名。

- 末尾编辑生成新修订；有后续时创建分支，原路径后续保留。任意助手重生成从对应用户回合及其之前路径重新构造，不带未来内容，也不自动复演旧工具副作用。
- 旧工具卡在新分支是不可执行历史证据。续答必须绑定新有效的提案及用户修订；跨服务商 opaque state 不混用。切模型遇到无法安全转换的待续工具链时明确阻止。
- 默认删除仅移除当前路径并可撤销；其后续依赖标为不再可延续或进入新路径。必须区分“从此路径移除”和“撤回来源”。前者不破坏其他分支共享的原始来源，当前分支的自动召回须排除被移除的直接依赖，避免从记忆绕回；真正撤回来源则让所有相关读取、摘要、精修、引用及快照拒绝旧内容。
- 本轮不新增永久清除的独立产品功能；现有整会话／角色删除必须覆盖新增正文、附件绑定和诊断快照，并保留允许的最小计量事实。旧备份中的历史副本不能宣称因当前删除而被追溯抹去。
- 普通消息列表、组装、导出、摘要、M1、Voice 等消费者全部经同一分支／修订解析边界。M1 旧消息 ID 继续指原始版本；新增修订来源必须明确新身份和新指纹，不改旧收据、不继承审核。仅创建分支不得产生新的记忆、反馈或学习。

### 6.4 计量与快照

input／output／cached read／cache write／reasoning 均允许 null。缓存读写是输入子项、reasoning 是输出子项；协议另报的字段按其定义归一化。汇总按 attempt ID 去重，输入为 0 时命中率不适用，缺失缓存不算 0。旧成功消息可作为“历史消息 usage”单列，不能伪造完整 attempt 身份或可靠派发时间；导入 usage 不算本机实际消耗。

保存 first provider text、first public text、last text、终态和派发时间；输出耗时按明确选定的正文事件计算。Voice 门控、人工工具等待、界面耗时分列。部分失败有可靠用量仍保留；迟到用量用原身份幂等更新，不产生新账目或覆盖新候选。

快照在最终 body 已定、鉴权头尚未包装时写入；发送使用同一冻结值。诊断访问再次验证来源权限，撤权块不给旧正文。依赖外部状态不能重建时显示缺口。元数据快照不能称为完整正文快照。

快照随会话保留，支持明确清除其正文而保留最小用量；默认聊天导出不包含快照、opaque state 或秘密。服务端 SQLite 当前并非整库静态加密，沿用同等私人数据保护及加密 `.ybackup`，不能宣传新增快照天然获得额外静态加密。

## 7 附件和模型能力落点

P1 新增版本化 `model_capabilities` 模块，以通道＋协议＋模型匹配，记录来源（官方／本机已验／自定义／未知）。档案保存值与能力分开，服务端拒绝显式不支持项。思考开关、effort、公开摘要展示是三个不同状态；不能把不显示摘要当作关闭推理。

P3 优先贯通 TXT／MD／代码纯文本、PDF／DOCX 本地提取及图片内容块。PDF 按页报告空白／扫描页／提取遗漏，DOCX 不执行宏；依赖在实施时锁定并检查许可证及资源限制，不复用桌面工具安装环境作为部署依赖。原生文件作为有明确能力证据的路径；套餐支持情况当前未知，默认不开放未经核验的原生文件／视觉组合。

前端放到 `frontend/src/chat/` 的附件输入、缩略图和捕获组件；文件选择、粘贴、拖放、截图单帧／裁剪均走同一上传与修订绑定。服务端校验真实 MIME、尺寸、页数、解压后体积及角色权限；不信任扩展名。上传成功不意味着已送给模型。

原文件建议作为受大小约束的 SQLite BLOB 或同库分块保存，复用当前整库加密备份，避免文件放库外却漏备份。转换正文及衍生预览可重建并注明版本。若实际容量验证迫使采用外部 blob，必须先补对应备份格式与恢复校验，不把附件排除后仍叫全量备份。

网页当前没有现成 MCP。P3 交付独立默认关闭的最小公开 URL 读取工具，复用现有逐次确认／工具结果续答；如届时 Owner 已配置合适 MCP，则复用而不重复建设。限定 HTTP(S)、响应类型／大小／时间和重定向次数；DNS 解析与每次跳转均检查并固定合法连接目标，拒绝回环、内网、云元数据与 IPv4／IPv6 绕过。保存原 URL、最终 URL、抓取时间、引用及失败原因；不支持登录或 JS 页面时明确提示，不启用浏览器自动操作。

官方资料只证明公有 API 的协议能力，不证明本地已接通或套餐同样支持：

- 缓存要求可复用前缀及兼容设置，还受模型的缓存查找边界影响。相同文本前缀不保证实际命中；本轮不增加预热或显式缓存参数。[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- 图片需要实际图像内容块；本项目当前字符串聊天路径没有贯通该能力。[Images and vision](https://developers.openai.com/api/docs/guides/images-vision)
- 当前 File inputs 文档区分 Responses 与 Chat Completions，并说明非 PDF 文件嵌入图片／图表不被同样提取；本地提取也不能冒充原生视觉。[File inputs](https://developers.openai.com/api/docs/guides/file-inputs)
- reasoning summary 与 opaque reasoning state 不等于原始内部推理正文。[Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)

以上页面于本轮读取。未复制 Operit 代码、未引入其许可或 Android 布局；套餐与自定义兼容 API 的能力仍须各自证据。

## 8 修订后的连续实施批次

P0 到此交回实施稿。后续获准实施后 P1～P4 连续开发、Mock 验证、修复和同步文档，不在每个按钮或批次边界请求继续。真实外发、日常数据迁移及服务切换继续按具体授权。

| 批次 | 实际交付 | 预计修改和新增模块 | 完成依据 |
| --- | --- | --- | --- |
| P1 请求记录和统一组装 | 持久 attempt、终态／用量、快照／详情、三通道能力与快捷覆盖、基础统计；修复重复输入、遗漏 usage、套餐参数与续答差异；为保留候选先落实最小修订身份 | 改 main.py、responses_adapter.py、chatgpt_plan.py；新增 backend/app/chat_requests.py、chat_context.py、model_capabilities.py、chat_schema.py；App.vue 仅接线，新增 chat/RequestDetails.vue、ModelQuickSettings.vue，复用 ui/ChatDrawer.vue 与 ContextMemoryPreview | 每个模拟派发在 wire、数据库和 UI 身份一致；缺失／重复／迟到 usage 正确，预览不写业务，旧记录不伪造；最后一轮重生成不再删旧消耗。 |
| P2 消息操作和分支 | 八项操作一次完成；版本切换、任意位置重生成、历史编辑、软删除／撤销、引用和独立翻译；全链依赖与音频适配 | 新增 chat_history.py、chat_auxiliary.py 及 chat/MessageActions.vue、MessageEditor.vue；改普通消息列表／组装、archives.py、memory_v2_sources.py 的修订资格适配、Voice manager／绑定与 receipt 消费者 | 新路径无未来消息、旧工具不可执行、旧账目不复制；源撤权不从记忆／快照／音频返回；刷新和并发旧流不污染选中修订。 |
| P3 附件和最小网页 | 图片／文本／PDF／DOCX／截图发送与历史重读；能力冲突显式处理；可关闭公开 URL 读取 | 新增 chat_attachments.py、必要的文档解析和 URL 读取模块及前端 chat/AttachmentInput.vue、CapturePreview.vue；扩展 adapters、备份及 Runtime schema 检查 | 校验实际 wire 内容块、权限、缺页、超限、取消、分支与重生成；不支持模型不丢图；URL SSRF／跳转及工具确认通过。 |
| P4 混合上下文与完整统计 | token 预算、完整回合分块、增量连续性摘要、覆盖依赖、窗口切换预览、前缀变化解释、分支／辅助统计 | 扩展 chat_context.py、chat_requests.py，新增 chat_summaries.py 与上下文设置／统计组件 | 长会话合成事实、更正及工具链保持；摘要失败不丢历史；移除／编辑／分支正确失效；实际消耗覆盖删除和替换，按 attempt 去重。 |
| P5 冻结验收与受控接入 | 最终专项与必要完整隔离回归、桌面／手机／浮窗流程、迁移恢复演练及具体真实测试单 | tests、受保护迁移脚本、yosaki_runtime.py、README、AGENTS、相关记录 | 验收逐需求映射；冻结字节明确；真实套餐／API、用户体验、正式接入各列结果，未测不填通过。 |

新增文件名为建议，实施时可按现有边界合并；不要求机械产生一表一模块。避免继续把所有业务堆在 main.py／App.vue，也不做整仓重构。

P4 默认策略：保留旧角色当前配置；新混合策略先手动选择、自动压缩默认关闭。预算阈值 75% 触发、回落 55%～60% 仅作隔离参数起点。用户启用自动压缩时明确模型、通道和预算；每次仍记录辅助 attempt，不借 Memory 付费流水线。正式默认迁移应有对照证据后再决定，不在 P1 悄悄改掉日常 legacy。

## 9 迁移保护与验收

### 9.1 迁移和回退

1. 在工作树外建立候选，记录当前相关源码及并行 Voice／UI／Memory 字节。当前已修改和未跟踪文件都属于基线，不 reset、clean、stash 或整文件覆盖。
2. 新 schema 先在全新合成库与“旧 DDL＋合成行”副本验证，显式幂等迁移；不依赖导入 main 自动改日常库。旧消息、origin 锚点、审核与账本原行保持；旧历史没有真实快照，不追补。
3. 建立旧消息的默认路径／原始修订时仅做明确映射；归档仍 archive_only，不得因分支或导入而恢复可聊天状态。历史用户手填摘要保留但不假定覆盖任何新分支。
4. 验证 V1／V2 加密备份、隔离新目录恢复、附件完整性、快照权限及秘密剔除；session、模型／MCP 凭据、Voice 同意与运行态按现有策略处理。新增不可读 provider state 不进普通导出。
5. 正式接入前给出实际 DDL／文件差异、备份恢复结果和加载清单；Voice 空闲、无活动生成或工具继续后沿 Runtime 预检加载。保留原开关，不改 Memory 付费开关／费率日期。
6. 回退先停止新功能派发，保留数据和兼容读取。产生修订／分支后，旧线性读取器不能直接接管新格式会话；需要兼容版本读取或明确只读锁。源码仅恢复仍匹配本批且无后续修改的文件；不整库覆盖，不删除 attempt、审核、unknown 或预留。

### 9.2 必要测试

| 范围 | 复用基础 | 新增重点 |
| --- | --- | --- |
| 三通道与工具 | test_local_acceptance.py、test_tool_anchor.py | 三协议正常／多请求工具／重复终态／incomplete／EOF／断线／取消／迟到 usage；跨模型 opaque state；无凭据真实外呼。 |
| 历史与来源 | Memory 来源、导入、审核、Chronicle 工具专项 | 中间编辑／分支／删除、共享祖先、来源撤回、归档拒绝；旧已审内容不被新修订冒用，RAW/CLEANED 不越界。 |
| 请求可检查性 | context budget preview、现有组装用例 | 快照和最终 wire 一致；身份／顺序／来源／参数可追；预览全库不变；权限撤回；正文不误脱敏成另一份提示。 |
| 计量 | 当前 usage 与 A3／Voice 各自账本专项 | 总输入输出与缓存／推理子项、未知覆盖率、删除后消耗、导入排除、分支共享去重、辅助账本只引用。 |
| Voice | chat lifecycle、auto read gate、continuous、receipt／播放器专项 | revision 绑定、旧流取消、工具回合／版本切换不误播旧内容；主动录音草稿、停止、同意和额度状态保留；零 ElevenLabs。 |
| 附件／网页 | 新自含合成夹具 | 图片特有内容、PDF 扫描页、DOCX 体积与路径、错误 MIME、共享附件回收、DNS 重绑定／跳转／响应上限、工具逐次确认。 |
| 上下文 | 现有 strict／chunked 与记忆预算专项 | 完整回合与输出预留、未知容量、摘要覆盖范围及失败、模型变小、稳定顺序与前缀变化位置。 |
| UI | UI 标准及 design-qa.md 既有流程 | 浅／深色、桌面、390×844、浮窗／全页；菜单／抽屉／键盘焦点、未保存、错误、刷新、录音／播放／工具待确认；17px／1.75 不缩字。 |

P5 在最终冻结候选上完成一次必要完整隔离回归、前端 build、Python 静态检查和隔离 health/API／浏览器工作流。测试导入前设置隔离 YOSAKI_DATA_DIR，禁用真实凭据与外部传输，复用合成 fixtures；不能直接从本工作目录跑测试然后假定未碰日常库。通过后只对新增变化和失败复测，不重复堆数字。

### 9.3 真实测试仍缺的条件

本轮真实模型／embedding／语音／OAuth／MCP 调用均为 0。公开文档浏览和本机健康检查另计，不称“零网络”。

真实前应冻结：用户选定的套餐模型及一个 API 档案、协议与能力、合成角色／会话和图片文件、每条完整请求／输出上限、最大请求数及取消规则。套餐的 2048 不能沿用当前未发送的字段作预算保证；必须先把真实通道的上限支持证据补齐。API 价格在实际执行时核对，不沿用 Memory 的费用授权或硬编码费率。

真实组优先以少数会话合并覆盖 v0.1 的十二类场景；不为每项另开付费批次。不自动刷新账户、测试连接或扩成第二个 API 协议；需要的账户刷新列入具体清单。失败／unknown 停止相应请求，不换通道、不补发旧任务。合成记忆正例必须独立建立并确认，不把 H5-B 待审草稿改成合格样本。

## 10 需要集中决定的产品范围

只有一项现状差异需要产品方明确，其他技术选择可按本稿推进：

**普通聊天是否新增结构化意愿、心理和学习策略参考？** 当前只有简短驱力与已批准学习记忆投影。v0.1 的“沿用现有系统”与真实注入验收场景容易让人误以为完整状态已经进入普通聊天。

- 建议首版保持现有注入范围：详情明确显示“本请求未注入”，先完成统一组装、修订和可观测性。Phase 2／3 原有结构化上下文不受影响。
- 若要求普通聊天也使用这些参考，应明确批准增加只读、有界且可关闭的注入，复用原模块，保留来源和预算，不触发观察／活动／反馈写入。该选择只影响这部分接线，不能阻塞 P1 其他已确定工作。

新增自动压缩默认关闭、旧角色配置不变、网页入口默认关闭、翻译首次选模型后点击即派发，均按 v0.1 的既定方向处理，无需再拆成多个选择题。真实模型／账户／费用／日常接入在实现完成后提交具体清单，不在 P0 索取密码或泛化外发批准。

## 11 本轮交付台账

| 项目 | 本轮结果 |
| --- | --- |
| P0 现状矩阵、实际请求链和上下文解释 | 完成，含实际只读配置与源码符号位置。 |
| 关键差异核实 | 重生成重复输入、chunked 边界、legacy 工具补齐、incomplete usage 与通道 payload 已用现有纯函数合成执行核对；其他项为源码检查。 |
| v0.2 实施稿 | 完成，包含最小 schema／接口、模块清单、连续批次、迁移和回退、验证与一项产品选择。 |
| 交付复核 | 15 个本机源码链接的文件及行号范围有效；审计前后所记录的 507 个现有源码／文档文件 SHA-256 一致，原 v0.1 未改。 |
| 功能实施与修复 | 未开始，按 v0.1 P0 边界执行。 |
| 日常数据／服务 | 仅限定只读查询及 health；无迁移、重启、业务写入或审核发布。 |
| 新增真实调用 | 模型 0、embedding 0、TTS／Scribe／WS 0、OAuth 0、MCP 0；没有读取凭据正文。 |

本轮交付文件为本文。v0.1、AGENTS、Memory／Voice／UI 基线和既有业务源文件保留。下一步是审阅本实施稿及第 10 节的注入范围，获准后连续执行 P1～P4；P0 完成不代表聊天增强功能已经交付。

## 12 Owner 确认与本批实施约束（2026-11-18～19）

Owner 已批准按 v0.2 连续实施 P1～P4。普通聊天保持既有注入范围，补齐普通发送与工具续答的参考接线；不新增完整结构化意愿、心理或学习策略。详情明确区分未接入、关闭、未选中、资格拒绝、预算拒绝及错误。

- 分支隔离覆盖摘要、临时记忆、Chronicle 和工具重读。角色级共享长期记忆与分支衍生来源分开；用合成精修正文正反例验证，禁止原路径分叉点之后及其他候选绕召回进入新分支。
- 没有覆盖范围的旧手填摘要不自动继承。原内容可查看，只有明确选择才成为当前分支人工参考，不补造 coverage。
- 套餐上限以通道能力证据及实际序列化为准。不支持／未核验时如实提示；客户端停止不作为供应商硬上限或费用保证。
- 允许必要 Memory 来源和 Voice 消费接线及隔离回归，不扩大为 Memory 加工、学习规则或语音实验。真实外发、日常迁移和服务切换在候选完成后按具体清单审阅。

当前交付：`docs/CHAT_ENHANCEMENT_CANDIDATE.md`；加载清单：`docs/CHAT_ENHANCEMENT_LOAD_CHECKLIST.md`。本隔离副本不代表日常已启用。
