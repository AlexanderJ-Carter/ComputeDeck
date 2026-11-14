#!/usr/bin/env python3
"""Lightweight cluster control-plane web UI (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from paths import (  # noqa: E402
    CLI,
    CONFIG_DIR,
    INVENTORY_FILE,
    LOCAL_DIR,
    LOCAL_JOBS_DIR,
    PROJECT_DIR,
    RESOURCES_FILE,
    SCRIPTS_DIR,
    STATE_DIR,
    VERSION_FILE,
    VENV_PYTHON,
    WEB_DIR,
    cluster_file,
    ensure_runtime_dirs,
    expand_path,
    job_file,
    list_job_files,
    nodes_file,
)

sys.path.insert(0, str(SCRIPTS_DIR))
from render_gateway import (  # noqa: E402
    build_payload as build_gateway_payload,
    render_curl,
    render_litellm_notes,
    render_nginx,
    render_python_client,
    write_artifacts as write_gateway_artifacts,
)
from terminal import open_shell, pump_channel  # noqa: E402
from ws import (  # noqa: E402
    encode_binary,
    encode_close,
    encode_pong,
    encode_text,
    handshake_headers,
    read_frame,
)

STATIC_DIR = WEB_DIR / "static"
ENDPOINTS_FILE = STATE_DIR / "endpoints.json"
ENDPOINT_HEALTH_FILE = STATE_DIR / "endpoint_health.json"
HOST_METRICS_FILE = STATE_DIR / "host_metrics.json"
LEASES_FILE = STATE_DIR / "leases.json"
QUEUE_FILE = STATE_DIR / "queue.json"
PROBE_FILE = STATE_DIR / "probe.json"
NODE_STATE_FILE = STATE_DIR / "node_state.json"
EXEC_DIR = STATE_DIR / "exec"
INVENTORY = INVENTORY_FILE

_action_lock = threading.Lock()
UI_VERSION = (
    VERSION_FILE.read_text(encoding="utf-8").strip()
    if VERSION_FILE.is_file()
    else "0.0.0-dev"
)

EXEC_PRESETS = {
    "nvidia-smi": {
        "label": "nvidia-smi",
        "command": "nvidia-smi",
    },
    "gpu-list": {
        "label": "列出 GPU",
        "command": "nvidia-smi -L",
    },
    "gpu-procs": {
        "label": "GPU 进程",
        "command": (
            "nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory "
            "--format=csv,noheader"
        ),
    },
    "uptime": {
        "label": "负载/内存",
        "command": "uptime; free -h; nproc",
    },
    "disk": {
        "label": "磁盘",
        "command": "df -h / /tmp 2>/dev/null; df -h | head -20",
    },
    "model-procs": {
        "label": "模型进程",
        "command": "pgrep -af 'start_model|sglang|python' | head -40 || true",
    },
    "ports": {
        "label": "监听端口",
        "command": "ss -lntp 2>/dev/null | head -40 || netstat -lntp 2>/dev/null | head -40",
    },
    "torch-cuda": {
        "label": "Torch CUDA",
        "command": (
            "python3 -c \"import importlib.util as u; "
            "s=u.find_spec('torch'); "
            "print('torch missing') if not s else "
            "(__import__('torch'), "
            "print('version', __import__('torch').__version__), "
            "print('cuda', __import__('torch').cuda.is_available(), "
            "'count', __import__('torch').cuda.device_count()))\""
        ),
    },
    "ray-check": {
        "label": "Ray 检测",
        "command": (
            "python3 -c \"import importlib.util as u; "
            "s=u.find_spec('ray'); "
            "print('ray: not installed') if not s else "
            "print('ray:', __import__('ray').__version__)\""
        ),
    },
}


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_nodes() -> list[str]:
    registry = node_registry()
    if registry:
        return sorted(registry.keys())
    nodes: list[str] = []
    in_workers = False
    for line in INVENTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_workers = line == "[gpu_workers]"
            continue
        if not in_workers:
            continue
        name = line.split()[0]
        if not name.startswith("ansible_"):
            nodes.append(name)
    return nodes


def node_registry() -> dict:
    data = _read_json(nodes_file(), {"nodes": {}})
    return data.get("nodes") or {}


def list_jobs() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in list_job_files()
    ]


def collect_exec_results() -> list[dict]:
    if not EXEC_DIR.is_dir():
        return []
    results = []
    for path in sorted(EXEC_DIR.glob("*.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def build_lb_plan(strategy: str = "least_latency") -> dict:
    """Build client-side load-balancing plan from endpoint health."""
    catalog = _read_json(ENDPOINTS_FILE, {"endpoints": [], "urls": []})
    health = _read_json(ENDPOINT_HEALTH_FILE, {})
    by_key = {}
    for row in health.get("results") or []:
        by_key[(row.get("node"), row.get("gpu"))] = row

    ranked = []
    for ep in catalog.get("endpoints") or []:
        h = by_key.get((ep.get("node"), ep.get("gpu"))) or {}
        ok = bool(h.get("ok")) if h else False
        latency = h.get("latency_ms")
        ranked.append({
            "node": ep.get("node"),
            "gpu": ep.get("gpu"),
            "url": ep.get("url"),
            "ok": ok,
            "latency_ms": latency,
            "detail": h.get("detail"),
        })

    healthy = [r for r in ranked if r.get("ok")]
    if strategy == "round_robin":
        ordered = healthy or ranked
    elif strategy == "by_node":
        ordered = sorted(healthy or ranked, key=lambda r: (r.get("node") or "", r.get("gpu") if r.get("gpu") is not None else -1))
    else:  # least_latency
        ordered = sorted(
            healthy or ranked,
            key=lambda r: (0 if r.get("ok") else 1, r.get("latency_ms") if r.get("latency_ms") is not None else 10_000),
        )

    urls = [r["url"] for r in ordered if r.get("url")]
    snippet = (
        "# 健康优先 · 低延迟优先（客户端轮询）\n"
        f"ENDPOINTS = {json.dumps(urls, ensure_ascii=False, indent=2)}\n"
        "\n"
        "def pick(i):\n"
        "    return ENDPOINTS[i % len(ENDPOINTS)] if ENDPOINTS else None\n"
    )
    gateway = _build_gateway_payload(ordered if healthy else ranked)
    return {
        "strategy": strategy,
        "healthy": len(healthy),
        "total": len(ranked),
        "urls": urls,
        "ordered": ordered,
        "snippet": snippet,
        "snippets": gateway.get("snippets") or {"python": snippet},
        "topology": gateway.get("topology"),
        "health_at": health.get("generated_at"),
    }


def _host_port_from_url(url: str) -> tuple[str | None, int | None]:
    rest = url.split("://", 1)[-1]
    host, _, port_s = rest.rpartition(":")
    if not host or not port_s.isdigit():
        return None, None
    return host, int(port_s)


def _build_gateway_payload(upstreams: list[dict] | None = None) -> dict:
    """Load or render gateway map/snippets for UI and /api/gateway."""
    payload = build_gateway_payload()
    if upstreams is None:
        return payload
    cleaned = []
    for u in upstreams:
        url = u.get("url")
        if not url:
            continue
        host, port = _host_port_from_url(url)
        cleaned.append({
            "node": u.get("node"),
            "host": host or u.get("host"),
            "gpu": u.get("gpu"),
            "port": port if port is not None else u.get("port"),
            "url": url,
            "ok": u.get("ok"),
            "latency_ms": u.get("latency_ms"),
        })
    if not cleaned:
        return payload
    key_file = (payload.get("topology") or {}).get("inference_pool", {}).get("api_key_file")
    payload["upstreams"] = cleaned
    payload["snippets"] = {
        "python": render_python_client(cleaned),
        "nginx": render_nginx(cleaned),
        "curl": render_curl(cleaned, key_file),
        "litellm_notes": render_litellm_notes(payload["topology"], cleaned),
    }
    return payload


def state_fingerprint() -> str:
    parts = []
    for path in (
        RESOURCES_FILE,
        LEASES_FILE,
        QUEUE_FILE,
        NODE_STATE_FILE,
        ENDPOINTS_FILE,
        ENDPOINT_HEALTH_FILE,
        HOST_METRICS_FILE,
        PROBE_FILE,
        nodes_file(),
        cluster_file(),
    ):
        if path.is_file():
            st = path.stat()
            parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        else:
            parts.append(f"{path.name}:missing")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_overview() -> dict:
    resources = _read_json(RESOURCES_FILE, {})
    registry = node_registry()
    leases = _read_json(LEASES_FILE, {"leases": []}).get("leases", [])
    lease_by_node = {
        node: lease for lease in leases for node in lease.get("nodes", [])
    }
    cordoned = _read_json(NODE_STATE_FILE, {"cordoned": {}}).get("cordoned", {})
    endpoint_health = _read_json(ENDPOINT_HEALTH_FILE, {})
    host_metrics = _read_json(HOST_METRICS_FILE, {})
    host_by_node = host_metrics.get("nodes") or {}
    nodes = []
    free_total = 0
    used_total = 0
    for name in inventory_nodes():
        node = resources.get(name, {"node": name, "gpus": [], "gpu_count": 0})
        meta = registry.get(name, {})
        gpus = node.get("gpus", [])
        free = node.get("free_count", sum(1 for g in gpus if g.get("status") == "free"))
        used = node.get("occupied_count", sum(1 for g in gpus if g.get("status") == "occupied"))
        free_total += free
        used_total += used
        lease = lease_by_node.get(name)
        state = "cordoned" if name in cordoned else ("busy" if used else "free")
        host = host_by_node.get(name) or {}
        nodes.append({
            "name": name,
            "host": meta.get("ansible_host") or node.get("ansible_host"),
            "labels": meta.get("labels") or [],
            "role": meta.get("role"),
            "note": meta.get("note"),
            "free": free,
            "used": used,
            "total": node.get("gpu_count", len(gpus)) or meta.get("gpus_per_node", 4),
            "gpus": gpus,
            "lease": lease,
            "cordoned": name in cordoned,
            "cordon": cordoned.get(name),
            "state": state,
            "collected_at": node.get("collected_at"),
            "cpu_percent": host.get("cpu_percent"),
            "load1": host.get("load1"),
            "mem_used_mb": host.get("mem_used_mb"),
            "mem_total_mb": host.get("mem_total_mb"),
            "mem_avail_mb": host.get("mem_avail_mb"),
            "cores": host.get("cores"),
            "host_ok": host.get("ok"),
        })
    queue = _read_json(QUEUE_FILE, {"workloads": []}).get("workloads", [])
    pending = sum(1 for wl in queue if wl.get("status") == "pending")
    probe = _read_json(PROBE_FILE, {})
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": UI_VERSION,
        "etag": state_fingerprint(),
        "summary": {
            "nodes": len(nodes),
            "gpus_free": free_total,
            "gpus_used": used_total,
            "gpus_total": free_total + used_total,
            "leases": len(leases),
            "jobs": len(list_jobs()),
            "queue_pending": pending,
            "cordoned": len(cordoned),
            "probe_ok": probe.get("ok_count"),
            "probe_fail": probe.get("fail_count"),
            "api_ok": endpoint_health.get("ok_count"),
            "api_fail": endpoint_health.get("fail_count"),
            "api_total": endpoint_health.get("total"),
            "host_metrics_at": host_metrics.get("generated_at"),
        },
        "nodes": nodes,
        "leases": leases,
        "jobs": list_jobs(),
        "queue": queue,
        "probe": probe,
        "cordoned": cordoned,
        "cluster": _read_json(cluster_file(), {}),
        "endpoints": _read_json(ENDPOINTS_FILE, {}),
        "endpoint_health": endpoint_health,
        "host_metrics": host_metrics,
        "lb": build_lb_plan("least_latency"),
        "exec_presets": [
            {"id": key, "label": meta["label"]} for key, meta in EXEC_PRESETS.items()
        ],
        "notes": {
            "ray": (
                "Core 不绑定 Ray。需要跨节点训练时再单独验证 NCCL/Ray。"
            ),
            "strategy": (
                "通用控制面：连通 → 看卡 → 腾空/隔离 → 跑任意 Job。"
                "推理 API / 模型缓存是可选模块。"
            ),
            "innovation": (
                "Core + Modules：并行 SSH 质量、Job 插件、可选 models/inference。"
            ),
        },
    }


def run_cli(args: list[str], timeout: int = 900) -> dict:
    cmd = [str(VENV_PYTHON), str(CLI), *args]
    completed = subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={
            **os.environ,
            "PATH": f"{PROJECT_DIR / '.venv' / 'bin'}:{os.environ.get('PATH', '')}",
        },
    )
    return {
        "cmd": cmd,
        "rc": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


SETUP_GUIDE = [
    {
        "id": "clone",
        "title": "准备控制面（Core）",
        "body": "安装依赖、生成 config/local、把 CLI 放进 PATH。这是通用控制面，不绑定某个模型。",
        "cli": [
            "uv venv .venv && uv pip install -r requirements.txt",
            "./examples/quickstart.sh",
            "gpu-control doctor",
        ],
    },
    {
        "id": "ssh",
        "title": "配置 SSH",
        "body": "在「配置」向导点选私钥、填用户/端口，保存并测试连通。",
        "cli": [
            "gpu-control ui --background",
            "# 或：ssh -i <key> -p <port> <user>@<host> hostname",
        ],
    },
    {
        "id": "nodes",
        "title": "登记节点",
        "body": "通用能力：节点清单 → inventory → 远程命令 / GPU 快照 / 腾空隔离。",
        "cli": [
            "gpu-control node render",
            "gpu-control get nodes",
            "gpu-control exec --nodes all -- 'nvidia-smi -L'",
        ],
    },
    {
        "id": "job",
        "title": "注册工作负载（可选）",
        "body": "任意 Job：推理服务、评测、预处理均可。复制 example 到 config/local/jobs/。",
        "cli": [
            "cp config/examples/decider.example.json config/local/jobs/myjob.json",
            "gpu-control status myjob --nodes all",
        ],
    },
    {
        "id": "models",
        "title": "模型工具（可选模块）",
        "body": "需要权重管理时再配 models.json：缓存检查 / 拉取。不做模型部署也能用 Core。",
        "cli": [
            "cp config/examples/models.example.json config/local/models.json",
            "gpu-control models check",
        ],
    },
]


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def discover_ssh_keys() -> list[dict]:
    home = Path.home() / ".ssh"
    found = []
    if not home.is_dir():
        return found
    skip_names = {"config", "known_hosts", "authorized_keys", "known_hosts.old"}
    for path in sorted(home.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".pub") or name in skip_names or name.startswith("."):
            continue
        if name.endswith((".sh", ".bash", ".py", ".txt", ".md")):
            continue
        head = path.read_bytes()[:80]
        if b"PRIVATE KEY" not in head and not name.startswith(("id_", "keys_")):
            continue
        mode = path.stat().st_mode & 0o777
        found.append({"path": str(path), "mode": oct(mode), "size": path.stat().st_size})
    return found


def build_setup_status() -> dict:
    ensure_runtime_dirs()
    cluster = _read_json(cluster_file(), {})
    nodes_doc = _read_json(nodes_file(), {"defaults": {}, "nodes": {}})
    defaults = nodes_doc.get("defaults") or {}
    key_raw = str(defaults.get("ansible_ssh_private_key_file") or "")
    key_path = expand_path(key_raw) if key_raw else ""
    api_key_raw = str((cluster.get("api") or {}).get("api_key_file") or "")
    api_key_path = expand_path(api_key_raw) if api_key_raw else ""

    jobs = []
    for path in list_job_files():
        job = json.loads(path.read_text(encoding="utf-8"))
        jobs.append({
            "name": job.get("name") or path.stem,
            "path": str(path),
            "python": job.get("python"),
            "script": job.get("script"),
            "python_exists": Path(expand_path(str(job.get("python") or ""))).exists()
            if job.get("python")
            else False,
            "script_exists": Path(expand_path(str(job.get("script") or ""))).exists()
            if job.get("script")
            else False,
        })

    checklist = [
        {"id": "cluster", "ok": bool(cluster), "label": "cluster.json"},
        {"id": "nodes", "ok": bool(nodes_doc.get("nodes")), "label": "nodes.json"},
        {
            "id": "ssh_key",
            "ok": bool(key_path) and Path(key_path).is_file(),
            "label": "SSH 私钥",
            "detail": key_path or "(未配置)",
        },
        {
            "id": "api_key",
            "ok": (not api_key_path) or Path(api_key_path).is_file(),
            "label": "API key 文件",
            "detail": api_key_path or "(可选)",
        },
        {"id": "inventory", "ok": INVENTORY_FILE.is_file(), "label": "inventory.ini"},
        {"id": "jobs", "ok": bool(jobs), "label": "已注册 jobs"},
        {
            "id": "ansible",
            "ok": (PROJECT_DIR / ".venv" / "bin" / "ansible-playbook").is_file(),
            "label": "Ansible venv",
        },
    ]
    return {
        "project": str(PROJECT_DIR),
        "config_local": str(LOCAL_DIR),
        "config_examples": str(CONFIG_DIR / "examples"),
        "cluster_path": str(cluster_file()),
        "nodes_path": str(nodes_file()),
        "cluster": cluster,
        "nodes": nodes_doc,
        "jobs": jobs,
        "ssh_keys": discover_ssh_keys(),
        "checklist": checklist,
        "guide": SETUP_GUIDE,
        "ready": all(item["ok"] for item in checklist),
    }


def _ssh_one(
    *,
    name: str,
    host: str,
    user: str,
    port: int,
    key: str,
    remote: str = "hostname",
    timeout: int = 8,
) -> dict:
    if not host:
        return {"node": name, "host": host, "ok": False, "latency_ms": None, "detail": "缺少 ansible_host"}
    if not key or not Path(key).is_file():
        return {
            "node": name,
            "host": host,
            "ok": False,
            "latency_ms": None,
            "detail": f"SSH 私钥不存在: {key or '(空)'}",
        }
    cmd = [
        "ssh",
        "-i",
        key,
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout={}".format(timeout),
        "-o",
        "ConnectionAttempts=1",
        f"{user}@{host}",
        remote,
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd, text=True, capture_output=True, check=False, timeout=timeout + 5
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = (completed.stdout or completed.stderr or "").strip()[:240] or f"rc={completed.returncode}"
        ok = completed.returncode == 0
    except subprocess.TimeoutExpired:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = "超时"
        ok = False
    return {
        "node": name,
        "host": host,
        "ok": ok,
        "latency_ms": latency_ms,
        "detail": detail,
    }


def test_ssh_connectivity(*, node: str | None = None, timeout: int = 8, remote: str = "hostname") -> dict:
    nodes_doc = _read_json(nodes_file(), {"defaults": {}, "nodes": {}})
    defaults = nodes_doc.get("defaults") or {}
    user = str(defaults.get("ansible_user") or "root")
    port = int(defaults.get("ansible_port") or 22)
    key = expand_path(str(defaults.get("ansible_ssh_private_key_file") or ""))
    targets = nodes_doc.get("nodes") or {}
    if node:
        if node not in targets:
            return {"ok": False, "error": f"未知节点: {node}", "results": []}
        targets = {node: targets[node]}

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, len(targets) or 1)) as pool:
        futs = [
            pool.submit(
                _ssh_one,
                name=name,
                host=str(meta.get("ansible_host") or ""),
                user=user,
                port=port,
                key=key,
                remote=remote,
                timeout=timeout,
            )
            for name, meta in sorted(targets.items())
        ]
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: r.get("node") or "")
    latencies = [r["latency_ms"] for r in results if r.get("ok") and r.get("latency_ms") is not None]
    ok = bool(results) and all(item["ok"] for item in results)
    payload = {
        "ok": ok,
        "results": results,
        "user": user,
        "port": port,
        "key": key,
        "ok_count": sum(1 for r in results if r.get("ok")),
        "total": len(results),
        "latency_ms": {
            "avg": int(sum(latencies) / len(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
            "min": min(latencies) if latencies else None,
        },
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "ssh_quality.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


WORKER_CHECK_CMD = (
    "echo HOST=$(hostname); "
    "nvidia-smi -L 2>/dev/null | head -8 || echo nvidia-smi-missing; "
    "echo ---; "
    "python3 -c \"import importlib.util as u; "
    "print('torch-missing') if not u.find_spec('torch') else "
    "(__import__('torch'), print('torch', __import__('torch').__version__, "
    "'cuda', __import__('torch').cuda.is_available()))\"; "
    "echo ---; "
    "df -h / /tmp 2>/dev/null | head -5; "
    "echo ---; "
    "uptime"
)


def build_quality_report() -> dict:
    ssh = _read_json(STATE_DIR / "ssh_quality.json", {})
    health = _read_json(ENDPOINT_HEALTH_FILE, {})
    probe = _read_json(PROBE_FILE, {})
    resources = _read_json(RESOURCES_FILE, {})

    ssh_ok = int(ssh.get("ok_count") or 0)
    ssh_total = int(ssh.get("total") or 0)
    api_ok = int(health.get("ok_count") or 0)
    api_total = int(health.get("total") or 0)
    probe_ok = int(probe.get("ok_count") or 0)
    probe_fail = int(probe.get("fail_count") or 0)
    probe_total = probe_ok + probe_fail

    gpu_free = 0
    gpu_total = 0
    for node in resources.values() if isinstance(resources, dict) else []:
        if not isinstance(node, dict) or "gpus" not in node:
            continue
        gpu_total += int(node.get("gpu_count") or len(node.get("gpus") or []))
        gpu_free += int(node.get("free_count") or 0)

    scores = []
    if ssh_total:
        scores.append(100.0 * ssh_ok / ssh_total)
    if api_total:
        scores.append(100.0 * api_ok / api_total)
    if probe_total:
        scores.append(100.0 * probe_ok / probe_total)
    score = int(sum(scores) / len(scores)) if scores else 0

    grade = "优秀" if score >= 90 else "良好" if score >= 75 else "一般" if score >= 50 else "较差"
    return {
        "score": score,
        "grade": grade,
        "ssh": ssh,
        "api": {
            "ok": api_ok,
            "total": api_total,
            "mode": health.get("mode"),
            "generated_at": health.get("generated_at"),
            "latency_avg_ms": (
                int(
                    sum(r.get("latency_ms") or 0 for r in (health.get("results") or []) if r.get("ok"))
                    / max(1, sum(1 for r in (health.get("results") or []) if r.get("ok")))
                )
                if any(r.get("ok") for r in (health.get("results") or []))
                else None
            ),
        },
        "mesh": {"ok": probe_ok, "fail": probe_fail, "total": probe_total},
        "gpus": {"free": gpu_free, "total": gpu_total, "used": max(0, gpu_total - gpu_free)},
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def run_oneclick(action: str, body: dict | None = None) -> dict:
    body = body or {}
    started = time.perf_counter()
    steps: list[dict] = []

    def step(name: str, fn):
        t0 = time.perf_counter()
        try:
            payload = fn()
            ok = True
            if isinstance(payload, dict) and "ok" in payload:
                ok = bool(payload.get("ok"))
            elif isinstance(payload, dict) and "rc" in payload:
                ok = int(payload.get("rc") or 0) == 0
            steps.append({
                "name": name,
                "ok": ok,
                "ms": int((time.perf_counter() - t0) * 1000),
                "payload": payload,
            })
            return payload
        except Exception as exc:  # noqa: BLE001
            steps.append({
                "name": name,
                "ok": False,
                "ms": int((time.perf_counter() - t0) * 1000),
                "error": str(exc),
            })
            return None

    def worker_check():
        result = run_cli(["exec", "--quiet", "--nodes", "all", "--", WORKER_CHECK_CMD], timeout=180)
        results = collect_exec_results()
        return {
            "ok": bool(results) and all(int(r.get("rc", 1)) == 0 for r in results),
            "results": results,
            "result": result,
        }

    def doctor_json():
        raw = run_cli(["doctor", "--json"], timeout=30)
        try:
            payload = json.loads(raw["stdout"] or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "error": "doctor 解析失败", "result": raw}
        return payload

    if action == "connect":
        step("SSH 连通", lambda: test_ssh_connectivity(timeout=int(body.get("timeout") or 6)))
    elif action == "control-bootstrap":
        step("运行时目录", lambda: {"ok": True, "dirs": str(STATE_DIR)} if (ensure_runtime_dirs() or True) else {"ok": False})
        step("渲染 inventory", lambda: run_cli(["node", "render"], timeout=60))
        step("Doctor 自检", doctor_json)
        step("CLI 可用性", lambda: {"ok": CLI.is_file(), "path": str(CLI)})
    elif action == "worker-check":
        step("SSH 连通", lambda: test_ssh_connectivity(timeout=6))
        step("被控节点体检", worker_check)
    elif action == "refresh":
        step("刷新 GPU", lambda: run_cli(["gpu", "refresh", "--quiet"], timeout=180))
    elif action == "health":
        step(
            "API 健康",
            lambda: run_cli(
                ["health", "--mode", body.get("mode") or "local"],
                timeout=180,
            ),
        )
    elif action == "deploy":
        job = (body.get("job") or "decider").strip()
        gpus = int(body.get("gpus") or 4)
        if body.get("nodes"):
            args = ["deploy", job, "--nodes", str(body["nodes"])]
        else:
            args = ["deploy", job, "--gpus", str(gpus)]
        step("一键部署", lambda: run_cli(args, timeout=900))
    elif action == "full-sync":
        step("SSH 连通", lambda: test_ssh_connectivity(timeout=6))
        step("刷新 GPU", lambda: run_cli(["gpu", "refresh", "--quiet"], timeout=180))
        step("API 健康", lambda: run_cli(["health", "--mode", "local"], timeout=180))
    elif action == "watchdog":
        step(
            "启动看门狗",
            lambda: run_cli(
                ["watchdog", "--job", body.get("job") or "decider", "--background"],
                timeout=30,
            ),
        )
    elif action == "model-check":
        def model_check():
            result = run_cli(["models", "check"], timeout=180)
            results = collect_exec_results()
            return {
                "ok": int(result.get("rc") or 0) == 0,
                "result": result,
                "results": results,
            }

        step("模型缓存检查", model_check)
    else:
        return {"ok": False, "error": f"未知动作: {action}", "steps": []}

    ok = all(s.get("ok") for s in steps) if steps else False
    return {
        "ok": ok,
        "action": action,
        "steps": steps,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "quality": build_quality_report(),
        "overview": build_overview(),
    }


def build_python_command(body: dict) -> str:
    python = (body.get("python") or "python3").strip() or "python3"
    script = (body.get("script") or "").strip()
    code = (body.get("code") or "").strip()
    args = (body.get("args") or "").strip()
    cwd = (body.get("cwd") or "").strip()
    cuda = (body.get("cuda") or "").strip()
    background = bool(body.get("background"))
    log_path = (body.get("log") or "/tmp/gpu-control-run.log").strip()

    if not script and not code:
        raise ValueError("script or code required")
    if script and code:
        raise ValueError("provide either script or code, not both")

    env_prefix = ""
    if cuda:
        env_prefix += f"CUDA_VISIBLE_DEVICES={shlex.quote(cuda)} "
    if cwd:
        env_prefix = f"cd {shlex.quote(cwd)} && {env_prefix}"

    if code:
        # one-liner / short snippet
        remote = f"{env_prefix}{shlex.quote(python)} -c {shlex.quote(code)}"
    else:
        remote = f"{env_prefix}{shlex.quote(python)} {shlex.quote(script)}"
        if args:
            remote += f" {args}"

    if background:
        remote = f"nohup bash -lc {shlex.quote(remote)} > {shlex.quote(log_path)} 2>&1 & echo PID:$! LOG:{log_path}"
    return remote


class Handler(BaseHTTPRequestHandler):
    server_version = f"gpu-control-ui/{UI_VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str,
        *,
        cache_control: str = "no-store",
        etag: str | None = None,
        extra_headers: dict | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-GPU-Control-Version", UI_VERSION)
        if etag:
            self.send_header("ETag", etag)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if code != 304:
            self.wfile.write(body)

    def _json(self, code: int, payload, *, etag: str | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8", etag=etag)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _handle_terminal_ws(self, parsed) -> None:
        qs = parse_qs(parsed.query)
        node = (qs.get("node") or [""])[0].strip()
        cols = int((qs.get("cols") or ["120"])[0])
        rows = int((qs.get("rows") or ["32"])[0])
        key = self.headers.get("Sec-WebSocket-Key")
        if not node:
            self._json(400, {"error": "node 必填"})
            return
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            self._json(400, {"error": "需要 WebSocket Upgrade"})
            return

        try:
            session = open_shell(node, cols=cols, rows=rows)
        except Exception as exc:  # noqa: BLE001
            self._json(503, {"error": f"SSH 终端连接失败: {exc}"})
            return

        self.send_response(101, "Switching Protocols")
        for header, value in handshake_headers(key):
            self.send_header(header, value)
        self.end_headers()

        stop = threading.Event()
        write_lock = threading.Lock()

        def ws_send(data: bytes) -> None:
            with write_lock:
                try:
                    self.wfile.write(data)
                    self.wfile.flush()
                except Exception:  # noqa: BLE001
                    stop.set()

        ws_send(encode_text(json.dumps({
            "type": "status",
            "ok": True,
            "node": session.node.name,
            "host": session.node.host,
            "user": session.node.user,
        }, ensure_ascii=False)))

        def on_pty(chunk: bytes) -> None:
            ws_send(encode_binary(chunk))

        pump = threading.Thread(
            target=pump_channel,
            args=(session, on_pty, stop),
            daemon=True,
        )
        pump.start()

        try:
            while not stop.is_set():
                frame = read_frame(self.rfile)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping
                    ws_send(encode_pong(payload))
                    continue
                if opcode in {0x1, 0x2}:  # text / binary
                    if opcode == 0x1:
                        text = payload.decode("utf-8", errors="replace")
                        if text.startswith("{"):
                            try:
                                msg = json.loads(text)
                            except json.JSONDecodeError:
                                session.write(payload)
                                continue
                            msg_type = msg.get("type")
                            if msg_type == "resize":
                                session.resize(int(msg.get("cols") or cols), int(msg.get("rows") or rows))
                                continue
                            if msg_type == "input":
                                session.write(str(msg.get("data") or "").encode("utf-8"))
                                continue
                            # 未知控制帧直接忽略，避免把 JSON 写进远端 shell
                            continue
                        session.write(payload)
                    else:
                        session.write(payload)
        finally:
            stop.set()
            session.close()
            try:
                ws_send(encode_close())
            except Exception:  # noqa: BLE001
                pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/ws/terminal":
            self._handle_terminal_ws(parsed)
            return

        if path in {"/", "/index.html"}:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace("__UI_VERSION__", UI_VERSION)
            body = html.encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
                self._json(404, {"error": "not found"})
                return
            content_type = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
            }.get(target.suffix, "application/octet-stream")
            raw = target.read_bytes()
            etag = hashlib.sha1(raw).hexdigest()[:16]
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", content_type, cache_control="public, max-age=60", etag=etag)
                return
            self._send(
                200,
                raw,
                content_type,
                cache_control="public, max-age=60",
                etag=etag,
            )
            return

        if path == "/api/health":
            self._json(200, {"ok": True, "service": "gpu-control-ui", "version": UI_VERSION})
            return

        if path == "/api/doctor":
            result = run_cli(["doctor", "--json"], timeout=30)
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "doctor parse failed", "result": result}
            self._json(200, payload)
            return

        if path == "/api/overview":
            qs = parse_qs(parsed.query)
            if qs.get("refresh", ["0"])[0] in {"1", "true", "yes"}:
                with _action_lock:
                    result = run_cli(["gpu", "refresh", "--quiet"], timeout=180)
                if result["rc"] != 0:
                    self._json(500, {"error": "refresh failed", "result": result})
                    return
            etag = state_fingerprint()
            if self.headers.get("If-None-Match") == etag and qs.get("refresh", ["0"])[0] not in {
                "1",
                "true",
                "yes",
            }:
                self._send(
                    304,
                    b"",
                    "application/json; charset=utf-8",
                    etag=etag,
                )
                return
            self._json(200, build_overview(), etag=etag)
            return

        if path == "/api/endpoints/health":
            self._json(200, _read_json(ENDPOINT_HEALTH_FILE, {}))
            return

        if path == "/api/jobs":
            self._json(200, {"jobs": list_jobs()})
            return

        if path == "/api/leases":
            self._json(200, _read_json(LEASES_FILE, {"leases": []}))
            return

        if path == "/api/queue":
            self._json(200, _read_json(QUEUE_FILE, {"workloads": []}))
            return

        if path == "/api/probe":
            self._json(200, _read_json(PROBE_FILE, {}))
            return

        if path == "/api/setup":
            self._json(200, build_setup_status())
            return

        if path == "/api/quality":
            self._json(200, build_quality_report())
            return

        if path == "/api/metrics":
            self._json(200, _read_json(HOST_METRICS_FILE, {"results": [], "nodes": {}}))
            return

        if path == "/api/endpoints/lb":
            qs = parse_qs(parsed.query)
            strategy = (qs.get("strategy") or ["least_latency"])[0]
            self._json(200, build_lb_plan(strategy))
            return

        if path == "/api/gateway":
            self._json(200, _build_gateway_payload())
            return

        if path == "/api/setup/guide":
            self._json(200, {"guide": SETUP_GUIDE})
            return

        if path == "/api/config/cluster":
            self._json(200, {"path": str(cluster_file()), "data": _read_json(cluster_file(), {})})
            return

        if path == "/api/config/nodes":
            self._json(200, {"path": str(nodes_file()), "data": _read_json(nodes_file(), {})})
            return

        if path.startswith("/api/config/job/"):
            name = path[len("/api/config/job/"):].strip("/")
            path_job = job_file(name)
            if not path_job:
                self._json(404, {"error": f"job not found: {name}"})
                return
            self._json(200, {"path": str(path_job), "data": json.loads(path_job.read_text(encoding="utf-8"))})
            return

        self._json(404, {"error": f"unknown path: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/api/resources/refresh":
            with _action_lock:
                result = run_cli(["gpu", "refresh", "--quiet"], timeout=180)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path.startswith("/api/jobs/") and path.endswith("/action"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                self._json(400, {"error": "bad job action path"})
                return
            job = parts[2]
            action = body.get("action")
            if action not in {"start", "stop", "restart", "status", "wait"}:
                self._json(400, {"error": "action must be start|stop|restart|status|wait"})
                return
            args = [action, job]
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            with _action_lock:
                result = run_cli(args, timeout=1200)
            self._json(200 if result["rc"] == 0 else 500, {"result": result})
            return

        if path == "/api/deploy":
            job = body.get("job")
            if not job:
                self._json(400, {"error": "job required"})
                return
            args = ["deploy", job]
            if body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            else:
                args.extend(["--gpus", str(body.get("gpus", 4))])
            if body.get("restart"):
                args.append("--restart")
            if body.get("note"):
                args.extend(["--note", body["note"]])
            with _action_lock:
                result = run_cli(args, timeout=1800)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/leases":
            job = body.get("job")
            nodes = body.get("nodes")
            if not job or not nodes:
                self._json(400, {"error": "job and nodes required"})
                return
            args = ["lease", "acquire", "--job", job, "--nodes", nodes]
            if body.get("gpus"):
                args.extend(["--gpus", str(body["gpus"])])
            if body.get("note"):
                args.extend(["--note", body["note"]])
            with _action_lock:
                result = run_cli(args)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "leases": _read_json(LEASES_FILE, {"leases": []})},
            )
            return

        if path.startswith("/api/leases/") and path.endswith("/release"):
            lease_id = path.strip("/").split("/")[2]
            with _action_lock:
                result = run_cli(["lease", "release", "--id", lease_id])
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "leases": _read_json(LEASES_FILE, {"leases": []})},
            )
            return

        if path == "/api/queue/submit":
            job = body.get("job")
            if not job:
                self._json(400, {"error": "job required"})
                return
            args = ["queue", "submit", job, "--gpus", str(body.get("gpus", 4))]
            if body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            if body.get("note"):
                args.extend(["--note", body["note"]])
            if body.get("schedule"):
                args.append("--schedule")
            with _action_lock:
                result = run_cli(args, timeout=1800)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/queue/schedule":
            with _action_lock:
                result = run_cli(["queue", "schedule", "--quiet"], timeout=1800)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path.startswith("/api/queue/") and path.endswith("/cancel"):
            workload_id = path.strip("/").split("/")[2]
            with _action_lock:
                result = run_cli(["queue", "cancel", "--id", workload_id])
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/probe/run":
            with _action_lock:
                result = run_cli(["probe", "--quiet"], timeout=180)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/endpoints/health/run":
            # Fan-out probe does not need the Ansible action lock.
            result = run_cli(["health", "--json"], timeout=60)
            health = _read_json(ENDPOINT_HEALTH_FILE, {})
            self._json(
                200,
                {"result": result, "health": health, "overview": build_overview()},
            )
            return

        if path == "/api/gateway/render":
            payload = _build_gateway_payload()
            written = write_gateway_artifacts(payload)
            self._json(200, {**payload, "written": written})
            return

        if path == "/api/free":
            node = body.get("node")
            if not node:
                self._json(400, {"error": "node required"})
                return
            args = ["free", "--node", node]
            if body.get("job"):
                args.extend(["--job", body["job"]])
            if body.get("cordon"):
                args.append("--cordon")
            if body.get("reason"):
                args.extend(["--reason", body["reason"]])
            with _action_lock:
                result = run_cli(args, timeout=900)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/uncordon":
            name = body.get("name")
            if not name:
                self._json(400, {"error": "name required"})
                return
            with _action_lock:
                result = run_cli(["node", "uncordon", name])
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/cordon":
            name = body.get("name")
            if not name:
                self._json(400, {"error": "name required"})
                return
            args = ["node", "cordon", name]
            if body.get("reason"):
                args.extend(["--reason", body["reason"]])
            with _action_lock:
                result = run_cli(args)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/add":
            name = (body.get("name") or "").strip()
            host = (body.get("host") or "").strip()
            if not name or not host:
                self._json(400, {"error": "name and host required"})
                return
            args = ["node", "add", name, "--host", host, "--gpus", str(body.get("gpus", 4))]
            if body.get("labels"):
                args.extend(["--labels", body["labels"]])
            with _action_lock:
                result = run_cli(args)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/remove":
            name = (body.get("name") or "").strip()
            if not name:
                self._json(400, {"error": "name required"})
                return
            with _action_lock:
                result = run_cli(["node", "remove", name])
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/exec":
            command = (body.get("command") or "").strip()
            preset = body.get("preset")
            if preset:
                if preset not in EXEC_PRESETS:
                    self._json(400, {"error": f"unknown preset: {preset}"})
                    return
                command = EXEC_PRESETS[preset]["command"]
            if not command:
                self._json(400, {"error": "command or preset required"})
                return
            args = ["exec", "--quiet"]
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            else:
                args.extend(["--nodes", "all"])
            args.extend(["--", command])
            with _action_lock:
                result = run_cli(args, timeout=300)
                results = collect_exec_results()
            # even if some hosts fail, still return structured results
            self._json(
                200 if results else (200 if result["rc"] == 0 else 500),
                {
                    "command": command,
                    "results": results,
                    "result": result,
                },
            )
            return

        if path == "/api/run-python":
            try:
                command = build_python_command(body)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            args = ["exec", "--quiet"]
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            else:
                args.extend(["--nodes", "all"])
            args.extend(["--", command])
            with _action_lock:
                result = run_cli(args, timeout=600)
                results = collect_exec_results()
            self._json(
                200 if results else (200 if result["rc"] == 0 else 500),
                {
                    "command": command,
                    "results": results,
                    "result": result,
                },
            )
            return

        if path == "/api/config/cluster":
            data = body.get("data")
            if not isinstance(data, dict):
                self._json(400, {"error": "data object required"})
                return
            target = cluster_file(write=True)
            with _action_lock:
                _atomic_write_json(target, data)
                root = PROJECT_DIR / "cluster.json"
                if not root.exists():
                    root.symlink_to(Path("config/local/cluster.json"))
            self._json(200, {"ok": True, "path": str(target), "setup": build_setup_status()})
            return

        if path == "/api/config/nodes":
            data = body.get("data")
            if not isinstance(data, dict) or "nodes" not in data:
                self._json(400, {"error": "data with nodes required"})
                return
            target = nodes_file(write=True)
            with _action_lock:
                _atomic_write_json(target, data)
                root = PROJECT_DIR / "nodes.json"
                if not root.exists():
                    root.symlink_to(Path("config/local/nodes.json"))
                render = run_cli(["node", "render"], timeout=60)
            self._json(
                200 if render["rc"] == 0 else 500,
                {"ok": render["rc"] == 0, "path": str(target), "render": render, "setup": build_setup_status()},
            )
            return

        if path.startswith("/api/config/job/"):
            name = path[len("/api/config/job/"):].strip("/")
            data = body.get("data")
            if not isinstance(data, dict):
                self._json(400, {"error": "data object required"})
                return
            data["name"] = data.get("name") or name
            target = job_file(name) or (LOCAL_JOBS_DIR / f"{name}.json")
            with _action_lock:
                _atomic_write_json(target, data)
            self._json(200, {"ok": True, "path": str(target), "setup": build_setup_status()})
            return

        if path == "/api/setup/test-ssh":
            with _action_lock:
                payload = test_ssh_connectivity(node=(body.get("node") or None))
            self._json(200, payload)
            return

        if path == "/api/actions/oneclick":
            action = (body.get("action") or "").strip()
            if not action:
                self._json(400, {"error": "action 必填"})
                return
            with _action_lock:
                payload = run_oneclick(action, body)
            self._json(200, payload)
            return

        if path == "/api/metrics/refresh":
            with _action_lock:
                completed = subprocess.run(
                    [str(VENV_PYTHON), str(PROJECT_DIR / "scripts" / "collect_host_metrics.py")],
                    cwd=PROJECT_DIR,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
            try:
                report = json.loads(completed.stdout or "{}")
            except json.JSONDecodeError:
                report = {
                    "ok": False,
                    "error": "metrics parse failed",
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            self._json(
                200 if completed.returncode == 0 else 500,
                {
                    "result": {"rc": completed.returncode, "stderr": completed.stderr},
                    "metrics": report,
                    "overview": build_overview(),
                },
            )
            return

        if path == "/api/setup/render":
            with _action_lock:
                result = run_cli(["node", "render"], timeout=60)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "setup": build_setup_status()},
            )
            return

        if path == "/api/setup/apply-ssh":
            key = expand_path(str(body.get("key") or "").strip())
            user = (body.get("user") or "").strip()
            port = body.get("port")
            if not key:
                self._json(400, {"error": "key required"})
                return
            target = nodes_file(write=True)
            doc = _read_json(target, {"defaults": {}, "nodes": {}})
            defaults = dict(doc.get("defaults") or {})
            defaults["ansible_ssh_private_key_file"] = key
            if user:
                defaults["ansible_user"] = user
            if port is not None and str(port).strip() != "":
                defaults["ansible_port"] = int(port)
            doc["defaults"] = defaults
            with _action_lock:
                _atomic_write_json(target, doc)
                render = run_cli(["node", "render"], timeout=60)
            self._json(
                200 if render["rc"] == 0 else 500,
                {
                    "ok": render["rc"] == 0,
                    "path": str(target),
                    "key_exists": Path(key).is_file(),
                    "render": render,
                    "setup": build_setup_status(),
                },
            )
            return

        self._json(404, {"error": f"unknown path: {path}"})


def main() -> None:
    host = os.environ.get("GPU_CONTROL_UI_HOST", "0.0.0.0")
    port = int(os.environ.get("GPU_CONTROL_UI_PORT", "8787"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"gpu-control UI listening on http://{host}:{port}")
    print(f"project: {PROJECT_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.server_close()


if __name__ == "__main__":
    main()
