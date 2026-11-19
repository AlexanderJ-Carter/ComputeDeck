## Copilot、小助手与 Web 看板

插件侧栏顶部有 **AI 与 MCP** 分区：

1. **Web 小助手**：打开控制面并自动展开助手面板（`#/assistant`）。对话、确认卡、模型配置都在浏览器里完成。
2. **监管公告**：未读告警/提示；也可打开 `#/notices`。
3. **MCP Server**：注册后，VS Code / Copilot 可调用 `gc_overview`、`gc_deploy`、`gc_exec_preset` 等工具。

MCP 启动：

```text
bin/gpu-control-mcp
```

并注入 `GPU_CONTROL_API_BASE` 与本机/SecretStorage 中的 Token。在 control 仓库打开时 Token 会自动读取。

[打开 Web 小助手](command:computedeck.openAssistant)

[打开监管公告](command:computedeck.openNotices)

[注册 MCP Server](command:computedeck.installMcp)

[打开 Web 控制面](command:computedeck.openDashboard)
