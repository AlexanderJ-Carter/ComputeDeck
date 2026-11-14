#!/usr/bin/env python3
"""Select free GPU nodes from resources.json."""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401
from paths import RESOURCES_FILE, STATE_DIR, nodes_file

LEASES_FILE = STATE_DIR / "leases.json"
NODE_STATE_FILE = STATE_DIR / "node_state.json"


def leased_nodes() -> set[str]:
    if not LEASES_FILE.is_file():
        return set()
    data = json.loads(LEASES_FILE.read_text(encoding="utf-8"))
    return {node for lease in data.get("leases", []) for node in lease.get("nodes", [])}


def cordoned_nodes() -> set[str]:
    if not NODE_STATE_FILE.is_file():
        return set()
    data = json.loads(NODE_STATE_FILE.read_text(encoding="utf-8"))
    return set((data.get("cordoned") or {}).keys())


def _nodes_meta() -> dict:
    path = nodes_file()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def gpus_per_node(name: str, node: dict) -> int:
    meta = _nodes_meta()
    if meta:
        cfg = meta.get("nodes", {}).get(name, {})
        if "gpus_per_node" in cfg:
            return int(cfg["gpus_per_node"])
        defaults = meta.get("defaults", {})
        if "gpus_per_node" in defaults:
            return int(defaults["gpus_per_node"])
    return int(node.get("gpu_count") or len(node.get("gpus") or []) or 4)


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

    candidates: list[str] = []
    capacity = 0
    for name, node in sorted(resources.items()):
        if not isinstance(node, dict) or "gpus" not in node:
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
