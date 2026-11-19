# 聊天增强最终交接入口

2026-11-19（香港）：功能、真实合成测试、日常加载及设置修复已完成。主文档：[当前交付](CHAT_ENHANCEMENT_CANDIDATE.md)；待办：[后续清单](CHAT_ENHANCEMENT_FOLLOWUP.md)；设置：[修复约定](MODEL_REASONING_SETTINGS_FIX.md)。

## 分工

Codex连续完成排查、修复、测试、实际浏览器验证、截图和已授权加载，不让Owner重复代跑技术验收，不重复索取已有批准。Owner只参与必要的凭据输入／系统人工交互、主观内容决定及超出已有授权的重大决策。请求参与时一次说明原因与最少操作。技术通过不替代Memory内容确认／发布；此原则已写入AGENTS.md。

## 日常入口与运行模式

- 聊天 http://127.0.0.1:5173/chat；工作台 /hub；Chronicle /chronicle；后端健康 http://127.0.0.1:8000/health。
- 沿既有Development＋daily Voice＋TTD WS专用Runtime及原数据路径运行；不裸导入main借初始化迁移。本轮收尾没有重启或切换Runtime。
- 模型切换：聊天顶部当前模型名称（下拉箭头）→“当前模型”→“聊天模型”；“管理模型配置”进入永久默认设置。输入区“请求／分支／上下文”进入统计、下一次模型设置和请求详情；各消息“消息操作／详情”进入该条操作与实际attempt。
- 网页默认关，套餐视觉保持未开放，自动摘要默认关；Memory／Voice原权限、审核和预算保护保留。日常补查的核价日期和逐条同意另按Memory计划，不由聊天收尾延长。

## 本机证据与备份

本机外部证据不是Git附件，不复制数据库、凭据、私人快照／附件或原始日志：

| 位置（本机） | 用途 |
|---|---|
| D:/AI xiangmu/YOSAKI-CHAT-P5-20261119/evidence | P5全量冻结、91专项、真实批准与real-20261119结果、43专项 |
| D:/AI xiangmu/YOSAKI-CHAT-LOAD-PREP-20261119/evidence | 49文件清单／补丁、增量安装、加密恢复与实际加载结果 |
| D:/AI xiangmu/YOSAKI-MODEL-SETTINGS-FIX-20261119/evidence | 永久模型回显修复的复现、测试与加载 |
| D:/AI xiangmu/YOSAKI-NEXT-SETTINGS-20261119/evidence | 单次刷新保留、独立合成主机、最终哈希与加载 |
| D:/AI xiangmu/YOSAKI-CHAT-CLOSEOUT-20261119/evidence | 本轮55文件核对、文档／补丁验证、Git范围及推送记录 |
| %LOCALAPPDATA%/YOSAKI-CHAT-DAILY-LOAD-20261119-f2d6fc4a | ACL／EFS保护的一致性快照、加密.ybackup、独立恢复、source-before原字节及两轮设置修复备份 |

## 兼容回退

新分支数据产生后不让旧线性读取器接管，不整库覆盖。暂停新派发并保留当前schema、账目、来源和分支可见性；必要时用scripts/chat_readonly_archive.py对最新受保护一致性快照导出只读视图。设置修复回退先清除对应标签页暂存，再核对已加载哈希逐文件处理；期间有并行修改则隔离合并。备份不能覆盖新增数据、unknown或预留。详见[加载与回退](CHAT_ENHANCEMENT_LOAD_CHECKLIST.md)。

## Git同步边界

本批使用codex/chat-enhancement-closeout-20261119专项分支，远端仍为既有origin。当前远端缺少完整日常Memory／Voice／UI基线，所以分支同步独立聊天源码、必要文档和公共文件的精确集成补丁；不将无关本机工作偷带入提交，不宣称干净检出就是完整日常Runtime。源／补丁和基线哈希见[同步说明](chat-enhancement/SOURCE_SYNC.md)。日常main工作树保留全部并行修改；后续整仓基线集成由Codex在对应授权任务处理，不要求Owner手工拼接。

收尾只新增文档、文件一致性与Git打包检查，不重跑已有充分证据的行为；无新增模型、OAuth、embedding、网页或语音供应商请求，无新迁移或Runtime切换。

## 收尾现场与并行更新

收尾起点55文件与最后聊天／设置加载记录完全匹配。随后main.py、App.vue、yosaki_runtime.py出现同期Voice接线，已保留；本批只在独立副本补5项（设置3项、真实路由Fake聊天1项、AI默认关闭不建表1项）及类型检查／构建，全部通过，真实供应商请求0。Git聊天补丁保持已验聊天快照，不混入新增Voice代码。

原19928／20908退出、8000／5173一度离线，原因未确认。Owner另行允许原模式恢复、AI关闭、零迁移／外呼；启动前守卫发现服务已恢复，本线程未重复启动或迁移。最终health和/chat均200；后端5248、前端9152仅作本次观测值。Development＋daily Voice＋TTD WS进程另带并行--ai-expression参数；AI用户设置没有启用记录，用户开关关闭，本批不替并行Voice授权作决定。当前158表中的6张AI表属于并行安装，不归本聊天收尾迁移。未来重新核对现场，不复用PID，也不把本线程0迁移写成全机未变化。
