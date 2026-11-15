# Changelog

本文件记录 [gpu-control](README.md) 的用户可见变更。

格式依据 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

分类：`Added` · `Changed` · `Deprecated` · `Removed` · `Fixed` · `Security`。

---

## [Unreleased]

### Added

- Edge `:18080` 根路径兜底页：浏览器打开不再反代出上游 401 JSON；说明这是内置 Edge（不是 nginx）
- 控制面可生成/保存 LiteLLM Master Key（`config/local/secrets/litellm_master_key`），保存后重启代理供 Admin UI 登录

### Changed

- 「API 端点」收成「统一入口」一页：Edge（集群反代）与 LiteLLM（OpenAI 层）并排说明，减少重复入口概念
- 看板趋势指标语义：拆成 **GPU 算力 / 显存（nvidia-smi 实测）** 与 **槽位占用（occupied 占比）**；只保留最近约 1 小时短窗，不再堆长期历史

---

## [1.0.0-beta.1] - 2026-11-15

ComputeDeck **1.0 先导测试版**：控制面 / CLI / MCP / 展板已可日常试用；预发布镜像不覆盖 `latest`。

### Added

- 发版支持先导测试版号（`X.Y.Z-beta.N` / `-rc.N`）：GitHub Release 标 prerelease，镜像不刷 `latest`

### Changed

- 节点矩阵：详情改为居中弹窗（点卡片打开，关闭/Esc/点遮罩收起），矩阵全宽展示

### Fixed

- 看板趋势图：百分比轴固定 0–100 + 参考线，常数序列不再贴底「看不见」
- GitHub Pages：`cancel-in-progress: false`，避免并发取消留下半成品 artifact、rerun 时 `deploy-pages` 因多个 `github-pages` 失败

---
## [0.23.1] - 2026-11-15

### Added

- 看板「连通与负载趋势」：`GET /api/metrics/history` + SVG 曲线（API 连通率 / GPU 占用 / CPU / 健康分）；总览自动采样
- AI 监管面板：总览自动写入规则告警；深度同步 + AI 解读落本地缓存；展板 AI 页增强曲线与动效

### Changed

- 配置「节点清单」自适应：计数、筛选、密度（卡片 / 紧凑行，≥6 台默认紧凑），保存以完整草稿为准不丢隐藏项

---
## [0.23.0] - 2026-11-15

### Added

- UI 小助手设置面板：OpenAI / 自定义兼容上游、`POST /api/assistant/config`、`GET|POST /api/assistant/models` 拉取后下拉选模型；未配置时引导设置
- 监管公告：`GET /api/notices` · `POST /api/notices/mark`（diagnose / watchdog / 健康规则 → `state/notices.json`）；看板铃铛 +「AI 解读」`POST /api/assistant/analyze`
- 配置向导增强：节点卡片表单、Job `kind/runtime/docker/env`、Watchdog 与鉴权可视化分节
- 助手上游：`POST /api/assistant/probe` 连通探测；DNS/超时中文提示；内网引导自定义兼容网关
- 宣传展板：AI 页（助手对话 / 命令补全 / 监管公告）；终页文案+安装分栏并突出 AI

### Fixed

- 跳过 Job 目录 macOS `._*` AppleDouble，避免 UI/助手 UTF-8 解码失败；助手密钥路径按 `Path` 解析
- 助手拉取模型失败时给出控制面 DNS/网络可读错误，避免只显示裸 `urlopen` 异常

---
## [0.22.0] - 2026-11-15

### Added

