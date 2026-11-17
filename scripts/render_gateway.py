#!/usr/bin/env python3
"""由端点目录渲染网关产物（nginx / LiteLLM 片段等）。

gpu-control 是**控制面**：库存、健康、配置片段；**不**代理推理流量。

推荐分层（节点 LB 只保留一层）：

1. Backends — 每 GPU HTTP 服务（Job 端点）
2. 节点 LB — 内置 Edge（唯一节点选择层）；nginx 仅作为同层替代或 Web/TLS 反代
3. OpenAI 层（可选）— LiteLLM；与 Edge 共存时必须指向 Edge，不再直连节点
4. Control — 本工具（仅渲染与探测）

同一批节点只能有一层做负载均衡；重复时 ``topology.layering`` 会给出告警。

业务路径与鉴权一律来自 ``cluster.api`` / ``cluster.gateway``。
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import _bootstrap  # noqa: F401
from paths import PROJECT_DIR, STATE_DIR, cluster_file, expand_path

ENDPOINTS_FILE = STATE_DIR / "endpoints.json"
HEALTH_FILE = STATE_DIR / "endpoint_health.json"
OUT_DIR = STATE_DIR / "gateway"

DEFAULT_API_PATHS = ["/health"]
LEGACY_ARTIFACTS = (
    "LITELLM.md",
)
LEGACY_GLOBS = ("nginx-*.conf",)


def _load(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _healthy_urls(catalog: dict, health: dict) -> list[dict]:
    by_key = {(r.get("node"), r.get("gpu")): r for r in health.get("results") or []}
    rows = []
    for ep in catalog.get("endpoints") or []:
        h = by_key.get((ep.get("node"), ep.get("gpu"))) or {}
        rows.append({
            **ep,
            "ok": bool(h.get("ok")) if h else False,
            "latency_ms": h.get("latency_ms"),
        })
    healthy = [r for r in rows if r.get("ok")]
    return healthy or rows


def _api_paths(api: dict) -> list[str]:
    raw = api.get("paths") or api.get("probe_paths") or DEFAULT_API_PATHS
    paths = [str(p) for p in raw if str(p).startswith("/")]
    return paths or list(DEFAULT_API_PATHS)


def _auth_line(api: dict) -> str | None:
    header = (api.get("auth_header") or "").strip()
    if not header:
        return None
    if header.lower() == "authorization":
        return "Authorization: Bearer <api_key_file>"
    return f"{header}: <token>"


def _gateway_cfg(cluster: dict) -> dict:
    return cluster.get("gateway") or {}


def _upstream_mode(value, default: str = "nodes") -> str:
    """归一化上游模式：nodes（直连节点）/ edge（经内置 Edge）。"""
    text = str(value or "").strip().lower()
    if text in {"edge", "ingress", "lb", "edge-lb"}:
        return "edge"
    if text in {"nodes", "node", "direct"}:
        return "nodes"
    return default


def _edge_url(gateway: dict, *, tls_ready: bool = False) -> str:
    override = str(gateway.get("litellm_edge_url") or "").strip()
    if override:
        return override.rstrip("/")
    ingress = gateway.get("ingress") or {}
    port = int(ingress.get("port") or gateway.get("nginx_listen") or 18080)
    bind = str(ingress.get("bind") or "0.0.0.0").strip()
    host = "127.0.0.1" if bind in {"", "0.0.0.0", "::", "::0", "localhost", "127.0.0.1"} else bind
    scheme = "https" if tls_ready else "http"
    return f"{scheme}://{host}:{port}"


def _openai_api_base(url: str, base_path: str) -> str:
    root = (url or "").rstrip("/")
    path = base_path if base_path.startswith("/") else f"/{base_path}"
    if path == "/" or root.endswith(path):
        return root
    return f"{root}{path}"


def build_topology(cluster: dict, catalog: dict, health: dict) -> dict:
    api = cluster.get("api") or {}
    defaults = catalog.get("api_defaults") or {}
    base = int(defaults.get("base_port") or api.get("base_port", 8000))
    count = int(defaults.get("ports_per_node") or api.get("ports_per_node", 1))
    by_node = catalog.get("by_node") or {}
    key_file = api.get("api_key_file") or catalog.get("api_key_file")
    if key_file:
        key_file = str(expand_path(str(key_file)))
    gateway = _gateway_cfg(cluster)
    ingress = gateway.get("ingress") if isinstance(gateway.get("ingress"), dict) else {}
    ingress_kind = str(ingress.get("kind") or "edge").lower()
    edge_enabled = bool(ingress.get("enabled", True)) and ingress_kind in {"edge", "both", "all"}
    edge_port = int(ingress.get("port") or gateway.get("nginx_listen") or 18080)
    tls_cfg = ingress.get("tls") if isinstance(ingress.get("tls"), dict) else {}
    edge_base = _edge_url(
        gateway,
        tls_ready=bool(tls_cfg.get("enabled") and tls_cfg.get("cert_file") and tls_cfg.get("key_file")),
    )
    litellm_upstream = _upstream_mode(gateway.get("litellm_upstream"), "nodes")
    nginx_upstream = _upstream_mode(gateway.get("nginx_upstream"), "nodes")
    litellm_configured = bool(
        gateway.get("openai_proxy_dir") or gateway.get("litellm_dir") or gateway.get("litellm_bin")
    )
    duplicate_node_lb = bool(edge_enabled and litellm_configured and litellm_upstream == "nodes")
    layering_warnings = []
    if duplicate_node_lb:
        layering_warnings.append({
            "code": "duplicate_node_lb",
            "severity": "warn",
            "message": "Edge 与 LiteLLM 都在对节点端口做负载均衡；建议 litellm_upstream=edge，由 Edge 统一选节点。",
            "fix": "cluster.gateway.litellm_upstream=\"edge\"，然后 gateway render + restart --kind litellm",
        })
    if litellm_upstream == "edge" and not edge_enabled:
        layering_warnings.append({
            "code": "litellm_edge_unavailable",
            "severity": "error",
            "message": "LiteLLM 配置为经 Edge，但 Edge 未启用。",
            "fix": "启用 gateway.ingress 或把 litellm_upstream 改回 nodes",
        })
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    proxy_dir = expand_path(str(proxy_dir_raw)) if proxy_dir_raw else None
    ui_port = int((cluster.get("control") or {}).get("ui_port") or 8787)
    model_name = str(gateway.get("model_name") or catalog.get("job") or cluster.get("default_job") or "gpu-pool")
    litellm_model = str(
        gateway.get("litellm_model")
        or gateway.get("openai_model")
        or f"openai/{model_name}"
    )
    api_base_path = str(gateway.get("openai_api_base_path") or "/v1")
    routing_strategy = str(gateway.get("routing_strategy") or "least-busy")
    node_ranges = {
        name: {
            "base_port": info.get("base_port"),
            "ports_per_node": info.get("ports_per_node"),
            "ports": info.get("ports"),
            "range": (
                f"{min(info.get('ports') or [base])}-{max(info.get('ports') or [base])}"
                if info.get("ports")
                else None
            ),
        }
        for name, info in by_node.items()
    }
    endpoint_count = catalog.get("count") or len(catalog.get("endpoints") or [])
    auth = _auth_line(api)
    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "control_ui": f"http://127.0.0.1:{ui_port}",
        "layering": {
            "edge_enabled": edge_enabled,
            "edge_url": edge_base,
            "edge_port": edge_port,
            "node_lb_owner": "edge" if edge_enabled and litellm_upstream == "edge" else (
                "litellm" if litellm_upstream == "nodes" else "edge"
            ),
            "litellm_upstream": litellm_upstream,
            "nginx_upstream": nginx_upstream,
            "duplicate_node_lb": duplicate_node_lb,
            "single_node_lb": not duplicate_node_lb,
            "warnings": layering_warnings,
            "summary": (
                "Edge 是唯一节点 LB；LiteLLM 经 Edge 做 OpenAI 治理层"
                if edge_enabled and litellm_upstream == "edge"
                else "Edge 与 LiteLLM 都在对节点做 LB（重复）"
                if duplicate_node_lb
                else "Edge 单层节点 LB"
            ),
        },
        "layers": {
            "backends": {
                "role": "per-GPU HTTP serving endpoints",
                "endpoint_count": endpoint_count,
            },
            "edge_lb": {
                "role": "control-host raw HTTP LB (Edge is the single node-LB layer)",
                "builtin": "gpu-control gateway start --kind edge",
                "artifact": str(OUT_DIR / "nginx-inference-pool.conf"),
                "listen": int(
                    ((gateway.get("ingress") or {}).get("port"))
                    or gateway.get("nginx_listen")
                    or 18080
                ),
                "ingress": gateway.get("ingress") or {},
                "upstream": "nodes",
                "upstream_count": endpoint_count,
                "edge_url": edge_base,
            },
            "ai_gateway": {
                "role": "optional OpenAI-compatible proxy (LiteLLM etc.)",
                "model_name": model_name,
                "litellm_model": litellm_model,
                "openai_api_base_path": api_base_path,
                "routing_strategy": routing_strategy,
                "upstream": litellm_upstream,
                "api_base": _openai_api_base(edge_base, api_base_path) if litellm_upstream == "edge" else None,
                "deployment_count": 1 if litellm_upstream == "edge" else endpoint_count,
                "artifact": str(OUT_DIR / "litellm-model-list.yaml"),
                "port": int(
                    gateway.get("openai_proxy_port")
                    or gateway.get("litellm_port")
                    or 0
                )
                or None,
                "dir": str(proxy_dir) if proxy_dir else None,
            },
            "control_plane": {
                "role": "UI/CLI/MCP orchestration; optional edge process on same host",
                "ui_port": ui_port,
            },
        },
        "inference_pool": {
            "job": catalog.get("job") or cluster.get("default_job"),
            "model_name": model_name,
            "litellm_model": litellm_model,
            "openai_api_base_path": api_base_path,
            "routing_strategy": routing_strategy,
            "base_port": base,
            "ports_per_node": count,
            "port_range": list(range(base, base + count)),
            "by_node": node_ranges,
            "paths": _api_paths(api),
            "health_path": api.get("health_path") or "/health",
            "sample_path": api.get("sample_path") or (api.get("health_path") or "/health"),
            "auth": auth,
            "auth_header": api.get("auth_header") or None,
            "auth_env": api.get("auth_env") or "INFERENCE_API_KEY",
            "api_key_file": key_file,
            "endpoint_count": endpoint_count,
            "litellm_upstream": litellm_upstream,
            "nginx_upstream": nginx_upstream,
            "edge_url": edge_base,
            "litellm_api_base": _openai_api_base(edge_base, api_base_path),
            "health": {
                "ok": health.get("ok_count"),
                "fail": health.get("fail_count"),
                "total": health.get("total"),
                "at": health.get("generated_at"),
                "mode": health.get("mode"),
            },
        },
        "local_services": {
            "gpu_control_ui": {"port": ui_port, "role": "control-plane Web"},
            "openai_compat_proxy": {
                "port": int(
                    gateway.get("openai_proxy_port")
                    or gateway.get("litellm_port")
                    or 0
                )
                or None,
                "role": gateway.get("openai_proxy_role") or "optional OpenAI-compatible proxy",
                "dir": str(proxy_dir) if proxy_dir else None,
            },
            "extra_http": {
                "port": int(gateway.get("extra_http_port") or gateway.get("vllm_port") or 0) or None,
                "role": gateway.get("extra_http_role") or "optional extra HTTP service",
            },
            "nginx": {
                "role": "alternative to builtin Edge LB",
                "installed": bool(shutil.which("nginx")),
                "status_label": (
                    "已装"
                    if shutil.which("nginx")
                    else "不必装（主入口用内置 Edge）"
                ),
                "generated": str(OUT_DIR / "nginx-inference-pool.conf"),
            },
        },
    }


def render_nginx(
    upstreams: list[dict],
    *,
    listen: int = 18080,
    health_path: str = "/health",
    server_name: str = "_",
    tls: dict | None = None,
    edge_url: str | None = None,
) -> str:
    """渲染 nginx 产物。

    - ``edge_url`` 有值：nginx 只做 Web/TLS 前端，单 upstream 指向 Edge；
    - 否则：nginx 作为 Edge 的同层替代，直接对节点端口 ``least_conn``。
    """
    tls = tls or {}
    proxy_buffering = "        proxy_buffering off;\n        proxy_next_upstream error timeout http_502 http_503 http_504;"
    if edge_url:
        parts = urlsplit(edge_url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        servers = f"    server {host}:{port};"
        upstream_name = "gpu_control_edge"
        least_conn = ""
        comment = (
            "# nginx is only TLS/domain front; Edge owns node LB.\n"
            "# Recommended: keep this listen port different from Edge (e.g. 443)."
        )
        health_path = "/_gpu_control/health"
    else:
        server_rows = []
        for ep in upstreams:
            host = ep.get("host")
            port = ep.get("port")
            if not host or port is None:
                parsed = urlsplit(str(ep.get("url") or ""))
                host = host or parsed.hostname
                port = port if port is not None else parsed.port
            if host and port is not None:
                server_rows.append(
                    f"    server {host}:{port} max_fails=2 fail_timeout=5s;"
                )
        servers = "\n".join(server_rows)
        upstream_name = "inference_pool"
        least_conn = "    least_conn;\n"
        comment = (
            "# Alternative to built-in Edge: nginx itself is the node-LB layer.\n"
            "# Do not also run Edge against the same node ports."
        )
    listen_line = f"listen {listen};"
    ssl_block = ""
    if tls.get("enabled") and tls.get("cert_file") and tls.get("key_file"):
        listen_line = f"listen {listen} ssl;"
        ssl_block = f"""
    ssl_certificate     {tls['cert_file']};
    ssl_certificate_key {tls['key_file']};
