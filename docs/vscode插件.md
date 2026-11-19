# VS Code 插件（ComputeDeck）

第一次使用可以先看 [开始使用](开始使用.md)，那一页从界面入口讲起。

插件目标是让日常操作都在编辑器里完成：看集群、看节点、启停任务、开终端、看公告、问助手、改配置。只有图表和助手长对话等复杂页面才需要打开完整网页看板。

源码在 `extensions/vscode/`，以 VSIX 形式发布（不发 npm）。版本见 `extensions/vscode/package.json`。

## 常用入口

| 入口 | 用途 |
|------|------|
| 左侧 ComputeDeck | 节点、任务、排队、入口 |
| 看板 / 工作台 | 健康分、节点、GPU、公告、插件内操作 |
| 怎么用 | 连接方式、免密范围和登录时机 |
| 五分钟上手 | 编辑器内 5 步指南 |
| 打开小助手 | 编辑器内聊天面板，写操作先确认 |
| 配置小助手 API | 填模型地址、密钥、模型名 |
| 打开配置文件 | 直接编辑 `cluster` / `nodes` / `jobs` |
| 排队与占卡 | 提交 Job、调度、取消排队 |
| 入口状态与启停 | 查看 Edge / LiteLLM / nginx，启停入口 |
| 公告 | 原生列表，选中标记已读 |
| 注册给 AI 用的工具 | 给 Copilot、Cursor 等注册 MCP |

编辑器和网页的小助手是两套东西：插件里的小助手是编辑器内聊天；「注册给 AI 用的工具」让外部 Agent 通过 MCP 调 `gc_*`。

## 安装

从 [GitHub Releases](https://github.com/AlexanderJ-Carter/ComputeDeck/releases) 下载 `computedeck-vscode-*.vsix`，或自行打包：

```bash
cd extensions/vscode && npm run package
code --install-extension computedeck-vscode-*.vsix
# 然后执行：Developer: Reload Window
```

预览：`./scripts/preview_extension.sh`

目前插件通过 VSIX 分发；Open VSX / VS Code Marketplace 的发布步骤和账号见 [打包与发布](打包与发布.md#51) 与 [发布与账号](发布与账号.md)。

## 设置（一般不需要改）

| 设置 | 默认 | 说明 |
|------|------|------|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 主控地址；在主控机上保持默认 |
| `computedeck.autoLocalAuth` | `true` | 自动使用本机密钥，减少手动登录 |
| `computedeck.uiOpenMode` | `auto` | 仅影响显式打开完整网页看板的方式 |

## 插件和网页各做什么

- **插件**：看板、节点、任务、终端、诊断、部署、助手、公告、配置、排队、入口
- **网页**：图表、助手长对话、完整配置界面
- **细节文档**：[接入拓扑与鉴权](接入拓扑与鉴权.md) · [配置指南](配置指南.md)
