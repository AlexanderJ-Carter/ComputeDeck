#!/usr/bin/env python3
"""节点存活探测：对 ``nodes.json`` 的每台主机做 TCP 连通性检查。

写入 ``state/liveness.json``，供总览 / 公告 / watchdog / 调度共用。
默认只跑一次；``--loop`` 常驻（UI 内也有后台线程，二选一即可）。
"""

from __future__ import annotations

import argparse
import json
import time

import _bootstrap  # noqa: F401
from liveness import run_check
from paths import STATE_DIR, cluster_file, nodes_file


def _load(path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run_once(*, only: list[str] | None = None, timeout: float | None = None) -> dict:
    nodes_doc = _load(nodes_file(), {"defaults": {}, "nodes": {}})
    cluster = _load(cluster_file(), {})
    if not (nodes_doc.get("nodes") or {}):
        return {"ok": False, "error": "nodes.json 未配置节点", "nodes": {}, "summary": {}}
    snapshot = run_check(
        nodes_doc,
        cluster=cluster,
        snapshot_path=STATE_DIR / "liveness.json",
        timeout=timeout,
        only=only,
    )
    snapshot["ok"] = True
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--node", action="append", default=[], help="只检查指定节点，可重复")
    parser.add_argument("--timeout", type=float, default=None, help="单节点 TCP 超时秒数")
    parser.add_argument("--loop", action="store_true", help="常驻循环探测")
    parser.add_argument("--interval", type=int, default=0, help="循环间隔（默认读 cluster.health.check_interval_sec）")
    args = parser.parse_args()

    if not args.loop:
        snapshot = run_once(only=args.node or None, timeout=args.timeout)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            _print_human(snapshot)
        return 0 if snapshot.get("ok") else 1

    cluster = _load(cluster_file(), {})
    interval = args.interval or int(((cluster.get("health") or {}).get("check_interval_sec")) or 60)
    while True:
        snapshot = run_once(only=args.node or None, timeout=args.timeout)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False))
        else:
            _print_human(snapshot)
        time.sleep(max(10, interval))


def _print_human(snapshot: dict) -> None:
    summary = snapshot.get("summary") or {}
    print(
        "Liveness: "
        f"online={summary.get('online', 0)} "
        f"degraded={summary.get('degraded', 0)} "
        f"offline={summary.get('offline', 0)} "
        f"unknown={summary.get('unknown', 0)} "
        f"at={snapshot.get('generated_at')}"
    )
    for name, node in sorted((snapshot.get("nodes") or {}).items()):
        checks = (node.get("checks") or {}).get("ssh") or {}
        latency = checks.get("latency_ms")
        suffix = f" {latency}ms" if latency is not None else ""
        error = node.get("error")
        print(
            f"  {name:<14} {str(node.get('state') or 'unknown'):<9}"
            f"{suffix}{(' — ' + str(error)[:120]) if error else ''}"
        )
    if snapshot.get("error"):
        print(f"error: {snapshot['error']}")


if __name__ == "__main__":
    raise SystemExit(main())