"""
        if tls.get("ca_file"):
            ssl_block += f"    ssl_client_certificate {tls['ca_file']};\n"
            if tls.get("require_client_cert"):
                ssl_block += "    ssl_verify_client on;\n"
            else:
                ssl_block += "    ssl_verify_client optional;\n"
    return f"""# Generated by gpu-control — do not edit by hand.
{comment}
# Install: include from nginx.conf, then nginx -t && reload.

upstream {upstream_name} {{
{least_conn}{servers}
    keepalive 32;
}}

server {{
    {listen_line}
    server_name {server_name or '_'};
{ssl_block}
    location = {health_path} {{
        proxy_http_version 1.1;
        proxy_pass http://{upstream_name};
        proxy_connect_timeout 2s;
        proxy_read_timeout 5s;
    }}

    location / {{
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Authorization $http_authorization;
{proxy_buffering}
        proxy_connect_timeout 2s;
        proxy_read_timeout 300s;
        proxy_pass http://{upstream_name};
    }}
}}
"""


def render_curl(upstreams: list[dict], pool: dict) -> str:
    first = upstreams[0]["url"] if upstreams else f"http://127.0.0.1:{pool.get('base_port', 8000)}"
    health = pool.get("health_path") or "/health"
    sample = pool.get("sample_path") or health
    key_file = pool.get("api_key_file")
    env_name = pool.get("auth_env") or "INFERENCE_API_KEY"
    key_expr = f"$(cat {key_file})" if key_file else f"${env_name}"
    auth_header = pool.get("auth_header")
    lines = [
        "# Endpoint probe / call examples (do not echo secrets)",
        f"KEY={key_expr}",
        f"BASE={first}",
    ]
    if auth_header and auth_header.lower() == "authorization":
        lines.append(f'curl -sS -H "Authorization: Bearer $KEY" "$BASE{health}"')
        if sample != health:
            lines.append(f'curl -sS -H "Authorization: Bearer $KEY" "$BASE{sample}"')
    elif auth_header:
        lines.append(f'curl -sS -H "{auth_header}: $KEY" "$BASE{health}"')
        if sample != health:
            lines.append(f'curl -sS -H "{auth_header}: $KEY" "$BASE{sample}"')
    else:
        lines.append(f'curl -sS "$BASE{health}"')
        if sample != health:
            lines.append(f'curl -sS "$BASE{sample}"')
    return "\n".join(lines) + "\n"


def render_litellm_yaml(upstreams: list[dict], pool: dict) -> str:
    """渲染 LiteLLM ``model_list``。

    ``litellm_upstream=edge`` 时只生成一个指向 Edge 的 deployment，节点 LB/重试/冷却
    全部由 Edge 负责；``nodes`` 模式保留逐节点 deployment（旧的直连模式）。
    """
    model_name = pool.get("model_name") or "gpu-pool"
    litellm_model = pool.get("litellm_model") or f"openai/{model_name}"
    base_path = pool.get("openai_api_base_path") or "/v1"
    auth_env = pool.get("auth_env") or "INFERENCE_API_KEY"
    strategy = pool.get("routing_strategy") or "least-busy"
    mode = str(pool.get("litellm_upstream") or "nodes").lower()
    if mode == "edge":
        api_base = pool.get("litellm_api_base") or _openai_api_base(
            pool.get("edge_url") or "http://127.0.0.1:18080", base_path
        )
        auth_header = str(pool.get("auth_header") or "Authorization")
        lines = [
            "# Generated by gpu-control — layering: litellm_upstream=edge",
            "# Edge is the ONLY node-LB layer. Do not add per-node api_base entries here.",
            "# LiteLLM provides the OpenAI model name / virtual keys / budgets layer.",
            "model_list:",
            f"  - model_name: {model_name}",
            "    litellm_params:",
            f"      model: {litellm_model}",
            f"      api_base: {api_base}",
        ]
        if auth_header.lower() == "authorization":
            lines.append(f"      api_key: os.environ/{auth_env}")
        else:
            lines.extend([
                f"      api_key: os.environ/{auth_env}",
                "      extra_headers:",
                f"        {auth_header}: os.environ/{auth_env}",
            ])
        lines.extend([
            "    model_info:",
            f"      id: {pool.get('litellm_deployment_id') or 'edge-lb'}",
            "",
            "router_settings:",
            "  routing_strategy: simple-shuffle  # single deployment → no node LB here",
            "  num_retries: 0                    # Edge owns retries",
            "  cooldown_time: 0",
            "  enable_pre_call_checks: false",
            "",
        ])
        return "\n".join(lines)
    lines = [
        "# Generated by gpu-control — layering: litellm_upstream=nodes",
        "# Warning: if Edge is also running, you have TWO node-LB layers.",
        "# Prefer litellm_upstream=edge and let Edge pick nodes.",
        "model_list:",
    ]
    if not upstreams:
        lines.append("  # (no upstreams) add backends, then re-run: gpu-control gateway render --write")
    for ep in upstreams:
        url = ep.get("url") or ""
        api_base = _openai_api_base(url, base_path)
        dep_id = f"{ep.get('node', 'node')}-gpu{ep.get('gpu', 0)}"
        lines.extend([
            f"  - model_name: {model_name}",
            "    litellm_params:",
            f"      model: {litellm_model}",
            f"      api_base: {api_base}",
            f"      api_key: os.environ/{auth_env}",
            "    model_info:",
            f"      id: {dep_id}",
        ])
    lines.extend([
        "",
        "router_settings:",
        f"  routing_strategy: {strategy}  # simple-shuffle | least-busy | latency-based-routing",
        "  num_retries: 2",
        "  allowed_fails: 2",
        "  cooldown_time: 30",
        "",
    ])
    return "\n".join(lines)


def render_gateway_notes(topology: dict, upstreams: list[dict]) -> str:
    local = topology["local_services"]
    pool = topology["inference_pool"]
    layers = topology.get("layers") or {}
    urls = [u["url"] for u in upstreams[:8]]
    proxy = local.get("openai_compat_proxy") or {}
    extra = local.get("extra_http") or {}
    ai = layers.get("ai_gateway") or {}
    edge = layers.get("edge_lb") or {}
    return f"""# Gateway notes (generated by gpu-control)

