# Memory V2 M5-A1：隔离年历阅读与投影诊断预览

状态：**隔离工程验收**；正式数据库、正式聊天上下文、向量检索和浮现均未接入。本页只使用合成来源的临时 SQLite 演示。

## 阅读界面

- 保留 L0～L5 快速导航，并增加年／月／周期树。ISO 周同时显示周一至周日的实际日期范围；未知发生时间单列，不以索引时间冒充发生时间。
- 已审核的规则式周期 DIGEST 明示「摘录式摘要／非语义概括」。`digest-display` 在验证当前版本、来源和规则正文完全相符后，将可读原文片段与来源 ID、角色、状态、证据类型分离；来源元数据由用户展开查看。无法验证的规则正文返回 409，不静默重排或修改 M3 派生版本。模型式 DIGEST 仍按原文展示，并保留「已审核周期摘要」标签。
- 失效来源的常态计数在「状态与诊断」展开区；当前请求遇到失效仍显著报错并清空旧详情。NARRATIVE／CLEANED 与 RAW 并排、长文本分页、版本绑定和明暗主题保持可用。

## 投影预览契约

`POST /api/memory-v2/calendar/projection-preview` 是**只读计算**，只处理用户显式选择的 1～50 个条目 ID。请求给出 `characterId`、`entryIds`、`asOf`、`offsetMinutes`、`budgetTokens` 和 `maxBlocks`。它直接调用现有 P2 `context_projection()`；不创建候选检索器，也不改时间换档算法。结果保留 P2 的投影块、原始来源映射、摘要／纪事版本、预算拒绝原因及 Token **估算**，另附当前资格、可用 RAW／CLEANED／NARRATIVE 视图和独立来源经历数。多文本视图只代表同一经历，不自动扩大候选集合。页面明确显示 `projectionOnly=true`、`formalModelReceived=false`。

`GET /api/memory-v2/calendar/nodes/{nodeId}/digest-display` 返回分页周期正文或有序摘录片段及另列来源元数据。后续页需绑定 `digestVersionId`；失效、换版或失权时返回 409。两条接口沿用 owner session、角色验证、M1 来源读时校验及 `Cache-Control: no-store`。普通读取不回填、不审核、不生成摘要、不写冷却，也不调用模型、向量或工具。

预览中 L1～L4 周期 DIGEST 仍要求所选候选覆盖该周期全部成员。未来 P3/P4 要让周期摘要成为可召回候选，须由 M2 明确扩充候选并完成相关性、权限、去重和预算检查；P2 不自行扩源。本预览不是正式聊天请求预览，不能证明模型已看到这些文字。

## 隔离运行与验收

沿用 [M5-A0 演示说明](MEMORY_V2_M5A0.md)中的临时 SQLite 脚本和 `VITE_YOSAKI_CHRONICLE_A0=1` 前端开关。正式 `main.py` 不注册 A1 路由。A1 截图：[暗色年／月／周期树](screenshots/m5a1_week_tree_dark.png)、[亮色阅读](screenshots/m5a1_week_tree_light.png)、[投影预览](screenshots/m5a1_projection_light.png)。截图均为合成数据。

A1 专项文件（含继承的基线测试）**17/17**，完整 `unittest discover -s tests -q` **247/247**，Python 编译检查通过；前端开关开启和关闭的构建均通过。API 测试覆盖六档与 P2 原结果一致、规则式摘要正文与机器标签分离、草稿／失效来源拦截、版本绑定分页、预算／去重／模拟时间、owner／角色与输入校验，以及跨连接 `PRAGMA data_version` 零写入。浏览器实际交互验证年→月→ISO 周展开、周期摘要来源元数据、三条显式经历形成 L0 纪事与 L1 周期摘要两块、明暗模式。规则式周界面按 ISO 周一至周日计算，跨年周按该周星期四归入 ISO 年／月；P2 后端已覆盖 ISO 周、时区与跨午夜边界。正式 SQLite 文件修改时间仍为 `2026-10-26T12:13:36.0827151Z`。

尚未提供正式数据迁移、真实私人聊天、编辑／审核／导入、向量／hybrid、任务维护、重要记忆常驻或正式上下文投递；权限仍是当前 owner session 和角色隔离。正式启用前继续要求独立备份、恢复、迁移与权限验收。
