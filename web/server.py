#!/usr/bin/env python3
"""轻量控制面 Web UI（仅标准库）。

静态页、``/api/*`` JSON、``/api/docs`` Swagger、``/help/`` 文档站与终端 WebSocket。
"""

from __future__ import annotations

import hashlib
import importlib.util
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
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from json_util import loads_relaxed  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from schemas import AuthTokenRequest, LiteLLMModelRequest  # noqa: E402
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
    write_secret_file,
)

from node_paths import default_paths, node_paths  # noqa: E402
from state_util import atomic_write_json as state_atomic_write_json, read_json as state_read_json  # noqa: E402
from process_util import pid_alive  # noqa: E402
import liveness  # noqa: E402
import assistant_session  # noqa: E402
import remote_access  # noqa: E402
from assistant_tools import route_for as assistant_route_for  # noqa: E402
from assistant_validate import validate_call as assistant_validate_call  # noqa: E402
from static_util import resolve_help_target  # noqa: E402
from jobs_util import (  # noqa: E402
    default_job_name,
    install_job_template,
    install_recommended_templates,
    list_job_templates,
    load_job_summaries,
)
from api_catalog import catalog_payload, openapi_payload  # noqa: E402
from assistant_policy import policy_payload  # noqa: E402
from assistant_chat import (  # noqa: E402
    analyze_dashboard,
    assistant_status,
    format_upstream_error,
    list_upstream_models,
    probe_upstream,
    save_assistant_config,
    compact_overview,
    handle_chat,
    stream_chat,
)
from notices_util import (  # noqa: E402
    findings_to_notices,
    list_notices,
    mark_notices,
    overview_to_notices,
    reconcile_notices,
    upsert_notices,
)
from metrics_history import list_history, record_overview_sample  # noqa: E402
from exec_presets import EXEC_PRESETS  # noqa: E402
from toolkits_util import catalog_payload as toolkits_catalog_payload  # noqa: E402
from toolkits_util import load_toolkit_state  # noqa: E402
from auth_util import (  # noqa: E402
    COOKIE_NAME,
    AuthPrincipal,
    auth_status_payload,
    authenticate_http,
    clear_login_failures,
    extract_bearer,
    issue_api_token,
    issue_session,
    load_auth_config,
    login_rate_limited,
    oidc_config,
    parse_cookies,
    password_login_allowed,
    record_login_failure,
    save_auth_mode,
    session_cookie_header,
    set_password,
    try_login,
    verify_api_token,
    verify_session,
)
from oidc_util import OidcError, begin_login, finish_login, probe_oidc  # noqa: E402

sys.path.insert(0, str(SCRIPTS_DIR))
from render_gateway import (  # noqa: E402
    apply_upstreams as apply_gateway_upstreams,
    build_payload as build_gateway_payload,
    write_artifacts as write_gateway_artifacts,
)
from gateway_ops import (  # noqa: E402
    clear_litellm_master_key,
    generate_litellm_master_key,
    litellm_master_key_status,
    read_litellm_master_key,
    restart_litellm,
    status_payload as gateway_status_payload,
    write_litellm_master_key,
)


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
            parsed = loads_relaxed(result.get("stdout") or "{}")
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
AUTO_OPS_FILE = STATE_DIR / "auto_ops.json"
HOST_METRICS_FILE = STATE_DIR / "host_metrics.json"
LEASES_FILE = STATE_DIR / "leases.json"
QUEUE_FILE = STATE_DIR / "queue.json"
PROBE_FILE = STATE_DIR / "probe.json"
NODE_STATE_FILE = STATE_DIR / "node_state.json"
EXEC_DIR = STATE_DIR / "exec"
LIVENESS_FILE = STATE_DIR / "liveness.json"
RESOURCES_HEALTH_FILE = STATE_DIR / "resources_health.json"
INVENTORY = INVENTORY_FILE

_action_lock = threading.Lock()
_assistant_action_lock = threading.Lock()
_assistant_inflight: set[tuple[str, str]] = set()
UI_VERSION = (
    os.environ.get("COMPUTEDECK_VERSION", "").strip()
    or (VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.is_file() else "")
    or "0.0.0-dev"
)



# 远程命令补全额外词条（非按钮预设）
EXEC_SUGGEST_EXTRA: list[dict[str, str]] = [
    {"command": "nvidia-smi -q", "label": "nvidia-smi 详单"},
    {"command": "nvidia-smi topo -m", "label": "GPU 拓扑"},
    {"command": "ps -ef | grep -E 'vllm|sglang|torch' | grep -v grep", "label": "筛推理进程"},
    {"command": "journalctl -n 80 --no-pager", "label": "最近系统日志"},
    {"command": "df -h", "label": "全部磁盘"},
    {"command": "free -h", "label": "内存"},
    {"command": "ip -br a", "label": "网卡简表"},
]


def suggest_exec_commands(query: str = "", limit: int = 20) -> list[dict[str, str]]:
    """远程命令智能补全候选（预设 + 额外 + 已安装 Job 名提示）。"""
    q = (query or "").strip().lower()
    items: list[dict[str, str]] = []
    for key, meta in EXEC_PRESETS.items():
        items.append(
            {
                "command": meta["command"],
                "label": meta["label"],
                "source": f"preset:{key}",
            }
        )
    items.extend({**row, "source": "extra"} for row in EXEC_SUGGEST_EXTRA)
    for path in list_job_files():
        name = path.stem
        items.append(
            {
                "command": f"pgrep -af {shlex.quote(name)} | head -30 || true",
                "label": f"Job 进程 · {name}",
                "source": "job",
            }
        )
    if q:
        items = [
            row
            for row in items
            if q in row["command"].lower() or q in row["label"].lower() or q in row["source"].lower()
        ]
    # 去重 command，保序
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in items:
        cmd = row["command"]
        if cmd in seen:
            continue
        seen.add(cmd)
        out.append(row)
        if len(out) >= max(1, min(limit, 50)):
            break
    return out


_LITELLM_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
_LITELLM_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def _litellm_admin_request(method: str, path: str, body: dict | None = None, *, timeout: int = 20) -> dict:
    """用服务端保存的 Master Key 调 LiteLLM Admin API（不把密钥下发到浏览器）。"""
    key = read_litellm_master_key()
    if not key:
        raise RuntimeError("未配置 LiteLLM Master Key")
    gateway = (_read_json(cluster_file(), {}) or {}).get("gateway") or {}
    port = int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return loads_relaxed(raw) if raw else {}


def _safe_inference_base(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("API Base 必须是 http(s):// 地址")
    if host in {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}:
        raise ValueError("API Base 不能指向云元数据地址")
    if host.startswith("169.254.") or host.startswith("fe80:"):
        raise ValueError("API Base 不能指向链路本地地址")
    return parsed.geturl().rstrip("/")


def litellm_models_payload() -> dict:
    info = _litellm_admin_request("GET", "/model/info")
    grouped: dict[str, dict] = {}
    for row in info.get("data") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("model_name") or "").strip()
        if not name:
            continue
        params = row.get("litellm_params") or {}
        meta = row.get("model_info") or {}
        item = grouped.setdefault(
            name,
            {
                "model_name": name,
                "model": params.get("model"),
                "api_base": params.get("api_base"),
                "db_model": False,
                "deployments": 0,
                "ids": [],
            },
        )
        item["deployments"] += 1
        item["db_model"] = item["db_model"] or bool(meta.get("db_model"))
        dep_id = str(meta.get("id") or "").strip()
        if dep_id:
            item["ids"].append(dep_id)
    return {"models": sorted(grouped.values(), key=lambda x: x["model_name"])}


def add_litellm_model(body: dict) -> dict:
    name = str(body.get("model_name") or "").strip()
    if not _LITELLM_MODEL_NAME.match(name):
        raise ValueError("model_name 只能包含字母、数字、. _ : / -，最长 64")
    api_base = _safe_inference_base(str(body.get("api_base") or ""))
    gateway = (_read_json(cluster_file(), {}) or {}).get("gateway") or {}
    default_model = str(gateway.get("litellm_model") or "openai/decider").strip()
    upstream = str(body.get("model") or default_model).strip()
    if not upstream.startswith("openai/"):
        raise ValueError("上游模型需为 openai/<model>（LiteLLM 的 OpenAI 兼容命名）")
    key_env = str(body.get("key_env") or "INFERENCE_API_KEY").strip()
    if not _LITELLM_ENV_NAME.match(key_env):
        raise ValueError("Key 环境变量名为大写字母/数字/下划线")
    dep_id = str(body.get("id") or f"ui-{name}").strip()
    if not _LITELLM_MODEL_NAME.match(dep_id):
        raise ValueError("模型 id 不合法")
    return _litellm_admin_request(
        "POST",
        "/model/new",
        {
            "model_name": name,
            "litellm_params": {
                "model": upstream,
                "api_base": api_base,
                "api_key": f"os.environ/{key_env}",
            },
            "model_info": {"id": dep_id, "db_model": True},
        },
    )


def delete_litellm_model(body: dict) -> dict:
    dep_id = str(body.get("id") or "").strip()
    if not _LITELLM_MODEL_NAME.match(dep_id):
        raise ValueError("模型 id 不合法")
    return _litellm_admin_request("POST", "/model/delete", {"id": dep_id})


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