## Layers (do not collapse these roles)

1. **Backends** — Job HTTP endpoints (`endpoint_count={pool.get('endpoint_count')}`)
2. **Edge LB (optional)** — nginx raw HTTP → `{edge.get('artifact')}` listen `:{edge.get('listen')}`
3. **AI Gateway (optional)** — OpenAI-compat proxy (LiteLLM…)
   - `model_name`: `{ai.get('model_name')}`
   - `litellm_model`: `{ai.get('litellm_model')}`
   - `routing_strategy`: `{ai.get('routing_strategy')}`
   - fragment: `{ai.get('artifact')}`
   - proxy port: `:{proxy.get('port') or '-'}` dir: `{proxy.get('dir') or '-'}`
4. **Control plane** — gpu-control UI/CLI only; **never** proxies inference

## When to use what

| Need | Use |
|------|-----|
| OpenAI `/v1/chat/completions` + LB/retry/cooldown | LiteLLM (or similar) + `litellm-model-list.yaml` |
| Raw custom HTTP paths | nginx edge LB or client-side `client_endpoints.py` |
| Virtual keys / budget / spend / Redis shared state | AI Gateway product — **not** gpu-control |
| Inventory / health / render | gpu-control |

## Pool

- Paths: {', '.join(pool.get('paths') or [])}
- Auth: {pool.get('auth') or '(none)'}
- Auth env: `{pool.get('auth_env')}`
- Extra HTTP :{extra.get('port') or '-'} ({extra.get('role')})

