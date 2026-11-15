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
| `.github/workflows/pages.yml` | `demo/**` 变更 | 发布宣传展板到 GitHub Pages |
| `.github/workflows/release.yml` | tag `vX.Y.Z` | CHANGELOG → GitHub Release + 推送 GHCR 镜像 |

### 打包与发布策略

本仓库是 **clone / 容器部署的控制面**（Ansible、`config/`、`web/`、`state/` 与 `bin/gpu-control` 绑在一起），`pyproject.toml` 里 `[tool.uv] package = false`，**暂不发布 PyPI wheel**。

| 方式 | 说明 |
|------|------|
| 源码 | 克隆后 `uv pip install -r requirements.txt` |
| 容器 | `Dockerfile` + `docker compose`；发版镜像 `ghcr.io/alexanderj-carter/computedeck` |
| PyPI | 未做；需先理顺可安装包布局再开 Trusted Publishing |

可选后续：把 `uv.lock` 纳入仓库（需统一 `uv sync`）。

### 发版（tag）

1. 同步 `VERSION` 与 `pyproject.toml` 的 `version`，把 CHANGELOG `[Unreleased]` 落成 `## [X.Y.Z] - YYYY-MM-DD`。
2. 本地：`python scripts/release_notes.py --check X.Y.Z` 与 `./scripts/check.sh`。
3. `git tag -a vX.Y.Z -m "vX.Y.Z"` 并推送 tag（需你明确允许 push）。
4. Actions `Release` 会创建 GitHub Release，并推送 `ghcr.io/<owner>/computedeck:<version>`（及 `latest`）。
