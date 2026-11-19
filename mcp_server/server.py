#!/usr/bin/env python3
"""gpu-control MCP 服务（stdio），供 Cursor 等 Agent 调用。

默认在主控本机包装 CLI。
设置 ``GPU_CONTROL_API_BASE=http://127.0.0.1:8787`` 时优先走 HTTP；
鉴权启用后需同时设置 ``GPU_CONTROL_API_TOKEN``（``gpu-control auth issue-token``）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from api_catalog import catalog_payload  # noqa: E402
from assistant_policy import TEACHING_SUMMARY  # noqa: E402
from paths import CLI, PROJECT_DIR, VENV_PYTHON  # noqa: E402
from exec_presets import EXEC_PRESETS  # noqa: E402

try:
    from mcp.server.mcpserver import MCPServer
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少 mcp 包。在主控执行:\n"
        "  uv pip install -r requirements-mcp.txt\n"
        "（内网可加 --index-url <your-pypi-mirror>）\n"
        f"原始错误: {exc}"
    ) from exc

mcp = MCPServer(
    name="gpu-control",
    instructions=TEACHING_SUMMARY
    + " 适用于 Cursor / Claude Code / Codex 等任意 stdio MCP 客户端。"
    + " 风险分级见 GET /api/assistant/policy（HTTP）或先 gc_api_catalog。",
)


def _api_base() -> str:
    return (os.environ.get("GPU_CONTROL_API_BASE") or "").rstrip("/")


def _api_token() -> str:
    return (os.environ.get("GPU_CONTROL_API_TOKEN") or "").strip()


def _http_json(method: str, path: str, body: dict | None = None, timeout: int = 120) -> Any:
    base = _api_base()
    if not base:
        raise RuntimeError("GPU_CONTROL_API_BASE 未设置")
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _http_json_safe(method: str, path: str, body: dict | None = None, timeout: int = 120) -> Any:
    try:
        return _http_json(method, path, body=body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _cli(args: list[str], *, timeout: int = 600) -> dict:
    cmd = [str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable), str(CLI), *args]
    completed = subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env={
            **os.environ,
            "PATH": f"{PROJECT_DIR / '.venv' / 'bin'}:{os.environ.get('PATH', '')}",
        },
    )
    return {
        "cmd": args,
        "rc": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _prefer_http() -> bool:
    return bool(_api_base())


def _read_state_json(filename: str, default: dict[str, Any]) -> dict[str, Any]:
    from paths import STATE_DIR

    path = STATE_DIR / filename
    if not path.is_file():
        return dict(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _cli_json(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    """跑 CLI 并解析 stdout JSON；失败时带上原始 cli 结果。"""
    result = _cli(args, timeout=timeout)
    try:
        payload = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "stdout 非 JSON", "_cli": result}
    if isinstance(payload, dict):
        payload["_cli"] = result
        return payload
    return {"ok": result["rc"] == 0, "data": payload, "_cli": result}


@mcp.tool()
def gc_api_catalog() -> dict:
    """列出 HTTP / CLI / MCP 能力目录，含鉴权与文档入口。"""
    return catalog_payload()


@mcp.tool()
def gc_auth_status() -> dict:
    """查询控制面鉴权模式、是否启用、token/密码是否已配置。"""
    if _prefer_http():
        return _http_json_safe("GET", "/api/auth/status")
    from auth_util import auth_status_payload

    return auth_status_payload()


@mcp.tool()
def gc_doctor() -> dict:
    """运行配置自检，返回 doctor JSON。"""
    if _prefer_http():
        return _http_json_safe("GET", "/api/doctor")
    result = _cli(["doctor", "--json"], timeout=60)
    payload = json.loads(result["stdout"] or "{}")
    payload["_cli"] = result
    return payload


@mcp.tool()
def gc_plan_job(
    gpus: int,
    job: str | None = None,
    nodes: str | None = None,
    include_unverified: bool = False,
) -> dict:
    """规划多机任务（例如 2×4 卡组成 8 卡作业），只读，不启动任务。

    返回 rank、MASTER_ADDR、NPROC_PER_NODE、hostfile 和告警。
    """
    args = ["plan-job", "--gpus", str(gpus), "--json"]
    if job:
        args.extend(["--job", job])
    if nodes:
        args.extend(["--nodes", nodes])
    if include_unverified:
        args.append("--include-unverified")
    return _cli_json(args, timeout=60)


@mcp.tool()
def gc_tls(action: str = "show") -> dict:
    """查看入口 TLS 证书状态；action=show|check，只读。"""
    action = str(action or "show").strip().lower()
    if action not in {"show", "check"}:
        return {"ok": False, "error": "action 须为 show|check"}
    return _cli_json(["tls", action], timeout=30)


@mcp.tool()
def gc_overview(refresh: bool = False) -> dict:
    """集群总览：节点、GPU、lease、Job 摘要。refresh=true 时刷新 GPU。"""
    if _prefer_http():
        q = "?refresh=1" if refresh else ""
        return _http_json_safe("GET", f"/api/overview{q}")
    args = ["overview"]
    if not refresh:
        args.append("--cached")
    return _cli(args, timeout=180)


@mcp.tool()
def gc_get_jobs() -> dict:
    """列出已注册 Job（推理 / 微调等插件）。"""
    if _prefer_http():
        return _http_json_safe("GET", "/api/jobs")
    return _cli(["get", "jobs"])


@mcp.tool()
def gc_job_action(
    job: str,
    action: str,
    node: str | None = None,
    nodes: str | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    """执行 Job 动作：start|stop|restart|status|wait。"""
    if action not in {"start", "stop", "restart", "status", "wait"}:
        return {"ok": False, "error": "action 须为 start|stop|restart|status|wait"}
    if _prefer_http():
        body: dict[str, Any] = {"action": action}
        if node:
            body["node"] = node
        elif nodes:
            body["nodes"] = nodes
        if extra_args:
            body["extra_args"] = extra_args
        return _http_json_safe("POST", f"/api/jobs/{job}/action", body, timeout=1200)
    args = [action, job]
    if node:
        args.extend(["--node", node])
    elif nodes:
        args.extend(["--nodes", nodes])
    for item in extra_args or []:
        args.extend(["--arg", item])
    return _cli(args, timeout=1200)


@mcp.tool()
def gc_get_config(kind: str, name: str | None = None) -> dict:
    """读取配置。kind=cluster|nodes|job；job 时需传 name。"""
    kind = kind.strip().lower()
    if kind == "cluster":
        if _prefer_http():
            return _http_json_safe("GET", "/api/config/cluster")
        return _read_local("cluster")
    if kind == "nodes":
        if _prefer_http():
            return _http_json_safe("GET", "/api/config/nodes")
        return _read_local("nodes")
    if kind == "job":
        if not name:
            return {"ok": False, "error": "job 需要 name"}
        if _prefer_http():
            return _http_json_safe("GET", f"/api/config/job/{name}")
        return _cli(["describe", "job", name])
    return {"ok": False, "error": "kind 须为 cluster|nodes|job"}


def _read_local(kind: str) -> dict:
    from paths import cluster_file, nodes_file

    path = cluster_file() if kind == "cluster" else nodes_file()
    if not path.is_file():
        return {"ok": False, "error": f"缺少文件 {path}"}
    return {"path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}


@mcp.tool()
def gc_put_config(kind: str, data: dict, name: str | None = None) -> dict:
    """写入配置。kind=cluster|nodes|job；job 时传 name 与完整 Job JSON。

    修改 nodes/路径后请再调用 gc_node(action='render') 与 gc_doctor()。
    """
    kind = kind.strip().lower()
    if not _prefer_http():
        from paths import LOCAL_JOBS_DIR, cluster_file, nodes_file

        if kind == "cluster":
            path = cluster_file(write=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return {"ok": True, "path": str(path)}
        if kind == "nodes":
            path = nodes_file(write=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            render = _cli(["node", "render"], timeout=60)
            return {"ok": render["rc"] == 0, "path": str(path), "render": render}
        if kind == "job":
            if not name:
                return {"ok": False, "error": "job 需要 name"}
            LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
            path = LOCAL_JOBS_DIR / f"{name}.json"
            payload = dict(data)
            payload["name"] = name
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return {"ok": True, "path": str(path)}
        return {"ok": False, "error": "kind 须为 cluster|nodes|job"}

    if kind == "cluster":
        return _http_json_safe("POST", "/api/config/cluster", {"data": data})
    if kind == "nodes":
        return _http_json_safe("POST", "/api/config/nodes", {"data": data})
    if kind == "job":
        if not name:
            return {"ok": False, "error": "job 需要 name"}
        return _http_json_safe("POST", f"/api/config/job/{name}", {"data": data})
    return {"ok": False, "error": "kind 须为 cluster|nodes|job"}


@mcp.tool()
def gc_toolkits(
    action: str = "list",
    bundle: str | None = None,
    ids: list[str] | None = None,
    node: str | None = None,
    nodes: str = "all",
    dry_run: bool = False,
) -> dict:
    """AI 工具栈。action=list|probe|install；bundle 如 train-stack / serve-stack。"""
    if action not in {"list", "probe", "install"}:
        return {"ok": False, "error": "action 须为 list|probe|install"}
    if _prefer_http():
        if action == "list":
            return _http_json_safe("GET", "/api/toolkits")
        body: dict[str, Any] = {}
        if bundle:
            body["bundle"] = bundle
        if ids:
            body["ids"] = ids
        if node:
            body["node"] = node
        else:
            body["nodes"] = nodes
        if dry_run:
            body["dry_run"] = True
        return _http_json_safe("POST", f"/api/toolkits/{action}", body, timeout=1800)
    args = ["toolkits", action]
    if bundle:
        args.extend(["--bundle", bundle])
    for tid in ids or []:
        args.extend(["--id", tid])
    if node:
        args.extend(["--node", node])
    else:
        args.extend(["--nodes", nodes])
    if dry_run:
        args.append("--dry-run")
    return _cli(args, timeout=1800)


@mcp.tool()
def gc_models(
    action: str = "list",
    node: str | None = None,
    nodes: str | None = None,
    model: str | None = None,
    name: str | None = None,
    tool: str | None = None,
    register: bool = False,
    register_id: str | None = None,
    timeout_sec: int = 3600,
) -> dict:
    """模型模块。action=list|show|deps|check|verify|paths|pull。

    pull 时传 ``name``（仓库 id）或已登记 ``model``；可选 ``register`` / ``tool``。
    """
    if action not in {"list", "show", "deps", "check", "verify", "paths", "pull"}:
        return {"ok": False, "error": "action 须为 list|show|deps|check|verify|paths|pull"}
    if action == "pull":
        if not name and not model:
            return {"ok": False, "error": "pull 需要 name（仓库 id）或 model（已登记 id）"}
        if _prefer_http():
            body: dict[str, Any] = {}
            if name:
                body["name"] = name
            if model:
                body["model"] = model
            if tool:
                body["tool"] = tool
            if register:
                body["register"] = True
            if register_id:
                body["register_id"] = register_id
            if node:
                body["node"] = node
            elif nodes:
                body["nodes"] = nodes
            body["timeout"] = timeout_sec
            return _http_json_safe("POST", "/api/models/pull", body, timeout=timeout_sec)
        args = ["models", "pull"]
        if name:
            args.extend(["--name", name])
        if model:
            args.extend(["--model", model])
        if tool:
            args.extend(["--tool", tool])
        if register:
            args.append("--register")
        if register_id:
            args.extend(["--register-id", register_id])
        if node:
            args.extend(["--node", node])
        elif nodes:
            args.extend(["--nodes", nodes])
        return _cli(args, timeout=timeout_sec)
    if _prefer_http() and action in {"deps", "check", "verify"}:
        body = {}
        if node:
            body["node"] = node
        elif nodes:
            body["nodes"] = nodes
        if model and action == "verify":
            body["model"] = model
        return _http_json_safe("POST", f"/api/models/{action}", body, timeout=300)
    if _prefer_http() and action == "list":
        q = f"?node={node}" if node else ""
        return _http_json_safe("GET", f"/api/models{q}")
    args = ["models", action]
    if node:
        args.extend(["--node", node])
    elif nodes:
        args.extend(["--nodes", nodes])
    if model and action == "verify":
        args.extend(["--model", model])
    return _cli(args, timeout=300)


@mcp.tool()
def gc_procs(
    action: str = "list",
    node: str | None = None,
    all_procs: bool = False,
    pids: list[int] | None = None,
    match: str | None = None,
) -> dict:
    """列出或结束 GPU 计算进程。action=list|kill。"""
    if action == "list":
        if _prefer_http() and node:
            return _http_json_safe("POST", "/api/procs", {"node": node, "refresh": True}, timeout=180)
        args = ["procs", "list", "--refresh"]
        if node:
            args.extend(["--node", node])
        return _cli(args, timeout=180)
    if action == "kill":
        if not node:
            return {"ok": False, "error": "kill 需要 node"}
        if _prefer_http():
            body: dict[str, Any] = {"node": node}
            if all_procs:
                body["all"] = True
            if pids:
                body["pids"] = pids
            if match:
                body["match"] = match
            return _http_json_safe("POST", "/api/procs/kill", body, timeout=300)
        args = ["procs", "kill", "--node", node]
        if all_procs:
            args.append("--all")
        for pid in pids or []:
            args.extend(["--pid", str(pid)])
        if match:
            args.extend(["--match", match])
        return _cli(args, timeout=300)
    return {"ok": False, "error": "action 须为 list|kill"}


@mcp.tool()
def gc_free_node(
    node: str,
    cordon: bool = False,
    no_kill: bool = False,
    wait: int = 20,
) -> dict:
    """腾空节点：停 Job、杀 GPU 进程（除非 no_kill）、释放 lease，可选隔离。"""
    if _prefer_http():
        return _http_json_safe(
            "POST",
            "/api/free",
            {"node": node, "cordon": cordon, "no_kill": no_kill, "wait": wait},
            timeout=1200,
        )
    args = ["free", "--node", node, "--wait", str(wait)]
    if cordon:
        args.append("--cordon")
    if no_kill:
        args.append("--no-kill")
    return _cli(args, timeout=1200)


@mcp.tool()
def gc_deploy(
    job: str,
    gpus: int | None = None,
    nodes: str | None = None,
    force: bool = False,
    restart: bool = False,
    no_wait: bool = False,
    note: str | None = None,
) -> dict:
    """按 GPU 数或指定节点部署 Job。

    ``gpus`` 省略时使用 ``cluster.scheduling.unit_gpus``（与 CLI 一致）；
    异主租约默认拒绝，设 ``force=true`` 才覆盖。
    """
    if _prefer_http():
        body: dict[str, Any] = {"job": job}
        if nodes:
            body["nodes"] = nodes
        if gpus is not None:
            body["gpus"] = gpus
        if force:
            body["force"] = True
        if restart:
            body["restart"] = True
        if no_wait:
            body["no_wait"] = True
        if note:
            body["note"] = note
        return _http_json_safe("POST", "/api/deploy", body, timeout=1800)
    args = ["deploy", job]
    if nodes:
        args.extend(["--nodes", nodes])
    if gpus is not None:
        args.extend(["--gpus", str(gpus)])
    if force:
        args.append("--force")
    if restart:
        args.append("--restart")
    if no_wait:
        args.append("--no-wait")
    if note:
        args.extend(["--note", note])
    return _cli(args, timeout=1800)


@mcp.tool()
def gc_queue(
    action: str = "list",
    job: str | None = None,
    gpus: int | None = None,
    nodes: str | None = None,
    note: str | None = None,
    schedule: bool = False,
    workload_id: str | None = None,
) -> dict:
    """工作负载队列。action=list|submit|schedule|cancel。

    submit 需要 ``job``；cancel 需要 ``workload_id``；submit 时可 ``schedule=true`` 立即调度。
    """
    action = action.strip().lower()
    if action not in {"list", "submit", "schedule", "cancel"}:
        return {"ok": False, "error": "action 须为 list|submit|schedule|cancel"}
    if action == "list":
        if _prefer_http():
            return _http_json_safe("GET", "/api/queue")
        return _read_state_json("queue.json", {"workloads": []})
    if action == "submit":
        if not job:
            return {"ok": False, "error": "submit 需要 job"}
        if _prefer_http():
            body: dict[str, Any] = {"job": job}
            if gpus is not None:
                body["gpus"] = gpus
            if nodes:
                body["nodes"] = nodes
            if note:
                body["note"] = note
            if schedule:
                body["schedule"] = True
            return _http_json_safe("POST", "/api/queue/submit", body, timeout=1800)
        args = ["queue", "submit", job]
        if gpus is not None:
            args.extend(["--gpus", str(gpus)])
        if nodes:
            args.extend(["--nodes", nodes])
        if note:
            args.extend(["--note", note])
        if schedule:
            args.append("--schedule")
        return _cli(args, timeout=1800)
    if action == "schedule":
        if _prefer_http():
            return _http_json_safe("POST", "/api/queue/schedule", {}, timeout=1800)
        return _cli(["queue", "schedule", "--quiet"], timeout=1800)
    if not workload_id:
        return {"ok": False, "error": "cancel 需要 workload_id"}
    if _prefer_http():
        return _http_json_safe("POST", f"/api/queue/{workload_id}/cancel", {}, timeout=120)
    return _cli(["queue", "cancel", "--id", workload_id], timeout=120)


@mcp.tool()
def gc_lease(
    action: str = "list",
    job: str | None = None,
    nodes: str | None = None,
    gpus: int | None = None,
    note: str | None = None,
    lease_id: str | None = None,
) -> dict:
    """占用租约。action=list|acquire|release。

    acquire 需要 ``job`` + ``nodes``；release 传 ``lease_id`` 或 ``nodes``。
    """
    action = action.strip().lower()
    if action not in {"list", "acquire", "release"}:
        return {"ok": False, "error": "action 须为 list|acquire|release"}
    if action == "list":
        if _prefer_http():
            return _http_json_safe("GET", "/api/leases")
        return _read_state_json("leases.json", {"leases": []})
    if action == "acquire":
        if not job or not nodes:
            return {"ok": False, "error": "acquire 需要 job 与 nodes"}
        if _prefer_http():
            body: dict[str, Any] = {"job": job, "nodes": nodes}
            if gpus is not None:
                body["gpus"] = gpus
            if note:
                body["note"] = note
            return _http_json_safe("POST", "/api/leases", body, timeout=120)
        args = ["lease", "acquire", "--job", job, "--nodes", nodes]
        if gpus is not None:
            args.extend(["--gpus", str(gpus)])
        if note:
            args.extend(["--note", note])
        return _cli(args, timeout=120)
    if not lease_id and not nodes:
        return {"ok": False, "error": "release 需要 lease_id 或 nodes"}
    if lease_id:
        if _prefer_http():
            return _http_json_safe("POST", f"/api/leases/{lease_id}/release", {}, timeout=60)
        return _cli(["lease", "release", "--id", lease_id], timeout=60)
    if _prefer_http():
        # HTTP 仅支持按 id 释放；按节点走 CLI
        return _cli(["lease", "release", "--nodes", nodes], timeout=60)
    return _cli(["lease", "release", "--nodes", nodes], timeout=60)


@mcp.tool()
def gc_diagnose(action: str = "check", ids: list[str] | None = None) -> dict:
    """一键检测 / 安全修复。action=check|repair；repair 可传 ``ids``。"""
    action = action.strip().lower()
    if action not in {"check", "repair"}:
        return {"ok": False, "error": "action 须为 check|repair"}
    if action == "check":
        if _prefer_http():
            return _http_json_safe("GET", "/api/diagnose")
        return _cli_json(["diagnose", "check"], timeout=60)
    if _prefer_http():
        body: dict[str, Any] = {}
        if ids:
            body["ids"] = ids
        return _http_json_safe("POST", "/api/diagnose/repair", body, timeout=120)
    args = ["diagnose", "repair"]
    for fid in ids or []:
        args.extend(["--id", fid])
    return _cli_json(args, timeout=120)


@mcp.tool()
def gc_gateway(action: str = "status", kind: str | None = None) -> dict:
    """统一入口。action=status|get|render|start|stop；start/stop 可传 ``kind``（edge|litellm|all）。"""
    action = action.strip().lower()
    if action not in {"status", "get", "render", "start", "stop"}:
        return {"ok": False, "error": "action 须为 status|get|render|start|stop"}
    if action == "status":
        if _prefer_http():
            return _http_json_safe("GET", "/api/gateway/status")
        return _cli(["gateway", "status"], timeout=30)
    if action == "get":
        if _prefer_http():
            return _http_json_safe("GET", "/api/gateway")
        return _cli(["gateway", "render"], timeout=60)
    if action == "render":
        if _prefer_http():
            return _http_json_safe("POST", "/api/gateway/render", {}, timeout=60)
        return _cli(["gateway", "render"], timeout=60)
    if action == "start":
        if _prefer_http():
            body: dict[str, Any] = {}
            if kind:
                body["kind"] = kind
            return _http_json_safe("POST", "/api/gateway/start", body, timeout=60)
        args = ["gateway", "start"]
        if kind:
            args.extend(["--kind", kind])
        return _cli(args, timeout=60)
    if _prefer_http():
        return _http_json_safe(
            "POST",
            "/api/gateway/stop",
            {"kind": kind or "all"},
            timeout=30,
        )
    return _cli(["gateway", "stop", "--kind", kind or "all"], timeout=30)


@mcp.tool()
def gc_node(
    action: str,
    name: str | None = None,
    host: str | None = None,
    gpus: int | None = None,
    labels: str | None = None,
    reason: str | None = None,
) -> dict:
    """节点生命周期。action=render|cordon|uncordon|add|remove。

    cordon/uncordon/remove/add 需要 ``name``；add 还要 ``host``，可选 ``gpus`` / ``labels``。
    """
    action = action.strip().lower()
    if action not in {"render", "cordon", "uncordon", "add", "remove"}:
        return {"ok": False, "error": "action 须为 render|cordon|uncordon|add|remove"}
    if action == "render":
        if _prefer_http():
            return _http_json_safe("POST", "/api/setup/render", {}, timeout=60)
        return _cli(["node", "render"], timeout=60)
    if not name:
        return {"ok": False, "error": f"{action} 需要 name"}
    if action == "cordon":
        if _prefer_http():
            body: dict[str, Any] = {"name": name}
            if reason:
                body["reason"] = reason
            return _http_json_safe("POST", "/api/node/cordon", body, timeout=60)
        args = ["node", "cordon", name]
        if reason:
            args.extend(["--reason", reason])
        return _cli(args, timeout=60)
    if action == "uncordon":
        if _prefer_http():
            return _http_json_safe("POST", "/api/node/uncordon", {"name": name}, timeout=60)
        return _cli(["node", "uncordon", name], timeout=60)
    if action == "remove":
        if _prefer_http():
            return _http_json_safe("POST", "/api/node/remove", {"name": name}, timeout=60)
        return _cli(["node", "remove", name], timeout=60)
    if not host:
        return {"ok": False, "error": "add 需要 host"}
    if _prefer_http():
        body = {"name": name, "host": host, "gpus": gpus if gpus is not None else 4}
        if labels:
            body["labels"] = labels
        return _http_json_safe("POST", "/api/node/add", body, timeout=60)
    args = ["node", "add", name, "--host", host, "--gpus", str(gpus if gpus is not None else 4)]
    if labels:
        args.extend(["--labels", labels])
    return _cli(args, timeout=60)


@mcp.tool()
def gc_liveness(action: str = "status", node: str | None = None) -> dict:
    """节点存活与快照新鲜度。action=status（读缓存）|check（立即 TCP 探活）。"""
    action = action.strip().lower()
    if action not in {"status", "check"}:
        return {"ok": False, "error": "action 须为 status|check"}
    if _prefer_http():
        if action == "check":
            body: dict[str, Any] = {}
            if node:
                body["node"] = node
            return _http_json_safe("POST", "/api/liveness/check", body, timeout=180)
        return _http_json_safe("GET", "/api/liveness")
    args = ["liveness", action, "--json"]
    if node:
        args.extend(["--node", node])
    return _cli_json(args, timeout=180)


@mcp.tool()
def gc_remote(
    action: str = "status",
    mode: str | None = None,
    port: int | None = None,
    confirm: bool = False,
) -> dict:
    """远程接入（Tailscale / Cloudflare）。action=status|plan|up|down。"""
    action = action.strip().lower()
    if action not in {"status", "plan", "up", "down"}:
        return {"ok": False, "error": "action 须为 status|plan|up|down"}
    if _prefer_http():
        if action in {"status", "plan"}:
            q = f"?plan=1&mode={mode}" if action == "plan" else f"?mode={mode}" if mode else ""
            return _http_json_safe("GET", f"/api/remote{q}")
        body: dict[str, Any] = {"action": action, "mode": mode or "auto", "confirm": bool(confirm)}
        if port is not None:
            body["port"] = port
        return _http_json_safe("POST", "/api/remote/action", body, timeout=60)
    args = ["remote", action, "--mode", mode or "auto"]
    if port is not None:
        args.extend(["--port", str(port)])
    if confirm:
        args.append("--confirm")
    return _cli_json(args, timeout=60)


@mcp.tool()
def gc_exec_preset(node: str, preset: str) -> dict:
    """在单个节点运行只读诊断预设（nvidia-smi / GPU 利用率 / 进程 / 端口 / 磁盘等）。

    :param node: 节点名。
    :param preset: 预设 id，见 gc_api_catalog 或 EXEC_PRESETS。
    """
    preset = str(preset or "").strip()
    meta = EXEC_PRESETS.get(preset)
    if not meta:
        return {
            "ok": False,
            "error": f"未知预设：{preset}",
            "presets": sorted(EXEC_PRESETS),
        }
    if not node:
        return {"ok": False, "error": "需要 node"}
    if _prefer_http():
        return _http_json_safe("POST", "/api/exec", {"preset": preset, "node": node}, timeout=180)
    result = _cli(["exec", "--quiet", "--node", node, "--", meta["command"]], timeout=180)
    return {
        "ok": result["rc"] == 0,
        "node": node,
        "preset": preset,
        "label": meta.get("label"),
        "command": meta.get("command"),
        "result": result,
    }


@mcp.tool()
def gc_exec(command: str, node: str | None = None, nodes: str = "all") -> dict:
    """在工作节点执行 shell 命令；只读诊断优先用 gc_exec_preset。

    客户端应自行按命令风险确认：只读命令可直接执行，修改类命令一次确认，
    rm/kill/reboot/python -c/重定向等危险命令二次确认。
    """
    if _prefer_http():
        body: dict[str, Any] = {"command": command}
        if node:
            body["node"] = node
        else:
            body["nodes"] = nodes
        return _http_json_safe("POST", "/api/exec", body, timeout=300)
    args = ["exec"]
    if node:
        args.extend(["--node", node])
    else:
        args.extend(["--nodes", nodes])
    args.extend(["--", command])
    return _cli(args, timeout=300)


@mcp.tool()
def gc_cli(args: list[str], timeout_sec: int = 600) -> dict:
    """逃生舱：直接执行 gpu-control 子命令，例如 ['node','render']。"""
    if not args:
        return {"ok": False, "error": "需要 args"}
    return _cli(list(args), timeout=timeout_sec)


def main() -> None:
    os.chdir(PROJECT_DIR)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
