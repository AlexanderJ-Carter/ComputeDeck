# ComputeDeck for VS Code

在 VS Code / CodeLab 里操作 [ComputeDeck](https://github.com/AlexanderJ-Carter/ComputeDeck)：侧栏导航 + 工作台面板；**看板 / 小助手 / 公告等页面默认在编辑器窗口打开**，不自动跳系统浏览器。

| 能力 | 说明 |
|---|---|
| 工作台 | 编辑器旁窗口：KPI、助手/MCP、公告摘要、常用操作、各页面入口 |
| 控制面页面 | 看板 · 节点 · 任务 · 入口 · 模型 · 终端 · 配置 · 助手 · 公告（IDE 内打开） |
| 集群树 | 节点 / Job / 队列 / 入口；行内终端、诊断、启停、腾空 |
| 状态栏 | `CD 4/4 节点 · 12/16 空闲`；点击打开工作台 |
| MCP | 注册后供 Copilot 调 `gc_*` |
| 上手引导 | 5 步 Walkthrough |

危险操作（腾空、停止 Job、部署）均有确认。

## 安装

```bash
cd extensions/vscode
npm run package
code --install-extension computedeck-vscode-*.vsix
```

需要 Node 18+ 与 `npx`。然后 **Developer: Reload Window**。

### 开发模式

```bash
cd extensions/vscode
code --extensionDevelopmentPath="$PWD"
```

### 连真实控制面预览

仓库根目录：

```bash
./scripts/preview_extension.sh
# 或 CONTROL_HOST=codelab-xxx ./scripts/preview_extension.sh --api http://127.0.0.1:8787
```

## 连接控制面

| 设置 | 默认 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 控制面地址；Remote SSH 时通常为本机 |
| `computedeck.uiOpenMode` | `editor` | 打开页面方式：`editor` / `auto` / `simpleBrowser` / `external` |
| `computedeck.pollIntervalSec` | `30` | 状态栏 / 树刷新间隔（秒） |
| `computedeck.statusBar` | `true` | 是否显示状态栏 |
| `computedeck.mcpPath` | `${workspaceFolder}/bin/gpu-control-mcp` | MCP 可执行文件 |
| `computedeck.sshUser` | 空 | 节点终端默认 SSH 用户 |
| `computedeck.autoLocalAuth` | `true` | 本机控制面仓库自动读 Token |
| `computedeck.controlRoot` | 空 | 手动指定控制面仓库路径 |

`uiOpenMode` 说明：

- **editor**（默认）：编辑器嵌入窗口（CodeLab / Remote 最稳）
- **auto**：优先 Simple Browser，否则编辑器窗口；**不会**自动打开系统浏览器
- **simpleBrowser**：强制 Simple Browser
- **external**：始终系统浏览器（仅当你明确需要）

鉴权（按优先级）：

1. 本机 `config/local/secrets/ui_api_token`（control 仓库打开时）
2. 环境变量 `GPU_CONTROL_API_TOKEN`
3. 命令面板 → **设置 API Token** / **密码登录**

## 常用命令

命令面板按 **ComputeDeck** 筛选，或点侧栏标题栏：

- 刷新 / 打开工作台 / 打开看板 / 打开控制面页面 / 上手引导
- 打开小助手 · 打开监管公告
- 在系统浏览器打开看板（显式外开）
- 自检 · 探活 · SSH · 诊断 · 部署 · 腾空 · 注册 MCP

## MCP / Copilot

扩展注册 `computedeck.mcp`，启动 `bin/gpu-control-mcp`，并注入：

```text
GPU_CONTROL_API_BASE=<computedeck.apiBase>
GPU_CONTROL_API_TOKEN=<本机或 SecretStorage 中的 token>
```

## 图标与本地化

- 市场图标 `media/icon.png`；活动栏 `media/activitybar.svg`
- 默认中文：`package.nls.json` / `package.nls.zh-cn.json`；英文：`package.nls.en.json`
- 运行时英文 bundle：`l10n/bundle.l10n.en.json`

更完整的说明见仓库 [`docs/vscode插件.md`](../../docs/vscode插件.md)。
