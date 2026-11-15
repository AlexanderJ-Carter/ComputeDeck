# Agent 约定（ComputeDeck / gpu-control）

产品名 **ComputeDeck**；CLI、MCP 服务名、环境变量前缀仍为 `gpu-control`。

适用于 Cursor · Claude Code · Codex · Continue · Windsurf 及其他 MCP / 读仓库约定的 Agent。

## 权威来源

1. 运行时契约：`GET /api` · `GET /api/openapi.json`（由 `lib/api_catalog.py` 生成）
2. 人读接入：`docs/ai-接入.md`
3. 客户端补充：Cursor Skill `.cursor/skills/gpu-control/SKILL.md`；Claude Code 见根目录 `CLAUDE.md`

不要凭记忆编造不存在的 `gc_*` 或 `/api` 路径；先查 catalog。

## 硬约束

- 场景配置只动 `config/local/`；示例在 `config/examples/`
- Job 是插件：按已安装列表与模板选择，不写死业务名
- 改 `nodes.json` 后执行 `gc_node(action="render")`（或 `POST /api/setup/render`）再 `doctor`
- GPU 数量：配置 / 实采 / `scheduling.unit_gpus`；缺配置就报错，不填魔法默认值
- 破坏性操作（`free` / `procs kill` / `put_config` / `exec`）前先 `overview` 或读配置确认目标
- 异主租约：默认拒绝覆盖；需要时用 `gc_deploy(..., force=true)` 或 HTTP `force`
- 调度 / 排障 / 入口 / 节点：优先 `gc_queue` · `gc_lease` · `gc_diagnose` · `gc_gateway` · `gc_node`，不要退回乱拼 `gc_cli`

## 鉴权

- Token：`gpu-control auth issue-token` → `GPU_CONTROL_API_TOKEN`
- MCP 走 UI：设 `GPU_CONTROL_API_BASE`；未设则本机 CLI
- 401 先查 `gc_auth_status` / `GET /api/auth/status`

## 多 Agent / 助手

| 客户端 | 示例 |
|--------|------|
| Cursor | `config/examples/mcp.cursor.json` |
| Claude Code | `config/examples/mcp.claude-code.json`（可作 `.mcp.json`） |
| Codex | `config/examples/mcp.codex.toml`（及 `.json`） |
| Continue | `config/examples/mcp.continue.json` |
| Windsurf | `config/examples/mcp.windsurf.json` |
| 通用 stdio | `config/examples/mcp.generic.json` |

- MCP 入口同一：`bin/gpu-control-mcp`（先 `uv pip install -r requirements-mcp.txt`）
- 风险分级：`GET /api/assistant/policy`（与 MCP `instructions` 同源）
- UI 小助手：`GET /api/assistant/status` · `POST /api/assistant/chat`（`cluster.control.assistant`）

## 交付形态（给 Agent）

- **默认**：克隆仓库 + `uv`；控制面与 `config/` / `web/` / Ansible 同树
- **容器**：`docker compose` / GHCR `ghcr.io/alexanderj-carter/computedeck`（只跑 Control）
- **不发** npm / PyPI（`package = false`，仓库即控制面；发版走 GHCR）
