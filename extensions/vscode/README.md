# ComputeDeck for VS Code

在编辑器里看 GPU 集群、开终端、部署任务。

**不会用？** 装好后点命令 **ComputeDeck: 怎么用**，或看仓库 [`docs/开始使用.md`](../../docs/开始使用.md)。

| 你要点的 | 干什么 |
|---|---|
| 左侧 ComputeDeck | 看节点 / Job |
| 工作台 | 空闲卡、公告、常用按钮 |
| 怎么用 | 白话说明 |
| 打开完整看板 | 网页大屏 |
| 五分钟上手 | 分步点一点就会 |

危险操作（腾空、停任务、部署）会再确认一次。

## 安装

```bash
cd extensions/vscode
npm run package
code --install-extension computedeck-vscode-*.vsix
```

然后 **Developer: Reload Window**。

在总控机打开本仓库时，一般会自动登录，不用粘贴密钥。

## 设置（多数不用改）

| 设置 | 默认 | 说明 |
|---|---|---|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 总控地址 |
| `computedeck.autoLocalAuth` | `true` | 自动本机登录 |
| `computedeck.uiOpenMode` | `auto` | 怎么打开大屏 |

更多：[docs/vscode插件.md](../../docs/vscode插件.md)
