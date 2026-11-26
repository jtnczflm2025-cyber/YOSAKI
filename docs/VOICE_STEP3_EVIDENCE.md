# 第3步隔离实现与模拟证据（2026-11-18）

## 实际变化

- 新增 segment_plan.py：复用 speech_planner.plan 和原句段切点；安全提取后才在可信句/段偏移插入白名单标签。真实请求文本（含标签）不超过2000；范围+空白gaps可逐字重建全文。插入信息、版本、有效参数纳入预览指纹。未向日常AI Planner增加长文推理或自动标签。无重复补标签、无reset；跨请求声学延续未听评。
- 既有 elevenlabs_adapter.py 仅增可选diagnostics观察器，默认None；原请求JSON、语言、省略settings、代理、无重试、期限/16MiB保持。新增阶段：连接（TCP/TLS开始统一归入连接阶段）、等待响应、派发尝试、响应头、首块/字节、接收完成、HTTP响应上下文退出。最后一项不代表TCP远端关闭或供应商确认接收。
- 新增 http_diagnostics.py：分job/sequence/attempt单调相对时间，白名单事件和错误类型，最多128事件，96后进度压缩给终态留空间。诊断写失败停止；不完整音频有界spool后单列文件/哈希/字节，永不提供完整媒体API。对象关闭释放文件句柄，不宣称物理擦除。
- 新增 segmented_reading.py：复用 audio_cache.claim/publish/validate_mp3 与原适配器；独立结果逐段导出并持久化。后段失败仍保留前段；每日计量通过注入既有原子reserve；并发start加锁+exclusive marker；重复/重启/unknown不重发。撤权/过期不再提供媒体，取消检查拒绝迟到结果。日常 audio_jobs.py 未修改；没有整条日常绑定写入。
- 新增独立假供应商路由/页面和真实候选CLI。lab默认关闭，仅明确启用时运行；真实连接被socket.connect审计门阻止，只有MockTransport。白名单场景/固定夹具，不能接客户端任意文本/地址。既有有序播放器直接复用，未改日常App/Voice设置或播放器实现。

## 验证与范围

| 证据 | 实际结果 | 适用范围 |
|---|---|---|
| tests.test_voice_segmented_reading + tests.test_voice_segmented_entry | 最终23/23 | 新两/三段、真实请求格式的假传输、标签字符/边界、完整映射、首错/unknown、部分音频隔离、后错保留、并发重复、重启、取消/迟到、撤权/过期、逐段日账本、预算拒绝零派发/零额外扣减、日志有界/写失败、最终CLI收据/绑定 |
| HTTP相关组合：上述早版17项＋test_voice_v2、test_voice_long_acceptance、test_voice_annotation_wiring | 87/87，75.565秒 | 默认Adapter回归及原两段/表达HTTP接线；本组合早于最后的隔离UI/本地拒绝分类微调，未冒称最终新专项全量；最终23项覆盖新增差异。包含重叠，不能与23相加 |
| tests/test_segmented_player.cjs | 3/3 | 原产品播放器三段顺序、stop拒绝迟到ended、不污染新播放器身份，假Audio，无外网 |
| vue-tsc --noEmit -p tsconfig.json | 通过 | 当前前端类型，包括新隔离Vue入口；未更依赖 |
| vite build --config vite.segmented-lab.config.ts | 通过，1737 modules | 独立产物dist-segmented-lab，未覆盖日常dist；Git仅新增该产物忽略项 |
| 独立浏览器IAB/Chromium | 通过 | 两段真实解码静音MP3、顺序排空、停止、刷新复播仍假HTTP2、后段失败前段可播、段间取消假HTTP1/第二段未发、不完整无媒体、浅深色/正文预览可用；非真人听感/跨设备验收 |

浏览器首次完整顺序：第一段开始15578.5ms、播放15606.6、结束18158.1；第二段开始18158.4、播放18173.3、结束20738.1（同一浏览器performance时钟，合成静音样本，不是供应商延迟或真人听到时间）。期间点击停止后未继续/恢复，刷新重播后仍假HTTP2。页面切换场景停止旧播放；“取消生成”与“只停播”分开。调试时修正了新隔离页切场景残留状态提示、共享字体变量名称和一个初始诊断写失败文件句柄释放；未修改日常业务逻辑。

诊断/服务器Mock时序是合成，不是旧unknown流回放。浏览器后端为本轮隔离进程，随后微调仅安全分类和“response_closed”命名；最终23专项通过实际当前路由/Adapter/CLI覆盖，未将旧浏览器记录伪称改码后全套重跑。界面最终版补类型和构建，播放器源保持不变。

## 现场保护、历史与后续

本轮未读真实凭据、未调用Provider、未打开日常SQLite；仅临时独立SQLite建表。日常8000后端PID11380、5173前端PID23628/9288保持；未重启、迁移、调整额度或Profile。新lab在18892/18893，收尾关闭，不影响原已有只读试听页。Memory/UI既存修改保留，没有reset/clean/stash或提交。

旧2438/2298首段unknown、第二段均未发保持；具体旧网络原因无新增证据仍unknown。第2步AI建议/usage1099+104=1203、账户续期分账、两份听评MP3及已用收据不变。第3步真实跨段衔接仍未通过；标签跨请求作用、七段Benchmark、日常AI标注接入仍分别待验。

本轮唯一下一真实候选为新2130字两次HTTP，完整文本、参数、77文件指纹、预检/日账本/一次收据/停止规则在 VOICE_STEP3_LISTENING_EXECUTION.md。没有创建真实approval或used，没有外呼，不要求用户此刻试听或操作。
