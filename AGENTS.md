# Agent 约定（ComputeDeck / gpu-control）

产品名 **ComputeDeck**；CLI、MCP 服务名、环境变量前缀仍为 `gpu-control`。

## 权威来源

1. 运行时契约：`GET /api` · `GET /api/openapi.json`（由 `lib/api_catalog.py` 生成）
2. 人读接入：`docs/ai-接入.md`
3. Cursor Skill：`.cursor/skills/gpu-control/SKILL.md`

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
