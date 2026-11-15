#!/usr/bin/env python3
"""解析 GPU 快照，汇总为 ``resources.json``。"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import RESOURCES_FILE, SNAPSHOT_DIR


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


def main() -> None:
    resources = {}
    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        node = parse_snapshot(path)
        resources[node["node"]] = node
        print(
            f"{node['node']}: "
            f"{node['occupied_count']}/{node['gpu_count']} occupied, "
            f"{node['free_count']} free"
        )

    RESOURCES_FILE.write_text(
        json.dumps(resources, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Saved: {RESOURCES_FILE}")


if __name__ == "__main__":
    main()
