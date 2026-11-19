# VS Code 插件（ComputeDeck）

**小白请先看：[开始使用](开始使用.md)。**

插件让你在写代码的窗口里看集群、开终端、部署任务。完整大屏网页仍用「打开完整看板」。

源码：`extensions/vscode/`。发版形态是 **VSIX**（不发 npm）。版本见 `extensions/vscode/package.json`。

## 你会用到的几个入口

| 入口 | 干什么 |
|------|--------|
| 左侧 ComputeDeck | 看节点 / 任务 |
| 工作台 | 空闲卡、公告、常用按钮 |
| 怎么用 | 白话说明：怎么连、何时要密码 |
| 打开完整看板 | 打开网页大屏 |
| 五分钟上手 | 分步引导 |

## 安装

```bash
cd extensions/vscode && npm run package
code --install-extension computedeck-vscode-*.vsix
# 然后：Developer: Reload Window
```

预览：`./scripts/preview_extension.sh`

## 设置（一般不用改）

| 设置 | 默认 | 说明 |
|------|------|------|
| `computedeck.apiBase` | `http://127.0.0.1:8787` | 总控地址；在总控上保持默认 |
| `computedeck.autoLocalAuth` | `true` | 自动用本机密钥，少输密码 |
| `computedeck.uiOpenMode` | `auto` | 怎么打开大屏；CodeLab 会先给「怎么用」 |

## 和网页的分工

- **插件**：日常看节点、终端、诊断、部署  
- **网页大屏**：图表、小助手长对话、复杂配置  
- **细节文档**：[接入拓扑与鉴权](接入拓扑与鉴权.md)
