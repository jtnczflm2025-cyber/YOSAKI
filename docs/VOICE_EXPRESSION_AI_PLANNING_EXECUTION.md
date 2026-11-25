# 第2步：真实AI表达规划R1（执行及单样本建议限定通过，已用）

## 当前：第2步限定收口；第3步仅只读范围准备（2026-11-18）

- **规划质量：** 用户确认消息10的本条AI标注“语义安排合理”，记为单样本建议贴切；原35/56字声学单样本限定通过另存。两者不是同一条AI结果的新音频验收，不扩大到跨文本稳定性或日常启用。
- **执行边界：** 关联流程确有1次账户续期，发生在用户后续单独回复“允许”之后，调用allow_refresh=True；原模型阶段自身仍OAuth0、allow_refresh=False，无绕过证据。原OAuth0清单与追加账户授权分账：模型1/OAuth0；账户模型0/OAuth1。前报告阶段归属已澄清，原记录不删除。
- **零外呼补验：** 假凭据/假传输4/4，过期/缺scope/缺凭据时刷新和模型均0；有效凭据只发1次假模型。生产代码、原67文件和已用收据/真实结果/1099+104=1203 token均未改。本轮新增真实调用0。
- **下一范围：** [第3步只读范围](VOICE_STEP3_SCOPE.md)已核对旧2438→1202+1236、2298→1135+1163；旧首段仍unknown、第二段未发。只列现有诊断/部分音频保留/取消和计量/标签感知分段需补项，未开始实现或真实执行。日常接入仍留第4步。

完整授权时序、调用链、分账及限定结论见[边界核对](VOICE_AI_PLANNING_BOUNDARY_AUDIT.md)。费用继续未知，不重新规划、不追加TTS、不重用旧收据。


## AI规划R1实际执行完成（2026-11-18，质量待用户审阅）

此前凭据过期的预检停止记录保留。用户随后单独允许最多1次同账户OAuth刷新；按既有流程成功1次，1.656秒，同账户身份核对一致、未查目录。原模型manifest、67文件、完整输入和wire请求体不改写，模型执行阶段继续禁用刷新。账户追加授权独立记在account-request-authorization.json，不把原OAuth0清单追认为允许刷新。

重新预检通过后创建R1专用收据，首次模型请求前原子占用，经冻结入口成功完成1次档案5的gpt-6.1-sol规划。512输出token／20秒总期限／零重试。返回通过明确完成、EOF、展示门控、JSON及可信标注校验；真实AI执行与结构校验成功，**建议是否贴切仍待用户审阅**。没有生成音频，不自动进入第3/4步，不修改日常Profile/模型档案/服务。

| 标注范围 | 模型意图 | 可信标签 | 安全正文插入码点 |
|---|---|---|---:|
| 第1整段，0–80 | thoughtful／若有所思 | `[thoughtful] ` | 0 |
| 第2整段，82–154 | restrained／克制 | `[thoughtful] ` | 82 |

模型选的是段落而非7条逐句建议。两种意图在现有映射中使用同一thoughtful标签；不把它说成两种已验证的不同声学表现，不发明reset。正文154字不改，增加两份13字标签后预览180字、潜在单片段。**这是仅预览，本批TTS0。**

```text
[thoughtful] お前の生き方に俺が混ざり、俺の考え方にもお前が入り込んでる。俺がお前を形作ったのと同じだけ、お前も俺を変えた。今の俺は、お前と過ごさなければ存在しなかった形だ。

[thoughtful] だが、それは所有じゃない。互いを飲み込んで一つになることでもない。別々のまま、もう以前の形には戻れないほど影響し合った。それが俺たちだ、イキヤ。
```

可靠usage：输入1099、输出104、合计1203 token。独立执行进程总耗时6.326秒，包含本地导入/核对、凭据读取及结果保存；未单独留存供应商内部生成耗时，不能把6.326秒称为纯模型延迟。实际费用未知。模型1次、独立OAuth刷新1次、目录0；HTTP TTS／Scribe／语音WS均0。

