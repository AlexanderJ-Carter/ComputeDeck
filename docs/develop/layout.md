# 仓库布局

通用控制面仓库：可提交内容保持中性；场景数据只进 `config/local/`。

## 顶层

| 路径 | 职责 |
|------|------|
| `bin/` | CLI / MCP 可执行入口（`gpu-control`、`gpu-control-mcp`） |
| `lib/` | 共享库（路径、鉴权、Job、端点、状态、OpenAPI 等） |
| `scripts/` | CLI 子命令与运维脚本（由 `bin/gpu-control` 或 Ansible 调用） |
| `web/` | 控制面 HTTP / WebSocket 与静态 UI |
| `mcp_server/` | MCP stdio 服务 |
| `playbooks/` | Ansible playbook |
| `config/examples/` | 可提交的中性模板 |
| `config/local/` | 本机真实配置与密钥（gitignore） |
| `jobs/` | 框架内置通用 Job（仅 `shell-job`） |
| `examples/` | 参考管理脚本与训练/推理示例 |
| `docs/` | MkDocs 中文文档 |
| `tests/` | 单元测试 |
| `state/` · `snapshots/` | 运行时产物占位（gitignore 内容） |

## 根文件

| 文件 | 说明 |
|------|------|
| `README.md` | 上手入口 |
| `CHANGELOG.md` | Keep a Changelog |
| `VERSION` · `pyproject.toml` | 版本（须同步） |
| `LICENSE` · `CONTRIBUTING.md` · `SECURITY.md` | 许可与协作 |
| `requirements*.txt` | 主控 / MCP / docs / worker 分层依赖 |
| `ansible.cfg` · `mkdocs.yml` | Ansible / 文档站 |
| `.github/workflows/ci.yml` | CI |

## 约定

1. **不要**把真实 IP、主机名、业务 Job、个人路径写进可提交树。
2. 新增共享逻辑优先放 `lib/`；仅 CLI 胶水放 `scripts/`。
3. 用户可见行为变更写 `CHANGELOG.md` `[Unreleased]`。
4. Docstring 规范见 [docstrings.md](docstrings.md)；校验见 [ci.md](ci.md)。
