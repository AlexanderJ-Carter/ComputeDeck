#!/usr/bin/env python3
"""Render inventory.ini (and optional SSH config snippet) from nodes.json."""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401
from paths import INVENTORY_FILE, STATE_DIR, expand_path, nodes_file


def _control_meta(data: dict) -> dict | None:
    ctrl = data.get("control")
    if isinstance(ctrl, dict) and ctrl.get("ansible_host"):
        return ctrl
    # legacy: control listed inside nodes with role=control
    for name, meta in (data.get("nodes") or {}).items():
        if (meta or {}).get("role") == "control":
            return {"name": name, **meta}
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-ssh-snippet", action="store_true")
    args = parser.parse_args()

    data = json.loads(nodes_file().read_text(encoding="utf-8"))
    defaults = data.get("defaults") or {}
    nodes = data.get("nodes") or {}
    control = _control_meta(data)
    control_name = (control or {}).get("name") or "control"

    lines: list[str] = []
    if control:
        lines.extend(
            [
                "[control]",
                f"{control_name} ansible_host={control.get('ansible_host', '')}",
                "",
                "[control:vars]",
                "ansible_connection=local",
                f"ansible_user={defaults.get('ansible_user', 'sankuai')}",
                "",
            ]
        )

    lines.append("[gpu_workers]")
    for name, meta in sorted(nodes.items()):
        if (meta or {}).get("role") == "control":
            continue
        host = meta.get("ansible_host", "")
        gpus = meta.get("gpus_per_node", defaults.get("gpus_per_node", 4))
        role = meta.get("role") or defaults.get("role") or "serving"
        lines.append(f"{name} ansible_host={host} gpus_per_node={gpus} node_role={role}")
    lines.append("")
    lines.append("[gpu_workers:vars]")
    for key in (
        "ansible_user",
        "ansible_port",
        "ansible_connection",
        "ansible_ssh_private_key_file",
    ):
        if key in defaults:
            value = defaults[key]
            if key == "ansible_ssh_private_key_file":
                value = expand_path(str(value))
            lines.append(f"{key}={value}")
    lines.append("")

    INVENTORY_FILE.write_text("\n".join(lines), encoding="utf-8")
    worker_count = sum(1 for _, m in nodes.items() if (m or {}).get("role") != "control")
    print(f"Wrote {INVENTORY_FILE} (workers={worker_count}; control={control_name if control else '-'})")

    if args.write_ssh_snippet:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        snippet = STATE_DIR / "ssh_hosts.conf"
        user = defaults.get("ansible_user", "root")
        port = defaults.get("ansible_port", 22)
        key = expand_path(str(defaults.get("ansible_ssh_private_key_file", "")))
        blocks = []
        if control and control.get("ansible_host"):
            blocks.append(
                "\n".join(
                    [
                        f"Host {control_name}",
                        f"    HostName {control.get('ansible_host')}",
                        f"    User {user}",
                        f"    Port {port}",
                        f"    IdentityFile {key}",
                    ]
                )
            )
        for name, meta in sorted(nodes.items()):
            if (meta or {}).get("role") == "control":
                continue
            blocks.append(
                "\n".join(
                    [
                        f"Host {name}",
                        f"    HostName {meta.get('ansible_host')}",
                        f"    User {user}",
                        f"    Port {port}",
                        f"    IdentityFile {key}",
                    ]
                )
            )
        snippet.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        print(f"Wrote {snippet}")
        print(f"Append to ~/.ssh/config with: Include {snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
