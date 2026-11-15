# 仓库布局

可提交内容保持中性；场景数据只进 `config/local/`。

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
| `jobs/` | 框架内置通用 Job（`shell-job`） |
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
| `LICENSE`（AGPL-3.0）· `NOTICE` · `AUTHORS` | 许可与版权 |
| `CONTRIBUTING` · `CODE_OF_CONDUCT` · `GOVERNANCE` · `SUPPORT` · `SECURITY` | 协作与安全 |
| `.github/` | CI、Issue / PR 模板 |
| `requirements*.txt` | 主控 / MCP / docs / worker 分层依赖 |
| `ansible.cfg` · `mkdocs.yml` | Ansible / 文档站 |
| `.github/workflows/ci.yml` | CI |

## 约定

1. 可提交树使用中性示例（`10.0.0.x` / `gpu-N` / `/path/to/...`）；真实 IP、主机名与个人路径放 `config/local/`。
2. 新增共享逻辑优先放 `lib/`；仅 CLI 胶水放 `scripts/`。
3. 用户可见行为变更写 `CHANGELOG.md` `[Unreleased]`。
4. Docstring 规范见 [docstrings.md](docstrings.md)；校验见 [ci.md](ci.md)。
