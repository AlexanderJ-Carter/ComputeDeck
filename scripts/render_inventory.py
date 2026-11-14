#!/usr/bin/env python3
"""Render inventory.ini (and optional SSH config snippet) from nodes.json."""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401
from paths import INVENTORY_FILE, STATE_DIR, expand_path, nodes_file


def _control_name(data: dict) -> str | None:
    ctrl = data.get("control") or {}
    if isinstance(ctrl, dict) and ctrl.get("name"):
        return str(ctrl["name"])
    nodes = data.get("nodes") or {}
    for name, meta in nodes.items():
        if (meta or {}).get("role") == "control":
            return name
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-ssh-snippet", action="store_true")
    args = parser.parse_args()

    data = json.loads(nodes_file().read_text(encoding="utf-8"))
    defaults = data.get("defaults") or {}
    nodes = data.get("nodes") or {}
    control = _control_name(data)

    lines: list[str] = []
    if control and control in nodes:
        meta = nodes[control]
        gpus = meta.get("gpus_per_node", defaults.get("gpus_per_node", 4))
        lines.extend(
            [
                "[control]",
                f"{control} ansible_host={meta.get('ansible_host', '')} gpus_per_node={gpus}",
                "",
            ]
        )

    lines.append("[gpu_workers]")
    for name, meta in sorted(nodes.items()):
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
    if control:
        lines.extend(
            [
                "[control:vars]",
                "ansible_connection=local",
                f"ansible_user={defaults.get('ansible_user', 'sankuai')}",
                "",
            ]
        )

    INVENTORY_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {INVENTORY_FILE} ({len(nodes)} nodes; control={control or '-'})")

    if args.write_ssh_snippet:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        snippet = STATE_DIR / "ssh_hosts.conf"
        user = defaults.get("ansible_user", "root")
        port = defaults.get("ansible_port", 22)
        key = expand_path(str(defaults.get("ansible_ssh_private_key_file", "")))
        blocks = []
        for name, meta in sorted(nodes.items()):
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