- 控制面小助手：`POST /api/assistant/chat` · `GET /api/assistant/status`（OpenAI 兼容上游；未配置则讲解+快捷动作）；远程命令补全 `GET /api/exec/suggest`
- 多 Agent MCP 示例：Cursor / Claude Code / Codex（含 TOML）/ Continue / Windsurf / 通用；`lib/assistant_policy.py` 风险分级
- `CLAUDE.md`：Claude Code 入口；`AGENTS.md` 覆盖多客户端与交付形态（源码+uv / GHCR，不发 PyPI/npm）
- 展板 Edge 故障转移 / least-conn 动效；窄屏标题与顶栏/终页布局修复
- MCP 一等工具：`gc_queue` · `gc_lease` · `gc_diagnose` · `gc_gateway` · `gc_node`；`gc_deploy(force/…)` · `gc_models(pull)`；catalog↔MCP 同步测试
- 发版：`scripts/release_notes.py` + tag `v*` 自动 GitHub Release；推送 GHCR 控制面镜像（暂不上传 PyPI）
- 发版便捷：`scripts/prepare_release.py`（bump / 折叠 CHANGELOG / 可选 check）；`.env.example` 配合 compose
- control 同步：`scripts/deploy_control.sh`（tar + 重启 UI，保留 `config/local` / `state`）
- Claude Code 根目录 `.mcp.json.example`（复制为 `.mcp.json` 即可改路径）
- 容器：`Dockerfile`（OCI label）· `docker-compose.yml` · `deploy/ssh/`；CI 构建冒烟；Release 推送附 SBOM/provenance
- `AGENTS.md`：Agent 硬约束与权威契约入口
- 开源治理文件：`CODE_OF_CONDUCT` · `AUTHORS` · `NOTICE` · `SUPPORT` · `GOVERNANCE` · Issue/PR 模板
- 产品名 **ComputeDeck**，命令行 `gpu-control`
- 文档配图：`docs/assets/` 架构总览 / Edge / 分层 / 分布式注入
- 产品 logo：`docs/assets/logo.svg`（README / MkDocs / UI favicon 与侧栏）
- Job 推荐包：`gpu-control jobs install --recommended`、`POST /api/jobs/install-template`（`recommended=true`）、任务页一键安装；模板带分类与推荐标记
- Job 最小契约校验（`validate_job_dict` / doctor / diagnose）；队列与租约状态机说明；state flock 契约文档
- `diagnose` 租约漂移检测；`deploy --force` 覆盖异主租约

### Security

- OIDC 默认 `require_userinfo=true`（禁止仅解码未验签 `id_token`）；pending `0600`
- 密码登录来源 IP 节流（过多失败 429）；响应 CSP / Permissions-Policy；doctor 检查密钥文件权限
- `/api/assistant/policy` 纳入鉴权发现路径（无需 Token 可读工具风险；chat/status 仍需登录）

### Changed

- 许可证由 MIT 改为 **AGPL-3.0-only**（网络提供修改版须提供源码）
- README / 用户文档定稿：自然表述、架构图；不做无产品依据的伪性能对标
- 公开仓库改名为 **ComputeDeck**；宣传展板 `demo/` + GitHub Pages 工作流（动效、自定义域名说明）
- Agent 面：Skill / `docs/ai-接入.md` / MCP instructions / OpenAPI 标题对齐 ComputeDeck；补 queue·lease 工作流与 catalog CLI 摘要
- Agent / 发布文档规整：各客户端 MCP 示例路径与注释统一；明确 Docker/GHCR 为主、不发 PyPI/npm；容器文档补充 MCP 接法
- 展板：Edge 页改成「控制面能力」表述（非独立网关产品）；Agent 页覆盖多客户端；终页补充 Docker/GHCR 与不发 PyPI/npm
- 宣传展板：默认自动播放、更自然文案、流体字阶与响应式；字体异步、配图懒加载、可见页才跑粒子/GPU 动效
- 宣传展板视觉：重做「为什么」对照页；能力轨 / 模型步骤 / 代码窗壳；全页节奏统一
- 宣传展板动效：气泡入场、桥接脉冲、能力轨侧线、健康环计数、背景极光与进度扫光
- 宣传展板：主 CSS 优先加载，系统字体兜底，减轻 Google Fonts 不可达时的「没样式」感
- 公开仓库地址写入 README / SUPPORT / `pyproject.toml` / MkDocs
- UI 品牌展示改为 ComputeDeck + logo（原文字 `gc`）；看板收敛为总览，部署/网关操作下沉到任务/端点页
- Docstring 统一为中文 Sphinx 冒号字段；CLI 报错中文化
- `state_util` 明确仅 POSIX flock；`queue` / `lease release --id` 统一走 `update_json`
- 新增 GitHub Actions CI、`docs/develop/ci.md`、`docs/develop/layout.md`
- 提交习惯改为 Conventional Commits；版本用 tag `vX.Y.Z` 标记
- GPU 数量改读配置 / 实采 / `scheduling.unit_gpus`；清理场景专用默认与文案；`gc_deploy` / `/api/deploy` 与 CLI 对齐
- API catalog 补 `jobs/{name}/logs`；高可用文档增加 strategy 词汇表
- 删除 `legacy_vllm` 别名；训练日志预设对齐 `/tmp/gpu-control-jobs`；`check.sh` 覆盖 `mcp_server`
## [0.21.0] - 2026-11-14

### Added

