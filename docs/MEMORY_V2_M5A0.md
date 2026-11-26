# Memory V2 M5-A0：隔离只读年历原型

状态：**隔离工程与合成页面验收**；未注册到正式 `main.py`，未迁移正式数据库，未接正式聊天上下文。

后续 M5-A1 已在同一隔离页面增加只读计算 `POST /projection-preview` 与 DIGEST 阅读元数据分离；本页记录 A0 基线，当前接口增量见 [M5-A1](MEMORY_V2_M5A1.md)。

## 范围和入口

- `frontend/src/main.ts` 只在路径 `/chronicle` 且构建变量 `VITE_YOSAKI_CHRONICLE_A0=1` 时加载 `ChronicleApp.vue`。关闭开关后加载原有应用；年历页的关闭操作返回 `/hub`。
- `backend/app/memory_v2_calendar_api.py` 提供可安装的 GET 接口。隔离测试应用还安装既有 `memory_v2_time_api.py`。两者均不由正式 `main.py` 注册。
- 所有年历请求检查 owner session；每次读取重新核对角色、M1 原始来源、P1-A 条目及 M3 派生版本。服务端只返回有权限且有效的正文；客户端不以草稿状态决定发布资格。`Cache-Control: no-store`，GET 不创建任务或更新来源。
- 时间树按 P2 的 L0～L5 动态年龄分级，L1～L4 显示已审核周期 DIGEST 或明确的缺失／失效状态。单经历梗概是另一种元数据，不称为周期摘要。未知发生时间保留“时间未核实”。旧记录始终允许主动 RAW 回查。

## 运行合成演示

在项目根目录开两个 PowerShell 终端：

```powershell
.\.venv\Scripts\python.exe scripts\chronicle_a0_demo.py --port 8765
```

```powershell
$env:VITE_YOSAKI_CHRONICLE_A0 = '1'
$env:YOSAKI_API_PROXY = 'http://127.0.0.1:8765'
npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:5173/chronicle`，使用脚本启动时显示的**合成演示账户**登录。示例脚本先创建系统临时目录并设置 `YOSAKI_DATA_DIR`，再导入后端；退出后临时 SQLite 被删除。只在 `127.0.0.1` 监听。示例含六个年龄档位、已审 DIGEST、缺失／失败／未审核摘要、未审核 CLEANED、已失效条目、未知发生时间及多页 RAW。所有文字均为合成内容。这个独立测试应用不提供正式模型、工具、编辑、导入或备份接口。

## 只读接口契约

| 接口 | 用途与保护 |
| --- | --- |
| `GET /api/memory-v2/chronicle/tree` | 时间根节点分页；游标绑定角色、时间视角、时区、筛选及索引代。 |
| `GET /api/memory-v2/chronicle/nodes/{nodeId}` | 子节点、条目成员及有效周期摘要分页；后续页绑定 DIGEST 版本。 |
| `GET /api/memory-v2/chronicle/entries/{entryId}/raw/{ordinal}` | 指定有序原始来源的 RAW 分页；后续页绑定正文指纹。 |
| `GET /api/memory-v2/calendar/characters`、`/status` | 可访问角色与有效／被安全过滤的条目计数。 |
| `GET /api/memory-v2/calendar/entries` | 标题、关键词、标签、梗概的基础关键词及日期范围查询；最多 50 条每页，游标绑定时间视角、筛选、索引代及当前来源快照。 |
| `GET /api/memory-v2/calendar/entries/{entryId}` | P1-A 元数据及 RAW／NARRATIVE／CLEANED 可用状态。 |
| `GET /api/memory-v2/calendar/entries/{entryId}/views/{viewType}` | 只返回当前已激活且通过 M3 校验的 NARRATIVE／CLEANED；最多 4096 字每页，后续页绑定版本。 |

角色、来源、审核或版本变化时拒绝旧内容；分页绑定变化返回 409，页面提示刷新并清空不能安全继续拼接的内容。未审核草稿不经正式读取返回正文。

## 页面可用操作

时间树逐层进入与返回、根和成员分页、周期摘要翻页、条目详情、NARRATIVE／CLEANED／RAW 切换、与 RAW 并排、RAW 多来源及长文分页、前后条、关键词／起止日期筛选、角色选择、明暗主题和刷新。来源失效计数、摘要缺失／待审核／下级缺失、任务异常和请求错误均有文字提示。所有操作调用上述隔离 API；没有写入按钮。

合成界面截图：[周期 DIGEST](screenshots/m5a0_period_digest.jpg)、[纪事与 RAW 并排](screenshots/m5a0_raw_narrative.jpg)。截图不代表正式数据或真实模型质量。

## 验收、边界和后续

隔离 API 专项 6/6，覆盖 owner session、角色和伪造来源、六档、草稿拒绝、来源修改与失权、游标版本变化、GET 无数据库写入。`unittest discover -s tests -q` 完整后端回归 **230/230**；前端开关开启与关闭的 `npm run build` 均通过。浏览器验收覆盖 L5→L1 展开、周期 DIGEST、关键词筛选、条目导航、RAW 多页和明暗模式；390×844 视口无横向溢出。另在临时 SQLite 修改一条合成来源后，请求下一页得到 409，页面清空已载标题、纪事及 RAW 并提示刷新；随后重新生成干净演示数据。正式 SQLite 文件修改时间保持 `2026-10-26T12:13:36.0827151Z`。

本原型尚未提供逐句高亮、标签／状态组合筛选、完整的任务列表、编辑审核、导入预览、话题拆合、权重／向量、设置、备份恢复、正式浮现、心潮念或经验桥视图。权限仍是当前 YOSAKI 单 owner session 加角色来源隔离；将来面向阿夜的独立用途权限和可撤销授权须另行设计和验收。真实长年历性能、旧备份恢复及正式接入也未验收。

P2 当前只有某周期全部成员已被 M2 选中才使用周期 DIGEST。未来 P3/P4 可让 M2 明确选取周期摘要候选，经相关性、权限、去重和 Token 预算检查后交给 P2 投影；P2 不能自行扩大候选范围。该待办不改变本轮的只读页面或正式召回链。
