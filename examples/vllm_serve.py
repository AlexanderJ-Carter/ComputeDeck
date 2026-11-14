#!/usr/bin/env python3
"""vLLM OpenAI-compatible service manager for gpu-control Jobs.

This is a small reference manager: it shows the contract a process Job needs
(start / stop / restart / status / wait) and adds a real readiness check
instead of treating "process is alive" as "service is ready".

Job template: ``config/examples/vllm-serve.example.json``.

Readiness:
  * ``GPU_CONTROL_HEALTH_URL`` (optional) — HTTP GET must return 2xx.
  * otherwise the TCP port parsed from ``--port`` (default 8000) must accept.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def runtime_dir() -> Path:
    raw = os.environ.get("GPU_CONTROL_JOB_RUNTIME", "").strip()
    if raw:
        return Path(raw)
    name = os.environ.get("GPU_CONTROL_JOB_NAME", "vllm-serve").strip() or "vllm-serve"
    return Path("/tmp/gpu-control-jobs") / name


def pid_file() -> Path:
    return runtime_dir() / "job.pid"


def log_file() -> Path:
    return runtime_dir() / "job.log"


def running_pid() -> int | None:
    path = pid_file()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        return None
    pid = int(text)
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def _port_from_args(rest: list[str]) -> int:
    for i, item in enumerate(rest):
        if item in {"--port", "-p"} and i + 1 < len(rest):
            try:
                return int(rest[i + 1])
            except ValueError:
                break
        if item.startswith("--port="):
            try:
                return int(item.split("=", 1)[1])
            except ValueError:
                break
    return int(os.environ.get("GPU_CONTROL_HEALTH_PORT", "8000"))


def _tcp_ready(port: int) -> bool:
    try:
        with socket.create_connection((os.environ.get("GPU_CONTROL_HEALTH_HOST", "127.0.0.1"), port), timeout=1.5):
            return True
    except OSError:
        return False


def _http_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — operator-provided URL
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ready(rest: list[str]) -> bool:
    url = os.environ.get("GPU_CONTROL_HEALTH_URL", "").strip()
    if url:
        return _http_ready(url)
    return _tcp_ready(_port_from_args(rest))


def cmd_start(args: argparse.Namespace) -> int:
    if running_pid() is not None:
        print("already running")
        return 1
    runtime_dir().mkdir(parents=True, exist_ok=True)
    rest = list(args.rest or [])
    if rest and rest[0] == "--":
        rest = rest[1:]
    cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", *rest]
    env = os.environ.copy()
    log = log_file().open("a", encoding="utf-8")
    log.write(f"# start {time.strftime('%Y-%m-%d %H:%M:%S')}\n# cmd={' '.join(cmd)}\n")
    log.flush()
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=args.workdir or None,
    )
    pid_file().write_text(f"{proc.pid}\n", encoding="utf-8")
    print(f"started pid={proc.pid} port={_port_from_args(rest)} log={log_file()}")
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    pid = running_pid()
    if pid is None:
        pid_file().unlink(missing_ok=True)
        print("not running")
        return 0
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)
    for _ in range(60):
        if running_pid() is None:
            break
        time.sleep(0.25)
    if running_pid() is not None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    pid_file().unlink(missing_ok=True)
    print("stopped")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    pid = running_pid()
    if pid is None:
        print("NOT READY")
        return 1
    if ready(args.rest or []):
        print(f"READY pid={pid} port={_port_from_args(args.rest or [])}")
        return 0
    print(f"STARTING pid={pid} (process alive, service not ready)")
    return 1


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.time() + max(1, int(args.timeout))
    rest = args.rest or []
    while time.time() < deadline:
        if running_pid() is None:
            print("FAILED process exited; see log")
            return 1
        if ready(rest):
            print("ready")
            return 0
        time.sleep(1)
    print("timeout")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "wait"])
    parser.add_argument("--workdir", help="working directory")
    parser.add_argument("--timeout", type=int, default=900, help="wait timeout seconds")
    parser.add_argument("rest", nargs=argparse.REMAINDER, help="vLLM args after --")
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
