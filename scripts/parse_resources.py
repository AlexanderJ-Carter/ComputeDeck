#!/usr/bin/env python3
"""解析 GPU 快照，汇总为 ``resources.json``。"""

from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from liveness import DEFAULT_TTLS, format_age, parse_ts
from paths import RESOURCES_FILE, SNAPSHOT_DIR, STATE_DIR, nodes_file
from state_util import atomic_write_json


def _rows(raw_rows: list[str]) -> list[list[str]]:
    return list(csv.reader(row for row in raw_rows if row and row.strip()))


def parse_snapshot(path: Path) -> dict:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    processes_by_gpu: dict[str, list[dict]] = defaultdict(list)

    for uuid, pid, name, memory in _rows(snapshot.get("process_rows", [])):
        processes_by_gpu[uuid.strip()].append({
            "pid": int(pid),
            "name": name.strip(),
            "memory_mib": int(memory),
        })

    gpus = []
    for index, uuid, total, used, utilization in _rows(snapshot.get("gpu_rows", [])):
        uuid = uuid.strip()
        processes = processes_by_gpu[uuid]
        memory_used = int(used)
        util = int(utilization)
        if processes or memory_used >= 1024 or util >= 10:
            status = "occupied"
        else:
            status = "free"

        gpus.append({
            "index": int(index),
            "uuid": uuid,
            "memory_total_mib": int(total),
            "memory_used_mib": memory_used,
            "utilization_percent": util,
            "processes": processes,
            "status": status,
        })

    free = sum(gpu["status"] == "free" for gpu in gpus)
    occupied = sum(gpu["status"] == "occupied" for gpu in gpus)
    return {
        "node": snapshot["node"],
        "collected_at": snapshot.get("collected_at"),
        "gpu_count": len(gpus),
        "free_count": free,
        "occupied_count": occupied,
        "gpus": gpus,
    }


def _configured_nodes() -> list[str]:
    if not nodes_file().is_file():
        return []
    try:
        data = json.loads(nodes_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted((data.get("nodes") or {}).keys())


def main() -> None:
    ttl = float(DEFAULT_TTLS["snapshot_ttl_sec"])
    now = time.time()
    resources = {}
    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        node = parse_snapshot(path)
        collected = parse_ts(node.get("collected_at"))
        age = int(now - collected) if collected is not None else None
        stale = collected is None or age > ttl
        node["snapshot_age_sec"] = age
        node["snapshot_age_text"] = format_age(age)
        node["stale"] = stale
        node["stale_reason"] = None if not stale else ("missing_timestamp" if collected is None else "snapshot_ttl")
        resources[node["node"]] = node
        print(
            f"{node['node']}: "
            f"{node['occupied_count']}/{node['gpu_count']} occupied, "
            f"{node['free_count']} free"
            + (f" [stale {node['snapshot_age_text']}]" if stale else "")
        )

    configured = _configured_nodes()
    missing = [name for name in configured if name not in resources]
    stale = [
        {"node": name, "last_ok_at": node.get("collected_at"), "age_sec": node.get("snapshot_age_sec"), "age_text": node.get("snapshot_age_text")}
        for name, node in sorted(resources.items())
        if node.get("stale")
    ]
    health = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attempted": configured,
        "ok": [name for name in configured if name in resources and not resources[name].get("stale")],
        "failed": missing,
        "stale": stale,
        "parsed": sorted(resources.keys()),
    }

    RESOURCES_FILE.write_text(
        json.dumps(resources, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    atomic_write_json(STATE_DIR / "resources_health.json", health)
    print(f"Saved: {RESOURCES_FILE}")
    if missing:
        print(f"Unreachable (no fresh snapshot): {', '.join(missing)}")
    if stale:
        print(f"Stale snapshots: {', '.join(x['node'] for x in stale)}")


if __name__ == "__main__":
    main()
