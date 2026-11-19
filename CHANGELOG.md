# Changelog

本文件记录 [gpu-control](README.md) 的用户可见变更。

格式依据 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

分类：`Added` · `Changed` · `Deprecated` · `Removed` · `Fixed` · `Security`。

---

## [Unreleased]

---

## [1.3.3] - 2026-11-19

### Added

- `gpu-control plan-job`：多机作业规划（2×4 卡 = 8 卡），输出 rank / MASTER_ADDR / NPROC_PER_NODE / hostfile 和卡数不匹配告警
- 就绪检查新增节点连接、多机 Job、TLS / nginx 状态步骤，支持和「立即探活」

---
## [1.3.2] - 2026-11-19

### Added

- `gpu-control plan`：把多台小卡机规划成一个多机作业，输出 rank / MASTER_ADDR / NPROC_PER_NODE / hostfile 和告警
- 就绪检查增加节点连接、多机 Job、TLS / nginx 步骤；支持「立即探活」后刷新状态

---
## [1.3.1] - 2026-11-19

### Changed

- 插件 Marketplace 元数据、README 截图与发布工作流（Open VSX / Marketplace OIDC）
- 新增配置指南、发布与账号说明；systemd 单元模板

---
## [1.3.0] - 2026-11-19

### Added

- 运行审计条目含 `actor` / `request_id`；助手 execute / reject / AI 解读写入 runlog
- `GET /api/assistant/insights` 回看最近 AI 解读；看板监管页展示历史列表
- 清空 runlog 需 `confirm=true`，并留下「谁清空了」审计行
- 就绪检查增加 `/help`、MCP 入口、VS Code 说明、MCP 示例配置项
- OIDC 支持 Groups 白名单与粘贴写入 Client Secret；配置页密钥就绪总览
- 工具栈：节点选择、捆绑/单包安装、覆盖 KPI
- 扩展手动发布工作流 `.github/workflows/vscode-publish.yml`（Open VSX）；文档补充 VSIX / Open VSX / Marketplace / npm / PyPI 的渠道说明
- `gateway nginx --apply`：渲染后备份、`nginx -t`、reload，校验失败回滚；`--watch` 跟随节点与健康变化；`--reload-only` 供多台 nginx 共用配置时跟随文件
- `gateway.nginx.*` 配置：`apply_path` / `test_cmd` / `reload_cmd` / `sync_interval_sec` / `backup`
- `gpu-control tls init|show|check`：生成标准 RSA 3072 自签证书、查看到期时间、30 天内到期提醒
- `gpu-control auth pair`：生成 5 分钟一次性配对链接，远程设备打开即换 Session
- `doctor` 增加 TLS 就绪/到期、nginx 配置漂移、自动跟随进程、API Token 缺失等检查
- `package_all.sh` 产出 `manifest.json` 并自动跑 `verify_release.py`；VSIX 按扩展自身版本命名



- 文档 [接入拓扑与鉴权](docs/接入拓扑与鉴权.md)：主控/被控、访问路径、鉴权矩阵、OIDC SSO 方向、VSIX 非 npm 交付约定

### Fixed

- Web 在 VS Code / CodeLab `/proxy/<port>/` 下打开时 CSS/API 丢失：静态资源改相对路径，并识别代理前缀拼 API/WS

### Changed

