# 校验与 CI

## 本地

```bash
uv venv .venv && uv pip install -r requirements.txt
./scripts/check.sh
```

`scripts/check.sh` 覆盖：

| 步骤 | 内容 |
|------|------|
| py_compile | `web/` · `bin/` · `lib/` · `scripts/` · `examples/` |
| unittest | `tests/` |
| JS syntax | 有 Node ≥16 时检查 `app.js` / `xterm-fit.js` |
| 静态 UI | HTML id 唯一、JS 引用存在、无外网字体/CDN |

可选冒烟（需 UI + token）：

```bash
.venv/bin/python scripts/smoke_ui.py
gpu-control doctor
```

## GitHub Actions

| 工作流 | 触发 | 作用 |
|--------|------|------|
| `.github/workflows/ci.yml` | `main` push / PR | `check.sh` + MkDocs `--strict` + Docker 构建冒烟 |
| `.github/workflows/pages.yml` | `demo/**`、`docs/**`、`mkdocs.yml` 变更 | 发布演示页 + `/docs/` 手册到 GitHub Pages |
| `.github/workflows/release.yml` | tag `vX.Y.Z` | CHANGELOG → GitHub Release + 推送 GHCR 镜像 |

### 打包与发布策略

本仓库是 **clone / 容器部署的控制面**（Ansible、`config/`、`web/`、`state/` 与 `bin/gpu-control` 绑在一起），`pyproject.toml` 里 `[tool.uv] package = false`，**不发布 PyPI wheel**。

| 方式 | 说明 |
|------|------|
| 源码 | 克隆后 `uv venv && uv pip install -r requirements.txt`（可参考根目录 `uv.lock`；Agent/MCP 另装 `requirements-mcp.txt`） |
| 容器 | `Dockerfile` + `docker compose`；发版镜像 `ghcr.io/alexanderj-carter/computedeck`（Public） |
| PyPI | **不做**；控制面绑配置树 / Ansible / 静态资源，不是可独立 wheel 的库 |
| npm | **不做**；前端依赖已 vendor（如 xterm），产品不是 Node 包 |

### 发版（tag）

便捷准备（写入版本 + 折叠 CHANGELOG + 校验，**不 push**）：

```bash
python scripts/prepare_release.py --bump patch          # 预览
python scripts/prepare_release.py --bump patch --apply --check
```

手工等价步骤：

1. 同步 `VERSION` 与 `pyproject.toml` 的 `version`，把 CHANGELOG `[Unreleased]` 落成 `## [X.Y.Z] - YYYY-MM-DD`。
2. 本地：`python scripts/release_notes.py --check X.Y.Z` 与 `./scripts/check.sh`。
3. 提交后：`git tag -a vX.Y.Z -m "vX.Y.Z"` 并推送 tag（**需你明确允许 push**）。
4. Actions `Release` 创建 GitHub Release，并推送 `ghcr.io/<owner>/computedeck:<version>`（及 `latest`，含 SBOM/provenance）。
5. 官方包已 Public；**新 fork / 新 Package 名**首次推送后仍须在 Packages 设为 Public，见 [docker.md](docker.md)。
