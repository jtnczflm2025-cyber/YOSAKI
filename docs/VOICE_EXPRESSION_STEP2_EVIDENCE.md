# 第 2 步表达规划隔离实现与验收

### R2 用户听评已记录（2026-11-18）

用户原话：“两位置候选的听感没有问题，很自然”。本条56字、thoughtful＋sighs两位置候选记为**单样本声学听评限定通过**。仅说明该次整体自然度获认可，不推定相邻未标注句不受影响、标签能精确结束、能恢复克制或效果稳定；不编造评分，不代普通版添加未反馈的评价。真实AI规划未调用，判断质量仍未验证，新能力未日常启用，第3步未开始。两份音频、原执行结果和已占用收据保留；本次记录新增真实调用0，费用继续未知。原始反馈另存运行目录 listening-feedback.json；原执行审计中当时的pending状态不改写。


日期：2026-11-18（香港）。当前状态：**隔离通过，真实接线仅模拟验证，待声学听评**。未日常启用。

下方“第 2 步原批次”描述保留其当时缺少真实适配器的状态；最新接线与新候选见本页末尾，不把旧代码证据冒充新版。

## 第 2 步原批次（历史适用范围）

## 实际实现

- `annotation_planner.py` 复用原 Speech Planner 与公开正文过滤。可信段/句 ID 绑定安全文本哈希、码点区间及分句版本；来源保留 Markdown 块区间，无法精确逐字映射时明确降为 whole_message，不编造位置。原消息不写回。原过滤规则仍会规范 Markdown 内部空白，标注前后的安全正文及段间空行逐字保留。
- 严格校验 schema、输入指纹、片段归属/粒度、8 项上限、白名单、重复及段句重叠。模型不返回正文、标签或任意解释。按多个合法起点插入可信标签，按段/按句/稀疏/相邻标注仍为一个 HTTP 请求。没有重置标签，也不声称标签能在片段末端精确结束。
- 完整回复一次假模型规划，模型请求含 512 输出 token、20 秒总期限及零重试。输入只发送过滤后公开正文/有限公开语境、声音人格、注入的只读白名单快照；不调用 Psychology、Memory、原聊天或工具。测试中快照来自固定合成 Source，没有日常快照读取。
- `annotation_store.py` 只使用仓库外独立 SQLite，拒绝项目目录和非实验库。持久 operation 与输入唯一约束、事务占用、防并发重复；重启后悬而未决的规划/合成为未知，不重发。每次修改追加新版本；预览、文本、标签、参数及版本共同确定音频指纹。旧方案与旧缓存保持可读，不写 `voice_expression_audio` 或 V2 绑定。
- `annotation_lab.py` 只安装在独立 FastAPI 实例，默认关闭，人工启动时显式 `--enable`。只接受合成夹具身份；本地独立 HttpOnly session 和来源检查，不复用日常登录或读取其数据库。没有真实规划连接器，构造器拒绝非 mock provider。独立启动脚本在 Windows 事件循环自唤醒 socket 建立后禁止后续所有外连；未加载任何供应商凭据。
- 页面默认普通无标签，AI 建议与小标均主动选择。支持显示/隐藏、按段/句编辑、移除、未保存提示、完整预览、历史版本、旧缓存测试音、取消。手选 thoughtful/普通模式零规划调用。未接入日常自动朗读、通话、WS 或真实 Provider。
- 假 TTS 收到预览相同的文字与目标参数，仅返回本地合成 WAV 测试音。它不是阿夜音频、不是实际 MP3 接口验收；请求格式预览仍明确 `eleven_v4/mp3_44100_128/ja/settings omitted`。

## 直接可核对的四项渲染结果

| 样本 | 安全文字／含标签请求字符 | 插入位置（码点） | 请求数 |
|---|---|---|---|
| 整条零标注 | 26／26 | 无 | 1 |
| 中间一句 | 30／43 | thoughtful @8 | 1 |
| 不同段不同意图 | 35／56 | thoughtful @0；sighs @17 | 1 |
| 相邻句不同意图 | 30／53 | thoughtful @0；relaxed @9 | 1 |

每项从请求文本删除**记录中的插入区间**即可逐字恢复安全正文；不靠删除所有方括号的宽泛正则获得通过。标签本身及后随空格全部计入送读字符。

## 测试与浏览器证据

