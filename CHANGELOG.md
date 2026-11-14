# Changelog

本文件记录 [gpu-control](README.md) 的用户可见变更。

格式依据 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

分类：`Added` · `Changed` · `Deprecated` · `Removed` · `Fixed` · `Security`。

---

## [Unreleased]

<!-- 下一版本条目写在这里；发版时整段挪到新版本节 -->

---

## [0.18.0] - 2026-11-14

### Added

- MkDocs Material 文档站：`mkdocs.yml` · `gpu-control docs build|serve` · UI `/help/`
- `/api/docs` Swagger 页增强：Bearer 保存、点选接口试调、链到文档站
- Docstring 规范：`docs/develop/docstrings.md`；API 文档索引 `docs/api/`

### Notes

- 构建文档：`uv pip install -r requirements-docs.txt && gpu-control docs build`

---

## [0.17.0] - 2026-11-14

### Added

- 运行日志：`state/runlog.jsonl` · `gpu-control logs` · `GET /api/runlog` · 看板「运行日志」
- 一键检测 / 安全修复：`gpu-control diagnose check|repair` · 看板按钮 · 可自动修 inventory/endpoints/残留 pid
- 看板完善：集群信息 · Edge/LiteLLM/nginx 状态与链接 · 机器占用/Lease · overview 内嵌 `ingress` 快照

### Changed

- 内置 Job 收敛为仅 `shell-job`；`http-serve` / `vllm-docker` / `train-job` 改为模板安装
- 任务页：主操作精简为状态/启动/停止/部署；模板安装区说明「不预装业务 Job」
- `/api/gateway/status` 改为直接读状态文件（不再起 CLI 子进程）

---

## [0.16.0] - 2026-11-14

### Added

- 一站式 AI 工具栈：`gpu-control toolkits list|probe|install`；内置 LLaMA-Factory / DeepSpeed / Accelerate / PEFT / vLLM 等
- Bundle：`train-stack` · `serve-stack`；HTTP `/api/toolkits*` · MCP `gc_toolkits` · UI「工具栈」页
- 首页改为数据看板：GPU 池、节点状态、负载/入口、工具栈就绪、SSH 延迟；运维动作收入折叠区

### Changed

- 导航「一键运维」→「看板」；原「总览」更名为「节点矩阵」

---

## [0.15.0] - 2026-11-14

### Added

- 微调/分布式训练：模板 `train-job` · `llamafactory-train` · `deepspeed-train` · `torchrun-train`（后续版本仅模板、不内置）
- `examples/train_manager.py`：统一 launcher（llamafactory / deepspeed / torchrun / accelerate / raw）
- Job 字段 `kind` · `distributed` · `nproc_per_node` · `master_port`；manage-job 按选中节点注入 `NNODES`/`NODE_RANK`/`MASTER_*`
- DeepSpeed 示例配置 `examples/deepspeed/ds_z{2,3}_config.json`；LLaMA-Factory YAML 骨架
- 远程命令预设：训练进程、NCCL/分布式环境、训练日志尾
- OpenAPI 3 完善：`bearerAuth` / `cookieAuth`、请求体 schema、鉴权元数据；文档页 `GET /api/docs`
- MCP：`gc_auth_status`；工具说明中文化；目录与鉴权/环境变量对齐

### Fixed

- 模型页「拉取后登记」复选框被全局 input 样式拉成全宽输入框

### Notes

- 不内置安装 LLaMA-Factory/DeepSpeed；节点自备环境。控制面负责选机、多机环境与启停

---

## [0.14.0] - 2026-11-14

### Added

- Edge LB 吸收成熟 Gateway 能力：`retries` · `cooldown` · `least_conn` · 可选 sticky header · 连接/读超时
- 远程命令预设扩充：GPU 利用率、API 端口、本机 health、Docker、主机信息等
- `GET /api/overview?light=1`：自动刷新轻量路径（跳过 LB 片段/完整 probe 矩阵）

### Changed

- UI 自动刷新 12s + 后台页暂停；减轻控制面轮询开销

### Notes

- 仍不内建虚拟密钥/预算/Redis（留给 LiteLLM/Kong/Portkey）；我们专注多机发现与 Control 统一入口

---

## [0.13.2] - 2026-11-14

### Fixed

- 去掉会竖排塌缩的 CSS `?` 气泡；运维说明改为正常横排文案
- 弱化背景网格与评分环动画；动作卡片更干净

---

## [0.13.1] - 2026-11-14

### Added

- 框架内置 Job：`jobs/shell-job` · `http-serve` · `vllm-docker`（UI 任务下拉直接可见）
- `gpu-control jobs templates|install`；`GET/POST /api/jobs/templates|install-template`
- UI：从模板添加 Job；配置页可选已有 Job；部署 GPU 数按任务自适应

### Fixed

- 「部署参数 / 任务」不再看起来像只有 Decider：多 Job 列表 + 来源标记（builtin/local）

---

## [0.13.0] - 2026-11-14

### Added

- Control **内置 Edge 反向代理**：`gpu-control gateway start --kind edge`（统一对外端口 → 内网 GPU endpoints）
- `gateway status|start|stop|nginx|install-litellm`；UI「启动/停止统一入口」
- `cluster.gateway.ingress`：`bind` / `port` / `kind` / `public_base_url`
- HTTP：`/api/gateway/status|start|stop`

### Notes

- 无 nginx 时用内置 edge；有 nginx 仍可 `include` 生成的 conf
- LiteLLM 需本机 `litellm` 或 `openai_proxy_dir/start_litellm.sh`
- 推理入口与 UI `:8787` 分离；UI 鉴权不替代推理 API 鉴权

---

## [0.12.0] - 2026-11-14

### Added

- 控制面可插拔鉴权：`none` / `password`（OIDC / WebAuthn 预留）
- `gpu-control auth`：`status` · `set-password` · `set-mode` · `issue-token`
- Session Cookie（HttpOnly / SameSite=Strict）+ API Bearer Token
- 登录门禁 UI；`/api/*` 与终端 WebSocket 统一鉴权
- 安全响应头：`X-Frame-Options` · `nosniff` · `Referrer-Policy` · `CORP`
- `doctor` 对未开启鉴权给出 WARN；`GPU_CONTROL_REQUIRE_AUTH=1` 可升为硬失败

### Notes

- 密码哈希 / session secret / API token 仅存 `config/local/secrets/`
- 推理入口（nginx / LiteLLM）鉴权仍由网关自身负责，与控制面鉴权分离

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
[0.14.0]: #0140---2026-11-14
[0.13.2]: #0132---2026-11-14
[0.13.1]: #0131---2026-11-14
[0.13.0]: #0130---2026-11-14
[0.12.0]: #0120---2026-11-14
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