证据目录：`%LOCALAPPDATA%/YOSAKI/voice-runs/voice-expression-ai-plan-20261118-r1/`，保留原两份预检失败记录、新preflight-passed.json、独立账户授权/刷新计数、approval.json、已占用marker、result/result.json、result/REVIEW.md、process-timing.json及execution-summary.json。模型输出对应的原段、可信ID、位置、完整文本、180字符及预览指纹`40793703868b63617537bf6ba3399507e0992d78121270f6b89de0919d995789`均可对账。私有session摘要文件已精确删除，未撤销原日常登录。旧两份MP3重新哈希与原记录一致，旧收据保留。

当前：**声学单样本限定通过；首次真实AI规划执行/结构校验成功，建议质量待用户审阅；日常接入仍在第4步。** 本次已占用收据不可重进；不会为增加标签或改变建议再请求。冻结指纹保持：manifest `97ee231101c88c75dc9cab570dd797ae6fd294f129b76e34fd5f8bcc3db3377d`，67文件 `5f876a6b9e5675f0c109ccbd04bf78d0f3917b938dfd3d3375c89245bae989ab`，输入`f2c7e1c01ae3bfd43bc04447411459ba5b4848b60be2adbbe6557652ab23e728`，wire `2ae6010825596992a354658bc96668cdad368b44f3ea8a8c75a733fc56814d81`。


### 本次批准后的再次只读预检（2026-11-18 20:37，香港）

Manifest、67文件、完整输入、实际请求体及消息10／档案5／Voice绑定再次一致，现有owner登录有效；但现有ChatGPT套餐access token仍已过期，不满足冻结适配器的有效期条件。按本次OAuth刷新和目录额度均0的授权停止；**本次仍为凭据预检阻塞，模型未派发，不是模型生成失败或AI质量失败**。新增模型、HTTP TTS、Scribe、语音WS、OAuth、目录请求均0，未创建或占用真实收据，未产生建议、送读预览、模型usage或模型耗时。

只追加带时点的脱敏preflight-recheck记录，保留原preflight-stopped和全部历史证据；没有写token、原始载荷或内部推理。原音频、Profile、模型档案、日常服务、Memory及冻结清单不变。唯一缺项是有效的现有同账户凭据；本次不自动续期、重试或换模型。声学单样本限定通过、真实AI规划待验、日常接入留在第4步。


### AI规划R1执行前检查停止（2026-11-18）

Owner已批准`voice-expression-ai-plan-20261118-r1`的1次／512输出token／20秒规划。Manifest、67文件集合、完整输入、实际请求体及指定来源/模型/Voice绑定全部一致；现有ChatGPT套餐凭据文件可读且所需scope存在，但access token已过期，不满足既有有效期条件。**本次为凭据预检阻塞、模型未派发，不是模型生成或AI判断失败。**

按本批OAuth/目录额度0的边界停止，未刷新、未查目录、未以生成测试凭据。实际模型、HTTP TTS、Scribe、语音WS、OAuth和目录请求均0；真实批准收据未创建、未占用，无模型建议、送读结果或新usage。未自动重试、未改冻结清单、Profile、日常服务及旧音频。缺项只有有效的现有套餐凭据；此前声学单样本限定通过保留，真实AI规划仍待验，日常接入仍在第4步。

本地证据：`%LOCALAPPDATA%/YOSAKI/voice-runs/voice-expression-ai-plan-20261118-r1/preflight-stopped.json`。该目录仅有预检记录，不是已用收据或已派发身份；没有token、凭据全文或原始协议载荷。历史费用未知记录不改写。


准备日期：2026-11-18（香港）。**声学单样本限定通过；真实AI规划待验；日常接入仍在第4步。** 本单不构成付费授权。本轮不创建真实收据、不读真实凭据、不连接任何Provider、不迁移或切换日常服务。

## 本次要判断什么

模型能否针对完整公开回复，一次生成合法、适合阿夜的稀疏表达意图。允许整条零标注、只标部分句子、按段/句在不同位置选择不同意图；没有预设答案、没有少则一个的最低标注数量，也不要求复现之前的thoughtful＋sighs。