## Healthy upstreams (truncated)

{chr(10).join('- ' + u for u in urls) or '- (none)'}
"""


def render_python_client(upstreams: list[dict]) -> str:
    urls = [u["url"] for u in upstreams if u.get("url")]
    return (
        "# Client-side endpoint list (control plane does not proxy traffic)\n"
        f"ENDPOINTS = {json.dumps(urls, ensure_ascii=False, indent=2)}\n"
        "\n"
        "def pick(i):\n"
        "    return ENDPOINTS[i % len(ENDPOINTS)] if ENDPOINTS else None\n"
    )


def render_snippets(
    topology: dict,
    upstreams: list[dict],
    *,
    listen: int | None = None,
) -> dict[str, str]:
    pool = topology["inference_pool"]
    listen_port = int(
        listen
        or ((topology.get("layers") or {}).get("edge_lb") or {}).get("listen")
        or 18080
    )
    notes = render_gateway_notes(topology, upstreams)
    litellm = render_litellm_yaml(upstreams, pool)
    cluster_ingress = {}
    cluster_doc = {}
    try:
        from paths import cluster_file

        cluster_doc = _load(cluster_file(), {})
        cluster_ingress = ((cluster_doc.get("gateway") or {}).get("ingress") or {})
    except Exception:  # noqa: BLE001
        cluster_ingress = {}
    domains = []
    if cluster_ingress.get("domain"):
        domains.append(str(cluster_ingress["domain"]))
    domains.extend(str(x) for x in (cluster_ingress.get("domains") or []) if x)
    server_name = " ".join(dict.fromkeys(domains)) if domains else "_"
    tls = cluster_ingress.get("tls") if isinstance(cluster_ingress.get("tls"), dict) else {}
    cluster_gateway = (cluster_doc.get("gateway") or {}) if isinstance(cluster_doc, dict) else {}
    nginx_listen = int(cluster_gateway.get("nginx_listen") or 18443)
    return {
        "python": render_python_client(upstreams),
        "nginx": render_nginx(
            upstreams,
            listen=nginx_listen,
            health_path=str(pool.get("health_path") or "/health"),
            server_name=server_name,
            tls=tls,
            edge_url=(
                pool.get("edge_url")
                if str(pool.get("nginx_upstream") or "nodes") == "edge"
                else None
            ),
        ),
        "curl": render_curl(upstreams, pool),
        "litellm": litellm,
        "gateway_notes": notes,
        # backward-compatible aliases
        "litellm_notes": notes,
        "litellm_yaml": litellm,
    }


def build_payload(*, listen: int | None = None) -> dict:
    cluster = _load(cluster_file(), {})
    catalog = _load(ENDPOINTS_FILE, {"endpoints": [], "urls": []})
    health = _load(HEALTH_FILE, {})
    upstreams = _healthy_urls(catalog, health)
    topology = build_topology(cluster, catalog, health)
    return {
        "topology": topology,
        "upstreams": upstreams,
        "snippets": render_snippets(topology, upstreams, listen=listen),
    }


def apply_upstreams(payload: dict, upstreams: list[dict], *, listen: int | None = None) -> dict:
    """替换上游并重新生成 snippets（供 Web UI 使用）。"""
    payload = {**payload, "upstreams": upstreams}
    payload["snippets"] = render_snippets(payload["topology"], upstreams, listen=listen)
    return payload


def write_artifacts(payload: dict) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_ARTIFACTS:
        legacy = OUT_DIR / name
        if legacy.is_file():
            legacy.unlink()
    # Drop stale per-scenario nginx snippets, keep the current generic artifact.
    for pattern in LEGACY_GLOBS:
        for legacy in OUT_DIR.glob(pattern):
            if legacy.name != "nginx-inference-pool.conf":
                legacy.unlink()
    paths = {
        "map": OUT_DIR / "map.json",
        "nginx": OUT_DIR / "nginx-inference-pool.conf",
        "curl": OUT_DIR / "curl-examples.sh",
        "gateway_notes": OUT_DIR / "GATEWAY.md",
        "litellm": OUT_DIR / "litellm-model-list.yaml",
        "python": OUT_DIR / "client_endpoints.py",
    }
    paths["map"].write_text(
        json.dumps(
            {"topology": payload["topology"], "upstreams": payload["upstreams"]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    snippets = payload["snippets"]
    paths["nginx"].write_text(snippets["nginx"], encoding="utf-8")
    paths["curl"].write_text(snippets["curl"], encoding="utf-8")
    paths["gateway_notes"].write_text(snippets["gateway_notes"], encoding="utf-8")
    paths["litellm"].write_text(snippets["litellm"], encoding="utf-8")
    paths["python"].write_text(snippets["python"], encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help=f"write under {OUT_DIR}")
    parser.add_argument("--listen", type=int, default=None, help="nginx listen port")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not ENDPOINTS_FILE.is_file():
        from subprocess import run
        from paths import VENV_PYTHON, SCRIPTS_DIR

        run(
            [str(VENV_PYTHON), str(SCRIPTS_DIR / "render_endpoints.py"), "--write"],
            cwd=PROJECT_DIR,
            check=False,
        )

    payload = build_payload(listen=args.listen)
    written = write_artifacts(payload) if args.write else {}
    out = {**payload, "written": written}
    if args.json or args.write:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        topo = payload["topology"]
        pool = topo["inference_pool"]
        ai = (topo.get("layers") or {}).get("ai_gateway") or {}
        print(f"ports {pool['port_range']} · endpoints={pool['endpoint_count']}")
        print(f"model_name={ai.get('model_name')} strategy={ai.get('routing_strategy')}")
        print(f"health {pool['health'].get('ok')}/{pool['health'].get('total')} · key={pool.get('api_key_file')}")
        print(f"nginx installed={topo['local_services']['nginx']['installed']}")
        print(f"upstreams={len(payload['upstreams'])}")
        if written:
            for name, path in written.items():
                print(f"  wrote {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
