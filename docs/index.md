# gpu-control 文档

多节点 GPU **通用控制面**（Web + CLI + MCP，SSH/Ansible）。  
不绑定具体模型、业务 API 或集群；场景配置只放 `config/local/`。

| 入口 | 说明 |
|------|------|
| [上手与运维手册](手册.md) | 安装、节点、Job、Gateway、腾空 |
| [生产部署](生产部署.md) | 支持边界、systemd、端口、发布检查清单 |
| [安全加固](安全加固.md) | 鉴权、绑定、Token、SSH host key、TLS |
| [配置参考](配置参考.md) | cluster / nodes / jobs / models / 环境变量 |
| [分布式与推理](分布式与推理.md) | 多机注入、DeepSpeed、Ray、vLLM、微调 |
| [高可用与负载均衡](高可用与负载均衡.md) | Edge LB、自动故障转移、watchdog、AI Gateway |
| [升级与备份](升级与备份.md) | 升级、回滚、备份、迁移 |
| [故障排查](故障排查.md) | UI/SSH/Gateway/终端/状态异常 |
| [AI / MCP 接入](ai-接入.md) | Agent、Bearer、Cursor MCP |
| [HTTP API](api/index.md) | 接口分组与常用示例 |
| [OpenAPI / Swagger](api/openapi.md) | `/api/docs` 交互文档 |
| [Docstring 规范](develop/docstrings.md) | 中文注释约定 |

## 快速链接（控制面在线时）

- UI：`http://<control>:8787/`
- API 目录：`/api`
- OpenAPI JSON：`/api/openapi.json`
- Swagger 页：`/api/docs`
- 本文档站：`/help/`（需先 `gpu-control docs build`）

```bash
uv pip install -r requirements-docs.txt
gpu-control docs build    # → web/static/help/
gpu-control docs serve    # 本地预览 :8000
```

## 本地校验

```bash
./scripts/check.sh                       # py_compile + 单测 + UI 静态审计
.venv/bin/python scripts/smoke_ui.py     # 需 UI 运行 + token
gpu-control doctor
```

版本见仓库根 `VERSION`；变更见 `CHANGELOG.md`。
