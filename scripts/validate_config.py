#!/usr/bin/env python3
"""校验集群配置是否可发布（``gpu-control doctor``）。"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import (
    ANSIBLE,
    CONFIG_DIR,
    INVENTORY_FILE,
    LOCAL_DIR,
    PROJECT_DIR,
    STATE_DIR,
    cluster_file,
    expand_path,
    list_job_files,
    nodes_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    issues: list[str] = []
    notes: list[str] = []

    cpath = cluster_file()
    npath = nodes_file()
    if not cpath.is_file():
        issues.append(f"missing cluster config (expected {LOCAL_DIR / 'cluster.json'})")
    if not npath.is_file():
        issues.append(f"missing nodes config (expected {LOCAL_DIR / 'nodes.json'})")

    cluster = {}
    nodes = {}
    if cpath.is_file():
        try:
            cluster = json.loads(cpath.read_text(encoding="utf-8"))
            notes.append(f"cluster: {cpath.relative_to(PROJECT_DIR)}")
        except json.JSONDecodeError as exc:
            issues.append(f"invalid cluster json: {exc}")
    if npath.is_file():
        try:
            nodes = json.loads(npath.read_text(encoding="utf-8"))
            notes.append(f"nodes: {npath.relative_to(PROJECT_DIR)}")
        except json.JSONDecodeError as exc:
            issues.append(f"invalid nodes json: {exc}")

    if cluster:
        for key in ("name", "strategy", "default_job", "api", "scheduling"):
            if key not in cluster:
                issues.append(f"cluster.json missing field: {key}")
        api = cluster.get("api") or {}
        key_file = api.get("api_key_file")
        if key_file:
            key_file = expand_path(key_file)
            if not Path(key_file).is_file():
                issues.append(f"api_key_file not found: {key_file}")
            else:
                notes.append("api_key_file ok")

    node_map = (nodes.get("nodes") or {}) if nodes else {}
    if not node_map:
        issues.append("nodes.json has no nodes")
    else:
        for name, meta in node_map.items():
            if not meta.get("ansible_host"):
                issues.append(f"node {name} missing ansible_host")

    if INVENTORY_FILE.is_file() and node_map:
        text = INVENTORY_FILE.read_text(encoding="utf-8")
        for name in node_map:
            if name not in text:
                issues.append(f"inventory.ini missing host {name} (run: gpu-control node render)")
    elif node_map:
        issues.append("inventory.ini missing (run: gpu-control node render)")

    defaults = (nodes.get("defaults") or {}) if nodes else {}
    ssh_key = defaults.get("ansible_ssh_private_key_file")
    if ssh_key:
        ssh_key = expand_path(ssh_key)
        if not Path(ssh_key).is_file():
            issues.append(f"ssh key missing: {ssh_key}")
        else:
            notes.append("ssh key ok")

    if not ANSIBLE.is_file():
        issues.append("venv ansible-playbook missing (pip install -r requirements.txt)")
    else:
        notes.append("ansible ok")

    jobs = list_job_files()
    if not jobs:
        issues.append(
            "no jobs registered（gpu-control jobs install --recommended）"
        )
    else:
        notes.append(f"jobs: {', '.join(p.stem for p in jobs)}")
        from jobs_util import validate_registered_jobs

        for problem in validate_registered_jobs():
            issues.append(f"job schema: {problem}")

    if (CONFIG_DIR / "examples").is_dir():
        notes.append("config/examples present")
    if STATE_DIR.is_dir():
        notes.append("state/ present")

    paths = (defaults.get("paths") or {}) if defaults else {}
    if paths:
        notes.append(f"defaults.paths: {', '.join(sorted(paths))}")
    else:
        notes.append("defaults.paths unset（Job/models 若用 ${paths.*} 需配置）")

    models_path = LOCAL_DIR / "models.json"
    if models_path.is_file():
        notes.append("models.json present（可选模块）")
    handbook = PROJECT_DIR / "docs" / "手册.md"
    if handbook.is_file():
        notes.append("docs/手册.md present")

    from auth_util import doctor_auth_notes

    auth_issues, auth_notes = doctor_auth_notes(cluster if cluster else None)
    issues.extend(auth_issues)
    notes.extend(auth_notes)
    if cluster:
        auth_cfg = (cluster.get("control") or {}).get("auth") or {}
        auth_mode = str(auth_cfg.get("mode") or "none").lower()
        token_file = Path(
            expand_path(str(auth_cfg.get("api_token_file") or LOCAL_DIR / "secrets" / "ui_api_token"))
        )
        if auth_mode not in {"none", "off", "disabled"} and not token_file.is_file():
            notes.append("API Token 未配置：插件/脚本无法自动登录；需要时执行 gpu-control auth issue-token")

    if cluster:
        from ingress_tls import normalize_ingress, probe_tls
        from nginx_ops import normalize_settings, status_for

        gateway = cluster.get("gateway") or {}
        ingress = normalize_ingress(gateway.get("ingress") or {}, gateway=gateway)
        tls = probe_tls(ingress.get("tls") or {})
        if tls.get("enabled") and not tls.get("ready"):
            issues.append("TLS 已启用但未就绪：" + "；".join(tls.get("reasons") or ["证书/私钥缺失"]))
        elif tls.get("enabled"):
            notes.append(f"TLS 就绪（到期 {tls.get('not_after') or '未知'}，最低 {tls.get('min_version')}）")
            not_after = tls.get("not_after")
            if not_after:
                try:
                    expires = datetime.strptime(not_after, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    if expires - datetime.now(timezone.utc) < timedelta(days=30):
                        issues.append(f"TLS 证书将在 {not_after} 过期（不足 30 天）")
                except ValueError:
                    pass

        settings = normalize_settings(gateway)
        kind = str(ingress.get("kind") or "edge").lower()
        if kind == "nginx" and not shutil.which("nginx"):
            issues.append("ingress.kind=nginx 但本机未安装 nginx")
        apply_path = str(settings.get("apply_path") or "").strip()
        if apply_path:
            apply_path = str(Path(expand_path(apply_path)))
            artifact = STATE_DIR / "gateway" / "nginx-inference-pool.conf"
            if not artifact.is_file():
                issues.append("nginx apply_path 已配置但尚未生成片段（gpu-control gateway render）")
            else:
                try:
                    current = status_for(apply_path, artifact.read_text(encoding="utf-8"))
                    if not current.get("in_sync"):
                        issues.append("nginx 配置与最新渲染不一致（gpu-control gateway nginx --apply）")
                    else:
                        notes.append("nginx 配置已同步")
                except OSError as exc:
                    issues.append(f"nginx 配置不可读：{exc}")
            if settings.get("auto_sync"):
                pid_path = STATE_DIR / "ingress-nginx-sync.pid"
                if not pid_path.is_file():
                    issues.append("nginx auto_sync 已开启但跟随进程未运行（gateway nginx --watch --background）")
                else:
                    notes.append("nginx 自动跟随已配置")

    payload = {
        "ok": not issues,
        "issues": issues,
        "notes": notes,
        "nodes": sorted(node_map.keys()),
        "strategy": cluster.get("strategy"),
        "project": str(PROJECT_DIR),
        "config_local": str(LOCAL_DIR),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"doctor: {'OK' if payload['ok'] else 'ISSUES'}")
        for item in issues:
            print(f"  ! {item}")
        for item in notes:
            print(f"  · {item}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
