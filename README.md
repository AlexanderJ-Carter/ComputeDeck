# gpu-control

多节点 GPU **通用控制面**（Web + CLI + MCP，SSH/Ansible）。  
不替代 K8s；推理、模型、分布式训练、Ray、vLLM 都是可选模块，由 Job 模板组合。

版本：[`VERSION`](VERSION) · 变更：[CHANGELOG.md](CHANGELOG.md)  
文档： [手册](docs/手册.md) · [生产部署](docs/生产部署.md) · [安全加固](docs/安全加固.md) · [配置参考](docs/配置参考.md) · [分布式与推理](docs/分布式与推理.md) · [高可用与负载均衡](docs/高可用与负载均衡.md) · [故障排查](docs/故障排查.md) · [AI/MCP](docs/ai-接入.md)

## 支持边界

| 组件 | 要求 |
|------|------|
| 主控 | Linux（推荐）/ macOS；Python 3.11+；`uv`；Ansible；SSH |
| 工作节点 | Linux + NVIDIA GPU；SSH 免密；`nvidia-smi` |
| 可选 | Docker + NVIDIA Toolkit；vLLM / Ray / LLaMA-Factory / DeepSpeed 等 |

## 快速开始（主控）

```bash
uv venv .venv && uv pip install -r requirements.txt
./examples/quickstart.sh
cp -n config/examples/cluster.example.json config/local/cluster.json   # 按需改
cp -n config/examples/nodes.example.json   config/local/nodes.json
gpu-control node render
gpu-control doctor
gpu-control auth set-password --generate --enable   # 生产必须
gpu-control auth issue-token
gpu-control ui --background
gpu-control watchdog --background   # 自动重启 default_job
```

UI 默认绑定 `cluster.control.ui_host`；未配置则为 `127.0.0.1`。  
远程访问：`ssh -L 8787:127.0.0.1:8787 <control>`。

工作节点拉模型工具（按需）：`gpu-control models install-tools`  
（清单见 `requirements-models-worker.txt`）

## AI / API / MCP

```bash
curl -s http://127.0.0.1:8787/api              # 接口目录（含鉴权说明）
curl -s http://127.0.0.1:8787/api/openapi.json # OpenAPI 3
# 浏览器：/api/docs（Swagger 试调）· /help/（MkDocs 文档站）
uv pip install -r requirements-mcp.txt
uv pip install -r requirements-docs.txt && gpu-control docs build
# Cursor：复制 config/examples/mcp.cursor.json，改绝对路径 + API Token
```

Agent 改配置走 `POST /api/config/{cluster,nodes,job/NAME}` 或 MCP `gc_put_config`。  
HTTP / MCP 鉴权：`Authorization: Bearer $(cat config/local/secrets/ui_api_token)`。

场景数据只放 `config/local/`；`config/examples/` 是中性模板。  
换集群：复制 examples → local，改节点/端口/路径即可，不必改框架代码（见手册 §11）。

## 页面一览

| 页 | 用途 |
|----|------|
| 看板 | 单屏聚焦：健康评分 · GPU 池 · API/Edge/TLS · 快捷操作 |
| 节点矩阵 | 详细矩阵 · 腾空/隔离 · GPU 进程 |
| 任务 | 已安装 Job 启停 / 模板安装 / 部署 / 队列 |
| 模型 | 下载 / 校验 / 登记 / 与 Job 联动 |
| 工具栈 | LLaMA-Factory / DeepSpeed / vLLM / Ray 探测与安装 |
| 节点 | inventory 增删 |
| API 端点 | 健康 · 统一入口（edge/nginx/LiteLLM） |
| 命令 / 终端 | 批量 exec · 交互 SSH |

## 常用命令

```bash
gpu-control overview
gpu-control procs list --node <name> --refresh
gpu-control procs kill --node <name> --all
gpu-control free --node <name> --wait 30 [--cordon]
gpu-control models deps && gpu-control models verify
gpu-control models pull --model <id>
gpu-control deploy <job> --gpus 4
gpu-control toolkits install --bundle train-stack
gpu-control jobs install train-job --name sft
gpu-control deploy sft --nodes gpu-1,gpu-2 --gpus 16
gpu-control jobs install ray-cluster --name ray && gpu-control deploy ray --nodes gpu-1,gpu-2
gpu-control jobs logs sft --node gpu-1 --lines 200
gpu-control jobs install vllm-serve --name vllm && gpu-control deploy vllm --nodes gpu-1 --gpus 4
gpu-control diagnose check && gpu-control logs
gpu-control gateway ensure              # 规整配套并拉起 Edge :18080
gpu-control gateway status              # companions（nginx 在 kind=edge 时不必装）
```

路径异构：`nodes.json` → `defaults.paths` + 节点覆盖；Job/models 用 `${paths.*}`。详见手册。  
统一入口务必在 **Control** 上启动（不要在 GPU worker 上起）。

## 分布式 / 推理

- 多机：`distributed=true` 自动注入 `NNODES/NODE_RANK/MASTER_*/NPROC_PER_NODE/HOSTFILE`。
- 微调：`train-job` / `llamafactory-train` / `deepspeed-train` / `torchrun-train`。
- Ray：`ray-cluster` 模板 + `examples/ray_manager.py`（head/worker + dashboard 8265）。
- vLLM：`vllm-serve`（真实就绪检查）或 `vllm-docker`。
- 高可用：Edge 健康感知 LB + 失败重试/冷却；可用时自动挂 AI Gateway/LiteLLM；watchdog 自动重启。
- 详见 [分布式与推理](docs/分布式与推理.md) 与 [高可用与负载均衡](docs/高可用与负载均衡.md)。

## 校验与发布

```bash
./scripts/check.sh                        # py_compile + 单测 + UI 静态审计
.venv/bin/python scripts/smoke_ui.py      # 需 UI 运行 + token
gpu-control doctor

# 干净发布包（不包含 config/local、state、inventory.ini）
git archive --format=tar.gz --prefix=gpu-control/ HEAD -o gpu-control.tgz
```

发布前检查：[生产部署 §6](docs/生产部署.md)。

## 配置

| 路径 | 提交 | 说明 |
|------|------|------|
| `config/examples/*` | ✅ | 中性模板 |
| `config/local/*` | ❌ | 本机真实配置 / 密钥 |
| `state/*` | ❌ | 运行时（原子写 + 锁） |