- VS Code 插件 `1.2.12`：侧栏「AI 与 MCP」、公告/助手深链、`viewsWelcome`、状态配色；本机 Token 自动登录
- Web：`#/assistant` / `#/notices` 深链；远程接入页场景卡 / 状态芯片 / 计划区；鉴权页 SSO 说明
- 明确不发 npm/PyPI；扩展以 VSIX 为准，发版四文件版本对齐
- 底部输出坞闲置隐藏；有结果才出现，去掉「执行输出」说教文案与活动记录折叠；启动不再预填自检
- 入口总览增加 Edge / LiteLLM / nginx / TLS / 对外 KPI；关键页挂 `/help` 情境链接
- 助手确认卡显示剩余有效期倒计时，高亮当前待决策卡；窄屏 KPI / 输出坞收紧
- 用户文档、插件文案与演示页统一整理：减少口号式对仗和重复句式，统一「看板」「主控」「工作节点」等叫法
- VS Code 扩展 `1.2.22`：怎么用页按钮分「常用 / 连接」、新增复制看板地址；轮询只在连接状态变化时重绘说明页
- 品牌定位扩展为「多节点算力控制面」，换新 logo，网页 / 插件 / 文档 / 演示页同步
- 新增 `docs/产品故事.md`：名字含义、产品边界、宣传短文案；README / 文档首页 / 演示页补入口
- 文档补充 nginx 与 Nginx UI 共存方式、容器部署边界；compose 增加 init / 停止宽限 / 日志轮转
- 助手工具步骤在完成后折叠为一行摘要，可展开明细；不再逐行重复「已执行」
- VS Code「五分钟上手」改为自定义全屏上手指南，移除原生 Walkthrough；插件打开完整看板时用 90 秒一次性票据免密换 Session
- 清理旧版 VSIX 产物、未使用的演示页 SVG 和本地缓存目录
- 生成的 nginx 配置补上 `X-Forwarded-*`、连接复用、请求不落盘缓冲和 next_upstream 次数限制


- 演示页顶栏和终页的「文档」链接原先指向演示页自身，改为仓库 `docs/`
- 流式确认卡先落盘再可点，避免抢点出现 `action_not_found`
- `curl -X GET` / `-o /dev/null` 探活不再误标为危险命令
- 助手执行成功与模型总结解耦；上游 429 不再盖过「已执行」心智

---
## [1.2.7] - 2026-11-18

### Changed

- 交互终端改为左右工作台：左侧节点/主机资源，右侧未连接为空态、连上后才展开 PTY
- 入口总览默认只留健康与启停；密钥 / 模型 / 诊断收入折叠区
- 任务页以「已安装列表」为主，启停/部署收成操作条；队列 / 租约 / 互通分面板
- 看板运维区分区卡片化；分页导航与输出坞视觉对齐；工具栈双栏工作区
- 宣传展板同步五子页看板、助手 FAB、终端工作台叙事
- 批量命令 / Python 表单分区与输出坞说明；模型库元信息卡片化；终端连接态加舞台顶栏；窄屏助手 FAB 图标化

### Fixed

- 小助手会话落盘加锁并按 pending 终态优先合并，避免 chat 覆盖已执行确认卡导致重复执行
- 对话历史改为从尾部裁剪，长对话不再丢掉最近上下文
- 流式中途上游失败仍落盘已生成的确认卡；执行强制校验 `args_hash`；确认卡过期以 `expires_at` 为准
- `gc_models` 只读 runner 按 action 派发；`gc_node add` 取消默认 4 卡，缺 GPU 数直接校验失败
- 前端：新对话中止流式、打开面板等待会话恢复、刷新后 FAB 提示待确认、监管解读可「在助手中跟进」
- 区分 pending 同步与线程渲染，避免 boot 预拉红点后打开面板只剩欢迎语；中止流后补拉确认卡；session 接口清理过期 pending；流式 tool 事件可提前下发确认卡

---

## [1.2.6] - 2026-11-18

### Added

- VS Code 插件新增 5 步上手引导（Walkthrough）：连接控制面 → 集群树 → 节点 → Job → MCP / Web；侧栏、命令面板与未连接状态均可打开
- 新增 `scripts/package_all.sh`：本地一键打包源码包、VSIX、文档站、容器镜像与 `SHA256SUMS`；文档新增《打包与发布》
- Release 工作流附加源码包与 SHA256 校验文件

### Changed

