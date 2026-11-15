---
name: gpu-control
description: >-
  Operate ComputeDeck (CLI gpu-control) multi-node GPU plane: cluster/nodes/job
  config, deploy/start/stop jobs, free GPUs, queue/lease, diagnose, gateway,
  models, MCP gc_* and /api. Use when editing ComputeDeck or gpu-control config,
  inventory, MCP, or HTTP API.
---

# ComputeDeck（gpu-control）

多节点 GPU 控制面。产品名 **ComputeDeck**；命令行 / MCP 服务名仍为 `gpu-control`。  
主控跑 UI `:8787` + CLI；worker 只跑 Job。权威契约：`GET /api` · `/api/openapi.json`。

## 接入

1. 先 `gc_api_catalog` 或 `GET /api` 看能力与鉴权。
2. 有 MCP：优先一等 `gc_*`；其余再用 `gc_cli`。
3. `GPU_CONTROL_API_BASE` 指向 UI 时 MCP 走 HTTP；鉴权开则设 `GPU_CONTROL_API_TOKEN`。
4. 401：`gc_auth_status` / `GET /api/auth/status`；本机 `gpu-control auth issue-token`。
5. 多客户端示例：`config/examples/mcp.*.json` / `mcp.codex.toml`；约定见根目录 `AGENTS.md`（Claude Code 另见 `CLAUDE.md`）。
6. 交付：克隆 + `uv`，或控制面容器（GHCR）；不发 npm / PyPI。

## 一等 MCP（20）

| 工具 | 用途 |
|------|------|
| `gc_api_catalog` | HTTP / CLI / MCP 目录 |
| `gc_auth_status` | 鉴权模式与 token 是否就绪 |
| `gc_doctor` | 配置自检 |
| `gc_overview` | 集群总览（`refresh=true` 强制刷新） |
| `gc_get_jobs` / `gc_job_action` | 列 Job；`action∈start\|stop\|restart\|status\|wait` |
| `gc_get_config` / `gc_put_config` | `kind∈cluster\|nodes\|job`（job 须 `name`） |
| `gc_toolkits` | `list\|probe\|install` |
| `gc_models` | `list\|show\|deps\|check\|verify\|paths\|pull` |
| `gc_procs` | `list\|kill` |
| `gc_free_node` | 腾空；`cordon=true` 可顺带隔离 |
| `gc_deploy` | 部署；可 `force` / `restart` / `no_wait`；`gpus` 可省 |
| `gc_queue` | `list\|submit\|schedule\|cancel` |
| `gc_lease` | `list\|acquire\|release` |
| `gc_diagnose` | `check\|repair` |
| `gc_gateway` | `status\|get\|render\|start\|stop` |
| `gc_node` | `render\|cordon\|uncordon\|add\|remove` |
| `gc_exec` | 远程 shell |
| `gc_cli` | 逃生舱：任意子命令 |

## 工作流

**改配置**：`gc_get_config` → `gc_put_config` → `gc_node(action="render")` → `gc_doctor()`  

**部署**：`gc_overview(refresh=true)` → `gc_get_jobs` → `gc_deploy`；异主租约拒绝则 `gc_deploy(..., force=true)`  

**腾空**：`gc_free_node`；只隔离 → `gc_node(action="cordon", name=…)`  

**排队**：`gc_queue(action="submit", job=…)` → `schedule` → `gc_lease(action="list")` / `gc_overview`  

**漂移**：`gc_diagnose(action="check")`

## 改配置要点

- Job JSON 在 `config/local/jobs/`；路径用 `${paths.*}`（`nodes.json` → `defaults.paths`）
- 缺业务 Job：`gpu-control jobs install --recommended` 或 UI「安装推荐包」
- 场景只写 `config/local`；不要写死业务 Job 名，以已安装列表为准

## 约束

- 默认内网；生产开 password / OIDC + API Token
- GPU 数量读配置 / 实采 / `scheduling.unit_gpus`，缺则报错，不静默填默认
- `state/leases.json` · `queue.json` 经 flock（单主控）
- `kill` / `free` / 写配置前先 `gc_overview` / `gc_get_config` 确认目标
- 删除的 API 记 CHANGELOG，不堆冗余 fallback
- 详阅：`docs/ai-接入.md` · `docs/手册.md` · 仓库根 `AGENTS.md`