- 最终新增专项 **32/32**，`python -m unittest tests.test_voice_annotation_lab -v`，16.161 秒。日志在仓库外 `%LOCALAPPDATA%/YOSAKI/voice-runs/annotation-step2-20261118-local/targeted-final.log`。
- 既有 HTTP 与表达相关回归 **54/54**。此前组合命令 `tests.test_voice_annotation_lab tests.test_voice_v2 tests.test_voice_v3_expression` 共 82 项通过，44.570 秒，其中旧版本新增专项为 28 项。随后新增专项扩至 32 并单独通过；不把 82 和 32 相加，也不称新版全后端回归。
- 前端 `vue-tsc --noEmit` 与 `npm run build` 通过；独立页面 Vite 构建通过。日常构建原有大 chunk 提示仍在，不新增依赖。独立产物留在仓库外 `annotation-step2-20261118-local/build-final`，没有复制到日常服务。
- 通过本地实际页面 `18889/voice-expression-lab.html`、独立 API `18888` 的浏览器工具操作验证：默认无标签/小标隐藏；中句建议；修改 thoughtful→resigned 生成第 2 版；移除生成第 3 版；第 1 版缓存仍能播放本地测试音；模型计数保持不变。不同段、相邻句、零标注均预览并派发到假 TTS；按段和按句编辑入口均实操。取消后预览消失、旧结果不播放。
- 独立后端重启与刷新后，假模型 4 次/假 TTS 5 次持久计数保留；再次取同一计划不增加规划。后续选择不同粒度是不同输入，另产生 2 次本地规划，非同一输入重试。真实 Provider 始终 0。
- 实际浅色桌面、390×844 手机浅/深色可读；正文 17px、行高 29.75px，document scrollWidth 375≤390，无页面横向溢出。复用 shared tokens、GlassSurface、ThemeToggle。截图在本轮浏览器工具中实际检查，不虚构磁盘截图文件。只验隔离页，不冒称新增日常浮窗或设备矩阵验收。
- 浏览器 happy path/取消后的最后改动仅为后端把未尝试模型的已知预检失败分类为 fallback，零派发；新增确定性专项覆盖该分支。没有重跑不受影响的全部页面流程。

最初专项的边界样例少算标签长度、Windows 夹具连接未显式 close、前端 plan 空值类型，以及用短 sleep 猜撤权时点均已修正。竞态改用 Event 明确关联“已派发→撤权/编辑→放行迟到结果”，没有放宽权限或未知结果规则。独立启动首次外连守卫误拦 Windows 事件循环自唤醒 socket；把守卫移到循环创建后再启动服务，仍禁止所有随后外连。日常启动脚本未改。

## 保留与未完成

- 全部旧普通/表达音频、历史收据、真实用量及长文未知结论保留。本批未读取或修改日常库、Profile、告知、额度、Memory 或服务。
- 本批实际真实模型、OAuth、目录、Scribe、HTTP TTS 和 TTD WS 均为 **0**。实验 SQLite 中的 claim/usage 均为假供应商数据，不是收费批准收据或真实用量。
- 真实 AI 标注质量、真实规划供应商适配/授权门控、多位置标签对后句的影响和恢复克制方式尚未验证。日常范围不扩大。
- [原 R1 听评提案](VOICE_EXPRESSION_LISTENING_CANDIDATE_R1.json) 是待批准提案，可由 `python -m scripts.voice_annotation_candidate` 零外呼重算。它故意冻结两位置标注，先听声学差异；**0 次真实模型，2 次 HTTP，共 91 字符**，不把假模型建议当成真实 AI 输出。现有 lab 无 real-execute 开关，真实执行前还须绑定受控评测派发门控、实际权限及专用收据；本轮未创建它们。

独立 18888／18889 测试实例已关闭，仅保留仓库外实验记录、测试日志及构建产物；未操作日常 8000／5173。最终候选 21 文件哈希及规范化清单一致性、本地文档链接和根目录附件不变复核通过。


## 本批：模型适配与固定 HTTP 门控接线（2026-11-18）

**隔离通过，真实接线仅模拟验证，待声学听评。** 新增代码没有安装到日常服务，没有进入第 3 步。未读取真实凭据或日常库；真实模型、OAuth、目录查询、Scribe、HTTP TTS、TTD WS 全为 0。所有批准样例均为临时 mock-only 数据，不存在本候选的真实收费收据。

### 实际变更与复用边界

