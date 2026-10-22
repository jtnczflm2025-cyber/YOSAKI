# YOSAKI 受控插件工具包（v1）

## 安全边界

首版插件是 JSON 清单，不是可执行代码包。导入不会运行 JavaScript、Python、shell、entrypoint 或前端扩展；插件只能绑定已发现的 MCP 工具。安装时会从 YOSAKI 已保存的 MCP 工具目录读取真实 schema，清单不能改写参数 schema 或取消确认。

启用顺序：

1. 在 MCP 窗口连接服务并刷新工具。
2. 在 MCP 工具目录启用要用的源工具。
3. 导入插件 JSON 清单。
4. 启用插件，再单独授权其中每个插件工具。
5. 模型提出调用后，仍需在聊天卡片中逐次确认。

插件包和工具默认停用；卸载只移除插件清单与绑定，不删除 MCP 服务、聊天或底层 MCP 工具。插件所声明的 `permissions` 当前用于展示审阅，不表示额外系统权限已实现。

## 清单格式示例

```json
{
  "id": "my.search.pack",
  "name": "我的搜索工具包",
  "version": "1.0.0",
  "description": "将已连接 MCP 服务中的搜索工具整理到一起",
  "author": "本地用户",
  "permissions": ["调用清单中列出的 MCP 工具"],
  "tools": [
    {
      "server_id": 1,
      "tool_name": "search",
      "alias": "网页搜索"
    }
  ]
}
```

`server_id` 和 `tool_name` 必须来自当前 YOSAKI MCP 工具列表；可以先下载界面中的示例清单，再替换引用。清单不接受额外字段（例如 `entrypoint`、`code` 或脚本路径）。单文件最大 256 KB；只接受 JSON。

服务端通过 `/api/plugins/install` 校验、规范化并计算清单 SHA-256。更新插件时 v1 使用卸载后重新导入；同一插件 ID 不会覆盖现有安装。

## 当前限制

- 只导入 JSON 清单，不支持 ZIP 包、URL 市场或自定义可执行插件。
- 插件只组合已启用 MCP 工具，不新增执行器。
- 工具执行和风险策略仍由 MCP 服务自身及 YOSAKI 聊天确认流程控制。
- 当前版本尚未完成用户登录；请勿公开暴露此开发服务。
