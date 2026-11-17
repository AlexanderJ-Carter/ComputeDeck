# ComputeDeck for VS Code

在 VS Code 里直接查看和操作 ComputeDeck GPU 控制面：

- 侧栏集群树：节点在线/离线、GPU 空闲/占用、Job、队列/租约、入口分层
- 状态栏：`CD 4/4 节点 · 12/16 空闲`，离线时变红
- 节点操作：打开 SSH 终端、运行 `nvidia-smi` / GPU 进程、隔离/恢复、腾空
- Job 操作：启动 / 停止 / 重启 / 状态 / 等待就绪
- 一键诊断：自检、节点探活、测试 SSH、只读诊断预设
- 入口分层：显示 Edge 唯一的节点 LB、LiteLLM 是否经 Edge、nginx 状态
- 自动注册 MCP Server：VS Code / Copilot 可以直接使用 `gc_*` 工具
- 打开 Web 控制面：完整看板、AI 助手、趋势图仍在浏览器里

## 安装

### 开发模式

```bash
cd extensions/vscode
code --extensionDevelopmentPath="$PWD"
```

### 打包 VSIX

```bash
cd extensions/vscode
npm run package
code --install-extension computedeck-vscode-1.0.0.vsix
```

需要 Node 18+ 和 `npx`。内网可先安装 `@vscode/vsce` 或使用离线打包。

## 连接控制面

在 VS Code 设置里配置：

| 设置 | 默认值 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 控制面地址；Remote SSH 时通常就是本机 |
| `computedeck.pollIntervalSec` | `30` | 状态栏/树刷新间隔 |
| `computedeck.statusBar` | `true` | 是否显示状态栏 |
| `computedeck.mcpPath` | `${workspaceFolder}/bin/gpu-control-mcp` | MCP 可执行文件路径 |
| `computedeck.sshUser` | 空 | 节点终端默认 SSH 用户 |

鉴权二选一：

1. 命令面板 → **ComputeDeck: 密码登录**（保存 Session Cookie 到 VS Code SecretStorage）
2. 命令面板 → **ComputeDeck: 设置 API Token**（`gpu-control auth issue-token` 生成，推荐给 MCP）

## 常用命令

- `ComputeDeck: 刷新集群状态`
- `ComputeDeck: 打开 Web 控制面`
- `ComputeDeck: 运行自检`
- `ComputeDeck: 节点探活`
- `ComputeDeck: 测试 SSH`
- `ComputeDeck: 运行只读诊断`
- `ComputeDeck: 部署 Job`
- `ComputeDeck: 腾空节点`
- `ComputeDeck: 注册 MCP Server`

节点和 Job 树项上也有内联按钮。

## MCP / Copilot

扩展会向 VS Code 注册 `computedeck.mcp` provider，启动：

```text
bin/gpu-control-mcp
```

并注入：

```text
GPU_CONTROL_API_BASE=<computedeck.apiBase>
GPU_CONTROL_API_TOKEN=<SecretStorage 中的 token>
```

在 Copilot Chat / 支持 MCP 的客户端里即可使用现有的 `gc_overview`、`gc_liveness`、`gc_deploy`、`gc_exec_preset` 等工具。

> MCP 走 HTTP 建议使用 API Token；只做密码登录时 MCP 可能无法通过鉴权。

## 设计取舍

- 不在扩展里重复实现 Web UI：完整看板、AI 助手、趋势仍用浏览器
- 扩展只做“高频、低摩擦”的操作：看状态、开终端、跑诊断、启停/部署
- 危险操作（腾空、停止 Job、部署）都保留确认弹窗
- 节点/Job 数据只来自控制面 API，不在扩展侧硬编码业务名