- 新 `backend/app/voice/annotation_model.py` 的 ProfilePlanningAdapter，复用 chatgpt_plan.payload、responses_adapter.payload/iter_sse_events/StreamState 和当前 AnswerDisplayGate。显式选择的同一档案 ID、协议、模型、参数及公开版本指纹进入输入绑定；不猜选模型、不改保存的 max_tokens，不导入 main.py、OAuth 运行器、Memory 或真实聊天执行流程。
- 支持既有 `chatgpt_plan`／`openai_responses` 档案；其他协议明确零派发。实际传输前要求整数 max_output_tokens=512，工具为空且 stream=true。20 秒 asyncio 总期限覆盖注入的凭据回调、连接、完整 SSE 读取与 EOF，不以 read timeout 冒充总期限。底层 retries=0、无重定向、trust_env=false；代理显式注入。
- 明确完成/失败/截断/EOF 缺完成/未知事件分别分类；只有完成、流收尾、回答绑定和展示允许后，才做严格 JSON 与原可信标注检查。重复 JSON key、NaN 或非法标注不接受；已观察可靠 usage 保留。取消关闭传输，不因 fallback 重试。后续真实 AI 质量仍待单独批准。
- `annotation_planner.py` 仅支持传入所选档案绑定；`annotation_lab.py` 加显式所选 profile_id 校验与 PlanningError 分类/usage 保留。lab 构造器仍拒绝非 mock provider，启动脚本的禁网策略不变。没有给 lab 增加通用外连按钮或 API。
- 新 `annotation_listening.py`／`scripts/voice_annotation_listen.py` 单独控制两份固定文本。复用原 ElevenLabsAdapter.synthesize_http、MP3 校验、Profile/告知读取、owner/day 预算 reserve、原结果目录保护和原子 JSON 写入。只新增默认 dry-run／显式执行／只读重播入口，不新增 Schema。
- `realtime_receipt.py` 把原 O_EXCL＋fsync 占用代码提取为 occupy_once，由原 claim 继续使用同一 marker 结构。新门控绑定固定 run 目录、approval.json 和 result 子目录，改名/复制收据及切换输出路径不恢复额度。收据占用后崩溃或未知均保留，模拟收据不能用真实 transport。
- 每份外呼前复用实际日额事务预占，失败/未知不返还；保留“已预占但未派发”和“已尝试但结果未知”。音频写仓库外独立评测目录，不写 voice_expression_audio/V2 绑定，不覆盖旧文件。
- 新静态 `frontend/voice-expression-listen.html` 复用银灰 tokens/silver 样式。两个原生音频控件，播放另一版会暂停前一版；没有合成接口。读取校验音频哈希；本地短期 cookie、来源检查、白名单事件及最多 128 条软件事件。日志不含 Key/Voice ID。非盲听、一次生成不证明稳定性。
- `scripts/voice_annotation_candidate.py` 扩大到全部直接 Voice 依赖的 65 文件集合；原 R1 JSON 原样归档。没有改后端依赖、npm 依赖、日常 App.vue/Voice 设置或启动入口。

### 实际受影响验证

- `python -m unittest tests.test_voice_annotation_wiring tests.test_voice_annotation_lab tests.test_voice_v4_product -v`：**69/69，76.700 秒**，日志 `%LOCALAPPDATA%/YOSAKI/voice-runs/annotation-wiring-20261118-local/affected-frozen-final.log`。其中接线 21 项、原标注 32 项、既有产品 WS/收据 16 项；不是全后端回归，不与历史 54/82 相加。两项实际墙钟测试分别等到 20 秒/30 秒总期限结束，假请求各一次，未缩短定时器冒称通过。
- 此后仅补一项实际 lab 路由的模型适配结果测试：**1/1，1.021 秒**，`adapter-outcome-final.log`。合成原始 SSE 经选定档案适配器：未知事件持久 failed_unknown；重复 JSON key 为已知 fallback、保留 usage=130；同 operation 重复 POST 不再次派发。这次只添加测试，未再改业务代码，未重复无关回归。
- 覆盖实际 wire 的两种已有 payload 构造、所选档案不变、最终512上限、20秒/零重试、原始 SSE 跨 UTF-8 数据块、严格结构、完成与截断/失败、未知、取消/可靠 usage、档案漂移及 cap/期限错误零派发。
- 两份 HTTP 实际 MockTransport 请求为固定 35/56 字、ja/v4/mp3、settings 省略；先后而非并行，累计2/91。首个429/ReadTimeout/取消/30秒总期限均停止第二份；占用不释放、不重复派发。缺/错/模拟收据、日额不足、告知撤销、关闭门控、代码/正文漂移及改名收据均在传输前拒绝。
- 浏览器内嵌 Chromium 经新 review-only 服务18890，播放实际假 HTTP 返回的结构有效 MP3（24 个本地合成帧，解码0.6255秒）。普通版及两位置版均 play→ended、error=null；普通版明确 pause→继续→ended，刷新重播成功。不是阿夜音频、无真人听感，不能冒称真实供应商 MP3 验收或 Edge 设备验收。
- 浏览器记录在 `browser-fake-result/playback.json`，两个音频及 result.json 同目录。假派发保持2，播放器根本没有生成接口。浏览器核验之后播放 HTML/review_app 未变；后续只加固定派发目录约束和回归测试，新增约束由上述最终组合验证，不用旧浏览器结果冒称新派发真实成功。
- 没有 TS/Vue 页面改动，沿用上一批类型检查/构建的原适用范围，不重复全前端构建。新增静态页面已通过上述实际浏览器播放。18890 测试实例已停止，日常8000/5173没有操作；不删除实验/历史证据。

