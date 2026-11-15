# Changelog

本文件记录 [gpu-control](README.md) 的用户可见变更。

格式依据 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

分类：`Added` · `Changed` · `Deprecated` · `Removed` · `Fixed` · `Security`。

---

## [Unreleased]

### Added

- 开源治理文件：`CODE_OF_CONDUCT` · `AUTHORS` · `NOTICE` · `SUPPORT` · `GOVERNANCE` · Issue/PR 模板
- 产品名 **ComputeDeck**，命令行 `gpu-control`
- 文档配图：`docs/assets/` 架构总览 / Edge / 分层 / 分布式注入
- 产品 logo：`docs/assets/logo.svg`（README / MkDocs / UI favicon 与侧栏）
- Job 推荐包：`gpu-control jobs install --recommended`、`POST /api/jobs/install-template`（`recommended=true`）、任务页一键安装；模板带分类与推荐标记
- Job 最小契约校验（`validate_job_dict` / doctor / diagnose）；队列与租约状态机说明；state flock 契约文档
- `diagnose` 租约漂移检测；`deploy --force` 覆盖异主租约

### Changed

- 许可证由 MIT 改为 **AGPL-3.0-only**（网络提供修改版须提供源码）
- README / 用户文档定稿：自然表述、架构图；不做无产品依据的伪性能对标
- 公开仓库地址写入 README / SUPPORT / `pyproject.toml` / MkDocs（`AlexanderJ-Carter/gpu-control`）
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
