# gpu-control

多节点 GPU **通用控制面**（Web + CLI，SSH/Ansible）。  
不替代 K8s；模型/推理是可选模块。

版本：[`VERSION`](VERSION) · 完整说明：[docs/手册.md](docs/手册.md)

## 快速开始（主控）

```bash
uv venv .venv && uv pip install -r requirements.txt
./examples/quickstart.sh
# 编辑 config/local/cluster.json、nodes.json
gpu-control doctor && gpu-control ui --background
```

工作节点拉模型工具（按需）：`gpu-control models install-tools`  
（清单见 `requirements-models-worker.txt`）

## 页面一览

| 页 | 用途 |
|----|------|
| 一键运维 | 连通 / 体检 / 同步 |
| 总览 | 节点矩阵 · 腾空/隔离 |
| 任务 | 任意 Job 启停（JSON 插件，非绑死某一模型） |
| 模型 | 路径 · 校验 · 拉取 |
| 节点 | inventory 增删 |
| 推理池 | API 健康 · 网关片段 |
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
