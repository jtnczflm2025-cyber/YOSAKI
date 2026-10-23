# YOSAKI 聊天档案格式

批量导出会生成 ZIP，每个对话是一个 JSON、Markdown 或 TXT 文件。导入使用多文件选择，先预览，再把每个文件追加为所选角色的新对话。单文件上限 8 MiB，整批上限 64 MiB，单文件最多 20,000 条消息。重复文件按 SHA-256 跳过。

## 完整迁移 JSON v2

JSON 保留角色名称、消息时间、正文、公开推理、内心独白、工具调用与工具运行状态。基础示例：[sample-conversation.json](../tests/fixtures/sample-conversation.json)；双区域和工具历史示例：[sample-rich-conversation.json](../tests/fixtures/sample-rich-conversation.json)。导入的工具运行记录统一标记为不可执行的历史卡片，不恢复待确认状态，也不执行工具。

## 可阅读 Markdown

```markdown
# 聊天档案示例

## 用户 · 2026-10-23T12:00:00+08:00

今晚想整理我的工作台。

## 助手 · 2026-10-23T12:00:05+08:00

我们先把想留下的内容列出来。
```

导入时角色标题须为 `用户` 和 `助手`；时间可省略。自定义显示名称只用于阅读版导出，改名后的 Markdown 不作为可重新导入的规范格式。

## 可阅读 TXT

```text
YOSAKI-CHAT-TXT/1
聊天档案示例

@user · 2026-10-23T12:00:00+08:00
今晚想整理我的工作台。
@end

@assistant · 2026-10-23T12:00:05+08:00
我们先把想留下的内容列出来。
@end
```

TXT 导入使用 `@user`、`@assistant` 和 `@end` 标记，并要求整份文件都符合这些消息边界。无法识别的角色、缺少结束标记或夹杂额外内容会报告错误，不会只导入可识别的前半段。正文行首的 `@end` 与 Markdown 正文行首的 `## ` 会在导出时转义，规范格式导入时还原。需要跨实例完整迁移时使用 JSON；清理后的 Markdown/TXT 是有损阅读副本。
