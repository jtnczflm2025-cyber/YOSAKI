# 第4步首批：短日语、单请求HTTP AI表达接线

> 本页保留首批独立组合层与lab页面的历史证据。本轮实际产品接线、6表设计及最终页面未验状态见[日常接入候选](VOICE_STEP4_DAILY_CANDIDATE.md)，不以本页浏览器结果替代最终候选验收。

2026-11-19。**隔离通过，默认关闭，未日常启用。** 本轮真实模型、TTS、Scribe、WS、OAuth及目录请求均0；没有读取真实凭据／日常库、正式迁移或日常服务切换。旧unknown、费用未知及已用收据保持封存，不重进旧批次，不准备新的真实收费候选。

## 先核对，再复用

第2步已有`annotation_planner`的完整回复输入／可信段句ID／严格标注校验／多位置渲染，`ProfilePlanningAdapter`的既有模型适配链、512输出token／20秒／零重试及展示门控，`LabService`的去重／失败回退，`AnnotationStore`的独立版本、音频占用及重启unknown保护。均直接复用，没有重写规划器或聊天、Memory逻辑。

新缺口是：既有lab来源为直接构造的Source；没有将已保存产品消息及完成／展示结果交接到一次规划后自动合成的流程；旧`VoiceJobs`会按模式重绑音频，现正式`voice_expression_audio`又不支持AI多版本，不能拿它覆盖旧绑定。

因此本批只加隔离组合层`annotation_http_product.py`，独立库继续复用`AnnotationStore`；不修改正式模式枚举、正式缓存键、表或原音频绑定。沿用现有`ElevenLabsAdapter.synthesize_http`、`audio_cache.validate_mp3`和`voiceAudioPlayer`。

## 完整交接契约

1. `SavedReplyRepository`先核对独立库路径，复用`manager.speech_context`检查已保存助手消息、角色、Profile、告知、原朗读过滤及模型／格式；严格要求`generation_status=completed`。
2. 后端注入的`read_completion`提供当前完成结果；客户端不能提交正文或展示许可。核对owner／角色／对话／消息、原正文SHA、generation ID、模型完成、流结束和`display_action=allow`。未满足时零规划／零TTS；不能用“有正文”或仅保存completed代替展示核验。
3. 本轮隔离夹具使用实际`AnswerDisplayGate`处理合成事件后形成上述后端交接结果，不是供应商真实流回放。将generation与交接指纹绑定到既有Source准备／缓存身份中；没有增加新的审核决定解析器。
4. 用户选择`none`或`thoughtful`时不解析／调用规划档案；AI模式必须明确同意并绑定明确选择的档案。隔离档案9只是合成配置，通过既有ProfilePlanningAdapter、原始合成SSE解码／Responses适配器／展示门控；没有读取或改变日常档案5。
5. 一次规划完成后复用可信校验与渲染，再自动进入一次HTTP。完整预览展示安全正文、标签、准确实际字符、参数、计划版本和指纹。原聊天正文不修改。
6. HTTP仍为原`POST /v1/text-to-dialogue`，`eleven_v4`／`ja`／`mp3_44100_128`，settings省略，单请求含标签≤2000码点、30秒／16MiB。未改成WS、未修改日常Adapter期限，也没有把长文`/stream`诊断结论套用为本路径稳定性证据。
7. TTS前再次核对当前来源、Profile与Voice摘要；复用原子预算回调，按实际含标签字符预占。缓存命中不经过该回调。MP3完整性检查通过、来源／版本仍有效才保存；不完整或迟到结果不发布。

## 回退与缓存行为

- 空标注是有效计划，保持留白。结构／意图／可信ID非法，或明确的规划失败，沿用第2步普通无标签回退；记录requested/effective mode及原因。每份回复不先合成AI版再补普通版。
- 模型结果未知、完成缺失／解析未知、取消、撤权或绑定变化沿用原停止规则，不重新规划、不借回退重新送读；TTS一旦失败／unknown，不自动改用普通版再次合成。完整文字不受规划或语音失败影响。
- 持久规划claim与音频claim复用原表，刷新、并发及重启不释放已尝试身份。编辑只增加计划revision与新指纹，不再调用模型；旧版本音频仍可按原身份访问，不覆盖普通旧绑定。
- 取消先将计划和在途音频置为不可发布，再取消任务；晚到的假TTS即使返回也不能发布。代码审阅中补齐了同样的关停顺序，并加专门回归，不能把关闭期间的迟到返回当作可用缓存。

