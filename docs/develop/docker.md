# 控制面容器

镜像只包含 **ComputeDeck 控制面**（Web UI、CLI、Ansible、MCP 依赖）。  
GPU worker 仍是真实 SSH 主机，不打进镜像。

## 本地构建

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8787/api/health
docker compose exec control gpu-control doctor
```

首次前：

1. 从 `config/examples/` 复制 `cluster.json` / `nodes.json` 到 `config/local/`
2. 按 [`deploy/ssh/README.md`](https://github.com/AlexanderJ-Carter/ComputeDeck/blob/main/deploy/ssh/README.md) 放好私钥

## 镜像与发版

| 项 | 值 |
|----|-----|
| 镜像名 | `ghcr.io/alexanderj-carter/computedeck` |
| 标签 | 发版 tag `vX.Y.Z` → `X.Y.Z` / `X.Y` / `latest` |
| 触发 | `.github/workflows/release.yml` |
| CI 冒烟 | `.github/workflows/ci.yml` 的 `docker` job（只构建不推送） |

```bash
docker pull ghcr.io/alexanderj-carter/computedeck:latest
```

## 设计取舍

- 容器内建 `.venv`，与仓库 `paths.VENV_*` / Ansible 调用方式一致
- 挂载 `config/local` 与 `state`，配置与租约/队列不进镜像层
- 默认监听 `0.0.0.0:8787`，**生产必须开鉴权**（password / OIDC + Token）
- 不把 GPU 运行时打进控制面镜像；worker 上的 Docker Job 仍由节点本地 runtime 执行
