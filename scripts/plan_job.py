#!/usr/bin/env python3
"""规划一次多机任务：把多台小卡机组合成一个作业。

例如两台 4 卡机：``gpu-control plan-job --gpus 8 --job train-job`` 会给出
rank / MASTER_ADDR / NPROC_PER_NODE 和告警。这里只做只读规划，不启动任务。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from jobs_util import default_job_name, load_job_summaries
from paths import PROJECT_DIR, SCRIPTS_DIR, VENV_PYTHON, cluster_file, nodes_file


def _load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _node_gpus(meta: dict, name: str) -> int:
    nodes = meta.get("nodes") or {}
    node = nodes.get(name) or {}
    if node.get("gpus_per_node") not in (None, ""):
        return int(node["gpus_per_node"])
    defaults = meta.get("defaults") or {}
    if defaults.get("gpus_per_node") not in (None, ""):
        return int(defaults["gpus_per_node"])
    return 0


def _select(gpus: int, *, include_unverified: bool) -> dict:
    cmd = [
        str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable),
        str(SCRIPTS_DIR / "select_nodes.py"),
        "--gpus",
        str(gpus),
        "--json",
    ]
    if include_unverified:
        cmd.append("--include-unverified")
    result = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip()}


def build_plan(
    *,
    requested_gpus: int,
    job: dict | None,
    selected: list[str],
    nodes_meta: dict,
    master_port: int = 29500,
) -> dict:
    warnings: list[str] = []
    rows = []
    total = 0
    for rank, name in enumerate(selected):
        node = ((nodes_meta.get("nodes") or {}).get(name) or {})
        host = str(node.get("ansible_host") or node.get("host") or "")
        gpus = _node_gpus(nodes_meta, name)
        total += gpus
        if not host:
            warnings.append(f"{name} 缺少 ansible_host")
        if gpus <= 0:
            warnings.append(f"{name} 缺少 gpus_per_node，无法确定 NPROC_PER_NODE")
        rows.append({"rank": rank, "node": name, "host": host, "gpus": gpus})
    if not selected:
        warnings.append("没有选中节点；先刷新 resources 并确认节点在线、空闲、未隔离")
    if total < requested_gpus:
        warnings.append(f"选中节点只有 {total} 卡，少于请求的 {requested_gpus} 卡")
    if len({row["gpus"] for row in rows}) > 1:
        warnings.append("选中节点卡数不一致；多机训练通常要求每节点 NPROC_PER_NODE 一致")
    distributed = bool((job or {}).get("distributed"))
    if job and not distributed:
        warnings.append(f"Job {job.get('name')} 不是 distributed=true，不会注入多机环境")
    if len(selected) > 1 and not (job or {}).get("distributed"):
        warnings.append("多节点任务需要 distributed=true 的 Job 模板")
    master = rows[0]["host"] if rows else ""
    job_gpus = int((job or {}).get("gpus_per_node") or 0)
    counts = {row["gpus"] for row in rows if row["gpus"] > 0}
    nproc = rows[0]["gpus"] if len(counts) == 1 and rows else job_gpus
    if job_gpus and any(row["gpus"] != job_gpus for row in rows):
        warnings.append(
            f"Job 要求每节点 {job_gpus} 卡，选中节点实际为 {sorted({row['gpus'] for row in rows})} 卡；"
            "请把 Job 的 gpus_per_node 改成每节点实际卡数"
        )
    job_matches = not job_gpus or all(row["gpus"] == job_gpus for row in rows)
    return {
        "ok": bool(selected) and total >= requested_gpus and (len(selected) == 1 or distributed) and job_matches,
        "requested_gpus": requested_gpus,
        "selected_gpus": total,
        "nodes": rows,
        "job": {
            "name": (job or {}).get("name"),
            "distributed": distributed,
            "gpus_per_node": (job or {}).get("gpus_per_node"),
        },
        "distributed": {
            "formula": f"{len(selected)} 节点 × {nproc} 进程 = {len(selected) * nproc} 卡",
            "master_addr": master,
            "master_port": master_port,
            "node_rank": {row["node"]: row["rank"] for row in rows},
            "nproc_per_node": nproc,
            "hostfile": [f"{row['host']} slots={row['gpus']}" for row in rows if row["host"]],
        },
        "warnings": warnings,
        "note": "这是作业级多机协同，不是把多台机器的显存合并成一张虚拟 GPU；通信由 NCCL / DeepSpeed / torchrun 等完成。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpus", type=int, required=True)
    parser.add_argument("--job", default="", help="Job 名；默认读 cluster.default_job")
    parser.add_argument("--nodes", default="", help="逗号分隔的节点名；省略则自动选空闲节点")
    parser.add_argument("--include-unverified", action="store_true", help="自动选节点时包含离线 / 过期节点")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    nodes_meta = _load_json(nodes_file(), {"defaults": {}, "nodes": {}})
    name = args.job.strip() or default_job_name()
    jobs = {row.get("name"): row for row in load_job_summaries()}
    job = jobs.get(name) if name else None
    if not job:
        print(f"未找到 Job：{name or '(none)'}；先 gpu-control jobs install --recommended", file=sys.stderr)
        return 2

    if args.nodes.strip():
        selected = [x.strip() for x in args.nodes.split(",") if x.strip()]
        missing = [x for x in selected if x not in (nodes_meta.get("nodes") or {})]
        if missing:
            print(f"节点不在 nodes.json：{', '.join(missing)}", file=sys.stderr)
            return 2
    else:
        picked = _select(args.gpus, include_unverified=args.include_unverified)
        if not picked.get("ok"):
            print(picked.get("error") or "自动选节点失败", file=sys.stderr)
            return 1
        selected = list(picked.get("selected") or [])

    cluster = _load_json(cluster_file(), {})
    master_port = int(((job or {}).get("master_port") or 29500))
    payload = build_plan(
        requested_gpus=args.gpus,
        job=job,
        selected=selected,
        nodes_meta=nodes_meta,
        master_port=master_port,
    )
    payload["cluster"] = cluster.get("name")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"plan: {payload['selected_gpus']}/{args.gpus} GPU · {payload['job']['name']} · "
            f"{payload['distributed']['formula']}"
        )
        for row in payload["nodes"]:
            print(f"  rank {row['rank']}: {row['node']} ({row['host'] or '?'}) · {row['gpus']} GPU")
        print(
            f"  MASTER_ADDR={payload['distributed']['master_addr']}:{payload['distributed']['master_port']} "
            f"NPROC_PER_NODE={payload['distributed']['nproc_per_node']}"
        )
        for warning in payload["warnings"]:
            print(f"  ! {warning}")
        print(f"  {payload['note']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
