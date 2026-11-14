#!/usr/bin/env python3
"""Merge per-node probe JSON files into state/probe.json matrix."""

from __future__ import annotations

import json

import _bootstrap  # noqa: F401
from paths import STATE_DIR

OUTPUT = STATE_DIR / "probe.json"


def main() -> None:
    rows: list[dict] = []
    for path in sorted(STATE_DIR.glob("probe-*.json")):
        if path.name == "probe.json":
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
        "ok_count": ok_count,
        "fail_count": fail_count,
        "nodes": nodes,
        "matrix": matrix,
        "rows": rows,
    }
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Probe edges: ok={ok_count} fail={fail_count}")
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
