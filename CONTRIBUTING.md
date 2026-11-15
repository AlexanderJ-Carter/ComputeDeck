# 贡献指南

**ComputeDeck**（命令：`gpu-control`）是多节点 GPU 控制面，配置驱动，方便在不同集群间复用。

仓库：<https://github.com/AlexanderJ-Carter/ComputeDeck>  
参与前请阅读 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 与 [GOVERNANCE.md](GOVERNANCE.md)。

## 开发环境

```bash
uv venv .venv && uv pip install -r requirements.txt
uv pip install -r requirements-docs.txt   # 可选：文档站
./scripts/check.sh                        # 语法 + 单测 + UI 静态审计
```

UI 冒烟（需 UI 已启动且有 token）：

```bash
.venv/bin/python scripts/smoke_ui.py
```

## 约定

1. **通用性**：可提交文件使用中性示例（`10.0.0.x` / `gpu-N` / `/path/to/...`），真实集群信息放本机配置。
2. **场景数据**进 `config/local/`（gitignore）；框架与 `config/examples/` 保持中性。
3. **Docstring**：中文 + Sphinx 冒号字段（`:param:` / `:returns:` / `:raises:`），见 [`docs/develop/docstrings.md`](docs/develop/docstrings.md)。
4. **目录归档**：共享逻辑进 `lib/`，CLI 胶水进 `scripts/`，见 [`docs/develop/layout.md`](docs/develop/layout.md)。
5. **API 演进**：删除的 API 记入 `CHANGELOG.md`，代码里不堆冗余 fallback。
6. **用户可见行为**变更时更新 `CHANGELOG.md` 的 `[Unreleased]`。
7. **许可证**：贡献按 **AGPL-3.0** 授权给项目（见 [LICENSE](LICENSE) / [NOTICE](NOTICE)）。

提交信息用 Conventional Commits（`feat` / `fix` / `docs` / `chore` / `style` / `refactor` / `test`）。版本号用 annotated tag `vX.Y.Z` 标记。

## Pull Request

1. 使用 `.github/PULL_REQUEST_TEMPLATE.md`。
2. 确保 `./scripts/check.sh` 通过。
3. 大改动先开 Issue 讨论（尤其是破坏性 API / 重命名 CLI）。

## 发版

主产物：**GitHub Release** + **GHCR** `ghcr.io/alexanderj-carter/computedeck`（不发 PyPI / npm）。

```bash
# 预览 → 写入版本/CHANGELOG → 跑校验（不推远程）
python scripts/prepare_release.py --bump patch
python scripts/prepare_release.py --bump patch --apply --check
# 再按脚本打印的 git commit / tag 步骤操作；push 前须明确允许
```

官方 GHCR 包已 Public；fork 首次推送后若匿名 401，再到 Packages 改可见性（见 [`docs/develop/docker.md`](docs/develop/docker.md)）。

**不发布 PyPI / npm**：仓库即控制面（`package = false`），用克隆 + `uv` 或容器。详见 [`docs/develop/ci.md`](docs/develop/ci.md)。  
Agent 约定：[`AGENTS.md`](AGENTS.md) · [`CLAUDE.md`](CLAUDE.md) · [`docs/ai-接入.md`](docs/ai-接入.md)。

## 文档

```bash
uv pip install -r requirements-docs.txt
gpu-control docs build    # → web/static/help/
```

叙述放 `docs/`；接口契约以 `lib/api_catalog.py` → `/api/openapi.json` 为准。
