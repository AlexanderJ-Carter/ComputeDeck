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
```

工作节点拉模型工具（按需）：`gpu-control models install-tools`  
（清单见 `requirements-models-worker.txt`）

## AI / API / MCP

```bash
curl -s http://127.0.0.1:8787/api              # 接口目录
curl -s http://127.0.0.1:8787/api/openapi.json # OpenAPI
uv pip install -r requirements-mcp.txt
# Cursor：复制 config/examples/mcp.cursor.json，改成绝对路径
```

Agent 改配置走 `POST /api/config/{cluster,nodes,job/NAME}` 或 MCP `gc_put_config`。

场景数据只放 `config/local/`；`config/examples/` 是中性模板。  
换集群：复制 examples → local，改节点/端口/路径即可，不必改框架代码（见手册 §11）。

## 页面一览

| 页 | 用途 |
|----|------|
| 一键运维 | 连通 / 体检 / 同步 |
| 总览 | 节点矩阵 · 腾空/隔离 |
| 任务 | 任意 Job 启停（JSON 插件） |
| 模型 | 下载 / 校验 / 登记 |
| 节点 | inventory 增删 |
| API 端点 | 健康 · 网关片段 |
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
```

路径异构：`nodes.json` → `defaults.paths` + 节点覆盖；Job/models 用 `${paths.*}`。详见手册。

## 配置

| 路径 | 提交 | 说明 |
|------|------|------|
| `config/examples/*` | ✅ | 模板 |
| `config/local/*` | ❌ | 本机真实配置 |
| `state/*` | ❌ | 运行时 |