- Web 小助手交互重做：对话式转录、只读工具以步骤形式展示，写操作确认卡内联在对话流中，执行结果就地展开
- 破坏性操作改为卡片内二次点击确认（Esc 可撤回），不再弹两次系统对话框
- 助手输入框改为多行自适应：Enter 发送、Shift+Enter 换行；支持 Ctrl/⌘ + K 唤起
- 配置页改为左侧分页：配置组（基础连接 · 节点清单 · Job 模板 · 巡检与守护 · 鉴权与访问）、检查与接入（就绪检查 · AI / MCP）、参考（文档与 API · 高级配置）；一次只显示一页，分页带就绪状态点，就绪检查可跳回对应配置
- 入口与网关、任务、模型、批量命令统一为左侧分页，一次只显示一个分区；支持 `#/页面/分区` 深链
- 看板改为五个互斥子页（总览 · 入口 · 运维 · 趋势 · 监管），深链 `#/actions/<子页>`；总览只放健康与 GPU 池，入口/运维/趋势/监管各自独立，不再挤在一屏
- 桌面端保留单屏应用外壳：侧栏与顶栏固定；看板子页与其它长页在内容区滚动，禁止趋势图等小版块内嵌滚动与硬压缩
- 配置页新增「登录与鉴权」（密码设置 / 轮换 + OIDC + Token）、「远程接入」（模式 / 暴露端口）与「日志与诊断」（UI / watchdog / Edge / LiteLLM / Cloudflare 日志尾 + 动作审计）；远程接入从入口页移入配置
- 趋势新增节点在线率、队列等待、健康评分采样；曲线补充时间轴，看板「让 AI 解读」可调用 `gc_metrics_history`
- GitHub Pages 展板更新：单屏 / 配置分页 / 趋势语义 / 新 AI 能力
- 节点级 SSH：节点卡片可逐台填写用户 / 端口 / 私钥；「基础连接」改为默认值；`node add` CLI / HTTP / 助手工具支持 `--user --port --key`
- 助手设置新增启用开关与超时；AI / MCP 页展示全部内置工具（31 个，按风险分组）；新增 `gc_logs` / `gc_auth_status` 只读工具，日志页可一键让 AI 分析
- 任务页模板不再自动展开，已安装任务列表优先显示；OAuth / OIDC、LiteLLM 模型与 AI 工具等关键信息默认展开
- 助手确认卡交互细化：执行中显示计时与转圈，完成后收起确认卡并保留执行记录；拒绝后留下轻量说明；同一工具+参数不会重复生成多张待确认卡；对话里的工具步骤会随确认卡同步状态
- 趋势图支持单点采样（先画点，后续成线），并在无数据时区分「无端点」「GPU 快照过期」「等待采样」；顶部补充样本数与最近采样时间
- 看板「入口与状态」合并为 4 条关键信息（API / 入口 / HTTPS·域名 / 守护·工具栈），并单独成页展示
- GitHub Pages 配置中心示意同步为「配置 / 检查与接入 / 参考」三组共 11 页

### Fixed

- 助手设置提示中的 Markdown 星号不再原样显示；未配置上游时预设默认选中「自定义」
- 修复 GPU 刷新在部分节点仍过期时仍返回成功的问题；看板刷新会提示仍过期 / 离线的节点
- 修复生产环境 `inventory.ini` 未随 `nodes.json` 重新渲染，导致 GPU 采集仍指向旧节点、趋势长期缺少 API / GPU 数据的问题
- 部署脚本不再同步本机生成的 `inventory.ini` / `resources.json`，避免覆盖生产清单与 GPU 快照
- 修复看板把状态 / 操作 / 趋势硬塞一屏导致 KPI 截断、图表被压扁，以及趋势区 `max-height: 300px` 内嵌滚动看不见后半段曲线的问题

---
## [1.2.5] - 2026-11-17

### Fixed

- 全站下拉统一增强为自定义选择器：目标节点、模型、队列、配置等所有原生 select 的选中项与选项都能完整换行显示，不再截断
- 鉴权模式选择器的 `none` 选项与配置值对齐

---
## [1.2.4] - 2026-11-17

### Fixed

- 任务选择改为自定义下拉：选中项与每个选项都能多行完整显示，不再依赖原生 select 的截断行为

---
## [1.2.3] - 2026-11-17

### Changed

- 继续精简小字：工具栈页去掉重复说明，批量命令页去掉底部提示行

---
## [1.2.2] - 2026-11-17

### Fixed

- 手机 / 平板导航改为换行，不再横向截断；`8 GPU` 等徽标保持一行
- LiteLLM「模型（简化管理）」折叠标题不再显示永久的“加载中…”

---
## [1.2.1] - 2026-11-17

### Changed

- 页面文案瘦身：移除看板/任务/入口/终端等重复小字，健康摘要改为一行；数据库状态不再显示长连接串
- 配置页路径显示为 `config/local/...`（完整路径放 tooltip）；HF / ModelScope 状态改为「已配 / 未配」

---
## [1.2.0] - 2026-11-17

