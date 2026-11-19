# ComputeDeck 文档

**ComputeDeck**（命令仍是 `gpu-control`）管理多台算力节点：谁在用、跑什么任务、对外怎么进。  
Web / CLI / MCP 共用同一套状态；场景配置写在 `config/local/`。许可证 **AGPL-3.0**。

为什么叫 ComputeDeck、它从哪里来、适合谁：[产品故事](产品故事.md)。

![架构总览](assets/architecture-overview.svg)

## 文档在哪

| 方式 | 说明 |
|------|------|
| **控制面 `/help/`** | 跟产品一起发布。主控执行 `gpu-control docs build` 后，在 UI 里打开 |
| **仓库 `docs/`** | 文档源码，GitHub 上可直接读 Markdown |
| **GitHub Pages** | 根路径是 `demo/` 产品演示页，`/docs/` 是手册静态副本 |

手册同时发布到控制面 `/help/` 和 Pages `/docs/`；控制面里的版本跟产品一起走，Pages 版本方便外部直接阅读。

```bash
uv pip install -r requirements-docs.txt
gpu-control docs build    # → web/static/help/
gpu-control docs serve    # 本机预览
```

## 按使用场景

### 第一次安装

1. [配置指南](配置指南.md) — 从空配置到可用的最短路径
2. [部署总览](部署总览.md) — 拓扑、端口、验证
3. [上手手册](手册.md) — 节点、Job、腾空、Gateway
4. [配置参考](配置参考.md) — `cluster` / `nodes` / jobs

### 需要对外提供推理

- [分布式与推理](分布式与推理.md)
- [高可用与负载均衡](高可用与负载均衡.md)
- [入口分层与 nginx](入口分层与nginx.md)
- [远程接入](远程接入.md)

### 生产与排障

- [生产部署](生产部署.md)
- [安全加固](安全加固.md)
- [接入拓扑与鉴权](接入拓扑与鉴权.md) — 主控与被控、SSH / 隧道 / Tailscale、OIDC SSO、插件与发布形态
- [升级与备份](升级与备份.md)
- [故障排查](故障排查.md)

### 接入 Agent 和 IDE

- [开始使用](开始使用.md) — 侧栏、工作台和完整看板的说明
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
| `:8787/api` | 接口目录 |
| `:8787/api/docs` | Swagger 试调 |
| `:8787/api/openapi.json` | OpenAPI |

版本见仓库根 `VERSION`，变更见 `CHANGELOG.md`。
