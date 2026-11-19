# Chat Enhancement 专项源码同步

本分支基于既有origin/main 32db65a，提交本轮独立聊天模块、组件、隔离脚本／测试、设置修复和脱敏交接。没有合并整个本机Memory／Voice／UI工作树，也没有合并既有Voice专项分支。

**这是有明确基线依赖的增量源码交付，不是完整日常应用快照，不能干净检出后直接替换日常Runtime。** 日常入口的main.py、App.vue、Memory来源及Voice消费包含大量并行基线，保留在本机工作树；本分支用chat-shared-integration.patch发布其中的聊天精确集成差异，避免把无关代码误入提交。README／AGENTS只直接暂存本批交接和Owner分工差异块。

DELIVERY_FILES.json给出独立文件原工作树SHA-256及规范化LF校验。SHARED_INTEGRATION.json逐文件绑定获批前置基线及补丁结果。补丁以LF比较排除原CRLF格式噪声，在独立目录实际check、应用，再按清单恢复原换行并逐字节验证。使用apply_overlay.py可复验此过程，它只向全新目录写入，不导入应用、不操作数据库或网络。不对origin/main或日常库盲目apply。AGENTS补丁仅包含本轮分工和聊天交接，不包含期间Memory状态同步；daily_sha256与integration_result_sha256因此可能不同。

重建需匹配这些公共文件的已授权本机基线和并行依赖，再在独立目录应用补丁及独立文件，按影响验证。该技术集成由Codex在后续整仓同步任务处理，不要求Owner手工拼接。既有日常加载无需再执行本补丁。

只提交数值用量／判定／测试版本摘要，不提交真实wire、响应正文、评分答案、message／attempt快照、数据库、密钥、OAuth授权记录、附件、备份、私人日志、依赖或生成Vue JavaScript。原敏感证据保留本机，位置说明见CHAT_ENHANCEMENT_HANDOFF.md。

本轮收尾未新增模型、OAuth、embedding、网页或语音供应商请求，未迁移日常数据库或切换Runtime。Git fetch/push仅用于已获授权的既有远程同步。

收尾另检测到main.py、App.vue、yosaki_runtime.py的同期Voice差异；本补丁保留已验聊天快照，不含新增Voice接线。当前完整隔离副本新增5项兼容与构建通过，源哈希留本机parallel-check/evidence/SOURCE.json。原进程一度离线，Owner允许恢复后守卫发现已有进程恢复，本线程未重复启动或迁移；并行AI表及入口不归本聊天提交。
