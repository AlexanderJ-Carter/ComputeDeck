# Changelog

本文件记录 [gpu-control](README.md) 的用户可见变更。

格式依据 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

分类：`Added` · `Changed` · `Deprecated` · `Removed` · `Fixed` · `Security`。

---

## [Unreleased]

<!-- 下一版本条目写在这里；发版时整段挪到新版本节 -->

---

## [0.11.1] - 2026-11-14

### Added

- 网关分层拓扑（backends / edge LB / AI Gateway / control）
- 生成 LiteLLM `model_list` 片段：`state/gateway/litellm-model-list.yaml`
- `cluster.gateway`：`model_name` · `litellm_model` · `openai_api_base_path` · `routing_strategy`

### Changed

- `GATEWAY.md` 写清「何时用 nginx / LiteLLM / 控制面」；UI 片段增加 LiteLLM 选项
- nginx 片段标明仅做原始 HTTP LB，不做 model 路由

### Notes

- 虚拟密钥、预算、Redis、fallback 组仍由 AI Gateway 产品负责；gpu-control 只渲染对接片段

---

## [0.11.0] - 2026-11-14

### Changed

- **去场景化**：运行时不再绑定 Decider / Mapika / deepseek / sglang / 美团路径
- 网关渲染改为读取 `cluster.api.paths|sample_path|auth_*`；产物改名 `nginx-inference-pool.conf` / `GATEWAY.md`
- 端口回退默认与 examples 对齐（`8000` / `1`）；`deploy/plan/queue --gpus` 接受任意正整数
- UI「推理池」→「API 端点」；placeholder / CLI 帮助 / MCP 安装提示中性化
- `manage-model.yml` 改为显式废弃，引导使用 Job + `manage-job.yml`
- `gateway.openai_proxy_*` 为首选键名（仍兼容旧 `litellm_*`）；探测 playbook 默认端口 `22`
- 手册新增「迁移与可复用」边界表

### Notes

- 现网拓扑、密钥、Decider 路径仍只在 `config/local/*`（gitignore）

---

## [0.10.10] - 2026-11-14

### Changed

- 推理池端点按节点解析端口：`ports_per_node` / `gpus_per_node` / `cluster.api` 默认；支持节点级 `base_port`
- inventory 写入每节点 `base_port` + `ports_per_node`；本地探测跟主机变量，不再全局写死 4 路
- 推理池马赛克改为「一节点一行」编组，端口格子自适应列数

### Added

- `lib/endpoints_util.py`；`state/endpoints.json` 增加 `by_node` 摘要

---

## [0.10.9] - 2026-11-14

### Added

- 模型页「下载新模型」：仓库 ID / 来源 / 节点 / 登记；`POST /api/models/pull` 支持 `name+tool+register`
- HF / ModelScope Token 保存到 `config/local/secrets/`，拉取时自动注入环境变量
- UI 问号提示（`.tip`）与折叠说明（`.fold`），收掉密密麻麻的 muted 小字

### Changed

- 模型页布局改为「下载」主操作 + 「已登记」次操作

---

## [0.10.8] - 2026-11-14

### Fixed

- 批量输出：进入页面即写入 Doctor 摘要，不再一直停在空白占位
- 增加「跑一键连通」与输出状态 meta；清空后仍有可操作提示

### Changed

- 去掉易被当成「黑框里没内容」的说明文案，左右分栏职责写进日志侧

---

## [0.10.7] - 2026-11-14

### Fixed

- 一键运维「批量输出」黑框：始终按步骤渲染 CLI / SSH / doctor 返回，不再只认 `payload.results`
- SSH 探测结果展示 `detail` / 延迟，避免出现全是 `(empty)`

---

## [0.10.6] - 2026-11-14

### Added

- HTTP API 目录：`GET /api`、OpenAPI：`GET /api/openapi.json`（`lib/api_catalog.py`）
- MCP stdio 服务：`bin/gpu-control-mcp`、`mcp_server/`、`requirements-mcp.txt`
- Cursor MCP 示例：`config/examples/mcp.cursor.json`
- AI 接入说明：`docs/ai-接入.md`；项目 Skill：`.cursor/skills/gpu-control/`
- MCP 工具：`gc_api_catalog`、`gc_doctor`、`gc_overview`、`gc_get_jobs`、`gc_job_action`、`gc_get_config`、`gc_put_config`、`gc_models`、`gc_procs`、`gc_free_node`、`gc_deploy`、`gc_exec`、`gc_cli`

