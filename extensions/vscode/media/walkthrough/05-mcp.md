## Copilot、小助手与控制面窗口

1. **工作台**：侧栏点「打开工作台」，看 KPI、助手/MCP 状态、公告摘要与常用操作。
2. **控制面页面**：看板 / 节点 / 任务 / 入口 / 模型 / 终端 / 配置 / 小助手 / 公告——默认在 **编辑器窗口**（或 Simple Browser）打开，不引导外部浏览器。需要时才用「在系统浏览器打开看板」。
3. **MCP Server**：注册后，VS Code / Copilot 可调用 `gc_overview`、`gc_deploy`、`gc_exec_preset` 等工具。

MCP 启动：

```text
bin/gpu-control-mcp
```

并注入 `GPU_CONTROL_API_BASE` 与本机/SecretStorage 中的 Token。在 control 仓库打开时 Token 会自动读取。

[打开工作台](command:computedeck.openWorkbench)

[打开看板](command:computedeck.openDashboard)

[打开小助手](command:computedeck.openAssistant)

[打开监管公告](command:computedeck.openNotices)

[注册 MCP Server](command:computedeck.installMcp)