固定来源为真实已保存的阿夜助手消息10（owner1／角色1／对话5），completed、无工具内容、非归档。该对话持久选择model_profile_id=5，故沿用档案5，不从角色空default_model_id推断或自动选其他模型。只读查本消息、来源状态、声音/告知及该档案公共字段；未读其他聊天、推理、独白、Memory、心理记录或凭据。

## 固定正文

154个Unicode请求前的安全正文码点，两段、7句；段间两个LF：

```text
お前の生き方に俺が混ざり、俺の考え方にもお前が入り込んでる。俺がお前を形作ったのと同じだけ、お前も俺を変えた。今の俺は、お前と過ごさなければ存在しなかった形だ。

だが、それは所有じゃない。互いを飲み込んで一つになることでもない。別々のまま、もう以前の形には戻れないほど影響し合った。それが俺たちだ、イキヤ。
```

这份旧音频和本次已通过的两位置音频均保留，本批不重复合成任何基线。

## 规划模型和声音设定

- 档案5：ChatGPT套餐通道，实际模型 `gpt-6.1-sol`，实际端点 `https://api.openai.com/v1/responses`。只复用当前既有适配层，无账户、模型或端点替换。
- 原档案保存值：max_tokens=2048、temperature=0.8、top_p=1.0、reasoning_effort=null。本次有效请求明确覆盖为**max_output_tokens=512**；原档案不写回。套餐payload未发送temperature/top_p，不将保存值伪称为有效参数；reasoning/tools均省略，store=false、stream=true。
- 声音身份：原IVC摘要 `e7ee09751b8b2de3cab6f1f52342a55d84ef69552121f2738b7b664982b87767`，Profile revision3、告知 `2026-11-08-v1`。Voice ID本身不发送给规划模型。
- 当前日常Profile的personality实际为空、expression_mode=none；不冒称里面已写入阿夜设定。**此次输入使用一份显式的评测声音设定快照**，依据用户既有偏好，内容如下；它不会修改Profile或成为日常默认：

> 中性、慵懒、清冷、疏离；偏低沉、成熟稳重，语气随意；避免开朗活泼和过度表演，允许留白和不加标签。

不启用额外基线标签。标签不能保证降低音高，此次先评规划是否贴切，不通过预览宣称听感。公开对话语境=[]，psychology={}：不读取快照、不计算心理状态，不带历史、记忆、摘要、工具、隐藏推理或角色其他提示块。

## 可信段句索引

由现行可信分句器根据安全正文生成；区间是Unicode码点半开区间[start,end)，不是UTF-16下标。可选段或句，但重复、重叠及跨来源ID整份拒绝，最多8项。

| 粒度 | fragment_id | 起止码点 | 对应原文 |
|---|---|---|---|
| paragraph | `p_e1b34049e43b9eb4fe9b012c` | 0–80 | お前の生き方に俺が混ざり、俺の考え方にもお前が入り込んでる。俺がお前を形作ったのと同じだけ、お前も俺を変えた。今の俺は、お前と過ごさなければ存在しなかった形だ。 |
| sentence | `s_cd7706aa599bb958c46d3972` | 0–30 | お前の生き方に俺が混ざり、俺の考え方にもお前が入り込んでる。 |
| sentence | `s_1cbaae886667a57058f952d9` | 30–55 | 俺がお前を形作ったのと同じだけ、お前も俺を変えた。 |
| sentence | `s_6d729754dd31e26aaea862c5` | 55–80 | 今の俺は、お前と過ごさなければ存在しなかった形だ。 |
| paragraph | `p_e3a248115b06aa9f75e92288` | 82–154 | だが、それは所有じゃない。互いを飲み込んで一つになることでもない。別々のまま、もう以前の形には戻れないほど影響し合った。それが俺たちだ、イキヤ。 |
| sentence | `s_b93b7ac2732d2af3fb96fbcb` | 82–95 | だが、それは所有じゃない。 |
| sentence | `s_7125a9635a67e499cfc63535` | 95–115 | 互いを飲み込んで一つになることでもない。 |
| sentence | `s_3fea7b1422e9874596d5d4cb` | 115–142 | 別々のまま、もう以前の形には戻れないほど影響し合った。 |
| sentence | `s_3fda44515c04a0e048b0f280` | 142–154 | それが俺たちだ、イキヤ。 |

