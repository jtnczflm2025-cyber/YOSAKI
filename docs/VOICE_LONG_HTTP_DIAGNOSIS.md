# 第3步HTTP接收与超时诊断（零供应商API外呼）

2026-11-18。结论保持：**第3步隔离验证通过，真实长文未通过**。旧 `voice-long-step3-20261118-r1` 的首段unknown、已用收据及1任务/1HTTP/1084字原计量不改；另两份历史unknown也不倒推原因。本轮不读真实凭据/日常库，不查询账单、改日额或切换服务。

## 1. 实际接收路径与证据边界

原入口：SegmentedReading.run → 外围asyncio.timeout(30) → ElevenLabsAdapter.synthesize_http → HTTPX AsyncClient.stream → POST `/v1/text-to-dialogue?output_format=mp3_44100_128`。请求eleven_v4/zh、inputs单项、settings省略，不用ElevenLabs SDK。

安装版本httpx0.28.1/httpcore1.0.9/h11 0.16.0已本地核对。AsyncClient.stream调用send(stream=True)，send仅在stream=False时await response.aread；因此原客户端**没有先缓冲整份响应再返回头**。Adapter在进入响应块后aiter_bytes逐块收取，末尾b''.join汇总完整音频；调用方完整性检查/落盘之后才提供完整文件播放。应用层等待完整MP3与HTTPX预缓冲不是同一件事。

