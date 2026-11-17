#!/usr/bin/env python3
"""从 ``resources.json`` 选择空闲 GPU 节点。"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401
import liveness
from paths import RESOURCES_FILE, STATE_DIR, cluster_file, nodes_file
from state_util import read_json

LEASES_FILE = STATE_DIR / "leases.json"
NODE_STATE_FILE = STATE_DIR / "node_state.json"


def leased_nodes() -> set[str]:
    data = read_json(LEASES_FILE, {"leases": []})
    return {
        node
        for lease in data.get("leases", [])
        for node in lease.get("nodes", [])
    }


def cordoned_nodes() -> set[str]:
    data = read_json(NODE_STATE_FILE, {"cordoned": {}})
    return set((data.get("cordoned") or {}).keys())


def _nodes_meta() -> dict:
    path = nodes_file()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def gpus_per_node(name: str, node: dict) -> int:
    """解析节点应有 GPU 数：配置优先，否则用实采；不发明默认值。

    :raises SystemExit: 配置与实采皆无。
    """
    meta = _nodes_meta()
    if meta:
        cfg = meta.get("nodes", {}).get(name, {})
        if "gpus_per_node" in cfg and cfg.get("gpus_per_node") not in (None, ""):
            return int(cfg["gpus_per_node"])
        defaults = meta.get("defaults", {})
        if "gpus_per_node" in defaults and defaults.get("gpus_per_node") not in (None, ""):
            return int(defaults["gpus_per_node"])
    count = node.get("gpu_count") or len(node.get("gpus") or [])
    if count:
        return int(count)
    raise SystemExit(
        f"节点 {name} 未配置 gpus_per_node，且 resources 中无实采 GPU 数据"
    )


def node_labels(name: str) -> set[str]:
    meta = _nodes_meta()
    if not meta:
        return set()
    cfg = meta.get("nodes", {}).get(name, {})
    return set(cfg.get("labels") or [])


def is_candidate(name: str, node: dict, *, blocked: set[str]) -> bool:
    if name in blocked:
        return False
    gpus = node["gpus"]
    expected = gpus_per_node(name, node)
    return len(gpus) == expected and all(gpu["status"] == "free" for gpu in gpus)


def health_view(resources: dict) -> dict:
    """合并存活 / 其它健康信号，供调度排除不可达或过期节点。"""
    configured = sorted(set(resources) | set((_nodes_meta().get("nodes") or {})))
    cluster = json.loads(cluster_file().read_text(encoding="utf-8")) if cluster_file().is_file() else {}
    return liveness.cluster_view(
        node_names=configured,
        resources=resources,
        ssh_quality=read_json(STATE_DIR / "ssh_quality.json", {}),
        host_metrics=read_json(STATE_DIR / "host_metrics.json", {}),
        probe=read_json(STATE_DIR / "probe.json", {}),
        endpoint_health=read_json(STATE_DIR / "endpoint_health.json", {}),
        persisted=liveness.read_snapshot(STATE_DIR / "liveness.json"),
        cluster=cluster,
    )


def unavailable_reason(name: str, view: dict) -> str | None:
    """返回不可调度原因；可调度返回 ``None``。"""
    node = (view.get("nodes") or {}).get(name) or {}
    state = str(node.get("state") or "unknown")
    snapshot = node.get("snapshot") or {}
    if state == "offline":
        return "offline"
    if state == "degraded":
        return "degraded"
    if state == "unknown":
        return "unknown"
    if snapshot.get("stale"):
        return f"snapshot-stale({snapshot.get('age_text') or '-'})"
    return None


def rank_candidates(names: list[str], *, prefer: set[str], avoid: set[str]) -> list[str]:
    def score(name: str) -> tuple[int, int, str]:
        labels = node_labels(name)
        prefer_hits = len(labels & prefer)
        avoid_hits = len(labels & avoid)
        return (-prefer_hits, avoid_hits, name)

    return sorted(names, key=score)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpus", type=int, required=True)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--allow-leased",
        action="store_true",
        help="Ignore lease records when selecting candidates",
    )
    parser.add_argument(
        "--allow-cordoned",
        action="store_true",
        help="Allow selecting cordoned nodes",
    )
    parser.add_argument(
        "--prefer-labels",
        default="",
        help="Comma-separated labels to prefer (e.g. serving)",
    )
    parser.add_argument(
        "--avoid-labels",
        default="",
        help="Comma-separated labels to deprioritize (e.g. shared)",
    )
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="同时考虑离线 / 未知 / 快照过期节点（默认排除）",
    )
    args = parser.parse_args()

    if not RESOURCES_FILE.is_file():
        print("resources.json missing; run: gpu-control refresh", file=sys.stderr)
        return 1

    resources = json.loads(RESOURCES_FILE.read_text(encoding="utf-8"))
    blocked = set()
    if not args.allow_leased:
        blocked |= leased_nodes()
    cordoned = set() if args.allow_cordoned else cordoned_nodes()
    blocked |= cordoned
    prefer = {x.strip() for x in args.prefer_labels.split(",") if x.strip()}
    avoid = {x.strip() for x in args.avoid_labels.split(",") if x.strip()}
    view = health_view(resources)

    candidates: list[str] = []
    capacity = 0
    for name, node in sorted(resources.items()):
        if not isinstance(node, dict) or "gpus" not in node:
            continue
        health_reason = None if args.include_unverified else unavailable_reason(name, view)
        if health_reason:
            if not args.json:
                print(f"{name}: {health_reason}")
            continue
        if is_candidate(name, node, blocked=blocked):
            candidates.append(name)
            capacity += gpus_per_node(name, node)
            if not args.json:
                print(f"{name}: candidate labels={','.join(sorted(node_labels(name))) or '-'}")
        elif not args.json:
            if name in cordoned:
                reason = "cordoned"
            elif name in leased_nodes() and not args.allow_leased:
                reason = "leased"
            else:
                reason = "occupied"
            print(f"{name}: {reason}")

    candidates = rank_candidates(candidates, prefer=prefer, avoid=avoid)

    unit = 4
    if candidates:
        unit = gpus_per_node(candidates[0], resources[candidates[0]])
    else:
        for node in resources.values():
            if isinstance(node, dict) and "gpus" in node:
                unit = gpus_per_node(str(node.get("node", "")), node)
                break

    if args.gpus % unit != 0:
        message = f"--gpus {args.gpus} must be a multiple of {unit}"
        if args.json:
            print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        else:
            print(message)
        return 1

    required_nodes = args.gpus // unit
    if len(candidates) < required_nodes:
        message = f"No sufficient candidates for {args.gpus} GPUs"
        if args.json:
            print(json.dumps({
                "ok": False,
                "gpus": args.gpus,
                "required_nodes": required_nodes,
                "candidates": candidates,
                "selected": [],
                "error": message,
            }, ensure_ascii=False))
        else:
            print(message)
        return 1

    selected = candidates[:required_nodes]
    if args.json:
        print(json.dumps({
            "ok": True,
            "gpus": args.gpus,
            "required_nodes": required_nodes,
            "candidates": candidates,
            "selected": selected,
            "capacity_gpus": capacity,
            "prefer_labels": sorted(prefer),
            "avoid_labels": sorted(avoid),
        }, ensure_ascii=False))
    else:
        print(f"Preliminary selection: {', '.join(selected)}")
        print("Refresh the resource snapshot before deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
