# AI / API / MCP 接入

把 gpu-control 交给 Cursor 或其他 Agent 调配置、启停 Job、腾空节点时用本页。

## 鉴权（必读）

| 模式 | 说明 |
|------|------|
| `none` | 仅适合 SSH 隧道；不要裸暴露 `:8787` |
| `password` | Session Cookie（浏览器）+ Bearer Token（脚本 / MCP） |
| `oidc` | 预留，尚未实现 |

```bash
gpu-control auth set-password --generate --enable
gpu-control auth issue-token          # 写入 config/local/secrets/ui_api_token
gpu-control auth status
```

- 浏览器：`POST /api/auth/login` → HttpOnly Cookie `gc_session`
- 脚本 / MCP：`Authorization: Bearer <token>`
- WebSocket 终端：可用查询参数 `?access_token=<token>`
- 公开发现路径（无需登录）：`/api` · `/api/openapi.json` · `/api/docs` · `/api/health` · `/api/auth/*`

---

## 1. 三条接入路径

| 路径 | 入口 | 适用 |
|------|------|------|
| **HTTP** | `http://<control>:8787/api` | 脚本、OpenAPI、MCP 走 HTTP |
| **CLI** | `gpu-control …` | 主控本机、Ansible |
| **MCP** | `bin/gpu-control-mcp`（stdio） | Cursor 等 Agent |

文档入口：

```bash
# 机器可读目录（含鉴权说明）
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api | jq '.auth,.docs'

# OpenAPI 3（含 bearerAuth / cookieAuth）
curl -s http://127.0.0.1:8787/api/openapi.json | jq '.info,.components.securitySchemes'

# 浏览器文档页（无需外网 CDN）
open http://127.0.0.1:8787/api/docs
```

---

## 2. AI 最常用的 HTTP

改配置（调参主入口）：

- `GET/POST /api/config/cluster` — body `{"data":{…}}`
- `GET/POST /api/config/nodes` — 写完会触发 inventory render
- `GET/POST /api/config/job/{name}`

运维：

- `GET /api/doctor` · `GET /api/overview?refresh=1` · `GET /api/overview?light=1`
- `GET /api/jobs` · `POST /api/jobs/install-template`
- `POST /api/jobs/{name}/action` — `{"action":"start|stop|…","node":"…"}`
- `POST /api/free` — `{"node":"…","cordon":false,"no_kill":false,"wait":20}`
- `POST /api/procs` / `POST /api/procs/kill`
- `POST /api/models/check|verify|deps|pull`
- `POST /api/deploy` · `POST /api/exec`
- `POST /api/gateway/start|stop` · `GET /api/gateway/status`

完整列表以 `GET /api` 或 `/api/docs` 为准。

**改配置后建议顺序**：写 config → `POST /api/setup/render`（或 CLI `node render`）→ `GET /api/doctor`。

---

## 3. MCP（Cursor）

主控安装：

```bash
cd /path/to/gpu-control
uv pip install -r requirements-mcp.txt
chmod +x bin/gpu-control-mcp
```

Cursor MCP 配置（示例见 `config/examples/mcp.cursor.json`）：

```json
{
  "mcpServers": {
    "gpu-control": {
      "command": "/ABS/PATH/TO/gpu-control/bin/gpu-control-mcp",
      "env": {
        "GPU_CONTROL_API_BASE": "http://127.0.0.1:8787",
        "GPU_CONTROL_API_TOKEN": "<gpu-control auth issue-token 的内容>"
      }
    }
  }
}
```

| 环境变量 | 作用 |
|----------|------|
| `GPU_CONTROL_API_BASE` | 设置后工具优先打 HTTP（UI 需在跑） |
| `GPU_CONTROL_API_TOKEN` | Bearer；鉴权启用时必填，否则 401 |
| （均未设） | 工具直接调本地 CLI，不依赖 UI |

常用工具：`gc_api_catalog` · `gc_auth_status` · `gc_doctor` · `gc_get_config` / `gc_put_config` · `gc_job_action` · `gc_free_node` · `gc_procs` · `gc_models` · `gc_deploy` · `gc_exec` · `gc_cli`。

---

## 4. Agent 行为约定

1. Job 是通用插件（推理 / 微调），**不要假设只有某一业务 Job**。
2. 改 `nodes.json` 路径后必须 `node render` + `doctor`。
3. 腾空占卡用 `free` / `procs kill`，不要只 cordon。
4. 危险动作（kill / free / 写配置）先 `gc_overview` / `gc_get_config` 确认目标。
5. HTTP 401 → 检查 token；先 `gc_auth_status` / `GET /api/auth/status`。
6. 项目 Skill：`.cursor/skills/gpu-control/SKILL.md`。

更多运维语义见 [手册.md](手册.md)。
