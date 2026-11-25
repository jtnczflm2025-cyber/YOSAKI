# 两段HTTP /stream 入口接线证据（2026-11-18，零真实外呼）

状态：**短样本流式接收成功，真实跨段长文待验**。本轮只接线、模拟和候选冻结；不读真实凭据/日常库，不创建真实收费收据、不调整日额、不切换日常服务。

## 最小变更

- 新入口 scripts/voice_long_stream_two_part.py：原Speech Planner/segment_plan＋SegmentedReading，注入已通过201字样本的LongHTTPAdapter及LongHTTPDiagnostics，端点固定 `/v1/text-to-dialogue/stream`；逐段权限/缓存claim/auto_budget.reserve、取消、幂等、部分成功保留及MP3检查原样复用。
- 没有修改backend/app/voice中的任何文件，日常/原隔离30秒默认及真实通过的接收器字节保持不变。本批只新增两段受控脚本、新夹具及专项；旧只读review_app增加本地受限结果目录参数与无完整音频拒绝，不添加任何合成路由。页面播放器HTML/顺序逻辑沿用原实现。
- 新正文为工坊整理木桌的全新测试文章，安全全文2090码点，实际1036+1052=2088字；两个LF段间gaps单列。自然完整段落切分，两请求均低于2000，无标签，不重发旧unknown或原第二段。Manifest的实际requests只有新两段，包含201字旧夹具作为纯离线构建依赖不产生请求。
- 新入口在任务启动前设置220秒外围硬限。原内部260不改变，但不能越过220；即使任务吞掉取消，外围expired检查也写明two_part_job_hard_deadline，不误称用户取消。单请求仍10/60/30/90秒、92秒清理兜底、write10/pool5、16MiB；首段验证保存核权后才发次段。

## 受影响专项：8/8，4.728秒

测试文件 tests/test_voice_long_stream_two_part.py。假VoiceAuthority、假Key、httpx.MockTransport；全部数据在每个TemporaryDirectory中，模拟批准/used只在假namespace，不是可用于真实日常的授权。没有读取真实Key/登录，也不经真实代理外连。

| 用例 | 实际断言 |
|---|---|
| 两段正常 | 两个实际假HTTP均为/stream；正文、语言、模型、settings省略与预览逐字一致；1任务/2请求/2088原子计量；每段头/接收/保存诊断及完整MP3 |
| 首段unknown | 仅1尝试/1036字，次段not_executed，不自动重试；无完整音频拒绝试听页 |
| 后段失败 | 次段503记unknown，首段完整音频和全文预览仍可访问，第二段无完整媒体 |
| 跨段取消 | 第一段保存后在可控间隙取消，仅1请求，次段未派发；已完成前段保留 |
| 缓存读取/重播 | 实际只读HTTP路径两次加载页面、两段及再次读取字节一致；无生成路由，假TTS仍2，预算不增 |
| 并发/重复 | 并发两个execute仅一个批次完成，合计2次TTS；重复执行拒绝，不恢复尝试 |
| 绑定漂移 | 预算/Manifest变化派发前拒绝，0请求，无used |
| 整任务期限 | 仅缩尺220秒外围定时器，保留实际执行器；过期明确unknown/job_hard_deadline，次段不发 |

已通过且未改的接收器38/38、HTTP协议/超时、MP3校验、缓存保护与播放器证据直接引用 VOICE_LONG_HTTP_DIAGNOSIS.md 和 VOICE_STEP3_EVIDENCE.md，不再重跑全量，不与8项相加。201字真实接收2.969/3.016/8.485秒及690512字节是历史单样本证据，不改写为本次两段真实成功。

## 实际只读页面检查（仅假音频）

IAB/Chromium在本机18896临时只读服务器读取两份已知可解码静音MP3。实际观察“正在播放第1段”→“已播完”；重播后停止显示“已停止”；刷新后重新播放并排空到“已播完”。点击了暂停/继续，但页面没有独立暂停状态文字，因此不将其写成新增暂停交互已改进或真人听见已停。实际页面媒体读取与TestClient预算断言配合，证明缓存播放路径不派发TTS。

该浏览器证据是软件播放/本地假时序，不是真实供应商声学听评。临时目录在本轮隔离证据根/browser，和未来真实运行目录分开；页面无Provider客户端或收费路径。收尾关闭该临时服务/页，日常8000/5173未操作。截图browser-proof.png只记录假音频排空状态。没有更改前端业务源码或依赖，不重跑不受影响的前端构建。

## 保留与候选

201字原MP3哈希与execution-summary一致，其用量1/1/201、首块3.016及阶段记录、已用收据均保留。用户认可的只是HTTP流式接收单样本，未追加试听/复测。旧长文unknown、原收据和记录不动。

候选及全文/逐段请求/指纹：VOICE_LONG_STREAM_TWO_PART_CANDIDATE.json、VOICE_LONG_STREAM_TWO_PART_EXECUTION.md。58文件包含54基线和4份本轮实际依赖；没有更新旧54/77候选作为新执行依据。未来获批后重核真正当日余额，不使用历史剩余额度代替实时数据；目前真实运行目录/批准记录不存在。

本轮原始证据：%LOCALAPPDATA%/YOSAKI/voice-runs/long-two-stream-wiring-20261118-local/ 的before字节/hash、wiring-tests.log、browser/、browser-proof.png及final-integrity.json。真实外呼0，保留“短样本流式接收成功，真实跨段长文待验”。
