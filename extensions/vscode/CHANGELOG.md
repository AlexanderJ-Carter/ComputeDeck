# Changelog

## 1.2.18

- 用户文案改成白话：「怎么用」「打开完整看板」「五分钟上手」；去掉术语堆砌
- 新增 `docs/开始使用.md`；接入页只保留三步会用 / 三种机器 / 何时要密码

## 1.2.17

- 新增「接入说明」页：主控 / 远程 / 被控、鉴权与免密矩阵；解释 CodeLab iframe 代理失败原因
- 打开看板默认不再盲嵌 `/proxy/8787`；Web IDE 走接入说明 + Simple Browser / 平台打开
- 工作台增加接入速览；`uiOpenMode` 支持 `auto` / `access` / `simpleBrowser` / `embed` / `external`

## 1.2.16

- 工作台 UI 重排：页头状态、四格 KPI、页面分段导航、连接/操作分区、公告列表层次更清晰

## 1.2.15

- Getting Started：优先原生 Walkthrough；CodeLab 等环境回落「上手指南」编辑器窗口（同五步内容）
- 控制面嵌入地址经 `asExternalUri` 转换，修复 CodeLab 里 iframe 直连 `127.0.0.1` 被拒
- 首次激活提示打开上手指南；侧栏节点默认折叠；命令「打开上手指南（窗口）」

## 1.2.14

- 控制面页面默认在编辑器窗口打开（`uiOpenMode=editor`）；不再自动跳系统浏览器
- 工作台增加各页面入口；新增「打开控制面页面」「在系统浏览器打开看板」
- 设置 `computedeck.uiOpenMode`：`editor` / `auto` / `simpleBrowser` / `external`
- README / Walkthrough / `docs/vscode插件.md` 与命令文案对齐「窗口优先」

## 1.2.13

- 侧栏精简：顶部「打开工作台」；KPI / 助手 / 公告 / 常用操作迁到编辑器旁 Webview 工作台面板
- Web 看板优先用 Simple Browser（不可用时回落系统浏览器）
- 状态栏点击打开工作台；标题栏保留刷新 / 工作台 / Web 看板 / 引导

## 1.2.12

- 侧栏新增「AI 与 MCP」：Web 小助手状态、监管公告、MCP 可执行文件/Token
- 节点/总览状态配色与图标；状态栏显示未读公告；未连接时 `viewsWelcome`
- 命令：打开 Web 小助手 / 监管公告（对接控制面 `#/assistant` · `#/notices`）
- 版本策略：日常可记本文件小步版本；打产品 tag 前与仓库根 `VERSION` 对齐（见 docs/打包与发布.md）。分发仅 VSIX，不发 npm。

## 1.2.11

- 本机 `ui_api_token` 优先于 SecretStorage，避免旧 Token 卡住自动登录

## 1.2.10

- 在 control 本机打开 gpu-control 仓库时自动读 `config/local/secrets/ui_api_token`，无需粘贴密码/Token
- 新增命令「使用本机 Token」；设置 `autoLocalAuth` / `controlRoot`

## 1.2.9

- 默认中文（`package.nls.json` + 运行时中文文案；英文见 `package.nls.en.json` / `l10n/bundle.l10n.en.json`）
- 树节点图标改用 `ThemeIcon`，不再把 `$(error)` 当纯文本显示
- Walkthrough：CodeLab 等无 `openWalkthrough` 时回落 markdown 预览

## 1.2.8

- 市场图标 `media/icon.png`；活动栏改用单色 `media/activitybar.svg`（跟随主题）
- 贡献点与运行时 i18n：`package.nls*.json` + `l10n` bundle
- README：安装 / 预览脚本 / 图标与本地化说明

## 1.0.0

- 首个正式版：集群树、状态栏、节点/Job 内联操作、SSH 终端、只读诊断
- 入口分层：Edge 唯一节点 LB、LiteLLM 是否经 Edge、nginx 角色
- 自动注册 `computedeck.mcp`，VS Code / Copilot 可直接使用 `gc_*` 工具
- 命令面板：刷新、打开 Web 控制面、登录 / Token、自检、探活、部署、腾空
