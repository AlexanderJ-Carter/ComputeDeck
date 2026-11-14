# jobs/

框架**只内置一个通用 Job**，避免预制一堆业务任务：

| 文件 | 用途 |
|------|------|
| `shell-job.json` | 任意 start/stop/status 管理脚本（契约示例） |

推理、微调、Docker 等一律从模板安装到 `config/local/jobs/`：

```bash
gpu-control jobs templates
gpu-control jobs install http-serve --name my-api
gpu-control jobs install train-job --name sft
gpu-control jobs install vllm-docker --name vllm
```

- 同名时 `config/local/jobs/` 优先于 `jobs/`
- 模板目录：`config/examples/*.example.json`
