# VS Code 插件（ComputeDeck）

插件适合「边写代码边看集群、顺手开终端 / 部署」：侧栏做导航，**工作台**与**控制面页面**都在 IDE 窗口内打开。  
主控 / 隧道 / OIDC / SSO 总图见 [接入拓扑与鉴权](接入拓扑与鉴权.md)。

源码：[extensions/vscode/](https://github.com/AlexanderJ-Carter/ComputeDeck/tree/main/extensions/vscode)

当前扩展版本见 `extensions/vscode/package.json`；发版时与仓库根 `VERSION` 对齐（见 [打包与发布](打包与发布.md)）。分发形态为 **VSIX**，不发 npm。

## 1. 功能

- **工作台**（编辑器旁 Webview）：集群 KPI、小助手 / MCP 状态、监管公告摘要、常用操作；一键打开各控制面页面
- **侧栏集群树**（保持精简）：
  - 打开工作台
  - 集群总览（默认折叠）、节点、Job、队列与租约、入口
  - 节点 / Job 行内：终端、诊断、启停、腾空
- **控制面页面窗口**：看板、节点、任务、入口、模型、工具栈、批量命令、交互终端、配置、小助手、监管公告  
  默认在 IDE 内打开（Simple Browser 或编辑器嵌入），**不会自动跳系统浏览器**
- 状态栏：`CD 4/4 节点 · 12/16 空闲`；点击打开工作台
- 一键诊断：自检、节点探活、测试 SSH；自动注册 MCP Server（`gc_*`）

## 2. 上手引导（Walkthrough）

命令面板 → `ComputeDeck: 打开上手引导`，或侧栏标题栏书本图标。

五步：连接控制面 → 侧栏与工作台 → 节点终端与诊断 → 部署与启停 Job → MCP 与控制面窗口。  
媒体在 `extensions/vscode/media/walkthrough/`。

## 3. 安装

```bash
cd extensions/vscode && npm run package
code --install-extension computedeck-vscode-*.vsix
# 然后 Developer: Reload Window
```

开发模式：`code --extensionDevelopmentPath="$PWD"`（在 `extensions/vscode`）。

连真实控制面预览：

```bash
./scripts/preview_extension.sh
# 或 CONTROL_HOST=<主控> ./scripts/preview_extension.sh --api http://127.0.0.1:8787
```

CI：`.github/workflows/vscode.yml` 在 `extensions/vscode/**` 变更时打包 VSIX；打 `v*` tag 时附加到 Release。

## 3.1 图标与本地化

- 市场图标：`media/icon.png`；活动栏：`media/activitybar.svg`
- 贡献点：`package.nls.json`（默认中文）+ `package.nls.zh-cn.json` + `package.nls.en.json`
- 运行时英文：`l10n/bundle.l10n.en.json`

## 4. 连接与设置

| 设置 | 默认 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 控制面地址；Remote SSH 时通常为本机 |
| `computedeck.uiOpenMode` | `editor` | `editor` / `auto` / `simpleBrowser` / `external`。默认编辑器窗口，不自动外跳 |
| `computedeck.autoLocalAuth` | `true` | 本机控制面仓库自动读 `ui_api_token` |
| `computedeck.controlRoot` | 空 | 手动指定控制面仓库路径 |
| `computedeck.pollIntervalSec` | `30` | 刷新间隔 |
| `computedeck.statusBar` | `true` | 状态栏开关 |
| `computedeck.mcpPath` | `${workspaceFolder}/bin/gpu-control-mcp` | MCP 可执行文件 |
| `computedeck.sshUser` | 空 | 节点终端默认 SSH 用户 |

鉴权优先级：本机 `config/local/secrets/ui_api_token` → 环境变量 `GPU_CONTROL_API_TOKEN` → 命令「设置 API Token」/「密码登录」。

相关命令：

- `打开工作台` · `打开看板` · `打开控制面页面` · `打开小助手` · `打开监管公告`
- `在系统浏览器打开看板`（显式外开，默认流程不会走到这里）

## 5. MCP / Copilot

插件注册 `computedeck.mcp`，启动 `bin/gpu-control-mcp`，并注入：

```text
GPU_CONTROL_API_BASE=<computedeck.apiBase>
GPU_CONTROL_API_TOKEN=<本机或 SecretStorage 中的 token>
```

## 6. 与 Web 的分工

| 场景 | 推荐 |
|---|---|
| 写代码时看节点 / Job、开终端、跑诊断、点开看板页 | VS Code 插件（窗口内） |
| 大屏投屏、系统浏览器书签 | 显式「在系统浏览器打开」或 `uiOpenMode=external` |
| Agent 自动操作 | MCP（插件可自动注册） |

插件不重复实现整套 Web UI，而是把同一控制面嵌进 IDE 窗口；不硬编码节点 / Job 名，数据来自控制面 API。
