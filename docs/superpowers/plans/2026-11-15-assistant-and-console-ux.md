# 实现计划：智能助手与控制台体验

> 依据：`docs/superpowers/specs/2026-11-15-assistant-and-console-ux-design.md`

## 文件地图

| 文件 | 职责 |
|------|------|
| `lib/assistant_policy.py` | 工具风险分级与教学摘要 |
| `config/examples/mcp.*.json` | Cursor / Claude Code / Codex / 通用 |
| `docs/ai-接入.md` | 多 Agent 接入说明 |
| `lib/api_catalog.py` | 挂上示例路径与 policy 摘要 |
| `web/server.py` | `/api/exec/suggest`；后续 `/api/assistant/chat` |
| `web/static/app.js` + `style.css` | 远程命令补全；后续助手面板 |
| `mcp_server/server.py` | instructions 与 policy 对齐 |
| `tests/test_assistant_policy.py` | 风险表与 catalog 一致 |

## P0 任务

1. 落地 `assistant_policy` + 多 Agent MCP 示例 + 文档  
2. 单测：每个 `gc_*` 都有 risk；catalog 引用示例文件存在  

## P1 任务

1. `GET /api/exec/suggest?q=`  
2. `#exec-custom` 下拉补全（键盘可达）  
3. （可选）`Ctrl+K` 命令面板骨架  

## P2 任务

1. `POST /api/assistant/chat` + OpenAI 兼容上游配置  
2. UI 浮动对话 + 确认流  

每阶段独立可测、可提交。
