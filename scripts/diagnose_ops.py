#!/usr/bin/env python3
"""一键检测与安全自动修复（仅处理简单、可逆问题）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from paths import (  # noqa: E402
    INVENTORY_FILE,
    PROJECT_DIR,
    STATE_DIR,
    VENV_PYTHON,
    CLI,
    ensure_runtime_dirs,
    cluster_file,
    nodes_file,
)
from runlog_util import append_runlog  # noqa: E402


def _pid_alive(pid_file: Path) -> bool | None:
    """None=无文件；True=进程在；False=pid 文件残留但进程已死。"""
    if not pid_file.is_file():
        return None
    text = pid_file.read_text(encoding="utf-8").strip().splitlines()
    if not text or not text[0].isdigit():
        return False
    return (Path("/proc") / text[0]).exists()


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
        if _pid_alive(STATE_DIR / "ingress-edge.pid") is not True:
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
            fix="gpu-control jobs install shell-job",
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
