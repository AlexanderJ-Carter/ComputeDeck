#!/usr/bin/env python3
"""gpu-control MCP server (stdio) for AI agents / Cursor.

Tools wrap the local CLI by default (run on the control host).
Set GPU_CONTROL_API_BASE=http://127.0.0.1:8787 to prefer HTTP when UI is up.
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
        "用工具读/改 config、启停任意 Job、腾空节点。"
        "不要假设特定模型或 Job 名；以 get jobs / config 为准。"
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
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
    """List HTTP APIs, CLI commands, and MCP tools for gpu-control."""
    return catalog_payload()


@mcp.tool()
def gc_doctor() -> dict:
    """Run configuration doctor (JSON)."""
    if _prefer_http():
        return _http_json("GET", "/api/doctor")
    result = _cli(["doctor", "--json"], timeout=60)
    try:
        payload = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "parse_error": True}
    payload["_cli"] = result
    return payload


@mcp.tool()
def gc_overview(refresh: bool = False) -> dict:
    """Cluster overview: nodes, GPUs, leases, jobs summary."""
    if _prefer_http():
        q = "?refresh=1" if refresh else ""
        return _http_json("GET", f"/api/overview{q}")
    args = ["overview"]
    if not refresh:
        args.append("--cached")
    return _cli(args, timeout=180)


@mcp.tool()
def gc_get_jobs() -> dict:
    """List registered jobs (pluggable workloads)."""
    if _prefer_http():
        return _http_json("GET", "/api/jobs")
    return _cli(["get", "jobs"])


@mcp.tool()
def gc_job_action(
    job: str,
    action: str,
    node: str | None = None,
    nodes: str | None = None,
    extra_args: list[str] | None = None,
) -> dict:
    """Run a job action: start|stop|restart|status|wait."""
    if action not in {"start", "stop", "restart", "status", "wait"}:
        return {"ok": False, "error": "action must be start|stop|restart|status|wait"}
    if _prefer_http():
        body: dict[str, Any] = {"action": action}
        if node:
            body["node"] = node
        elif nodes:
            body["nodes"] = nodes
        if extra_args:
            body["extra_args"] = extra_args
        return _http_json("POST", f"/api/jobs/{job}/action", body, timeout=1200)
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
    """Read config. kind=cluster|nodes|job. For job, pass name=."""
    kind = kind.strip().lower()
    if kind == "cluster":
        if _prefer_http():
            return _http_json("GET", "/api/config/cluster")
        return _read_local("cluster")
    if kind == "nodes":
        if _prefer_http():
            return _http_json("GET", "/api/config/nodes")
        return _read_local("nodes")
    if kind == "job":
        if not name:
            return {"ok": False, "error": "job name required"}
        if _prefer_http():
            return _http_json("GET", f"/api/config/job/{name}")
        return _cli(["describe", "job", name])
    return {"ok": False, "error": "kind must be cluster|nodes|job"}


def _read_local(kind: str) -> dict:
    from paths import cluster_file, nodes_file

    path = cluster_file() if kind == "cluster" else nodes_file()
    if not path.is_file():
        return {"ok": False, "error": f"missing {path}"}
    return {"path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}


@mcp.tool()
def gc_put_config(kind: str, data: dict, name: str | None = None) -> dict:
    """Write config. kind=cluster|nodes|job. For job pass name= and full job JSON in data.

    After nodes/cluster path changes, call gc_cli(['node','render']) then gc_doctor().
    """
    kind = kind.strip().lower()
    if not _prefer_http():
        # write locally then optional render
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
                return {"ok": False, "error": "job name required"}
            LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
            path = LOCAL_JOBS_DIR / f"{name}.json"
            payload = dict(data)
            payload["name"] = name
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return {"ok": True, "path": str(path)}
        return {"ok": False, "error": "kind must be cluster|nodes|job"}

    if kind == "cluster":
        return _http_json("POST", "/api/config/cluster", {"data": data})
    if kind == "nodes":
        return _http_json("POST", "/api/config/nodes", {"data": data})
    if kind == "job":
        if not name:
            return {"ok": False, "error": "job name required"}
        return _http_json("POST", f"/api/config/job/{name}", {"data": data})
    return {"ok": False, "error": "kind must be cluster|nodes|job"}


@mcp.tool()
def gc_models(action: str = "list", node: str | None = None, model: str | None = None) -> dict:
    """Models module: list|show|deps|check|verify|paths."""
    if action not in {"list", "show", "deps", "check", "verify", "paths"}:
        return {"ok": False, "error": "unsupported action"}
    if _prefer_http() and action in {"deps", "check", "verify"}:
        body: dict[str, Any] = {}
        if node:
            body["node"] = node
        if model and action == "verify":
            body["model"] = model
        return _http_json("POST", f"/api/models/{action}", body, timeout=300)
    if _prefer_http() and action in {"list", "show", "paths"}:
        if action == "list":
            return _http_json("GET", "/api/models")
        # show/paths via CLI for richer text
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
    """List or kill GPU compute processes. action=list|kill."""
    if action == "list":
        if _prefer_http() and node:
            return _http_json("POST", "/api/procs", {"node": node, "refresh": True}, timeout=180)
        args = ["procs", "list", "--refresh"]
        if node:
            args.extend(["--node", node])
        return _cli(args, timeout=180)
    if action == "kill":
        if not node:
            return {"ok": False, "error": "node required"}
        if _prefer_http():
            body: dict[str, Any] = {"node": node}
            if all_procs:
                body["all"] = True
            if pids:
                body["pids"] = pids
            if match:
                body["match"] = match
            return _http_json("POST", "/api/procs/kill", body, timeout=300)
        args = ["procs", "kill", "--node", node]
        if all_procs:
            args.append("--all")
        for pid in pids or []:
            args.extend(["--pid", str(pid)])
        if match:
            args.extend(["--match", match])
        return _cli(args, timeout=300)
    return {"ok": False, "error": "action must be list|kill"}


@mcp.tool()
def gc_free_node(
    node: str,
    cordon: bool = False,
    no_kill: bool = False,
    wait: int = 20,
) -> dict:
    """Free a node: stop jobs, kill GPU procs (unless no_kill), release leases, optional cordon."""
    if _prefer_http():
        return _http_json(
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
def gc_deploy(job: str, gpus: int = 4, nodes: str | None = None) -> dict:
    """Deploy a job by GPU count or explicit nodes."""
    if _prefer_http():
        body: dict[str, Any] = {"job": job}
        if nodes:
            body["nodes"] = nodes
        else:
            body["gpus"] = gpus
        return _http_json("POST", "/api/deploy", body, timeout=1800)
    args = ["deploy", job]
    if nodes:
        args.extend(["--nodes", nodes])
    else:
        args.extend(["--gpus", str(gpus)])
    return _cli(args, timeout=1800)


@mcp.tool()
def gc_exec(command: str, node: str | None = None, nodes: str = "all") -> dict:
    """Run a shell command on worker node(s)."""
    if _prefer_http():
        body: dict[str, Any] = {"command": command}
        if node:
            body["node"] = node
        else:
            body["nodes"] = nodes
        return _http_json("POST", "/api/exec", body, timeout=300)
    args = ["exec"]
    if node:
        args.extend(["--node", node])
    else:
        args.extend(["--nodes", nodes])
    args.extend(["--", command])
    return _cli(args, timeout=300)


@mcp.tool()
def gc_cli(args: list[str], timeout_sec: int = 600) -> dict:
    """Escape hatch: run arbitrary gpu-control CLI args, e.g. ['node','render']."""
    if not args:
        return {"ok": False, "error": "args required"}
    return _cli(list(args), timeout=timeout_sec)


def main() -> None:
    os.chdir(PROJECT_DIR)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
