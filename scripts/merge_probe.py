#!/usr/bin/env python3
"""合并各节点 probe JSON，写入 ``state/probe.json`` 矩阵。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import _bootstrap  # noqa: F401
from paths import STATE_DIR

OUTPUT = STATE_DIR / "probe.json"
DEFAULT_MAX_AGE_SEC = 86400


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-age-sec",
        type=int,
        default=DEFAULT_MAX_AGE_SEC,
        help="超过该年龄的 probe-<node>.json 视为过期并跳过（默认 24h）",
    )
    parser.add_argument("--json", action="store_true", help="输出完整报告 JSON")
    args = parser.parse_args()

    now = time.time()
    rows: list[dict] = []
    sources: dict[str, dict] = {}
    skipped: list[dict] = []
    for path in sorted(STATE_DIR.glob("probe-*.json")):
        if path.name == "probe.json":
            continue
        age = int(now - path.stat().st_mtime)
        node = path.name[len("probe-"):-len(".json")]
        if age > max(0, args.max_age_sec):
            skipped.append({"file": path.name, "age_sec": age})
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < 0:
            continue
        payload = json.loads(text[start : end + 1])
        if isinstance(payload, list):
            rows.extend(payload)
            sources[node] = {
                "file": path.name,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "age_sec": age,
                "rows": len(payload),
            }

    nodes = sorted({row["from"] for row in rows} | {row["to"] for row in rows})
    matrix = {src: {dst: None for dst in nodes if dst != src} for src in nodes}
    ok_count = 0
    fail_count = 0
    for row in rows:
        matrix[row["from"]][row["to"]] = {
            "ok": row["ok"],
            "host": row.get("host"),
            "port": row.get("port"),
            "error": row.get("error") or None,
        }
        if row["ok"]:
            ok_count += 1
        else:
            fail_count += 1

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok_count": ok_count,
        "fail_count": fail_count,
        "nodes": nodes,
        "matrix": matrix,
        "rows": rows,
        "sources": sources,
        "skipped": skipped,
    }
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"Probe edges: ok={ok_count} fail={fail_count}")
    if skipped:
        print(f"Skipped stale sources: {', '.join(x['file'] for x in skipped)}")
    width = max((len(n) for n in nodes), default=8)
    header = " " * width + "  " + " ".join(f"{n:>{width}}" for n in nodes)
    print(header)
    for src in nodes:
        cells = []
        for dst in nodes:
            if src == dst:
                cells.append(f"{'.':>{width}}")
            else:
                cell = matrix[src].get(dst)
                mark = "Y" if cell and cell["ok"] else "N"
                cells.append(f"{mark:>{width}}")
        print(f"{src:<{width}}  " + " ".join(cells))
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
