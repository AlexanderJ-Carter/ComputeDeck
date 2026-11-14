# AI / API / MCP 接入

把 gpu-control 交给 Cursor 或其他 Agent 调配置、启停 Job、腾空节点时用本页。

**鉴权**：内网信任，无 token。不要把 `:8787` 暴露公网。

---

## 1. 三条接入路径

| 路径 | 入口 | 适用 |
|------|------|------|
| **HTTP** | `http://<control>:8787/api` | 脚本、OpenAPI 客户端、MCP 走 HTTP |
| **CLI** | `gpu-control …` | 主控本机、Ansible 编排 |
| **MCP** | `bin/gpu-control-mcp`（stdio） | Cursor 等 Agent |

目录与 OpenAPI：

```bash
curl -s http://127.0.0.1:8787/api | jq .
curl -s http://127.0.0.1:8787/api/openapi.json | jq '.info'
```

---

## 2. AI 最常用的 HTTP

改配置（调参主入口）：

- `GET/POST /api/config/cluster` — body `{"data":{…}}`
- `GET/POST /api/config/nodes` — 写完会触发 inventory render
- `GET/POST /api/config/job/{name}`

运维：

- `GET /api/doctor` · `GET /api/overview?refresh=1` · `GET /api/jobs`
- `POST /api/jobs/{name}/action` — `{"action":"start|stop|…","node":"…"}`
- `POST /api/free` — `{"node":"…","cordon":false,"no_kill":false,"wait":20}`
- `POST /api/procs` / `POST /api/procs/kill`
- `POST /api/models/check|verify|deps|pull`
- `POST /api/deploy` · `POST /api/exec`

完整列表以 `GET /api` 为准。

**改配置后建议顺序**：写 config → `POST /api/setup/render`（或 CLI `node render`）→ `GET /api/doctor`。

---

## 3. MCP（Cursor）

主控安装：

```bash
cd /path/to/gpu-control
uv pip install -r requirements-mcp.txt
# 内网可加: --index-url <your-pypi-mirror>
chmod +x bin/gpu-control-mcp
```

Cursor MCP 配置（示例见 `config/examples/mcp.cursor.json`）：

```json
{
  "mcpServers": {
    "gpu-control": {
      "command": "/ABS/PATH/TO/gpu-control/bin/gpu-control-mcp",
      "env": {
        "GPU_CONTROL_API_BASE": "http://127.0.0.1:8787"
      }
    }
  }
}
```

- 设了 `GPU_CONTROL_API_BASE`：工具优先打 HTTP（UI 需在跑）。
- 未设：工具直接调本地 CLI（不依赖 UI）。

常用工具：`gc_api_catalog`、`gc_doctor`、`gc_get_config` / `gc_put_config`、`gc_job_action`、`gc_free_node`、`gc_procs`、`gc_models`、`gc_cli`。

---

## 4. Agent 行为约定

1. Job 是通用插件，**不要假设只有 decider**。
2. 改 `nodes.json` 路径后必须 `node render` + `doctor`。
3. 腾空占卡用 `free` / `procs kill`，不要只 cordon。
4. 危险动作（kill / free / 写配置）先 `gc_overview` / `gc_get_config` 确认目标。
5. 项目 Skill：`.cursor/skills/gpu-control/SKILL.md`。

更多运维语义见 [手册.md](手册.md)。