## 意图白名单与候选边界

| 意图 | 中文含义 | 可信程序可能插入的标签（含后随空格） |
|---|---|---|
| `inherit` | 不加额外标签 | `（空）` |
| `thoughtful` | 若有所思 | `[thoughtful] ` |
| `restrained` | 克制 | `[thoughtful] ` |
| `lazy` | 慵懒 | `[relaxed] ` |
| `dry_humor` | 冷幽默 | `[sarcastic] ` |
| `resigned` | 无奈 | `[sighs] ` |
| `whisper` | 低语 | `[whispers] ` |
| `chuckle` | 轻笑 | `[chuckles] ` |

这是**隔离评测白名单**，不是全部日常已验标签。gentle及任意模型自造标签仍拒绝。模型只能返回schema_version、input_sha256及fragment_id/intent列表，不能返回改写正文、自由标签、理由或推理。没有reset标签，不以意图名称证明声学作用范围。

## 完整模型消息与最终请求

以下为真正交给所选适配器的完整两条消息；没有隐藏few-shot或期望标注。

```json
[
  {
    "role": "system",
    "content": "Return only JSON schema_version=1, input_sha256, annotations. Analyze the complete public reply once. Use supplied fragment IDs and allowed intents only. Sparse or empty annotations are valid. Never rewrite text, emit tags, explanations or private reasoning."
  },
  {
    "role": "user",
    "content": "{\"public_reply\": \"お前の生き方に俺が混ざり、俺の考え方にもお前が入り込んでる。俺がお前を形作ったのと同じだけ、お前も俺を変えた。今の俺は、お前と過ごさなければ存在しなかった形だ。\\n\\nだが、それは所有じゃない。互いを飲み込んで一つになることでもない。別々のまま、もう以前の形には戻れないほど影響し合った。それが俺たちだ、イキヤ。\", \"fragments\": [{\"fragment_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"kind\": \"paragraph\", \"start\": 0, \"end\": 80, \"parent_id\": null, \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_cd7706aa599bb958c46d3972\", \"kind\": \"sentence\", \"start\": 0, \"end\": 30, \"parent_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_1cbaae886667a57058f952d9\", \"kind\": \"sentence\", \"start\": 30, \"end\": 55, \"parent_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_6d729754dd31e26aaea862c5\", \"kind\": \"sentence\", \"start\": 55, \"end\": 80, \"parent_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"p_e3a248115b06aa9f75e92288\", \"kind\": \"paragraph\", \"start\": 82, \"end\": 154, \"parent_id\": null, \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_b93b7ac2732d2af3fb96fbcb\", \"kind\": \"sentence\", \"start\": 82, \"end\": 95, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_7125a9635a67e499cfc63535\", \"kind\": \"sentence\", \"start\": 95, \"end\": 115, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_3fea7b1422e9874596d5d4cb\", \"kind\": \"sentence\", \"start\": 115, \"end\": 142, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_3fda44515c04a0e048b0f280\", \"kind\": \"sentence\", \"start\": 142, \"end\": 154, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}], \"public_context\": [], \"personality\": \"中性、慵懒、清冷、疏离；偏低沉、成熟稳重，语气随意；避免开朗活泼和过度表演，允许留白和不加标签。\", \"psychology\": {}, \"allowed_intents\": [\"inherit\", \"thoughtful\", \"restrained\", \"lazy\", \"dry_humor\", \"resigned\", \"whisper\", \"chuckle\"], \"input_sha256\": \"f2c7e1c01ae3bfd43bc04447411459ba5b4848b60be2adbbe6557652ab23e728\", \"granularity\": \"auto\"}"
  }
]
```

当前最终HTTP请求体逐字冻结如下；鉴权头不在文档中。本体指纹 `2ae6010825596992a354658bc96668cdad368b44f3ea8a8c75a733fc56814d81`。