准备阶段首先发现候选清单误列不存在的 Python namespace __init__.py、临时测试输出与假源库同目录被原路径保护拒绝；移除不存在清单项、把假源库放独立 data 子目录后通过，未弱化路径保护。随后固定 run 路径防收据改名复用，补齐取消、真实总期限和实际适配结果持久去重。最初失败输出保留在工具记录，未冒称全部首次通过。

### 最终冻结与未验证项

- 运行编号 `voice-expression-multiposition-20261118-r2`。
- 规范化 manifest SHA-256：`962744d4bf519ffd11fc4267098074b9d4a7dcac0f1e9c97465a5e1e4d882c89`；65 文件集合 SHA-256：`451ae8c1b542d947a5136b81c43f58508a35e73a1b3543711eb167d9e179be13`。
- 新候选 `VOICE_EXPRESSION_LISTENING_CANDIDATE.json`；原候选在 `VOICE_EXPRESSION_LISTENING_CANDIDATE_R1.json`，原指纹748ef71c…与6a9f6014…保留，不授权新字节。
- 完整正文、参数、只读预检、2 HTTP/91 字及停止规则在 VOICE_NEXT_EXECUTION.md；真实模型0。Profile/告知/代理/余额将在另行批准后的只读预检核对，目前未读取。
- 真实 AI 判断、两位置标签实际影响/自然度、未标注后句与恢复克制均未验证。不得把冻结候选当作真实 AI 输出，不发明 reset 标签，不改变日常表达放行范围，不迁移/切换日常服务。


## 两位置表达 R2 已批准执行（2026-11-18，听评待反馈）

运行 `voice-expression-multiposition-20261118-r2`。执行前重算 manifest `962744d4bf519ffd11fc4267098074b9d4a7dcac0f1e9c97465a5e1e4d882c89` 与65文件集合 `451ae8c1b542d947a5136b81c43f58508a35e73a1b3543711eb167d9e179be13`，无漂移。只读核对精确日常库、owner/角色1、有效登录、Profile revision3、Voice摘要、告知及7897代理；既有受保护日常服务健康，无切换。

- 新一次性批准与占用 marker 已保留；普通35字→两位置56字，**2次HTTP／91字**，串行、无重试。真实模型、OAuth、目录、Scribe、TTD WS均0。无需额外元数据请求。
- 普通版成功：103,278字节，3.063秒；两位置版成功：119,997字节，3.140秒。共223,275字节；均通过本地MP3结构检查，独立文件/音频SHA保留。不是听感通过证据。
- 香港2026-11-18日账本由0任务/0请求/0字变为2/2/91；核对时余 **18任务／38请求／3909字**。未提额、未重置、未改Profile/告知或旧音频绑定；实际费用及供应商积分扣减未知，未为查费追加请求。
- 已在Edge打开只读 `http://127.0.0.1:18890/`。两份播放器只读已有文件，暂停、重播和刷新没有合成入口。当前代理未代用户试听，软件播放与真人听感均待实际操作/反馈；不提前判定标签作用范围或AI判断通过。
- 证据在 `%LOCALAPPDATA%/YOSAKI/voice-runs/voice-expression-multiposition-20261118-r2/`：preflight.json、approval.json、approval.json.used、execution-audit.json、result/result.json及两份MP3。一次性私有session摘要文件已按精确路径移除，未撤销原日常登录。只读18890保持供本次试听，日常8000/5173与Memory工作不变。试听结束再关闭只读实例，不新增请求。

