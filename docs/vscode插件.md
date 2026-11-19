# VS Code 插件（ComputeDeck）

第一次使用可以先看 [开始使用](开始使用.md)，那一页从界面入口讲起。

插件用来在写代码的窗口里查看集群、开终端、部署任务。完整的网页界面仍在「打开完整看板」。

源码在 `extensions/vscode/`，以 VSIX 形式发布（不发 npm）。版本见 `extensions/vscode/package.json`。

## 常用入口

| 入口 | 用途 |
|------|------|
| 左侧 ComputeDeck | 查看节点和任务 |
| 工作台 | 空闲卡、公告、常用按钮 |
| 怎么用 | 连接方式和登录时机的说明 |
| 打开完整看板 | 打开完整网页界面 |
| 五分钟上手 | 分步引导 |
| 打开小助手 | 网页聊天（大屏 `#/assistant`） |
| 配置小助手 API | 在插件里填模型地址 / 密钥 / 模型名 |
| 注册给 AI 用的工具 | 给 Copilot 等注册 MCP |

网页小助手和给外部 Agent 用的 MCP 工具是两件事：前者在网页里对话，后者让 Copilot、Cursor 等直接调用 `gc_*`。

## 安装

```bash
cd extensions/vscode && npm run package
code --install-extension computedeck-vscode-*.vsix
# 然后执行：Developer: Reload Window
```

预览：`./scripts/preview_extension.sh`

## 设置（一般不需要改）

| 设置 | 默认 | 说明 |
|------|------|------|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 主控地址；在主控机上保持默认 |
| `computedeck.autoLocalAuth` | `true` | 自动使用本机密钥，减少手动登录 |
| `computedeck.uiOpenMode` | `auto` | 打开完整看板的方式；CodeLab 会先显示「怎么用」 |

## 插件和网页各做什么

- **插件**：日常查看节点、终端、诊断、部署
- **网页**：图表、助手对话、复杂配置
- **细节文档**：[接入拓扑与鉴权](接入拓扑与鉴权.md)