### Added

- 入口页 LiteLLM「模型（简化管理）」：列出模型与 deployment，添加模型别名 / 删除数据库模型；服务端用 Master Key 调 Admin API，密钥不下发浏览器
- LiteLLM 启动时开启 `STORE_MODEL_IN_DB`，`/model/new` 的别名可持久化
- 配置页鉴权面板：OIDC 允许邮箱 / Claim / Scopes 字段与「测试 OIDC」；API Token 一键生成 / 轮换 / 清除（只显示一次）
- `POST /api/auth/token`、`POST /api/auth/oidc/check` 与 `/api/gateway/litellm/models` API 目录

### Fixed

- 任务下拉改为「名称 · GPU · 来源」短标签，完整模型/说明放到下一行，避免窄屏截断与 `8 GPU` 断行

---
## [1.1.1] - 2026-11-17

### Changed

- `scripts/deploy_control.sh`：同步前自动备份 `config/local` + `state`；支持 `RESTART_GATEWAY=1` / `RESTART_WATCHDOG=1` / `SKIP_BACKUP=1`
- VS Code 插件入口文案同步 LiteLLM 单层
- 文档去掉“只能 / 这不是…的问题 / 不必装”等防御性表述，改用直接的能力描述

---
## [1.1.0] - 2026-11-17

### Changed

- 推理入口收敛为 **LiteLLM 单层**：model_list 直连每个节点端口，重试/冷却/虚拟密钥/预算统一在 LiteLLM；Edge / nginx 只做 18080 的前置反代（`ingress.proxy_to` / `nginx_upstream="litellm"`）
- 部署文档重组：新增《部署总览》，入口分层、生产部署、配置参考同步到 LiteLLM 单层
- 移除旧 hash 路由别名（`#/guide`、`#/nodes`、`#/runtime`、`#/shell`）

### Added

- 入口页与 `gateway status` 显示 `edge_front` / `edge_proxy_to`；Edge 前置模式只报一个上游
- nginx 产物支持 `nginx_upstream="litellm"`（单 upstream → 127.0.0.1:4000）

---
## [1.0.4] - 2026-11-17

### Added

- 自动巡检：健康数据过期时后台自动采集 API / GPU 快照（`cluster.health.auto_refresh`），看板显示自动巡检状态与最近一次采集年龄
- AI 自动巡检：出现离线、探活异常或快照过期时，自动把 overview / notices / diagnose 交给 AI 生成只读建议（`cluster.assistant.auto_insight`，默认 30 分钟节流）
- 任务下拉显示模型与「本地」来源，并带完整说明 tooltip

---
## [1.0.3] - 2026-11-17

### Added

- LiteLLM 管理数据库：新增 `gpu-control gateway litellm-db init|start|stop|status|url|apply-schema`（用户态 Postgres，无需 root）；`gateway.litellm_database_mode="embedded"` 时启动 LiteLLM 会自动拉起数据库，Admin UI 登录与虚拟密钥可用
- `config/examples/litellm-schema-1.86.7.sql`：LiteLLM 1.86.7 的 Postgres schema，供 `litellm-db init` / `apply-schema` 使用

### Fixed

- 按钮与链接尺寸不一致：`a.btn` 约 40px、同排 `button` 为 36px；现在统一为 36px（tiny 28px），primary 投影收敛，避免看起来“更大”；移动端保留 40/34px 触摸目标
- 「填入任务并部署」未参与全局 busy 禁用，长任务期间可被重复点击
- 入口页 LiteLLM 卡片：未接库、内置 Postgres 未运行、LiteLLM 未运行或解释器缺少 prisma 时，Admin 链接会禁用并说明原因
- 任务下拉只显示 `4GPU` / `local`；现在显示模型与「本地」来源，并给下拉加完整说明的 tooltip
- 发布流水线补齐 `timeout-minutes`；SECURITY.md 与 Issue 模板的版本号更新到 1.0.x
- 容器镜像不再把本地 `extensions/` 与历史 VSIX 打进运行时；`COMPUTEDECK_VERSION` 现在会覆盖 UI 显示版本

### Security

