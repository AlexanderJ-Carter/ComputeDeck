# 控制面容器

镜像只包含 **ComputeDeck 控制面**（Web UI、CLI、Ansible、MCP 依赖）。  
GPU worker 仍是真实 SSH 主机，不打进镜像。

## 本地构建

```bash
cp -n config/examples/cluster.example.json config/local/cluster.json
cp -n config/examples/nodes.example.json config/local/nodes.json
cp -n .env.example .env   # 可选：改端口 / 镜像 tag
# 私钥：deploy/ssh/，见 deploy/ssh/README.md
docker compose up -d --build
curl -fsS http://127.0.0.1:8787/api/health
docker compose exec control gpu-control doctor
```

首次前：

1. 从 `config/examples/` 复制 `cluster.json` / `nodes.json` 到 `config/local/`
2. 按 [`deploy/ssh/README.md`](https://github.com/AlexanderJ-Carter/ComputeDeck/blob/main/deploy/ssh/README.md) 放好私钥
3. 生产务必开 password / OIDC + API Token（见 [安全加固](../安全加固.md)）

## 镜像与发版

| 项 | 值 |
|----|-----|
| 镜像名 | `ghcr.io/alexanderj-carter/computedeck` |
| 标签 | 发版 tag `vX.Y.Z` → `X.Y.Z` / `X.Y` / `latest` |
| 触发 | `.github/workflows/release.yml` |
| CI 冒烟 | `.github/workflows/ci.yml` 的 `docker` job（只构建不推送） |
| 本地准备 | `python scripts/prepare_release.py --bump patch --apply --check` |

```bash
docker pull ghcr.io/alexanderj-carter/computedeck:latest
# 或覆盖 compose 镜像：
# COMPUTEDECK_IMAGE=ghcr.io/alexanderj-carter/computedeck:0.22.0 docker compose up -d
```

### 包可见性

官方 `ghcr.io/alexanderj-carter/computedeck` 已 **Public**，可直接：

```bash
docker pull ghcr.io/alexanderj-carter/computedeck:0.22.0
```

GHCR **新建** Package 默认仍私有。fork / 改名后的首次推送若匿名 401：Packages → 该包 → **Package settings** → **Public**。  
镜像带 OCI label（`org.opencontainers.image.source` 等），便于在 Packages 页跳回源码。

## 与 Agent / MCP

- IDE 侧 stdio MCP 仍用宿主机上的 `bin/gpu-control-mcp`（或挂载进开发机的同路径），示例见 `config/examples/mcp.*.json`。
- 设 `GPU_CONTROL_API_BASE=http://127.0.0.1:<UI_PORT>` 指向已映射端口；Token 与源码安装相同。
- 容器内调试：`docker compose exec control gpu-control …` / `gpu-control-mcp`（交互式排查，不是给 Cursor 挂 stdio 的首选）。

## 设计取舍

- 容器内建 `.venv`，与仓库 `paths.VENV_*` / Ansible 调用方式一致
- 挂载 `config/local` 与 `state`，配置与租约/队列不进镜像层
- 默认监听 `0.0.0.0:8787`，**生产必须开鉴权**（password / OIDC + Token）
- compose 里 `GPU_CONTROL_UI_SECURE` 默认 `0`（本机 HTTP）；反代 HTTPS 后再设 `1`
- 不把 GPU 运行时打进控制面镜像；worker 上的 Docker Job 仍由节点本地 runtime 执行
- **不发 PyPI / npm**：控制面是仓库/镜像交付，不是库包（见 [ci.md](ci.md)）