- 看板单屏聚焦：健康评分、GPU 池、入口/API/Edge/TLS/工具栈状态行与快捷操作
- 设计系统 `tokens.css`；主题三态；Hash 路由；本地 xterm.js；应用内确认弹窗
- Ray 工具栈与 `ray-cluster` 模板；vLLM 进程模板与真实就绪检查
- 通用 `docker-job` / `domain-job` 模板；`gpu-control jobs logs` 与任务页日志
- DeepSpeed hostfile 自动生成；统一 `GPU_CONTROL_JOB_RUNTIME`
- `lib/state_util.py`：原子写、flock、JSON 损坏时备份后重建
- UI：SIGTERM 优雅退出、pid 校验；默认绑定 `127.0.0.1`
- 单元测试（state / endpoints / jobs / runlog / ws）与产品文档（部署/安全/备份/排障/配置/分布式/高可用）
- overview 缓存与 `quality` 字段；smoke 覆盖静态资源与鉴权

### Changed

- AI Gateway 就绪时随 `gateway ensure/start` 自动启动；看板展示配套状态
- watchdog 连续失败阈值后重启，健康后清零；`restart` 保留 Lease
- 配置向导按完整 JSON 合并；文案与示例去场景化
- 长任务不再锁死导航；自动刷新仅渲染当前页

### Fixed

- 端点池与 vLLM 单端口不一致导致的假死端点
- 配置保存丢字段；动态内容 XSS 转义
- 场景产物清理改为通用 glob；提交树去掉外部路径与节点名

### Security

- Job/节点名白名单；静态与 Job 路径 `is_relative_to` 校验；`safeHref` 限制链接协议

## [0.19.0] - 2026-11-14

### Added

- 入口 TLS / 域名 / CA（`ingress.domain` · `ingress.tls.*`）；Edge 可包装 TLS
- OIDC 授权码 + PKCE；`gpu-control auth oidc-check`
- 首页状态面板（Edge / nginx / LiteLLM 就绪）

### Changed

- Edge / nginx / LiteLLM 角色规整；`gateway ensure` 按 ingress 拉起主入口
- Edge 仅在控制面启动；看板状态与配套 ensure 对齐

## [0.18.0] - 2026-11-14

### Added

- MkDocs Material 文档站：`gpu-control docs build|serve`，UI `/help/`
- `/api/docs` Swagger：Bearer 保存、点选试调
- Docstring 规范与 `docs/api/` 索引

## [0.17.0] - 2026-11-14

### Added

- 一站式工具栈：`gpu-control toolkits`（LLaMA-Factory / DeepSpeed / Accelerate / PEFT / vLLM）
- Bundle：`train-stack` · `serve-stack`；HTTP/MCP/UI 工具栈入口
- 训练模板：`train-job` · `llamafactory-train` · `deepspeed-train` · `torchrun-train`
- `examples/train_manager.py` 统一 launcher；DeepSpeed / LLaMA-Factory 示例配置
- 运行日志：`state/runlog.jsonl` · `gpu-control logs` · `GET /api/runlog`
- 一键检测 / 安全修复：`gpu-control diagnose check|repair`
- 首页数据看板：GPU 池、节点、负载/入口、工具栈、SSH 延迟
- OpenAPI 鉴权元数据完善；MCP `gc_auth_status`

### Changed

- 「一键运维」更名为「看板」；原「总览」更名为「节点矩阵」
- 内置 Job 仅保留 `shell-job`；其余改为模板安装
- `/api/gateway/status` 直接读状态文件

### Fixed

- 模型页「拉取后登记」复选框被全局样式拉宽

## [0.14.0] - 2026-11-14

### Added

- Edge LB：`retries` · `cooldown` · `least_conn` · 可选 sticky · 超时
- 远程命令预设扩充（GPU 利用率、API 端口、Docker、主机信息等）
- `GET /api/overview?light=1` 轮询路径

### Changed

- UI 自动刷新 12s，后台页暂停

## [0.13.2] - 2026-11-14

### Fixed

- 去掉竖排塌缩的 CSS 提示气泡；运维说明改为横排
- 弱化背景网格与评分环动画

## [0.13.1] - 2026-11-14

### Added

- 内置 Job：`shell-job` · `http-serve` · `vllm-docker`
- `gpu-control jobs templates|install` 与对应 HTTP API
- UI 从模板添加 Job；部署 GPU 数随任务自适应

### Fixed

- 多 Job 列表与来源标记，避免「只有单一业务 Job」的观感

## [0.13.0] - 2026-11-14

### Added

- 内置 Edge 反向代理：`gpu-control gateway start --kind edge`
- `gateway status|start|stop|nginx|install-litellm` 与 UI 启停
- `cluster.gateway.ingress`；HTTP `/api/gateway/status|start|stop`

## [0.12.0] - 2026-11-14

### Added

