# 阿夜克制表达：单因素对照候选

> 2026-11-24：Owner已明确暂停本28字候选。准备材料、原Manifest和完整55文件源码保留，真实请求／批准收据0；本单不再作为待执行批次。当前主线及唯一新提案见 [多位置AI表达执行单](VOICE_AI_MULTIPOSITION_PRODUCT_EXECUTION.md)。

日期：2026-11-23（Asia/Hong_Kong）。**仅本地准备和模拟通过，真实调用0，未创建真实批准收据；日常设置不变。**

上一批 `voice-ai-product-listen-20261122-r1` 正式收口，不重进、不重新聊天、不重新规划或合成普通版。完整保留用户原话：「嗯，除了语气有些过于活泼，其他都符合预期」，及原产品缓存／音频版本／usage／已占用收据。技术链路通过；本条音色、自然度等限定认可，语气目标仍有差距。费用继续未知。

## 核对事实与假设

| 已核实事实 | 可调整位置／不能据此推断的事 |
| --- | --- |
| 消息191的公开正文、规划输入、缓存对应同一句15字日语：`まだ眠れないなら、少し話そう。` | 保留这句正文和标点；本次不改写为更冷淡或更低沉的文字 |
| 实际规划输入 `personality=""`、`public_context=[]`、`psychology={}`；来源 `baseline_enabled=false`、`baseline_applicable=false`。Voice配置中的personality也为空，revision3 | 明确的声音表演目标没有进入这次规划输入。这是可调整的输入位置，不证明它导致了活泼，也不证明补写目标就会降低音高 |
| 规划返回合法空标注，无回退；最终实际送读15字、没有标签 | 不能说AI主动选择了“活泼”标签，不能把一次音高／语气差异归因于标签 |
| 既定原Voice、`eleven_v4`、`mp3_44100_128`、`language_code=ja`；HTTP `/v1/text-to-dialogue`，settings省略。持久化预览、冻结适配器与本批假传输核对一致 | 声音目标文字不自动成为供应商参数。本候选不增加pitch、speed、stability或其他未核实参数，不换Voice或模型 |
| 历史 `voice-tone-delivery-20261117-r1`：正文「言葉を選びたい。今は静かに話そう。」17字，普通版17／克制版30字；克制规则渲染 `[thoughtful] `。报告记载62318／66498字节成功缓存，用户确认克制版符合预期 | 同机制有单样本听感依据，可作为优先候选；那是另一条正文，不保证对当前短句有效。`restrained` 是本地意图名，实际标签仍是thoughtful |
| 更早慵懒组用户更偏爱无标签；relaxed虽相对慵懒但音调较高、偏活泼；每版一次 | 不默认使用relaxed，不把重复试听当独立样本，也不推定relaxed在所有文本无效 |

资料：当前产品事实见[上一批结果](VOICE_STEP4_PRODUCT_LISTENING_RESULT_20261123.md)；克制旧证据见[原执行记录](VOICE_NEXT_EXECUTION.md)中 `voice-tone-delivery-20261117-r1`、主Plan的原听评及该私有运行目录 `report.json`。本次只读投影 `source-comparison.json` 保留实际输入的上述安全字段，没有读取模型／语音凭据或无关历史。

## 可审阅的声音表演目标配置

版本 `aya-performance-candidate-1`，状态 `review_candidate_not_applied`，完整配置在候选JSON的 `performance_goal_candidate`：

> 慵懒、低沉、克制，亲近但不过分雀跃，允许自然停顿和留白。

- 允许 `annotations=[]`；不要求每句带标签，不强迫轻笑、低语或叹气。
- 普通无标签和明确手选thoughtful均保留；不修改聊天正文、整体人格或日常Voice Profile。
- 慵懒／低沉是待检验的表演目标；当前不新增音高参数，也不承诺thoughtful会降低音高。
- 亲近、自然停顿与留白不能靠改写当前正文或添加未经验证的重置标签实现。本句原有逗号／句号保留。
- 未来若批准把此目标传给AI规划器，应把版本／内容纳入输入指纹，产生新计划版本；旧零标注计划和音频继续可访问，不能覆写原计划或强制追加标签。**本次短TTS候选不调用AI，不应用这份新配置，避免同时改变两个因素。**

## 唯一对照差异和完整送读

| 版本 | 完整实际送读文本 | 字符／新增请求 |
| --- | --- | --- |
| 原缓存：AI零标注，历史参照 | `まだ眠れないなら、少し話そう。` | 15字；新增0 |
| 克制候选 | `[thoughtful] まだ眠れないなら、少し話そう。` | **28字（13字前缀含空格＋15字正文）；新增1次HTTP** |

只改变“在开头插入一个已有受控thoughtful前缀”。Voice、revision3、正文、标点、语言、模型、MP3格式和settings省略保持不变。不重新聊天、规划或生成原版，不把本地目标配置送给TTS。

原参照：对话22／消息191／generation `a2ea8cdea4ea3598ffc7e7ecd7e4fba4`；原音频SHA-256 `8be6faafc160645014cba37b6737ec55a58f15e0e9477de5d73b434e3dff9529`。导出文件与产品缓存BLOB相同，原文件不覆盖。

