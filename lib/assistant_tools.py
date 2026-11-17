"""UI 助手的工具注册表：暴露给模型的 schema、调用级风险与 HTTP 路由。

只有这里注册的工具才会出现在模型上下文里；写操作统一映射到已有 ``/api/*``
端点，避免在助手链路里复制一份业务逻辑。
"""

from __future__ import annotations

from typing import Any, Callable
import os
import re
import shlex

from exec_presets import EXEC_PRESETS

Risk = str
Route = Callable[[dict[str, Any]], dict[str, Any] | None]

# 读动作：同一工具里这些 action 自动执行；其它 action 走确认。
READ_ACTIONS: dict[str, set[str]] = {
    "gc_diagnose": {"check"},
    "gc_gateway": {"status", "get"},
    "gc_job_logs": {"tail"},
    "gc_lease": {"list"},
    "gc_liveness": {"status", "check"},
    "gc_models": {"list", "show", "deps", "check", "verify", "paths"},
    "gc_node": set(),
    "gc_notices": {"list"},
    "gc_procs": {"list"},
    "gc_queue": {"list"},
    "gc_remote": {"status", "plan"},
    "gc_toolkits": {"list"},
}


def _obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props}


def _spec(
    name: str,
    description: str,
    *,
    parameters: dict[str, Any] | None = None,
    risk: Risk = "write",
    route: Route | None = None,
    target: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters or _obj(),
        "risk": risk,
        "route": route,
        "target": target or {},
    }


def _path_quote(value: Any) -> str:
    from urllib.parse import quote

    return quote(str(value or ""), safe="")


def _deploy_route(args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"job": args.get("job")}
    for key in ("nodes", "gpus", "restart", "force", "no_wait", "note"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {"method": "POST", "path": "/api/deploy", "body": body}


def _free_route(args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"node": args.get("node")}
    for key in ("cordon", "no_kill", "wait", "force", "dry_run", "reason", "job"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {"method": "POST", "path": "/api/free", "body": body}


def _job_action_route(args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"action": args.get("action")}
    for key in ("node", "nodes", "extra_args"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {
        "method": "POST",
        "path": f"/api/jobs/{_path_quote(args.get('name'))}/action",
        "body": body,
    }


def _node_route(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "")
    if action == "render":
        return {"method": "POST", "path": "/api/setup/render", "body": {}}
    if action == "cordon":
        body = {"name": args.get("name")}
        if args.get("reason"):
            body["reason"] = args["reason"]
        return {"method": "POST", "path": "/api/node/cordon", "body": body}
    if action == "uncordon":
        return {"method": "POST", "path": "/api/node/uncordon", "body": {"name": args.get("name")}}
    if action == "remove":
        return {"method": "POST", "path": "/api/node/remove", "body": {"name": args.get("name")}}
    if action == "add":
        body = {
            "name": args.get("name"),
            "host": args.get("host"),
            "gpus": args.get("gpus") if args.get("gpus") is not None else 4,
        }
        if args.get("labels"):
            body["labels"] = args["labels"]
        return {"method": "POST", "path": "/api/node/add", "body": body}
    return {}


def _queue_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "list")
    if action == "list":
        return {"method": "GET", "path": "/api/queue", "body": None}
    if action == "submit":
        body = {"job": args.get("job")}
        for key in ("gpus", "nodes", "note", "schedule"):
            if args.get(key) not in (None, ""):
                body[key] = args[key]
        return {"method": "POST", "path": "/api/queue/submit", "body": body}
    if action == "schedule":
        return {"method": "POST", "path": "/api/queue/schedule", "body": {}}
    if action == "cancel":
        return {
            "method": "POST",
            "path": f"/api/queue/{_path_quote(args.get('workload_id'))}/cancel",
            "body": {},
        }
    return None


def _lease_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "list")
    if action == "list":
        return {"method": "GET", "path": "/api/leases", "body": None}
    if action == "acquire":
        body = {"job": args.get("job"), "nodes": args.get("nodes")}
        if args.get("gpus") is not None:
            body["gpus"] = args["gpus"]
        if args.get("note"):
            body["note"] = args["note"]
        return {"method": "POST", "path": "/api/leases", "body": body}
    if action == "release":
        lease_id = args.get("lease_id")
        if lease_id:
            return {
                "method": "POST",
                "path": f"/api/leases/{_path_quote(lease_id)}/release",
                "body": {},
            }
        return {
            "method": "POST",
            "path": "/api/leases/release",
            "body": {"nodes": args.get("nodes")},
        }
    return None


def _gateway_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "status")
    if action == "status":
        return {"method": "GET", "path": "/api/gateway/status", "body": None}
    if action == "get":
        return {"method": "GET", "path": "/api/gateway", "body": None}
    if action == "render":
        return {"method": "POST", "path": "/api/gateway/render", "body": {}}
    if action == "start":
        body = {}
        if args.get("kind"):
            body["kind"] = args["kind"]
        return {"method": "POST", "path": "/api/gateway/start", "body": body}
    if action == "stop":
        return {
            "method": "POST",
            "path": "/api/gateway/stop",
            "body": {"kind": args.get("kind") or "all"},
        }
    return None


