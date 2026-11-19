## 先连上控制面

插件只读控制面的 HTTP API，不在本地保存集群状态。

1. **地址**：默认 `http://127.0.0.1:8787`。用 VS Code / CodeLab Remote SSH 连到主控时保持默认；在本机访问远端主控时，填 SSH 隧道地址。
2. **鉴权**（控制面开了鉴权才需要）：
   - **在 control 机器、工作区是 gpu-control 仓库时**：自动读 `config/local/secrets/ui_api_token`，一般不用粘贴
   - 或点 [使用本机 Token](command:computedeck.useLocalCredentials)
   - 或 [密码登录](command:computedeck.login) / [设置 API Token](command:computedeck.setToken)
3. 点侧栏标题栏刷新，或打开 [工作台](command:computedeck.openWorkbench)，看到 KPI / 节点即接通。

> MCP / Copilot 走 HTTP，需要 **API Token**；密码登录覆盖控制面会话（编辑器窗口内也可登录）。

常用设置：

| 设置 | 默认 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 控制面地址 |
| `computedeck.uiOpenMode` | `editor` | 看板打开方式：默认编辑器窗口，不自动跳系统浏览器 |
| `computedeck.autoLocalAuth` | `true` | 本机控制面仓库自动读 Token |
| `computedeck.pollIntervalSec` | `30` | 刷新间隔（秒） |

[打开 ComputeDeck 设置](command:workbench.action.openSettings?%5B%22computedeck%22%5D)
