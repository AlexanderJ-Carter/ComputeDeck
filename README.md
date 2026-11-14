# gpu-control

多节点 GPU **通用控制面**：Web + CLI，SSH/Ansible fan-out。  
不替代 K8s；也不是「只做模型部署」的工具。

版本：[`VERSION`](VERSION)

## 定位（请先读）

| 层级 | 能力 | 是否必须 |
|------|------|----------|
| **Core** | 节点清单、GPU/主机指标、远程命令、**交互终端**、腾空/隔离、lease、doctor、配置向导 | 是 |
| **Workloads** | `jobs/*.json` 注册任意启停脚本（推理 / 评测 / 预处理…） | 按需 |
| **Module: inference** | endpoints、health、deploy、gateway（nginx/LiteLLM 产物） | 按需 |
| **Module: models** | 模型目录、路径检查、权重拉取、启动参数 | 按需 |
| **Docker Job** | `runtime: docker` 在节点起停容器（需本机已装 docker） | 按需 |

设计说明见 [docs/架构.md](docs/架构.md)，操作见 [docs/指南.md](docs/指南.md)。

## 快速开始

```bash
uv venv .venv && uv pip install -r requirements.txt
./examples/quickstart.sh
# 编辑 config/local/cluster.json、nodes.json
gpu-control doctor
gpu-control ui --background    # 仅在主控 control 启动 :8787
# 隧道：ssh -L 8787:127.0.0.1:8787 <control>
```

角色：`control`（`10.146.226.228`，CPU）= 主控 UI/CLI/Ansible；`deepseek-1/2/4/5` = GPU 工作节点。  
管理台 **终端** = 真 SSH PTY（xterm）；底部「批量输出」只显示命令结果，不是交互 shell。

## 常用命令

```bash
# Core
gpu-control overview
gpu-control metrics               # CPU / 内存 / 负载
gpu-control exec --nodes all -- 'nvidia-smi -L'
gpu-control free --node <name> --cordon

# Workload（示例名可换成你的 job）
gpu-control start <job> --nodes all
gpu-control deploy <job> --gpus 4

# 可选模块
gpu-control health
gpu-control gateway               # nginx / curl / LiteLLM 对接说明
gpu-control models list
gpu-control models check
gpu-control models paths
```

节点目录不一致时：在 `nodes.json` 写 `defaults.paths`，差异节点再覆盖 `paths`；Job 用 `${paths.python}` 等。

## 仓库布局

```text
bin/ web/ lib/ playbooks/ scripts/
config/examples/   # 可提交模板
config/local/      # 本机配置（gitignore）
jobs/              # Job 约定说明 + example
docs/架构.md  docs/指南.md
```

## 配置

| 路径 | 提交 | 说明 |
|------|------|------|
| `config/examples/*` | ✅ | 脱敏模板 |
| `config/local/*` | ❌ | 密钥路径、内网 IP、真实 job/models |
| `state/*` | ❌ | 运行时状态 |