**当前状态：两份真实HTTP生成成功，待声学听评；真实AI规划仍仅模拟验证。** 冻结清单原字节及其中执行前状态不改写；已占用收据不能再执行，不复用旧候选预算。第3步未开始。


### 第2步真实AI规划候选已准备（2026-11-18，未执行）

**声学单样本限定通过；真实AI规划待验；日常接入仍在第4步。** 使用已保存消息10（154字日语、两段7句），对话5明确绑定的档案5／ChatGPT套餐gpt-6.1-sol。一次接收全文，允许零标注、稀疏或不同位置不同意图，不预填之前两个标签；输入共2726码点。原Profile人格字段为空，此候选声音设定是用户已述偏好的独立评测快照，不写Profile、不当作已保存字段。

新执行单 [VOICE_EXPRESSION_AI_PLANNING_EXECUTION.md](VOICE_EXPRESSION_AI_PLANNING_EXECUTION.md) 和JSON冻结全部system/user、可信段句ID、白名单、实际请求及67文件。最多1次模型／512输出token／20秒总期限／零重试，TTS、Scribe、WS、OAuth、目录0。尚无真实建议，成功后才展示建议对应原句和最终完整送读预览，不合成。新入口11/11专项及磁盘默认核验通过；本轮实际外呼0、凭据读取0、真实收据0。原两份音频、用量、费用未知及已占用TTS收据不变。

实际入口细节、最终哈希与11项专项适用范围见上述AI规划执行单。原模型适配器、原65文件和音频未改；新增脚本/测试两个文件。本轮不重复无变化前端或整套后端回归。


### AI规划R1执行前检查停止（2026-11-18）

Owner已批准`voice-expression-ai-plan-20261118-r1`的1次／512输出token／20秒规划。Manifest、67文件集合、完整输入、实际请求体及指定来源/模型/Voice绑定全部一致；现有ChatGPT套餐凭据文件可读且所需scope存在，但access token已过期，不满足既有有效期条件。**本次为凭据预检阻塞、模型未派发，不是模型生成或AI判断失败。**

按本批OAuth/目录额度0的边界停止，未刷新、未查目录、未以生成测试凭据。实际模型、HTTP TTS、Scribe、语音WS、OAuth和目录请求均0；真实批准收据未创建、未占用，无模型建议、送读结果或新usage。未自动重试、未改冻结清单、Profile、日常服务及旧音频。缺项只有有效的现有套餐凭据；此前声学单样本限定通过保留，真实AI规划仍待验，日常接入仍在第4步。

本地证据：`%LOCALAPPDATA%/YOSAKI/voice-runs/voice-expression-ai-plan-20261118-r1/preflight-stopped.json`。该目录仅有预检记录，不是已用收据或已派发身份；没有token、凭据全文或原始协议载荷。历史费用未知记录不改写。


### 本次批准后的再次只读预检（2026-11-18 20:37，香港）

Manifest、67文件、完整输入、实际请求体及消息10／档案5／Voice绑定再次一致，现有owner登录有效；但现有ChatGPT套餐access token仍已过期，不满足冻结适配器的有效期条件。按本次OAuth刷新和目录额度均0的授权停止；**本次仍为凭据预检阻塞，模型未派发，不是模型生成失败或AI质量失败**。新增模型、HTTP TTS、Scribe、语音WS、OAuth、目录请求均0，未创建或占用真实收据，未产生建议、送读预览、模型usage或模型耗时。

只追加带时点的脱敏preflight-recheck记录，保留原preflight-stopped和全部历史证据；没有写token、原始载荷或内部推理。原音频、Profile、模型档案、日常服务、Memory及冻结清单不变。唯一缺项是有效的现有同账户凭据；本次不自动续期、重试或换模型。声学单样本限定通过、真实AI规划待验、日常接入留在第4步。


## AI规划R1实际执行完成（2026-11-18，质量待用户审阅）

此前凭据过期的预检停止记录保留。用户随后单独允许最多1次同账户OAuth刷新；按既有流程成功1次，1.656秒，同账户身份核对一致、未查目录。原模型manifest、67文件、完整输入和wire请求体不改写，模型执行阶段继续禁用刷新。账户追加授权独立记在account-request-authorization.json，不把原OAuth0清单追认为允许刷新。

