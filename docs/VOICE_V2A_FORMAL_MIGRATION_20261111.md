# Voice V2-A 正式库受控迁移记录

日期：2026-11-11（Asia/Hong_Kong）  
状态：**V2-A Schema 已迁移；普通 Development 后端已恢复。Voice 与业务后台任务仍关闭。**

## 授权与执行范围

仅在 `D:\AI xiangmu\自建前端\data\yosaki.sqlite3` 增量新增 `voice_audio_cache`、`voice_message_audio` 两表，显式索引 `voice_audio_cache_expiry`、`voice_message_audio_cache`，以及 SQLite 自动索引 `sqlite_autoindex_voice_audio_cache_1`、`sqlite_autoindex_voice_message_audio_1`。未导入 `backend.app.main` 执行迁移，未创建 Profile、绑定音频、确认告知或调用 ElevenLabs。

## 执行前核对与备份

- Git 工作树开始时有 219 条既存状态；未运行 reset、clean、stash，未清理 Memory 等未提交文件。迁移前 `docs/VOICE_V2A_ENTRY_MANIFEST.json` 的 36 个源码/依赖/测试文件全部匹配当时 SHA-256 `135a68aa4789e14f940efb7b70e9cc8c0d07f0f9abb39c37a54a866910eff2d1`。
- 8000 当时无监听。以 SQLite `mode=ro` 实际连接执行 `PRAGMA database_list`，确认绝对路径为上面的日常库。迁移前有 168 个 Schema 对象、83 张非 SQLite 内部表、43 条记录；完整性 `ok`、外键错误 0；六个 V2 对象均不存在。
- 角色 1 的既有 Voice Profile：`provider=elevenlabs`、`revision=1`、`active=0`；Voice ID 只作内部整行摘要比较，未输出原值；第三方告知 0 条。
- 使用 SQLite `backup()` 从只读源创建一致性备份，再从该备份在另一新目录独立恢复并打开验证。两份副本 SHA-256 均为 `ab6b5e36cf701b19deb30d04363e17a284976355d530cb13a4b69cbc5d0f0bef`，Schema、逐表记录摘要、Profile 摘要、完整性和外键检查均与源库一致。受保护备份路径：`C:\Users\ASUS\AppData\Local\YOSAKI\v2a-voice-entry-4019ece5d4114bc780efccb0275d51ac\formal-v2a-3805728eb5f64796b392f28d22bc4c46\pre_v2a.sqlite3`；独立恢复在同目录的 `independent_restore\yosaki.sqlite3`。目录 ACL 仅当前用户及 SYSTEM 完全控制。两份副本没有覆盖活动库。

## 事务结果

首次尝试在提交前被一条过严断言拦截并**回滚**：它把新建的两张空表名称纳入全表摘要，导致与迁移前摘要不相等。立即只读复核确认日常库仍为 168 个对象、无 V2 表、原文件 SHA-256 不变。随后在独立诊断副本证明六个新增 DDL 均准确、旧表结构和旧表逐行摘要不变；修正核对范围为“迁移前旧表集合”。

修正后，同一个不导入 `main.py` 的一次性执行器先在隔离副本完整提交验收，再对日常库执行 `BEGIN IMMEDIATE → storage.init_db() → 事务内核对 → 再次 init_db() 幂等核对 → COMMIT`。提交前再次比较新鲜库与备份的 Schema、旧数据、Profile 和告知摘要。实际只新增上述 **2 表、2 条显式索引、2 条 SQLite 自动索引**；无旧对象 DDL 修改、旧表记录写入或其他索引、触发器和约束变动。迁移后为 174 个 Schema 对象、85 张非 SQLite 内部表，旧表仍 43 条记录，两张新表均 0 行。旧表数据与 Profile 整行摘要不变；`revision=1`、`active=0`、告知 0 条。完整性 `ok`、外键错误 0。