### Changed

- README / 手册增加 API · MCP · AI 接入入口说明

---

## [0.10.5] - 2026-11-14

### Changed

- Job 去场景绑定：通用模板与 `default_job` 解析，不再默认假设 decider
- CLI / watchdog 帮助文案去掉硬编码 decider 默认

### Added

- 通用 Job 示例（shell / http-serve 等）与 `jobs` 说明

---

## [0.10.4] - 2026-11-14

### Added

- `gpu-control procs list|kill`：列出 / 结束 GPU 计算进程
- Web 总览：节点进程表、按 PID / 全部结束
- `free` 默认会杀占卡进程（可用 `--no-kill` 关闭）

### Fixed

- 腾空后校验更可靠，残留进程可被明确处理

---

## [0.10.3] - 2026-11-14

### Changed

- 文档合并为单一 `docs/手册.md`；理清主控 / worker 依赖分层
- 模型校验与下载路径说明（HF / ModelScope、`snapshot_download` 回落）

### Added

- `requirements-models-worker.txt` 与 `models deps|install-tools` 流程说明

---

## [0.10.2] - 2026-11-14

### Fixed

- 顶栏「实时」改为紧凑开关，避免原生 checkbox 撑破布局

---

## [0.10.1] - 2026-11-14

### Fixed

- 腾空 / 隔离：校验 GPU、强杀残留进程
- 调度拒绝落到 cordon 节点（除非显式允许）

---

## [0.10.0] - 2026-11-14

### Added

- 节点路径覆盖：`nodes.json` → `defaults.paths` + 节点级覆盖；Job / models 支持 `${paths.*}`
- 模型模块：目录检查、校验、拉取、启动参数回填
- Docker Job 运行时示例（`runtime: docker`）；无 Docker 节点继续用 `process`

---

## [0.9.4] - 2026-11-14

### Fixed

- 纠正主控为 CPU control；GPU worker 恢复为 `deepseek-1/2/4/5`
- inventory 主控组改为 `control_plane`，避免与 host 同名告警
- 任务页用「目标节点」下拉，去掉隐式「选中节点」

---

## [0.9.3] - 2026-11-14

### Changed

- 收紧一键运维首页布局

---

## [0.9.2] - 2026-11-14

### Changed

- 固定 control 主控与 worker 节点角色

---

## [0.9.1] - 2026-11-14

### Changed

- 控制台视觉重构为石墨 / 信号青仪表盘风格

---

## [0.9.0] - 2026-11-14

### Added

- 初版多节点 GPU 控制面：CLI、Web UI、Ansible/SSH
- Core：节点 / GPU 快照、exec、lease、cordon、deploy、queue
- Workloads：Job JSON 插件启停
- Modules：推理池健康、Gateway/nginx/LiteLLM 片段渲染
- 终端 WebSocket（交互 SSH PTY）与 UI 冒烟脚本

---

## 维护约定

1. **发版前**：把 `[Unreleased]` 中条目移到新版本节，日期用 `YYYY-MM-DD`；同步 `VERSION` 与 `pyproject.toml`。
2. **条目粒度**：写用户可感知变化；实现细节留给 commit message。
3. **分类只用上表六类**；没有的分类整节省略。
4. **链接**：下方版本锚点在具备远程 tag 后可改为 compare URL。

[Unreleased]: #unreleased
[0.11.1]: #0111---2026-11-14
[0.11.0]: #0110---2026-11-14
[0.10.10]: #01010---2026-11-14
[0.10.9]: #0109---2026-11-14
[0.10.8]: #0108---2026-11-14
[0.10.7]: #0107---2026-11-14
[0.10.6]: #0106---2026-11-14
[0.10.5]: #0105---2026-11-14
[0.10.4]: #0104---2026-11-14
[0.10.3]: #0103---2026-11-14
[0.10.2]: #0102---2026-11-14
[0.10.1]: #0101---2026-11-14
[0.10.0]: #0100---2026-11-14
[0.9.4]: #094---2026-11-14
[0.9.3]: #093---2026-11-14
[0.9.2]: #092---2026-11-14
[0.9.1]: #091---2026-11-14
[0.9.0]: #090---2026-11-14
