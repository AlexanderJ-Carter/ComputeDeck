#!/usr/bin/env python3
"""Watch registered jobs and restart them when status becomes unhealthy."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone

import _bootstrap  # noqa: F401
from paths import CLI, PROJECT_DIR, STATE_DIR, VENV_PYTHON

LOG_FILE = STATE_DIR / "watchdog.log"
STATE_FILE = STATE_DIR / "watchdog_state.json"


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {message}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VENV_PYTHON), str(CLI), *args],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def load_state() -> dict:
    if not STATE_FILE.is_file():
        return {"restarts": {}}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_and_fix(job: str, nodes: str | None, max_restarts: int) -> None:
    args = ["status", job]
    if nodes:
        args.extend(["--nodes", nodes])
    result = run_cli(args)
    healthy = result.returncode == 0
    log(f"check job={job} nodes={nodes or 'all'} healthy={healthy} rc={result.returncode}")
    if healthy:
        return

    state = load_state()
    key = f"{job}:{nodes or 'all'}"
    count = int(state.setdefault("restarts", {}).get(key, 0))
    if count >= max_restarts:
        log(f"skip restart job={job} reached max_restarts={max_restarts}")
        return

    log(f"restarting unhealthy job={job} attempt={count + 1}/{max_restarts}")
    restart_args = ["restart", job]
    if nodes:
        restart_args.extend(["--nodes", nodes])
    restart = run_cli(restart_args)
    log(f"restart rc={restart.returncode}")
    if restart.stdout.strip():
        log(restart.stdout.strip().splitlines()[-1])
    if restart.returncode == 0:
        wait = run_cli(["wait", job, *(["--nodes", nodes] if nodes else [])])
        log(f"wait rc={wait.returncode}")
        if wait.returncode == 0:
            state["restarts"][key] = 0
        else:
            state["restarts"][key] = count + 1
    else:
        state["restarts"][key] = count + 1
    state["last_action"] = {
        "job": job,
        "nodes": nodes,
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
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    args = parser.parse_args()

    jobs = args.jobs or ["decider"]
    log(f"watchdog start jobs={jobs} interval={args.interval}s nodes={args.nodes or 'all'}")
    while True:
        for job in jobs:
            try:
                check_and_fix(job, args.nodes, args.max_restarts)
            except Exception as exc:  # noqa: BLE001 - keep daemon alive
                log(f"error job={job}: {exc}")
        if args.once:
            return 0
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