新表约束由 `backend/app/voice/storage.py` 的当前 DDL 原样创建：`voice_audio_cache.cache_key` 唯一，角色外键级联；`voice_message_audio(message_id,sequence)` 唯一，消息及缓存外键级联。`PRAGMA table_info`、`foreign_key_list` 和 `index_list` 在提交后再次只读核对通过。受保护目录的 `migration_formal_audit.json` 保存无正文/Voice ID 的摘要，执行器文件 SHA-256 为 `33e3ca293df4bb60bf42767c576bc0e2f3736fef4e8861fadf25a9804ed2dd3b`。

## 普通 Development 恢复

项目 `.venv` 缺少 `backend/requirements.txt` 已声明的解析包；只用离线缓存安装锁定的 `markdown-it-py==4.2.0` 与 `mdurl==0.1.2` 到该虚拟环境，未改依赖文件。随后通过 `scripts/start_yosaki_development.ps1` 启动普通 Development，未使用 Voice 验收入口或裸 Uvicorn。

8000 监听进程命令为 `scripts/yosaki_runtime.py development`，启动日志明确显示 `Mode=development`、实际数据库绝对路径为日常库、`Voice Enabled=false`、`Background Tasks Enabled=false`；`GET /health` 为 200。启动后再次只读复核：174 个 Schema 对象、全部表的记录摘要及 Profile 摘要均与迁移提交时一致；新表 0 行、告知 0 条，完整性 `ok`、外键错误 0。现有 5173 前端当时未监听，本轮未擅自启动它。没有通过登录或会触发业务处理的页面路径做验证。

## 隔离正文标签修复与指纹

仅改 `backend/app/voice/readable_text.py`、`tests/test_voice_v2.py` 及本报告/规划文档：已知 `[whispers]`、`[laughs]` 等来源 Audio Tags 整项删除；剩余 ASCII `[`、`]` 移除而保留其中普通文字。预览和实际 Mock HTTP 输入共用规划结果，原 `messages.content` 不变。隔离 `tests.test_voice_v2` **22/22 通过，退出码 0**；断言包括标签删除、普通方括号文字、预览与发送一致和原正文不变。本轮未重跑完整后端回归或前端构建；此前 507/507 为更早冻结基线。

当前 [VOICE_V2A_ENTRY_MANIFEST.json](VOICE_V2A_ENTRY_MANIFEST.json) 包含源码 24、依赖 3、测试 9 个文件，SHA-256 为 `2799486ca16ee290260292ade7da9a096f80a700d2f117dc965a921166151f8f`；源码/依赖/测试汇总 SHA-256 分别为 `ad8ab9e9c19dd269862f0cd9d854e8acd6e3fbaea814a8408a2855b5614c43a9`、`8b3924bbf825a9b9137885b56aa5cb80e485a9f18a18eb5e5cc1a4fd4c43f00b`、`ef1dbc263c3e88d3d067f395fdda90accfbc19ea0063d11e08706654165f58c2`。相较迁移前清单，仅 `readable_text.py` 和 `test_voice_v2.py` 两项内容指纹变化。正式数据库和 `.venv` 属运行数据/环境，不入 Git。

最终 Git 工作树状态为 220 条（原有 219 条加本报告）；本轮涉及的源码/测试只有上述两项，另更新 `docs/VOICE_V2A_ENTRY_MANIFEST.json`、`docs/VOICE_V2_IMPLEMENTATION_PLAN.md`、`docs/VOICE_V2A_FINAL_ACCEPTANCE.md`、`docs/VOICE_SYSTEM_PLAN.md` 及本报告。旧冻结清单中的 Memory 相关文件指纹仍全部匹配，`git diff --check` 通过；没有暂存或提交。服务启动后的再次健康检查仍为 200，8000 仍由普通 Development 进程监听；5173 前端没有监听。

本次不启用 Voice、不激活 Profile、不确认第三方处理告知、不执行真实朗读或 V1-B/V2 Benchmark 请求。V1-B 的实际费用继续保持未知。
