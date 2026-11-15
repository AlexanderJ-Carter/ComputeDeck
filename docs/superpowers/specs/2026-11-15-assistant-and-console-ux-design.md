# ComputeDeck 智能助手与控制台体验设计

日期：2026-11-15  
状态：已定方向，分阶段落地

## 目标

让人在 Web 控制面、以及 Cursor / Claude Code / Codex 等任意 stdio MCP Agent，都能：

1. **查状态**：overview / doctor / leases / queue / gateway  
2. **做简单操作**：部署、启停 Job、排队、腾空（受白名单约束）  
3. **懂系统**：产品语义、配置位置、工作流、禁区  
4. **用得更快**：远程命令智能补全、快捷动作、命令面板等辅助能力  

产品名 **ComputeDeck**；CLI / MCP / 服务名仍为 `gpu-control`。

## 非目标（本阶段不做）

- 不内嵌完整 Agent 编排框架（LangGraph 等）  
- 不把 GPU worker 打进控制面容器  
- 不在未配置上游模型时假装「会思考」——助手可降级为「目录 + 快捷动作」  

## 架构（推荐方案）

```
┌─────────────┐  ┌──────────────┐  ┌─────────────────┐
│ Web 小助手   │  │ MCP stdio    │  │ 脚本 / OpenAPI  │
│ UI chat     │  │ 任意 Agent   │  │ curl / SDK      │
└──────┬──────┘  └──────┬───────┘  └────────┬────────┘
       │                │                   │
       ▼                ▼                   ▼
┌──────────────────────────────────────────────────┐
│ 共享：教学 prompt · 工具白名单 · 风险分级         │
│ lib/assistant_policy.py + api_catalog            │
└───────────────────────┬──────────────────────────┘
                        ▼
              现有 HTTP / CLI 能力面
         GET /api · /api/openapi.json · gc_*
```

权威能力面不变；助手与 MCP **不另造业务语义**。

### 工具风险分级

| 级别 | 示例 | UI 助手默认 | MCP |
|------|------|-------------|-----|
| `read` | overview、doctor、jobs、leases、catalog | 自动执行 | 自动 |
| `write` | put_config、deploy、queue submit、job start/stop | 需确认 | 按 Agent 策略；文档要求先读后写 |
| `destructive` | free、procs kill、exec、force deploy | 二次确认 + 展示目标 | 文档强约束；后续可加 `GPU_CONTROL_ALLOW_DESTRUCTIVE` |

## 多 Agent 接入（P0）

同一入口：`bin/gpu-control-mcp`（stdio）。

| 客户端 | 配置示例 |
|--------|----------|
| Cursor | `config/examples/mcp.cursor.json` |
| Claude Code | `config/examples/mcp.claude-code.json` |
| Codex / 通用 | `config/examples/mcp.codex.json` + `mcp.generic.json` |

环境变量统一：

- `GPU_CONTROL_API_BASE` — 走 HTTP（UI 在跑）  
- `GPU_CONTROL_API_TOKEN` — Bearer  
- 皆未设 — 本机 CLI  

教学包：MCP `instructions`、`docs/ai-接入.md`、`AGENTS.md`、Skill 保持同一套工作流与禁区叙述。

## UI 小助手（P2）

- 入口：看板右下角浮动对话  
- API：`POST /api/assistant/chat`（需登录）  
- 上游：OpenAI 兼容 Chat Completions（`cluster.control.assistant`：`base_url` / `model` / `api_key_env`）  
- 工具调用：仅暴露白名单内工具，映射到现有 `/api/*`  
- 无上游时：返回能力目录 + 建议快捷提问（不假装 LLM）  

## 控制台智能补全（P1，与助手并行）

范围：**运维 → 远程命令** 的 `#exec-custom`（以及后续可扩到 Python 脚本路径）。

| 能力 | 说明 |
|------|------|
| 静态建议 | 现有 `EXEC_PRESETS` + 常用诊断命令库 |
| 上下文建议 | 已安装 Job 名、节点名、`defaults.paths` 下常见路径 |
| 历史 | 本机会话 `localStorage` 最近命令（可清） |
| UI | 输入时下拉；↑↓ 选择；Tab 补全；Enter 仍执行 |

后端：`GET /api/exec/suggest?q=` 返回 `{items:[{command,label,source}]}`，纯读。

交互终端（xterm PTY）**不做** shell 补全代理（复杂度高、安全面大）；PTY 侧仅可加「常用命令芯片」粘贴。

## 其他辅助能力（按价值插入）

| 优先级 | 能力 | 说明 |
|--------|------|------|
| P1 | 命令面板 `⌘/Ctrl+K` | 跳转视图、触发 overview/doctor/deploy 草稿 |
| P1 | 操作确认条 | destructive 前展示节点 / Job / 租约摘要 |
| P2 | 助手快捷提问 | 「集群是否健康」「如何腾空 gpu-1」 |
| P2 | 结果可追溯 | 助手动作在控制台留下结构化回执 |
| P3 | 审计日志 | `state/assistant-audit.jsonl`（可选） |

## 分阶段

1. **P0** 多 Agent MCP 示例 + 教学包对齐 + `assistant_policy` 风险表  
2. **P1** `GET /api/exec/suggest` + 远程命令补全 UI；命令面板骨架  
3. **P2** `/api/assistant/chat` + UI 对话；接 OpenAI 兼容上游  
4. **P3** 审计、确认流打磨、PTY 命令芯片  

## 验收

- Claude Code / Codex / Cursor 任选其一，用同一 MCP 能 `gc_overview`  
- 窄屏展板标题完整（并行 UI 债，不阻塞助手）  
- `#exec-custom` 输入 `nvid` 能提示 `nvidia-smi` 类命令  
- 未配置 LLM 时助手仍给出可点快捷动作，不报错装死  

## 决策记录

- 选型：共用工具核，不绑死单一 IDE  
- 模型：可配置 OpenAI 兼容，可走集群自有推理入口  
- 破坏性操作：UI 默认确认；MCP 靠教学 + 后续环境开关  
