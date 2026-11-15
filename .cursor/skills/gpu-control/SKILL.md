---
name: gpu-control
description: >-
  Operate the gpu-control multi-node GPU plane: read/write cluster/nodes/job
  config, start/stop jobs, free GPUs, models paths. Use when editing
  gpu-control config, MCP tools gc_*, /api endpoints, or Ansible inventory.
---

# gpu-control

多节点 GPU 控制面。主控跑 UI `:8787` + CLI；worker 只跑 Job。

## 接入

1. 先调 `gc_api_catalog` 或 `GET /api` 看能力。
2. 有 MCP：优先 `gc_*`；否则 HTTP `/api/*` 或 `gpu-control` CLI。
3. `GPU_CONTROL_API_BASE` 指向 UI 时 MCP 走 HTTP。

## 改配置

- 读：`gc_get_config(kind=cluster|nodes|job, name=…)`
- 写：`gc_put_config` → 再 `gc_cli(["node","render"])` → `gc_doctor()`
- Job JSON 在 `config/local/jobs/`；路径用 `${paths.*}`，见 `nodes.json` 的 `defaults.paths`

## 运维

- 总览：`gc_overview(refresh=true)`
- 启停：`gc_job_action(job, action, node=…)` — 按已安装 Job 选择
- 腾空：`gc_free_node` / `gc_procs(action=kill, all_procs=true)`
- 模型：`gc_models(action=deps|check|verify|list)`

## 约束

- 默认面向内网；生产环境开启 password / OIDC 与 API Token
- 配置以 `config/local` 为准；删除的 API 记入 CHANGELOG，代码不堆冗余 fallback
- GPU 数量读配置 / 实采 / `scheduling.unit_gpus`，缺配置时报错，不静默填默认值
- 详细接口：`docs/ai-接入.md`、手册 `docs/手册.md`
