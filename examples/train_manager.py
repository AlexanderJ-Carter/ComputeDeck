#!/usr/bin/env python3
"""通用训练 Job 管理脚本（LLaMA-Factory / DeepSpeed / torchrun / accelerate / raw）。

由 gpu-control manage-job 调用：argv[1] = start|stop|restart|status|wait。

分布式环境变量（job.distributed=true 时由 playbook 注入）：
  NNODES, NODE_RANK, MASTER_ADDR, MASTER_PORT, NPROC_PER_NODE

示例：
  python train_manager.py start --launcher llamafactory --config /path/sft.yaml
  python train_manager.py start --launcher torchrun -- module train.py --epochs 1
  python train_manager.py start --launcher deepspeed --deepspeed ds_z2.json -- train.py
  python train_manager.py start --launcher raw -- bash -lc 'my_train.sh'
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _runtime_dir() -> Path:
    raw = os.environ.get("GPU_CONTROL_JOB_RUNTIME", "").strip()
    if raw:
        return Path(raw)
    name = os.environ.get("GPU_CONTROL_JOB_NAME", "train-job").strip() or "train-job"
    return Path(f"/tmp/gpu-control-train/{name}")


def _pid_file() -> Path:
    return _runtime_dir() / "job.pid"


def _log_file() -> Path:
    return _runtime_dir() / "job.log"


def _meta_file() -> Path:
    return _runtime_dir() / "job.meta"


def is_running() -> int | None:
    path = _pid_file()
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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _dist_env() -> dict[str, str]:
    nnodes = os.environ.get("NNODES") or os.environ.get("GPU_CONTROL_NNODES") or "1"
    rank = os.environ.get("NODE_RANK") or os.environ.get("GPU_CONTROL_NODE_RANK") or "0"
    addr = os.environ.get("MASTER_ADDR") or os.environ.get("GPU_CONTROL_MASTER_ADDR") or "127.0.0.1"
    port = os.environ.get("MASTER_PORT") or os.environ.get("GPU_CONTROL_MASTER_PORT") or "29500"
    nproc = os.environ.get("NPROC_PER_NODE") or os.environ.get("GPU_CONTROL_NPROC_PER_NODE") or "1"
    return {
        "NNODES": str(nnodes),
        "NODE_RANK": str(rank),
        "MASTER_ADDR": str(addr),
        "MASTER_PORT": str(port),
        "NPROC_PER_NODE": str(nproc),
        "FORCE_TORCHRUN": os.environ.get("FORCE_TORCHRUN", "1"),
    }


def _build_command(args: argparse.Namespace) -> list[str]:
    dist = _dist_env()
    launcher = (args.launcher or "llamafactory").strip().lower()
    rest = list(args.rest or [])

    if launcher == "llamafactory":
        if not args.config:
            raise SystemExit("llamafactory 需要 --config <yaml>")
        return ["llamafactory-cli", "train", args.config, *rest]

    if launcher == "torchrun":
        cmd = [
            "torchrun",
            f"--nnodes={dist['NNODES']}",
            f"--node_rank={dist['NODE_RANK']}",
            f"--nproc_per_node={dist['NPROC_PER_NODE']}",
            f"--master_addr={dist['MASTER_ADDR']}",
            f"--master_port={dist['MASTER_PORT']}",
        ]
        if args.rdzv_id:
            cmd.extend(["--rdzv_backend", "c10d", "--rdzv_id", args.rdzv_id])
        cmd.extend(rest)
        if len(rest) == 0:
            raise SystemExit("torchrun 需要在 -- 后接训练入口，如 -- train.py")
        return cmd

    if launcher == "deepspeed":
        cmd = [
            "deepspeed",
            f"--num_gpus={dist['NPROC_PER_NODE']}",
            f"--num_nodes={dist['NNODES']}",
            f"--node_rank={dist['NODE_RANK']}",
            f"--master_addr={dist['MASTER_ADDR']}",
            f"--master_port={dist['MASTER_PORT']}",
        ]
        if args.deepspeed:
            # deepspeed 自身也吃 --deepspeed；训练脚本参数里再带一份亦可
            pass
        cmd.extend(rest)
        if args.deepspeed:
            cmd.extend(["--deepspeed", args.deepspeed])
        if len(rest) == 0:
            raise SystemExit("deepspeed 需要在 -- 后接训练入口，如 -- train.py")
        return cmd

    if launcher == "accelerate":
        cmd = [
            "accelerate",
            "launch",
            f"--num_machines={dist['NNODES']}",
            f"--machine_rank={dist['NODE_RANK']}",
            f"--num_processes={dist['NPROC_PER_NODE']}",
            f"--main_process_ip={dist['MASTER_ADDR']}",
            f"--main_process_port={dist['MASTER_PORT']}",
            *rest,
        ]
        if len(rest) == 0:
            raise SystemExit("accelerate 需要在 -- 后接训练入口")
        return cmd

    if launcher == "raw":
        if not rest:
            raise SystemExit("raw 需要在 -- 后接完整命令")
        return rest

    raise SystemExit(f"未知 launcher: {launcher}（llamafactory|torchrun|deepspeed|accelerate|raw）")


def cmd_start(args: argparse.Namespace) -> int:
    runtime = _runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    if is_running() is not None:
        print("already running")
        return 1

    command = _build_command(args)
    dist = _dist_env()
    env = os.environ.copy()
    env.update(dist)
    if args.workdir:
        cwd = args.workdir
    else:
        cwd = os.getcwd()

    log = _log_file().open("a", encoding="utf-8")
    log.write(f"# start {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    log.write(f"# cwd={cwd}\n")
    log.write(f"# dist={dist}\n")
    log.write(f"# cmd={' '.join(command)}\n")
    log.flush()

    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    _meta_file().write_text(
        "\n".join(
            [
                f"pid={proc.pid}",
                f"launcher={args.launcher}",
                f"nnodes={dist['NNODES']}",
                f"node_rank={dist['NODE_RANK']}",
                f"master={dist['MASTER_ADDR']}:{dist['MASTER_PORT']}",
                f"nproc={dist['NPROC_PER_NODE']}",
                f"cmd={' '.join(command)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"started pid={proc.pid} launcher={args.launcher} "
        f"rank={dist['NODE_RANK']}/{dist['NNODES']} "
        f"nproc={dist['NPROC_PER_NODE']} "
        f"log={_log_file()}"
    )
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    pid = is_running()
    if pid is None:
        print("not running")
        _pid_file().unlink(missing_ok=True)
        return 0
    os.killpg(pid, signal.SIGTERM)
    for _ in range(40):
        if is_running() is None:
            break
        time.sleep(0.25)
    if is_running() is not None:
        os.killpg(pid, signal.SIGKILL)
        time.sleep(0.2)
    _pid_file().unlink(missing_ok=True)
    print("stopped")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    pid = is_running()
    if pid is None:
        print("NOT READY")
        return 1
    meta = ""
    if _meta_file().is_file():
        meta = " " + " ".join(_meta_file().read_text(encoding="utf-8").splitlines()[:3])
    print(f"READY pid={pid}{meta}")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    deadline = time.time() + max(1, int(args.timeout))
    while time.time() < deadline:
        if is_running() is not None:
            print("ready")
            return 0
        time.sleep(0.5)
    print("timeout")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "wait"])
    parser.add_argument(
        "--launcher",
        default=os.environ.get("GPU_CONTROL_TRAIN_LAUNCHER", "llamafactory"),
        help="llamafactory|torchrun|deepspeed|accelerate|raw",
    )
    parser.add_argument("--config", help="LLaMA-Factory 训练 YAML")
    parser.add_argument("--deepspeed", help="DeepSpeed JSON 配置路径")
    parser.add_argument("--workdir", help="工作目录")
    parser.add_argument("--rdzv-id", default="", help="torchrun rdzv_id（可选）")
    parser.add_argument("--timeout", type=int, default=120, help="wait 超时秒")
    parser.add_argument(
        "rest",
        nargs=argparse.REMAINDER,
        help="-- 之后传给 launcher 的参数",
    )
    args = parser.parse_args()
    if args.rest and args.rest[0] == "--":
        args.rest = args.rest[1:]

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
