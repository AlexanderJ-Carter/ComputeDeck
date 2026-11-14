#!/usr/bin/env python3
"""Render Decider API endpoint catalog from nodes.json + cluster.json."""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401
from paths import STATE_DIR, cluster_file, expand_path, nodes_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write state/endpoints.json")
    args = parser.parse_args()

    nodes = json.loads(nodes_file().read_text(encoding="utf-8"))
    cluster_path = cluster_file()
    cluster = json.loads(cluster_path.read_text(encoding="utf-8")) if cluster_path.is_file() else {}
    api = cluster.get("api") or {}
    base = int(api.get("base_port", 30000))
    count = int(api.get("ports_per_node", 4))
    ports = list(range(base, base + count))
    key_file = api.get("api_key_file")
    if key_file:
        key_file = expand_path(key_file)

    endpoints = []
    for name, meta in sorted((nodes.get("nodes") or {}).items()):
        host = meta.get("ansible_host")
        if not host:
            continue
        for i, port in enumerate(ports):
            endpoints.append({
                "node": name,
                "host": host,
                "gpu": i,
                "port": port,
                "url": f"http://{host}:{port}",
                "health": f"http://{host}:{port}{api.get('health_path', '/health')}",
                "role": meta.get("role") or "serving",
                "labels": meta.get("labels") or [],
            })

    payload = {
        "job": cluster.get("default_job") or "",
        "strategy": cluster.get("strategy"),
        "api_key_file": key_file,
        "count": len(endpoints),
        "endpoints": endpoints,
        "urls": [e["url"] for e in endpoints],
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        out = STATE_DIR / "endpoints.json"
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out} ({len(endpoints)} endpoints)")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