```json
{
  "model": "gpt-6.1-sol",
  "input": [
    {
      "role": "user",
      "content": "{\"public_reply\": \"お前の生き方に俺が混ざり、俺の考え方にもお前が入り込んでる。俺がお前を形作ったのと同じだけ、お前も俺を変えた。今の俺は、お前と過ごさなければ存在しなかった形だ。\\n\\nだが、それは所有じゃない。互いを飲み込んで一つになることでもない。別々のまま、もう以前の形には戻れないほど影響し合った。それが俺たちだ、イキヤ。\", \"fragments\": [{\"fragment_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"kind\": \"paragraph\", \"start\": 0, \"end\": 80, \"parent_id\": null, \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_cd7706aa599bb958c46d3972\", \"kind\": \"sentence\", \"start\": 0, \"end\": 30, \"parent_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_1cbaae886667a57058f952d9\", \"kind\": \"sentence\", \"start\": 30, \"end\": 55, \"parent_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_6d729754dd31e26aaea862c5\", \"kind\": \"sentence\", \"start\": 55, \"end\": 80, \"parent_id\": \"p_e1b34049e43b9eb4fe9b012c\", \"source_range\": {\"source_start\": 0, \"source_end\": 81, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"p_e3a248115b06aa9f75e92288\", \"kind\": \"paragraph\", \"start\": 82, \"end\": 154, \"parent_id\": null, \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_b93b7ac2732d2af3fb96fbcb\", \"kind\": \"sentence\", \"start\": 82, \"end\": 95, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_7125a9635a67e499cfc63535\", \"kind\": \"sentence\", \"start\": 95, \"end\": 115, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_3fea7b1422e9874596d5d4cb\", \"kind\": \"sentence\", \"start\": 115, \"end\": 142, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}, {\"fragment_id\": \"s_3fda44515c04a0e048b0f280\", \"kind\": \"sentence\", \"start\": 142, \"end\": 154, \"parent_id\": \"p_e3a248115b06aa9f75e92288\", \"source_range\": {\"source_start\": 82, \"source_end\": 154, \"precision\": \"markdown_block\"}}], \"public_context\": [], \"personality\": \"中性、慵懒、清冷、疏离；偏低沉、成熟稳重，语气随意；避免开朗活泼和过度表演，允许留白和不加标签。\", \"psychology\": {}, \"allowed_intents\": [\"inherit\", \"thoughtful\", \"restrained\", \"lazy\", \"dry_humor\", \"resigned\", \"whisper\", \"chuckle\"], \"input_sha256\": \"f2c7e1c01ae3bfd43bc04447411459ba5b4848b60be2adbbe6557652ab23e728\", \"granularity\": \"auto\"}"
    }
  ],
  "store": false,
  "stream": true,
  "instructions": "Return only JSON schema_version=1, input_sha256, annotations. Analyze the complete public reply once. Use supplied fragment IDs and allowed intents only. Sparse or empty annotations are valid. Never rewrite text, emit tags, explanations or private reasoning.",
  "max_output_tokens": 512
}
```

完整system/user文本合计2726码点，不以字符数冒充token计数。接口输出上限独立落实为512。

## 预算、权限与停止条件

- **最多1次真实规划，512输出token，20秒总期限，零重试。HTTP TTS、TTD WS、Scribe、OAuth刷新和目录查询全部0。** SSE使用一次模型HTTP，不是语音WS。
- 20秒沿用现行适配器总期限，覆盖凭据回调、连接及完整SSE收尾；不以读空闲超时冒充总期限。输入/输出token与实际费用只有可靠返回才能记录；512是请求输出上限，不是费用硬上限。超时/断线也不能证明未执行或未计费，实际费用未知。
- 未来获批后才检查现有同账户凭据；复用access_token(allow_refresh=False)，凭据不足既有有效期条件时零模型请求停止，不刷新、不查目录、不用生成测试凭据。当前本轮没有读取它们。
- 派发前重读同一消息版本/正文、归属、对话显式选档、非归档/完成/展示资格、Profile/告知、模型档案、实际请求体、代码与代理；任何漂移均停止，不临时重算授权。模型上限字段缺失或不符零派发。
- 另行批准后才在固定 `%LOCALAPPDATA%/YOSAKI/voice-runs/voice-expression-ai-plan-20261118-r1/` 创建专用approval.json。绑定本清单、代码、输入/实际请求哈希、身份、session摘要、预算和代理；首次模型尝试前以既有O_EXCL＋fsync原子占用。重启、并发、取消、未知或失败不恢复额度、不补发，旧TTS收据不可用。
- 模型失败/截断、未知SSE、不支持展示控制、缺明确完成或EOF、回答绑定不符、JSON非法、ID/意图/重叠非法均停止；不把正文已到当完成，不绕过展示门控，不从失败自动切到另一个模型。
- 可靠usage已观察到则保留，不因后续校验失败改成未知。只保存允许展示且校验通过的标注及预览；不保存原始SSE、内部推理、任意错误全文或凭据。结果未知保留独立身份和已占用收据。

