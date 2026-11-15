#!/usr/bin/env python3
"""由 ``nodes.json`` + ``cluster.json`` 渲染推理 API 端点目录。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import STATE_DIR, cluster_file, expand_path, nodes_file

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from endpoints_util import build_endpoint_catalog  # noqa: E402
from state_util import atomic_write_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write state/endpoints.json")
    args = parser.parse_args()

    nodes = json.loads(nodes_file().read_text(encoding="utf-8"))
    cluster_path = cluster_file()
    cluster = json.loads(cluster_path.read_text(encoding="utf-8")) if cluster_path.is_file() else {}
    payload = build_endpoint_catalog(nodes, cluster)
    if payload.get("api_key_file"):
        payload["api_key_file"] = expand_path(str(payload["api_key_file"]))

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        out = STATE_DIR / "endpoints.json"
        atomic_write_text(out, text)
        by_node = payload.get("by_node") or {}
        summary = ", ".join(
            f"{name}:{info.get('ports_per_node')}p@{info.get('base_port')}"
            for name, info in by_node.items()
        )
        print(f"Wrote {out} ({payload.get('count')} endpoints; {summary})")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
