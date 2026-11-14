#!/usr/bin/env python3
"""Ray cluster manager for gpu-control Jobs.

gpu-control's distributed job injection already provides NODE_RANK, NNODES,
MASTER_ADDR and MASTER_PORT, so a Ray cluster can be expressed as one Job:

  * rank 0  → `ray start --head` (GCS on MASTER_PORT, dashboard on 8265)
  * rank >0 → `ray start --address=<MASTER_ADDR>:<MASTER_PORT>`

Template: ``config/examples/ray-cluster.example.json``.
Use ``ray job submit --address http://<master>:8265 -- <cmd>`` to run jobs.

Env:
  GPU_CONTROL_JOB_RUNTIME  runtime/log directory (default /tmp/gpu-control-jobs/ray-cluster)
  RAY_DASHBOARD_PORT       dashboard port (default 8265)
  RAY_NUM_GPUS             GPUs to advertise per node (default NPROC_PER_NODE / 1)
"""

from __future__ import annotations

import argparse
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def runtime_dir() -> Path:
    raw = _env("GPU_CONTROL_JOB_RUNTIME")
    if raw:
        return Path(raw)
    name = _env("GPU_CONTROL_JOB_NAME", "ray-cluster") or "ray-cluster"
    return Path("/tmp/gpu-control-jobs") / name


def log_file() -> Path:
    return runtime_dir() / "job.log"


def state_file() -> Path:
    return runtime_dir() / "ray.state"


def topology() -> dict[str, str]:
    return {
        "nnodes": _env("NNODES", "1") or "1",
        "rank": _env("NODE_RANK", "0") or "0",
        "addr": _env("MASTER_ADDR", "127.0.0.1") or "127.0.0.1",
        "port": _env("MASTER_PORT", "6379") or "6379",
        "nproc": _env("NPROC_PER_NODE", "1") or "1",
        "dashboard": _env("RAY_DASHBOARD_PORT", "8265") or "8265",
        "gpus": _env("RAY_NUM_GPUS") or _env("NPROC_PER_NODE", "1") or "1",
    }


def _run(cmd: list[str]) -> int:
    runtime_dir().mkdir(parents=True, exist_ok=True)
    with log_file().open("a", encoding="utf-8") as log:
        log.write(f"# {' '.join(shlex.quote(x) for x in cmd)}\n")
        log.flush()
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
    return proc.returncode


def cluster_address(top: dict[str, str]) -> str:
    return f"{top['addr']}:{top['port']}"


def ray_status(top: dict[str, str]) -> bool:
    """Return True when the GCS/Ray cluster answers ``ray status``."""
    cmd = ["ray", "status", f"--address={cluster_address(top)}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _wait_tcp(host: str, port: str, timeout: int = 180) -> bool:
    deadline = time.time() + max(1, timeout)
    while time.time() < deadline:
        try:
            with socket.create_connection((host, int(port)), timeout=2):
                return True
        except (OSError, ValueError):
            time.sleep(2)
    return False


def cmd_start(_: argparse.Namespace) -> int:
    top = topology()
    is_head = top["rank"] == "0"
    if not is_head and not _wait_tcp(top["addr"], top["port"]):
        print(f"head not reachable: {top['addr']}:{top['port']}")
        return 1
    cmd = ["ray", "start", "--num-gpus", top["gpus"], "--disable-usage-stats"]
    if is_head:
        cmd += [
            "--head",
            "--node-ip-address",
            top["addr"],
            "--port",
            top["port"],
            "--dashboard-host",
            "0.0.0.0",
            "--dashboard-port",
            top["dashboard"],
        ]
    else:
        cmd += ["--address", cluster_address(top)]
    rc = _run(cmd)
    if rc == 0:
        runtime_dir().mkdir(parents=True, exist_ok=True)
        state_file().write_text(
            f"rank={top['rank']}\nnodes={top['nnodes']}\naddress={cluster_address(top)}\n",
            encoding="utf-8",
        )
        print(f"ray started rank={top['rank']}/{top['nnodes']} address={cluster_address(top)}")
    return rc


def cmd_stop(_: argparse.Namespace) -> int:
    rc = _run(["ray", "stop", "--force"])
    state_file().unlink(missing_ok=True)
    print("ray stopped")
    return 0 if rc == 0 else rc


def cmd_status(_: argparse.Namespace) -> int:
    top = topology()
    if ray_status(top):
        print(f"READY address={cluster_address(top)} dashboard=:{top['dashboard']}")
        return 0
    print("NOT READY")
    return 1


def cmd_wait(args: argparse.Namespace) -> int:
    top = topology()
    deadline = time.time() + max(1, int(args.timeout))
    while time.time() < deadline:
        if ray_status(top):
            print("ready")
            return 0
        time.sleep(1)
    print("timeout")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "wait"])
    parser.add_argument("--timeout", type=int, default=300, help="wait timeout seconds")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "restart":
        cmd_stop(args)
        return cmd_start(args)
    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "wait": cmd_wait,
    }
    return int(handlers[args.action](args))


if __name__ == "__main__":
    raise SystemExit(main())
