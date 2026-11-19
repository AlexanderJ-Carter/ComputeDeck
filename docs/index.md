# ComputeDeck 文档

**ComputeDeck**（命令仍是 `gpu-control`）管多机 GPU：谁在用、跑什么任务、对外怎么进。  
Web / CLI / MCP 共用同一套状态；场景配置写在 `config/local/`。许可证 **AGPL-3.0**。

![架构总览](assets/architecture-overview.svg)

## 文档放在哪

| 方式 | 说明 |
|------|------|
| **控制面 `/help/`** | 跟产品一起走。主控执行 `gpu-control docs build` 后，UI 里打开即可 |
| **仓库 `docs/`** | 源码即文档，GitHub 上直接阅读 Markdown |
| **GitHub Pages** | 只托管**宣传展板**（`demo/`），不是这本手册 |

不要把手册和宣传站塞进同一个 Pages 根路径；手册以控制面为准。

```bash
uv pip install -r requirements-docs.txt
gpu-control docs build    # → web/static/help/
gpu-control docs serve    # 本机预览
```

## 按场景读

### 刚装上

1. [部署总览](部署总览.md) — 拓扑、端口、验证
2. [上手手册](手册.md) — 节点、Job、腾空、Gateway
3. [配置参考](配置参考.md) — `cluster` / `nodes` / jobs

### 要对外服务

- [分布式与推理](分布式与推理.md)
- [高可用与负载均衡](高可用与负载均衡.md)
- [入口分层与 nginx](入口分层与nginx.md)
- [远程接入](远程接入.md)

### 生产与排障

- [生产部署](生产部署.md)
- [安全加固](安全加固.md)
- [接入拓扑与鉴权](接入拓扑与鉴权.md) — 主控/被控、SSH/隧道/Tailscale、OIDC SSO、插件与发布形态
- [升级与备份](升级与备份.md)
- [故障排查](故障排查.md)

### 接 Agent / IDE

- [开始使用](开始使用.md) — 侧栏 / 工作台 / 完整看板白话说明
- [AI / MCP 接入](ai-接入.md)
- [VS Code 插件](vscode插件.md)

### 接口与开发

- [HTTP API](api/index.md) · [OpenAPI](api/openapi.md)
- [容器部署](develop/docker.md)
- [仓库布局](develop/layout.md) · [CI](develop/ci.md)

## 控制面常用入口

| 地址 | 作用 |
|------|------|
| `:8787/` | Web UI |
| `:8787/help/` | 本手册 |
| `:8787/api` | 能力目录 |
| `:8787/api/docs` | Swagger 试调 |
| `:8787/api/openapi.json` | OpenAPI |

版本见仓库根 `VERSION`，变更见 `CHANGELOG.md`。
