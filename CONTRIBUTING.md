# 贡献指南

**ComputeDeck**（仓库 / CLI：`gpu-control`）是通用多节点 GPU 控制面：配置驱动，不绑定特定 IP、机型、业务 Job 或公司内网路径。

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

## 必须遵守

1. **通用性**：禁止把真实集群 IP、主机名、业务名、个人路径写进可提交文件；示例只用 `10.0.0.x` / `gpu-N` / `/path/to/...`。
2. **场景数据**只进 `config/local/`（gitignore）；框架与 `config/examples/` 保持中性。
3. **Docstring**：中文 + Sphinx 冒号字段（`:param:` / `:returns:` / `:raises:`）；见 [`docs/develop/docstrings.md`](docs/develop/docstrings.md)。禁止 Google 风格 `Args:` / `Returns:`。
4. **目录归档**：共享逻辑进 `lib/`，CLI 胶水进 `scripts/`，场景只进 `config/local/`；见 [`docs/develop/layout.md`](docs/develop/layout.md)。
5. **不要向后兼容堆料**：删掉的 API 记入 `CHANGELOG.md`，不在代码里留冗余 fallback。
6. **变更用户可见行为**时更新 `CHANGELOG.md` 的 `[Unreleased]`。
7. **许可证**：贡献默认按 **AGPL-3.0** 授权给项目（见 [LICENSE](LICENSE) / [NOTICE](NOTICE)）。

提交信息用 Conventional Commits（`feat` / `fix` / `docs` / `chore` / `style` / `refactor` / `test`）。版本号用 annotated tag `vX.Y.Z` 标记。

## Pull Request

1. 使用 `.github/PULL_REQUEST_TEMPLATE.md`。
2. 确保 `./scripts/check.sh` 通过。
3. 大改动先开 Issue 讨论（尤其是破坏性 API / 重命名 CLI）。

## 发版

1. 同步 `VERSION` 与 `pyproject.toml` 的 `version`。
2. 将 `[Unreleased]` 移入新版本节。
3. 打 annotated tag：`vX.Y.Z`。
4. 本地跑通 `./scripts/check.sh`。

## 文档

```bash
uv pip install -r requirements-docs.txt
gpu-control docs build    # → web/static/help/
```

叙述放 `docs/`；接口契约以 `lib/api_catalog.py` → `/api/openapi.json` 为准。
