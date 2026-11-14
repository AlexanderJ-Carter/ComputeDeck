#!/usr/bin/env python3
"""Render gateway artifacts from the endpoint catalog.

gpu-control is the **control plane**: inventory, health, and config snippets.
It does **not** proxy inference traffic.

Recommended layering (borrowed from LiteLLM / edge-LB practice):

1. Backends — per-GPU HTTP services (Job endpoints)
2. Edge LB (optional) — nginx least_conn for raw HTTP
3. AI Gateway (optional) — OpenAI-compatible proxy (LiteLLM etc.):
   same ``model_name`` → load-balance / retries / cooldown
4. Control plane — this tool (render + probe only)

All service-specific paths and auth come from ``cluster.api`` / ``cluster.gateway``.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import PROJECT_DIR, STATE_DIR, cluster_file, expand_path

ENDPOINTS_FILE = STATE_DIR / "endpoints.json"
HEALTH_FILE = STATE_DIR / "endpoint_health.json"
OUT_DIR = STATE_DIR / "gateway"

DEFAULT_API_PATHS = ["/health"]
LEGACY_ARTIFACTS = (
    "nginx-decider.conf",
    "LITELLM.md",
)


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
        "layers": {
            "backends": {
                "role": "per-GPU HTTP serving endpoints",
                "endpoint_count": endpoint_count,
            },
            "edge_lb": {
                "role": "control-host raw HTTP LB (builtin edge and/or nginx)",
                "builtin": "gpu-control gateway start --kind edge",
                "artifact": str(OUT_DIR / "nginx-inference-pool.conf"),
                "listen": int(
                    ((gateway.get("ingress") or {}).get("port"))
                    or gateway.get("nginx_listen")
                    or 18080
                ),
                "ingress": gateway.get("ingress") or {},
            },
            "ai_gateway": {
                "role": "optional OpenAI-compatible proxy (LiteLLM etc.)",
                "model_name": model_name,
                "litellm_model": litellm_model,
                "openai_api_base_path": api_base_path,
                "routing_strategy": routing_strategy,
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
) -> str:
    servers = "\n".join(
        f"    server {ep['host']}:{ep['port']} max_fails=2 fail_timeout=5s;"
        for ep in upstreams
        if ep.get("host") and ep.get("port") is not None
    )
    tls = tls or {}
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
# Edge LB for raw HTTP backends. For OpenAI chat/completions, prefer LiteLLM
# (see litellm-model-list.yaml) — nginx will not do model_name routing / fallbacks.
# Install: include from nginx.conf, then nginx -t && reload.

upstream inference_pool {{
    least_conn;
{servers}
    keepalive 32;
}}

server {{
    {listen_line}
    server_name {server_name or '_'};
{ssl_block}
    location = {health_path} {{
        proxy_http_version 1.1;
        proxy_pass http://inference_pool;
        proxy_connect_timeout 2s;
        proxy_read_timeout 5s;
    }}

    location / {{
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Authorization $http_authorization;
        proxy_connect_timeout 2s;
        proxy_read_timeout 300s;
        proxy_pass http://inference_pool;
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
    """LiteLLM model_list fragment: same model_name → router load-balances."""
    model_name = pool.get("model_name") or "gpu-pool"
    litellm_model = pool.get("litellm_model") or f"openai/{model_name}"
    base_path = pool.get("openai_api_base_path") or "/v1"
    auth_env = pool.get("auth_env") or "INFERENCE_API_KEY"
    strategy = pool.get("routing_strategy") or "least-busy"
    lines = [
        "# Generated by gpu-control — merge into your LiteLLM config.yaml",
        "# Pattern: identical model_name → LiteLLM Router load-balances deployments.",
        "# Retries stay in-group; configure fallbacks in your full LiteLLM config.",
        "# Start proxy yourself, e.g.: litellm --config /path/to/config.yaml",
        "# gpu-control does not run LiteLLM and does not own virtual keys / spend / Redis.",
        "model_list:",
    ]
    if not upstreams:
        lines.append(f"  # (no upstreams) add backends, then re-run: gpu-control gateway --write")
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
    return {
        "python": render_python_client(upstreams),
        "nginx": render_nginx(
            upstreams,
            listen=listen_port,
            health_path=str(pool.get("health_path") or "/health"),
            server_name=server_name,
            tls=tls,
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
    """Replace upstreams and regenerate snippets (used by Web UI)."""
    payload = {**payload, "upstreams": upstreams}
    payload["snippets"] = render_snippets(payload["topology"], upstreams, listen=listen)
    return payload


def write_artifacts(payload: dict) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_ARTIFACTS:
        legacy = OUT_DIR / name
        if legacy.is_file():
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
