# ApprovalError 离线定位、隔离修复与验收

日期：2026-11-24，Asia/Hong_Kong。范围仅为离线修复；真实 Provider／OAuth／目录／模型／规划／TTS／Scribe／WS 均 **0**，无正式迁移或日常加载。原 `voice-ai-multiposition-product-20261124-r1` 已封存，不重进，不复用旧授权，不创建下一批真实候选。

## 1. 根因与证据边界

**已确认的代码缺陷：新请求的字节上限错误继承旧候选。**

| 检查 | 事实 |
| --- | --- |
| 冻结实际聊天请求 | 按实际 HTTPX JSON 序列化为 **1026 UTF-8 字节** |
| 原候选允许值 | `input_byte_limits.chat=835`，`supported_input_byte_budget.chat_serialized_request=835` |
| 留存 attempt 快照 | 与冻结 body 及其语义指纹一致；序列化仍为1026字节，超出191字节；`max_output_tokens=2048` 正确 |
| 冻结拒绝分支 | `scripts/voice_ai_product_listening.py` 原第98行，`BatchGate.validate()`，`len(request.content)>manifest['input_byte_limits'][stage]`；拒绝码 `full_model_input_budget_exceeded` |
| 实际 CLI 离线复现 | 用封存源码、复制数据库、合成凭据／收据和假传输，执行原受保护 CLI，捕获同一分支，`STAGE=chat`；假传输0次、收据未占用 |
| 原假测试为什么未发现 | manifest 手写 `chat=10000`，没有检查新候选继承的835上限；并且旧 fixture 在 startup／登录后 attach，实际入口在 startup 前 attach。后者已补覆盖，未发现它造成阶段丢失 |
| 不是平台审批 | 异常类是项目 `backend/app/voice/realtime_receipt.py` 的 `ApprovalError`，由应用围栏抛出；不是工具或平台执行审查，不存在应用内绕过平台审批 |

证据：私有目录 `C:/Users/ASUS/AppData/Local/YOSAKI/voice-runs/voice-approval-offline-20261124/` 中的 `historical-byte-comparison.json`、`frozen-cli-reproduction.json`、`frozen-input-manifest.json`。比较只输出长度、一致性和哈希，没有输出真实请求全文、凭据或内部推理。

**不能恢复的历史事实：** 旧现场没有保存具体异常文字／分支，也没有独立网络接收证据。上述结论是“留存实际请求与冻结校验之间存在确定冲突，且同一实际 CLI 可确定复现”，不是补造原始异常日志。原 attempt `7632ef5d42acee9749e2862ff0eba148` 的 unknown、无可靠usage及费用未知全部保留；不改写为已发送、未发送、完成或零费用。原 OAuth1次及规划／TTS0等计数保持原记录。

## 2. 授权如何经过实际产品路径

1. 受保护 CLI 在导入实际产品前读取固定 manifest、收据与显式数据库路径；围栏在 startup 前安装。
2. 实际 `/api/chat/stream` 只接受绑定 owner／角色／新空对话／档案5／固定用户输入，禁用不在本批范围内的入口。权限不来自前端按钮。
3. 原序列化器生成完整请求，强制核对冻结请求指纹、2048上限；HTTP、异步 StreamingResponse 与模型传输共享服务端 gate。规划任务从真实完成回执取得 generation／message／正文绑定；不补造许可。
4. `chat_attempt_workers` 是持久 PID／进程创建身份登记，并非另一个模型队列进程。离线验证记录 worker PID、attempt／generation、owner、角色、对话与规划后的message关联；`asyncio.create_task` 继承阶段上下文，结束／取消后恢复。
5. 真正进入内层 transport 前执行完整校验与原子 `O_EXCL` 占用；整批及chat／planning／tts阶段各只占用一次。重启不会恢复后续阶段资格，崩溃不会释放旧收据。

## 3. 隔离修复

补丁仅在私有 `candidate/`，没有覆盖日常源文件：

| 文件 | 修改 |
| --- | --- |
| `scripts/voice_ai_multiposition_candidate.py` | 用最终 body 的真实 HTTPX 序列化字节数生成新候选的两个预算字段，不再继承旧835；不改旧manifest |
| `scripts/voice_ai_product_listening.py` | 启动即核对 body 指纹和字节预算一致性；收据有明确有效期，入口、HTTP及派发前重复核验；身份／上下文／Voice／告知／账户／阶段和原子占用保留；不允许旧四字段收据隐式成为新版批准 |
| 同上 | 有界安全拒绝诊断：固定拒绝码、校验阶段、run／owner／角色／对话／attempt／message／worker关联、单调相对时间、请求长度／上限／哈希；无正文、Cookie、鉴权头、token或任意错误全文；128条滚动上限、丢弃数及独立拒绝终态，原子落盘；写入失败禁止传输 |
| `backend/app/chat_requests.py` | 保留旧`dispatched`和保守计量，明确其只是应用尝试；在现有sources中分别记录授权后内层传输入口、响应头和带响应身份的协议事件，不新增Schema。响应身份只留哈希，不等于完整接收／合成完成／账单确认 |
| `backend/app/chat_api.py` | attempt详情新增可读的`dispatch_evidence`，旧记录缺少传输证据时返回未知，不倒推历史 |
| 两份既有测试及新增离线设施 | 修正合成收据有效期；补精确字节构造、实际 CLI／产品路径、并发、异常退出、取消、诊断界限和浏览器验证 |