## 最终受影响验证

`tests/test_voice_ai_http_product.py`最终**14/14通过，11.045秒**。前一轮10项及随后13项是开发中版本，最终14项取代其对本批新增代码的验收范围，数量不相加。覆盖：

- 完成／流结束／展示绑定先于规划；伪正文、错模型档案及默认关闭在派发前拒绝。
- 合成原始SSE经实际ProfilePlanningAdapter到可信标注、HTTP请求体、MP3验证、保存、音频读取；真实供应商0。
- 512输出上限、含标签精确字符、一条HTTP、原子日额预占；缓存重播不扣新额度。预算拒绝明确记未派发，TTS为0、账本不增加且不重复尝试，不能混为供应商unknown。
- 手选／关闭优先，规划档案不可用不阻断这两种隔离选择；空标注及非法建议／明确失败的单次普通回退。
- 模型unknown零TTS；TTSunknown不再规划、不换普通版补合成；并发重复／重启复用各最多一次。
- 编辑后新音频指纹、旧表达版本与原普通绑定仍可访问；完整文字不变。
- 撤回告知、Profile漂移、取消及关停后的迟到结果不得发布。
- 非日语或多HTTP片段明确拒绝；没有自动转语言、分段或扩大支持路径。

新增前端类型检查通过；独立Vite构建通过（未重复构建未改的日常入口）。UI复用银灰共享主题和`voiceAudioPlayer`，正文17px／29.75px，无横向溢出。

Edge独立页面已验证：默认普通无标签、明确选择并同意AI、35字普通版与48字表达版分别保存、软件播放至ended、重播／暂停／继续／停止、切换模式和刷新后重新选择原模式复用。隔离持久计数最终仍为**1次假规划、2次假HTTP／83字符、2份完整音频**；重复播放与刷新未增加。不是新的真实阿夜听感。

浏览器当时前后端均为初次10项接线版本。随后补Voice派发前复核、关停取消顺序及预算拒绝分类；前端仅追加预算拒绝终态文案，不改播放或模式逻辑，最终重新类型检查／构建通过。最终14项包含正常HTTP及新增关闭／撤权／预算拒绝分支，未将早期浏览器记录冒充这些最终分支的再次浏览器验收。浏览器扩展有两条来自扩展自身的通用错误消息，页面最终播放／缓存流程无对应失败，不冒称整个浏览器控制环境零错误。

临时浏览器页及18896／18897隔离监听已关闭，未操作日常8000／5173或用户其他页面。原始日志及独立模拟库保留；临时进程关闭前先核对专用命令及父子关系，普通conhost不作为未知进程强行处理。

## 本批文件与边界

- 后端：新增`backend/app/voice/annotation_http_product.py`。
- 隔离入口与夹具：新增`scripts/voice_ai_http_lab.py`。仅接受MockTransport／ASGITransport；即使本地开关打开，也不具备真实Provider派发路径。启动脚本在服务开始前拒绝出站socket连接。
- 页面：新增`frontend/src/voice/AIHTTPBridgeLab.vue`、`aiHTTPBridgeMain.ts`、`frontend/voice-ai-http-lab.html`、`frontend/vite.ai-http-lab.config.ts`。不修改日常App、VoiceSettings或MessageAudioPlayer。
- 新专项：`tests/test_voice_ai_http_product.py`；没有改依赖或正式Schema。

本地可复现入口为`python -m scripts.voice_ai_http_lab --enable --data-dir <仓库外独立目录>`与独立Vite config，默认不开AI模式。命令是开发证据，不要求用户操作终端或参与协议调试。

证据及本地指纹：`%LOCALAPPDATA%/YOSAKI/voice-runs/step4-ai-http-20261119-local/`，包括`bridge-tests-deliverable.log`、`typecheck.log`、`build.log`、浏览器模拟库、`local-source-verification.json`。旧真实Manifest和收据不改写成新代码授权。

## 仍未启用的范围

当前只是第4步短日语单请求HTTP的隔离接线；日常自动朗读／连续通话的开关、真实完成回执交接安装、正式多版本存储与备份策略仍需单独准备后受控接入，不能把独立页当日常已上线。WS／其他语言／跨请求表达与新的声学作用范围均未由本批验证。第2步真实AI建议单样本已确认、56字两位置声学单样本通过有效；真实短段衔接与整篇长文仍未通过，首响应unknown根因及实际费用仍未知。