- 可插拔鉴权：`none` / `password`
- `gpu-control auth`：`status` · `set-password` · `set-mode` · `issue-token`
- Session Cookie + API Bearer Token；登录门禁；终端 WebSocket 鉴权
- 安全响应头；`doctor` 对未开启鉴权给出 WARN

### Security

- 密码哈希 / session secret / API token 仅存 `config/local/secrets/`

## [0.11.1] - 2026-11-14

### Added

- 网关分层拓扑说明（backends / edge LB / AI Gateway / control）
- LiteLLM `model_list` 片段渲染
- `cluster.gateway`：`model_name` · `litellm_model` · `routing_strategy` 等

### Changed

- nginx 片段标明仅做 HTTP LB，不做 model 路由

## [0.11.0] - 2026-11-14

### Added

- HTTP API 目录与 OpenAPI；MCP stdio（`bin/gpu-control-mcp`）与 Cursor 示例
- AI 接入文档与项目 Skill
- 模型页下载/登记；HF / ModelScope Token 注入
- 端点按节点解析端口（`ports_per_node` / `base_port`）；`lib/endpoints_util.py`

### Changed

- 去场景化：运行时不再绑定特定业务/路径；网关产物中性命名
- UI「推理池」→「API 端点」；`manage-model.yml` 显式废弃
- 模型页改为「下载」主操作 + 「已登记」次操作

### Fixed

- 一键运维批量输出渲染；SSH 探测展示延迟与 detail

## [0.10.5] - 2026-11-14

### Added

- 通用 Job 模板与 `default_job` 解析

### Changed

- Job / CLI / watchdog 不再默认假设单一业务 Job

## [0.10.4] - 2026-11-14

### Added

- `gpu-control procs list|kill`；Web 进程表
- `free` 默认结束占卡进程（`--no-kill` 可关）

### Fixed

- 腾空后残留进程可被明确处理

## [0.10.3] - 2026-11-14

### Added

- `requirements-models-worker.txt` 与 `models deps|install-tools`

### Changed

- 文档合并为 `docs/手册.md`；理清主控 / worker 依赖与模型路径

## [0.10.2] - 2026-11-14

### Fixed

- 顶栏「实时」改为紧凑开关，避免撑破布局

## [0.10.1] - 2026-11-14

### Fixed

- 腾空/隔离：校验 GPU、强杀残留；拒绝调度到 cordon 节点

## [0.10.0] - 2026-11-14

### Added

- 节点路径覆盖与 `${paths.*}`
- 模型目录检查、校验、拉取、启动参数回填
- Docker Job 运行时（`runtime: docker`）

## [0.9.4] - 2026-11-14

### Fixed

- 主控为 CPU control；GPU worker 角色纠正
- inventory 主控组改为 `control_plane`
- 任务页「目标节点」下拉，去掉隐式选中

## [0.9.3] - 2026-11-14

### Changed

- 收紧一键运维首页布局

## [0.9.2] - 2026-11-14

### Changed

- 固定 control 主控与 worker 节点角色

## [0.9.1] - 2026-11-14

### Changed

- 控制台视觉重构为石墨 / 信号青仪表盘

## [0.9.0] - 2026-11-14

### Added

- 初版多节点 GPU 控制面：CLI、Web UI、Ansible/SSH
- Core：节点 / GPU 快照、exec、lease、cordon、deploy、queue
- Workloads：Job JSON 插件启停
- Modules：推理池健康、Gateway/nginx/LiteLLM 片段
- 终端 WebSocket 与 UI 冒烟脚本

---

## 维护约定

1. 发版前：将 `[Unreleased]` 移入新版本节，日期 `YYYY-MM-DD`；同步 `VERSION` 与 `pyproject.toml`。
2. 条目写用户可感知变化；实现细节放 commit message。
3. 分类只用上表六类；无内容的分类整节省略。
4. 版本锚点见下方；具备远程后可改为 compare URL。

[Unreleased]: #unreleased
[1.0.0-beta.1]: #100-beta1---2026-11-15
[0.23.1]: #0231---2026-11-15
[0.23.0]: #0230---2026-11-15
[0.22.0]: #0220---2026-11-15
[0.21.0]: #0210---2026-11-14
[0.19.0]: #0190---2026-11-14
[0.18.0]: #0180---2026-11-14
[0.17.0]: #0170---2026-11-14
[0.14.0]: #0140---2026-11-14
[0.13.2]: #0132---2026-11-14
[0.13.1]: #0131---2026-11-14
[0.13.0]: #0130---2026-11-14
[0.12.0]: #0120---2026-11-14
[0.11.1]: #0111---2026-11-14
[0.11.0]: #0110---2026-11-14
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