def state_fingerprint(*, bucket_sec: int = 0) -> str:
    parts = []
    watched = [
        RESOURCES_FILE,
        RESOURCES_HEALTH_FILE,
        LEASES_FILE,
        QUEUE_FILE,
        NODE_STATE_FILE,
        ENDPOINTS_FILE,
        ENDPOINT_HEALTH_FILE,
        HOST_METRICS_FILE,
        PROBE_FILE,
        LIVENESS_FILE,
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
    if bucket_sec > 0:
        # 文件都不变时，年龄本身也会让“快照过期/节点离线”状态变化；
        # 加时间桶让 ETag 至少每 bucket_sec 刷新一次。
        parts.append(f"bucket:{int(time.time() // bucket_sec)}")
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
            if pid_alive(candidate):
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
    fingerprint = state_fingerprint(bucket_sec=60)
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
    payload["etag"] = fingerprint
    payload["cached"] = False
    # 轻量规则告警 + 指标采样（节流）；diagnose 仍由 /api/notices?sync=1 触发
    rows = overview_to_notices(payload)
    if rows:
        upsert_notices(rows)
    reconcile_notices(rows)
    record_overview_sample(payload)
    payload["notices"] = list_notices(limit=20)
    payload["metrics_tail"] = list_history(limit=36)
    with _OVERVIEW_LOCK:
        _OVERVIEW_CACHE[key] = {
            "at": time.monotonic(),
            "fingerprint": fingerprint,
            "payload": payload,
        }
    return payload


def build_liveness_view(*, refresh: bool = False) -> dict:
    """节点存活视图：优先后台 TCP 探测，缺失时回落既有健康文件。"""
    cluster = _read_json(cluster_file(), {})
    if refresh:
        run_liveness_check()
    persisted = liveness.read_snapshot(LIVENESS_FILE)
    return liveness.cluster_view(
        node_names=inventory_nodes(),
        resources=_read_json(RESOURCES_FILE, {}),
        ssh_quality=_read_json(STATE_DIR / "ssh_quality.json", {}),
        host_metrics=_read_json(HOST_METRICS_FILE, {}),
        probe=_read_json(PROBE_FILE, {}),
        endpoint_health=_read_json(ENDPOINT_HEALTH_FILE, {}),
        persisted=persisted,
        cluster=cluster,
    )


def run_liveness_check() -> dict:
    """立即做一轮 TCP 存活探测，写 ``state/liveness.json``。"""
    nodes_doc = _read_json(nodes_file(), {"defaults": {}, "nodes": {}})
    cluster = _read_json(cluster_file(), {})
    snapshot = liveness.run_check(
        nodes_doc,
        cluster=cluster,
        snapshot_path=LIVENESS_FILE,
    )
    invalidate_overview_cache()
    return snapshot


def _cli_json_result(args: list[str], timeout: int = 60) -> Any:
    result = run_cli(args, timeout=timeout, log=False)
    try:
        return loads_relaxed(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "stdout 非 JSON",
            "rc": result.get("rc"),
            "stdout": (result.get("stdout") or "")[-2000:],
            "stderr": (result.get("stderr") or "")[-1000:],
        }


def assistant_runners(jobs_list: list[dict]) -> dict:
    """只读工具执行器：写操作统一走 /api/assistant/execute。"""

    def _config(args: dict) -> Any:
        kind = str((args or {}).get("kind") or "").strip().lower()
        if kind == "cluster":
            return {"path": str(cluster_file()), "data": _read_json(cluster_file(), {})}
        if kind == "nodes":
            return {"path": str(nodes_file()), "data": _read_json(nodes_file(), {})}
        if kind == "job":
            name = str((args or {}).get("name") or "").strip()
            path = job_file(name) if name else None
            if path is None:
                return {"ok": False, "error": f"未知 Job：{name}"}
            return {"path": str(path), "data": _read_json(path, {})}
        return {"ok": False, "error": "kind 须为 cluster|nodes|job"}

    def _gateway(args: dict) -> Any:
        action = str((args or {}).get("action") or "status")
        if action == "get":
            return build_gateway_payload()
        return _gateway_status_fresh()

    def _models(args: dict) -> Any:
        action = str((args or {}).get("action") or "list").strip() or "list"
        node = str((args or {}).get("node") or "").strip() or None
        nodes = str((args or {}).get("nodes") or "").strip() or None
        model = str((args or {}).get("model") or "").strip() or None
        if action == "list":
            return build_models_payload(node)
        if action in {"check", "deps", "verify", "show", "paths"}:
            cmd = ["models", action]
            if node:
                cmd.extend(["--node", node])
            elif nodes:
                cmd.extend(["--nodes", nodes])
            if model and action == "verify":
                cmd.extend(["--model", model])
            return _cli_json_result(cmd, timeout=300)
        return {"ok": False, "error": f"只读执行器不支持 gc_models action={action}"}

    def _toolkits(_args: dict) -> Any:
        return _toolkit_overview(load_toolkit_state())

    def _procs(args: dict) -> Any:
        node = str((args or {}).get("node") or "").strip()
        cmd = ["procs", "list", "--refresh"]
        if node:
            cmd.extend(["--node", node])
        return _cli_json_result(cmd, timeout=180)

    def _exec_preset(args: dict) -> Any:
        node = str((args or {}).get("node") or "").strip()
        preset = str((args or {}).get("preset") or "").strip()
        meta = EXEC_PRESETS.get(preset)
        if not node or meta is None:
            return {"ok": False, "error": "node and preset required"}
        result = run_cli(["exec", "--quiet", "--node", node, "--", meta["command"]], timeout=180)
        results = collect_exec_results()
        ok = bool([row for row in results if exec_row_ok(row)])
        return {
            "ok": ok,
            "node": node,
            "preset": preset,
            "label": meta.get("label"),
            "command": meta.get("command"),
            "results": results,
            "result": result,
        }

    def _exec_command(args: dict) -> Any:
        node = str((args or {}).get("node") or "").strip()
        command = str((args or {}).get("command") or "").strip()
        if not node or not command:
            return {"ok": False, "error": "node and command required"}
        result = run_cli(["exec", "--quiet", "--node", node, "--", command], timeout=300)
        results = collect_exec_results()
        ok = bool([row for row in results if exec_row_ok(row)])
        return {
            "ok": ok,
            "error": None if ok else exec_failure_detail({"results": results}, result),
            "node": node,
            "command": command,
            "results": results,
            "result": result,
        }

    def _logs(args: dict) -> Any:
        name = str((args or {}).get("name") or "").strip()
        lines = int((args or {}).get("lines") or 200)
        return _cli_json_result(
            ["jobs", "logs", name, "--lines", str(max(1, min(2000, lines)))],
            timeout=120,
        )

    def _notices(_args: dict) -> Any:
        return list_notices(limit=50)

    def _metrics_history(args: dict) -> Any:
        try:
            limit = int((args or {}).get("limit") or 60)
        except (TypeError, ValueError):
            limit = 60
        return list_history(limit=max(5, min(limit, 240)))

    def _logs_tail(args: dict) -> Any:
        name = str((args or {}).get("name") or "ui")
        try:
            lines = int((args or {}).get("lines") or 200)
        except (TypeError, ValueError):
            lines = 200
        lines = max(1, min(lines, 500))
        files = {
            "ui": STATE_DIR / "ui.log",
            "watchdog": STATE_DIR / "watchdog.log",
            "ingress-edge": STATE_DIR / "ingress-edge.log",
            "ingress-litellm": STATE_DIR / "ingress-litellm.log",
            "cloudflared": STATE_DIR / "cloudflared.log",
        }
        if name == "runlog":
            return list_runlog(limit=lines)
        path_obj = files.get(name)
        if path_obj is None:
            return {"ok": False, "error": f"unknown log: {name}"}
        if not path_obj.is_file():
            return {"ok": True, "name": name, "path": str(path_obj), "exists": False, "lines": []}
        return {
            "ok": True,
            "name": name,
            "path": str(path_obj),
            "exists": True,
            "size": path_obj.stat().st_size,
            "lines": _tail_lines(path_obj, lines),
        }

    def _liveness(args: dict) -> Any:
        action = str((args or {}).get("action") or "status")
        if action == "check":
            run_liveness_check()
        return build_liveness_view()

    def _remote(args: dict) -> Any:
        try:
            from remote_access import plan, status

            action = str((args or {}).get("action") or "status")
            if action == "plan":
                return plan(mode=str((args or {}).get("mode") or "tailscale-serve"))
            return status()
        except Exception as exc:  # noqa: BLE001 — 远程接入模块缺失时降级
            return {"ok": False, "error": str(exc)}

    def _queue(_args: dict) -> Any:
        payload = _read_json(QUEUE_FILE, {"workloads": []})
        return {"ok": True, **payload}

    def _lease(_args: dict) -> Any:
        payload = _read_json(LEASES_FILE, {"leases": []})
        return {"ok": True, **payload}

    return {
        "gc_overview": lambda args: build_overview(
            light=not bool((args or {}).get("refresh")),
            force=bool((args or {}).get("refresh")),
        ),
        "gc_liveness": _liveness,
        "gc_doctor": lambda _args: _cli_json_result(["doctor", "--json"], timeout=30),
        "gc_diagnose": lambda _args: _cli_json_result(["diagnose", "check"], timeout=90),
        "gc_get_jobs": lambda _args: {"jobs": jobs_list, "default_job": default_job_name()},
        "gc_job_logs": _logs,
        "gc_exec_preset": _exec_preset,
        "gc_exec": _exec_command,
        "gc_api_catalog": lambda _args: catalog_payload(),
        "gc_metrics_history": _metrics_history,
        "gc_logs": _logs_tail,
        "gc_auth_status": lambda _args: auth_status_payload(),
        "gc_get_config": _config,
        "gc_queue": _queue,
        "gc_lease": _lease,
        "gc_gateway": _gateway,
        "gc_models": _models,
        "gc_toolkits": _toolkits,
        "gc_procs": _procs,
        "gc_notices": _notices,
        "gc_remote": _remote,
    }


def exec_row_ok(row: Any) -> bool:
    """exec 结果行可能有 ok 或仅 rc（CLI 写出），两种都认。"""
    if not isinstance(row, dict):
        return False
    if "ok" in row:
        return bool(row.get("ok"))
    try:
        return int(row.get("rc")) == 0
    except (TypeError, ValueError):
        return False


def exec_failure_detail(data: Any, result: Any = None) -> str:
    """把 exec / 动作失败转成可读原因，避免回复里出现 None。"""
    if isinstance(data, dict):
        err = data.get("error")
        if err:
            return str(err)
        rows = data.get("results")
        if isinstance(rows, list):
            for row in rows:
                if not exec_row_ok(row):
                    node = row.get("node") or "节点"
                    rc = row.get("rc")
                    detail = row.get("stderr") or row.get("stdout") or ""
                    return f"{node} 执行失败（rc={rc}）{(': ' + str(detail)[:200]) if detail else ''}"
        msg = data.get("message")
        if msg:
            return str(msg)
    if isinstance(result, dict):
        err = result.get("error") or result.get("stderr")
        if err:
            return str(err)[:300]
    return "执行失败，但未返回明确错误；请查看执行输出。"


def compact_action_result(data: Any) -> Any:
    """执行结果回传 UI / 模型前去掉大块冗余字段。"""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    out.pop("overview", None)
    out.pop("liveness", None)
    return out


def _tail_lines(path: Path, lines: int, *, max_block: int = 512 * 1024) -> list[str]:
    """读取文本日志末尾若干行；大文件只读最后一段。"""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, max(64 * 1024, min(max_block, lines * 4096)))
            fh.seek(size - block)
            raw = fh.read()
    except OSError:
        return []
    rows = raw.decode("utf-8", errors="replace").splitlines()
    if block < size and rows:
        rows = rows[1:]
    return rows[-lines:]


