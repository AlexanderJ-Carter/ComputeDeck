"""HTTP / CLI / MCP 能力目录与 OpenAPI 文档生成。

供 ``GET /api``、``GET /api/openapi.json``、``GET /api/docs`` 与 MCP ``gc_api_catalog`` 使用。
"""

from __future__ import annotations

from typing import Any

from paths import VERSION_FILE

API_BASE = "/api"

# 常用请求体说明（写入 OpenAPI requestBody）
_BODY: dict[str, dict[str, Any]] = {
    "auth_login": {
        "type": "object",
        "required": ["password"],
        "properties": {"password": {"type": "string", "description": "管理密码"}},
    },
    "config_write": {
        "type": "object",
        "required": ["data"],
        "properties": {"data": {"type": "object", "description": "完整 JSON 文档"}},
    },
    "job_action": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "stop", "restart", "status", "wait"],
            },
            "node": {"type": "string"},
            "nodes": {"type": "string", "description": "逗号分隔节点名"},
            "extra_args": {"type": "array", "items": {"type": "string"}},
        },
    },
    "deploy": {
        "type": "object",
        "properties": {
            "job": {"type": "string"},
            "gpus": {"type": "integer"},
            "nodes": {"type": "string"},
            "restart": {"type": "boolean"},
            "no_wait": {"type": "boolean"},
            "note": {"type": "string"},
        },
    },
    "free": {
        "type": "object",
        "required": ["node"],
        "properties": {
            "node": {"type": "string"},
            "cordon": {"type": "boolean"},
            "no_kill": {"type": "boolean"},
            "wait": {"type": "integer"},
        },
    },
    "procs": {
        "type": "object",
        "required": ["node"],
        "properties": {
            "node": {"type": "string"},
            "refresh": {"type": "boolean"},
        },
    },
    "procs_kill": {
        "type": "object",
        "required": ["node"],
        "properties": {
            "node": {"type": "string"},
            "all": {"type": "boolean"},
            "pids": {"type": "array", "items": {"type": "integer"}},
            "match": {"type": "string"},
        },
    },
    "exec": {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"type": "string"},
            "node": {"type": "string"},
            "nodes": {"type": "string"},
            "preset": {"type": "string", "description": "远程命令预设 id"},
        },
    },
    "models_pull": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "仓库 id，如 org/model"},
            "model": {"type": "string", "description": "已登记 model id"},
            "tool": {"type": "string", "enum": ["auto", "huggingface-cli", "modelscope"]},
            "register": {"type": "boolean"},
            "register_id": {"type": "string"},
            "node": {"type": "string"},
        },
    },
    "gateway_kind": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["edge", "litellm", "all"], "default": "edge"},
        },
    },
    "install_template": {
        "type": "object",
        "required": ["template"],
        "properties": {
            "template": {"type": "string"},
            "name": {"type": "string"},
        },
    },
}


def _version() -> str:
    if VERSION_FILE.is_file():
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0"
    return "0"


def _auth_info() -> dict[str, Any]:
    from auth_util import AUTH_DISCOVERY_PATHS, AUTH_PUBLIC_PATHS, load_auth_config

    cfg = load_auth_config()
    public = sorted(AUTH_PUBLIC_PATHS | AUTH_DISCOVERY_PATHS | {"/api/docs", "/docs"})
    if cfg.enabled:
        note = (
            "已启用鉴权。浏览器用 Session Cookie；脚本 / MCP 用 "
            "Authorization: Bearer <token>（gpu-control auth issue-token）。"
        )
    else:
        note = (
            "当前 mode=none，仅适合 SSH 隧道访问。"
            "生产请执行：gpu-control auth set-password --generate --enable"
        )
    return {
        "mode": cfg.mode,
        "enabled": cfg.enabled,
        "schemes": ["bearer", "cookie"] if cfg.enabled else [],
        "header": "Authorization: Bearer <token>",
        "cookie": "gc_session",
        "issue_token": "gpu-control auth issue-token",
        "login": "POST /api/auth/login {\"password\":\"…\"}",
        "public_paths": public,
        "mcp_env": {
            "GPU_CONTROL_API_BASE": "http://127.0.0.1:8787",
            "GPU_CONTROL_API_TOKEN": "<Bearer token>",
        },
        "note": note,
    }