def _models_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "list")
    if action == "list":
        node = args.get("node")
        return {
            "method": "GET",
            "path": f"/api/models?node={_path_quote(node)}" if node else "/api/models",
            "body": None,
        }
    if action in {"check", "deps", "verify"}:
        body = {}
        for key in ("node", "nodes", "model"):
            if args.get(key) not in (None, ""):
                body[key] = args[key]
        return {"method": "POST", "path": f"/api/models/{action}", "body": body}
    if action == "pull":
        body = {}
        for key in ("name", "model", "tool", "register", "register_id", "node", "nodes"):
            if args.get(key) not in (None, ""):
                body[key] = args[key]
        if args.get("timeout_sec") is not None:
            body["timeout"] = args["timeout_sec"]
        return {"method": "POST", "path": "/api/models/pull", "body": body}
    return None


def _toolkits_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "list")
    if action == "list":
        return {"method": "GET", "path": "/api/toolkits", "body": None}
    body = {}
    for key in ("bundle", "ids", "node", "nodes", "dry_run"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {"method": "POST", "path": f"/api/toolkits/{action}", "body": body}


def _procs_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "list")
    if action == "list":
        body = {}
        if args.get("node"):
            body["node"] = args["node"]
            body["refresh"] = True
        return {"method": "POST", "path": "/api/procs", "body": body}
    body = {"node": args.get("node")}
    for key in ("all", "pids", "match", "signal", "dry_run"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {"method": "POST", "path": "/api/procs/kill", "body": body}


def _diagnose_route(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "check")
    if action == "check":
        return {"method": "GET", "path": "/api/diagnose", "body": None}
    body = {}
    if args.get("ids"):
        body["ids"] = args["ids"]
    return {"method": "POST", "path": "/api/diagnose/repair", "body": body}


def _exec_route(args: dict[str, Any]) -> dict[str, Any]:
    body = {"command": args.get("command")}
    if args.get("node"):
        body["node"] = args["node"]
    elif args.get("nodes"):
        body["nodes"] = args["nodes"]
    return {"method": "POST", "path": "/api/exec", "body": body}


def _put_config_route(args: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(args.get("kind") or "").lower()
    data = args.get("data")
    if kind in {"cluster", "nodes"}:
        return {"method": "POST", "path": f"/api/config/{kind}", "body": {"data": data}}
    if kind == "job" and args.get("name"):
        return {
            "method": "POST",
            "path": f"/api/config/job/{_path_quote(args.get('name'))}",
            "body": {"data": data},
        }
    return None


def _notices_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "list")
    if action == "list":
        return {"method": "GET", "path": "/api/notices", "body": None}
    body = {}
    for key in ("ids", "read", "dismissed"):
        if args.get(key) is not None:
            body[key] = args[key]
    return {"method": "POST", "path": "/api/notices/mark", "body": body}


def _liveness_route(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "status")
    if action == "status":
        return {"method": "GET", "path": "/api/liveness", "body": None}
    body = {}
    if args.get("node"):
        body["node"] = args["node"]
    return {"method": "POST", "path": "/api/liveness/check", "body": body}


def _remote_route(args: dict[str, Any]) -> dict[str, Any] | None:
    action = str(args.get("action") or "status")
    if action in {"status", "plan"}:
        mode = args.get("mode")
        suffix = f"?mode={_path_quote(mode)}" if mode else ""
        return {"method": "GET", "path": f"/api/remote{suffix}", "body": None}
    body = {"action": action, "confirm": True}
    for key in ("mode", "port"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {"method": "POST", "path": "/api/remote/action", "body": body}


def _resources_refresh_route(_args: dict[str, Any]) -> dict[str, Any]:
    return {"method": "POST", "path": "/api/resources/refresh", "body": {}}


def _probe_run_route(_args: dict[str, Any]) -> dict[str, Any]:
    return {"method": "POST", "path": "/api/probe/run", "body": {}}


def _metrics_refresh_route(_args: dict[str, Any]) -> dict[str, Any]:
    return {"method": "POST", "path": "/api/metrics/refresh", "body": {}}


def _notices_read_route(args: dict[str, Any]) -> dict[str, Any]:
    return _notices_route(args) or {"method": "GET", "path": "/api/notices", "body": None}


def _install_template_route(args: dict[str, Any]) -> dict[str, Any]:
    body = {}
    for key in ("template", "name", "recommended", "force"):
        if args.get(key) not in (None, ""):
            body[key] = args[key]
    return {"method": "POST", "path": "/api/jobs/install-template", "body": body}


def _job_logs_route(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "GET",
        "path": f"/api/jobs/{_path_quote(args.get('name'))}/logs",
        "body": None,
    }


_SPECS: list[dict[str, Any]] = [
    _spec(
        "gc_overview",
        "集群总览：节点在线/离线、GPU 占用、租约、队列、入口状态。回答状态问题先调用它。",
        parameters=_obj(refresh={"type": "boolean", "description": "true=刷新 GPU 快照"}),
        risk="read",
    ),
    _spec(
        "gc_liveness",
        "节点存活与数据新鲜度。action=status 读缓存；check 立即 TCP 探活。",
        parameters=_obj(
            action={"type": "string", "enum": ["status", "check"]},
            node={"type": "string", "description": "check 时可只探测一个节点"},
        ),
        risk="read",
    ),
    _spec(
        "gc_doctor",
        "配置与运行环境自检。",
        parameters=_obj(),
        risk="read",
    ),
    _spec(
        "gc_diagnose",
        "一键检测常见问题；repair 仅修复安全可逆项。",
        parameters=_obj(
            action={"type": "string", "enum": ["check", "repair"]},
            ids={"type": "array", "items": {"type": "string"}},
        ),
        risk="write",
        route=_diagnose_route,
        target={},
    ),
    _spec(
        "gc_get_jobs",
        "列出已安装 Job（插件）及默认 Job。",
        parameters=_obj(),
        risk="read",
    ),
    _spec(
        "gc_job_logs",
        "查看某个 Job 的最近日志。",
        parameters=_obj(
            name={"type": "string", "description": "Job 名"},
            lines={"type": "integer", "description": "行数，默认 200"},
        ),
        risk="read",
        target={"jobs": "name"},
    ),
    _spec(
        "gc_api_catalog",
        "HTTP / CLI / MCP 能力目录。",
        parameters=_obj(),
        risk="read",
    ),
    _spec(
        "gc_get_config",
        "读取配置。kind=cluster|nodes|job；job 需要 name。",
        parameters=_obj(
            kind={"type": "string", "enum": ["cluster", "nodes", "job"]},
            name={"type": "string"},
        ),
        risk="read",
    ),
    _spec(
        "gc_queue",
        "工作负载队列。action=list|submit|schedule|cancel。",
        parameters=_obj(
            action={"type": "string", "enum": ["list", "submit", "schedule", "cancel"]},
            job={"type": "string"},
            gpus={"type": "integer"},
            nodes={"type": "string", "description": "逗号分隔节点名"},
            note={"type": "string"},
            schedule={"type": "boolean"},
            workload_id={"type": "string"},
        ),
        risk="write",
        route=_queue_route,
        target={"jobs": "job", "nodes": "nodes"},
    ),
    _spec(
        "gc_lease",
        "节点占用租约。action=list|acquire|release。",
        parameters=_obj(
            action={"type": "string", "enum": ["list", "acquire", "release"]},
            job={"type": "string"},
            nodes={"type": "string"},
            gpus={"type": "integer"},
            note={"type": "string"},
            lease_id={"type": "string"},
        ),
        risk="write",
        route=_lease_route,
        target={"jobs": "job", "nodes": "nodes"},
    ),
    _spec(
        "gc_gateway",
        "统一入口 Edge / LiteLLM。action=status|get|render|start|stop。",
        parameters=_obj(
            action={"type": "string", "enum": ["status", "get", "render", "start", "stop"]},
            kind={"type": "string", "enum": ["edge", "litellm", "all"]},
        ),
        risk="write",
        route=_gateway_route,
    ),
    _spec(
        "gc_node",
        "节点生命周期：render|cordon|uncordon|add|remove。",
        parameters=_obj(
            action={"type": "string", "enum": ["render", "cordon", "uncordon", "add", "remove"]},
            name={"type": "string"},
            host={"type": "string", "description": "add 时的 ansible_host"},
            gpus={"type": "integer"},
            labels={"type": "string", "description": "逗号分隔标签"},
            reason={"type": "string"},
        ),
        risk="write",
        route=_node_route,
        target={"node_name": "name"},
    ),
    _spec(
        "gc_deploy",
        "按 GPU 数或指定节点部署 Job。写操作，需用户确认。",
        parameters=_obj(
            job={"type": "string"},
            nodes={"type": "string"},
            gpus={"type": "integer"},
            restart={"type": "boolean"},
            force={"type": "boolean", "description": "覆盖异主租约"},
            no_wait={"type": "boolean"},
            note={"type": "string"},
        ),
        risk="write",
        route=_deploy_route,
        target={"jobs": "job", "nodes": "nodes"},
    ),
    _spec(
        "gc_free_node",
        "腾空节点：停 Job、结束 GPU 进程、释放租约，可选隔离。破坏性操作，需二次确认。",
        parameters=_obj(
            node={"type": "string"},
            job={"type": "string"},
            cordon={"type": "boolean"},
            no_kill={"type": "boolean"},
            wait={"type": "integer"},
            force={"type": "boolean"},
            dry_run={"type": "boolean", "description": "只预检不执行"},
            reason={"type": "string"},
        ),
        risk="destructive",
        route=_free_route,
        target={"nodes": "node"},
    ),
    _spec(
        "gc_job_action",
        "Job 动作：start|stop|restart|status|wait。写操作，需确认。",
        parameters=_obj(
            name={"type": "string"},
            action={"type": "string", "enum": ["start", "stop", "restart", "status", "wait"]},
            node={"type": "string"},
            nodes={"type": "string"},
            extra_args={"type": "array", "items": {"type": "string"}},
        ),
        risk="write",
        route=_job_action_route,
        target={"jobs": "name", "nodes": "nodes"},
    ),
    _spec(
        "gc_procs",
        "GPU 进程。action=list 只读；kill 结束进程（破坏性）。",
        parameters=_obj(
            action={"type": "string", "enum": ["list", "kill"]},
            node={"type": "string"},
            all={"type": "boolean"},
            pids={"type": "array", "items": {"type": "integer"}},
            match={"type": "string"},
            dry_run={"type": "boolean"},
        ),
        risk="destructive",
        route=_procs_route,
        target={"nodes": "node"},
    ),
    _spec(
        "gc_models",
        "模型模块。action=list|show|deps|check|verify|paths|pull。pull 写磁盘需确认。",
        parameters=_obj(
            action={"type": "string", "enum": ["list", "show", "deps", "check", "verify", "paths", "pull"]},
            node={"type": "string"},
            nodes={"type": "string"},
            model={"type": "string"},
            name={"type": "string", "description": "pull 的仓库 id"},
            tool={"type": "string", "enum": ["auto", "huggingface-cli", "modelscope"]},
            register={"type": "boolean"},
            register_id={"type": "string"},
            timeout_sec={"type": "integer"},
        ),
        risk="write",
        route=_models_route,
        target={"nodes": "node"},
    ),
    _spec(
        "gc_toolkits",
        "AI 工具栈。action=list|probe|install。install 写节点环境，需确认。",
        parameters=_obj(
            action={"type": "string", "enum": ["list", "probe", "install"]},
            bundle={"type": "string"},
            ids={"type": "array", "items": {"type": "string"}},
            node={"type": "string"},
            nodes={"type": "string"},
            dry_run={"type": "boolean"},
        ),
        risk="write",
        route=_toolkits_route,
        target={"nodes": "node"},
    ),
    _spec(
        "gc_exec_preset",
        "在单个在线节点运行只读诊断预设（nvidia-smi / GPU 利用率 / 进程 / 端口 / 磁盘等），自动执行、无需确认。",
        parameters=_obj(
            node={"type": "string", "description": "节点名"},
            preset={
                "type": "string",
                "enum": sorted(EXEC_PRESETS.keys()),
                "description": "只读诊断预设",
            },
        ),
        risk="read",
        target={"nodes": "node"},
    ),
    _spec(
        "gc_exec",
        "在单个工作节点执行任意 shell 命令（破坏性，需二次确认）。只读诊断请优先用 gc_exec_preset；必须指定 node，禁止 all。",
        parameters=_obj(
            command={"type": "string"},
            node={"type": "string"},
        ),
        risk="destructive",
        route=_exec_route,
        target={"nodes": "node"},
    ),
    _spec(
        "gc_put_config",
        "写入配置（cluster|nodes|job）。高风险管理操作，确认卡会展示变更摘要。",
        parameters=_obj(
            kind={"type": "string", "enum": ["cluster", "nodes", "job"]},
            name={"type": "string"},
            data={"type": "object"},
        ),
        risk="destructive",
        route=_put_config_route,
    ),
    _spec(
        "gc_notices",
        "监管公告。action=list|mark。",
        parameters=_obj(
            action={"type": "string", "enum": ["list", "mark"]},
            ids={"type": "array", "items": {"type": "string"}},
            read={"type": "boolean"},
            dismissed={"type": "boolean"},
        ),
        risk="write",
        route=_notices_route,
    ),
    _spec(
        "gc_resources_refresh",
        "刷新全部节点的 GPU 快照（Ansible 采集，可能较慢）。",
        parameters=_obj(),
        risk="write",
        route=_resources_refresh_route,
    ),
    _spec(
        "gc_probe_run",
        "重跑节点间 TCP 互通矩阵。",
        parameters=_obj(),
        risk="write",
        route=_probe_run_route,
    ),
    _spec(
        "gc_metrics_refresh",
        "采集节点 CPU / 内存 / load。",
        parameters=_obj(),
        risk="write",
        route=_metrics_refresh_route,
    ),
    _spec(
        "gc_install_template",
        "安装 Job 模板。recommended=true 安装推荐包。",
        parameters=_obj(
            template={"type": "string"},
            name={"type": "string"},
            recommended={"type": "boolean"},
            force={"type": "boolean"},
        ),
        risk="write",
        route=_install_template_route,
    ),
    _spec(
        "gc_remote",
        "远程接入（Tailscale / Cloudflare）。action=status|plan|up|down。",
        parameters=_obj(
            action={"type": "string", "enum": ["status", "plan", "up", "down"]},
            mode={"type": "string", "enum": ["tailscale-serve", "tailscale-funnel", "cloudflared-quick", "cloudflared-tunnel"]},
            port={"type": "integer"},
        ),
        risk="write",
        route=_remote_route,
    ),
]


_READ_COMMANDS = {
    "nvidia-smi", "uptime", "df", "free", "ps", "pgrep", "ls", "cat", "head", "tail",
    "grep", "wc", "sort", "uniq", "cut", "tr", "date", "uname", "hostname", "whoami",
    "id", "nproc", "lscpu", "lspci", "lsblk", "ip", "ifconfig", "netstat", "ss",
    "journalctl", "dmesg", "echo", "printf", "pwd", "which", "type", "stat", "file",
    "md5sum", "sha256sum", "readlink", "du", "find", "sed", "awk", "watch",
}
_WRITE_COMMANDS = {
    "cp", "mkdir", "touch", "chmod", "chown", "ln", "sed", "rsync", "scp", "pip",
    "pip3", "apt", "apt-get", "yum", "dnf", "systemctl", "service", "docker",
}
_DESTRUCTIVE_COMMANDS = {
    "rm", "rmdir", "mv", "dd", "mkfs", "reboot", "shutdown", "poweroff", "halt",
    "kill", "pkill", "killall", "sudo", "su", "truncate", "tee", "mount", "umount",
    "python", "python3", "bash", "sh", "zsh", "perl", "ruby", "node", "curl", "wget",
}
_UNSAFE_SHELL = re.compile(r"[;&|><`]|\$\(|\$\{|\n|\r")


def exec_command_risk(command: str) -> Risk:
    """给任意 shell 命令做保守分级。

    - 只读白名单且无 shell 连接符 / 重定向 → ``read``（自动执行）
    - 已知修改类命令 / 未知命令 → ``write``（一次确认）
    - rm / kill / reboot / python -c / 管道重定向等 → ``destructive``（二次确认）
    """
    text = str(command or "").strip()
    if not text:
        return "destructive"
    if _UNSAFE_SHELL.search(text):
        return "destructive"
    try:
        parts = shlex.split(text)
    except ValueError:
        return "destructive"
    if not parts:
        return "destructive"
    cmd = os.path.basename(parts[0])
    args = parts[1:]
    lowered = [str(x).lower() for x in args]
    if cmd in {"python", "python3", "bash", "sh", "zsh", "perl", "ruby", "node"}:
        return "destructive"
    if cmd in {"rm", "rmdir", "mv", "dd", "mkfs", "reboot", "shutdown", "poweroff", "halt",
               "kill", "pkill", "killall", "sudo", "su", "truncate", "tee", "mount", "umount"}:
        return "destructive"
    if cmd in {"docker"}:
        if args and lowered[0] in {"ps", "images", "logs", "inspect", "stats", "version", "info"}:
            return "read"
        return "destructive"
    if cmd in {"systemctl", "service"}:
        if args and lowered[0] in {"status", "is-active", "is-enabled", "list-units", "list-unit-files", "show"}:
            return "read"
        return "destructive"
    if cmd in {"curl", "wget"}:
        if any(a in lowered for a in ("-x", "--request", "-d", "--data", "--data-raw", "-f", "--form", "-t", "--upload-file")):
            return "destructive"
        return "read"
    if cmd == "find" and any(a in lowered for a in ("-delete", "-exec", "-execdir", "-ok")):
        return "destructive"
    if cmd == "sed" and any(a in lowered for a in ("-i", "--in-place")):
        return "write"
    if cmd in {"awk", "watch"} and ("system(" in text or "exec(" in text or "popen" in text):
        return "destructive"
    if cmd in _READ_COMMANDS:
        return "read"
    if cmd in _WRITE_COMMANDS:
        return "write"
    return "write"

_SPEC_BY_NAME = {spec["name"]: spec for spec in _SPECS}


def public_specs() -> list[dict[str, Any]]:
    """OpenAI tools 数组（只暴露模型需要的字段）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for spec in _SPECS
    ]


def tool_spec(name: str) -> dict[str, Any] | None:
    return _SPEC_BY_NAME.get(name)


def tool_names() -> list[str]:
    return list(_SPEC_BY_NAME)


def tool_risks() -> dict[str, str]:
    """工具级默认风险（调用级请看 ``risk_for_call``）。"""
    return {spec["name"]: spec["risk"] for spec in _SPECS}


def _action(args: dict[str, Any]) -> str:
    return str((args or {}).get("action") or "").strip().lower()


def risk_for_call(name: str, args: dict[str, Any] | None = None) -> Risk:
    """调用级风险：同一工具的不同 action 可分别为读/写。"""
    spec = _SPEC_BY_NAME.get(name)
    if spec is None:
        return "destructive"
    args = args or {}
    action = _action(args)
    read_actions = READ_ACTIONS.get(name, set())
    if action and action in read_actions:
        return "read"
    if not action and name in {
        "gc_overview",
        "gc_doctor",
        "gc_get_jobs",
        "gc_api_catalog",
        "gc_get_config",
        "gc_job_logs",
    }:
        return "read"
    # gc_liveness 的 check 也是只读探测
    if name == "gc_liveness" and action in {"status", "check", ""}:
        return "read"
    if name == "gc_exec":
        return exec_command_risk(str(args.get("command") or ""))
    return str(spec.get("risk") or "write")


def confirm_level(name: str, args: dict[str, Any] | None = None) -> str:
    """确认级别：``read`` / ``confirm`` / ``confirm_twice``。"""
    risk = risk_for_call(name, args)
    if risk == "read":
        return "read"
    return "confirm_twice" if risk == "destructive" else "confirm"


def route_for(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    spec = _SPEC_BY_NAME.get(name)
    if spec is None or spec.get("route") is None:
        return None
    try:
        return spec["route"](args or {})
    except Exception:  # noqa: BLE001 — 路由构造失败按未注册处理
        return None


def summary_for(name: str, args: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, str]:
    """生成确认卡标题与说明（中文）。"""
    args = args or {}
    target = target or {}
    nodes = target.get("nodes") or []
    jobs = target.get("jobs") or []
    node_text = "、".join(nodes) if nodes else ""
    job_text = "、".join(jobs) if jobs else ""
    action = _action(args)
    if name == "gc_deploy":
        label = f"部署 {job_text or args.get('job') or 'Job'}"
        if node_text:
            summary = f"在 {node_text} 上部署 {job_text}，随后等待就绪。"
        elif args.get("gpus"):
            summary = f"调度 {args['gpus']} 张 GPU 部署 {job_text}。"
        else:
            summary = f"按 cluster.scheduling.unit_gpus 调度并部署 {job_text}。"
    elif name == "gc_free_node":
        label = f"腾空节点 {node_text or args.get('node')}"
        extras = []
        if args.get("cordon"):
            extras.append("并隔离")
        if args.get("no_kill"):
            extras.append("不杀进程")
        summary = "停止该节点上的 Job、释放租约" + ("".join(extras)) + "。"
    elif name == "gc_job_action":
        label = f"{action} Job {job_text or args.get('name')}"
        summary = f"对 {job_text or args.get('name')} 执行 {action}"
        if node_text:
            summary += f"（节点 {node_text}）"
        summary += "。"
    elif name == "gc_node":
        label = {
            "render": "渲染 inventory",
            "cordon": f"隔离节点 {args.get('name')}",
            "uncordon": f"恢复节点 {args.get('name')}",
            "add": f"添加节点 {args.get('name')}",
            "remove": f"删除节点 {args.get('name')}",
        }.get(action, f"节点 {action}")
        summary = {
            "render": "根据 nodes.json 重新渲染 inventory 与端点缓存。",
            "cordon": f"节点 {args.get('name')} 将不再参与调度与部署。",
            "uncordon": f"节点 {args.get('name')} 恢复参与调度。",
            "add": f"新增节点 {args.get('name')} → {args.get('host')}，GPU 数 {args.get('gpus') or 4}。",
            "remove": f"从 nodes.json 删除节点 {args.get('name')}。",
        }.get(action, "修改节点清单。")
    elif name == "gc_queue":
        label = f"队列 {action}"
        summary = {
            "submit": f"提交 {job_text or args.get('job')} 到队列" + ("并立即调度" if args.get("schedule") else "") + "。",
            "schedule": "按调度策略启动 pending 工作负载。",
            "cancel": f"取消队列项 {args.get('workload_id')}。",
            "list": "查看队列。",
        }.get(action, "队列操作。")
    elif name == "gc_lease":
        label = f"租约 {action}"
        summary = {
            "acquire": f"为 {job_text or args.get('job')} 登记节点 {node_text or args.get('nodes')}。",
            "release": "释放租约。",
            "list": "查看租约。",
        }.get(action, "租约操作。")
    elif name == "gc_gateway":
        label = f"入口 {action}"
        summary = {
            "start": f"启动入口 {args.get('kind') or 'edge'}。",
            "stop": f"停止入口 {args.get('kind') or 'all'}。",
            "render": "重新渲染网关配置与片段。",
            "status": "查看入口状态。",
            "get": "查看入口拓扑。",
        }.get(action, "入口操作。")
    elif name == "gc_procs":
        label = f"结束 GPU 进程 @ {node_text or args.get('node')}"
        summary = "将结束匹配的 GPU 计算进程；请核对 PID / 匹配规则。"
    elif name == "gc_exec_preset":
        preset = str(args.get("preset") or "")
        label = f"运行只读诊断：{EXEC_PRESETS.get(preset, {}).get('label', preset)} @ {node_text or args.get('node')}"
        summary = f"预设命令：{EXEC_PRESETS.get(preset, {}).get('command', preset)}（只读，自动执行）"
    elif name == "gc_exec":
        label = f"在 {node_text or args.get('node')} 执行命令"
        summary = f"command: {args.get('command')}"
    elif name == "gc_models":
        label = f"模型 {action}"
        summary = {
            "pull": f"拉取模型 {args.get('name') or args.get('model')}。",
            "verify": f"校验模型 {args.get('model') or '全部'}。",
            "check": "检查模型依赖。",
            "deps": "检查模型依赖。",
            "list": "列出模型。",
            "show": "查看模型详情。",
            "paths": "查看模型路径。",
        }.get(action, "模型操作。")
    elif name == "gc_toolkits":
        label = f"工具栈 {action}"
        summary = {
            "probe": "探测节点上的 AI 工具栈。",
            "install": f"安装工具栈 {args.get('bundle') or args.get('ids') or ''}。",
            "list": "列出工具栈。",
        }.get(action, "工具栈操作。")
    elif name == "gc_diagnose":
        label = "一键修复" if action == "repair" else "运行自检"
        summary = "修复安全可逆的常见问题；高风险项仍会保留。" if action == "repair" else "运行 diagnose check。"
    elif name == "gc_put_config":
        label = f"写入 {args.get('kind')} 配置"
        summary = "将覆盖配置文档；请核对确认卡中的变更摘要。"
    elif name == "gc_notices":
        label = f"公告 {action}"
        summary = "标记公告已读 / 清除。"
    elif name == "gc_resources_refresh":
        label = "刷新 GPU 快照"
        summary = "对全部节点执行 Ansible 采集并解析 resources.json；离线节点会标记过期。"
    elif name == "gc_probe_run":
        label = "重跑互通矩阵"
        summary = "对节点间 TCP 端口做一轮互通探测。"
    elif name == "gc_metrics_refresh":
        label = "采集主机指标"
        summary = "采集各节点 CPU / 内存 / load。"
    elif name == "gc_install_template":
        label = "安装 Job 模板"
        summary = "把模板写入 config/local/jobs。"
    elif name == "gc_remote":
        label = f"远程接入 {action}"
        summary = {
            "up": f"启动 {args.get('mode') or 'tailscale-serve'}，把 :{args.get('port') or 8787} 暴露到远程。",
            "down": "关闭远程接入。",
            "plan": "查看远程接入计划。",
            "status": "查看远程接入状态。",
        }.get(action, "远程接入操作。")
    else:
        label = name
        summary = "执行该操作。"
    return {"label": label, "summary": summary}
