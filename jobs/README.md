# 工作负载（Job）

Job = 任意可远程启停的脚本，**不限模型服务**。

查找顺序：`config/local/jobs/` → `jobs/`。  
仓库只放 `*.example.json`；真实配置放 `config/local/jobs/`（gitignore）。

| 字段 | 含义 |
|------|------|
| `name` | 与文件名一致 |
| `description` | 说明 |
| `gpus_per_node` | 单节点占用 GPU 数 |
| `python` / `script` | 远端解释器与管理脚本（支持 `${ENV}`） |
| `extra_args` | 追加参数 |
| `actions` | 允许的动作：start/stop/restart/status/wait |

```bash
cp config/examples/decider.example.json config/local/jobs/myjob.json
gpu-control start myjob --nodes all
```
