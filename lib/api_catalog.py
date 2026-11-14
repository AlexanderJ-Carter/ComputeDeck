"""Machine-readable catalog of HTTP APIs and CLI for AI / MCP clients."""

from __future__ import annotations

from paths import VERSION_FILE

API_BASE = "/api"


def _version() -> str:
    if VERSION_FILE.is_file():
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0"
    return "0"


def http_endpoints() -> list[dict]:
    """Flattened list of control-plane HTTP endpoints."""
    return [
        # meta
        {"method": "GET", "path": "/api", "summary": "API 目录（本列表）", "group": "meta"},
        {"method": "GET", "path": "/api/openapi.json", "summary": "OpenAPI 3 草图", "group": "meta"},
        {"method": "GET", "path": "/api/health", "summary": "UI 健康检查", "group": "meta"},
        {"method": "GET", "path": "/api/auth/status", "summary": "鉴权状态（公开）", "group": "auth"},
        {"method": "GET", "path": "/api/auth/me", "summary": "当前登录主体", "group": "auth"},
        {"method": "POST", "path": "/api/auth/login", "summary": "密码登录（Set-Cookie）", "group": "auth"},
        {"method": "POST", "path": "/api/auth/logout", "summary": "退出并清除 Cookie", "group": "auth"},
        {"method": "GET", "path": "/api/doctor", "summary": "配置自检 JSON", "group": "meta"},
        # read
        {"method": "GET", "path": "/api/overview", "summary": "集群总览（可 ?refresh=1）", "group": "read"},
        {"method": "GET", "path": "/api/jobs", "summary": "已注册 Job 列表", "group": "read"},
        {"method": "GET", "path": "/api/models", "summary": "模型/缓存/路径（?node=）", "group": "read"},
        {"method": "GET", "path": "/api/leases", "summary": "占用登记", "group": "read"},
        {"method": "GET", "path": "/api/queue", "summary": "工作负载队列", "group": "read"},
        {"method": "GET", "path": "/api/probe", "summary": "节点互通矩阵缓存", "group": "read"},
        {"method": "GET", "path": "/api/quality", "summary": "SSH/质量评分", "group": "read"},
        {"method": "GET", "path": "/api/metrics", "summary": "主机 CPU/内存指标", "group": "read"},
        {"method": "GET", "path": "/api/endpoints/health", "summary": "推理 API 健康缓存", "group": "read"},
        {"method": "GET", "path": "/api/endpoints/lb", "summary": "负载计划 ?strategy=", "group": "read"},
        {"method": "GET", "path": "/api/gateway", "summary": "网关片段/拓扑", "group": "read"},
        {"method": "GET", "path": "/api/setup", "summary": "配置向导状态", "group": "config"},
        {"method": "GET", "path": "/api/setup/guide", "summary": "上手步骤", "group": "config"},
        {"method": "GET", "path": "/api/config/cluster", "summary": "读取 cluster.json", "group": "config"},
        {"method": "GET", "path": "/api/config/nodes", "summary": "读取 nodes.json", "group": "config"},
        {
            "method": "GET",
            "path": "/api/config/job/{name}",
            "summary": "读取单个 Job JSON",
            "group": "config",
        },
        # write / actions
        {"method": "POST", "path": "/api/config/cluster", "summary": "写入 cluster.json body.data", "group": "config"},
        {"method": "POST", "path": "/api/config/nodes", "summary": "写入 nodes.json body.data", "group": "config"},
        {
            "method": "POST",
            "path": "/api/config/job/{name}",
            "summary": "创建/更新 Job body.data",
            "group": "config",
        },
        {"method": "POST", "path": "/api/setup/apply-ssh", "summary": "写入 SSH 默认并 render", "group": "config"},
        {"method": "POST", "path": "/api/setup/render", "summary": "渲染 inventory", "group": "config"},
        {"method": "POST", "path": "/api/setup/test-ssh", "summary": "测试 SSH 连通", "group": "config"},
        {"method": "POST", "path": "/api/resources/refresh", "summary": "刷新 GPU 快照", "group": "ops"},
        {"method": "POST", "path": "/api/metrics/refresh", "summary": "刷新主机指标", "group": "ops"},
        {
            "method": "POST",
            "path": "/api/jobs/{name}/action",
            "summary": "Job 动作 body.action=start|stop|… node/nodes/extra_args",
            "group": "ops",
        },
        {"method": "POST", "path": "/api/deploy", "summary": "一键部署 body.job/gpus/nodes", "group": "ops"},
        {"method": "POST", "path": "/api/free", "summary": "腾空节点 body.node[,cordon,no_kill,wait]", "group": "ops"},
        {"method": "POST", "path": "/api/procs", "summary": "列出 GPU 进程 body.node[,refresh]", "group": "ops"},
        {
            "method": "POST",
            "path": "/api/procs/kill",
            "summary": "杀进程 body.node + all|pids|match",
            "group": "ops",
        },
        {"method": "POST", "path": "/api/node/cordon", "summary": "隔离节点 body.name", "group": "ops"},
        {"method": "POST", "path": "/api/node/uncordon", "summary": "取消隔离 body.name", "group": "ops"},
        {"method": "POST", "path": "/api/node/add", "summary": "添加节点 body.name/host", "group": "ops"},
        {"method": "POST", "path": "/api/node/remove", "summary": "移除节点 body.name", "group": "ops"},
        {"method": "POST", "path": "/api/exec", "summary": "远程命令 body.command + node/nodes", "group": "ops"},
        {"method": "POST", "path": "/api/run-python", "summary": "远程跑 Python", "group": "ops"},
        {"method": "POST", "path": "/api/models/check", "summary": "模型路径检查", "group": "models"},
        {"method": "POST", "path": "/api/models/verify", "summary": "模型校验", "group": "models"},
        {"method": "POST", "path": "/api/models/deps", "summary": "节点 HF/MS/docker 依赖", "group": "models"},
        {"method": "POST", "path": "/api/models/install-tools", "summary": "节点安装拉取工具", "group": "models"},
        {"method": "POST", "path": "/api/models/pull", "summary": "拉取权重 body.name|model[,tool,register]", "group": "models"},
        {
            "method": "POST",
            "path": "/api/models/secrets",
            "summary": "保存/清除 HF·ModelScope token body.kind+token|clear",
            "group": "models",
        },
        {"method": "POST", "path": "/api/leases", "summary": "登记占用", "group": "ops"},
        {"method": "POST", "path": "/api/leases/{id}/release", "summary": "释放占用", "group": "ops"},
        {"method": "POST", "path": "/api/queue/submit", "summary": "入队", "group": "ops"},
        {"method": "POST", "path": "/api/queue/schedule", "summary": "调度队列", "group": "ops"},
        {"method": "POST", "path": "/api/queue/{id}/cancel", "summary": "取消队列项", "group": "ops"},
        {"method": "POST", "path": "/api/probe/run", "summary": "跑互通探测", "group": "ops"},
        {"method": "POST", "path": "/api/endpoints/health/run", "summary": "跑 API 健康探测", "group": "ops"},
        {"method": "POST", "path": "/api/gateway/render", "summary": "写出网关文件", "group": "ops"},
        {"method": "POST", "path": "/api/actions/oneclick", "summary": "一键运维 body.action", "group": "ops"},
        {
            "method": "WS",
            "path": "/ws/terminal",
            "summary": "交互终端 WebSocket（非 JSON API）",
            "group": "terminal",
        },
    ]


