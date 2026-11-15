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

工作流文件：仓库根目录 `.github/workflows/ci.yml`。

- 安装 `uv` + Python 3.11 + `requirements.txt`
- 跑 `./scripts/check.sh`
- 安装 docs 依赖并以 `--strict` 构建 MkDocs

推送到 `main` 或打开 PR 时触发。无远程时仍以本地 `check.sh` 为准。