def assistant_known_jobs(jobs_list: list[dict]) -> set[str]:
    out = set()
    for job in jobs_list or []:
        if isinstance(job, dict) and job.get("name"):
            out.add(str(job["name"]))
    return out


def _signals_fresh(
    ssh_quality: dict, probe: dict, ttls: dict, endpoint_health: dict | None = None
) -> dict:
    """判断 ssh / api / mesh 信号是否在 TTL 内。"""
    now = time.time()

    def _age(value):
        epoch = liveness.parse_ts(value)
        return None if epoch is None else int(now - epoch)

    ssh_age = _age(ssh_quality.get("checked_at"))
    api_age = _age((endpoint_health or {}).get("generated_at"))
    mesh_age = _age(probe.get("generated_at")) if probe else None
    host_age = _age(_read_json(HOST_METRICS_FILE, {}).get("generated_at"))
    return {
        "ssh_age_sec": ssh_age,
        "ssh_fresh": ssh_age is not None and ssh_age <= int(ttls["ssh_ttl_sec"]),
        "api_age_sec": api_age,
        "api_fresh": api_age is not None and api_age <= int(ttls["api_ttl_sec"]),
        "mesh_age_sec": mesh_age,
        "mesh_fresh": mesh_age is not None and mesh_age <= int(ttls["mesh_ttl_sec"]),
        "host_age_sec": host_age,
        "host_fresh": host_age is not None and host_age <= int(ttls["host_ttl_sec"]),
    }


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
    probe = _read_json(PROBE_FILE, {})
    cluster_doc = _read_json(cluster_file(), {})
    nodes_doc = _read_json(nodes_file(), {})
    live_view = build_liveness_view()
    live_nodes = live_view.get("nodes") or {}
    ttls = liveness.load_ttls(cluster_doc)
    signals = _signals_fresh(_read_json(STATE_DIR / "ssh_quality.json", {}), probe, ttls, endpoint_health)
    nodes = []
    free_total = 0
    used_total = 0
    excluded_total = 0
    counts = {"online": 0, "degraded": 0, "offline": 0, "unknown": 0}
    offline_names: list[str] = []
    stale_names: list[str] = []
    for name in inventory_nodes():
        node = resources.get(name, {"node": name, "gpus": [], "gpu_count": 0})
        meta = registry.get(name, {})
        gpus = node.get("gpus", [])
        free = node.get("free_count", sum(1 for g in gpus if g.get("status") == "free"))
        used = node.get("occupied_count", sum(1 for g in gpus if g.get("status") == "occupied"))
        total = (
            node.get("gpu_count")
            or len(gpus)
            or meta.get("gpus_per_node")
            or 0
        )
        live = live_nodes.get(name) or {}
        live_state = str(live.get("state") or "unknown")
        snapshot = live.get("snapshot") or {}
        snapshot_stale = bool(snapshot.get("stale"))
        # 只有“可达且快照新鲜”的节点才计入 GPU 空闲/占用统计，
        # 否则旧的 resources.json 会让离线节点永远显示为占用/空闲。
        counted = live_state == "online" and not snapshot_stale
        if counted:
            free_total += free
            used_total += used
        else:
            excluded_total += total
        counts[live_state] = counts.get(live_state, 0) + 1
        if live_state == "offline":
            offline_names.append(name)
        if snapshot_stale:
            stale_names.append(name)
        lease = lease_by_node.get(name)
        if live_state == "offline":
            state = "offline"
        elif live_state == "degraded":
            state = "degraded"
        elif live_state == "unknown":
            state = "unknown"
        elif snapshot_stale:
            state = "stale"
        elif name in cordoned:
            state = "cordoned"
        elif used:
            state = "busy"
        else:
            state = "free"
        host = host_by_node.get(name) or {}
        nodes.append({
            "name": name,
            "host": meta.get("ansible_host") or node.get("ansible_host"),
            "labels": meta.get("labels") or [],
            "role": meta.get("role"),
            "note": meta.get("note"),
            "free": free,
            "used": used,
            "total": total,
            "gpus": gpus,
            "lease": lease,
            "cordoned": name in cordoned,
            "cordon": cordoned.get(name),
            "state": state,
            "liveness_state": live_state,
            "counted": counted,
            "reachable": live.get("reachable"),
            "last_seen": live.get("last_seen"),
            "last_checked": live.get("last_checked"),
            "last_seen_age_sec": live.get("last_seen_age_sec"),
            "consecutive_failures": live.get("consecutive_failures"),
            "liveness_error": live.get("error"),
            "snapshot_stale": snapshot_stale,
            "snapshot_age_sec": snapshot.get("age_sec"),
            "snapshot_age_text": snapshot.get("age_text"),
            "liveness": live,
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
    sm_vals: list[float] = []
    mem_vals: list[float] = []
    for node in nodes:
        if not node.get("counted"):
            continue
        for gpu in node.get("gpus") or []:
            util = gpu.get("utilization_percent")
            if util is not None:
                try:
                    sm_vals.append(float(util))
                except (TypeError, ValueError):
                    pass
            used_mem = gpu.get("memory_used_mib")
            total_mem = gpu.get("memory_total_mib")
            try:
                if used_mem is not None and total_mem is not None and float(total_mem) > 0:
                    mem_vals.append(100.0 * float(used_mem) / float(total_mem))
            except (TypeError, ValueError):
                pass
    gpu_sm_util = round(sum(sm_vals) / len(sm_vals), 1) if sm_vals else None
    gpu_mem_util = round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else None
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
            "nodes_online": counts.get("online", 0),
            "nodes_degraded": counts.get("degraded", 0),
            "nodes_offline": counts.get("offline", 0),
            "nodes_unknown": counts.get("unknown", 0),
            "nodes_stale": len(stale_names),
            "offline_nodes": offline_names,
            "stale_nodes": stale_names,
            "gpus_free": free_total,
            "gpus_used": used_total,
            "gpus_total": free_total + used_total,
            "gpus_last_known_total": free_total + used_total + excluded_total,
            "gpus_excluded": excluded_total,
            "gpu_sm_util_pct": gpu_sm_util,
            "gpu_mem_util_pct": gpu_mem_util,
            "data_fresh": not offline_names and not stale_names,
            "leases": len(leases),
            "jobs": len(jobs),
            "queue_pending": pending,
            "cordoned": len(cordoned),
            "probe_ok": probe.get("ok_count"),
            "probe_fail": probe.get("fail_count"),
            "probe_stale": not signals["mesh_fresh"],
            "api_ok": endpoint_health.get("ok_count"),
            "api_fail": endpoint_health.get("fail_count"),
            "api_total": endpoint_health.get("total"),
            "api_stale": not signals["api_fresh"],
            "ssh_stale": not signals["ssh_fresh"],
            "host_metrics_at": host_metrics.get("generated_at"),
            "host_stale": not signals["host_fresh"],
            "control": control_name,
            "toolkits_ready": _toolkit_ready_count(toolkit_state),
            "busy_nodes": sum(
                1 for n in nodes if n.get("counted") and n.get("state") == "busy"
            ),
            "free_nodes": sum(
                1 for n in nodes if n.get("counted") and n.get("state") == "free"
            ),
        },
        "nodes": nodes,
        "liveness": live_view,
        "signals": signals,
        "resources_health": _read_json(RESOURCES_HEALTH_FILE, {}),
        "leases": leases,
        "jobs": jobs,
        "toolkits": _toolkit_overview(toolkit_state),
        "ingress": _gateway_status_fresh(allow_cli=not light),
        "quality": build_quality_report(),
        "watchdog": watchdog_status(),
        "notices": list_notices(limit=20),
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
        payload["auto_ops"] = _read_json(AUTO_OPS_FILE, {})
        return payload

    payload["probe"] = probe
    payload["cluster"] = cluster_doc
    payload["endpoints"] = endpoints
    payload["host_metrics"] = host_metrics
    payload["auto_ops"] = _read_json(AUTO_OPS_FILE, {})
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

    auth_state = auth_status_payload(cfg=load_auth_config())
    mcp_path = PROJECT_DIR / "bin" / "gpu-control-mcp"
    mcp_exists = mcp_path.is_file()
    mcp_exec = mcp_exists and os.access(mcp_path, os.X_OK)
    mcp_module = importlib.util.find_spec("mcp") is not None
    assistant = assistant_status(cluster)
    control = cluster.get("control") or {}
    api_host = str(control.get("host") or "127.0.0.1")
    api_port = int(control.get("ui_port") or 8787)
    nodes_count = len(nodes_doc.get("nodes") or {})
    key_ok = bool(key_path) and Path(key_path).is_file()
    steps = [
        {
            "id": "auth",
            "title": "主控与鉴权",
            "ok": bool(cluster) and (bool(auth_state.get("enabled")) or not control.get("ui_host") or control.get("ui_host") in {"127.0.0.1", "localhost"}),
            "detail": f"鉴权 {auth_state.get('mode') or 'none'} · UI :{api_port}",
            "anchor": "cfg-auth",
        },
        {
            "id": "ssh",
            "title": "SSH 与节点",
            "ok": nodes_count > 0 and key_ok and INVENTORY_FILE.is_file(),
            "detail": f"{nodes_count} 个节点 · inventory {'已生成' if INVENTORY_FILE.is_file() else '未生成'}",
            "anchor": "cfg-nodes",
        },
        {
            "id": "jobs",
            "title": "Job 与模型",
            "ok": bool(jobs),
            "detail": f"{len(jobs)} 个已安装 Job",
            "anchor": "cfg-job",
        },
        {
            "id": "gateway",
            "title": "入口与 LiteLLM",
            "ok": None,
            "detail": "到「入口与网关」查看 Edge / LiteLLM / 模型",
            "anchor": "endpoints",
        },
        {
            "id": "integration",
            "title": "AI 与 MCP",
            "ok": bool(mcp_exec and mcp_module and assistant.get("ready")),
            "detail": f"MCP {'就绪' if mcp_exec and mcp_module else '待配置'} · AI {'就绪' if assistant.get('ready') else '待配置'}",
            "anchor": "integration",
        },
    ]
    integrations = {
        "api_base": f"http://{api_host}:{api_port}",
        "auth": auth_state,
        "mcp": {
            "path": str(mcp_path),
            "exists": mcp_exists,
            "executable": mcp_exec,
            "module": mcp_module,
        },
        "assistant": assistant,
    }
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
        "steps": steps,
        "integrations": integrations,
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
                user=str(meta.get("ansible_user") or user),
                port=int(meta.get("ansible_port") or port),
                key=expand_path(str(meta.get("ansible_ssh_private_key_file") or key))
                if (meta.get("ansible_ssh_private_key_file") or key)
                else "",
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
    cluster = _read_json(cluster_file(), {})
    ttls = liveness.load_ttls(cluster)
    view = build_liveness_view()
    view_nodes = view.get("nodes") or {}
    total_nodes = len(view_nodes)
    online = sum(1 for n in view_nodes.values() if n.get("state") == "online")
    offline = [name for name, n in view_nodes.items() if n.get("state") == "offline"]
    degraded = [name for name, n in view_nodes.items() if n.get("state") == "degraded"]
    fresh_snapshots = sum(
        1 for n in view_nodes.values() if not (n.get("snapshot") or {}).get("stale")
    )

    ssh_ok = int(ssh.get("ok_count") or 0)
    ssh_total = int(ssh.get("total") or 0)
    api_ok = int(health.get("ok_count") or 0)
    api_total = int(health.get("total") or 0)
    probe_ok = int(probe.get("ok_count") or 0)
    probe_fail = int(probe.get("fail_count") or 0)
    probe_total = probe_ok + probe_fail
    signals = _signals_fresh(ssh, probe, ttls, health)

    gpu_free = 0
    gpu_total = 0
    gpu_excluded = 0
    for name, node in (resources.items() if isinstance(resources, dict) else []):
        if not isinstance(node, dict) or "gpus" not in node:
            continue
        live = view_nodes.get(name) or {}
        counted = (
            live.get("state") == "online"
            and not (live.get("snapshot") or {}).get("stale")
        )
        count = int(node.get("gpu_count") or len(node.get("gpus") or []))
        if counted:
            gpu_total += count
            gpu_free += int(node.get("free_count") or 0)
        else:
            gpu_excluded += count

    components: dict[str, dict] = {
        "nodes": {
            "ok": online,
            "total": total_nodes,
            "fresh": True,
            "offline": offline,
            "degraded": degraded,
        },
        "snapshots": {
            "ok": fresh_snapshots,
            "total": total_nodes,
            "fresh": True,
            "stale": [
                name
                for name, n in view_nodes.items()
                if (n.get("snapshot") or {}).get("stale")
            ],
        },
        "ssh": {
            "ok": ssh_ok,
            "total": ssh_total,
            "fresh": signals["ssh_fresh"],
            "age_sec": signals["ssh_age_sec"],
        },
        "api": {
            "ok": api_ok,
            "total": api_total,
            "fresh": signals["api_fresh"],
            "age_sec": signals["api_age_sec"],
        },
        "mesh": {
            "ok": probe_ok,
            "fail": probe_fail,
            "total": probe_total,
            "fresh": signals["mesh_fresh"],
            "age_sec": signals["mesh_age_sec"],
        },
    }

    scores: list[float] = []
    if total_nodes:
        scores.append(100.0 * online / total_nodes)
        scores.append(100.0 * fresh_snapshots / total_nodes)
    if signals["ssh_fresh"] and ssh_total:
        scores.append(100.0 * ssh_ok / ssh_total)
    if signals["api_fresh"] and api_total:
        scores.append(100.0 * api_ok / api_total)
    if signals["mesh_fresh"] and probe_total:
        scores.append(100.0 * probe_ok / probe_total)
    score = int(sum(scores) / len(scores)) if scores else None
    if score is None:
        grade = "数据不足"
    else:
        grade = "优秀" if score >= 90 else "良好" if score >= 75 else "一般" if score >= 50 else "较差"
    stale_components = [name for name, item in components.items() if not item.get("fresh")]
    if offline:
        hint = f"离线：{', '.join(offline)}"
    elif stale_components:
        hint = "部分数据已过期 · 点「全部复检」"

    else:
        hint = "数据在有效期内。"
    return {
        "score": score,
        "grade": grade,
        "known": score is not None,
        "summary": hint,
        "hint": hint,
        "components": components,
        "stale": stale_components,
        "ssh": {**ssh, "fresh": signals["ssh_fresh"], "age_sec": signals["ssh_age_sec"]},
        "api": {
            "ok": api_ok,
            "total": api_total,
            "mode": health.get("mode"),
            "generated_at": health.get("generated_at"),
            "fresh": signals["api_fresh"],
            "age_sec": signals["api_age_sec"],
            "latency_avg_ms": (
                int(
                    sum(r.get("latency_ms") or 0 for r in (health.get("results") or []) if r.get("ok"))
                    / max(1, sum(1 for r in (health.get("results") or []) if r.get("ok")))
                )
                if any(r.get("ok") for r in (health.get("results") or []))
                else None
            ),
        },
        "mesh": {
            "ok": probe_ok,
            "fail": probe_fail,
            "total": probe_total,
            "fresh": signals["mesh_fresh"],
            "age_sec": signals["mesh_age_sec"],
        },
        "nodes": {
            "online": online,
            "offline": offline,
            "degraded": degraded,
            "total": total_nodes,
            "snapshot_stale": components["snapshots"]["stale"],
        },
        "gpus": {
            "free": gpu_free,
            "total": gpu_total,
            "used": max(0, gpu_total - gpu_free),
            "excluded": gpu_excluded,
        },
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
            payload = loads_relaxed(raw["stdout"] or "{}")
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
        try:
            line = fmt % args
        except (TypeError, ValueError):
            line = str(fmt)
        # 访问日志不带凭据：URL 查询参数里的 token / key / password 一律脱敏。
        line = re.sub(
            r"([?&](?:access_token|token|api_key|key|password)=)[^&\s]+",
            r"\1***",
            line,
            flags=re.IGNORECASE,
        )
        print(f"[{self.log_date_time_string()}] {self.address_string()} {line}")

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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
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

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # 没有 Content-Length 的流：用 close 作为结束信号，避免客户端一直等 keep-alive。
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True

    def _sse_send(self, event: str, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        chunk = f"event: {event}\ndata: {data}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

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

    def _same_origin(self) -> bool:
        """阻止跨站 WebSocket / 写请求（浏览器会带 Origin；CLI/脚本通常不带）。"""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        host = (self.headers.get("Host") or "").strip()
        try:
            host_name = (urlparse(f"//{host}").hostname or "").lower()
        except ValueError:
            return False
        # Cookie 按域名而非端口作用域，比较主机名即可；尊重反代保留的 Host。
        return bool(parsed.hostname and host_name and parsed.hostname.lower() == host_name)

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

    def _call_local_api(self, method: str, path: str, body: dict | None = None, *, timeout: int = 1900) -> dict:
        """从助手执行端点回调本机已有 API，复用同一套校验与锁。"""
        try:
            bound_host = str(self.server.server_address[0])
            port = int(self.server.server_address[1])
        except (AttributeError, IndexError, TypeError, ValueError):
            bound_host = ""
            port = int(os.environ.get("GPU_CONTROL_UI_PORT", "8787"))
        host = bound_host if bound_host not in {"", "0.0.0.0", "::", "::0"} else "127.0.0.1"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # 转发调用者身份，让内部请求重新通过 auth gate。
        for name in ("Authorization", "Cookie"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        req = urllib.request.Request(
            f"http://{host}:{port}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=max(5, timeout)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    payload = loads_relaxed(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"raw": raw[:2000]}
                return {"ok": True, "status": resp.status, "data": payload}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = loads_relaxed(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"error": raw[:1000]}
            return {"ok": False, "status": exc.code, "data": payload}
        except Exception as exc:  # noqa: BLE001 — 内部调用失败要可解释
            return {"ok": False, "status": 0, "error": str(exc), "data": {"error": str(exc)}}

    def _handle_terminal_ws(self, parsed) -> None:
        if not self._same_origin():
            self._json(403, {"ok": False, "error": "跨站 WebSocket 被拒绝"})
            return
        qs = parse_qs(parsed.query)
        # 终端不应支持 URL 里的长期 token（会进历史/日志）；用 Cookie 或 Authorization。
        if qs.get("access_token") or qs.get("token"):
            self._json(403, {"ok": False, "error": "终端连接请使用 Cookie 或 Authorization，勿用 URL token"})
            return
        if not self._auth_gate("/ws/terminal", parsed.query):
            return
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
            rel = unquote(path[len("/static/"):])
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
            target = resolve_help_target(STATIC_DIR, path)
            if target is not None:
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

        if path == "/api/assistant/policy":
            self._json(200, policy_payload())
            return

        if path == "/api/assistant/session":
            qs = parse_qs(parsed.query)
            cid = assistant_session.valid_id((qs.get("conversation_id") or [""])[0])
            if not cid:
                self._json(400, {"ok": False, "error": "conversation_id 不合法"})
                return
            session = assistant_session.load(cid)
            before = json.dumps(session.get("pending") or {}, sort_keys=True, ensure_ascii=False)
            assistant_session.clean_expired_pending(session)
            after = json.dumps(session.get("pending") or {}, sort_keys=True, ensure_ascii=False)
            if before != after:
                assistant_session.save_merged(session)
                session = assistant_session.load(cid)
            raw = session.get("messages") or []
            messages = []
            for row in raw:
                if not isinstance(row, dict):
                    continue
                role = str(row.get("role") or "")
                content = str(row.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
            pending = [
                assistant_session.public_pending(row)
                for row in (session.get("pending") or {}).values()
                if isinstance(row, dict)
                and str(row.get("status") or "pending") in {"pending", "armed"}
                and not assistant_session.action_expired(row)
            ]
            self._json(
                200,
                {
                    "ok": True,
                    "conversation_id": cid,
                    "exists": bool(raw or pending),
                    "messages": messages[-80:],
                    "pending_actions": pending,
                },
            )
            return

        if path == "/api/assistant/status":
            cluster = _read_json(cluster_file(), {})
            overview = build_overview(light=True)
            jobs = overview.get("jobs") if isinstance(overview, dict) else []
            self._json(
                200,
                assistant_status(cluster, context={"overview": overview, "jobs": jobs or []}),
            )
            return

        if path == "/api/assistant/models":
            qs = parse_qs(parsed.query)
            base_url = (qs.get("base_url") or [None])[0]
            try:
                payload = list_upstream_models(base_url=base_url or None)
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                info = format_upstream_error(exc, base_url=base_url or "")
                self._json(502, {"ok": False, **info})
                return
            self._json(200, payload)
            return

        if path == "/api/exec/suggest":
            qs = parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            self._json(200, {"query": q, "items": suggest_exec_commands(q, limit=limit)})
            return

        if path == "/api/doctor":
            result = run_cli(["doctor", "--json"], timeout=30)
            try:
                payload = loads_relaxed(result["stdout"] or "{}")
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

        if path == "/api/logs":
            qs = parse_qs(parsed.query)
            name = (qs.get("name") or ["ui"])[0]
            try:
                lines = int((qs.get("lines") or ["200"])[0])
            except ValueError:
                lines = 200
            lines = max(1, min(lines, 2000))
            log_files = {
                "ui": STATE_DIR / "ui.log",
                "watchdog": STATE_DIR / "watchdog.log",
                "ingress-edge": STATE_DIR / "ingress-edge.log",
                "ingress-litellm": STATE_DIR / "ingress-litellm.log",
                "cloudflared": STATE_DIR / "cloudflared.log",
                "runlog": STATE_DIR / "runlog.jsonl",
            }
            log_path = log_files.get(name)
            if log_path is None:
                self._json(400, {"ok": False, "error": f"unknown log: {name}"})
                return
            if not log_path.is_file():
                self._json(
                    200,
                    {
                        "ok": True,
                        "name": name,
                        "path": str(log_path),
                        "exists": False,
                        "lines": [],
                        "size": 0,
                    },
                )
                return
            stat = log_path.stat()
            self._json(
                200,
                {
                    "ok": True,
                    "name": name,
                    "path": str(log_path),
                    "exists": True,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "lines": _tail_lines(log_path, lines),
                },
            )
            return

        if path == "/api/diagnose":
            result = run_cli(["diagnose", "check"], timeout=60)
            try:
                payload = loads_relaxed(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "error": "diagnose parse failed", "result": result}
            findings = payload.get("findings") if isinstance(payload, dict) else None
            if isinstance(findings, list):
                upsert_notices(findings_to_notices(findings))
            self._json(200, payload)
            return

        if path == "/api/notices":
            qs = parse_qs(parsed.query)
            sync = (qs.get("sync") or ["0"])[0] in {"1", "true", "yes"}
            if sync:
                overview = build_overview(light=True)
                rows = overview_to_notices(overview)
                result = run_cli(["diagnose", "check"], timeout=60)
                try:
                    diag = loads_relaxed(result["stdout"] or "{}")
                except json.JSONDecodeError:
                    diag = {}
                findings = diag.get("findings") if isinstance(diag, dict) else None
                if isinstance(findings, list):
                    rows.extend(findings_to_notices(findings))
                if rows:
                    upsert_notices(rows)
            include_dismissed = (qs.get("all") or ["0"])[0] in {"1", "true", "yes"}
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            self._json(200, list_notices(include_dismissed=include_dismissed, limit=limit))
            return

        if path == "/api/overview":
            qs = parse_qs(parsed.query)
            if qs.get("refresh", ["0"])[0] in {"1", "true", "yes"}:
                with _action_lock:
                    result = run_cli(["gpu", "refresh", "--quiet"], timeout=180)
                if result["rc"] != 0:
                    self._json(500, {"error": "refresh failed", "result": result})
                    return
            etag = state_fingerprint(bucket_sec=60)
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

        if path == "/api/remote":
            query = parse_qs(parsed.query)
            mode = (query.get("mode") or [""])[0] or None
            want_plan = str((query.get("plan") or ["0"])[0]).lower() in {"1", "true", "yes"}
            if want_plan or mode:
                payload = remote_access.plan(mode=mode or "auto")
            else:
                st = remote_access.status()
                payload = {
                    **st,
                    "summary": remote_access.summary_line(st),
                    "recommended": remote_access.plan(mode="auto"),
                }
            self._json(200, payload)
            return

        if path == "/api/liveness":
            query = parse_qs(parsed.query)
            refresh = str((query.get("check") or ["0"])[0]).lower() in {"1", "true", "yes"}
            view = build_liveness_view(refresh=refresh)
            self._json(
                200,
                {
                    "ok": True,
                    **view,
                    "resources_health": _read_json(RESOURCES_HEALTH_FILE, {}),
                },
            )
            return

        if path == "/api/metrics":
            self._json(200, _read_json(HOST_METRICS_FILE, {"results": [], "nodes": {}}))
            return

        if path == "/api/metrics/history":
            qs = parse_qs(parsed.query)
            try:
                limit = int((qs.get("limit") or ["60"])[0])
            except ValueError:
                limit = 60
            self._json(200, list_history(limit=limit))
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

        if path == "/api/gateway/litellm/master-key":
            self._json(200, {"ok": True, **litellm_master_key_status()})
            return

        if path == "/api/gateway/litellm/models":
            try:
                payload = litellm_models_payload()
            except Exception as exc:  # noqa: BLE001 — LiteLLM 未运行 / 未配密钥
                self._json(502, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True, **payload})
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
            client_key = self.client_address[0] if self.client_address else "unknown"
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
            if login_rate_limited(client_key):
                self._json(
                    429,
                    {
                        "ok": False,
                        "error": "登录失败次数过多，请稍后再试",
                        "auth": auth_status_payload(cfg=cfg),
                    },
                )
                return
            password = str(body.get("password") or "")
            principal = try_login(password, cfg=cfg)
            if not principal:
                record_login_failure(client_key)
                time.sleep(0.35)
                self._json(401, {"ok": False, "error": "invalid password"})
                return
            clear_login_failures(client_key)
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

        if path.startswith("/api") and not self._same_origin():
            self._json(403, {"ok": False, "error": "跨站写请求被拒绝"})
            return

        if path.startswith("/api") and not self._auth_gate(path, parsed.query):
            return

        if path == "/api/auth/token":
            try:
                req = AuthTokenRequest.model_validate(body)
            except ValidationError as exc:
                self._json(400, {"ok": False, "error": exc.errors()[0].get("msg", "invalid body")})
                return
            cfg = load_auth_config()
            action = req.action
            if action == "clear":
                if cfg.api_token_file.is_file():
                    cfg.api_token_file.unlink()
                self._json(200, {"ok": True, "auth": auth_status_payload(cfg=cfg)})
                return
            token, token_path = issue_api_token(cfg=cfg)
            self._json(
                200,
                {
                    "ok": True,
                    "token": token,
                    "path": str(token_path),
                    "auth": auth_status_payload(cfg=cfg),
                },
            )
            return

        if path == "/api/auth/password":
            password = str(body.get("password") or "")
            enable = bool(body.get("enable", True))
            if len(password) < 8:
                self._json(400, {"ok": False, "error": "密码至少 8 位"})
                return
            try:
                hash_path = set_password(password)
                if enable:
                    save_auth_mode("password")
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            try:
                os.chmod(hash_path, 0o600)
            except OSError:
                pass
            self._json(
                200,
                {
                    "ok": True,
                    "path": str(hash_path),
                    "enabled": enable,
                    "auth": auth_status_payload(),
                },
            )
            return

        if path == "/api/auth/oidc/check":
            cfg = load_auth_config()
            try:
                probe = probe_oidc(oidc_config(cfg))
            except Exception as exc:  # noqa: BLE001 — IdP 不可达是诊断结果
                self._json(502, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": bool(probe.get("ready")), **probe})
            return

        if path == "/api/assistant/config":
            base_url = str(body.get("base_url") or "").strip()
            model = str(body.get("model") or "").strip()
            enabled = bool(body.get("enabled", True))
            clear_key = bool(body.get("clear_api_key"))
            api_key = body.get("api_key")
            if api_key is not None and not isinstance(api_key, str):
                self._json(400, {"ok": False, "error": "api_key 须为字符串"})
                return
            timeout_raw = body.get("timeout_sec")
            timeout_sec = int(timeout_raw) if timeout_raw is not None else None
            try:
                status = save_assistant_config(
                    base_url=base_url,
                    model=model,
                    enabled=enabled,
                    timeout_sec=timeout_sec,
                    api_key=api_key,
                    clear_api_key=clear_key,
                )
            except (OSError, ValueError, TypeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True, **status})
            return

        if path == "/api/assistant/models":
            # 允许用草稿 Base URL / Key 试拉，不必先保存
            draft_base = body.get("base_url")
            draft_key = body.get("api_key")
            base_arg = str(draft_base).strip() if isinstance(draft_base, str) else None
            try:
                payload = list_upstream_models(
                    base_url=base_arg,
                    api_key=str(draft_key) if isinstance(draft_key, str) else None,
                )
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                info = format_upstream_error(exc, base_url=base_arg or "")
                self._json(502, {"ok": False, **info})
                return
            self._json(200, payload)
            return

        if path == "/api/assistant/probe":
            draft_base = body.get("base_url")
            draft_key = body.get("api_key")
            base_arg = str(draft_base).strip() if isinstance(draft_base, str) else None
            try:
                payload = probe_upstream(
                    base_url=base_arg,
                    api_key=str(draft_key) if isinstance(draft_key, str) else None,
                )
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            # 探测失败也用 200 + ok:false，便于 UI 展示 dns/hint 结构化字段
            self._json(200, payload)
            return

        if path == "/api/notices/mark":
            ids = body.get("ids")
            if ids is not None and not isinstance(ids, list):
                self._json(400, {"ok": False, "error": "ids 须为数组"})
                return
            read = body.get("read")
            dismissed = body.get("dismissed")
            if read is None and dismissed is None:
                self._json(400, {"ok": False, "error": "须提供 read 或 dismissed"})
                return
            payload = mark_notices(
                ids=[str(x) for x in ids] if isinstance(ids, list) else None,
                read=bool(read) if read is not None else None,
                dismissed=bool(dismissed) if dismissed is not None else None,
            )
            invalidate_overview_cache()
            self._json(200, {"ok": True, **payload})
            return

        if path == "/api/assistant/analyze":
            cluster = _read_json(cluster_file(), {})
            overview = build_overview(light=True)
            upsert_notices(overview_to_notices(overview))
            result = run_cli(["diagnose", "check"], timeout=60)
            try:
                diag = loads_relaxed(result["stdout"] or "{}")
            except json.JSONDecodeError:
                diag = {}
            findings = diag.get("findings") if isinstance(diag, dict) else []
            if isinstance(findings, list):
                upsert_notices(findings_to_notices(findings))
            notices = list_notices(limit=30).get("items") or []
            out = analyze_dashboard(
                overview=overview,
                notices=notices,
                findings=findings if isinstance(findings, list) else [],
                cluster=cluster,
            )
            invalidate_overview_cache()
            self._json(200, out)
            return

        if path == "/api/assistant/chat":
            messages = body.get("messages")
            if messages is not None and not isinstance(messages, list):
                self._json(400, {"ok": False, "error": "messages 须为数组"})
                return
            cluster = _read_json(cluster_file(), {})
            overview = build_overview(light=True)
            jobs_payload = load_job_summaries()
            if isinstance(jobs_payload, list):
                jobs_list = jobs_payload
            elif isinstance(jobs_payload, dict):
                jobs_list = list(jobs_payload.get("jobs") or [])
            else:
                jobs_list = []
            live_view = build_liveness_view()
            nodes_meta = _read_json(nodes_file(), {}).get("nodes") or {}

            cid = assistant_session.valid_id(body.get("conversation_id"))
            if cid:
                session = assistant_session.load(cid)
                history = list(session.get("messages") or [])
                # 只追加本轮最后一条用户消息，避免客户端把整段历史重复塞入
                new_text = body.get("message")
                if not isinstance(new_text, str) or not new_text.strip():
                    new_text = ""
                    for row in reversed(messages or []):
                        if (
                            isinstance(row, dict)
                            and row.get("role") == "user"
                            and str(row.get("content") or "").strip()
                        ):
                            new_text = str(row["content"]).strip()
                            break
                if new_text and not (
                    history
                    and history[-1].get("role") == "user"
                    and history[-1].get("content") == new_text
                ):
                    history.append({"role": "user", "content": new_text})
            else:
                cid = assistant_session.new_id()
                session = assistant_session.load(cid)
                new_text = body.get("message")
                if not isinstance(new_text, str) or not new_text.strip():
                    new_text = ""
                    for row in reversed(messages or []):
                        if (
                            isinstance(row, dict)
                            and row.get("role") == "user"
                            and str(row.get("content") or "").strip()
                        ):
                            new_text = str(row["content"]).strip()
                            break
                history = (
                    [{"role": "user", "content": new_text}]
                    if new_text
                    else (messages if isinstance(messages, list) else None)
                )

            context = {
                "cluster": cluster,
                "overview": overview,
                "overview_light": compact_overview(overview),
                "jobs": jobs_list,
                "generated_at": overview.get("generated_at"),
            }
            runners = assistant_runners(jobs_list)
            known_jobs = assistant_known_jobs(jobs_list)

            def _active_pending() -> list[dict]:
                pending = session.get("pending") or {}
                return [
                    assistant_session.public_pending(row)
                    for row in pending.values()
                    if isinstance(row, dict)
                    and str(row.get("status") or "pending") in {"pending", "armed"}
                ]

            def _persist_session(*, messages=None, pending_full=None) -> None:
                if messages is not None:
                    session["messages"] = messages
                for action in pending_full or []:
                    assistant_session.put_pending(session, action)
                assistant_session.clean_expired_pending(session)
                assistant_session.save_merged(session)
                # 重新加载，保证后续 _active_pending 看到磁盘终态优先后的结果。
                refreshed = assistant_session.load(cid)
                session.clear()
                session.update(refreshed)

            if bool(body.get("stream")):
                self._sse_start()
                self._sse_send("meta", {"conversation_id": cid})
                stream_state: dict = {}
                try:
                    for event, payload in stream_chat(
                        history=history,
                        context=context,
                        runners=runners,
                        known_nodes=nodes_meta,
                        known_jobs=known_jobs,
                        liveness_view=live_view,
                        state=stream_state,
                    ):
                        if event == "done":
                            _persist_session(
                                messages=stream_state.get("history") or session.get("messages") or [],
                                pending_full=stream_state.get("pending_full") or [],
                            )
                            payload["conversation_id"] = cid
                            active = _active_pending()
                            if active:
                                payload["pending_actions"] = active
                            self._sse_send("done", payload)
                        elif event == "error":
                            _persist_session(
                                messages=stream_state.get("history") or session.get("messages") or [],
                                pending_full=stream_state.get("pending_full") or [],
                            )
                            active = _active_pending()
                            if active:
                                payload["pending_actions"] = active
                            payload["conversation_id"] = cid
                            self._sse_send("error", payload)
                        else:
                            self._sse_send(event, payload)
                except (BrokenPipeError, ConnectionResetError):
                    _persist_session(
                        messages=stream_state.get("history"),
                        pending_full=stream_state.get("pending_full") or [],
                    )
                    return
                except Exception as exc:  # noqa: BLE001 — SSE 已开始，只能回错误事件
                    _persist_session(
                        messages=stream_state.get("history"),
                        pending_full=stream_state.get("pending_full") or [],
                    )
                    try:
                        self._sse_send("error", {"error": "internal_error", "reply": str(exc)})
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                return

            result = handle_chat(
                history=history,
                messages=messages if history is None else None,
                context=context,
                runners=runners,
                known_nodes=nodes_meta,
                known_jobs=known_jobs,
                liveness_view=live_view,
            )
            _persist_session(
                messages=result.get("history") if result.get("history") is not None else None,
                pending_full=result.pop("pending_actions_full", None) or [],
            )
            active = _active_pending()
            if active:
                result["pending_actions"] = active
            result["conversation_id"] = cid
            # 400 会让前端 api() 丢掉 reply/open_settings；未配置上游也是有效回复。
            self._json(200, result)
            return

        if path == "/api/assistant/execute":
            cid = assistant_session.valid_id(body.get("conversation_id"))
            action_id = str(body.get("action_id") or "").strip()
            if not cid or not action_id:
                self._json(
                    400,
                    {"ok": False, "error": "conversation_id and action_id required"},
                )
                return
            session = assistant_session.load(cid)
            action = assistant_session.get_pending(session, action_id)
            if action is None:
                self._json(
                    409,
                    {"ok": False, "error": "action_not_found", "detail": "确认卡不存在或会话已过期"},
                )
                return
            stage = str(body.get("stage") or "execute").lower()
            if stage == "reject":
                assistant_session.update_pending(session, action_id, status="rejected")
                assistant_session.save_merged(session)
                self._json(200, {"ok": True, "status": "rejected", "conversation_id": cid})
                return
            status = str(action.get("status") or "pending")
            claim_key = (cid, action_id)

            def _already_executed() -> bool:
                self._json(
                    200,
                    {
                        "ok": True,
                        "status": "executed",
                        "already": True,
                        "conversation_id": cid,
                        "action_id": action_id,
                        "tool": action.get("tool"),
                        "result": action.get("result"),
                        "reply": "该操作已经执行过，无需重复确认。",
                        "pending_actions": [
                            assistant_session.public_pending(row)
                            for row in (assistant_session.load(cid).get("pending") or {}).values()
                            if isinstance(row, dict)
                            and str(row.get("status") or "pending") in {"pending", "armed"}
                        ],
                    },
                )
                return True

            if status == "executed":
                _already_executed()
                return
            if status == "executing":
                with _assistant_action_lock:
                    if claim_key in _assistant_inflight:
                        self._json(
                            409,
                            {
                                "ok": False,
                                "error": "action_in_progress",
                                "detail": "该操作正在执行，请稍候。",
                                "action": assistant_session.public_pending(action),
                            },
                        )
                        return
                    # 服务重启后残留的 executing：超过 5 分钟允许重新领取
                    updated = assistant_session.parse_time(action.get("updated_at"))
                    if updated is not None and time.time() - updated < 300:
                        self._json(
                            409,
                            {"ok": False, "error": "action_in_progress", "detail": "该操作正在执行，请稍候。"},
                        )
                        return
                    status = "pending"
            if status not in {"pending", "armed"}:
                self._json(
                    409,
                    {"ok": False, "error": "action_not_pending", "detail": f"当前状态：{status}"},
                )
                return
            if assistant_session.action_expired(action):
                assistant_session.update_pending(session, action_id, status="expired")
                assistant_session.save_merged(session)
                self._json(409, {"ok": False, "error": "action_expired", "detail": "确认卡已过期，请重新提问"})
                return
            provided_hash = str(body.get("args_hash") or "").strip()
            expected_hash = str(action.get("args_hash") or "").strip()
            if not provided_hash or not expected_hash or provided_hash != expected_hash:
                self._json(
                    409,
                    {"ok": False, "error": "args_mismatch", "detail": "参数与提案不一致，请重新提问"},
                )
                return
            confirm = body.get("confirm")
            if action.get("confirm_level") == "confirm_twice":
                if str(confirm).upper() != "EXECUTE":
                    self._json(
                        409,
                        {
                            "ok": False,
                            "error": "needs_second_confirm",
                            "detail": "破坏性操作需要二次确认（confirm=\"EXECUTE\"）",
                            "action": assistant_session.public_pending(action),
                        },
                    )
                    return
            elif confirm is not True:
                self._json(
                    409,
                    {
                        "ok": False,
                        "error": "needs_confirm",
                        "detail": "写操作需要用户确认",
                        "action": assistant_session.public_pending(action),
                    },
                )
                return

            jobs_list = load_job_summaries()
            if isinstance(jobs_list, dict):
                jobs_list = list(jobs_list.get("jobs") or [])
            nodes_meta = _read_json(nodes_file(), {}).get("nodes") or {}
            live_view = build_liveness_view()
            validation = assistant_validate_call(
                str(action.get("tool") or ""),
                action.get("args") or {},
                nodes=nodes_meta,
                jobs=assistant_known_jobs(jobs_list),
                liveness_view=live_view,
            )
            if not validation.get("ok"):
                self._json(
                    409,
                    {
                        "ok": False,
                        "error": validation.get("error") or "validation_failed",
                        "detail": validation.get("detail"),
                        "action": assistant_session.public_pending(action),
                    },
                )
                return
            route = assistant_route_for(str(action.get("tool") or ""), action.get("args") or {})
            if not route:
                self._json(
                    400,
                    {"ok": False, "error": "no_route", "detail": "该工具没有可执行的路由"},
                )
                return

            # 原子领取：双击 / 重复请求只能有一个真正执行；已执行则幂等返回。
            with _assistant_action_lock:
                session = assistant_session.load(cid)
                fresh = assistant_session.get_pending(session, action_id)
                if fresh is None:
                    self._json(409, {"ok": False, "error": "action_not_found", "detail": "确认卡不存在"})
                    return
                fresh_status = str(fresh.get("status") or "pending")
                if fresh_status == "executed":
                    self._json(
                        200,
                        {
                            "ok": True,
                            "status": "executed",
                            "already": True,
                            "conversation_id": cid,
                            "action_id": action_id,
                            "tool": fresh.get("tool"),
                            "result": fresh.get("result"),
                            "reply": "该操作已经执行过，无需重复确认。",
                            "pending_actions": [],
                        },
                    )
                    return
                if fresh_status == "executing":
                    updated = assistant_session.parse_time(fresh.get("updated_at"))
                    # 进程内正在执行，或 5 分钟内的 executing 视为有效；
                    # 服务重启后残留的 executing 允许重新领取。
                    if claim_key in _assistant_inflight or (
                        updated is not None and time.time() - updated < 300
                    ):
                        self._json(
                            409,
                            {
                                "ok": False,
                                "error": "action_in_progress",
                                "detail": "该操作正在执行，请稍候。",
                                "action": assistant_session.public_pending(fresh),
                            },
                        )
                        return
                elif fresh_status not in {"pending", "armed"}:
                    self._json(
                        409,
                        {
                            "ok": False,
                            "error": "action_not_pending",
                            "detail": f"当前状态：{fresh_status}",
                            "action": assistant_session.public_pending(fresh),
                        },
                    )
                    return
                action = fresh
                assistant_session.update_pending(
                    session,
                    action_id,
                    status="executing",
                    started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                assistant_session.save_merged(session)
                _assistant_inflight.add(claim_key)
            try:
                result = self._call_local_api(
                    str(route.get("method") or "POST"),
                    str(route.get("path") or ""),
                    route.get("body"),
                    timeout=1900,
                )
                data = compact_action_result(result.get("data"))
                executed_ok = bool(result.get("ok")) and not (
                    isinstance(data, dict) and data.get("ok") is False
                )

                def _finish_action(current: dict) -> dict:
                    assistant_session.record_tool_result(
                        current,
                        call_id=str(action.get("call_id") or ""),
                        result=data,
                    )
                    assistant_session.update_pending(
                        current,
                        action_id,
                        status="executed" if executed_ok else "failed",
                        executed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        result_summary=(data.get("message") if isinstance(data, dict) else None)
                        or ("执行成功" if executed_ok else "执行失败"),
                        result=data,
                    )
                    return current

                session = assistant_session.update(cid, _finish_action)
            finally:
                with _assistant_action_lock:
                    _assistant_inflight.discard(claim_key)

            reply = None
            follow_pending: list[dict] = []
            if executed_ok and body.get("continue", True):
                cluster = _read_json(cluster_file(), {})
                context = {
                    "cluster": cluster,
                    "overview": None,
                    "overview_light": compact_overview(build_overview(light=True)),
                    "jobs": jobs_list,
                }
                cont = handle_chat(
                    history=session.get("messages") or [],
                    context=context,
                    runners=assistant_runners(jobs_list),
                    known_nodes=nodes_meta,
                    known_jobs=assistant_known_jobs(jobs_list),
                    liveness_view=build_liveness_view(),
                )
                if cont.get("history") is not None:
                    session["messages"] = cont["history"]
                for row in cont.pop("pending_actions_full", None) or []:
                    assistant_session.put_pending(session, row)
                assistant_session.clean_expired_pending(session)
                assistant_session.save_merged(session)
                session = assistant_session.load(cid)
                if cont.get("ok") is False:
                    reply = (
                        "操作已执行，但模型总结暂不可用："
                        f"{cont.get('reply') or cont.get('error') or '上游异常'}"
                    )
                else:
                    reply = cont.get("reply")
                follow_pending = cont.get("pending_actions") or []
            pending_map = session.get("pending") or {}
            active = [
                assistant_session.public_pending(row)
                for row in pending_map.values()
                if isinstance(row, dict)
                and str(row.get("status") or "pending") in {"pending", "armed"}
            ]
            self._json(
                200,
                {
                    "ok": executed_ok,
                    "status": "executed" if executed_ok else "failed",
                    "conversation_id": cid,
                    "action_id": action_id,
                    "tool": action.get("tool"),
                    "http_status": result.get("status"),
                    "result": data,
                    "reply": reply
                    or (
                        "操作已执行。"
                        if executed_ok
                        else f"操作失败：{exec_failure_detail(data, result)}"
                    ),
                    "pending_actions": active or follow_pending,
                },
            )
            return

        if path == "/api/resources/refresh":
            with _action_lock:
                result = run_cli(["gpu", "refresh", "--quiet"], timeout=180)
            health = _read_json(RESOURCES_HEALTH_FILE, {})
            fresh = health.get("ok") or []
            stale = health.get("stale") or []
            failed = health.get("failed") or []
            ok = result["rc"] == 0 and not stale and not failed
            attempted = health.get("attempted") or []
            if ok:
                message = f"已刷新 {len(fresh)}/{len(attempted)} 个节点 GPU 快照"
            else:
                parts = []
                if stale:
                    parts.append(f"{len(stale)} 个节点快照仍过期（{', '.join(str(x.get('node') if isinstance(x, dict) else x) for x in stale)}）")
                if failed:
                    parts.append(f"{len(failed)} 个节点未采集（{', '.join(str(x) for x in failed)}）")
                message = f"刷新 {len(fresh)}/{len(attempted)} 个节点；" + "；".join(parts)
            self._json(
                200 if result["rc"] == 0 else 500,
                {
                    "ok": ok,
                    "message": message,
                    "fresh": fresh,
                    "stale": stale,
                    "failed": failed,
                    "result": result,
                    "overview": build_overview(),
                },
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
            write_secret_file(path_obj, token + "\n")
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
            if body.get("no_wait"):
                args.append("--no-wait")
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

        if path == "/api/leases/release":
            nodes = body.get("nodes")
            lease_id = str(body.get("id") or "").strip()
            if not lease_id and not nodes:
                self._json(400, {"error": "id or nodes required"})
                return
            args = ["lease", "release"]
            if lease_id:
                args.extend(["--id", lease_id])
            else:
                args.extend(["--nodes", str(nodes)])
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

        if path == "/api/liveness/check":
            only = body.get("nodes") or body.get("node")
            if isinstance(only, str):
                only = [x.strip() for x in only.split(",") if x.strip()]
            snapshot = run_liveness_check() if not only else None
            if snapshot is None:
                nodes_doc = _read_json(nodes_file(), {"defaults": {}, "nodes": {}})
                cluster = _read_json(cluster_file(), {})
                snapshot = liveness.run_check(
                    nodes_doc,
                    cluster=cluster,
                    snapshot_path=LIVENESS_FILE,
                    only=list(only),
                )
                invalidate_overview_cache()
            self._json(
                200,
                {
                    "ok": True,
                    **snapshot,
                    "liveness": build_liveness_view(),
                },
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
                payload = loads_relaxed(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "result": result}
            self._json(200 if result["rc"] == 0 else 500, {**payload, "result": result})
            return

        if path == "/api/gateway/stop":
            kind = str(body.get("kind") or "all").strip() or "all"
            with _action_lock:
                result = run_cli(["gateway", "stop", "--kind", kind], timeout=30)
            try:
                payload = loads_relaxed(result["stdout"] or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "result": result}
            self._json(200 if result["rc"] == 0 else 500, {**payload, "result": result})
            return

        if path == "/api/gateway/litellm/models":
            try:
                req = LiteLLMModelRequest.model_validate(body)
            except ValidationError as exc:
                self._json(400, {"ok": False, "error": exc.errors()[0].get("msg", "invalid body")})
                return
            payload = req.model_dump(exclude_none=True)
            try:
                with _action_lock:
                    result = (
                        delete_litellm_model(payload)
                        if req.action == "delete"
                        else add_litellm_model(payload)
                    )
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 — LiteLLM / DB 未就绪
                self._json(502, {"ok": False, "error": str(exc)})
                return
            invalidate_gateway_cache()
            try:
                models = litellm_models_payload().get("models") or []
            except Exception:  # noqa: BLE001
                models = []
            self._json(200, {"ok": True, "result": result, "models": models})
            return

        if path == "/api/gateway/litellm/master-key":
            clear = bool(body.get("clear"))
            generate = bool(body.get("generate"))
            restart = body.get("restart")
            if restart is None:
                restart = True
            restart = bool(restart)
            master_key = str(body.get("master_key") or body.get("key") or "").strip()
            revealed = None
            with _action_lock:
                if clear:
                    clear_litellm_master_key()
                elif generate:
                    revealed = generate_litellm_master_key()
                    write_litellm_master_key(revealed)
                elif master_key:
                    write_litellm_master_key(master_key)
                else:
                    self._json(
                        400,
                        {
                            "error": "请提供 master_key，或 generate=true / clear=true",
                            **litellm_master_key_status(),
                        },
                    )
                    return
                restart_result = restart_litellm() if restart else None
            # 让 gateway status 缓存立刻反映新密钥状态
            with _GATEWAY_LOCK:
                _GATEWAY_CACHE["at"] = 0.0
                _GATEWAY_CACHE["payload"] = None
            payload = {
                "ok": True,
                **litellm_master_key_status(),
                "restart": restart_result,
                "status": _gateway_status_fresh(),
            }
            if revealed:
                payload["master_key"] = revealed
                payload["note"] = "请立即复制 Master Key；之后接口不再回显明文"
            self._json(200, payload)
            return

        if path == "/api/remote/action":
            action = str(body.get("action") or "status").strip().lower()
            mode = str(body.get("mode") or "auto").strip()
            port = body.get("port")
            try:
                port = int(port) if port not in (None, "") else None
            except (TypeError, ValueError):
                self._json(400, {"ok": False, "error": "port 必须是整数"})
                return
            if action == "status":
                self._json(200, remote_access.status())
                return
            if action == "plan":
                self._json(200, remote_access.plan(mode=mode, port=port))
                return
            if action == "up":
                payload = remote_access.up(
                    mode=mode,
                    port=port,
                    confirm=bool(body.get("confirm")),
                    force=bool(body.get("force")),
                )
                self._json(200 if payload.get("ok") else 409, payload)
                return
            if action == "down":
                payload = remote_access.down(
                    mode=mode,
                    confirm=bool(body.get("confirm")),
                )
                self._json(200 if payload.get("ok") else 409, payload)
                return
            self._json(400, {"ok": False, "error": "action 须为 status|plan|up|down"})
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
            if body.get("user"):
                args.extend(["--user", str(body["user"]).strip()])
            if body.get("port"):
                try:
                    port = int(body["port"])
                except (TypeError, ValueError):
                    self._json(400, {"error": "port 必须是整数"})
                    return
                if not 1 <= port <= 65535:
                    self._json(400, {"error": "port 必须在 1..65535"})
                    return
                args.extend(["--port", str(port)])
            if body.get("key"):
                args.extend(["--key", str(body["key"]).strip()])
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
            # 部分 / 全部主机失败仍返回结构化结果；HTTP 200 表示“请求已处理”，
            # 失败细节交给 ok / results / result 字段，避免离线节点把整页打成 500。
            successes = [row for row in results if exec_row_ok(row)]
            self._json(
                200,
                {
                    "ok": bool(successes),
                    "error": None if successes else exec_failure_detail({"results": results}, result),
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
            successes = [row for row in results if exec_row_ok(row)]
            self._json(
                200,
                {
                    "ok": bool(successes),
                    "error": None if successes else exec_failure_detail({"results": results}, result),
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
                payload = loads_relaxed(result["stdout"] or "{}")
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
                report = loads_relaxed(completed.stdout or "{}")
            except json.JSONDecodeError:
                report = {
                    "ok": False,
                    "error": "metrics parse failed",
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            ok_count = int((report or {}).get("ok_count") or 0)
            self._json(
                200,
                {
                    "ok": ok_count > 0,
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


def start_liveness_monitor(stop_event: threading.Event) -> threading.Thread | None:
    """后台节点探活：默认每 ``cluster.health.check_interval_sec`` 秒一轮。"""
    if str(os.environ.get("GPU_CONTROL_LIVENESS_MONITOR", "1")).lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return None
    cluster = _read_json(cluster_file(), {})
    cfg = (cluster.get("health") or {}) if isinstance(cluster, dict) else {}
    if cfg.get("monitor") is False:
        return None
    interval = max(10, int(cfg.get("check_interval_sec") or liveness.DEFAULT_TTLS["check_interval_sec"]))

    def loop() -> None:
        while not stop_event.is_set():
            try:
                snapshot = run_liveness_check()
                summary = snapshot.get("summary") or {}
                print(
                    "liveness: "
                    f"online={summary.get('online', 0)} "
                    f"degraded={summary.get('degraded', 0)} "
                    f"offline={summary.get('offline', 0)} "
                    f"unknown={summary.get('unknown', 0)}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 — 后台线程不能拖垮 UI
                print(f"liveness monitor error: {exc}", file=sys.stderr, flush=True)
            stop_event.wait(interval)

    thread = threading.Thread(target=loop, daemon=True, name="liveness-monitor")
    thread.start()
    return thread


def _auto_ops_cfg(cluster: dict) -> dict:
    """自动巡检配置：按各健康信号的 TTL 在过期前采集。"""
    health = (cluster.get("health") or {}) if isinstance(cluster, dict) else {}
    raw = health.get("auto_refresh", True)
    if raw is False:
        return {"enabled": False}
    cfg = raw if isinstance(raw, dict) else {}
    ttls = liveness.load_ttls(cluster)

    def due(key: str, ttl: float, floor: int = 60) -> int:
        value = cfg.get(key)
        try:
            seconds = int(value) if value not in (None, "") else int(float(ttl) * 0.6)
        except (TypeError, ValueError):
            seconds = int(float(ttl) * 0.6)
        return max(floor, seconds)

    gpu_stale = due("gpu_stale_sec", ttls["snapshot_ttl_sec"], 120)
    gpu_min = cfg.get("gpu_min_interval_sec")
    try:
        gpu_min_sec = max(60, int(gpu_min)) if gpu_min not in (None, "") else gpu_stale
    except (TypeError, ValueError):
        gpu_min_sec = gpu_stale
    assistant = (cluster.get("assistant") or {}) if isinstance(cluster, dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "interval_sec": max(20, int(cfg.get("interval_sec") or 60)),
        "ssh_stale_sec": due("ssh_stale_sec", ttls["ssh_ttl_sec"], 60),
        "host_stale_sec": due("host_stale_sec", ttls["host_ttl_sec"], 60),
        "api_stale_sec": due("api_stale_sec", ttls["api_ttl_sec"], 60),
        "mesh_stale_sec": due("mesh_stale_sec", ttls["mesh_ttl_sec"], 120),
        "gpu_stale_sec": gpu_stale,
        "gpu_min_interval_sec": gpu_min_sec,
        "ai_enabled": bool(assistant.get("auto_insight", True)),
        "ai_interval_sec": max(300, int(assistant.get("auto_insight_interval_sec") or 1800)),
    }


def _age_sec(path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _due(age: float | None, stale_sec: int) -> bool:
    return age is None or age > stale_sec


def _auto_ops_tick(cluster: dict, cfg: dict, state: dict) -> dict:
    """一轮自动巡检：在各类健康信号过期前采集；有异常才让 AI 做只读解读。"""
    now_epoch = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
    actions: list[str] = []
    state.update(
        {
            "enabled": bool(cfg.get("enabled")),
            "checked_at": stamp,
            "interval_sec": cfg.get("interval_sec"),
        }
    )
    if not cfg.get("enabled"):
        return state

    def record(name: str, ok: bool, age: float | None) -> None:
        state[f"last_{name}"] = {
            "at": stamp,
            "at_epoch": now_epoch,
            "ok": bool(ok),
            "age_sec": round(age, 1) if age is not None else None,
        }
        actions.append(name)

    ssh_path = STATE_DIR / "ssh_quality.json"
    ssh_age = _age_sec(ssh_path)
    if _due(ssh_age, cfg["ssh_stale_sec"]):
        result = test_ssh_connectivity(timeout=6)
        record("ssh", bool(result.get("ok")), ssh_age)

    host_age = _age_sec(HOST_METRICS_FILE)
    if _due(host_age, cfg["host_stale_sec"]):
        completed = subprocess.run(
            [str(VENV_PYTHON), str(PROJECT_DIR / "scripts" / "collect_host_metrics.py")],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        record("host", completed.returncode == 0, host_age)

    api_age = _age_sec(ENDPOINT_HEALTH_FILE)
    if _due(api_age, cfg["api_stale_sec"]):
        result = run_cli(["health", "--json"], timeout=120)
        record("api", result.get("rc") == 0, api_age)

    mesh_age = _age_sec(PROBE_FILE)
    if _due(mesh_age, cfg["mesh_stale_sec"]):
        result = run_cli(["probe", "--quiet"], timeout=180)
        record("mesh", result.get("rc") == 0, mesh_age)

    overview = build_overview(light=True)
    summary = overview.get("summary") or {}
    # GPU 快照新鲜度以 liveness 的逐节点视角为准（resources.json 的 mtime 会被别的命令改写）。
    gpu_stale = bool(summary.get("stale_nodes")) or bool(summary.get("nodes_stale"))
    gpu_age = _age_sec(RESOURCES_FILE)
    if gpu_age is not None and gpu_age > cfg["gpu_stale_sec"]:
        gpu_stale = True
    last_gpu = (state.get("last_gpu") or {}).get("at_epoch") or 0
    if gpu_stale and now_epoch - last_gpu > cfg["gpu_min_interval_sec"]:
        result = run_cli(["gpu", "refresh", "--quiet"], timeout=180)
        record("gpu", result.get("rc") == 0, gpu_age)
        overview = build_overview(light=True)
        summary = overview.get("summary") or {}

    if actions:
        invalidate_overview_cache()

    anomalies = bool(
        actions
        or summary.get("nodes_offline")
        or summary.get("offline_nodes")
        or summary.get("nodes_degraded")
        or summary.get("stale_nodes")
        or summary.get("nodes_unknown")
    )
    last_ai = (state.get("ai") or {}).get("at_epoch") or 0
    if (
        cfg.get("ai_enabled")
        and anomalies
        and now_epoch - last_ai > cfg["ai_interval_sec"]
        and assistant_status(cluster).get("ready")
    ):
        notices = list_notices(limit=20).get("items") or []
        findings = []
        try:
            diag = run_cli(["diagnose", "check"], timeout=60)
            parsed = loads_relaxed(diag.get("stdout") or "{}")
            findings = parsed.get("findings") if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, TypeError):
            findings = []
        out = analyze_dashboard(
            overview=overview,
            notices=notices,
            findings=findings if isinstance(findings, list) else [],
            cluster=cluster,
        )
        state["ai"] = {
            "at": stamp,
            "at_epoch": now_epoch,
            "ok": bool(out.get("ok")),
            "text": out.get("reply") or out.get("error") or "",
            "model": out.get("model"),
        }
        actions.append("ai")
    state["actions"] = actions
    return state


def start_auto_ops_monitor(stop_event: threading.Event) -> threading.Thread | None:
    """后台自动巡检：健康数据过期自动采集，异常时触发 AI 只读解读。"""
    if str(os.environ.get("GPU_CONTROL_AUTO_REFRESH", "1")).lower() in {"0", "false", "off", "no"}:
        return None

    def loop() -> None:
        while not stop_event.is_set():
            cluster = _read_json(cluster_file(), {})
            cfg = _auto_ops_cfg(cluster)
            interval = cfg.get("interval_sec") or 60
            if cfg.get("enabled") and not stop_event.is_set():
                acquired = _action_lock.acquire(blocking=False)
                if acquired:
                    try:
                        state = _read_json(AUTO_OPS_FILE, {})
                        if not isinstance(state, dict):
                            state = {}
                        state = _auto_ops_tick(cluster, cfg, state)
                        state_atomic_write_json(AUTO_OPS_FILE, state)
                    except Exception as exc:  # noqa: BLE001 — 后台线程不能拖垮 UI
                        print(f"auto-ops error: {exc}", file=sys.stderr, flush=True)
                    finally:
                        _action_lock.release()
                else:
                    print("auto-ops: skip (another action is running)", file=sys.stderr, flush=True)
            else:
                state = _read_json(AUTO_OPS_FILE, {})
                if isinstance(state, dict):
                    state["enabled"] = bool(cfg.get("enabled"))
                    state_atomic_write_json(AUTO_OPS_FILE, state)
            stop_event.wait(interval)

    thread = threading.Thread(target=loop, daemon=True, name="auto-ops")
    thread.start()
    return thread


def main() -> None:
    # 默认仅回环；cmd_ui 在运维明确要暴露局域网时传入 cluster.control.ui_host。
    host = os.environ.get("GPU_CONTROL_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("GPU_CONTROL_UI_PORT", "8787"))
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not loopback and str(os.environ.get("GPU_CONTROL_ALLOW_INSECURE", "0")).lower() not in {"1", "true", "yes"}:
        try:
            auth_enabled = load_auth_config().enabled
        except Exception:  # noqa: BLE001
            auth_enabled = False
        if not auth_enabled:
            print(
                f"refusing to bind {host}:{port} without auth; "
                "enable auth or set GPU_CONTROL_ALLOW_INSECURE=1 for a trusted lab network",
                file=sys.stderr,
            )
            raise SystemExit(2)
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
    start_liveness_monitor(stopping)
    start_auto_ops_monitor(stopping)
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
