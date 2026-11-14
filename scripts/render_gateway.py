#!/usr/bin/env python3
"""Render gateway artifacts (nginx / curl / client notes) from the endpoint catalog.

All service-specific paths and auth come from ``cluster.api`` / ``cluster.gateway``.
The control plane does not proxy inference traffic.
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


def build_topology(cluster: dict, catalog: dict, health: dict) -> dict:
    api = cluster.get("api") or {}
    defaults = catalog.get("api_defaults") or {}
    base = int(defaults.get("base_port") or api.get("base_port", 8000))
    count = int(defaults.get("ports_per_node") or api.get("ports_per_node", 1))
    by_node = catalog.get("by_node") or {}
    key_file = api.get("api_key_file") or catalog.get("api_key_file")
    if key_file:
        key_file = str(expand_path(str(key_file)))
    gateway = cluster.get("gateway") or {}
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    proxy_dir = expand_path(str(proxy_dir_raw)) if proxy_dir_raw else None
    ui_port = int((cluster.get("control") or {}).get("ui_port") or 8787)
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
    auth = _auth_line(api)
    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "control_ui": f"http://127.0.0.1:{ui_port}",
        "inference_pool": {
            "job": catalog.get("job") or cluster.get("default_job"),
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
            "endpoint_count": catalog.get("count") or len(catalog.get("endpoints") or []),
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
                "role": "optional edge LB (may be absent on control host)",
                "installed": bool(shutil.which("nginx")),
                "generated": str(OUT_DIR / "nginx-inference-pool.conf"),
            },
        },
    }


def render_nginx(upstreams: list[dict], *, listen: int = 18080) -> str:
    servers = "\n".join(
        f"    server {ep['host']}:{ep['port']} max_fails=2 fail_timeout=5s;"
        for ep in upstreams
        if ep.get("host") and ep.get("port") is not None
    )
    return f"""# Generated by gpu-control — do not edit by hand.
# Inference pool upstream. Forward Authorization if your API needs it.
# Install: include from nginx.conf, then nginx -t && reload.

upstream inference_pool {{
    least_conn;
{servers}
    keepalive 32;
}}

server {{
    listen {listen};
    server_name _;

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
    if auth_header:
        lines.append(f'curl -sS -H "{auth_header}: Bearer $KEY" "$BASE{health}"')
        if sample != health:
            lines.append(f'curl -sS -H "{auth_header}: Bearer $KEY" "$BASE{sample}"')
    else:
        lines.append(f'curl -sS "$BASE{health}"')
        if sample != health:
            lines.append(f'curl -sS "$BASE{sample}"')
    return "\n".join(lines) + "\n"


def render_gateway_notes(topology: dict, upstreams: list[dict]) -> str:
    local = topology["local_services"]
    pool = topology["inference_pool"]
    urls = [u["url"] for u in upstreams[:8]]
    proxy = local.get("openai_compat_proxy") or {}
    extra = local.get("extra_http") or {}
    return f"""# Gateway notes (generated by gpu-control)
#
# Control plane does not proxy inference traffic.
# - Endpoint pool: ports={pool.get('port_range')} endpoints={pool.get('endpoint_count')}
# - Paths: {', '.join(pool.get('paths') or [])}
# - Auth: {pool.get('auth') or '(none)'}
#
# Healthy upstreams (truncated):
{chr(10).join('#  - ' + u for u in urls) or '#  (none)'}
#
# Optional local services from cluster.gateway:
# - OpenAI-compatible proxy :{proxy.get('port') or '-'} ({proxy.get('role')})
# - Extra HTTP :{extra.get('port') or '-'} ({extra.get('role')})
#
# Wire an OpenAI-compatible backend into your proxy config, e.g.:
#   api_base: http://<host>:<port>/v1
#   api_key:  os.environ/{pool.get('auth_env') or 'INFERENCE_API_KEY'}
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


def build_payload(*, listen: int | None = None) -> dict:
    cluster = _load(cluster_file(), {})
    catalog = _load(ENDPOINTS_FILE, {"endpoints": [], "urls": []})
    health = _load(HEALTH_FILE, {})
    upstreams = _healthy_urls(catalog, health)
    topology = build_topology(cluster, catalog, health)
    pool = topology["inference_pool"]
    listen_port = int(listen or (cluster.get("gateway") or {}).get("nginx_listen") or 18080)
    return {
        "topology": topology,
        "upstreams": upstreams,
        "snippets": {
            "python": render_python_client(upstreams),
            "nginx": render_nginx(upstreams, listen=listen_port),
            "curl": render_curl(upstreams, pool),
            "gateway_notes": render_gateway_notes(topology, upstreams),
            # backward-compatible alias for older UI selections
            "litellm_notes": render_gateway_notes(topology, upstreams),
        },
    }


def write_artifacts(payload: dict) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {
        "map": OUT_DIR / "map.json",
        "nginx": OUT_DIR / "nginx-inference-pool.conf",
        "curl": OUT_DIR / "curl-examples.sh",
        "gateway_notes": OUT_DIR / "GATEWAY.md",
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
    paths["nginx"].write_text(payload["snippets"]["nginx"], encoding="utf-8")
    paths["curl"].write_text(payload["snippets"]["curl"], encoding="utf-8")
    paths["gateway_notes"].write_text(payload["snippets"]["gateway_notes"], encoding="utf-8")
    paths["python"].write_text(payload["snippets"]["python"], encoding="utf-8")
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
        print(f"ports {pool['port_range']} · endpoints={pool['endpoint_count']}")
        print(f"health {pool['health'].get('ok')}/{pool['health'].get('total')} · key={pool.get('api_key_file')}")
        print(f"nginx installed={topo['local_services']['nginx']['installed']}")
        print(f"upstreams={len(payload['upstreams'])}")
        if written:
            for name, path in written.items():
                print(f"  wrote {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
