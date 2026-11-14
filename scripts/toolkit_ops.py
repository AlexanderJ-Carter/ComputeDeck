#!/usr/bin/env python3
"""节点 AI 工具栈：list / probe / install。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from node_paths import default_paths, load_nodes_data, node_paths  # noqa: E402
from paths import (  # noqa: E402
    INVENTORY_FILE,
    PROJECT_DIR,
    STATE_DIR,
    ansible_cmd,
    ensure_runtime_dirs,
)
from toolkits_util import (  # noqa: E402
    build_install_script,
    build_probe_script,
    catalog_payload,
    load_toolkit_state,
    resolve_ids,
    save_toolkit_state,
)

EXEC_DIR = STATE_DIR / "exec"


def _ansible_env(*, quiet: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")
    if quiet:
        env["ANSIBLE_STDOUT_CALLBACK"] = "minimal"
    return env


def _inventory_nodes() -> list[str]:
    data = load_nodes_data()
    nodes = data.get("nodes") or {}
    if nodes:
        return sorted(nodes.keys())
    if not INVENTORY_FILE.is_file():
        return []
    out: list[str] = []
    in_workers = False
    for line in INVENTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_workers = line == "[gpu_workers]"
            continue
        if not in_workers:
            continue
        name = line.split()[0]
        if not name.startswith("ansible_"):
            out.append(name)
    return out


def _select_nodes(node: str | None, nodes: str | None) -> list[str]:
    all_nodes = _inventory_nodes()
    if node:
        return [node]
    if nodes and nodes != "all":
        wanted = {n.strip() for n in nodes.split(",") if n.strip()}
        return [n for n in all_nodes if n in wanted]
    return all_nodes


def _python_for(node: str) -> str:
    paths = node_paths(node, load_nodes_data())
    return paths.get("python") or default_paths().get("python") or "python3"


def _run_remote(command: str, limit: list[str], *, quiet: bool = False) -> int:
    ensure_runtime_dirs()
    if EXEC_DIR.is_dir():
        for stale in EXEC_DIR.glob("*.json"):
            stale.unlink()
    cmd = ansible_cmd("-i", str(INVENTORY_FILE), str(PROJECT_DIR / "playbooks" / "exec.yml"))
    if limit:
        cmd.extend(["--limit", ",".join(limit)])
    cmd.extend(["-e", json.dumps({"remote_command": command}, ensure_ascii=False)])
    if not quiet:
        print("+", " ".join(cmd[:5]), f"limit={','.join(limit)}")
    return subprocess.call(cmd, cwd=PROJECT_DIR, env=_ansible_env(quiet=quiet))


def _read_exec_results(limit: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in limit:
        path = EXEC_DIR / f"{name}.json"
        if not path.is_file():
            out[name] = {"node": name, "rc": -1, "stdout": "", "stderr": "missing result"}
            continue
        out[name] = json.loads(path.read_text(encoding="utf-8"))
    return out


def cmd_list(_: argparse.Namespace) -> int:
    print(json.dumps(catalog_payload(), ensure_ascii=False, indent=2))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    toolkits = resolve_ids(list(args.id or []) or None, args.bundle)
    limit = _select_nodes(args.node, args.nodes)
    if not limit:
        print("没有可探测的 gpu_workers", file=sys.stderr)
        return 1

    groups: dict[str, list[str]] = {}
    for name in limit:
        groups.setdefault(_python_for(name), []).append(name)

    nodes_state: dict[str, Any] = dict(load_toolkit_state().get("nodes") or {})
    overall_rc = 0
    for python_bin, group in groups.items():
        script = build_probe_script(toolkits, python_bin=python_bin)
        rc = _run_remote(script, group, quiet=bool(args.quiet))
        if rc != 0:
            overall_rc = rc
        results = _read_exec_results(group)
        for name, raw in results.items():
            entry: dict[str, Any] = {
                "node": name,
                "rc": raw.get("rc"),
                "python": python_bin,
                "toolkits": [],
                "raw": (raw.get("stdout") or "")[:2000],
            }
            stdout = (raw.get("stdout") or "").strip().splitlines()
            if stdout and raw.get("rc") == 0:
                payload = json.loads(stdout[-1])
                entry["toolkits"] = payload.get("toolkits") or []
            nodes_state[name] = entry

    state = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ids": [t["id"] for t in toolkits],
        "nodes": nodes_state,
    }
    save_toolkit_state(state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return overall_rc


def cmd_install(args: argparse.Namespace) -> int:
    if not args.id and not args.bundle:
        print("install 需要 --id 或 --bundle（如 --bundle train-stack）", file=sys.stderr)
        return 2
    toolkits = resolve_ids(list(args.id or []) or None, args.bundle)
    limit = _select_nodes(args.node, args.nodes)
    if not limit:
        print("没有可安装的 gpu_workers", file=sys.stderr)
        return 1

    groups: dict[str, list[str]] = {}
    for name in limit:
        groups.setdefault(_python_for(name), []).append(name)

    overall_rc = 0
    reports: list[dict[str, Any]] = []
    for python_bin, group in groups.items():
        script = build_install_script(toolkits, python_bin=python_bin)
        if args.dry_run:
            reports.append({"python": python_bin, "nodes": group, "command": script})
            continue
        rc = _run_remote(script, group, quiet=bool(args.quiet))
        if rc != 0:
            overall_rc = rc
        results = _read_exec_results(group)
        reports.append(
            {
                "python": python_bin,
                "nodes": group,
                "rc": rc,
                "results": {
                    n: {
                        "rc": results[n].get("rc"),
                        "stdout_tail": (results[n].get("stdout") or "")[-500:],
                        "stderr_tail": (results[n].get("stderr") or "")[-300:],
                    }
                    for n in group
                },
            }
        )

    if args.dry_run:
        print(json.dumps({"dry_run": True, "plans": reports}, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps({"ok": overall_rc == 0, "installs": reports}, ensure_ascii=False, indent=2))
    probe_ns = argparse.Namespace(
        id=[t["id"] for t in toolkits],
        bundle=None,
        node=args.node,
        nodes=args.nodes,
        quiet=args.quiet,
    )
    cmd_probe(probe_ns)
    return overall_rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="列出工具栈与最近探测")
    p_list.set_defaults(func=cmd_list)

    p_probe = sub.add_parser("probe", help="在节点探测是否已安装")
    p_probe.add_argument("--id", action="append", default=[], help="toolkit id，可重复")
    p_probe.add_argument("--bundle", help="bundle id，如 train-stack")
    p_probe.add_argument("--node")
    p_probe.add_argument("--nodes", default="all")
    p_probe.add_argument("--quiet", action="store_true")
    p_probe.set_defaults(func=cmd_probe)

    p_install = sub.add_parser("install", help="在节点 pip 安装")
    p_install.add_argument("--id", action="append", default=[], help="toolkit id，可重复")
    p_install.add_argument("--bundle", help="bundle id，如 train-stack")
    p_install.add_argument("--node")
    p_install.add_argument("--nodes", default="all")
    p_install.add_argument("--dry-run", action="store_true")
    p_install.add_argument("--quiet", action="store_true")
    p_install.set_defaults(func=cmd_install)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
