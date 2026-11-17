## 让 Agent 也能用

插件注册 `computedeck.mcp` provider，启动工作区的：

```text
bin/gpu-control-mcp
```

并注入 `GPU_CONTROL_API_BASE` 与 SecretStorage 里的 `GPU_CONTROL_API_TOKEN`。在支持 MCP 的 Copilot Chat / Claude / Codex 中即可调用 `gc_overview`、`gc_liveness`、`gc_deploy`、`gc_exec_preset` 等工具。

写操作仍按风险分级：只读自动执行，写操作确认，破坏性操作二次确认。

[注册 MCP Server](command:computedeck.installMcp)

## 完整看板在浏览器

趋势曲线、AI 小助手、完整配置、终端矩阵在 Web `:8787`：

[打开 Web 控制面](command:computedeck.openDashboard)

更多说明见文档站「VS Code 插件」章节，或仓库 `docs/vscode插件.md`。
