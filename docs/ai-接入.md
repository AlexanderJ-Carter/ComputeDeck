# AI / API / MCP 接入

**ComputeDeck**（命令行 / MCP：`gpu-control`）给 Cursor、Claude Code、Codex、Continue、Windsurf 或其他 Agent 调配置、启停 Job、腾空节点、排队占卡时，按本页接入。

权威机器契约：`GET /api` · `GET /api/openapi.json` · 浏览器 `/api/docs`。  
仓库约定：[AGENTS.md](https://github.com/AlexanderJ-Carter/ComputeDeck/blob/main/AGENTS.md)；Claude Code：[CLAUDE.md](https://github.com/AlexanderJ-Carter/ComputeDeck/blob/main/CLAUDE.md)；Cursor Skill：[gpu-control/SKILL.md](https://github.com/AlexanderJ-Carter/ComputeDeck/blob/main/.cursor/skills/gpu-control/SKILL.md)。

## 鉴权

| 模式 | 说明 |
|------|------|
| `none` | 关闭鉴权；配合 SSH 隧道使用，生产环境改用 password / oidc |
| `password` | Session Cookie（浏览器）+ Bearer Token（脚本 / MCP） |
| `oidc` | OpenID Connect 授权码 + PKCE；API 使用 Bearer |

```bash
gpu-control auth set-password --generate --enable
gpu-control auth issue-token          # 写入 config/local/secrets/ui_api_token
gpu-control auth status
```

- 浏览器：`POST /api/auth/login` → HttpOnly Cookie `gc_session`
- 脚本 / MCP：`Authorization: Bearer <token>`
- WebSocket 终端：使用 Session Cookie 或 `Authorization` 头；出于安全考虑不接受 URL 里的 `?access_token=`
- 公开发现路径（无需登录）：`/api` · `/api/openapi.json` · `/api/docs` · `/api/health` · `/api/auth/*` · `/api/assistant/policy`

## 控制面里的接入向导

配置页有两块直接服务于 AI / MCP 接入：

- **配置 → 就绪检查**：配置页左侧分为「配置 / 检查与接入 / 参考」三组，一次只显示一页；「就绪检查」汇总主控与鉴权、SSH 与节点、Job 与模型、入口与 LiteLLM、AI 与 MCP 状态，可跳回对应分页，或点「重新检测」重新读取。
- **配置 → AI / MCP**：
  - API 接入：Base URL、鉴权模式、Token 是否已配置；「测试 API」同时检查 `/api/health` 与 `/api`。
  - MCP 接入：检查 `bin/gpu-control-mcp` 与 `mcp` 依赖；选择客户端后「复制 MCP 配置」会生成 / 轮换 Token 并复制可粘贴的 JSON / TOML。
  - AI 助手：显示是否就绪、当前模型，直接打开设置。

「让 AI 指导」会把当前步骤状态发给助手；助手读取 setup / overview / doctor 后给出下一步操作，只读操作直接执行，写操作先确认。

---

## 1. 三条接入路径

| 路径 | 入口 | 适用 |
|------|------|------|
| **HTTP** | `http://<control>:8787/api` | 脚本、OpenAPI、MCP 走 HTTP |
| **CLI** | `gpu-control …` | 主控本机、Ansible |
| **MCP** | `bin/gpu-control-mcp`（stdio） | 任意支持 MCP 的 Agent |

```bash
# 机器可读目录（含鉴权说明）
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api | jq '.auth,.docs'

# OpenAPI 3（含 bearerAuth / cookieAuth）
curl -s http://127.0.0.1:8787/api/openapi.json | jq '.info,.components.securitySchemes'

# 浏览器文档页（无需外网 CDN）
open http://127.0.0.1:8787/api/docs
```

### 工具怎么选

1. 有一等 `gc_*` → 用它（参数结构化，少拼 CLI）。
2. 调度 / 排障 / 入口 / 节点：`gc_queue` · `gc_lease` · `gc_diagnose` · `gc_gateway` · `gc_node`。
3. 其余冷门子命令才用 `gc_cli`；完整路径表或 body → `gc_api_catalog` / `/api/openapi.json`。

### 交付形态

| 方式 | 说明 |
|------|------|
| 源码 + `uv` | 默认；控制面与配置、静态资源、Ansible 同树 |
| 容器 / GHCR | 只跑 Control：`docker compose` 或 `ghcr.io/alexanderj-carter/computedeck` |
| PyPI / npm | **不做**（`package = false`；前端依赖已 vendor，不发 npm 包） |

---

## 2. 常用 HTTP 接口

改配置（调参主入口）：

- `GET/POST /api/config/cluster` — body `{"data":{…}}`
- `GET/POST /api/config/nodes` — 写完会触发 inventory render
- `GET/POST /api/config/job/{name}`

运维：

- `GET /api/doctor` · `GET /api/overview?refresh=1` · `GET /api/overview?light=1`
- `GET /api/jobs` · `POST /api/jobs/install-template`（`template` 或 `recommended=true`）
- `POST /api/jobs/{name}/action` — `{"action":"start|stop|…","node":"…"}`
- `POST /api/deploy` — 可带 `force` / `restart` / `no_wait` / `note`
- `POST /api/free` — `{"node":"…","cordon":false,"no_kill":false,"wait":20}`
- `POST /api/procs` / `POST /api/procs/kill`
- `POST /api/node/cordon|uncordon|add|remove`
- `POST /api/models/check|verify|deps|pull`
- `POST /api/exec`
- `POST /api/gateway/start|stop` · `GET /api/gateway/status`
- `GET /api/diagnose` · `POST /api/diagnose/repair`

调度（MCP：`gc_queue` / `gc_lease`；HTTP 同路径）：

- `GET /api/leases` · `POST /api/leases` · `POST /api/leases/{id}/release` · `POST /api/leases/release`（按 nodes）
- `GET /api/queue` · `POST /api/queue/submit` · `POST /api/queue/schedule` · `POST /api/queue/{id}/cancel`

存活与远程接入：

- `GET /api/liveness` · `POST /api/liveness/check` — 节点存活与快照新鲜度；`summary.offline_nodes` / `stale_nodes` / `gpus_excluded`
- `GET /api/remote` · `POST /api/remote/action` — Tailscale / Cloudflare 状态、计划与启停（详见 [远程接入](远程接入.md)）

助手：

- `GET /api/assistant/policy` · `GET /api/assistant/status` · `POST /api/assistant/chat`（`stream=true` 走 SSE：`meta` / `delta` / `tool` / `done` / `error`）
- `POST /api/assistant/execute` — 确认并执行助手动作；结果回喂模型
- `POST /api/assistant/config` · `GET|POST /api/assistant/models` — UI 设置上游与拉取模型列表
- `POST /api/assistant/analyze` — 只读解读 overview + notices + diagnose
- `GET /api/notices` · `POST /api/notices/mark` — 监管公告（规则告警；可 `?sync=1`）
- `GET /api/metrics/history` — 看板趋势采样（API/GPU/CPU/在线率/队列/健康分）
- `GET /api/logs?name=ui|watchdog|ingress-edge|ingress-litellm|cloudflared|runlog&lines=200` — 日志尾（需鉴权）
- `GET /api/exec/suggest` — 远程命令补全

**助手确认协议**：对话返回 `conversation_id` 与 `pending_actions[]`（确认卡）。确认后调用：

```json
POST /api/assistant/execute
{
  "conversation_id": "conv_…",
  "action_id": "act_…",
  "confirm": true          // destructive 操作改为 "EXECUTE"（二次确认）
}
```

服务端会重新校验节点 / Job 是否仍存在、目标是否离线、参数哈希是否被篡改，然后回调已有 `/api/*` 执行；执行结果以 `role:"tool"` 写回会话，再让模型总结并给出下一步。`stage:"reject"` 可取消。坏参数、未知目标、离线目标都会被拒绝并返回结构化错误，不会产生任何写操作。

**趋势工具**：助手可调用 `gc_metrics_history`（只读）读取最近约 1 小时的控制面趋势，包括 API 连通率、GPU 算力/显存、槽位占用、CPU 均载、节点在线率、队列等待与健康评分；看板「趋势」面板的「让 AI 解读」会直接发起这条分析。

**UI 交互**：确认卡内联在对话流中。普通写操作点一次「允许」即执行；破坏性操作需要在同一张卡上再点一次（Esc 可取消这次确认）。执行时卡片下方出现运行块，节点结果与命令输出就地展开，随后由模型给出总结。只读工具不弹确认，直接在回答上方以步骤形式显示。

完整列表以 `GET /api` 或 `/api/docs` 为准。

**改配置后建议顺序**：写 config → `POST /api/setup/render`（或 CLI `node render`）→ `GET /api/doctor`。

---

## 3. MCP（任意 Agent）

同一 stdio 入口：`bin/gpu-control-mcp`。适用于 **Cursor / Claude Code / Codex / Continue / Windsurf** 以及其他支持 MCP 的客户端，不绑死某一 IDE。

主控安装：

```bash
cd /path/to/ComputeDeck   # 或本地目录名 gpu-control
uv pip install -r requirements-mcp.txt
chmod +x bin/gpu-control-mcp
```

| 客户端 | 示例配置 | 放入何处（常见） |
|--------|----------|------------------|
| Cursor | `config/examples/mcp.cursor.json` | `~/.cursor/mcp.json` 或项目 `.cursor/mcp.json` |
| Claude Code | `config/examples/mcp.claude-code.json` | 项目 `.mcp.json` 或用户 MCP 设置 |
| Codex | `config/examples/mcp.codex.toml` | `~/.codex/config.toml`（JSON 变体见 `mcp.codex.json`） |
| Continue | `config/examples/mcp.continue.json` | `~/.continue/config.json` 的 `mcpServers` |
| Windsurf | `config/examples/mcp.windsurf.json` | Windsurf MCP 设置（字段多与 Cursor 相近） |
| 通用 | `config/examples/mcp.generic.json` | 任意 stdio MCP 宿主 |

公共字段：

| 环境变量 | 作用 |
|----------|------|
| `GPU_CONTROL_API_BASE` | 设置后工具优先打 HTTP（UI 需在跑） |
| `GPU_CONTROL_API_TOKEN` | Bearer；鉴权启用时必填，否则 401 |
| （均未设） | 工具直接调本地 CLI，不依赖 UI |

Cursor 片段示例：

```json
{
  "mcpServers": {
    "gpu-control": {
      "command": "/ABS/PATH/TO/ComputeDeck/bin/gpu-control-mcp",
      "env": {
        "GPU_CONTROL_API_BASE": "http://127.0.0.1:8787",
        "GPU_CONTROL_API_TOKEN": "<gpu-control auth issue-token 的内容>"
      }
    }
  }
}
```

容器里跑控制面时：MCP 进程仍建议在**宿主机**指向仓库/挂载树里的 `bin/gpu-control-mcp`，并用 `GPU_CONTROL_API_BASE=http://127.0.0.1:8787` 打到已映射的 UI；容器内调试用 `docker compose exec` 跑 CLI。

教学与风险：MCP `instructions` 与 `GET /api/assistant/policy` 同源（`lib/assistant_policy.py`）。  
UI 小助手：设置面板填 Base URL / 模型 / Key（`POST /api/assistant/config`，可 `POST /api/assistant/models` 下拉）；`POST /api/assistant/chat` 调 OpenAI 兼容上游；未配则引导去设置。

一等工具（23）：`gc_api_catalog` · `gc_auth_status` · `gc_doctor` · `gc_overview` · `gc_get_jobs` · `gc_job_action` · `gc_get_config` · `gc_put_config` · `gc_models` · `gc_toolkits` · `gc_procs` · `gc_free_node` · `gc_deploy` · `gc_queue` · `gc_lease` · `gc_diagnose` · `gc_gateway` · `gc_node` · `gc_liveness` · `gc_remote` · `gc_exec_preset` · `gc_exec` · `gc_cli`。

说明：

- `gc_deploy` 的 `gpus` 可省略（读 `cluster.scheduling.unit_gpus`）；异主租约用 `force=true`。
- `gc_exec_preset(node, preset)` 运行只读诊断（`nvidia-smi` / `gpu-util` / `gpu-procs` / `ports` 等），自动执行。
- `gc_exec` 按命令分级：`nvidia-smi / df / ps / ls / cat / grep / ss / docker ps / systemctl status` 等只读命令自动执行；`mkdir / sed -i / cp` 等修改类命令一次确认；`rm / kill / reboot / python -c / 重定向 / 管道` 等危险命令二次确认。
- `gc_models(action="pull", name="org/model")` 可拉权重；可选 `register`。
- `gc_node(action="render")` 替代昔日 `gc_cli(["node","render"])`。

---

## 4. 端到端工作流（Agent）

### 改路径并自检

1. `gc_get_config(kind="nodes")`
2. `gc_put_config(kind="nodes", data=…)`
3. `gc_node(action="render")`
4. `gc_doctor()`

### 部署 Job

1. `gc_overview(refresh=true)` · `gc_get_jobs`
2. `gc_deploy(job="…")`（或指定 `gpus` / `nodes`）
3. 若返回异主租约拒绝 → `gc_deploy(job="…", force=true)`

### 排队占卡

1. `gc_queue(action="submit", job="…", gpus=…)`（或 `schedule=true` 一次做完）
2. `gc_queue(action="schedule")`
3. `gc_lease(action="list")` · `gc_overview`

### 腾空与排障

1. `gc_free_node(node="…")`；只需隔离 → `gc_node(action="cordon", name="…")`
2. 漂移：`gc_diagnose(action="check")`

---

## 5. Agent 行为约定

1. Job 是通用插件，按已安装列表与模板选择，而不是写死某一个业务名。
2. 改完 `nodes.json` 路径后执行 `node render` 与 `doctor`。
3. 腾空占卡用 `free` / `procs kill`；`cordon` 负责隔离调度。
4. `kill` / `free` / 写配置前，先用 `gc_overview` / `gc_get_config` 确认目标。
5. 收到 HTTP 401 时检查 token，可用 `gc_auth_status` 或 `GET /api/auth/status`。
6. 返回体：HTTP 看 JSON；`gc_cli` 看 `rc` / `stdout` / `stderr`，`rc≠0` 当失败。
7. 项目约定：`AGENTS.md`；Claude Code：`CLAUDE.md`；Cursor Skill：`.cursor/skills/gpu-control/SKILL.md`。

运维语义见 [手册.md](手册.md)；容器见 [develop/docker.md](develop/docker.md)。
