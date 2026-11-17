#!/usr/bin/env python3
"""一键检测与安全自动修复（仅处理简单、可逆问题）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from paths import (  # noqa: E402
    INVENTORY_FILE,
    PROJECT_DIR,
    RESOURCES_FILE,
    STATE_DIR,
    VENV_PYTHON,
    CLI,
    ensure_runtime_dirs,
    cluster_file,
    nodes_file,
)
from process_util import pid_alive  # noqa: E402
from runlog_util import append_runlog  # noqa: E402
from state_util import read_json  # noqa: E402


def _pid_alive(pid_file: Path) -> bool | None:
    """None=无文件；True=进程在；False=pid 文件残留但进程已死。"""
    if not pid_file.is_file():
        return None
    text = pid_file.read_text(encoding="utf-8").strip().splitlines()
    if not text or not text[0].isdigit():
        return False
    return pid_alive(int(text[0]))


def collect_findings() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(
        fid: str,
        *,
        severity: str,
        title: str,
        detail: str,
        fix: str | None = None,
        auto: bool = False,
    ) -> None:
        findings.append(
            {
                "id": fid,
                "severity": severity,
                "title": title,
                "detail": detail,
                "fix": fix,
                "auto": auto,
            }
        )

    ensure_runtime_dirs()

    if not cluster_file().is_file():
        add(
            "missing-cluster",
            severity="error",
            title="缺少 cluster.json",
            detail=f"期望 {cluster_file()}",
            fix="复制 config/examples/cluster.example.json → config/local/cluster.json",
        )
    if not nodes_file().is_file():
        add(
            "missing-nodes",
            severity="error",
            title="缺少 nodes.json",
            detail=f"期望 {nodes_file()}",
            fix="复制 config/examples/nodes.example.json → config/local/nodes.json",
        )

    node_map = {}
    if nodes_file().is_file():
        node_map = (json.loads(nodes_file().read_text(encoding="utf-8")).get("nodes") or {})

    if node_map and not INVENTORY_FILE.is_file():
        add(
            "missing-inventory",
            severity="error",
            title="缺少 inventory.ini",
            detail="节点已配置但未渲染 inventory",
            fix="gpu-control node render",
            auto=True,
        )
    elif node_map and INVENTORY_FILE.is_file():
        text = INVENTORY_FILE.read_text(encoding="utf-8")
        missing = [n for n in node_map if n not in text]
        if missing:
            add(
                "stale-inventory",
                severity="warn",
                title="inventory 与 nodes 不一致",
                detail="缺失: " + ", ".join(missing),
                fix="gpu-control node render",
                auto=True,
            )

    endpoints = STATE_DIR / "endpoints.json"
    if node_map and not endpoints.is_file():
        add(
            "missing-endpoints",
            severity="warn",
            title="缺少 endpoints 目录缓存",
            detail=str(endpoints),
            fix="gpu-control endpoints",
            auto=True,
        )

    for label, name in (("ui", "ui.pid"), ("edge", "ingress-edge.pid"), ("watchdog", "watchdog.pid")):
        alive = _pid_alive(STATE_DIR / name)
        if alive is False:
            add(
                f"stale-{label}-pid",
                severity="warn",
                title=f"{label} pid 文件残留",
                detail=str(STATE_DIR / name),
                fix=f"删除 {name}",
                auto=True,
            )

    resources = PROJECT_DIR / "resources.json"
    if node_map and (not resources.is_file() or resources.stat().st_size < 8):
        add(
            "missing-resources",
            severity="info",
            title="尚无 GPU 资源快照",
            detail="可执行 gpu-control gpu refresh",
            fix="gpu-control gpu refresh",
            auto=False,
        )

    cluster = {}
    if cluster_file().is_file():
        cluster = json.loads(cluster_file().read_text(encoding="utf-8"))
    auth = ((cluster.get("control") or {}).get("auth") or {})
    mode = str(auth.get("mode") or "none").lower()
    if mode in {"", "none", "off"}:
        add(
            "auth-open",
            severity="warn",
            title="控制面鉴权未开启",
            detail="建议 password + Bearer；仅 SSH 隧道时可暂缓",
            fix="gpu-control auth set-password --generate --enable",
            auto=False,
        )

    ingress = (cluster.get("gateway") or {}).get("ingress") or {}
    ingress_kind = str(ingress.get("kind") or "edge").lower()
    if bool(ingress.get("enabled", True)) and ingress_kind in {"edge", "both", "all"}:
        edge_up = _pid_alive(STATE_DIR / "ingress-edge.pid") is True
        if not edge_up:
            # 与 gateway status 共用身份探测：容器 / SSH 隧道下 pidfile 可能不可见。
            try:
                from gateway_ops import _edge_runtime, _ingress_cfg

                gateway = cluster.get("gateway") or {}
                edge_up = _edge_runtime(_ingress_cfg(gateway))[0]
            except (ImportError, OSError, ValueError):
                edge_up = False
        if not edge_up:
            add(
                "edge-down",
                severity="error",
                title="统一入口 Edge 未运行",
                detail=f"ingress.kind={ingress_kind} port={ingress.get('port') or 18080}",
                fix="gpu-control gateway ensure",
                auto=True,
            )

    default_job = (cluster.get("default_job") or "").strip()
    from jobs_util import registered_job_names

    names = registered_job_names()
    if default_job and default_job not in names:
        add(
            "default-job-missing",
            severity="warn",
            title=f"default_job={default_job} 未注册",
            detail="请安装模板或改 cluster.default_job",
            fix=f"gpu-control jobs install shell-job --name {default_job}",
            auto=False,
        )
    if not names:
        add(
            "no-jobs",
            severity="info",
            title="尚未注册任何 Job",
            detail="框架仅内置 shell-job；其余从模板安装",
            fix="gpu-control jobs install --recommended",
            auto=False,
        )
    else:
        from jobs_util import validate_registered_jobs

        for problem in validate_registered_jobs():
            add(
                f"job-schema:{problem.split(':', 1)[0]}",
                severity="error",
                title="Job 契约校验失败",
                detail=problem,
                fix="对照 jobs/README.md 补全字段后重装或编辑 JSON",
                auto=False,
            )

    # ---- 节点存活 / 快照新鲜度 -------------------------------------------
    try:
        import liveness

        persisted = liveness.read_snapshot(STATE_DIR / "liveness.json")
        live = liveness.cluster_view(
            node_names=list(node_map),
            resources=read_json(RESOURCES_FILE, {}),
            ssh_quality=read_json(STATE_DIR / "ssh_quality.json", {}),
            host_metrics=read_json(STATE_DIR / "host_metrics.json", {}),
            probe=read_json(STATE_DIR / "probe.json", {}),
            endpoint_health=read_json(STATE_DIR / "endpoint_health.json", {}),
            persisted=persisted,
            cluster=cluster,
        )
        for row in liveness.offline_notices(live):
            add(
                row["id"],
                severity="error",
                title=row["title"],
                detail=row["detail"],
                fix=row.get("fix"),
                auto=False,
            )
        for row in liveness.stale_notices(live):
            add(
                row["id"],
                severity="warn",
                title=row["title"],
                detail=row["detail"],
                fix=row.get("fix"),
                auto=False,
            )
        generated = liveness.parse_ts(persisted.get("generated_at"))
        ttls = liveness.load_ttls(cluster)
        if generated is None or time.time() - generated > float(ttls["probe_ttl_sec"]):
            add(
                "liveness-stale",
                severity="warn",
                title="节点存活探测已过期",
                detail="后台探活未运行或 liveness.json 过期；总览会回落到旧健康文件。",
                fix="gpu-control liveness check",
                auto=True,
            )
    except Exception as exc:  # noqa: BLE001 — 诊断不能因存活模块失败而中断
        add(
            "liveness-error",
            severity="warn",
            title="节点存活检测失败",
            detail=str(exc),
            fix="gpu-control liveness check",
            auto=False,
        )

    leases = read_json(STATE_DIR / "leases.json", {"leases": []}).get("leases") or []
    resources = read_json(RESOURCES_FILE, {})
    if leases and isinstance(resources, dict) and resources:
        for lease in leases:
            lease_id = str(lease.get("id") or "")
            job = str(lease.get("job") or "")
            for node in lease.get("nodes") or []:
                snap = resources.get(node)
                if not isinstance(snap, dict) or "gpus" not in snap:
                    continue
                gpus = snap.get("gpus") or []
                if not gpus:
                    continue
                if all(str(g.get("status") or "") == "free" for g in gpus):
                    add(
                        f"stale-lease:{lease_id or node}",
                        severity="warn",
                        title=f"租约可能漂移：{node}",
                        detail=(
                            f"lease={lease_id or '-'} job={job or '-'} "
                            f"但 resources 显示该节点 GPU 全空闲"
                            "（进程崩溃 / procs kill 不会自动释租约）"
                        ),
                        fix=(
                            f"gpu-control lease release --id {lease_id}"
                            if lease_id
                            else f"gpu-control lease release --nodes {node}"
                        ),
                        auto=False,
                    )

    return findings


def apply_fix(finding_id: str) -> dict[str, Any]:
    """执行单个安全修复。"""
    ensure_runtime_dirs()
    if finding_id in {"missing-inventory", "stale-inventory"}:
        completed = subprocess.run(
            [str(VENV_PYTHON), str(CLI), "node", "render"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        return {
            "id": finding_id,
            "ok": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    if finding_id == "missing-endpoints":
        completed = subprocess.run(
            [str(VENV_PYTHON), str(CLI), "endpoints"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        return {
            "id": finding_id,
            "ok": completed.returncode == 0,
            "stdout": completed.stdout[-500:],
            "stderr": completed.stderr[-300:],
        }
    if finding_id.startswith("stale-") and finding_id.endswith("-pid"):
        name = {
            "stale-ui-pid": "ui.pid",
            "stale-edge-pid": "ingress-edge.pid",
            "stale-watchdog-pid": "watchdog.pid",
        }.get(finding_id)
        if not name:
            return {"id": finding_id, "ok": False, "error": "unknown pid fix"}
        path = STATE_DIR / name
        if path.is_file():
            path.unlink()
        return {"id": finding_id, "ok": True, "removed": str(path)}
    if finding_id == "liveness-stale":
        completed = subprocess.run(
            [str(VENV_PYTHON), str(ROOT / "scripts" / "liveness_probe.py"), "--json"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        return {
            "id": finding_id,
            "ok": completed.returncode == 0,
            "stdout": completed.stdout[-500:],
            "stderr": completed.stderr[-300:],
        }
    if finding_id == "edge-down":
        completed = subprocess.run(
            [str(VENV_PYTHON), str(CLI), "gateway", "ensure"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        return {
            "id": finding_id,
            "ok": completed.returncode == 0,
            "stdout": completed.stdout[-800:],
            "stderr": completed.stderr[-300:],
        }
    return {"id": finding_id, "ok": False, "error": "该问题不支持自动修复"}


def cmd_check(_: argparse.Namespace) -> int:
    findings = collect_findings()
    errors = sum(1 for f in findings if f["severity"] == "error")
    payload = {
        "ok": errors == 0,
        "findings": findings,
        "counts": {
            "error": errors,
            "warn": sum(1 for f in findings if f["severity"] == "warn"),
            "info": sum(1 for f in findings if f["severity"] == "info"),
            "auto": sum(1 for f in findings if f.get("auto")),
        },
    }
    append_runlog(
        "diagnose",
        ok=payload["ok"],
        summary=f"findings={len(findings)} errors={errors}",
        detail={"counts": payload["counts"]},
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def cmd_repair(args: argparse.Namespace) -> int:
    findings = collect_findings()
    targets = [f for f in findings if f.get("auto")]
    if args.id:
        wanted = set(args.id)
        targets = [f for f in findings if f["id"] in wanted]
    results = []
    for f in targets:
        results.append(apply_fix(f["id"]))
    after = collect_findings()
    payload = {
        "ok": all(r.get("ok") for r in results) if results else True,
        "repaired": results,
        "remaining": after,
        "counts": {
            "attempted": len(results),
            "remaining": len(after),
        },
    }
    append_runlog(
        "repair",
        ok=payload["ok"],
        summary=f"repaired={len(results)} remaining={len(after)}",
        detail={"repaired_ids": [r.get("id") for r in results]},
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_check = sub.add_parser("check", help="检测常见问题")
    p_check.set_defaults(func=cmd_check)
    p_repair = sub.add_parser("repair", help="自动修复可安全处理的问题")
    p_repair.add_argument("--id", action="append", default=[], help="只修指定 finding id")
    p_repair.set_defaults(func=cmd_repair)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
