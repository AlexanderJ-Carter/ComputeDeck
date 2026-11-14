# 工作负载（Job）

Job = 任意可远程启停的脚本或容器，**不限模型服务**。

查找顺序：`config/local/jobs/` → `jobs/`。  
仓库只放 `*.example.json`；真实配置放 `config/local/jobs/`（gitignore）。

| 字段 | 含义 |
|------|------|
| `name` | 与文件名一致 |
| `description` | 说明 |
| `gpus_per_node` | 单节点占用 GPU 数 |
| `runtime` | `process`（默认）或 `docker` |
| `python` / `script` | process：远端解释器与管理脚本；支持 `${paths.*}` |
| `extra_args` | 追加启动参数（如 `--base-port 30000`） |
| `env` | 进程环境变量（值支持 `${paths.*}`） |
| `model` | 可选，对应 `models.json` 中的 `models[].id` |
| `docker` | `runtime=docker` 时：`image` / `name` / `gpus` / `ports` / `volumes` / `command` / `env` |
| `actions` | 允许的动作：start/stop/restart/status/wait |

路径：在 `nodes.json` 的 `defaults.paths` 声明集群默认目录；某节点不同则在该节点写 `paths` 覆盖。Job 里用 `${paths.python}` 等占位，启停时按节点解析。

```bash
cp config/examples/decider.example.json config/local/jobs/myjob.json
# 或 docker 示例
cp config/examples/job-docker.example.json config/local/jobs/vllm-docker.json

gpu-control models paths
gpu-control start myjob --node deepseek-1
gpu-control start myjob --node deepseek-1 --arg --base-port --arg 30000
```