- 助手命令风险分级补漏：GNU `sed` 的 `e` 命令 / `s///e`、`curl -T/-b/-H @file/--data-urlencode` 等可外发文件的参数不再被当作只读自动执行
- `/api` 与终端不再接受 URL 里的 `?access_token=` / `?token=`（只接受 Cookie 或 Authorization）；访问日志对 token / key / password 查询参数脱敏
- 助手上游会解析 DNS 并拒绝链路本地 / 云元数据地址（含 IPv4-mapped IPv6），不再只匹配字面主机名
- 密钥文件改为以 0600 原子创建，消除先 0644 再 chmod 的可读窗口

---
## [1.0.2] - 2026-11-17

### Fixed

- 容器镜像 `org.opencontainers.image.version` 被 metadata-action 的默认标签覆盖成 `1.0`；现在显式写入完整版本与 revision

---
## [1.0.1] - 2026-11-17

### Fixed

- 配置页签获得焦点时按 `←` / `→` 会同时切换页签并翻页；现在只切页签

---
## [1.0.0] - 2026-11-17

### Added

- 交互打磨：左侧导航与配置页签支持方向键 / Home / End；全局 `←` `→` 切页、`?` 打开快捷键帮助；统一悬浮、按下、键盘焦点三态；移动端保留主题切换（自动 / 浅色 / 深色）
- 页面组织：入口页拆为「入口总览 / 远程接入 / 上游推理端点」独立面板；队列、交互终端、远程命令、运行 Python 等面板补充一句用途说明
- API 文档页：表格行可 Tab 聚焦、Enter 填入试调；保存 Token 改为内联提示；窄屏表格横向滚动
- LiteLLM 管理数据库：`gateway.litellm_database_url` / `gateway.litellm_database_url_file`；`gateway status` 增加 `litellm.database`，未接数据库时 Admin UI 链接置灰并提示改用 Master Key
- 发布：`.github/workflows/release.yml` 统一打包并附加 VSIX；扩展版本随主版本同步；`docker compose` 默认只发布到 `127.0.0.1`（`UI_BIND` 可改）；CI / Release 增加并发控制与超时

### Fixed

- 模型下载、部署、队列提交、工具栈安装在前端约 2 分钟就报超时；现在与后端长任务一致
- 助手未配置上游时返回 400 导致前端丢掉 `reply` / `open_settings`，现在按正常回复返回
- LiteLLM 未接数据库时打开 Admin 只会看到 `Authentication Error, Not connected to DB!`；现在入口页与文档明确说明「Admin 需要数据库，API 用 Master Key」
- 健康卡的 SSH 信号补上数据年龄；状态接口新鲜度按 60 秒分桶，减少无意义刷新

### Security

- 静态页 CSP 外置 `theme-boot.js` / `api-docs.js`，不再依赖内联脚本
- 跨站写请求与终端 WebSocket 拒绝；终端不再接受 URL 中的长期 token
- 上游请求不跟随 3xx、拒绝云元数据地址；自定义上游未显式填 Key 时不再回退保存的密钥
- 非回环监听且未启用鉴权时拒绝启动（容器镜像显式允许，宿主机部署仍受保护）

---
## [1.0.0-beta.14] - 2026-11-17

### Added

- VS Code 插件原型 `extensions/vscode/`：集群树、状态栏、节点/Job 操作、SSH 终端、只读诊断、入口分层、自动注册 MCP、打开 Web 控制面
- VS Code 插件打包流水线 `.github/workflows/vscode.yml`：扩展变更时打包 VSIX，tag 时附加到 GitHub Release
- `scripts/package_extension.sh` 本地打包脚本；`scripts/check.sh` 增加扩展语法检查
- 新文档《VS Code 插件》


---
## [1.0.0-beta.13] - 2026-11-17

### Added

- 统一入口分层：Edge 作为唯一节点 LB；`gateway.litellm_upstream=edge` 让 LiteLLM 只生成一个指向 Edge 的 deployment，不再重复选节点
- `gateway.nginx_upstream=edge`：nginx 只做 Web/TLS 前端，单 upstream 指向 Edge；`nodes` 仍作为 Edge 的同层替代
- 入口页显示分层说明与重复 LB 告警；`gateway status` 返回 `layering` 与 LiteLLM 实际上游
- 启动 LiteLLM 时从 `cluster.api.api_key_file` 注入上游推理密钥环境变量
- 新文档《入口分层与 nginx》：六种拓扑、nginx 四种用法、性能与 TLS 规则


