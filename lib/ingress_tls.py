"""统一入口 TLS / 域名配置解析与证书探测。

配置位于 ``cluster.gateway.ingress``：

- ``domain`` / ``domains``：对外域名（写入 public URL / nginx server_name）
- ``tls.enabled`` + ``cert_file`` / ``key_file`` / ``ca_file``：HTTPS 与可选客户端 CA
"""

from __future__ import annotations

import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import expand_path


def normalize_ingress(raw: dict | None, *, gateway: dict | None = None) -> dict[str, Any]:
    """规范化 ingress 配置（含 TLS / 域名）。

    :param raw: ``cluster.gateway.ingress`` 原始对象。
    :param gateway: 可选 ``cluster.gateway``，用于默认监听端口。
    """
    gateway = gateway or {}
    raw = raw if isinstance(raw, dict) else {}
    tls_raw = raw.get("tls") if isinstance(raw.get("tls"), dict) else {}
    domains: list[str] = []
    if raw.get("domain"):
        domains.append(str(raw["domain"]).strip())
    for item in raw.get("domains") or []:
        text = str(item).strip()
        if text and text not in domains:
            domains.append(text)
    cert = str(tls_raw.get("cert_file") or "").strip()
    key = str(tls_raw.get("key_file") or "").strip()
    ca = str(tls_raw.get("ca_file") or "").strip()
    return {
        "enabled": bool(raw.get("enabled", True)),
        "bind": str(raw.get("bind") or "0.0.0.0"),
        "port": int(raw.get("port") or gateway.get("nginx_listen") or 18080),
        "kind": str(raw.get("kind") or "edge"),
        # 前置代理模式：设置后 Edge 只转发到一个上游（例如 LiteLLM :4000），不再做节点 LB。
        "proxy_to": str(raw.get("proxy_to") or "").strip(),
        "public_base_url": str(raw.get("public_base_url") or "").rstrip("/"),
        "domain": domains[0] if domains else "",
        "domains": domains,
        "strategy": str(raw.get("strategy") or "round_robin").lower(),
        "retries": max(0, int(raw.get("retries") or 2)),
        "cooldown_sec": max(0, int(raw.get("cooldown_sec") or 30)),
        "connect_timeout_sec": float(raw.get("connect_timeout_sec") or 2),
        "read_timeout_sec": float(raw.get("read_timeout_sec") or 300),
        "sticky_header": str(raw.get("sticky_header") or "").strip(),
        "reload_interval_sec": float(raw.get("reload_interval_sec") or 5),
        "tls": {
            "enabled": bool(tls_raw.get("enabled", False)),
            "cert_file": expand_path(cert) if cert else "",
            "key_file": expand_path(key) if key else "",
            "ca_file": expand_path(ca) if ca else "",
            "require_client_cert": bool(tls_raw.get("require_client_cert", False)),
            "min_version": str(tls_raw.get("min_version") or "TLSv1.2"),
        },
    }


def probe_tls(tls: dict[str, Any]) -> dict[str, Any]:
    """探测证书文件是否就绪（不启动服务）。

    :param tls: 规范化后的 ``ingress.tls``。
    :returns: 含 ``ready`` / ``state`` / ``reasons`` / ``not_after`` 等字段。
    """
    enabled = bool(tls.get("enabled"))
    cert = Path(str(tls.get("cert_file") or ""))
    key = Path(str(tls.get("key_file") or ""))
    ca = Path(str(tls.get("ca_file") or "")) if tls.get("ca_file") else None
    reasons: list[str] = []
    if not enabled:
        return {
            "enabled": False,
            "ready": False,
            "state": "off",
            "label": "未启用",
            "ok": True,
            "reasons": [],
            "not_after": None,
            "cert_file": str(cert) if tls.get("cert_file") else None,
            "key_file": str(key) if tls.get("key_file") else None,
            "ca_file": str(ca) if ca else None,
        }
    if not cert.is_file():
        reasons.append(f"缺证书 {cert}")
    if not key.is_file():
        reasons.append(f"缺私钥 {key}")
    if tls.get("ca_file") and ca is not None and not ca.is_file():
        reasons.append(f"缺 CA {ca}")
    not_after = None
    if cert.is_file() and key.is_file():
        try:
            not_after = _cert_not_after(cert)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"证书不可读: {exc}")
    ready = enabled and not reasons
    if ready:
        state, label = "ready", "HTTPS 就绪"
    elif enabled:
        state, label = "blocked", "缺证书"
    else:
        state, label = "off", "未启用"
    return {
        "enabled": enabled,
        "ready": ready,
        "state": state,
        "label": label,
        "ok": (not enabled) or ready,
        "reasons": reasons,
        "not_after": not_after,
        "cert_file": str(cert) if tls.get("cert_file") else None,
        "key_file": str(key) if tls.get("key_file") else None,
        "ca_file": str(ca) if ca else None,
        "require_client_cert": bool(tls.get("require_client_cert")),
        "min_version": tls.get("min_version") or "TLSv1.2",
    }


def public_base_url(ingress: dict[str, Any], *, control_host: str = "", tls_ready: bool = False) -> str:
    """推导对外 URL：优先 ``public_base_url``；否则域名/主控 + http(s)。

    :param ingress: 规范化后的 ingress。
    :param control_host: 无域名时回落的主控主机。
    :param tls_ready: TLS 就绪则用 ``https``。
    """
    configured = str(ingress.get("public_base_url") or "").rstrip("/")
    if configured:
        return configured
    host = str(ingress.get("domain") or control_host or "<control>").strip()
    port = int(ingress.get("port") or 18080)
    scheme = "https" if tls_ready else "http"
    default_port = 443 if scheme == "https" else 80
    if port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def build_ssl_context(tls: dict[str, Any]) -> ssl.SSLContext:
    """为 Edge 构建服务端 ``SSLContext``。

    :param tls: 规范化后的 ``ingress.tls``。
    :returns: 已加载证书链的上下文。
    :raises FileNotFoundError: 证书未就绪。
    """
    probe = probe_tls(tls)
    if not probe["ready"]:
        raise FileNotFoundError("; ".join(probe["reasons"]) or "TLS 未就绪")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    min_ver = str(tls.get("min_version") or "TLSv1.2").upper().replace(".", "")
    if hasattr(ssl, "TLSVersion"):
        if "1_3" in min_ver or "13" in min_ver:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        else:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(probe["cert_file"]), str(probe["key_file"]))
    if probe.get("ca_file"):
        ctx.load_verify_locations(cafile=str(probe["ca_file"]))
        if tls.get("require_client_cert"):
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.verify_mode = ssl.CERT_OPTIONAL
    return ctx


def _cert_not_after(cert_path: Path) -> str | None:
    """返回证书过期时间（ISO8601）；无 openssl 则返回 ``None``。"""
    import subprocess

    try:
        out = subprocess.check_output(
            ["openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    # notAfter=Nov 14 12:00:00 2027 GMT
    if "=" not in out:
        return None
    raw = out.split("=", 1)[1].strip()
    try:
        dt = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return raw
