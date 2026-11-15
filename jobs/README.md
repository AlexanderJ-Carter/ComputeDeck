# jobs/

框架内置通用 Job：

| 文件 | 用途 |
|------|------|
| `shell-job.json` | 任意 start/stop/status 管理脚本（契约示例） |

推理、微调、Docker 等从模板安装到 `config/local/jobs/`：

```bash
gpu-control jobs templates
gpu-control jobs install --recommended          # 一键推荐包
gpu-control jobs install http-serve --name my-api
gpu-control jobs install train-job --name sft
gpu-control jobs install vllm-docker --name vllm
```

推荐包含：`shell-job`、`http-serve`、`vllm-serve`、`vllm-docker`、`train-job`、`llamafactory-train`、`deepspeed-train`、`torchrun-train`、`ray-cluster`、`docker-job`。

- 同名时 `config/local/jobs/` 优先于 `jobs/`
- 模板目录：`config/examples/*.example.json`
- Web：任务页「安装推荐包」或 `POST /api/jobs/install-template`（`{"recommended":true}`）

## Job 最小契约

`gpu-control doctor` / `validate_job_dict` 强校验：

| 字段 | 要求 |
|------|------|
| `name` | 非空 |
| `runtime` | `process`（默认）或 `docker` |
| `kind` | `service`（默认）或 `train` |
| process | 必填 `python` + `script` |
| docker | 必填 `docker.image` |
| `actions` | 若写出，须含 `start`/`stop`/`status`/`wait` |

### 管理脚本（process）

远端执行：`python script <action> …`，`action` ∈ start/stop/restart/status/wait。

- 退出码 `0` = 成功；`status` 未就绪可用非 0（playbook 允许 status rc=1）
- **`wait`**：业务就绪（端口/HTTP 等），由脚本自行实现；训练类可表示「进程已拉起」

### Docker `wait`

playbook 只轮询容器 `running`，**不**探业务 HTTP。需要应用就绪请用 process + 自有 wait。摘要字段 `wait_semantics`：`container_running` | `manager_script`。
