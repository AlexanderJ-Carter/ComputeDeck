# VS Code 插件（ComputeDeck）

Web 看板适合大屏与完整操作；VS Code 插件适合“边写代码边看集群、顺手开终端/部署”的日常流。

源码：[extensions/vscode/](https://github.com/AlexanderJ-Carter/ComputeDeck/tree/main/extensions/vscode)

## 1. 功能

- 侧栏 `ComputeDeck` 集群树：
  - 集群总览：健康分、在线/离线、GPU 空闲/占用、排队、租约、入口分层
  - 节点：状态、GPU 空闲/占用、最后探活；内联“终端 / 只读诊断 / 腾空”
  - Job：启动 / 停止 / 重启 / 状态 / 等待就绪
  - 队列与租约
  - 入口：Edge、LiteLLM 上游、nginx、重复 LB 告警
- 状态栏：`CD 4/4 节点 · 12/16 空闲`；离线时变红
- 节点操作：
  - 打开集成 SSH 终端（读取节点 `ansible_port/user/key`）
  - 运行 `nvidia-smi` / GPU 进程 / 只读诊断预设
  - 隔离 / 取消隔离 / 腾空（带确认）
- 一键诊断：自检、节点探活、测试 SSH
- 自动注册 MCP Server：VS Code / Copilot 可直接使用 `gc_*` 工具
- 打开 Web 控制面：完整看板、AI 助手、趋势仍在浏览器

## 2. 安装

开发模式：

```bash
cd extensions/vscode
code --extensionDevelopmentPath="$PWD"
```

打包安装：

```bash
./scripts/package_extension.sh
code --install-extension extensions/vscode/computedeck-vscode-1.0.0.vsix
```

CI：`.github/workflows/vscode.yml` 会在 `extensions/vscode/**` 变更时打包 VSIX 并上传 artifact；打 `v*` tag 时由 `.github/workflows/release.yml` 重新打包并把 VSIX 附加到对应 GitHub Release（扩展版本与主版本同步）。

## 3. 连接与鉴权

设置（VS Code Settings）：

| 设置 | 默认 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 控制面地址；Remote SSH 时通常就是本机 |
| `computedeck.pollIntervalSec` | `30` | 刷新间隔 |
| `computedeck.statusBar` | `true` | 状态栏开关 |
| `computedeck.mcpPath` | `${workspaceFolder}/bin/gpu-control-mcp` | MCP 可执行文件 |
| `computedeck.sshUser` | 空 | 节点终端默认 SSH 用户 |

鉴权：

- 命令面板 → `ComputeDeck: 密码登录`（Session Cookie 存 SecretStorage）
- 或 `ComputeDeck: 设置 API Token`（`gpu-control auth issue-token`）

MCP 走 HTTP 建议用 API Token；只做密码登录时 MCP 可能无法通过鉴权。

## 4. MCP / Copilot

插件注册 `computedeck.mcp` provider，启动 `bin/gpu-control-mcp`，并注入：

```text
GPU_CONTROL_API_BASE=<computedeck.apiBase>
GPU_CONTROL_API_TOKEN=<SecretStorage 中的 token>
```

在支持 MCP 的 Copilot Chat 中即可使用 `gc_overview` · `gc_liveness` · `gc_deploy` · `gc_exec_preset` 等现有工具。

## 5. 与 Web 的分工

| 场景 | 推荐 |
|---|---|
| 看整体、趋势、AI 助手、完整配置 | Web `:8787` |
| 写代码时看节点/Job、开终端、跑诊断 | VS Code 插件 |
| Agent 自动操作 | MCP（插件可自动注册） |
| 外部 TLS / 域名 / 网页 | nginx → Edge，见 [入口分层与 nginx](入口分层与nginx.md) |

插件不重复实现 Web UI，也不在扩展侧硬编码节点/Job 名；所有数据来自控制面 API。
