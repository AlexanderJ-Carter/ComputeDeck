# jobs/

Job 是**可插拔工作负载**：任意能远程 start/stop/status 的脚本或容器。  
**不绑定** Decider / 某一模型；那些只是示例。

- 真实配置：`config/local/jobs/<name>.json`（gitignore）
- 模板：`config/examples/shell-job.example.json`、`http-serve.example.json`、`job-docker.example.json`
- 领域示例（可删）：`decider.example.json`
- 最小管理脚本：`examples/job_manager_stub.py`

详见 [docs/手册.md](../docs/手册.md) §6。
