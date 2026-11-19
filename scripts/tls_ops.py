#!/usr/bin/env python3
"""控制面 TLS 证书：生成自签证书、查看到期时间、做就绪检查。

只使用标准 TLS（RSA 3072 / SHA-256），不自造加密协议。自签证书适合内网
和验收；生产优先用公司 CA 或 ACME（Let's Encrypt 等）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import LOCAL_DIR, cluster_file
from ingress_tls import normalize_ingress, probe_tls


def _load_cluster() -> dict:
    path = cluster_file()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cluster(doc: dict) -> Path:
    path = cluster_file(write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _san_entries(domain: str, host: str = "") -> list[str]:
    values = ["DNS:localhost", "IP:127.0.0.1"]
    if domain and domain not in values:
        values.append(f"DNS:{domain}")
    if host and host not in {domain, "127.0.0.1"}:
        try:
            import ipaddress

            ipaddress.ip_address(host)
            values.append(f"IP:{host}")
        except ValueError:
            values.append(f"DNS:{host}")
    return list(dict.fromkeys(values))


def _cert_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    cert = Path(args.cert).expanduser() if args.cert else LOCAL_DIR / "secrets" / "ingress.crt"
    key = Path(args.key).expanduser() if args.key else LOCAL_DIR / "secrets" / "ingress.key"
    return cert, key


def cmd_init(args: argparse.Namespace) -> int:
    if not shutil.which("openssl"):
        print("openssl 未安装；先安装 openssl，或手工放入证书并设置 tls.cert_file/key_file", file=sys.stderr)
        return 2
    cluster = _load_cluster()
    control = cluster.get("control") or {}
    gateway = cluster.get("gateway") or {}
    ingress = normalize_ingress(gateway.get("ingress") or {}, gateway=gateway)
    domain = str(args.domain or ingress.get("domain") or control.get("host") or "localhost").strip()
    host = str(control.get("host") or "").strip()
    cert_path, key_path = _cert_paths(args)
    if (cert_path.exists() or key_path.exists()) and not args.force:
        print(f"证书已存在：{cert_path} / {key_path}；要覆盖请加 --force", file=sys.stderr)
        return 2
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cert_path.parent.chmod(0o700)
    except OSError:
        pass
    san = ",".join(_san_entries(domain, host))
    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-sha256",
        "-days",
        str(max(1, int(args.days))),
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-subj",
        f"/CN={domain}",
        "-addext",
        f"subjectAltName={san}",
        "-addext",
        "keyUsage=critical,digitalSignature,keyEncipherment",
        "-addext",
        "extendedKeyUsage=serverAuth",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"openssl 失败：{proc.stderr.strip() or proc.stdout.strip()}", file=sys.stderr)
        return 2
    try:
        key_path.chmod(0o600)
        cert_path.chmod(0o644)
    except OSError:
        pass

    doc = _load_cluster()
    control = doc.setdefault("control", {})
    gateway = doc.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        gateway = {}
        doc["gateway"] = gateway
    raw_ingress = gateway.get("ingress") if isinstance(gateway.get("ingress"), dict) else {}
    raw_ingress = dict(raw_ingress)
    if domain and not raw_ingress.get("domain"):
        raw_ingress["domain"] = domain
    tls = dict(raw_ingress.get("tls") or {})
    tls.update(
        {
            "enabled": True,
            "cert_file": str(cert_path),
            "key_file": str(key_path),
            "min_version": str(tls.get("min_version") or "TLSv1.2"),
        }
    )
    raw_ingress["tls"] = tls
    gateway["ingress"] = raw_ingress
    path = _write_cluster(doc)
    probe = probe_tls(normalize_ingress(raw_ingress, gateway=gateway).get("tls") or {})
    print(
        json.dumps(
            {
                "ok": bool(probe.get("ready")),
                "domain": domain,
                "cert": str(cert_path),
                "key": str(key_path),
                "not_after": probe.get("not_after"),
                "config": str(path),
                "next": "gpu-control gateway render && gpu-control gateway start --kind edge（或按 ingress 配置重启入口）",
                "note": "自签证书需要客户端信任；生产请换公司 CA / ACME 证书。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if probe.get("ready") else 1


def _tls_status() -> dict:
    cluster = _load_cluster()
    gateway = cluster.get("gateway") or {}
    ingress = normalize_ingress(gateway.get("ingress") or {}, gateway=gateway)
    probe = probe_tls(ingress.get("tls") or {})
    return {"ingress": ingress, "tls": probe}


def cmd_show(_: argparse.Namespace) -> int:
    status = _tls_status()
    print(json.dumps(status["tls"], ensure_ascii=False, indent=2))
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    status = _tls_status()
    tls = status["tls"]
    problems: list[str] = []
    if tls.get("enabled") and not tls.get("ready"):
        problems.extend(tls.get("reasons") or ["TLS 未就绪"])
    not_after = tls.get("not_after")
    if not_after:
        try:
            expires = datetime.strptime(not_after, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if expires - datetime.now(timezone.utc) < timedelta(days=30):
                problems.append(f"证书将在 {not_after} 过期（不足 30 天）")
        except ValueError:
            pass
    payload = {"ok": not problems, "tls": tls, "problems": problems}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_init = sub.add_parser("init", help="生成自签证书并写入 ingress.tls")
    p_init.add_argument("--domain", default="", help="证书域名；默认读 ingress.domain / control.host")
    p_init.add_argument("--days", type=int, default=825, help="有效期天数")
    p_init.add_argument("--cert", default="", help="证书输出路径")
    p_init.add_argument("--key", default="", help="私钥输出路径")
    p_init.add_argument("--force", action="store_true", help="覆盖已有证书")
    p_init.set_defaults(func=cmd_init)

    p_show = sub.add_parser("show", help="查看证书状态与到期时间")
    p_show.set_defaults(func=cmd_show)

    p_check = sub.add_parser("check", help="检查证书是否就绪 / 临近过期")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