---
## [1.0.0-beta.12] - 2026-11-17

### Fixed

- 健康卡把 API 探测的新鲜度误用 SSH 时间戳判断，导致“8/8 过期”且没有年龄；现在 SSH / API / 互通分别显示“正常/总数 · 数据年龄”
- 健康卡「刷新评分」改为「全部复检」：真正重新采集存活、SSH、API、互通与主机指标后再重算


---
## [1.0.0-beta.11] - 2026-11-17

### Fixed

- watchdog 不再把 `unreachable=0` 误判为节点不可达；只重启 PLAY RECAP 中 `failed>0` 的主机，避免一个节点故障时重启全部节点

---
## [1.0.0-beta.10] - 2026-11-17

### Fixed

- 服务重启后残留的 `executing` 动作超过 5 分钟可重新领取
- 助手确认卡执行中显示 busy 状态并禁用按钮；双击 / 重复确认只允许一个请求真正执行
- 节点卡片名称/地址/GPU/SSH 端口一行四列，面板标题与操作可换行

---
## [1.0.0-beta.9] - 2026-11-17

### Fixed

- 助手动作执行改为幂等 + 原子领取：双击 / 重复确认不会再出现 `action_not_pending — 当前状态：executed`，也不会重复执行腾空等破坏性操作
- 已执行的确认卡会自动移除；正在执行时按钮禁用并提示“正在执行”；执行失败不再把弹窗/卡片留在界面上


---
## [1.0.0-beta.8] - 2026-11-17

### Fixed

- 交互终端仍使用全局 SSH 端口，导致新节点（端口 22）在 UI 里“连接失败”；现在终端、SSH 测试、host metrics、inventory 全部按节点端口/用户/私钥生效
- 节点卡片名称/地址/GPU/SSH 端口改为一行四列，避免端口输入框独占整行；面板标题与操作可换行


---
## [1.0.0-beta.7] - 2026-11-17

### Added

- 节点支持每节点 SSH 覆盖（`ansible_port` / `ansible_user` / `ansible_ssh_private_key_file`）：inventory、host metrics、UI「测试 SSH」与 SSH 片段均按节点生效；节点卡片新增「SSH 端口」字段
- 入口页「高级：拓扑」改为“上游 / 网关”两行结构化展示，客户端片段选择器靠右成组

### Fixed

- 重启后节点换了 SSH 端口（如 22）仍沿用全局 8416 导致连不上；现在可为单节点设置端口
- 入口页高级区长文本与选择器在窄屏/高缩放下布局错位


---
## [1.0.0-beta.6] - 2026-11-17

### Added

- 助手刷新后恢复最近对话与待确认动作（`GET /api/assistant/session`）
- 助手流式回复增加「停止」按钮；Esc 关闭面板；右侧 CLI 提示可点击复制
- 命令风险标签（只读 / 修改 / 危险）显示在确认卡与工具描述中

### Fixed

- 对话后的新快捷推荐按钮点击无响应（快捷动作状态未更新）
- `gc_exec` 工具描述仍写成“一律二次确认”，与命令级风险分级不一致


---
## [1.0.0-beta.5] - 2026-11-17

### Added

- 助手快捷推荐按集群状态生成（离线节点 / 过期快照 / 队列 pending / GPU 负载 / 入口未启动 / 无 Job）
- `gc_exec` 改为命令级风险分级：只读命令自动执行、修改类一次确认、`rm/kill/reboot/python -c` 等危险命令二次确认
- 助手回复约定 3–6 行、一次只发起一个动作

### Fixed

- `exec` 结果只有 `rc=0` 没有 `ok` 字段时被误判为失败，导致助手显示“操作失败：None”；现在按 `rc` 判定并给出具体失败原因


---
## [1.0.0-beta.4] - 2026-11-17

### Added

- `gc_exec_preset`（HTTP `/api/exec` preset / MCP `gc_exec_preset`）：单节点只读诊断预设（nvidia-smi / GPU 利用率 / 进程 / 端口 / 磁盘 / 日志尾），助手自动执行、无需确认；任意命令仍走 `gc_exec` 二次确认
- 趋势曲线增加 0% / 50% / 100% 纵轴刻度与最近值标签

