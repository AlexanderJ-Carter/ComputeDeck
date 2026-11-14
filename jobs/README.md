# jobs/

框架自带的**通用 Job**（可直接出现在 UI「任务」下拉框）：

| 文件 | 用途 |
|------|------|
| `shell-job.json` | 任意进程管理脚本 |
| `http-serve.json` | HTTP / 推理服务管理脚本模板 |
| `vllm-docker.json` | Docker + vLLM 示例 |

场景专用 Job（如 Decider）只放 `config/local/jobs/`，不要写进本目录。

- 覆盖：同名文件在 `config/local/jobs/` 优先于 `jobs/`
- 更多模板：`config/examples/*.example.json`
- 安装模板：`gpu-control jobs install <template>` 或 UI「从模板添加」