重新预检通过后创建R1专用收据，首次模型请求前原子占用，经冻结入口成功完成1次档案5的gpt-6.1-sol规划。512输出token／20秒总期限／零重试。返回通过明确完成、EOF、展示门控、JSON及可信标注校验；真实AI执行与结构校验成功，**建议是否贴切仍待用户审阅**。没有生成音频，不自动进入第3/4步，不修改日常Profile/模型档案/服务。

| 标注范围 | 模型意图 | 可信标签 | 安全正文插入码点 |
|---|---|---|---:|
| 第1整段，0–80 | thoughtful／若有所思 | `[thoughtful] ` | 0 |
| 第2整段，82–154 | restrained／克制 | `[thoughtful] ` | 82 |

模型选的是段落而非7条逐句建议。两种意图在现有映射中使用同一thoughtful标签；不把它说成两种已验证的不同声学表现，不发明reset。正文154字不改，增加两份13字标签后预览180字、潜在单片段。**这是仅预览，本批TTS0。**

```text
〔历史听评示例原句留本机〕

〔历史听评示例原句留本机〕
```

可靠usage：输入1099、输出104、合计1203 token。独立执行进程总耗时6.326秒，包含本地导入/核对、凭据读取及结果保存；未单独留存供应商内部生成耗时，不能把6.326秒称为纯模型延迟。实际费用未知。模型1次、独立OAuth刷新1次、目录0；HTTP TTS／Scribe／语音WS均0。

证据目录：`%LOCALAPPDATA%/YOSAKI/voice-runs/voice-expression-ai-plan-20261118-r1/`，保留原两份预检失败记录、新preflight-passed.json、独立账户授权/刷新计数、approval.json、已占用marker、result/result.json、result/REVIEW.md、process-timing.json及execution-summary.json。模型输出对应的原段、可信ID、位置、完整文本、180字符及预览指纹`40793703868b63617537bf6ba3399507e0992d78121270f6b89de0919d995789`均可对账。私有session摘要文件已精确删除，未撤销原日常登录。旧两份MP3重新哈希与原记录一致，旧收据保留。

当前：**声学单样本限定通过；首次真实AI规划执行/结构校验成功，建议质量待用户审阅；日常接入仍在第4步。** 本次已占用收据不可重进；不会为增加标签或改变建议再请求。冻结指纹保持：manifest `97ee231101c88c75dc9cab570dd797ae6fd294f129b76e34fd5f8bcc3db3377d`，67文件 `5f876a6b9e5675f0c109ccbd04bf78d0f3917b938dfd3d3375c89245bae989ab`，输入`f2c7e1c01ae3bfd43bc04447411459ba5b4848b60be2adbbe6557652ab23e728`，wire `2ae6010825596992a354658bc96668cdad368b44f3ea8a8c75a733fc56814d81`。


## 当前：第2步限定收口；第3步仅只读范围准备（2026-11-18）

- **规划质量：** 用户确认消息10的本条AI标注“语义安排合理”，记为单样本建议贴切；原35/56字声学单样本限定通过另存。两者不是同一条AI结果的新音频验收，不扩大到跨文本稳定性或日常启用。
- **执行边界：** 关联流程确有1次账户续期，发生在用户后续单独回复“允许”之后，调用allow_refresh=True；原模型阶段自身仍OAuth0、allow_refresh=False，无绕过证据。原OAuth0清单与追加账户授权分账：模型1/OAuth0；账户模型0/OAuth1。前报告阶段归属已澄清，原记录不删除。
- **零外呼补验：** 假凭据/假传输4/4，过期/缺scope/缺凭据时刷新和模型均0；有效凭据只发1次假模型。生产代码、原67文件和已用收据/真实结果/1099+104=1203 token均未改。本轮新增真实调用0。
- **下一范围：** [第3步只读范围](VOICE_STEP3_SCOPE.md)已核对旧2438→1202+1236、2298→1135+1163；旧首段仍unknown、第二段未发。只列现有诊断/部分音频保留/取消和计量/标签感知分段需补项，未开始实现或真实执行。日常接入仍留第4步。

完整授权时序、调用链、分账及限定结论见[边界核对](VOICE_AI_PLANNING_BOUNDARY_AUDIT.md)。费用继续未知，不重新规划、不追加TTS、不重用旧收据。