### Fixed

- 助手确认卡不再被压缩到只剩按钮；长命令不再把气泡撑坏；不再向用户展示 action_id 等内部 ID
- 配置页分区导航与折叠高级项；入口与状态不再裁掉最后一行；交互终端对离线节点禁用连接


---
## [1.0.0-beta.3] - 2026-11-17

---
## [1.0.0-beta.2] - 2026-11-17

### Added

- 节点存活与数据新鲜度：`lib/liveness.py` + `gpu-control liveness status|check` + `GET /api/liveness`；离线节点、快照过期、GPU 统计排除旧快照；后台每 60 秒 TCP 探活（`cluster.health.check_interval_sec` 可调，`GPU_CONTROL_LIVENESS_MONITOR=0` 关闭）
- 看板离线 / 探活异常 / 快照过期徽标与顶部新鲜度横幅；「立即探活」按钮
- AI 助手流式回复（SSE：`meta` / `delta` / `tool` / `done` / `error`），自动回落普通请求；回复支持 Markdown（标题、列表、表格、代码块）与代码块一键复制
- GPU 实际负载：总览新增 `gpu_sm_util_pct` / `gpu_mem_util_pct`，看板把「槽位占用」与「实际算力/显存负载」分开展示
- AI 助手真正可执行：工具注册表覆盖部署、腾空、Job 启停、队列/租约、入口、节点、模型、工具栈、诊断、远程接入等；写操作生成确认卡，破坏性操作二次确认；执行结果回喂模型
- `POST /api/assistant/execute`：服务端会话 + 动作校验（节点/Job 存在、目标不离线、参数哈希）+ 复用现有 `/api/*` 执行
- 远程接入：`gpu-control remote status|plan|up|down`、`GET /api/remote`、`POST /api/remote/action`、入口页「远程接入」卡片；支持 Tailscale serve/funnel 与 Cloudflare quick/named tunnel，公网模式默认要求已开鉴权
- UI 信息架构梳理：看板精简为「状态横幅 / 集群状态 / 趋势 / 常用操作 / 需要关注」；队列与租约移到任务页，远程接入移到入口页；导航拆成 看板 / 节点 / 任务 / 入口与网关 / 模型 / 工具栈 / 批量命令 / 交互终端 / 配置
- 每页新增一句话目的说明；任务页把部署方式 / GPU 数 / 指定节点从高级折叠移到独立「部署 / 换机」区
- 统一字号、行高、控件高度、面板内边距；补 1280 / 768 / 1600 断点：平板顶栏导航、手机横向滚动导航、看板单列、助手底部抽屉、表格横向滚动
- 节点详情对离线 / 旧快照做明确标注：状态显示「离线」、进程表降级为旧快照、结束进程 / 腾空按钮禁用，避免把过期数据当实时状态
- MCP 新增 `gc_liveness` · `gc_remote`
- Edge `:18080` 根路径兜底页：浏览器打开不再反代出上游 401 JSON；说明这是内置 Edge（不是 nginx）
- 控制面可生成/保存 LiteLLM Master Key（`config/local/secrets/litellm_master_key`），保存后重启代理供 Admin UI 登录
- LiteLLM 状态字段 `phase` / `listen_ok` / `log_tail` / `binary`；入口与网关卡支持启动 / 停止 / 重启与诊断折叠
- 可选配置 `gateway.litellm_bin`；解析顺序覆盖 `~/.local/bin` 与当前 Python 同目录 CLI
- 跨平台进程工具 `lib/process_util.py`：gateway / diagnose / UI / watchdog 统一 pid 判活与命令行身份校验
- 节点页 KPI 摘要：节点数、GPU 占用、空闲、忙闲、隔离、队列

### Changed

- 「API 端点」收成「入口与网关」一页：Edge（集群反代）与 LiteLLM（OpenAI 层）并排说明，减少重复入口概念
- 侧栏收成 7 项；「模型 + 工具栈」并入「模型与工具」子页签，「批量命令 + 交互终端」并入「终端与命令」子页签
- 执行输出默认收起；AI 解读改为折叠，避免每页被大块日志/文本占满
- 看板趋势指标语义：拆成 **GPU 算力 / 显存（nvidia-smi 实测）** 与 **槽位占用（occupied 占比）**；只保留最近约 1 小时短窗，不再堆长期历史
- LiteLLM 启动优先直接 exec 已解析二进制 + runtime yaml，不再因瘦 PATH 掉进外部 `start_litellm.sh` 隐式 pip
- `scripts/deploy_control.sh` 同步已构建的 `web/static/help`，避免无 MkDocs 的控制机文档站长期停留在旧版本