def cli_commands() -> list[dict]:
    return [
        {"cmd": "gpu-control doctor", "summary": "配置自检"},
        {"cmd": "gpu-control overview", "summary": "总览"},
        {"cmd": "gpu-control get jobs|nodes|leases|queue|endpoints", "summary": "列资源"},
        {"cmd": "gpu-control describe job|node NAME", "summary": "详情"},
        {"cmd": "gpu-control start|stop|restart|status|wait JOB", "summary": "Job 动作"},
        {"cmd": "gpu-control deploy JOB --gpus N|--nodes …", "summary": "一键部署"},
        {"cmd": "gpu-control procs list|kill …", "summary": "GPU 进程"},
        {"cmd": "gpu-control free --node N [--cordon] [--no-kill]", "summary": "腾空"},
        {"cmd": "gpu-control models list|deps|check|verify|pull|paths", "summary": "模型模块"},
        {"cmd": "gpu-control exec --node N -- CMD", "summary": "远程命令"},
        {"cmd": "gpu-control node render|cordon|uncordon …", "summary": "节点/inventory"},
        {"cmd": "gpu-control ui --background", "summary": "启动 Web :8787"},
    ]


def mcp_tools_summary() -> list[dict]:
    return [
        {"name": "gc_api_catalog", "summary": "列出 HTTP/CLI/MCP 能力"},
        {"name": "gc_doctor", "summary": "运行 doctor"},
        {"name": "gc_overview", "summary": "集群总览"},
        {"name": "gc_get_jobs", "summary": "列出 Job"},
        {"name": "gc_job_action", "summary": "启停 Job"},
        {"name": "gc_get_config", "summary": "读 cluster/nodes/job 配置"},
        {"name": "gc_put_config", "summary": "写配置（AI 改配置入口）"},
        {"name": "gc_models", "summary": "models list/deps/check/verify/paths"},
        {"name": "gc_procs", "summary": "列出或结束 GPU 进程"},
        {"name": "gc_free_node", "summary": "腾空/隔离节点"},
        {"name": "gc_exec", "summary": "远程执行命令"},
        {"name": "gc_deploy", "summary": "部署 Job"},
        {"name": "gc_cli", "summary": "直接跑 gpu-control 子命令（逃生舱）"},
    ]


def catalog_payload() -> dict:
    return {
        "service": "gpu-control",
        "version": _version(),
        "base": API_BASE,
        "auth": "内网信任；无 token。切勿暴露公网。",
        "ui_default": "http://<control>:8787",
        "groups": {
            "meta": "健康与目录",
            "read": "只读状态",
            "config": "读写配置（AI 调参主入口）",
            "ops": "运维动作",
            "models": "模型模块",
            "terminal": "交互终端",
        },
        "http": http_endpoints(),
        "cli": cli_commands(),
        "mcp": {
            "entrypoint": "bin/gpu-control-mcp",
            "transport": "stdio",
            "tools": mcp_tools_summary(),
            "cursor_example": "config/examples/mcp.cursor.json",
        },
        "ai_notes": [
            "改配置优先 POST /api/config/{cluster,nodes,job/NAME}，再 node render / doctor。",
            "启停负载用 Job 插件，不要假设只有 decider。",
            "腾空占卡用 /api/free 或 /api/procs/kill。",
            "完整说明见 docs/ai-接入.md 与 docs/手册.md。",
        ],
    }


def openapi_payload() -> dict:
    paths: dict = {}
    for ep in http_endpoints():
        if ep["method"] == "WS":
            continue
        item = paths.setdefault(ep["path"], {})
        item[ep["method"].lower()] = {
            "summary": ep["summary"],
            "tags": [ep["group"]],
            "responses": {"200": {"description": "OK"}},
        }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "gpu-control",
            "version": _version(),
            "description": "GPU 多节点控制面 HTTP API（供 UI / AI / MCP 使用）",
        },
        "paths": paths,
    }
