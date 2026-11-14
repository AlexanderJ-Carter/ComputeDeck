# gpu-control 文档

多节点 GPU **通用控制面**（Web + CLI + MCP，SSH/Ansible）。

| 入口 | 说明 |
|------|------|
| [上手与运维手册](手册.md) | 安装、节点、Job、Gateway、腾空 |
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

版本见仓库根 `VERSION`；变更见 `CHANGELOG.md`。