### Fixed

- **离线节点不再伪装成占用/空闲**：`/api/overview` 的 GPU 统计只计入“可达且快照新鲜”的节点；离线 / 快照过期节点单独标记，质量分不再把过期探测当健康
- `gpu refresh` / `probe` 在部分节点不可达时仍解析可用结果，并写 `state/resources_health.json`、为探针文件加时间戳，避免一个死节点冻结全集群快照
- watchdog 跳过离线 / 未知节点，不再对不可达主机反复 restart；日志改为单一落点并加 flock 单实例，消除重复行
- 调度（`select_nodes`）排除离线 / 探活异常 / 快照过期节点，避免把任务部署到已掉线的机器
- 助手把 `gc_queue list` / `gc_lease list` / `gc_gateway status` 误判为写操作、生成不可执行的确认卡；现按调用级风险自动执行
- 助手工具历史被截断导致多轮确认失败；现保留 `assistant.tool_calls` 与 `role:"tool"` 配对
- 端口监听者身份校验：SSH 隧道 / 陌生进程不再被当成 Edge / LiteLLM；返回 `port_conflict` / `unmanaged` 并提示占用者
- Edge 运行判定改为 pidfile 或 `/_gpu_control/health` 身份校验，`gateway ensure` 不会因端口被占用而误判已启动
- PID 复用误杀：stop / restart 前校验命令行身份，不匹配时只清理 pidfile，不 kill 陌生进程/进程组
- macOS 上 `/proc` 判活失效：`ui --background`、watchdog、gateway stop、diagnose 统一改用 `os.kill(pid, 0)` + `ps`
- LiteLLM 日志尾只读最后 64KB，避免看板轮询整文件读入内存
- 直接打开「入口与网关」时状态一直显示“加载中”，现在切页即刷新
- 文档站中文路径（如 `/help/手册/`）未做 URL 解码，浏览器访问会落到「文档未构建」兜底页；现按解码后的安全路径解析
- 宣传展板终页「快速开始」代码框被 grid 拉伸，顶部出现大块黑色空白；改为按内容高度垂直居中

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
[1.3.3]: #133---2026-11-19
[1.3.2]: #132---2026-11-19
[1.3.1]: #131---2026-11-19
[1.3.0]: #130---2026-11-19
[1.2.6]: #126---2026-11-18
[1.2.5]: #125---2026-11-17
[1.2.4]: #124---2026-11-17
[1.2.3]: #123---2026-11-17
[1.2.2]: #122---2026-11-17
[1.2.1]: #121---2026-11-17
[1.2.0]: #120---2026-11-17
[1.1.1]: #111---2026-11-17
[1.1.0]: #110---2026-11-17
[1.0.4]: #104---2026-11-17
[1.0.3]: #103---2026-11-17
[1.0.2]: #102---2026-11-17
[1.0.1]: #101---2026-11-17
[1.0.0]: #100---2026-11-17
[1.0.0-beta.14]: #100-beta14---2026-11-17
[1.0.0-beta.13]: #100-beta13---2026-11-17
[1.0.0-beta.12]: #100-beta12---2026-11-17
[1.0.0-beta.11]: #100-beta11---2026-11-17
[1.0.0-beta.10]: #100-beta10---2026-11-17
[1.0.0-beta.9]: #100-beta9---2026-11-17
[1.0.0-beta.8]: #100-beta8---2026-11-17
[1.0.0-beta.7]: #100-beta7---2026-11-17
[1.0.0-beta.6]: #100-beta6---2026-11-17
[1.0.0-beta.5]: #100-beta5---2026-11-17
[1.0.0-beta.4]: #100-beta4---2026-11-17
[1.0.0-beta.3]: #100-beta3---2026-11-17
[1.0.0-beta.2]: #100-beta2---2026-11-17
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
