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
from paths import CLI, PROJECT_DIR, VENV_PYTHON  # noqa: E402

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
    instructions=(
        "gpu-control 是可迁移的多节点 GPU 控制面。"
        "用工具读/改 config、启停任意 Job（含推理与微调）、腾空节点。"
        "不要假设特定模型或 Job 名；以 get jobs / config 为准。"
        "HTTP 模式下若返回 401，检查 GPU_CONTROL_API_TOKEN。"
        "改配置后建议 gc_cli(['node','render']) 与 gc_doctor()。"
    ),
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

    修改 nodes/路径后请再调用 gc_cli(['node','render']) 与 gc_doctor()。
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
def gc_models(action: str = "list", node: str | None = None, model: str | None = None) -> dict:
    """模型模块。action=list|show|deps|check|verify|paths。"""
    if action not in {"list", "show", "deps", "check", "verify", "paths"}:
        return {"ok": False, "error": "不支持的 action"}
    if _prefer_http() and action in {"deps", "check", "verify"}:
        body: dict[str, Any] = {}
        if node:
            body["node"] = node
        if model and action == "verify":
            body["model"] = model
        return _http_json_safe("POST", f"/api/models/{action}", body, timeout=300)
    if _prefer_http() and action == "list":
        return _http_json_safe("GET", "/api/models")
    args = ["models", action]
    if node:
        args.extend(["--node", node])
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
def gc_deploy(job: str, gpus: int | None = None, nodes: str | None = None) -> dict:
    """按 GPU 数或指定节点部署 Job。

    ``gpus`` 省略时使用 ``cluster.scheduling.unit_gpus``（与 CLI 一致）；
    未配置且未传 ``gpus``/``nodes`` 时会报错。
    """
    if _prefer_http():
        body: dict[str, Any] = {"job": job}
        if nodes:
            body["nodes"] = nodes
        if gpus is not None:
            body["gpus"] = gpus
        return _http_json_safe("POST", "/api/deploy", body, timeout=1800)
    args = ["deploy", job]
    if nodes:
        args.extend(["--nodes", nodes])
    if gpus is not None:
        args.extend(["--gpus", str(gpus)])
    return _cli(args, timeout=1800)


@mcp.tool()
def gc_exec(command: str, node: str | None = None, nodes: str = "all") -> dict:
    """在工作节点执行 shell 命令。"""
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
