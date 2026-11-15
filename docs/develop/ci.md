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
| `.github/workflows/ci.yml` | `main` push / PR | `check.sh` + MkDocs `--strict` |
| `.github/workflows/pages.yml` | `demo/**` 变更 | 发布宣传展板到 GitHub Pages |
| `.github/workflows/release.yml` | tag `vX.Y.Z` | 校验版本 → 检查 → 用 CHANGELOG 建 GitHub Release |

### 打包与发布策略

本仓库是 **clone 部署的控制面**（Ansible、`config/`、`web/`、`state/` 与 `bin/gpu-control` 绑在一起），`pyproject.toml` 里 `[tool.uv] package = false`，**暂不发布 PyPI wheel**。

推荐安装：

```bash
git clone https://github.com/AlexanderJ-Carter/ComputeDeck.git
cd ComputeDeck
uv venv .venv && uv pip install -r requirements.txt
```

以后若要做 `pip install computedeck` / 上传 PyPI，需要先把可安装包布局（entry points、package-data、路径根）理顺，再开 Trusted Publishing；不要在现状下强行 `uv build` 上传。

可选后续（未做）：

- 打 tag 时再推 GHCR 容器镜像
- 把 `uv.lock` 纳入仓库以锁定依赖（需统一用 `uv sync`）

### 发版（tag）

1. 同步 `VERSION` 与 `pyproject.toml` 的 `version`，把 CHANGELOG `[Unreleased]` 落成 `## [X.Y.Z] - YYYY-MM-DD`。
2. 本地：`python scripts/release_notes.py --check X.Y.Z` 与 `./scripts/check.sh`。
3. `git tag -a vX.Y.Z -m "vX.Y.Z"` 并推送 tag（需你明确允许 push）。
4. Actions `Release` 会自动创建 GitHub Release。
