#!/usr/bin/env python3
"""轻量控制面 Web UI（仅标准库）。

静态页、``/api/*`` JSON、``/api/docs`` Swagger、``/help/`` 文档站与终端 WebSocket。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
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

from node_paths import default_paths, node_paths  # noqa: E402
from state_util import atomic_write_json as state_atomic_write_json, read_json as state_read_json  # noqa: E402
from jobs_util import (  # noqa: E402
    default_job_name,
    install_job_template,
    install_recommended_templates,
    list_job_templates,
    load_job_summaries,
)
from api_catalog import catalog_payload, openapi_payload  # noqa: E402
from toolkits_util import catalog_payload as toolkits_catalog_payload  # noqa: E402
from toolkits_util import load_toolkit_state  # noqa: E402
from auth_util import (  # noqa: E402
    COOKIE_NAME,
    AuthPrincipal,
    auth_status_payload,
    authenticate_http,
    extract_bearer,
    issue_session,
    load_auth_config,
    oidc_config,
    parse_cookies,
    password_login_allowed,
    session_cookie_header,
    try_login,
    verify_api_token,
    verify_session,
)
from oidc_util import OidcError, begin_login, finish_login  # noqa: E402

sys.path.insert(0, str(SCRIPTS_DIR))
from render_gateway import (  # noqa: E402
    apply_upstreams as apply_gateway_upstreams,
    build_payload as build_gateway_payload,
    write_artifacts as write_gateway_artifacts,
)
from gateway_ops import status_payload as gateway_status_payload  # noqa: E402


# ---------------------------------------------------------------------------
# 短 TTL 缓存：仪表盘约每 12s 轮询 /api/overview；每次都 spawn
# ``gpu-control gateway status`` 会拖慢页面。写入或非只读 CLI 会失效缓存。
# ---------------------------------------------------------------------------

_GATEWAY_TTL = 12.0
_GATEWAY_CACHE: dict = {"at": 0.0, "payload": None}
_GATEWAY_LOCK = threading.Lock()

_OVERVIEW_TTL = 6.0
_OVERVIEW_CACHE: dict[str, dict] = {
    "full": {"at": 0.0, "fingerprint": "", "payload": None},
    "light": {"at": 0.0, "fingerprint": "", "payload": None},
}
_OVERVIEW_LOCK = threading.Lock()
_last_action_at = 0.0

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")


class BadRequestError(Exception):
    """用户输入非法时抛出，由 Handler 转为 HTTP 400。"""


def require_name(value, label: str = "name") -> str:
    name = str(value or "").strip()
    if not name or not _SAFE_NAME_RE.match(name):
        raise BadRequestError(f"{label} 不合法（允许字母数字开头，限 . _ -，≤64 字符）")
    return name


def require_host(value, label: str = "host") -> str:
    host = str(value or "").strip()
    if not host or not _SAFE_HOST_RE.match(host):
        raise BadRequestError(f"{label} 不合法（仅允许主机名/IP 字符）")
    return host


def require_labels(value) -> str:
    text = str(value or "").strip()
    if any(ch in text for ch in '<>"\'\\\n\r\t'):
        raise BadRequestError("labels 含非法字符")
    return text


def invalidate_overview_cache(*, gateway: bool = False) -> None:
    with _OVERVIEW_LOCK:
        for entry in _OVERVIEW_CACHE.values():
            entry["at"] = 0.0
    if gateway:
        invalidate_gateway_cache()


def invalidate_gateway_cache() -> None:
    with _GATEWAY_LOCK:
        _GATEWAY_CACHE["at"] = 0.0


def _gateway_status_fresh(*, allow_cli: bool = True) -> dict:
    """返回网关状态（数秒 TTL 缓存）。

    :param allow_cli: 为 ``False`` 时（轻量仪表盘轮询）不 spawn 子进程，
        只用缓存或进程内 payload。
    :returns: 网关状态字典。
    """
    now = time.monotonic()
    with _GATEWAY_LOCK:
        payload = _GATEWAY_CACHE["payload"]
        if payload is not None and now - _GATEWAY_CACHE["at"] < _GATEWAY_TTL:
            return payload

    payload = None
    if allow_cli:
        result = run_cli(["gateway", "status"], timeout=20, log=False)
        try:
            parsed = json.loads(result.get("stdout") or "{}")
            if isinstance(parsed, dict) and parsed.get("ok") is not None:
                payload = parsed
        except json.JSONDecodeError:
            payload = None
    if payload is None:
        payload = gateway_status_payload()
    with _GATEWAY_LOCK:
        _GATEWAY_CACHE["payload"] = payload
        _GATEWAY_CACHE["at"] = time.monotonic()
    return payload
from model_ops import (  # noqa: E402
    cache_entries,
    load_models,
    model_entries,
    models_file,
    save_models,
    secrets_status,
    token_file,
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
    "gpu-util": {
        "label": "GPU 利用率",
        "command": (
            "nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,"
            "memory.used,memory.total,temperature.gpu "
            "--format=csv"
        ),
    },
    "gpu-list": {
        "label": "列出 GPU",
        "command": "nvidia-smi -L",
    },
    "gpu-procs": {
        "label": "GPU 进程",
        "command": (
            "nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory "
            "--format=csv"
        ),
    },
    "uptime": {
        "label": "负载/内存",
        "command": "uptime; free -h; nproc; cat /proc/loadavg",
    },
    "disk": {
        "label": "磁盘",
        "command": "df -h / /tmp 2>/dev/null; df -h | head -20",
    },
    "model-procs": {
        "label": "推理进程",
        "command": (
            "pgrep -af 'python|torch|vllm|sglang|uvicorn|gunicorn' | head -50 || true"
        ),
    },
    "train-procs": {
        "label": "训练进程",
        "command": (
            "pgrep -af 'torchrun|deepspeed|llamafactory|accelerate|train_manager' | head -50 || true"
        ),
    },
    "nccl-env": {
        "label": "NCCL/分布式环境",
        "command": (
            "env | grep -E '^(NCCL_|MASTER_|NNODES|NODE_RANK|NPROC_|CUDA_VISIBLE)' | sort || "
            "echo 'no dist env in this shell'"
        ),
    },
    "train-log": {
        "label": "最近训练日志尾",
        "command": (
            "ls -t /tmp/gpu-control-jobs/*/job.log 2>/dev/null | head -1 | "
            "xargs -r tail -n 40 || echo 'no job log under /tmp/gpu-control-jobs'"
        ),
    },
    "ports": {
        "label": "监听端口",
        "command": "ss -lntp 2>/dev/null | head -50 || netstat -lntp 2>/dev/null | head -50",
    },
    "api-ports": {
        "label": "常见 API 端口",
        "command": (
            "ss -lntp 2>/dev/null | grep -E ':3000|:8000|:8080|:4000' || "
            "echo 'no common API ports listening'"
        ),
    },
    "local-health": {
        "label": "本机 /health",
        "command": (
            "for p in 8000 8080 4000 3000; do "
            "code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 2 "
            "http://127.0.0.1:$p/health || echo down); "
            "echo \"127.0.0.1:$p/health -> $code\"; done"
        ),
    },
    "docker-ps": {
        "label": "Docker 容器",
        "command": "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' 2>/dev/null || echo 'docker unavailable'",
    },
    "hostname": {
        "label": "主机信息",
        "command": "hostname; date; whoami; ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | head -5",
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
}


def _read_json(path: Path, default):
    # 损坏的运行时 JSON 不应拖垮整站；state_util 会挪开坏文件并返回 default 深拷贝。
    return state_read_json(path, default)


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
    return load_job_summaries()


def _toolkit_ready_count(state: dict) -> dict:
    """汇总工具栈探测：{nodes, ok_items, total_items}。"""
    nodes = state.get("nodes") or {}
    ok_items = 0
    total_items = 0
    for entry in nodes.values():
        for item in entry.get("toolkits") or []:
            total_items += 1
            if item.get("ok"):
                ok_items += 1
    return {
        "nodes": len(nodes),
        "ok": ok_items,
        "total": total_items,
        "generated_at": state.get("generated_at"),
    }


def _toolkit_overview(state: dict) -> dict:
    ready = _toolkit_ready_count(state)
    by_id: dict[str, dict] = {}
    for entry in (state.get("nodes") or {}).values():
        for item in entry.get("toolkits") or []:
            tid = item.get("id") or "?"
            slot = by_id.setdefault(tid, {"id": tid, "ok": 0, "fail": 0, "versions": []})
            if item.get("ok"):
                slot["ok"] += 1
                ver = item.get("version")
                if ver and ver not in slot["versions"]:
                    slot["versions"].append(ver)
            else:
                slot["fail"] += 1
    return {
        "summary": ready,
        "by_id": sorted(by_id.values(), key=lambda x: x["id"]),
        "nodes": state.get("nodes") or {},
        "generated_at": state.get("generated_at"),
    }


def build_models_payload(node: str | None = None) -> dict:
    data = load_models()
    return {
        "path": str(models_file()),
        "exists": models_file().is_file(),
        "defaults_paths": default_paths(),
        "node_paths": node_paths(node) if node else None,
        "caches": cache_entries(data, node=node),
        "models": model_entries(data, node=node),
        "pull": data.get("pull") or {},
        "secrets": secrets_status(),
    }


def collect_exec_results() -> list[dict]:
    if not EXEC_DIR.is_dir():
        return []
    results = []
    for path in sorted(EXEC_DIR.glob("*.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def build_lb_plan(strategy: str = "least_latency") -> dict:
    """按端点健康度生成客户端负载均衡方案。

    :param strategy: ``least_latency`` / ``round_robin`` / ``by_node``。
    :returns: 含 urls、ordered、snippets 等字段的计划字典。
    """
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
    """加载或渲染网关拓扑 / 片段，供 UI 与 ``/api/gateway``。

    :param upstreams: 若提供则覆盖默认上游列表后重新生成 snippets。
    """
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
    return apply_gateway_upstreams(payload, cleaned)


def state_fingerprint() -> str:
    parts = []
    watched = [
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
        STATE_DIR / "toolkits.json",
        STATE_DIR / "ssh_quality.json",
        STATE_DIR / "ingress_edge.json",
        STATE_DIR / "ingress-edge.pid",
        models_file(),
        *list_job_files(),
    ]
    for path in watched:
        try:
            if path.is_file():
                st = path.stat()
                parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
            else:
                parts.append(f"{path.name}:missing")
        except OSError:
            parts.append(f"{path.name}:error")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def watchdog_status() -> dict:
    """仪表盘用的自动重启状态（不启动子进程）。"""
    cluster = _read_json(cluster_file(), {})
    cfg = cluster.get("watchdog") or {}
    jobs = list(cfg.get("jobs") or [])
    if not jobs:
        fallback = default_job_name()
        if fallback:
            jobs = [fallback]
    pid = None
    pid_file = STATE_DIR / "watchdog.pid"
    if pid_file.is_file():
        try:
            candidate = int(pid_file.read_text(encoding="utf-8").strip().splitlines()[0])
            if (Path("/proc") / str(candidate)).exists():
                pid = candidate
        except (OSError, ValueError, IndexError):
            pid = None
    state = _read_json(STATE_DIR / "watchdog_state.json", {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "running": pid is not None,
        "pid": pid,
        "jobs": jobs,
        "interval_sec": int(cfg.get("interval_sec") or 60),
        "max_restarts": int(cfg.get("max_restarts") or 3),
        "failure_threshold": int(cfg.get("failure_threshold") or 2),
        "last_action": state.get("last_action"),
        "restarts": state.get("restarts") or {},
        "log": str(STATE_DIR / "watchdog.log"),
    }


def build_overview(*, light: bool = False, force: bool = False) -> dict:
    """构建仪表盘总览；指纹未变且在 TTL 内时复用短缓存。

    :param light: 轻量模式，跳过昂贵的 LB / 完整 probe 矩阵。
    :param force: 强制重建，忽略缓存。
    :returns: 总览 payload。
    """
    key = "light" if light else "full"
    fingerprint = state_fingerprint()
    now = time.monotonic()
    with _OVERVIEW_LOCK:
        entry = _OVERVIEW_CACHE[key]
        fresh = (
            not force
            and entry["payload"] is not None
            and entry["fingerprint"] == fingerprint
            and now - entry["at"] < _OVERVIEW_TTL
            and now - _last_action_at > 1.5
        )
        if fresh:
            payload = dict(entry["payload"])
            payload["cached"] = True
            return payload

    payload = _build_overview(light=light)
    payload["cached"] = False
    with _OVERVIEW_LOCK:
        _OVERVIEW_CACHE[key] = {
            "at": time.monotonic(),
            "fingerprint": fingerprint,
            "payload": payload,
        }
    return payload


def _build_overview(*, light: bool = False) -> dict:
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
            "total": (
                node.get("gpu_count")
                or len(gpus)
                or meta.get("gpus_per_node")
                or 0
            ),
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
    cluster_doc = _read_json(cluster_file(), {})
    nodes_doc = _read_json(nodes_file(), {})
    control_meta = dict(cluster_doc.get("control") or {})
    nodes_control = nodes_doc.get("control")
    if isinstance(nodes_control, dict):
        for key in ("name", "ansible_host", "ui_host", "ui_port", "note"):
            if key in nodes_control and key not in control_meta:
                control_meta[key] = nodes_control[key]
        if nodes_control.get("ansible_host") and not control_meta.get("host"):
            control_meta["host"] = nodes_control["ansible_host"]
    control_name = control_meta.get("node") or control_meta.get("name") or "control"
    control_meta.setdefault("node", control_name)
    control_meta.setdefault("ui_port", 8787)
    jobs = list_jobs()
    endpoints = _read_json(ENDPOINTS_FILE, {})
    toolkit_state = load_toolkit_state()
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": UI_VERSION,
        "etag": state_fingerprint(),
        "light": light,
        "control": control_meta,
        "summary": {
            "nodes": len(nodes),
            "gpus_free": free_total,
            "gpus_used": used_total,
            "gpus_total": free_total + used_total,
            "leases": len(leases),
            "jobs": len(jobs),
            "queue_pending": pending,
            "cordoned": len(cordoned),
            "probe_ok": probe.get("ok_count"),
            "probe_fail": probe.get("fail_count"),
            "api_ok": endpoint_health.get("ok_count"),
            "api_fail": endpoint_health.get("fail_count"),
            "api_total": endpoint_health.get("total"),
            "host_metrics_at": host_metrics.get("generated_at"),
            "control": control_name,
            "toolkits_ready": _toolkit_ready_count(toolkit_state),
            "busy_nodes": sum(1 for n in nodes if n.get("used")),
            "free_nodes": sum(1 for n in nodes if not n.get("used") and not n.get("cordoned")),
        },
        "nodes": nodes,
        "leases": leases,
        "jobs": jobs,
        "toolkits": _toolkit_overview(toolkit_state),
        "ingress": _gateway_status_fresh(allow_cli=not light),
        "quality": build_quality_report(),
        "watchdog": watchdog_status(),
        "queue": queue,
        "cordoned": cordoned,
        "endpoint_health": endpoint_health,
        "exec_presets": [
            {"id": key, "label": meta["label"]} for key, meta in EXEC_PRESETS.items()
        ],
        "notes": {
            "strategy": (
                "通用控制面：连通 → 看卡 → 腾空/隔离 → 跑任意 Job。"
                "统一入口：gpu-control gateway start --kind edge。"
            ),
        },
    }
    if light:
        # 自动刷新路径：跳过昂贵的 LB snippet 与完整 probe 矩阵。
        payload["probe"] = {
            "ok_count": probe.get("ok_count"),
            "fail_count": probe.get("fail_count"),
            "generated_at": probe.get("generated_at"),
        }
        payload["cluster"] = {
            "name": cluster_doc.get("name"),
            "default_job": cluster_doc.get("default_job"),
            "strategy": cluster_doc.get("strategy"),
            "gateway": {"ingress": ((cluster_doc.get("gateway") or {}).get("ingress") or {})},
        }
        payload["endpoints"] = {
            "count": endpoints.get("count") or len(endpoints.get("endpoints") or []),
            "by_node": endpoints.get("by_node") or {},
            "endpoints": endpoints.get("endpoints") or [],
            "urls": endpoints.get("urls")
            or [ep.get("url") for ep in endpoints.get("endpoints") or [] if ep.get("url")],
        }
        payload["host_metrics"] = {
            "generated_at": host_metrics.get("generated_at"),
            "nodes": host_by_node,
        }
        return payload

    payload["probe"] = probe
    payload["cluster"] = cluster_doc
    payload["endpoints"] = endpoints
    payload["host_metrics"] = host_metrics
    payload["lb"] = build_lb_plan("least_latency")
    return payload


_READ_ONLY_CLI = {"doctor", "logs", "get", "overview", "health"}


def _cli_is_read_only(args: list[str]) -> bool:
    if not args:
        return True
    cmd = args[0]
    if cmd in _READ_ONLY_CLI:
        return True
    sub = args[1] if len(args) > 1 else ""
    if cmd == "gateway" and sub == "status":
        return True
    if cmd == "models" and sub in {"show", "deps", "verify", "list"}:
        return True
    if cmd == "toolkits" and sub == "list":
        return True
    return False


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def run_cli(args: list[str], timeout: int = 900, *, log: bool = True) -> dict:
    global _last_action_at
    cmd = [str(VENV_PYTHON), str(CLI), *args]
    try:
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
        result = {
            "cmd": cmd,
            "rc": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "cmd": cmd,
            "rc": -1,
            "stdout": _as_text(exc.stdout),
            "stderr": (_as_text(exc.stderr) + f"\n[timeout after {timeout}s]").strip(),
        }
    except OSError as exc:
        result = {"cmd": cmd, "rc": -1, "stdout": "", "stderr": f"failed to run CLI: {exc}"}

    if not _cli_is_read_only(args):
        _last_action_at = time.monotonic()
        invalidate_overview_cache(gateway=(args and args[0] == "gateway"))
    if log and args and args[0] not in {"logs"}:
        from runlog_util import record_cli

        try:
            record_cli(args, result, source="api")
        except Exception:  # noqa: BLE001 — logging must never break an action
            pass
    return result


SETUP_GUIDE = [
    {
        "id": "clone",
        "title": "主控依赖与配置",
        "body": "在主控安装依赖并生成 config/local。",
        "cli": [
            "uv venv .venv && uv pip install -r requirements.txt",
            "./examples/quickstart.sh",
            "gpu-control doctor",
        ],
    },
    {
        "id": "ssh",
        "title": "SSH 与节点",
        "body": "配置私钥与节点清单，渲染 inventory，确认连通。",
        "cli": [
            "gpu-control node render",
            "gpu-control get nodes",
            "gpu-control exec --nodes all -- hostname",
        ],
    },
    {
        "id": "job",
        "title": "安装 Job 模板",
        "body": "从模板安装推理或训练 Job 到 config/local/jobs/。",
        "cli": [
            "gpu-control jobs templates",
            "gpu-control jobs install http-serve --name my-api",
            "gpu-control jobs install train-job --name sft",
        ],
    },
    {
        "id": "models",
        "title": "模型工具（可选）",
        "body": "先检查依赖；节点缺少拉取工具时再安装。",
        "cli": [
            "cp config/examples/models.example.json config/local/models.json",
            "gpu-control models deps",
            "gpu-control models verify",
        ],
    },
]


def _atomic_write_json(path: Path, payload: dict) -> None:
    state_atomic_write_json(path, payload)


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
    for job in load_job_summaries():
        jobs.append({
            **job,
            "python_exists": Path(expand_path(str(job.get("python") or ""))).exists()
            if job.get("python") and "${paths." not in str(job.get("python"))
            else None,
            "script_exists": Path(expand_path(str(job.get("script") or ""))).exists()
            if job.get("script") and "${paths." not in str(job.get("script"))
            else None,
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
        job = (body.get("job") or default_job_name() or "").strip()
        if not job:
            return {"ok": False, "error": "未指定 job，且没有已注册任务", "steps": []}
        if body.get("nodes"):
            args = ["deploy", job, "--nodes", str(body["nodes"])]
            if body.get("gpus") not in (None, ""):
                args.extend(["--gpus", str(int(body["gpus"]))])
        else:
            if body.get("gpus") in (None, ""):
                unit = ((_read_json(cluster_file(), {}).get("scheduling") or {}).get("unit_gpus"))
                if unit in (None, ""):
                    return {
                        "ok": False,
                        "error": "部署需要 gpus，或配置 cluster.scheduling.unit_gpus",
                        "steps": [],
                    }
                gpus = int(unit)
            else:
                gpus = int(body["gpus"])
            args = ["deploy", job, "--gpus", str(gpus)]
        step("一键部署", lambda: run_cli(args, timeout=900))
    elif action == "full-sync":
        step("SSH 连通", lambda: test_ssh_connectivity(timeout=6))
        step("刷新 GPU", lambda: run_cli(["gpu", "refresh", "--quiet"], timeout=180))
        step("API 健康", lambda: run_cli(["health", "--mode", "local"], timeout=180))
    elif action == "watchdog":
        job = (body.get("job") or default_job_name() or "").strip()
        if not job:
            return {"ok": False, "error": "未指定 job，且没有已注册任务", "steps": []}
        wd = (_read_json(cluster_file(), {}).get("watchdog") or {})
        step(
            "启动看门狗",
            lambda: run_cli(
                [
                    "watchdog",
                    "--job",
                    job,
                    "--background",
                    "--interval",
                    str(wd.get("interval_sec") or 60),
                    "--max-restarts",
                    str(wd.get("max_restarts") or 3),
                    "--failure-threshold",
                    str(wd.get("failure_threshold") or 2),
                ],
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
    elif action == "diagnose":
        step("一键检测", lambda: run_cli(["diagnose", "check"], timeout=60))
    elif action == "repair":
        step("一键修复", lambda: run_cli(["diagnose", "repair"], timeout=120))
    else:
        return {"ok": False, "error": f"未知动作: {action}", "steps": []}

    ok = all(s.get("ok") for s in steps) if steps else False
    from runlog_util import append_runlog

    append_runlog(
        "oneclick",
        ok=ok,
        summary=action,
        detail={"steps": [{"name": s.get("name"), "ok": s.get("ok"), "ms": s.get("ms")} for s in steps]},
        source="ui",
    )
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if etag:
            self.send_header("ETag", etag)
        if extra_headers:
            for key, value in extra_headers.items():
                if value is None:
                    continue
                self.send_header(key, value)
        self.end_headers()
        if code != 304:
            self.wfile.write(body)

    def _json(
        self,
        code: int,
        payload,
        *,
        etag: str | None = None,
        extra_headers: dict | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(
            code,
            data,
            "application/json; charset=utf-8",
            etag=etag,
            extra_headers=extra_headers,
        )

    def _redirect(self, location: str, *, extra_headers: dict | None = None) -> None:
        headers = {"Location": location}
        if extra_headers:
            headers.update(extra_headers)
        self._send(302, b"", "text/plain; charset=utf-8", extra_headers=headers)

    def _auth_gate(self, path: str, query: str = "") -> bool:
        """鉴权门控。

        :returns: 可继续处理为 ``True``；已回 401 则为 ``False``。
        """
        principal, err = authenticate_http(path=path, headers=self.headers, query=query)
        self.auth_principal = principal
        if err is None:
            return True
        cfg = load_auth_config()
        self._json(
            401,
            {
                "ok": False,
                "error": err,
                "auth": auth_status_payload(cfg=cfg, principal=None),
            },
            extra_headers={"WWW-Authenticate": 'Bearer realm="gpu-control"'},
        )
        return False

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise BadRequestError("invalid Content-Length")
        if length <= 0:
            return {}
        if length > 8 * 1024 * 1024:
            raise BadRequestError("request body too large (max 8 MB)")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequestError("invalid JSON body")
        if not isinstance(data, dict):
            raise BadRequestError("JSON body must be an object")
        return data

    def _handle_terminal_ws(self, parsed) -> None:
        if not self._auth_gate("/ws/terminal", parsed.query):
            return
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
        try:
            self._do_GET_impl()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 — keep the server alive
            import traceback

            traceback.print_exc()
            try:
                self._json(500, {"ok": False, "error": f"internal error: {exc}"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _do_GET_impl(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/ws/terminal":
            self._handle_terminal_ws(parsed)
            return

        if path.startswith("/api") and not self._auth_gate(path, parsed.query):
            return

        if path == "/api/auth/status":
            cfg = load_auth_config()
            cookies = parse_cookies(self.headers.get("Cookie"))
            real = verify_session(cookies.get(COOKIE_NAME, ""), cfg=cfg)
            if not real:
                bearer = extract_bearer(self.headers.get("Authorization"))
                if bearer and verify_api_token(bearer, cfg=cfg):
                    real = AuthPrincipal(subject="api-token", method="bearer", mode=cfg.mode)
            self._json(200, auth_status_payload(cfg=cfg, principal=real))
            return

        if path == "/api/auth/oidc/login":
            cfg = load_auth_config()
            if cfg.mode != "oidc":
                self._json(400, {"ok": False, "error": "当前 control.auth.mode 不是 oidc"})
                return
            try:
                started = begin_login(oidc_config(cfg))
            except OidcError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._redirect(started["authorize_url"])
            return

        if path == "/api/auth/oidc/callback":
            cfg = load_auth_config()
            qs = parse_qs(parsed.query)
            err = (qs.get("error") or [None])[0]
            if err:
                desc = (qs.get("error_description") or [err])[0]
                self._send(
                    400,
                    f"OIDC 登录失败: {desc}".encode("utf-8"),
                    "text/plain; charset=utf-8",
                )
                return
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            if not code or not state:
                self._json(400, {"ok": False, "error": "缺少 code/state"})
                return
            try:
                identity = finish_login(oidc_config(cfg), code=str(code), state=str(state))
            except OidcError as exc:
                self._send(
                    401,
                    f"OIDC 回调失败: {exc}".encode("utf-8"),
                    "text/plain; charset=utf-8",
                )
                return
            token = issue_session(subject=str(identity["subject"]), cfg=cfg)
            self._redirect(
                "/",
                extra_headers={
                    "Set-Cookie": session_cookie_header(token, ttl_hours=cfg.session_ttl_hours),
                },
            )
            return

        if path == "/api/auth/me":
            cfg = load_auth_config()
            self._json(
                200,
                auth_status_payload(cfg=cfg, principal=getattr(self, "auth_principal", None)),
            )
            return

        if path in {"/", "/index.html"}:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace("__UI_VERSION__", UI_VERSION)
            body = html.encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            root = STATIC_DIR.resolve()
            target = (STATIC_DIR / rel).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                self._json(404, {"error": "not found"})
                return
            content_type = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".mjs": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".map": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".ico": "image/x-icon",
                ".woff": "font/woff",
                ".woff2": "font/woff2",
                ".ttf": "font/ttf",
                ".md": "text/markdown; charset=utf-8",
                ".txt": "text/plain; charset=utf-8",
            }.get(target.suffix.lower(), "application/octet-stream")
            raw = target.read_bytes()
            etag = hashlib.sha1(raw).hexdigest()[:16]
            cache = "public, max-age=3600" if "/vendor/" in path else "public, max-age=60"
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", content_type, cache_control=cache, etag=etag)
                return
            self._send(200, raw, content_type, cache_control=cache, etag=etag)
            return

        if path == "/api/health":
            self._json(200, {"ok": True, "service": "gpu-control-ui", "version": UI_VERSION})
            return

        if path in {"/api", "/api/"}:
            self._json(200, catalog_payload())
            return

        if path == "/api/openapi.json":
            self._json(200, openapi_payload())
            return

        if path in {"/api/docs", "/docs"}:
            docs = STATIC_DIR / "api-docs.html"
            self._send(
                200,
                docs.read_bytes(),
                "text/html; charset=utf-8",
                cache_control="public, max-age=30",
            )
            return

        if path in {"/help", "/help/"} or path.startswith("/help/"):
            help_root = (STATIC_DIR / "help").resolve()
            rel = path[len("/help"):].lstrip("/") or "index.html"
            if rel.endswith("/"):
                rel = rel + "index.html"
            target = (help_root / rel).resolve()
            if help_root.is_dir() and target.is_relative_to(help_root) and target.is_file():
                content_type = {
                    ".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".woff2": "font/woff2",
                    ".json": "application/json",
                }.get(target.suffix, "application/octet-stream")
                self._send(200, target.read_bytes(), content_type, cache_control="public, max-age=60")
                return
            fallback = STATIC_DIR / "help-fallback.html"
            self._send(
                200,
                fallback.read_bytes(),
                "text/html; charset=utf-8",
                cache_control="no-store",
            )
            return

        if path == "/api/doctor":
            result = run_cli(["doctor", "--json"], timeout=30)
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "doctor parse failed", "result": result}
            self._json(200, payload)
            return

        if path == "/api/runlog":
            from runlog_util import list_runlog

            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["50"])[0])
            kind = (qs.get("kind") or [None])[0]
            self._json(200, list_runlog(limit=limit, kind=kind))
            return

        if path == "/api/diagnose":
            result = run_cli(["diagnose", "check"], timeout=60)
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "diagnose parse failed", "result": result}
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
            light = qs.get("light", ["0"])[0] in {"1", "true", "yes"}
            self._json(200, build_overview(light=light), etag=etag)
            return

        if path == "/api/endpoints/health":
            self._json(200, _read_json(ENDPOINT_HEALTH_FILE, {}))
            return

        if path.startswith("/api/jobs/") and path.endswith("/logs"):
            raw = path[len("/api/jobs/"): -len("/logs")].strip("/")
            try:
                job = require_name(raw, "job 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            qs = parse_qs(parsed.query)
            lines = (qs.get("lines") or ["120"])[0]
            try:
                lines = max(1, min(2000, int(lines)))
            except ValueError:
                lines = 120
            args = ["jobs", "logs", job, "--lines", str(lines)]
            node = (qs.get("node") or [""])[0].strip()
            if node and node != "all":
                try:
                    args.extend(["--node", require_name(node, "node 名")])
                except BadRequestError as exc:
                    self._json(400, {"error": str(exc)})
                    return
            with _action_lock:
                result = run_cli(args, timeout=120)
            self._json(200 if result["rc"] == 0 else 500, {"result": result, "job": job})
            return

        if path == "/api/jobs":
            self._json(200, {
                "jobs": list_jobs(),
                "default_job": default_job_name(),
                "templates": list_job_templates(),
            })
            return

        if path == "/api/jobs/templates":
            self._json(200, {"templates": list_job_templates()})
            return

        if path == "/api/models":
            qs = parse_qs(parsed.query)
            node = (qs.get("node") or [None])[0]
            self._json(200, build_models_payload(node))
            return

        if path == "/api/toolkits":
            self._json(200, toolkits_catalog_payload())
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

        if path == "/api/gateway/status":
            self._json(200, _gateway_status_fresh())
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
            if not _SAFE_NAME_RE.match(name):
                self._json(400, {"error": "job 名不合法"})
                return
            path_job = job_file(name)
            if not path_job:
                self._json(404, {"error": f"job not found: {name}"})
                return
            self._json(200, {"path": str(path_job), "data": json.loads(path_job.read_text(encoding="utf-8"))})
            return

        self._json(404, {"error": f"unknown path: {path}"})

    def do_POST(self) -> None:
        try:
            self._do_POST_impl()
        except BadRequestError as exc:
            try:
                self._json(400, {"ok": False, "error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 — keep the server alive on bad input
            import traceback

            traceback.print_exc()
            try:
                self._json(500, {"ok": False, "error": f"internal error: {exc}"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _do_POST_impl(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_body()
        except BadRequestError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/auth/login":
            cfg = load_auth_config()
            if cfg.mode == "none":
                self._json(200, {"ok": True, "auth": auth_status_payload(cfg=cfg)})
                return
            if not password_login_allowed(cfg):
                self._json(
                    501,
                    {
                        "ok": False,
                        "error": f"密码登录不可用（mode={cfg.mode}）；请用 OIDC 或开启 allow_password_fallback",
                        "auth": auth_status_payload(cfg=cfg),
                    },
                )
                return
            password = str(body.get("password") or "")
            principal = try_login(password, cfg=cfg)
            if not principal:
                time.sleep(0.35)
                self._json(401, {"ok": False, "error": "invalid password"})
                return
            token = issue_session(subject=principal.subject, cfg=cfg)
            self._json(
                200,
                {"ok": True, "auth": auth_status_payload(cfg=cfg, principal=principal)},
                extra_headers={
                    "Set-Cookie": session_cookie_header(token, ttl_hours=cfg.session_ttl_hours),
                },
            )
            return

        if path == "/api/auth/logout":
            cfg = load_auth_config()
            self._json(
                200,
                {"ok": True, "auth": auth_status_payload(cfg=cfg, principal=None)},
                extra_headers={
                    "Set-Cookie": session_cookie_header("", ttl_hours=cfg.session_ttl_hours, clear=True),
                },
            )
            return

        if path.startswith("/api") and not self._auth_gate(path, parsed.query):
            return

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
            try:
                job = require_name(parts[2], "job 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            action = body.get("action")
            if action not in {"start", "stop", "restart", "status", "wait"}:
                self._json(400, {"error": "action must be start|stop|restart|status|wait"})
                return
            args = [action, job]
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            for item in body.get("extra_args") or []:
                args.extend(["--arg", str(item)])
            with _action_lock:
                result = run_cli(args, timeout=1200)
            self._json(200 if result["rc"] == 0 else 500, {"result": result})
            return

        if path in {"/api/models/check", "/api/models/verify", "/api/models/deps"}:
            action = path.rsplit("/", 1)[-1]
            args = ["models", action]
            if body.get("model") and action == "verify":
                args.extend(["--model", body["model"]])
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            with _action_lock:
                result = run_cli(args, timeout=180)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "results": collect_exec_results(), "models": build_models_payload(body.get("node"))},
            )
            return

        if path == "/api/models/install-tools":
            args = ["models", "install-tools"]
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            with _action_lock:
                result = run_cli(args, timeout=int(body.get("timeout") or 900))
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "results": collect_exec_results()},
            )
            return

        if path == "/api/models/pull":
            args = ["models", "pull"]
            if body.get("model"):
                args.extend(["--model", body["model"]])
            if body.get("name"):
                args.extend(["--name", body["name"]])
            if body.get("cache"):
                args.extend(["--cache", body["cache"]])
            if body.get("tool"):
                args.extend(["--tool", str(body["tool"])])
            if body.get("register"):
                args.append("--register")
            if body.get("register_id") or body.get("id"):
                args.extend(["--register-id", str(body.get("register_id") or body.get("id"))])
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            if not body.get("model") and not body.get("name"):
                self._json(400, {"error": "填写仓库 id（name）或选择已登记 model"})
                return
            with _action_lock:
                result = run_cli(args, timeout=int(body.get("timeout") or 3600))
            self._json(
                200 if result["rc"] == 0 else 500,
                {
                    "result": result,
                    "results": collect_exec_results(),
                    "models": build_models_payload(body.get("node")),
                },
            )
            return

        if path in {"/api/toolkits/probe", "/api/toolkits/install"}:
            action = "probe" if path.endswith("/probe") else "install"
            args = ["toolkits", action]
            ids_raw = body.get("ids")
            if ids_raw is None:
                ids_raw = body.get("id")
            id_list: list[str] = []
            if isinstance(ids_raw, list):
                id_list = [str(x) for x in ids_raw if x]
            elif isinstance(ids_raw, str) and ids_raw.strip():
                id_list = [ids_raw.strip()]
            for tid in id_list:
                args.extend(["--id", tid])
            if body.get("bundle"):
                args.extend(["--bundle", str(body["bundle"])])
            if body.get("node"):
                args.extend(["--node", body["node"]])
            elif body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            if action == "install" and body.get("dry_run"):
                args.append("--dry-run")
            if action == "install" and not body.get("bundle") and not id_list:
                self._json(400, {"error": "install 需要 bundle 或 ids"})
                return
            timeout = int(body.get("timeout") or (1800 if action == "install" else 300))
            with _action_lock:
                result = run_cli(args, timeout=timeout)
            payload = {
                "result": result,
                "toolkits": toolkits_catalog_payload(),
                "overview": build_overview(light=True),
            }
            self._json(200 if result["rc"] == 0 else 500, payload)
            return

        if path == "/api/models/secrets":
            kind = (body.get("kind") or "").strip().lower()
            token = (body.get("token") or "").strip()
            clear = bool(body.get("clear"))
            if kind not in {"hf", "huggingface", "modelscope", "ms"}:
                self._json(400, {"error": "kind 需为 hf 或 modelscope"})
                return
            path_obj = token_file("hf" if kind in {"hf", "huggingface"} else "modelscope")
            if clear:
                if path_obj.is_file():
                    path_obj.unlink()
                self._json(200, {"ok": True, "cleared": True, "secrets": secrets_status()})
                return
            if not token:
                self._json(400, {"error": "token 必填（或 clear=true）"})
                return
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            path_obj.write_text(token + "\n", encoding="utf-8")
            path_obj.chmod(0o600)
            # keep models.json pointers
            data = load_models()
            pull = dict(data.get("pull") or {})
            if kind in {"hf", "huggingface"}:
                pull["hf_token_file"] = str(path_obj)
            else:
                pull["modelscope_token_file"] = str(path_obj)
            data["pull"] = pull
            save_models(data)
            self._json(200, {"ok": True, "path": str(path_obj), "secrets": secrets_status()})
            return

        if path == "/api/deploy":
            try:
                job = require_name(body.get("job"), "job 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            args = ["deploy", job]
            if body.get("nodes"):
                args.extend(["--nodes", body["nodes"]])
            if body.get("gpus") not in (None, ""):
                args.extend(["--gpus", str(int(body["gpus"]))])
            elif not body.get("nodes"):
                # 交给 CLI 读 cluster.scheduling.unit_gpus；皆缺则 CLI 报错
                pass
            if body.get("restart"):
                args.append("--restart")
            if body.get("force"):
                args.append("--force")
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
            # 扇出健康探测不需要 Ansible 动作锁。
            result = run_cli(["health", "--json"], timeout=60)
            health = _read_json(ENDPOINT_HEALTH_FILE, {})
            self._json(
                200,
                {"result": result, "health": health, "overview": build_overview()},
            )
            return

        if path == "/api/jobs/install-template":
            recommended = bool(body.get("recommended"))
            force = bool(body.get("force"))
            if recommended:
                with _action_lock:
                    payload = install_recommended_templates(force=force)
                self._json(200, {
                    **payload,
                    "overview": build_overview(),
                })
                return
            template_id = str(body.get("template") or body.get("id") or "").strip()
            name = (body.get("name") or "").strip() or None
            if not template_id:
                self._json(400, {"error": "template required（或传 recommended=true）"})
                return
            try:
                with _action_lock:
                    installed = install_job_template(template_id, name=name)
            except (FileNotFoundError, ValueError, OSError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(200, {
                "ok": True,
                **installed,
                "jobs": list_jobs(),
                "templates": list_job_templates(),
                "overview": build_overview(),
            })
            return

        if path == "/api/gateway/render":
            payload = _build_gateway_payload()
            written = write_gateway_artifacts(payload)
            self._json(200, {**payload, "written": written})
            return

        if path == "/api/gateway/start":
            kind = str(body.get("kind") or "").strip()
            args = ["gateway", "start"]
            if kind:
                args.extend(["--kind", kind])
            with _action_lock:
                result = run_cli(args, timeout=60)
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "result": result}
            self._json(200 if result["rc"] == 0 else 500, {**payload, "result": result})
            return

        if path == "/api/gateway/stop":
            kind = str(body.get("kind") or "all").strip() or "all"
            with _action_lock:
                result = run_cli(["gateway", "stop", "--kind", kind], timeout=30)
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "result": result}
            self._json(200 if result["rc"] == 0 else 500, {**payload, "result": result})
            return

        if path == "/api/procs":
            node = body.get("node")
            nodes = body.get("nodes")
            args = ["procs", "list"]
            if node:
                args.extend(["--node", node])
            elif nodes:
                args.extend(["--nodes", str(nodes)])
            if body.get("refresh"):
                args.append("--refresh")
            with _action_lock:
                result = run_cli(args, timeout=180)
            overview = build_overview() if body.get("refresh") else None
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": overview},
            )
            return

        if path == "/api/procs/kill":
            node = body.get("node")
            if not node:
                self._json(400, {"error": "node required"})
                return
            args = ["procs", "kill", "--node", node]
            if body.get("all"):
                args.append("--all")
            for pid in body.get("pids") or []:
                args.extend(["--pid", str(int(pid))])
            if body.get("match"):
                args.extend(["--match", str(body["match"])])
            if body.get("signal"):
                args.extend(["--signal", str(body["signal"])])
            if body.get("dry_run"):
                args.append("--dry-run")
            if not body.get("all") and not body.get("pids") and not body.get("match"):
                self._json(400, {"error": "需要 all / pids / match"})
                return
            with _action_lock:
                result = run_cli(args, timeout=300)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
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
            if body.get("no_kill"):
                args.append("--no-kill")
            if body.get("force"):
                args.append("--force")
            if body.get("allow_busy"):
                args.append("--allow-busy")
            if body.get("dry_run"):
                args.append("--dry-run")
            wait = body.get("wait")
            if wait is not None and str(wait) != "":
                args.extend(["--wait", str(int(wait))])
            if body.get("reason"):
                args.extend(["--reason", body["reason"]])
            with _action_lock:
                result = run_cli(args, timeout=1200)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/uncordon":
            try:
                name = require_name(body.get("name"), "node 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            with _action_lock:
                result = run_cli(["node", "uncordon", name])
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/cordon":
            try:
                name = require_name(body.get("name"), "node 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
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
            try:
                name = require_name(body.get("name"), "node 名")
                host = require_host(body.get("host"))
                labels = require_labels(body.get("labels"))
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            try:
                gpus = int(body.get("gpus", 4))
            except (TypeError, ValueError):
                self._json(400, {"error": "gpus 必须是正整数"})
                return
            if gpus < 1 or gpus > 64:
                self._json(400, {"error": "gpus 必须在 1..64"})
                return
            args = ["node", "add", name, "--host", host, "--gpus", str(gpus)]
            if labels:
                args.extend(["--labels", labels])
            with _action_lock:
                result = run_cli(args)
            self._json(
                200 if result["rc"] == 0 else 500,
                {"result": result, "overview": build_overview()},
            )
            return

        if path == "/api/node/remove":
            try:
                name = require_name(body.get("name"), "node 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
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
            invalidate_overview_cache()
            self._json(200, {"ok": True, "path": str(target), "setup": build_setup_status()})
            return

        if path == "/api/config/nodes":
            data = body.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
                self._json(400, {"error": "data with nodes object required"})
                return
            try:
                validated = {}
                for raw_name, meta in data["nodes"].items():
                    node_name = require_name(raw_name, "node 名")
                    if not isinstance(meta, dict):
                        raise BadRequestError(f"节点 {node_name} 配置必须是对象")
                    node = dict(meta)
                    node["ansible_host"] = require_host(meta.get("ansible_host"), f"{node_name} 地址")
                    if "gpus_per_node" in node:
                        try:
                            gpus = int(node["gpus_per_node"])
                        except (TypeError, ValueError):
                            raise BadRequestError(f"{node_name} gpus_per_node 必须是整数")
                        if gpus < 1 or gpus > 64:
                            raise BadRequestError(f"{node_name} gpus_per_node 必须在 1..64")
                        node["gpus_per_node"] = gpus
                    labels = node.get("labels")
                    if labels is not None:
                        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
                            raise BadRequestError(f"{node_name} labels 必须是字符串数组")
                        require_labels(",".join(labels))
                    validated[node_name] = node
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            data["nodes"] = validated
            target = nodes_file(write=True)
            with _action_lock:
                _atomic_write_json(target, data)
                root = PROJECT_DIR / "nodes.json"
                if not root.exists():
                    root.symlink_to(Path("config/local/nodes.json"))
                render = run_cli(["node", "render"], timeout=60)
            invalidate_overview_cache()
            self._json(
                200 if render["rc"] == 0 else 500,
                {"ok": render["rc"] == 0, "path": str(target), "render": render, "setup": build_setup_status()},
            )
            return

        if path.startswith("/api/config/job/"):
            name = path[len("/api/config/job/"):].strip("/")
            try:
                name = require_name(name, "job 名")
            except BadRequestError as exc:
                self._json(400, {"error": str(exc)})
                return
            data = body.get("data")
            if not isinstance(data, dict):
                self._json(400, {"error": "data object required"})
                return
            data = dict(data)
            data["name"] = name
            if data.get("gpus_per_node") is not None:
                try:
                    gpus = int(data["gpus_per_node"])
                except (TypeError, ValueError):
                    self._json(400, {"error": "gpus_per_node 必须是整数"})
                    return
                if gpus < 1 or gpus > 64:
                    self._json(400, {"error": "gpus_per_node 必须在 1..64"})
                    return
                data["gpus_per_node"] = gpus
            for key in ("extra_args", "actions"):
                if data.get(key) is not None and (
                    not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key])
                ):
                    self._json(400, {"error": f"{key} 必须是字符串数组"})
                    return
            if data.get("env") is not None and not isinstance(data["env"], dict):
                self._json(400, {"error": "env 必须是对象"})
                return
            # 编辑一律写入 config/local/jobs，避免污染内置模板。
            LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
            target = (LOCAL_JOBS_DIR / f"{name}.json").resolve()
            if not target.is_relative_to(LOCAL_JOBS_DIR.resolve()):
                self._json(400, {"error": "job 名不合法"})
                return
            with _action_lock:
                _atomic_write_json(target, data)
            invalidate_overview_cache()
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

        if path == "/api/diagnose/repair":
            args = ["diagnose", "repair"]
            for fid in body.get("ids") or []:
                if isinstance(fid, str) and fid:
                    args.extend(["--id", fid])
            with _action_lock:
                result = run_cli(args, timeout=120)
            try:
                payload = json.loads(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "repair parse failed", "result": result}
            payload["result"] = result
            payload["overview"] = build_overview(light=True)
            self._json(200 if result["rc"] == 0 else 500, payload)
            return

        if path == "/api/runlog/clear":
            from runlog_util import clear_runlog

            self._json(200, clear_runlog())
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
    # 默认仅回环；cmd_ui 在运维明确要暴露局域网时传入 cluster.control.ui_host。
    host = os.environ.get("GPU_CONTROL_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("GPU_CONTROL_UI_PORT", "8787"))
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"gpu-control UI cannot bind {host}:{port}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    pid_file = STATE_DIR / "ui.pid"
    stopping = threading.Event()

    def _stop(_signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        # shutdown() must run outside the serve_forever() thread.
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    print(f"gpu-control UI listening on http://{host}:{port}")
    print(f"project: {PROJECT_DIR}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        try:
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
        print("shutting down")


if __name__ == "__main__":
    main()
