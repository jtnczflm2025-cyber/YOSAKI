# R2-C 执行记录 — 2026-11-10

最终状态：R2-C 已完成。日常库增量初始化、Development 端口交接和用户自行输入真实密码后的登录验收均通过；8000 当前使用日常数据库，Voice 与业务后台任务开关为 0。以下保留早期失败及恢复经过。

源码指纹与冻结清单一致。最新基线用户 1、角色 1、模型档案 3、对话 3、消息 5，Voice Profile/告知均 0。一致性备份、独立新目录原样恢复和新鲜快照初始化演练通过；原密钥可解密已有凭据，未替换或输出密钥。

日常库实际新增 38 张表、11 列、48 个索引和一条 mv2_meta(needs_rebuild)；完整 Schema 与冻结 DDL/隔离演练结果一致。所有旧表旧列逐行摘要一致，完整性 ok，外键错误 0，密钥字节保持一致。一次性初始化进程 Voice=0、业务后台任务=0，未运行 lifespan，禁用网络。

备份目录：C:\Users\ASUS\AppData\Local\YOSAKI\r2c-20261110-020637。before.sqlite3 为一致性原库备份；restored/yosaki.sqlite3 为独立恢复验证副本；secret_material 为受控密钥保全；baseline.json 保存私有基线与备份哈希。目录仅授权当前用户及 SYSTEM，不加入 Git。

端口交接时 AttachConsole(10796) 失败，未发送成功的关闭信号。随后检查发现原后端及前端均已退出，原因无法确定；不能将其记录为保持在线。未启动 Development。按授权失败回退步骤恢复了原预览：后端 PID 32264（父进程 27280），前端 PID 27452；8000 health 和 5173 页面均返回 200。

恢复后端明确使用 %TEMP%/yosaki-preview，Voice=0；后台任务开关恢复为原未设置时的等效值 1。该值属于预览原有策略，不是 Development 的关闭状态。真实日常库仍保持初始化后现场，没有备份覆盖或合并。真实密码登录未进行，没有本次 Development 登录会话写入。

当前状态：初始化成功，切换未完成，预览已恢复。需审阅失败现场后再继续端口交接；不得把当前浏览器页面认定为日常库。操作日志和结构核验记录保存在上述受控备份目录。

## 2026-11-10 第二次交接尝试

切换前源码指纹无差异；日常库 Schema 与批准清单一致，所有旧列逐行摘要不变，integrity_check=ok，foreign_key_check 为 0；用户 1、角色 1、模型档案 3、对话 3、消息 5、Voice 两表 0。原预览后端 PID 32264/27280（02:09:03 启动，uvicorn、临时预览目录），前端 PID 27452 代理到 8000。上次原进程退出没有足够日志可确定原因；不再尝试 AttachConsole。

按要求由用户在独立 PowerShell 窗口交接。第一次命令因 PID 32264 当时已不存在且 Stop-Process 使用 ErrorAction Stop，后续命令没有执行。只读 Development 预检通过，8000 当时空闲。第二次仅执行 Development 启动三行命令，Windows PowerShell 5.1 在解析 `scripts/start_yosaki_development.ps1` 时对中文提示文字报 UnexpectedToken；Python 入口未运行，日常库未再次初始化，Development 未启动。

依照失败流程停止尝试并恢复预览：后端进程 8744 监听 8000，前端进程 27452 监听 5173；health 与前端页面均返回 200。读取运行进程指定变量确认数据目录为 %TEMP%/yosaki-preview、Voice=0、业务后台任务=1（预览原有等效策略）。日常库 Schema 仍与初始化后清单一致、完整性 ok、外键错误 0，auth_sessions 仍为 4，没有本次真实登录会话写入。

当前状态：日常库已初始化，但服务仍为预览。服务切换及真实密码登录未完成。继续前需在隔离环境修复并验证 Windows PowerShell 5.1 启动脚本的编码/解析问题，或经审阅后明确改用已验收的 Python Development 入口。不得将预览恢复当作 Development 成功。

## 2026-11-10 启动脚本修复与 Development 切换

Windows PowerShell 5.1.19041.6456 解析器准确定位：原三个后端脚本均在第 6 行第 37 列报 UnexpectedToken；原 Preview 前端脚本另有三个解析错误。四个脚本均为无 BOM 的 UTF-8，PS 5.1 误读中文单引号内文字并破坏字符串边界。仅将四份 start_yosaki*.ps1 转为带 BOM 的 UTF-8，命令逻辑和 Python 保护未改。PS 5.1 重新解析均为 0 错误；四份脚本 SHA-256 已加入 R2C_SCHEMA_AUDIT.json。

隔离项目位于 C:\Users\ASUS\AppData\Local\YOSAKI\r2c-隔离 项目，路径包含中文和空格；已准备独立数据库，隔离副本仅将端口映射为 18700/18701/18702。Windows PowerShell 5.1 实际执行 Development、Preview、Test 完整 .ps1 脚本，均正确传递模式、通过 /health 200，并在 Ctrl+C 后记录 Uvicorn 完整 shutdown。针对性后端专项 4/4 通过，未重跑无变化的完整回归。

由用户在独立 PowerShell 窗口现场核对 8000 监听进程，停止预览后端并用修复后的 Development 专用入口启动。前端 5173 保持运行。当前 Development 服务进程 PID 17324，命令为 yosaki_runtime.py development；运行进程环境实际 YOSAKI_DATA_DIR=D:\AI xiangmu\自建前端\data、YOSAKI_VOICE_ENABLED=0、YOSAKI_BACKGROUND_TASKS_ENABLED=0、YOSAKI_RUN_MODE=development。8000 health 200；未认证的 auth/status 返回 setup_required=false、authenticated=false。日常库只读检查：实际绝对路径匹配、Schema 与冻结清单一致、旧字段逐行摘要无变化、integrity_check=ok、foreign_key_check=0；用户 1、角色 1、模型档案 3、对话 3、消息 5、Voice 两表 0，mv2_meta 仍 needs_rebuild，登录前 auth_sessions=4。全部已验收文件指纹无漂移。

真实密码登录由用户自行操作，结果待记录。本节未执行任何一次性初始化、恢复覆盖或 ElevenLabs 调用。

## 真实登录验收完成

用户在现有 5173 页面自行输入原账户密码并报告登录成功；助手未接收、读取、修改或输出密码和 Cookie。登录后的只读数据库检查显示 auth_sessions 从 4 增至 5，auth_login_attempts 为 0。与切换前基线比较，旧表中仅 auth_sessions 摘要改变；其他既有表全部旧列摘要一致。Schema 仍与已批准清单一致，integrity_check=ok、foreign_key_check 错误 0。用户 1、角色 1、模型档案 3、对话 3、消息 5、Voice Profile 和告知均 0。8000 仍由 Development 专用入口进程 PID 17324 监听，health 200；前端 5173 仍由 PID 27452 监听。

结论：R2-C 日常库初始化、端口交接、Development 启动和真实密码登录均已完成。`YOSAKI_VOICE_ENABLED=0`、`YOSAKI_BACKGROUND_TASKS_ENABLED=0` 已从运行进程环境核实。V1-B 和 V2 未开始。