官方Create dialogue返回音频文件；另有Stream dialogue的HTTP `/v1/text-to-dialogue/stream`返回音频流。文档没有承诺普通端点一定在全音频生成后才发头，也没有给60秒首响应保证；不能将未见响应头直接认定供应商在缓冲。[Create dialogue](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert)；[Stream dialogue](https://elevenlabs.io/docs/api-reference/text-to-dialogue/stream)。

原waiting_response由send_request_headers.started或receive_response_headers.started设置：包含代理CONNECT的发送/等头、目标请求头发送、可能的请求体发送，以及目标等头。原connecting也混合TCP和TLS。httpcore代理将同一trace传入CONNECT请求；旧日志没有保存hop/method、send body完成或receive_response_headers.complete，所以不能从重复waiting_response确定具体属于哪一跳。

原response_headers事件是在`async with client.stream(...) as response`进入后记录，是**返回调用方时间**。旧R1没有该事件、没有first_audio。它不证明网卡/代理缓冲区里完全没有响应头或音频；底层HTTP解析完成到返回调用方之间的时点没有留存，无法恢复。没有抓包、没有原始头/完整载荷，不会补造。

新增隔离诊断分别记录：proxy_connect/origin、TCP/TLS起止、send_headers_start、send_body_complete、headers_wait、transport_headers_complete及HTTP状态码、response_object_ready、首解码音频块/字节、收尾。底层headers complete是**本机httpcore解析到完整响应头**，不是远端发送时刻或网卡首字节时刻。两者差值包含本机调度、trace诊断落盘等开销；不伪称网络传输耗时。不记录完整request/headers/return_value、任意异常字符串、正文/推理/凭据。依赖版本及三份实际库文件哈希一起冻结，trace不支持时明确失败，不猜测来源。

源码：[HTTPX 0.28.1 client](https://github.com/encode/httpx/blob/0.28.1/httpx/_client.py)，[httpcore 1.0.9 HTTP/1.1](https://github.com/encode/httpcore/blob/1.0.9/httpcore/_async/http11.py)，[代理CONNECT](https://github.com/encode/httpcore/blob/1.0.9/httpcore/_async/http_proxy.py)。安装源码摘录及哈希位于本轮独立证据目录；[官方trace说明](https://www.encode.io/httpcore/extensions/)也提醒事件集合受版本影响。

## 2. 哪个期限实际先触发

原HTTPX Timeout(30)分别给connect/read/write/pool，不是30秒整体；read是每次等网络数据的空闲限制，响应头解析也使用read。外围asyncio.timeout(30)从Adapter调用前开始，另有整Job260秒。[HTTPX超时契约](https://www.python-httpx.org/advanced/timeouts/)。

R1终态为local_TimeoutError、诊断错误类型TimeoutError。若HTTPX自身ReadTimeout/ConnectTimeout先发生，原Adapter会转为ProviderError，结果应provider_unknown，且有相应映射。最后一条等待阶段0.829秒、停止30.235秒，仅29.406秒；整个Job30.281秒还包含预占/核权/落盘。证据确认**单HTTP外围总期限先触发**，不是Job260秒或已观察到的HTTPX读取超时。原期限没有被延长。仍不知道若继续等待供应商是否会成功，也不能据此认定应无限延长。

只读检查本机两份必要代理配置中顶层数字监听/超时项：mixed-port7897匹配原批准路线；未找到显式dial/read/write/request/http/idle timeout。未访问控制器、令牌文件、订阅、路由或代理日志。核心默认值/运行时热更新和供应商内部期限未观察，保持未知；“未配置”不能等同“没有超时”。R1没有代理错误/HTTP响应证据，只能说首先暴露的停止是本地外围期限，不能排除代理内部仍在等待或缓冲。

## 3. 最小隔离修正

- 日常elevenlabs_adapter.py及http_diagnostics.py字节保持不变，日常各入口仍原期限；不重构聊天、Memory或播放器。
- 新增long_http_adapter.py，仅新隔离长文入口可实例化。可验证普通/stream两条固定HTTP路径，候选明确选择官方`/stream`；不支持客户端任意地址或自动换端点。相同原Voice/Profile/模型/格式/语言及省略settings。HTTPX仍stream=True；必要变化是服务端端点和更细的限时/观测，而不是再将客户端“改成流式”。
- 连接建立总预算10秒（含代理CONNECT、TLS，到目标POST头开始），首目标响应头60秒（含连接/发送阶段，从本次dispatch开始），读取每块空闲30秒，单请求硬限90秒，write10/pool5。读取持续有数据也不能突破90。Header与connect计时分开，进入body后不再把60秒header预算用于整份音频；body中逐次wait_for保留空闲限制。
- 隔离执行层92秒只用于给90秒收尾留空间；整任务100秒涵盖派发、接收、验证/保存，从启动任务开始，不含事前只读核对。响应清理有1秒额外局部上限，清理失败不覆写先前的超时类别；仍由92/100秒边界兜底。这些都是**待下批单独批准**的真实期限，已用R1仍保留原30/260。
- SegmentedReading仅新增可选期限与diagnostics_factory，默认仍30秒、原诊断类。顺带修复任务尚未首次运行就取消可能残留generating的分支，写明确cancelled且零派发。新适配器超时仍unknown，不自动重试/补发；后段不发。
- 16MiB、完整帧验证、缓存去重、独占执行标记、原子逐段日账本、已成功片段保留和不完整产物隔离不变。没有让片段一到就发布“完整缓存”。流式HTTP接收不等于已启用实时浏览器播放。

## 4. 必要验证

最终组合38/38，21.322秒：test_voice_long_http_timing 11项、test_voice_long_http_entry 4项、原分段专项及最终入口23项。存在重叠场景，报告单一组合，不与历史数字相加。无前端修改，不重跑无关构建/Memory或全后端。

| 合成场景 | 实际断言 |
|---|---|
| 首响应35秒（0.35秒缩尺）后成功 | 旧外围30秒挡住；新独立策略可以收完，一次请求，无重试；不冒充旧R1回放 |
| 底层先解析头，延迟返回调用方，再延迟音频 | 三个时点严格区分，未记录任意头值 |
| 持续分块，总长超过旧30秒 | 保持每块空闲与整体期限分离，全部按序累计 |
| 收到首块后停滞 | timeout_kind=audio_idle，已有字节保留为不可发布诊断产物 |
| 首响应迟迟不来/持续分块突破硬限 | 分别first_response/request_hard，不自动再发 |
| 连接阶段不足或代理CONNECT头先到 | connect_budget独立；proxy_connect头不作为origin头 |
| 头前取消/音频中取消 | 仅一次尝试、响应/流释放，后续片段不发 |
| 超过16MiB/错误收据/绑定漂移/重复执行/未知结果 | 字节上限或派发前拒绝；未知占用和日账本保留，不恢复额度 |
| 已安装httpcore真实本机HTTP服务 | 无外网；实际头解析时点早于音频块，证明不是只给下游注入手写事件 |
| 段间取消、后段失败、旧音频拒收、缓存复播 | 沿用并重跑受影响原专项，完整文字/成功前段保持可访问 |

缩尺序列用于及时验证计时规则；真实loopback仅证明本机解析/分块行为，不证明远端/实际代理性能。首次测试发现Windows本机初始化超过缩尺连接窗，以及诊断工厂重复构造导致测试句柄泄漏；已修测试为真实loopback使用充足本地预算、让真实执行器注入一次诊断工厂。冻结工具首次对重导出class使用inspect.getsource失败，已改为方法定位实际模块文件哈希；不涉及凭据/真实收据或派发，最终组合已覆盖。

## 5. 尚不能解决的事实

即使下一次小样本成功，也不能证明旧1084字会在更长时间内完成，不能把新endpoint/新文本/新时限多处变化后的结果单独归因。没有供应商/代理内部证据时，首头缺失仍无法分离上游排队、模型生成和代理缓冲；此限制提前说明，不再按每一层重复申请请求。下一候选只验证该HTTP流式接收路径的可观测行为，不是整篇长文复验，不改变第3步真实长文未通过。

证据：`%LOCALAPPDATA%/YOSAKI/voice-runs/long-http-diagnosis-20261118-local/`。含源码基线/旧字节、安装库源码摘录、必要代理配置安全摘要、旧安全结果摘要哈希、初次失败日志与最终组合日志。旧R1结果/diagnostics、used和全部音频绑定未修改，原unknown费用仍未知。