## 待单独批准的固定执行范围

- 新运行编号：`voice-tone-restrained-20261123-r1`。此编号无真实收据，未执行。
- [冻结Manifest](VOICE_RESTRAINED_COMPARISON_CANDIDATE.json)规范化SHA-256：`0ed2a94365a06091acdf0f528d70d69f92fdf0ee68ed6a6c5daf2ca354eb62a7`。
- 完整55文件集合SHA-256：`4e308799255d6a9b71178e9eb0fef82d655808346997153778b394de7394b414`。逐文件和实际依赖版本见Manifest。新增代码未接入日常路由或启动入口。
- **最多1任务／1次HTTP／28个实际送读字符／30秒总期限／16MiB**；连接／读取等客户端超时沿现有HTTP Adapter，外层总期限不因持续接收延长。
- 聊天、规划、OAuth、目录GET、Scribe、TTD WS、重试、重定向均0。费用未知；调用量上限不是实际账单硬上限。
- 固定 `POST https://api.elevenlabs.io/v1/text-to-dialogue?output_format=mp3_44100_128`，请求体 `model_id=eleven_v4`、`language_code=ja`、`inputs=[{text:上述28字, voice_id:原Voice}]`，settings省略。原Voice摘要 `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767`，Profile revision3，告知 `2026-11-08-v1`。
- HTTP direct、`trust_env=False`、proxy=None；不修改系统代理、不切换端点。凭据只在批准后实际派发路径需要时读取，不调用账户接口测通。
- 执行前只读复核最终代码／依赖／Manifest／原缓存哈希、有效Owner登录、Voice／告知及执行时香港日期日余额，至少余1任务／1HTTP／28字；不足即停止，不提额、不刷新或改变设置。
- 采用已有 `VoiceAuthority`、日额原子预占、`expected_approval` 和持久独占 `.used` 收据。收据绑定代码、Manifest、数据库、当前登录、Voice和预算；默认派发关闭。旧批次收据、mock收据不能使用，重复、并发、崩溃和unknown不恢复额度。
- 任意条件漂移、拒绝、失败、超时、unknown、取消、权限撤销或保存验证失败即结束，不自动回退普通版或再次合成。已尝试用量保留；原音频不受影响。
- 候选仅在独立评测目录保存完整MP3，不写V2绑定／annotation_audio、不覆盖原缓存、不迁移／切换日常服务。完整校验及再次核权后才发布候选。

## 本轮实现与验证

新增 `scripts/voice_restrained_comparison.py`、`tests/test_voice_restrained_comparison.py` 和只读 `frontend/voice-restrained-listen.html`，没有修改现有Planner／Profile／设置接口／Memory／日常启动或数据库。构建器只产出固定候选；复用既有安全正文与表达渲染，不为本次新增标签映射。

**16/16受影响专项通过**：实际假HTTP只收到一次精确28字请求及既定参数、外层30秒／客户端30秒、原子计量；默认关闭、坏Manifest／收据、额度不足、撤权、mock/real隔离、unknown不重试不返还、302不跟随、取消、重复／并发／重启、音频不完整／超字节、参照损坏、只读页待生成与缓存重播。初次测试fixture漏关SQLite连接导致临时文件清理失败，已用 `closing` 修复，随后16项通过；日常库未被测试连接。

实际Edge只读页面：合成音频经一次假Adapter派发，两个播放器完成播放、暂停／继续、刷新重播；总假HTTP仍1、真实Provider0。页面明确标“本地合成数据验证”，不是新的真人或声学证据；候选未生成时不显示可播放的候选控件，页面没有生成路由。沿共享银灰主题和17px／1.75正文，视觉截图检查通过。仅favicon404，无播放器错误。独立端口18904和浏览器已关闭。

证据目录 `C:/Users/ASUS/AppData/Local/YOSAKI/voice-runs/voice-tone-restrained-20261123-prep/`：`source-comparison.json`、`targeted-tests.json`、`browser-fake-dispatch.json`、`browser-playback.txt`及 `.playwright-cli` 截图／记录。首次假服务网络围栏误阻Windows事件循环的本机自连接，已限定允许loopback、继续禁止Provider外连后通过；不涉及真实请求。未重跑无关全量／前端构建（新增独立静态页面，不改应用TypeScript）。

## 听评方式和判断标准

批准并成功后，Codex打开同一只读对照页，或直接提供这两份已保存音频；Owner只听一次并反馈，不操作终端／日志。普通版标“原缓存／历史参照”，候选标“thoughtful克制候选”；非盲听，不称同批严格A/B。暂停、重播、刷新均只读，二者不同时播放。

重点：①语气是否减少雀跃、更克制成熟，仍亲近；②音色与自然度是否保留；③停顿是否自然；④感知音高有无改善或反而升高（只按主观观察记录，不编造数值）。如果候选更合适，只对本句单样本认可；若没有改善或有副作用，保留原缓存及零标注选择，不自动追加调参或生成。单次生成差异仍混有随机变化，不能证明thoughtful的稳定因果效果。日常设置的应用留待明确决定。
