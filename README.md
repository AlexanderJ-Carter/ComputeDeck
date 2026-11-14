# gpu-control

多节点 GPU **通用控制面**（Web + CLI，SSH/Ansible）。  
不替代 K8s；模型/推理是可选模块。

版本：[`VERSION`](VERSION) · 变更：[CHANGELOG.md](CHANGELOG.md) · 手册：[docs/手册.md](docs/手册.md) · AI/MCP：[docs/ai-接入.md](docs/ai-接入.md)

## 快速开始（主控）

```bash
uv venv .venv && uv pip install -r requirements.txt
./examples/quickstart.sh
# 编辑 config/local/cluster.json、nodes.json
gpu-control doctor && gpu-control ui --background
# 建议启用控制面鉴权：
# gpu-control auth set-password --generate --enable
```

工作节点拉模型工具（按需）：`gpu-control models install-tools`  
（清单见 `requirements-models-worker.txt`）

## AI / API / MCP

```bash
curl -s http://127.0.0.1:8787/api              # 接口目录（含鉴权说明）
curl -s http://127.0.0.1:8787/api/openapi.json # OpenAPI 3
# 浏览器：http://127.0.0.1:8787/api/docs
uv pip install -r requirements-mcp.txt
# Cursor：复制 config/examples/mcp.cursor.json，改绝对路径 + API Token
```

Agent 改配置走 `POST /api/config/{cluster,nodes,job/NAME}` 或 MCP `gc_put_config`。  
HTTP / MCP 鉴权：`Authorization: Bearer $(gpu-control auth issue-token)`。

场景数据只放 `config/local/`；`config/examples/` 是中性模板。  
换集群：复制 examples → local，改节点/端口/路径即可，不必改框架代码（见手册 §11）。

## 页面一览

| 页 | 用途 |
|----|------|
| 看板 | 集群健康 · GPU 池 · 负载 · 工具栈就绪 |
| 节点矩阵 | 详细矩阵 · 腾空/隔离 |
| 任务 | 已安装 Job 启停（模板按需安装，不预装一堆） |
| 模型 | 下载 / 校验 / 登记 |
| 工具栈 | LLaMA-Factory / DeepSpeed / vLLM 一站式探测安装 |
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
gpu-control diagnose check && gpu-control logs
gpu-control gateway start --kind edge   # Control 统一推理入口 :18080
gpu-control gateway status
```

路径异构：`nodes.json` → `defaults.paths` + 节点覆盖；Job/models 用 `${paths.*}`。详见手册。  
统一入口务必在 **Control** 上启动（不要在 GPU worker 上起）。

## 配置

| 路径 | 提交 | 说明 |
|------|------|------|
| `config/examples/*` | ✅ | 模板 |
| `config/local/*` | ❌ | 本机真实配置 |
| `state/*` | ❌ | 运行时 |