没有放宽原冻结校验、改变旧收据、重算旧批授权指纹、返还已尝试额度或恢复旧执行入口。没有依赖升级。`AGENTS.md` 已包含Owner要求的执行／验收分工原则，本轮核对一致，无需重复追加。

## 4. 离线验收

### 实际受保护入口及独立数据库副本

封存快照复制到每个独立用例目录；复制库内清除登录和API凭据，创建合成同账户身份及合成收据。授权 gate 使用真实校验分支（`mock_only=False`），只在最终内层传输处替换为假 HTTP。所有非loopback socket连接及隔离目录外SQLite访问被进程审计钩子拒绝；未读取真实凭据。

| 场景 | 观察结果 |
| --- | --- |
| 最终合法路径 | 实际CLI→实际聊天路由→异步流／worker登记→假聊天2048→真实完成回执→假规划512→假HTTP TTS一次／80字符；40032字节假MP3完整保存；缓存重读无新增调用 |
| 旧835上限 | 新入口在启动产品／保存消息前报`manifest_chat_byte_budget_inconsistent`，假传输0；原冻结入口复现仍为旧`full_model_input_budget_exceeded`，两者不混为一条历史记录 |
| 缺失／非法／过期／无期限／绑定不符收据 | 传输前拒绝，固定安全拒绝码；后续有效期也在原子占用前再查 |
| 错目标／错账户／Voice版本变化／过期合成凭据／请求上限漂移 | 传输前拒绝，HTTP或validation阶段可定位；OAuth与目录0 |
| 并发实际聊天 | 同一绑定目标两次并发得到200／409，最多一次假聊天传输 |
| 跨进程竞争／异常退出／重启 | 两进程只有一个原子占用成功；占用后进程异常退出，再启动仍拒绝，不恢复额度或planning资格 |
| 内层传输抛错／取消 | 已占用状态保留，无二次传输；未知仍为未知，未捏造供应商接收 |
| 诊断失败／上限 | 不可写时假传输0，安全原因留入可写的现有attempt诊断；140条合成拒绝只保留128条、计数丢弃12并独立保留最后拒绝；未知错误字符串不原样落盘 |

证据为 `acceptance/*/result.json`、相应合成收据和授权阶段日志。**这些是合成复现／验证，不冒充原供应商流或历史网络抓包。**

### 回归与实际浏览器

- 既有 Voice 产品／多位置／日常组合：**40/40**。
- 新授权专项9项＋受影响聊天回归37项：**46项通过**。初次组合另有1项测试模块导入错误：分支测试使用顶层`import test_memory_v2_closeout`，执行命令缺少tests目录；修正测试进程的`PYTHONPATH=.../tests`后分支相关 **3/3**，没有修改Memory业务逻辑。
- 合计 **89个不同检查通过**；最终入口预检10项、关联诊断9项为受影响复验，和前述有重叠，不相加夸大总数。保留初次导入错误及中途工具调试日志。
- Edge实际冻结聊天页（现有`App.vue`构建，不是演示页）：错误输入提示拒绝且0假派发；正确输入完成聊天回执、规划及合成；播放器产生`playing/ended`、无音频错误；刷新后再次缓存播放结束，调用仍为1假聊天／1假规划／1假HTTP。只证明软件播放，不是新增真人听感。
- 最后增强入口预检／关联诊断后补相应CLI／专项复验，并再次通过最终版本实际聊天页：错误输入0派发、正确输入完整播放、刷新缓存播完；三阶段attempt关联一致，规划／TTS的message关联一致。最终浏览器记录、源码一致性与清理见私有冻结清单及`browser-acceptance.json`。

本轮未改前端或依赖，不重复构建无变化的前端。正式DB结构、声音设置、音频、缓存及额度未被本补丁修改；日常455文件在最终核对时仍与旧冻结集合一致，既存Memory和其他工作没有覆盖。

## 5. 冻结、是否生效及剩余条件

隔离源码：`C:/Users/ASUS/AppData/Local/YOSAKI/voice-runs/voice-approval-offline-20261124/candidate/`。

交付补丁：同目录上层 `isolated-fix.patch`；完整离线源码、依赖、测试和阶段矩阵：`offline-freeze.json`。**这是离线修复证据，不是新的真实调用manifest或批准收据。** 无Schema／正式迁移／日常部署，日常尚未生效。

完整459文件离线集合：`a685523b5c2a0be71db727b5ca2118e864a5ef9efba41143f3ffd535546ccdf7`；补丁SHA-256：`f8882067110237931d03dd68147dd80bdee899bc163e1ad66e54bc574ec3f648`。另在冻结清单记录455文件运行子集及实际浏览器合成manifest的一致性；上述指纹不替代未来真实授权。

原批次收据、unknown、用量及原始证据不动。浏览器已退出合成登录，隔离库确认该临时会话不存在，独立浏览器及loopback实例已关闭。原日常服务没有被接管／重启。

下一次真实验证需另行审阅新候选：采用本修复后的完整源码指纹、重新由真实构造结果冻结精确请求字节预算和正文／规划输入，检查选定身份／Voice／revision／告知／权限／有效登录及当日余额，并创建带明确有效期的全新一次性批准。凭据续期只有在另获范围内才能进行。仍须先新鲜隔离启动验证，获得真实调用批准后才可派发；本轮不生成候选、不创建真实收据或提前调用。

**仍未完成：整段多位置表达的新真实产品听评。既有声音、标签及AI建议的单样本结论原样保留，不以本次假传输代替。**