def http_endpoints() -> list[dict[str, Any]]:
    """控制面 HTTP 接口清单。"""
    return [
        {"method": "GET", "path": "/api", "summary": "API 能力目录（本列表）", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/openapi.json", "summary": "OpenAPI 3.0 文档", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/docs", "summary": "Swagger 风格接口文档页", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/health", "summary": "UI 健康检查", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/auth/status", "summary": "鉴权状态（公开）", "group": "auth", "auth": "public"},
        {"method": "GET", "path": "/api/auth/me", "summary": "当前登录主体", "group": "auth"},
        {
            "method": "POST",
            "path": "/api/auth/login",
            "summary": "密码登录（Set-Cookie）",
            "group": "auth",
            "auth": "public",
            "body": "auth_login",
        },
        {"method": "POST", "path": "/api/auth/logout", "summary": "退出并清除 Cookie", "group": "auth", "auth": "public"},
        {"method": "GET", "path": "/api/doctor", "summary": "配置自检 JSON", "group": "meta"},
        {"method": "GET", "path": "/api/diagnose", "summary": "一键检测常见问题", "group": "meta"},
        {"method": "POST", "path": "/api/diagnose/repair", "summary": "安全自动修复 body.ids?", "group": "ops"},
        {"method": "GET", "path": "/api/runlog", "summary": "运行日志 ?limit=&kind=", "group": "meta"},
        {"method": "POST", "path": "/api/runlog/clear", "summary": "清空运行日志", "group": "ops"},
        {
            "method": "GET",
            "path": "/api/overview",
            "summary": "集群总览",
            "group": "read",
            "query": {
                "refresh": "1=刷新 GPU",
                "light": "1=轻量（跳过 LB/完整 probe）",
            },
        },
        {"method": "GET", "path": "/api/jobs", "summary": "已注册 Job + 模板摘要", "group": "read"},
        {"method": "GET", "path": "/api/jobs/templates", "summary": "可安装 Job 模板", "group": "read"},
        {
            "method": "POST",
            "path": "/api/jobs/install-template",
            "summary": "安装模板到 local/jobs",
            "group": "ops",
            "body": "install_template",
        },
        {"method": "GET", "path": "/api/models", "summary": "模型/缓存/路径（?node=）", "group": "read"},
        {"method": "GET", "path": "/api/toolkits", "summary": "AI 工具栈目录与探测状态", "group": "read"},
        {
            "method": "POST",
            "path": "/api/toolkits/probe",
            "summary": "探测节点工具栈 body.ids|bundle",
            "group": "ops",
        },
        {
            "method": "POST",
            "path": "/api/toolkits/install",
            "summary": "安装工具栈 body.bundle=train-stack|serve-stack 或 ids",
            "group": "ops",
        },
        {"method": "GET", "path": "/api/leases", "summary": "占用登记", "group": "read"},
        {"method": "GET", "path": "/api/queue", "summary": "工作负载队列", "group": "read"},
        {"method": "GET", "path": "/api/probe", "summary": "节点互通矩阵缓存", "group": "read"},
        {"method": "GET", "path": "/api/quality", "summary": "SSH/质量评分", "group": "read"},
        {"method": "GET", "path": "/api/metrics", "summary": "主机 CPU/内存指标", "group": "read"},
        {"method": "GET", "path": "/api/endpoints/health", "summary": "推理 API 健康缓存", "group": "read"},
        {"method": "GET", "path": "/api/endpoints/lb", "summary": "负载计划 ?strategy=", "group": "read"},
        {"method": "GET", "path": "/api/gateway", "summary": "网关片段/拓扑", "group": "read"},
        {"method": "GET", "path": "/api/gateway/status", "summary": "Control 统一入口状态", "group": "read"},
        {"method": "GET", "path": "/api/setup", "summary": "配置向导状态", "group": "config"},
        {"method": "GET", "path": "/api/setup/guide", "summary": "上手步骤", "group": "config"},
        {"method": "GET", "path": "/api/config/cluster", "summary": "读取 cluster.json", "group": "config"},
        {"method": "GET", "path": "/api/config/nodes", "summary": "读取 nodes.json", "group": "config"},
        {"method": "GET", "path": "/api/config/job/{name}", "summary": "读取单个 Job JSON", "group": "config"},
        {
            "method": "POST",
            "path": "/api/config/cluster",
            "summary": "写入 cluster.json",
            "group": "config",
            "body": "config_write",
        },
        {
            "method": "POST",
            "path": "/api/config/nodes",
            "summary": "写入 nodes.json",
            "group": "config",
            "body": "config_write",
        },
        {
            "method": "POST",
            "path": "/api/config/job/{name}",
            "summary": "创建/更新 Job",
            "group": "config",
            "body": "config_write",
        },
        {"method": "POST", "path": "/api/setup/apply-ssh", "summary": "写入 SSH 默认并 render", "group": "config"},
        {"method": "POST", "path": "/api/setup/render", "summary": "渲染 inventory", "group": "config"},
        {"method": "POST", "path": "/api/setup/test-ssh", "summary": "测试 SSH 连通", "group": "config"},
        {"method": "POST", "path": "/api/resources/refresh", "summary": "刷新 GPU 快照", "group": "ops"},
        {"method": "POST", "path": "/api/metrics/refresh", "summary": "刷新主机指标", "group": "ops"},
        {
            "method": "POST",
            "path": "/api/jobs/{name}/action",
            "summary": "Job 动作 start|stop|restart|status|wait",
            "group": "ops",
            "body": "job_action",
        },
        {"method": "POST", "path": "/api/deploy", "summary": "一键部署", "group": "ops", "body": "deploy"},
        {"method": "POST", "path": "/api/free", "summary": "腾空节点", "group": "ops", "body": "free"},
        {"method": "POST", "path": "/api/procs", "summary": "列出 GPU 进程", "group": "ops", "body": "procs"},
        {
            "method": "POST",
            "path": "/api/procs/kill",
            "summary": "结束 GPU 进程",
            "group": "ops",
            "body": "procs_kill",
        },
        {"method": "POST", "path": "/api/node/cordon", "summary": "隔离节点", "group": "ops"},
        {"method": "POST", "path": "/api/node/uncordon", "summary": "取消隔离", "group": "ops"},
        {"method": "POST", "path": "/api/node/add", "summary": "添加节点", "group": "ops"},
        {"method": "POST", "path": "/api/node/remove", "summary": "移除节点", "group": "ops"},
        {"method": "POST", "path": "/api/exec", "summary": "远程命令", "group": "ops", "body": "exec"},
        {"method": "POST", "path": "/api/run-python", "summary": "远程跑 Python", "group": "ops"},
        {"method": "POST", "path": "/api/models/check", "summary": "模型路径检查", "group": "models"},
        {"method": "POST", "path": "/api/models/verify", "summary": "模型校验", "group": "models"},
        {"method": "POST", "path": "/api/models/deps", "summary": "节点 HF/MS/docker 依赖", "group": "models"},
        {"method": "POST", "path": "/api/models/install-tools", "summary": "节点安装拉取工具", "group": "models"},
        {
            "method": "POST",
            "path": "/api/models/pull",
            "summary": "拉取权重并可登记",
            "group": "models",
            "body": "models_pull",
        },
        {
            "method": "POST",
            "path": "/api/models/secrets",
            "summary": "保存/清除 HF·ModelScope token",
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
        {
            "method": "POST",
            "path": "/api/gateway/start",
            "summary": "启动 edge/LiteLLM 入口",
            "group": "ops",
            "body": "gateway_kind",
        },
        {
            "method": "POST",
            "path": "/api/gateway/stop",
            "summary": "停止入口",
            "group": "ops",
            "body": "gateway_kind",
        },
        {"method": "POST", "path": "/api/actions/oneclick", "summary": "一键运维", "group": "ops"},
        {
            "method": "WS",
            "path": "/ws/terminal",
            "summary": "交互终端 WebSocket（可用 ?access_token=）",
            "group": "terminal",
        },
    ]


def cli_commands() -> list[dict[str, str]]:
    """常用 CLI 命令摘要。"""
    return [
        {"cmd": "gpu-control doctor", "summary": "配置自检"},
        {"cmd": "gpu-control diagnose check|repair", "summary": "一键检测 / 安全修复"},
        {"cmd": "gpu-control logs [--limit N] [--kind oneclick]", "summary": "运行日志"},
        {"cmd": "gpu-control overview", "summary": "总览"},
        {"cmd": "gpu-control get jobs|nodes|leases|queue|endpoints", "summary": "列资源"},
        {"cmd": "gpu-control describe job|node NAME", "summary": "详情"},
        {"cmd": "gpu-control start|stop|restart|status|wait JOB", "summary": "Job 动作"},
        {"cmd": "gpu-control deploy JOB --gpus N|--nodes …", "summary": "一键部署"},
        {"cmd": "gpu-control procs list|kill …", "summary": "GPU 进程"},
        {"cmd": "gpu-control free --node N [--cordon] [--no-kill]", "summary": "腾空"},
        {"cmd": "gpu-control models list|deps|check|verify|pull|paths", "summary": "模型模块"},
        {"cmd": "gpu-control toolkits list|probe|install [--bundle train-stack]", "summary": "AI 工具栈"},
        {"cmd": "gpu-control jobs templates|install …", "summary": "Job 模板"},
        {"cmd": "gpu-control exec --node N -- CMD", "summary": "远程命令"},
        {"cmd": "gpu-control node render|cordon|uncordon …", "summary": "节点/inventory"},
        {"cmd": "gpu-control auth status|set-password|issue-token|set-mode", "summary": "控制面鉴权"},
        {"cmd": "gpu-control gateway start|stop|status [--kind edge]", "summary": "统一入口"},
        {"cmd": "gpu-control ui --background", "summary": "启动 Web :8787"},
    ]


def mcp_tools_summary() -> list[dict[str, str]]:
    """MCP 工具一览（与 mcp_server 保持同步）。"""
    return [
        {"name": "gc_api_catalog", "summary": "列出 HTTP / CLI / MCP 能力与鉴权说明"},
        {"name": "gc_auth_status", "summary": "查询鉴权模式与是否已配置 token"},
        {"name": "gc_doctor", "summary": "运行配置自检"},
        {"name": "gc_overview", "summary": "集群总览"},
        {"name": "gc_get_jobs", "summary": "列出已注册 Job"},
        {"name": "gc_job_action", "summary": "启停 Job（start|stop|…）"},
        {"name": "gc_get_config", "summary": "读 cluster / nodes / job 配置"},
        {"name": "gc_put_config", "summary": "写配置（Agent 改配置主入口）"},
        {"name": "gc_models", "summary": "模型 list / deps / check / verify / paths"},
        {"name": "gc_toolkits", "summary": "工具栈 list / probe / install"},
        {"name": "gc_procs", "summary": "列出或结束 GPU 进程"},
        {"name": "gc_free_node", "summary": "腾空 / 隔离节点"},
        {"name": "gc_deploy", "summary": "部署 Job"},
        {"name": "gc_exec", "summary": "远程执行命令"},
        {"name": "gc_cli", "summary": "直接跑 gpu-control 子命令（逃生舱）"},
    ]


def catalog_payload() -> dict[str, Any]:
    """``GET /api`` 完整目录。"""
    auth = _auth_info()
    return {
        "service": "gpu-control",
        "version": _version(),
        "base": API_BASE,
        "docs": {
            "openapi": "/api/openapi.json",
            "swagger_ui": "/api/docs",
            "help": "/help/",
            "handbook": "docs/手册.md",
            "ai": "docs/ai-接入.md",
            "mkdocs": "mkdocs.yml",
        },
        "auth": auth,
        "ui_default": "http://<control>:8787",
        "groups": {
            "meta": "健康与目录",
            "auth": "登录与身份",
            "read": "只读状态",
            "config": "读写配置（Agent 调参主入口）",
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
            "env": auth["mcp_env"],
        },
        "ai_notes": [
            "鉴权启用后，HTTP 客户端必须带 Bearer；MCP 设 GPU_CONTROL_API_TOKEN。",
            "改配置优先 POST /api/config/{cluster,nodes,job/NAME}，再 node render / doctor。",
            "启停负载用 Job 插件（含 train-job）；不要假设只有某一业务 Job。",
            "腾空占卡用 /api/free 或 /api/procs/kill。",
            "完整说明见 docs/ai-接入.md 与 /api/docs。",
        ],
    }


def openapi_payload() -> dict[str, Any]:
    """生成 OpenAPI 3.0.3 文档（含 Bearer / Cookie 鉴权）。"""
    auth = _auth_info()
    paths: dict[str, Any] = {}
    for ep in http_endpoints():
        if ep["method"] == "WS":
            continue
        method = ep["method"].lower()
        op: dict[str, Any] = {
            "operationId": f"{method}_{ep['path'].strip('/').replace('/', '_').replace('{', '').replace('}', '')}",
            "summary": ep["summary"],
            "tags": [ep["group"]],
            "responses": {
                "200": {"description": "成功"},
                "401": {"description": "未鉴权（鉴权启用时）"},
                "400": {"description": "请求参数错误"},
            },
        }
        if ep.get("auth") == "public":
            op["security"] = []
        query = ep.get("query") or {}
        if query:
            op["parameters"] = [
                {
                    "name": key,
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": str(desc),
                }
                for key, desc in query.items()
            ]
        body_key = ep.get("body")
        if body_key and body_key in _BODY:
            op["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": _BODY[body_key],
                    }
                },
            }
        if "{" in ep["path"]:
            params = op.setdefault("parameters", [])
            for part in ep["path"].split("/"):
                if part.startswith("{") and part.endswith("}"):
                    name = part[1:-1]
                    params.append(
                        {
                            "name": name,
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    )
        paths.setdefault(ep["path"], {})[method] = op

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "gpu-control",
            "version": _version(),
            "description": (
                "多节点 GPU 控制面 HTTP API（UI / Agent / MCP）。\n\n"
                f"**鉴权**：mode=`{auth['mode']}`；"
                f"{auth['note']}\n\n"
                "交互文档：`/api/docs` · 机器目录：`/api`"
            ),
            "contact": {"name": "gpu-control"},
        },
        "servers": [{"url": "/", "description": "当前控制面"}],
        "tags": [
            {"name": "meta", "description": "健康与目录"},
            {"name": "auth", "description": "登录与身份"},
            {"name": "read", "description": "只读状态"},
            {"name": "config", "description": "配置读写"},
            {"name": "ops", "description": "运维动作"},
            {"name": "models", "description": "模型模块"},
            {"name": "terminal", "description": "终端"},
        ],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "token",
                    "description": "gpu-control auth issue-token 签发的 API Token",
                },
                "cookieAuth": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "gc_session",
                    "description": "浏览器登录后的 HttpOnly Session",
                },
            }
        },
        "security": [{"bearerAuth": []}, {"cookieAuth": []}],
        "paths": paths,
        "x-gpu-control-auth": auth,
    }
