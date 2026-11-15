#!/usr/bin/env python3
"""gpu-control Job 管理脚本骨架（复制后按业务改）。

约定：``argv[1]`` 为 ``start|stop|restart|status|wait``，其后为可选 extra_args。
成功退出 0；``status`` 未就绪时可退出 1。
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

JOB_NAME = (os.environ.get("GPU_CONTROL_JOB_NAME") or "shell-job").strip() or "shell-job"
RUNTIME = Path(os.environ.get("GPU_CONTROL_JOB_RUNTIME") or f"/tmp/gpu-control-jobs/{JOB_NAME}")
PID_FILE = RUNTIME / "job.pid"
LOG_FILE = RUNTIME / "job.log"


def is_running() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def cmd_start(_: argparse.Namespace) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if is_running() is not None:
        print("already running")
        return 1
    pid = os.fork()
    if pid == 0:
        # child: trivial heartbeat process
        with LOG_FILE.open("a", encoding="utf-8") as log:
            log.write(f"started at {time.time()}\n")
            log.flush()
            while True:
                time.sleep(30)
                log.write(f"heartbeat {time.time()}\n")
                log.flush()
    PID_FILE.write_text(str(pid), encoding="utf-8")
    print(f"started pid={pid}")
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    pid = is_running()
    if pid is None:
        print("not running")
        PID_FILE.unlink(missing_ok=True)
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if is_running() is None:
            break
        time.sleep(0.2)
    if is_running() is not None:
        os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print("stopped")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    pid = is_running()
    if pid is None:
        print("NOT READY")
        return 1
    print(f"READY pid={pid}")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.time() + max(1, int(args.timeout))
    while time.time() < deadline:
        if cmd_status(args) == 0:
            return 0
        time.sleep(1)
    print("timeout")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "wait"])
    parser.add_argument("--timeout", type=int, default=60)
    args, _unknown = parser.parse_known_args()
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
