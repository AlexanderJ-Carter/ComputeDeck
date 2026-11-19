# Claude Code — ComputeDeck

本仓库是多节点算力控制面 **ComputeDeck**（CLI / MCP：`gpu-control`）。

## 先读

1. 仓库约定：[AGENTS.md](AGENTS.md)（硬约束与工具优先级）
2. 人读接入：[docs/ai-接入.md](docs/ai-接入.md)
3. 运行时契约：`GET /api` · `GET /api/openapi.json`（UI `:8787`）

不要凭记忆编造 `gc_*` 或 HTTP 路径；先 `gc_api_catalog` 或打开 catalog。

## MCP

```bash
uv pip install -r requirements-mcp.txt
chmod +x bin/gpu-control-mcp
```

把 [`.mcp.json.example`](.mcp.json.example) 或 [config/examples/mcp.claude-code.json](config/examples/mcp.claude-code.json) 复制/合并为项目 `.mcp.json`；`command` 换成主控上 `bin/gpu-control-mcp` 的绝对路径。鉴权开则设 `GPU_CONTROL_API_TOKEN`（`gpu-control auth issue-token`）。

## 行为摘要

- 场景只改 `config/local/`；Job 按已安装列表，不写死业务名
- 改 `nodes.json` 后：`gc_node(action="render")` → `gc_doctor()`
- 优先 `gc_queue` · `gc_lease` · `gc_diagnose` · `gc_gateway` · `gc_node`；少用 `gc_cli`
- `free` / `kill` / `exec` / 写配置前先 `gc_overview` 或读配置确认目标
