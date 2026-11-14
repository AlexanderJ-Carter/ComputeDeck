#!/usr/bin/env python3
"""Collect structured per-node exec results written by playbooks/exec.yml."""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401
from paths import STATE_DIR

EXEC_DIR = STATE_DIR / "exec"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    results = []
    if EXEC_DIR.is_dir():
        for path in sorted(EXEC_DIR.glob("*.json")):
            results.append(json.loads(path.read_text(encoding="utf-8")))

    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        return 0

    if not results:
        print("No exec results")
        return 1

    worst = 0
    for item in results:
        rc = int(item.get("rc", 1))
        worst = max(worst, 0 if rc == 0 else rc)
        print(f"===== {item.get('node')} (rc={rc}) =====")
        stdout = (item.get("stdout") or "").rstrip()
        stderr = (item.get("stderr") or "").rstrip()
        if stdout:
            print(stdout)
        if stderr:
            print(stderr, file=sys.stderr)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
