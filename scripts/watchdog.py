#!/usr/bin/env python3
"""监视已注册 Job；状态异常时按阈值自动重启。"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import _bootstrap  # noqa: F401
import liveness
from paths import CLI, INVENTORY_FILE, PROJECT_DIR, STATE_DIR, VENV_PYTHON, cluster_file, nodes_file
from jobs_util import default_job_name
from state_util import atomic_write_json

LOG_FILE = STATE_DIR / "watchdog.log"
STATE_FILE = STATE_DIR / "watchdog_state.json"
NODE_STATE_FILE = STATE_DIR / "node_state.json"
LOCK_FILE = STATE_DIR / "watchdog.lock"


def cordoned_nodes() -> set[str]:
    if not NODE_STATE_FILE.is_file():
        return set()
    data = json.loads(NODE_STATE_FILE.read_text(encoding="utf-8"))
    return set((data.get("cordoned") or {}).keys())


def inventory_workers() -> list[str]:
    hosts: list[str] = []
    if not INVENTORY_FILE.is_file():
        return hosts
    in_workers = False
    for line in INVENTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_workers = line == "[gpu_workers]"
            continue
        if in_workers:
            hosts.append(line.split()[0])
    return hosts


def _read(path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def liveness_view() -> dict:
    """读取存活视图：优先 liveness.json，缺失时回落既有健康文件。"""
    nodes_doc = _read(nodes_file(), {"nodes": {}})
    cluster = _read(cluster_file(), {})
    names = list((nodes_doc.get("nodes") or {}).keys()) or inventory_workers()
    return liveness.cluster_view(
        node_names=names,
        resources=_read(PROJECT_DIR / "resources.json", {}),
        ssh_quality=_read(STATE_DIR / "ssh_quality.json", {}),
        host_metrics=_read(STATE_DIR / "host_metrics.json", {}),
        probe=_read(STATE_DIR / "probe.json", {}),
        endpoint_health=_read(STATE_DIR / "endpoint_health.json", {}),
        persisted=liveness.read_snapshot(STATE_DIR / "liveness.json"),
        cluster=cluster,
    )


def unreachable_nodes() -> dict[str, str]:
    """返回 state ∈ {offline, unknown} 的节点 → 原因。"""
    view = liveness_view()
    out: dict[str, str] = {}
    for name, node in (view.get("nodes") or {}).items():
        state = str((node or {}).get("state") or "unknown")
        if state in {"offline", "unknown"}:
            out[name] = state
    return out


def schedulable_limit(nodes: str | None) -> str | None:
    blocked = cordoned_nodes()
    health = unreachable_nodes()
    if blocked:
        log(f"skip cordoned nodes={sorted(blocked)}")
    if health:
        log(
            "skip unreachable nodes="
            + ",".join(f"{name}({state})" for name, state in sorted(health.items()))
        )
    if nodes:
        wanted = [
            n.strip()
            for n in nodes.split(",")
            if n.strip() and n.strip() not in blocked and n.strip() not in health
        ]
    else:
        wanted = [n for n in inventory_workers() if n not in blocked and n not in health]
    if not wanted:
        return ""
    if nodes is None and len(wanted) == len(inventory_workers()):
        return None
    return ",".join(wanted)


def log(message: str) -> None:
    """单一日志落点：文件；只有前台交互终端才同时打印，避免后台重复行。"""
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {message}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    if sys.stdout.isatty():
        print(line, flush=True)


def run_cli(args: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VENV_PYTHON), str(CLI), *args],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def load_state() -> dict:
    if not STATE_FILE.is_file():
        return {"restarts": {}}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_FILE, data)


_RECAP = re.compile(r"^(\S+)\s*:\s*ok=\d+\s+changed=\d+\s+unreachable=(\d+)\s+failed=(\d+)", re.M)


def failed_hosts_from_output(output: str) -> list[str]:
    """从 Ansible PLAY RECAP 中找出 failed>0 且 unreachable=0 的主机。"""
    out = []
    for match in _RECAP.finditer(output or ""):
        host, unreachable, failed = match.group(1), int(match.group(2)), int(match.group(3))
        if failed > 0 and unreachable == 0:
            out.append(host)
    return sorted(set(out))


def check_and_fix(job: str, nodes: str | None, max_restarts: int, failure_threshold: int = 2) -> None:
    limit = schedulable_limit(nodes)
    if limit == "":
        health = unreachable_nodes()
        reason = "unreachable=" + ",".join(sorted(health)) if health else "all cordoned"
        log(f"skip job={job}: no schedulable nodes ({reason})")
        return
    args = ["status", job]
    if limit:
        args.extend(["--nodes", limit])
    result = run_cli(args)
    healthy = result.returncode == 0
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    log(f"check job={job} nodes={limit or 'all'} healthy={healthy} rc={result.returncode}")
    if not healthy and re.search(r"unreachable=[1-9]\d*", output, re.I):
        # 只有真正有 unreachable 主机时才跳过；failed=1 / unreachable=0 是 Job 故障。
        # 不能直接匹配单词 UNREACHABLE，否则会误伤 "unreachable=0"。
        log(f"skip restart job={job}: ansible reported UNREACHABLE hosts")
        state = load_state()
        state.setdefault("failures", {})[f"{job}:{limit or 'all'}"] = 0
        save_state(state)
        return
    state = load_state()
    key = f"{job}:{limit or 'all'}"
    failures = state.setdefault("failures", {})
    if healthy:
        # A healthy check resets both the failure streak and the restart budget.
        changed = False
        if int(state.setdefault("restarts", {}).get(key, 0)):
            state["restarts"][key] = 0
            changed = True
        if int(failures.get(key, 0)):
            failures[key] = 0
            changed = True
        if changed:
            save_state(state)
        return

    streak = int(failures.get(key, 0)) + 1
    failures[key] = streak
    save_state(state)
    if streak < max(1, failure_threshold):
        log(f"unhealthy job={job} streak={streak}/{failure_threshold}; waiting for confirmation")
        return
    log(f"confirmed unhealthy job={job} streak={streak}/{failure_threshold}")

    count = int(state.setdefault("restarts", {}).get(key, 0))
    if count >= max_restarts:
        log(f"skip restart job={job} reached max_restarts={max_restarts}")
        return

    failed_hosts = failed_hosts_from_output(output)
    restart_limit = ",".join(failed_hosts) if failed_hosts else limit
    log(
        f"restarting unhealthy job={job} attempt={count + 1}/{max_restarts}"
        + (f" nodes={restart_limit}" if restart_limit else "")
    )
    restart_args = ["restart", job, "--allow-cordoned"]
    if restart_limit:
        restart_args.extend(["--nodes", restart_limit])
    restart = run_cli(restart_args)
    log(f"restart rc={restart.returncode}")
    if restart.stdout.strip():
        log(restart.stdout.strip().splitlines()[-1])
    if restart.returncode == 0:
        wait_args = ["wait", job]
        if restart_limit:
            wait_args.extend(["--nodes", restart_limit])
        wait = run_cli(wait_args)
        log(f"wait rc={wait.returncode}")
        if wait.returncode == 0:
            state["restarts"][key] = 0
            failures[key] = 0
        else:
            state["restarts"][key] = count + 1
            failures[key] = 0
    else:
        state["restarts"][key] = count + 1
    state["last_action"] = {
        "job": job,
        "nodes": limit,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "restart_rc": restart.returncode,
    }
    save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", action="append", dest="jobs", help="Job to watch; repeatable")
    parser.add_argument("--nodes", help="Limit watchdog to these nodes")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between checks")
    parser.add_argument("--max-restarts", type=int, default=3, help="Max consecutive restarts per target")
    parser.add_argument("--failure-threshold", type=int, default=2, help="Consecutive unhealthy checks before restart")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    args = parser.parse_args()

    jobs = args.jobs or ([default_job_name()] if default_job_name() else [])
    if not jobs:
        log("no jobs to watch (register a job or pass --job)")
        return 1

    pid_file = STATE_DIR / "watchdog.pid"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_FILE.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"watchdog already running (lock={LOCK_FILE})", file=sys.stderr)
        return 0
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    stop = {"requested": False}

    def _stop(_signum, _frame):
        stop["requested"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    log(f"watchdog start jobs={jobs} interval={args.interval}s nodes={args.nodes or 'all'}")
    try:
        while not stop["requested"]:
            for job in jobs:
                try:
                    check_and_fix(job, args.nodes, args.max_restarts, args.failure_threshold)
                except Exception as exc:  # noqa: BLE001 - keep daemon alive
                    log(f"error job={job}: {exc}")
            if args.once:
                return 0
            # Sleep in small slices so SIGTERM is prompt.
            for _ in range(max(1, args.interval // 2)):
                if stop["requested"]:
                    break
                time.sleep(2)
    finally:
        try:
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()
        except OSError:
            pass
        log("watchdog stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