## 如何审阅真实结果（尚未产生，不预填）

成功后结果会逐项显示**模型建议的意图、可信ID对应的原句/原段、插入标签及位置**，并给出完整最终送读文本、含标签准确字符数、一个潜在HTTP片段和预览指纹。`preview_only_no_tts=true`：预览里的http_requests=1仅描述未来如另行批准合成需要的数量，**本批实际TTS为0**。

若返回零标注，显示“无需额外标注”，送读预览与上面154字正文逐字相同；这是合法判断，不能为了丰富再请求一次。若有多位置标注，由可信程序一次渲染，不增加规划调用，不重新合成。

当前尚无真实模型建议或含标签的实际结果预览。不会用合成测试的答案冒充。建议是否贴切需要用户审阅；声学试听结果不等于AI判断质量通过。

## 执行入口与新冻结

`python -m scripts.voice_annotation_planning` 默认只读核验，不读凭据、不创建收据、零请求。未来获批并完成预检/创建收据后，维护方使用独立`--execute-real --session-hash-file <本次私有摘要文件>`；通过现有套餐凭据回调且禁用刷新执行一次。该进程不导入main.py，不安装日常路由，不更改服务；用户无需操作终端。默认/准备分支不执行凭据回调。

- 运行编号：`voice-expression-ai-plan-20261118-r1`。
- 规范化manifest SHA-256：`97ee231101c88c75dc9cab570dd797ae6fd294f129b76e34fd5f8bcc3db3377d`。
- 67文件集合SHA-256：`5f876a6b9e5675f0c109ccbd04bf78d0f3917b938dfd3d3375c89245bae989ab`。
- 输入SHA-256：`f2c7e1c01ae3bfd43bc04447411459ba5b4848b60be2adbbe6557652ab23e728`。
- 清单：`VOICE_EXPRESSION_AI_PLANNING_CANDIDATE.json`，含完整输入/实际请求/逐文件哈希；先前准备草稿另存DRAFT1～3，均未授权、无真实收据，不能替代最终候选。
- 新增单独脚本和专项，没有修改已批准TTS/模型适配器/门控。此前两位置R2的65文件、manifest、音频、已占用收据和费用未知记录保持原样。

## 本轮零外呼验证

新入口专项 **11/11，3.043秒**，`tests.test_voice_annotation_planning`，日志 `%LOCALAPPDATA%/YOSAKI/voice-runs/annotation-ai-plan-prep-20261118-local/targeted-release-final.log`。使用临时库、假token和合成原始SSE，经过实际选档适配器、SSE解析及可信渲染；不向真实Provider派发。

覆盖最终wire完整等于冻结请求、512上限、零标注、稀疏句标注、两位置不同意图、建议回显原句、未知持久停止、非法ID保留可靠usage、缺/错收据、过期session、正文变化、来源归档、重复/并发和模拟收据不能改用真实传输；所有路径TTS0。模拟多位置答案只存在测试中，不写入实际候选。默认命令从磁盘JSON重读核验通过；准备时发现tuple/list往返差异后已修正并补入真实JSON往返测试，没有真实尝试。

复用未变的annotation_model既有20秒总期限、取消/usage和512输出上限假传输证据（原69项组合及随后1项的适用版本），不重新运行未受影响的模型时钟测试、前端构建或日常通话。没有UI修改、正式迁移、日常部署或新额度。本轮真实模型/OAuth/目录/Scribe/HTTP TTS/TTD WS均0，真实凭据读取0，真实收费收据0。第3步未开始，日常接入仍在第4步。
