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
    "auth_exchange": {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "登录后跳转的站内路径，默认 /#/actions"}},
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
            "gpus": {
                "type": "integer",
                "description": "省略则用 cluster.scheduling.unit_gpus",
            },
            "nodes": {"type": "string"},
            "restart": {"type": "boolean"},
            "no_wait": {"type": "boolean"},
            "force": {
                "type": "boolean",
                "description": "覆盖异主租约",
            },
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
    "assistant_chat": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string", "description": "服务端会话 id；省略则新建"},
            "message": {"type": "string", "description": "本轮用户消息"},
            "stream": {
                "type": "boolean",
                "description": "true 时返回 SSE：event=meta|delta|tool|done|error",
            },
            "messages": {
                "type": "array",
                "description": "兼容旧客户端的历史消息",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                        "content": {"type": "string"},
                    },
                    "required": ["role", "content"],
                },
            },
        },
    },
    "liveness_check": {
        "type": "object",
        "properties": {
            "node": {"type": "string", "description": "只探测一个节点"},
            "nodes": {
                "description": "节点名数组或逗号分隔字符串；省略则全部",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
        },
    },
    "assistant_execute": {
        "type": "object",
        "required": ["conversation_id", "action_id"],
        "properties": {
            "conversation_id": {"type": "string"},
            "action_id": {"type": "string"},
            "stage": {"type": "string", "enum": ["execute", "reject"], "default": "execute"},
            "confirm": {
                "description": "写操作为 true；破坏性操作为字符串 EXECUTE（二次确认）",
                "oneOf": [{"type": "boolean"}, {"type": "string", "enum": ["EXECUTE"]}],
            },
            "args_hash": {"type": "string", "description": "可选：校验参数未被篡改"},
            "continue": {"type": "boolean", "description": "执行后是否让模型总结，默认 true"},
        },
    },
    "remote_action": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["status", "plan", "up", "down"]},
            "mode": {
                "type": "string",
                "enum": [
                    "auto",
                    "tailscale-serve",
                    "tailscale-funnel",
                    "cloudflared-quick",
                    "cloudflared-tunnel",
                    "all",
                ],
            },
            "port": {"type": "integer"},
            "confirm": {"type": "boolean", "description": "up/down 必须为 true"},
            "force": {"type": "boolean", "description": "鉴权未开启时允许公网暴露"},
        },
    },
    "assistant_config": {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "OpenAI 兼容根，如 https://api.openai.com/v1"},
            "model": {"type": "string"},
            "enabled": {"type": "boolean"},
            "timeout_sec": {"type": "integer"},
            "api_key": {"type": "string", "description": "写入 secrets 文件；省略则保留原密钥"},
            "clear_api_key": {"type": "boolean", "description": "为真则删除密钥文件"},
        },
    },
    "assistant_models": {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "草稿 Base URL，可先于保存试拉"},
            "api_key": {"type": "string", "description": "草稿密钥，可先于保存试拉"},
        },
    },
    "notices_mark": {
        "type": "object",
        "properties": {
            "ids": {"type": "array", "items": {"type": "string"}, "description": "省略则作用于全部未清除项"},
            "read": {"type": "boolean"},
            "dismissed": {"type": "boolean"},
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
    "litellm_master_key": {
        "type": "object",
        "properties": {
            "master_key": {"type": "string", "description": "自定义 Master Key；与 generate/clear 三选一"},
            "generate": {"type": "boolean", "description": "生成并保存新 Key"},
            "clear": {"type": "boolean", "description": "清除已保存 Key"},
            "restart": {
                "type": "boolean",
                "default": True,
                "description": "写入后是否重启 LiteLLM",
            },
        },
    },
    "auth_token": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["issue", "clear"], "default": "issue"},
        },
    },
    "litellm_model": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "delete"], "default": "add"},
            "model_name": {"type": "string", "description": "对外模型名"},
            "model": {"type": "string", "description": "上游模型，如 openai/decider"},
            "api_base": {"type": "string", "description": "http(s):// 节点推理地址"},
            "key_env": {"type": "string", "description": "上游密钥环境变量名"},
            "id": {"type": "string", "description": "删除时传 deployment id"},
        },
    },
    "install_template": {
        "type": "object",
        "properties": {
            "template": {"type": "string", "description": "模板 id；与 recommended 二选一"},
            "name": {"type": "string", "description": "安装后的 Job 名（默认模板名）"},
            "recommended": {
                "type": "boolean",
                "description": "为 true 时安装推荐模板包（忽略 template/name）",
            },
            "force": {
                "type": "boolean",
                "description": "与 recommended 同用：覆盖已安装同名 Job",
            },
        },
    },
    "lease_acquire": {
        "type": "object",
        "required": ["job", "nodes"],
        "properties": {
            "job": {"type": "string"},
            "nodes": {"type": "string", "description": "逗号分隔节点名"},
            "gpus": {"type": "integer"},
            "note": {"type": "string"},
        },
    },
    "queue_submit": {
        "type": "object",
        "required": ["job"],
        "properties": {
            "job": {"type": "string"},
            "gpus": {"type": "integer", "description": "省略则用 scheduling.unit_gpus"},
            "nodes": {"type": "string"},
            "note": {"type": "string"},
            "schedule": {"type": "boolean", "description": "入队后立即 schedule"},
        },
    },
    "node_name": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "reason": {"type": "string", "description": "cordon 原因（可选）"},
        },
    },
    "node_add": {
        "type": "object",
        "required": ["name", "host"],
        "properties": {
            "name": {"type": "string"},
            "host": {"type": "string"},
            "gpus": {"type": "integer", "default": 4},
            "labels": {"type": "string", "description": "逗号分隔标签"},
        },
    },
    "diagnose_repair": {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要修复的 finding id；省略则按 diagnose 默认集",
            },
        },
    },
    "models_check": {
        "type": "object",
        "properties": {
            "node": {"type": "string"},
            "nodes": {"type": "string"},
            "model": {"type": "string", "description": "verify 时可选"},
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
        "oidc_login": "GET /api/auth/oidc/login",
        "oidc_callback": "GET /api/auth/oidc/callback",
        "public_paths": public,
        "mcp_env": {
            "GPU_CONTROL_API_BASE": "http://127.0.0.1:8787",
            "GPU_CONTROL_API_TOKEN": "<Bearer token>",
        },
        "note": note,
    }


def http_endpoints() -> list[dict[str, Any]]:
    """控制面 HTTP / WebSocket 接口清单（供目录与 OpenAPI）。"""
    return [
        {"method": "GET", "path": "/api", "summary": "API 能力目录（本列表）", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/openapi.json", "summary": "OpenAPI 3.0 文档", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/docs", "summary": "Swagger 风格接口文档页", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/health", "summary": "UI 健康检查", "group": "meta", "auth": "public"},
        {"method": "GET", "path": "/api/auth/status", "summary": "鉴权状态（公开）", "group": "auth", "auth": "public"},
        {"method": "GET", "path": "/api/auth/me", "summary": "当前登录主体", "group": "auth"},
        {
            "method": "POST",
            "path": "/api/auth/exchange",
            "summary": "用 Bearer Token / Session 换一次性登录票据（插件打开看板）",
            "group": "auth",
            "body": "auth_exchange",
        },
        {
            "method": "GET",
            "path": "/auth/consume",
            "summary": "一次性票据换 Session 并跳转（浏览器）",
            "group": "auth",
            "auth": "public",
        },
        {
            "method": "POST",
            "path": "/api/auth/login",
            "summary": "密码登录（Set-Cookie）",
            "group": "auth",
            "auth": "public",
            "body": "auth_login",
        },
        {
            "method": "GET",
            "path": "/api/auth/oidc/login",
            "summary": "OIDC 跳转 IdP（授权码+PKCE）",
            "group": "auth",
            "auth": "public",
        },
        {
            "method": "GET",
            "path": "/api/auth/oidc/callback",
            "summary": "OIDC 回调并写 Session",
            "group": "auth",
            "auth": "public",
        },
        {"method": "POST", "path": "/api/auth/logout", "summary": "退出并清除 Cookie", "group": "auth", "auth": "public"},
        {"method": "GET", "path": "/api/doctor", "summary": "配置自检 JSON", "group": "meta"},
        {"method": "GET", "path": "/api/diagnose", "summary": "一键检测常见问题", "group": "meta"},
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
            "summary": "安装模板到 local/jobs（body.recommended=true 装推荐包）",
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
        {
            "method": "GET",
            "path": "/api/liveness",
            "summary": "节点存活与快照新鲜度（?check=1 立即探活）",
            "group": "read",
            "query": {"check": "1=立即执行 TCP 探活"},
        },
        {
            "method": "POST",
            "path": "/api/liveness/check",
            "summary": "立即探测节点存活（body.node/nodes 可限定）",
            "group": "ops",
            "body": "liveness_check",
        },
        {"method": "GET", "path": "/api/metrics", "summary": "主机 CPU/内存指标", "group": "read"},
        {
            "method": "GET",
            "path": "/api/metrics/history",
            "summary": "控制面采样曲线 ?limit=",
            "group": "read",
        },
        {"method": "GET", "path": "/api/endpoints/health", "summary": "推理 API 健康缓存", "group": "read"},
        {"method": "GET", "path": "/api/endpoints/lb", "summary": "负载计划 ?strategy=", "group": "read"},
        {"method": "GET", "path": "/api/gateway", "summary": "网关片段/拓扑", "group": "read"},
        {"method": "GET", "path": "/api/gateway/status", "summary": "Control 统一入口状态", "group": "read"},
        {
            "method": "GET",
            "path": "/api/gateway/litellm/master-key",
            "summary": "LiteLLM Master Key 是否已配置（不回显明文）",
            "group": "read",
        },
        {
            "method": "GET",
            "path": "/api/gateway/litellm/models",
            "summary": "LiteLLM 模型与 deployment 列表",
            "group": "read",
        },
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
        {
            "method": "GET",
            "path": "/api/jobs/{name}/logs",
            "summary": "Job 日志尾部 ?lines=&node=",
            "group": "read",
        },
        {
            "method": "POST",
            "path": "/api/jobs/{name}/logs",
            "summary": "Job 日志尾部 body.lines|node",
            "group": "ops",
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
        {
            "method": "POST",
            "path": "/api/node/cordon",
            "summary": "隔离节点",
            "group": "ops",
            "body": "node_name",
        },
        {
            "method": "POST",
            "path": "/api/node/uncordon",
            "summary": "取消隔离",
            "group": "ops",
            "body": "node_name",
        },
        {
            "method": "POST",
            "path": "/api/node/add",
            "summary": "添加节点",
            "group": "ops",
            "body": "node_add",
        },
        {
            "method": "POST",
            "path": "/api/node/remove",
            "summary": "移除节点",
            "group": "ops",
            "body": "node_name",
        },
        {"method": "GET", "path": "/api/exec/suggest", "summary": "远程命令补全建议", "group": "ops"},
        {"method": "POST", "path": "/api/exec", "summary": "远程命令", "group": "ops", "body": "exec"},
        {
            "method": "GET",
            "path": "/api/assistant/policy",
            "summary": "助手/MCP 工具风险与教学摘要",
            "group": "meta",
        },
        {
            "method": "GET",
            "path": "/api/assistant/status",
            "summary": "小助手是否就绪、预设与快捷动作",
            "group": "meta",
        },
        {
            "method": "POST",
            "path": "/api/assistant/config",
            "summary": "保存助手上游（base_url/model/api_key）",
            "group": "config",
            "body": "assistant_config",
        },
        {
            "method": "GET",
            "path": "/api/assistant/models",
            "summary": "拉取上游模型列表 ?base_url=",
            "group": "meta",
        },
        {
            "method": "POST",
            "path": "/api/assistant/models",
            "summary": "用草稿凭证拉取上游模型列表",
            "group": "meta",
            "body": "assistant_models",
        },
        {
            "method": "POST",
            "path": "/api/assistant/probe",
            "summary": "探测控制面到助手上游的 DNS/HTTP 可达性",
            "group": "meta",
            "body": "assistant_models",
        },
        {
            "method": "POST",
            "path": "/api/assistant/analyze",
            "summary": "AI 只读解读看板（overview + notices + diagnose）",
            "group": "meta",
        },
        {
            "method": "POST",
            "path": "/api/assistant/chat",
            "summary": "控制面小助手对话（只读工具自动执行，写操作返回确认卡）",
            "group": "meta",
            "body": "assistant_chat",
        },
        {
            "method": "GET",
            "path": "/api/assistant/session",
            "summary": "读取助手会话与待确认动作（?conversation_id=）",
            "group": "meta",
        },
        {
            "method": "POST",
            "path": "/api/assistant/execute",
            "summary": "确认并执行助手动作；结果回喂模型",
            "group": "ops",
            "body": "assistant_execute",
        },
        {
            "method": "GET",
            "path": "/api/remote",
            "summary": "远程接入状态 / 计划（?plan=1&mode=）",
            "group": "read",
        },
        {
            "method": "POST",
            "path": "/api/remote/action",
            "summary": "远程接入 status|plan|up|down",
            "group": "ops",
            "body": "remote_action",
        },
        {
            "method": "POST",
            "path": "/api/leases/release",
            "summary": "按 id 或 nodes 释放租约",
            "group": "ops",
        },
        {
            "method": "GET",
            "path": "/api/notices",
            "summary": "公告列表 ?sync=1 从 diagnose/overview 刷新",
            "group": "meta",
        },
        {
            "method": "POST",
            "path": "/api/notices/mark",
            "summary": "标记公告已读/清除",
            "group": "ops",
            "body": "notices_mark",
        },
        {"method": "POST", "path": "/api/run-python", "summary": "远程跑 Python", "group": "ops"},
        {
            "method": "POST",
            "path": "/api/models/check",
            "summary": "模型路径检查",
            "group": "models",
            "body": "models_check",
        },
        {
            "method": "POST",
            "path": "/api/models/verify",
            "summary": "模型校验",
            "group": "models",
            "body": "models_check",
        },
        {
            "method": "POST",
            "path": "/api/models/deps",
            "summary": "节点 HF/MS/docker 依赖",
            "group": "models",
            "body": "models_check",
        },
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
        {
            "method": "POST",
            "path": "/api/leases",
            "summary": "登记占用",
            "group": "ops",
            "body": "lease_acquire",
        },
        {"method": "POST", "path": "/api/leases/{id}/release", "summary": "释放占用", "group": "ops"},
        {
            "method": "POST",
            "path": "/api/queue/submit",
            "summary": "入队",
            "group": "ops",
            "body": "queue_submit",
        },
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
            "path": "/api/gateway/litellm/master-key",
            "summary": "设置/生成/清除 LiteLLM Master Key（可重启代理）",
            "group": "ops",
            "body": "litellm_master_key",
        },
        {
            "method": "POST",
            "path": "/api/auth/token",
            "summary": "生成或清除 API Bearer Token（仅返回一次）",
            "group": "ops",
            "body": "auth_token",
        },
        {
            "method": "POST",
            "path": "/api/auth/oidc/check",
            "summary": "OIDC 配置自检（discovery / 客户端参数）",
            "group": "ops",
        },
        {
            "method": "POST",
            "path": "/api/gateway/litellm/models",
            "summary": "添加/删除 LiteLLM 模型（简单别名；高级配置去 Admin）",
            "group": "ops",
            "body": "litellm_model",
        },
        {
            "method": "POST",
            "path": "/api/gateway/stop",
            "summary": "停止入口",
            "group": "ops",
            "body": "gateway_kind",
        },
        {
            "method": "POST",
            "path": "/api/diagnose/repair",
            "summary": "安全自动修复",
            "group": "ops",
            "body": "diagnose_repair",
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
        {"cmd": "gpu-control doctor", "summary": "配置自检（含 TLS / nginx 漂移）"},
        {"cmd": "gpu-control tls init|show|check", "summary": "入口 TLS 证书"},
        {"cmd": "gpu-control auth pair", "summary": "一次性远程配对链接"},
        {"cmd": "gpu-control bench", "summary": "控制面 API / 静态资源基准"},
        {"cmd": "gpu-control plan-job --gpus N", "summary": "规划多机任务（2×4 卡 = 8 卡作业）"},
        {"cmd": "gpu-control diagnose check|repair", "summary": "一键检测 / 安全修复"},
        {"cmd": "gpu-control logs [--limit N] [--kind oneclick]", "summary": "运行日志"},
        {"cmd": "gpu-control overview", "summary": "总览"},
        {"cmd": "gpu-control get jobs|nodes|leases|queue|endpoints", "summary": "列资源"},
        {"cmd": "gpu-control describe job|node NAME", "summary": "详情"},
        {"cmd": "gpu-control start|stop|restart|status|wait JOB", "summary": "Job 动作"},
        {"cmd": "gpu-control deploy JOB --gpus N|--nodes … [--force]", "summary": "一键部署"},
        {"cmd": "gpu-control queue list|submit|schedule|cancel …", "summary": "工作负载队列"},
        {"cmd": "gpu-control lease list|release …", "summary": "占用租约"},
        {"cmd": "gpu-control procs list|kill …", "summary": "GPU 进程"},
        {"cmd": "gpu-control free --node N [--cordon] [--no-kill]", "summary": "腾空"},
        {"cmd": "gpu-control models list|deps|check|verify|pull|paths", "summary": "模型模块"},
        {"cmd": "gpu-control toolkits list|probe|install [--bundle train-stack]", "summary": "AI 工具栈"},
        {"cmd": "gpu-control jobs templates|install …", "summary": "Job 模板"},
        {"cmd": "gpu-control exec --node N -- CMD", "summary": "远程命令"},
        {"cmd": "gpu-control node render|cordon|uncordon …", "summary": "节点/inventory"},
        {"cmd": "gpu-control auth status|set-password|issue-token|set-mode", "summary": "控制面鉴权"},
        {"cmd": "gpu-control gateway start|stop|status|render [--kind edge]", "summary": "统一入口"},
        {"cmd": "gpu-control probe|endpoints|metrics|watchdog …", "summary": "探测 / 健康 / 指标 / 巡检"},
        {"cmd": "gpu-control liveness status|check [--json]", "summary": "节点存活与快照新鲜度"},
        {"cmd": "gpu-control remote status|plan|up|down", "summary": "Tailscale / Cloudflare 远程接入"},
        {"cmd": "gpu-control ui --background", "summary": "启动 Web :8787"},
    ]


def mcp_tools_summary() -> list[dict[str, str]]:
    """MCP 工具一览（与 mcp_server 保持同步）。"""
    return [
        {"name": "gc_api_catalog", "summary": "列出 HTTP / CLI / MCP 能力与鉴权说明"},
        {"name": "gc_auth_status", "summary": "查询鉴权模式与是否已配置 token"},
        {"name": "gc_doctor", "summary": "运行配置自检（含 TLS / nginx 漂移）"},
        {"name": "gc_plan_job", "summary": "多机任务规划（2×4 卡 = 8 卡），只读"},
        {"name": "gc_tls", "summary": "入口 TLS 证书 show / check，只读"},
        {"name": "gc_overview", "summary": "集群总览"},
        {"name": "gc_get_jobs", "summary": "列出已注册 Job"},
        {"name": "gc_job_action", "summary": "启停 Job（start|stop|…）"},
        {"name": "gc_get_config", "summary": "读 cluster / nodes / job 配置"},
        {"name": "gc_put_config", "summary": "写配置（Agent 改配置主入口）"},
        {"name": "gc_models", "summary": "模型 list/show/deps/check/verify/paths/pull"},
        {"name": "gc_toolkits", "summary": "工具栈 list / probe / install"},
        {"name": "gc_procs", "summary": "列出或结束 GPU 进程"},
        {"name": "gc_free_node", "summary": "腾空节点（可选顺带 cordon）"},
        {"name": "gc_deploy", "summary": "部署 Job（支持 force/restart/no_wait）"},
        {"name": "gc_queue", "summary": "队列 list/submit/schedule/cancel"},
        {"name": "gc_lease", "summary": "租约 list/acquire/release"},
        {"name": "gc_diagnose", "summary": "诊断 check / 安全修复 repair"},
        {"name": "gc_gateway", "summary": "入口 status/get/render/start/stop"},
        {"name": "gc_node", "summary": "节点 render/cordon/uncordon/add/remove"},
        {"name": "gc_liveness", "summary": "节点存活与快照新鲜度 status/check"},
        {"name": "gc_remote", "summary": "远程接入 Tailscale / Cloudflare status/plan/up/down"},
        {"name": "gc_exec_preset", "summary": "单节点只读诊断预设（nvidia-smi / 进程 / 端口）"},
        {"name": "gc_exec", "summary": "远程执行任意命令（需确认）"},
        {"name": "gc_cli", "summary": "任意子命令逃生舱"},
    ]


def catalog_payload() -> dict[str, Any]:
    """``GET /api`` 完整目录。"""
    auth = _auth_info()
    return {
        "service": "gpu-control",
        "product": "ComputeDeck",
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
            "examples": {
                "cursor": "config/examples/mcp.cursor.json",
                "claude_code": "config/examples/mcp.claude-code.json",
                "codex": "config/examples/mcp.codex.toml",
                "codex_json": "config/examples/mcp.codex.json",
                "continue": "config/examples/mcp.continue.json",
                "windsurf": "config/examples/mcp.windsurf.json",
                "generic": "config/examples/mcp.generic.json",
            },
            "agents_md": "AGENTS.md",
            "claude_md": "CLAUDE.md",
            "skill": ".cursor/skills/gpu-control/SKILL.md",
            "env": auth["mcp_env"],
        },
        "assistant": {
            "policy": "/api/assistant/policy",
            "status": "/api/assistant/status",
            "config": "/api/assistant/config",
            "models": "/api/assistant/models",
            "analyze": "/api/assistant/analyze",
            "probe": "/api/assistant/probe",
            "chat": "/api/assistant/chat",
            "session": "/api/assistant/session",
            "execute": "/api/assistant/execute",
        },
        "liveness": "/api/liveness",
        "remote": {
            "status": "/api/remote",
            "action": "/api/remote/action",
        },
        "notices": {
            "list": "/api/notices",
            "mark": "/api/notices/mark",
        },
        "ai_notes": [
            "产品名 ComputeDeck；CLI/MCP/service 仍为 gpu-control。",
            "MCP 适用于 Cursor / Claude Code / Codex / Continue / Windsurf 等任意 stdio Agent；示例见 mcp.examples。",
            "仓库约定 AGENTS.md；Claude Code 另见 CLAUDE.md；交付为源码+uv 或 GHCR，不发 PyPI/npm。",
            "鉴权启用后，HTTP 客户端必须带 Bearer；MCP 设 GPU_CONTROL_API_TOKEN。",
            "改配置优先 POST /api/config/{cluster,nodes,job/NAME}，再 gc_node(render) / doctor。",
            "启停负载用 Job 插件；不要假设只有某一业务 Job。",
            "腾空占卡用 /api/free 或 /api/procs/kill；纯隔离用 gc_node(cordon)。",
            "调度：gc_queue / gc_lease；排障：gc_diagnose；入口：gc_gateway。",
            "工具风险分级见 GET /api/assistant/policy。",
            "完整说明见 docs/ai-接入.md、AGENTS.md 与 /api/docs。",
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
            "title": "ComputeDeck (gpu-control)",
            "version": _version(),
            "description": (
                "ComputeDeck 多节点算力控制面 HTTP API（CLI/服务名 gpu-control）。\n\n"
                f"**鉴权**：mode=`{auth['mode']}`；"
                f"{auth['note']}\n\n"
                "交互文档：`/api/docs` · 机器目录：`/api` · Agent：`docs/ai-接入.md`"
            ),
            "contact": {"name": "ComputeDeck"},
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
