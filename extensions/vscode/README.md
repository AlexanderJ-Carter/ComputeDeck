# ComputeDeck for VS Code

在 VS Code 里操作 [ComputeDeck](https://github.com/AlexanderJ-Carter/ComputeDeck) 多节点 GPU 控制面：侧栏做导航，工作台面板承载 KPI / 助手 / 公告 / 常用操作。

| 能力 | 说明 |
|---|---|
| 工作台 | 编辑器旁独立窗口：集群 KPI、小助手/MCP 状态、监管公告、自检/探活/部署等 |
| 集群树 | 节点在线/离线、GPU、Job、队列/租约、入口；总览默认折叠 |
| 状态栏 | `CD 4/4 节点 · 12/16 空闲`；点击打开工作台 |
| 节点 | SSH 终端、`nvidia-smi` / GPU 进程、隔离/恢复、腾空 |
| Job | 部署、启动 / 停止 / 重启 / 状态 / 等待就绪 |
| Web 看板 | Simple Browser（或系统浏览器）打开完整控制面 / 助手 / 公告 |
| MCP | 注册 MCP 供 Copilot 调 `gc_*` |
| 上手引导 | 5 步 Walkthrough：连接 → 集群树 → 节点 → Job → AI/MCP |

完整看板、AI 对话、趋势图仍在 Web 控制面；插件只做高频、低摩擦操作。危险操作（腾空、停止 Job、部署）均有确认。

## 安装

### 从仓库打包 VSIX

```bash
cd extensions/vscode
npm run package
code --install-extension computedeck-vscode-*.vsix
```

需要 Node 18+ 与 `npx`。内网可预先安装 `@vscode/vsce`。

### 开发模式

```bash
cd extensions/vscode
code --extensionDevelopmentPath="$PWD"
```

### 连真实控制面预览

仓库根目录：

```bash
# 本机控制面已在 :8787
./scripts/preview_extension.sh

# 或经 SSH 在远端主控签发 Token，并假定本机已隧道到 :8787
CONTROL_HOST=codelab-xxx ./scripts/preview_extension.sh --api http://127.0.0.1:8787
```

脚本会：探活 `/api/health` → `auth issue-token` → 写入临时 VS Code profile（注入 `apiBase` 与 `GPU_CONTROL_API_TOKEN`）→ 用 Extension Development Host 打开插件。

## 连接控制面

| 设置 | 默认 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 控制面地址；Remote SSH 时通常为本机 |
| `computedeck.pollIntervalSec` | `30` | 状态栏 / 树刷新间隔（秒） |
| `computedeck.statusBar` | `true` | 是否显示状态栏 |
| `computedeck.mcpPath` | `${workspaceFolder}/bin/gpu-control-mcp` | MCP 可执行文件 |
| `computedeck.sshUser` | 空 | 节点终端默认 SSH 用户 |

鉴权（按优先级自动尝试，control 本机通常零操作）：

1. **本机控制面仓库**：工作区是 `gpu-control`（或 `~/gpu-control`）且存在 `config/local/secrets/ui_api_token` 时自动加载（可用「使用本机 Token」手动触发）
2. 环境变量 `GPU_CONTROL_API_TOKEN`
3. 命令面板 → **设置 API Token** / **密码登录**

`computedeck.autoLocalAuth` 默认开启；可用 `computedeck.controlRoot` 指定仓库路径。

## 常用命令

命令面板按分类 **ComputeDeck** 筛选，或点侧栏标题栏图标：

- 刷新 / 打开工作台 / 打开 Web 控制面 / 打开上手引导
- 工作台内：自检 · 探活 · SSH · 诊断 · 部署 · 腾空 · MCP

节点与 Job 树项上有内联按钮。

## MCP / Copilot

扩展向 VS Code 注册 `computedeck.mcp`，启动：

```text
bin/gpu-control-mcp
```

并注入：

```text
GPU_CONTROL_API_BASE=<computedeck.apiBase>
GPU_CONTROL_API_TOKEN=<SecretStorage 中的 token>
```

即可在 Copilot Chat 等客户端使用 `gc_overview`、`gc_liveness`、`gc_deploy`、`gc_exec_preset` 等工具。MCP 走 HTTP 时需要 API Token；仅密码登录可能无法通过 MCP 鉴权。

## 图标与本地化

- **市场图标**：`media/icon.png`（128×128，由品牌 `logo.svg` 导出）
- **活动栏**：`media/activitybar.svg`（单色 `currentColor`，跟随主题明暗）
- **贡献点 i18n**：`package.nls.json`（默认英文）+ `package.nls.zh-cn.json`
- **运行时 i18n**：`l10n/bundle.l10n.zh-cn.json`，界面文案经 `vscode.l10n.t`

VS Code 显示语言为简体中文时自动使用中文命令名、设置说明、树与通知；其他语言回落英文。

## 设计取舍

- 不在扩展里重做 Web UI
- 节点 / Job 数据只来自控制面 API，不硬编码业务名
- 异主租约、破坏性操作遵循控制面策略；插件侧保留确认弹窗
