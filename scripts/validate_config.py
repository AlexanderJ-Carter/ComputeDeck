#!/usr/bin/env python3
"""校验集群配置是否可发布（``gpu-control doctor``）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import (
    ANSIBLE,
    CONFIG_DIR,
    INVENTORY_FILE,
    LOCAL_DIR,
    PROJECT_DIR,
    STATE_DIR,
    cluster_file,
    expand_path,
    list_job_files,
    nodes_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    issues: list[str] = []
    notes: list[str] = []

    cpath = cluster_file()
    npath = nodes_file()
    if not cpath.is_file():
        issues.append(f"missing cluster config (expected {LOCAL_DIR / 'cluster.json'})")
    if not npath.is_file():
        issues.append(f"missing nodes config (expected {LOCAL_DIR / 'nodes.json'})")

    cluster = {}
    nodes = {}
    if cpath.is_file():
        try:
            cluster = json.loads(cpath.read_text(encoding="utf-8"))
            notes.append(f"cluster: {cpath.relative_to(PROJECT_DIR)}")
        except json.JSONDecodeError as exc:
            issues.append(f"invalid cluster json: {exc}")
    if npath.is_file():
        try:
            nodes = json.loads(npath.read_text(encoding="utf-8"))
            notes.append(f"nodes: {npath.relative_to(PROJECT_DIR)}")
        except json.JSONDecodeError as exc:
            issues.append(f"invalid nodes json: {exc}")

    if cluster:
        for key in ("name", "strategy", "default_job", "api", "scheduling"):
            if key not in cluster:
                issues.append(f"cluster.json missing field: {key}")
        api = cluster.get("api") or {}
        key_file = api.get("api_key_file")
        if key_file:
            key_file = expand_path(key_file)
            if not Path(key_file).is_file():
                issues.append(f"api_key_file not found: {key_file}")
            else:
                notes.append("api_key_file ok")

    node_map = (nodes.get("nodes") or {}) if nodes else {}
    if not node_map:
        issues.append("nodes.json has no nodes")
    else:
        for name, meta in node_map.items():
            if not meta.get("ansible_host"):
                issues.append(f"node {name} missing ansible_host")

    if INVENTORY_FILE.is_file() and node_map:
        text = INVENTORY_FILE.read_text(encoding="utf-8")
        for name in node_map:
            if name not in text:
                issues.append(f"inventory.ini missing host {name} (run: gpu-control node render)")
    elif node_map:
        issues.append("inventory.ini missing (run: gpu-control node render)")

    defaults = (nodes.get("defaults") or {}) if nodes else {}
    ssh_key = defaults.get("ansible_ssh_private_key_file")
    if ssh_key:
        ssh_key = expand_path(ssh_key)
        if not Path(ssh_key).is_file():
            issues.append(f"ssh key missing: {ssh_key}")
        else:
            notes.append("ssh key ok")

    if not ANSIBLE.is_file():
        issues.append("venv ansible-playbook missing (pip install -r requirements.txt)")
    else:
        notes.append("ansible ok")

    jobs = list_job_files()
    if not jobs:
        issues.append(
            "no jobs registered（gpu-control jobs install --recommended）"
        )
    else:
        notes.append(f"jobs: {', '.join(p.stem for p in jobs)}")
        from jobs_util import validate_registered_jobs

        for problem in validate_registered_jobs():
            issues.append(f"job schema: {problem}")

    if (CONFIG_DIR / "examples").is_dir():
        notes.append("config/examples present")
    if STATE_DIR.is_dir():
        notes.append("state/ present")

    paths = (defaults.get("paths") or {}) if defaults else {}
    if paths:
        notes.append(f"defaults.paths: {', '.join(sorted(paths))}")
    else:
        notes.append("defaults.paths unset（Job/models 若用 ${paths.*} 需配置）")

    models_path = LOCAL_DIR / "models.json"
    if models_path.is_file():
        notes.append("models.json present（可选模块）")
    handbook = PROJECT_DIR / "docs" / "手册.md"
    if handbook.is_file():
        notes.append("docs/手册.md present")

    from auth_util import doctor_auth_notes

    auth_issues, auth_notes = doctor_auth_notes(cluster if cluster else None)
    issues.extend(auth_issues)
    notes.extend(auth_notes)

    payload = {
        "ok": not issues,
        "issues": issues,
        "notes": notes,
        "nodes": sorted(node_map.keys()),
        "strategy": cluster.get("strategy"),
        "project": str(PROJECT_DIR),
        "config_local": str(LOCAL_DIR),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"doctor: {'OK' if payload['ok'] else 'ISSUES'}")
        for item in issues:
            print(f"  ! {item}")
        for item in notes:
            print(f"  · {item}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
