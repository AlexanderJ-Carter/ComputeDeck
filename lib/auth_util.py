"""控制面鉴权（可插拔）。

模式
----
``none``
    开放访问（仅适合 SSH 隧道）。doctor 会告警。
``password``
    单管理员密码（scrypt）+ 签名 HttpOnly Session Cookie；
    可选长期 API Bearer Token（MCP / 脚本）。
``oidc``
    OpenID Connect 授权码 + PKCE；浏览器走 IdP，API 仍可用 Bearer。
    可选 ``oidc.allow_password_fallback`` 同时保留密码登录。

密钥目录：``config/local/secrets/``（gitignore）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from paths import LOCAL_DIR, PROJECT_DIR, cluster_file, expand_path

COOKIE_NAME = "gc_session"
AUTH_PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/oidc/login",
    "/api/auth/oidc/callback",
})
# 目录 / OpenAPI / 文档页保持可达，便于 Agent 发现鉴权要求。
AUTH_DISCOVERY_PATHS = frozenset({
    "/api",
    "/api/",
    "/api/openapi.json",
    "/api/docs",
    "/docs",
    "/help",
    "/help/",
})

DEFAULT_TTL_HOURS = 168
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


@dataclass(frozen=True)
class AuthPrincipal:
    subject: str
    method: str  # session | bearer | none
    mode: str


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    session_ttl_hours: int
    password_hash_file: Path
    session_secret_file: Path
    api_token_file: Path
    oidc: dict[str, Any]

    @property
    def enabled(self) -> bool:
        """鉴权是否启用（非 none/off/disabled）。"""
        return self.mode not in {"", "none", "off", "disabled"}


def _secrets_dir() -> Path:
    path = LOCAL_DIR / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_path(name: str) -> Path:
    return _secrets_dir() / name


def load_auth_config(cluster: dict | None = None) -> AuthConfig:
    """从 ``cluster.control.auth``（或环境变量）加载鉴权配置。"""
    if cluster is None:
        path = cluster_file()
        cluster = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    control = cluster.get("control") or {}
    raw = control.get("auth") if isinstance(control.get("auth"), dict) else {}
    mode = str(raw.get("mode") or os.environ.get("GPU_CONTROL_AUTH_MODE") or "none").strip().lower()
    ttl = int(raw.get("session_ttl_hours") or DEFAULT_TTL_HOURS)
    if ttl < 1:
        ttl = 1

    def resolve(key: str, default_name: str) -> Path:
        value = raw.get(key)
        if value:
            return Path(expand_path(str(value)))
        return _default_path(default_name)

    return AuthConfig(
        mode=mode,
        session_ttl_hours=ttl,
        password_hash_file=resolve("password_hash_file", "ui_password.hash"),
        session_secret_file=resolve("session_secret_file", "ui_session_secret"),
        api_token_file=resolve("api_token_file", "ui_api_token"),
        oidc=dict(raw.get("oidc") or {}),
    )


def save_auth_mode(mode: str) -> Path:
    """将 ``control.auth.mode`` 写入 local ``cluster.json``。

    :param mode: ``none`` / ``password`` / ``oidc``。
    :returns: 写入的配置文件路径。
    :raises ValueError: mode 非法。
    """
    mode = mode.strip().lower()
    if mode not in {"none", "password", "oidc"}:
        raise ValueError("mode must be none|password|oidc")
    path = cluster_file(write=True)
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    control = dict(doc.get("control") or {})
    auth = dict(control.get("auth") or {})
    auth["mode"] = mode
    auth.setdefault("session_ttl_hours", DEFAULT_TTL_HOURS)
    control["auth"] = auth
    doc["control"] = control
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def hash_password(password: str) -> str:
    """用 scrypt 生成可存储的密码哈希串。

    :raises ValueError: 密码为空或短于 8 字符。
    """
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        salt.hex(),
        digest.hex(),
    )


def verify_password(password: str, encoded: str) -> bool:
    """校验明文密码与 scrypt 哈希串。"""
    try:
        algo, n_s, r_s, p_s, salt_hex, digest_hex = encoded.strip().split("$", 5)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def set_password(password: str, *, cfg: AuthConfig | None = None) -> Path:
    """写入密码哈希文件（权限 ``0600``）。"""
    cfg = cfg or load_auth_config()
    encoded = hash_password(password)
    path = cfg.password_hash_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def password_configured(cfg: AuthConfig | None = None) -> bool:
    """是否已配置非空密码哈希文件。"""
    cfg = cfg or load_auth_config()
    return cfg.password_hash_file.is_file() and bool(cfg.password_hash_file.read_text(encoding="utf-8").strip())


def ensure_session_secret(cfg: AuthConfig | None = None) -> bytes:
    """读取或自动创建 Session HMAC 密钥。"""
    cfg = cfg or load_auth_config()
    path = cfg.session_secret_file
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return token.encode("utf-8")


def issue_session(
    *,
    subject: str = "admin",
    cfg: AuthConfig | None = None,
) -> str:
    """签发签名 Session Cookie 值（``body.sig``）。"""
    cfg = cfg or load_auth_config()
    secret = ensure_session_secret(cfg)
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + int(cfg.session_ttl_hours * 3600),
        "v": 1,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session(token: str, *, cfg: AuthConfig | None = None) -> AuthPrincipal | None:
    """校验 Session；无效或过期返回 ``None``。"""
    cfg = cfg or load_auth_config()
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    if not body or not sig:
        return None
    secret = ensure_session_secret(cfg)
    expect = _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (json.JSONDecodeError, ValueError):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    subject = str(payload.get("sub") or "admin")
    return AuthPrincipal(subject=subject, method="session", mode=cfg.mode)


def issue_api_token(*, cfg: AuthConfig | None = None) -> tuple[str, Path]:
    """创建长期 API Bearer Token 文件（明文仅返回一次）。

    :returns: ``(token, 文件路径)``。
    """
    cfg = cfg or load_auth_config()
    token = secrets.token_urlsafe(32)
    path = cfg.api_token_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return token, path


def verify_api_token(token: str, *, cfg: AuthConfig | None = None) -> bool:
    """常量时间比较 Bearer Token 与磁盘存储。"""
    cfg = cfg or load_auth_config()
    if not token or not cfg.api_token_file.is_file():
        return False
    stored = cfg.api_token_file.read_text(encoding="utf-8").strip()
    if not stored:
        return False
    return hmac.compare_digest(stored, token)


def parse_cookies(header: str | None) -> dict[str, str]:
    """解析 ``Cookie`` 请求头为名称→值映射。"""
    if not header:
        return {}
    jar = SimpleCookie()
    try:
        jar.load(header)
    except Exception:  # noqa: BLE001
        return {}
    return {k: morsel.value for k, morsel in jar.items()}


def session_cookie_header(token: str, *, ttl_hours: int, clear: bool = False) -> str:
    """构造 ``Set-Cookie`` 头（HttpOnly / SameSite=Strict）。"""
    max_age = 0 if clear else max(60, int(ttl_hours * 3600))
    parts = [
        f"{COOKIE_NAME}={'' if clear else token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Strict",
        f"Max-Age={max_age}",
    ]
    # Secure only when explicitly requested (TLS terminator / HTTPS UI).
    if os.environ.get("GPU_CONTROL_UI_SECURE", "").lower() in {"1", "true", "yes"}:
        parts.append("Secure")
    return "; ".join(parts)


def extract_bearer(authorization: str | None) -> str | None:
    """从 ``Authorization`` 头提取 Bearer 凭证。"""
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def _ui_base_from_cluster(cluster: dict | None = None) -> str:
    if cluster is None:
        path = cluster_file()
        cluster = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    control = cluster.get("control") or {}
    host = str(control.get("host") or "").strip()
    port = int(control.get("ui_port") or 8787)
    if not host:
        return ""
    scheme = "https" if os.environ.get("GPU_CONTROL_UI_SECURE", "").lower() in {"1", "true", "yes"} else "http"
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def oidc_config(cfg: AuthConfig | None = None, *, cluster: dict | None = None) -> dict[str, Any]:
    """规范化后的 OIDC 配置。"""
    from oidc_util import normalize_oidc

    cfg = cfg or load_auth_config(cluster)
    return normalize_oidc(cfg.oidc, ui_base=_ui_base_from_cluster(cluster))


def password_login_allowed(cfg: AuthConfig | None = None) -> bool:
    """当前模式是否允许密码登录（含 OIDC 回落）。"""
    cfg = cfg or load_auth_config()
    if cfg.mode == "password":
        return True
    if cfg.mode == "oidc":
        return bool((cfg.oidc or {}).get("allow_password_fallback")) and password_configured(cfg)
    return False


def authenticate_http(
    *,
    path: str,
    headers: Any,
    query: str = "",
    cfg: AuthConfig | None = None,
) -> tuple[AuthPrincipal | None, str | None]:
    """校验 HTTP/WebSocket 请求身份。

    :param path: 请求路径。
    :param headers: 类 mapping 的请求头。
    :param query: 原始查询串（WebSocket 可用 ``access_token``）。
    :returns: ``(principal, error)``；放行时 ``error`` 为 ``None``。鉴权关闭时返回合成主体。
    """
    cfg = cfg or load_auth_config()
    if not cfg.enabled:
        return AuthPrincipal(subject="anonymous", method="none", mode="none"), None

    if path in AUTH_PUBLIC_PATHS or path in AUTH_DISCOVERY_PATHS:
        return AuthPrincipal(subject="public", method="none", mode=cfg.mode), None

    if not path.startswith("/api") and not path.startswith("/ws/"):
        return AuthPrincipal(subject="static", method="none", mode=cfg.mode), None

    cookies = parse_cookies(headers.get("Cookie") if headers else None)
    session = verify_session(cookies.get(COOKIE_NAME, ""), cfg=cfg)
    if session:
        return session, None

    bearer = extract_bearer(headers.get("Authorization") if headers else None)
    if bearer and verify_api_token(bearer, cfg=cfg):
        return AuthPrincipal(subject="api-token", method="bearer", mode=cfg.mode), None

    # WebSocket clients can pass ?access_token=
    qs = parse_qs(query or "")
    q_token = (qs.get("access_token") or qs.get("token") or [None])[0]
    if q_token and verify_api_token(str(q_token), cfg=cfg):
        return AuthPrincipal(subject="api-token", method="bearer", mode=cfg.mode), None
    if q_token and verify_session(str(q_token), cfg=cfg):
        return AuthPrincipal(subject="admin", method="session", mode=cfg.mode), None

    return None, "authentication required"


def try_login(password: str, *, cfg: AuthConfig | None = None) -> AuthPrincipal | None:
    """密码登录；失败返回 ``None``。"""
    cfg = cfg or load_auth_config()
    if not password_login_allowed(cfg):
        return None
    if not password_configured(cfg):
        return None
    encoded = cfg.password_hash_file.read_text(encoding="utf-8").strip()
    if not verify_password(password, encoded):
        return None
    return AuthPrincipal(subject="admin", method="session", mode=cfg.mode)


def auth_status_payload(*, cfg: AuthConfig | None = None, principal: AuthPrincipal | None = None) -> dict:
    """构造 ``/api/auth/status`` 响应体。"""
    cfg = cfg or load_auth_config()
    from oidc_util import oidc_ready

    oidc_cfg = oidc_config(cfg)
    ready = oidc_ready(oidc_cfg)
    authed = (not cfg.enabled) or bool(
        principal and principal.method in {"session", "bearer"}
    )
    token_ok = False
    if cfg.api_token_file.is_file():
        token_ok = bool(cfg.api_token_file.read_text(encoding="utf-8").strip())
    return {
        "mode": cfg.mode,
        "enabled": cfg.enabled,
        "authenticated": authed,
        "password_configured": password_configured(cfg),
        "password_login": password_login_allowed(cfg),
        "api_token_configured": token_ok,
        "session_ttl_hours": cfg.session_ttl_hours,
        "oidc_ready": ready,
        "oidc": {
            "ready": ready,
            "issuer": oidc_cfg.get("issuer") or "",
            "client_id": oidc_cfg.get("client_id") or "",
            "redirect_uri": oidc_cfg.get("redirect_uri") or "",
            "login_url": "/api/auth/oidc/login",
            "allow_password_fallback": bool(oidc_cfg.get("allow_password_fallback")),
        },
        "principal": (
            {"subject": principal.subject, "method": principal.method}
            if principal and principal.method in {"session", "bearer"}
            else None
        ),
        "login_path": "/api/auth/login",
        "supports": ["none", "password", "oidc"],
    }


def doctor_auth_notes(cluster: dict | None = None) -> tuple[list[str], list[str]]:
    """为 ``doctor`` / ``validate_config`` 收集鉴权问题与提示。

    :returns: ``(issues, notes)``。
    """
    issues: list[str] = []
    notes: list[str] = []
    cfg = load_auth_config(cluster)
    require = os.environ.get("GPU_CONTROL_REQUIRE_AUTH", "").lower() in {"1", "true", "yes"}
    if not cfg.enabled:
        msg = (
            "WARN: UI auth disabled (control.auth.mode=none). "
            "Enable before public/LAN exposure: "
            "gpu-control auth set-password --generate --enable"
        )
        if require:
            issues.append(msg)
        else:
            notes.append(msg)
        return issues, notes

    notes.append(f"UI auth mode={cfg.mode}")
    if cfg.mode == "password":
        if not password_configured(cfg):
            issues.append(
                f"auth mode=password but hash missing ({cfg.password_hash_file}). "
                "Run: gpu-control auth set-password"
            )
        else:
            notes.append("password hash ok")
        if not cfg.session_secret_file.is_file():
            notes.append("session secret will be auto-created on first login")
        if cfg.api_token_file.is_file():
            notes.append("API bearer token file present")
        else:
            notes.append("no API token yet (optional: gpu-control auth issue-token)")
    elif cfg.mode == "oidc":
        from oidc_util import oidc_ready, probe_oidc

        oc = oidc_config(cfg)
        if not oidc_ready(oc):
            issues.append(
                "auth mode=oidc 但缺少 issuer/client_id/redirect；"
                "见 cluster.control.auth.oidc"
            )
        else:
            probe = probe_oidc(oc)
            if not probe.get("ready"):
                issues.append("OIDC discovery 失败: " + "; ".join(probe.get("reasons") or []))
            else:
                notes.append(f"OIDC issuer={oc.get('issuer')} client_id={oc.get('client_id')}")
            if oc.get("allow_password_fallback") and password_configured(cfg):
                notes.append("OIDC 允许密码回落登录")
    return issues, notes


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    import base64

    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def relative_secret(path: Path) -> str:
    """尽量返回相对仓库根的密钥路径展示串。"""
    try:
        return str(path.relative_to(PROJECT_DIR))
    except ValueError:
        return str(path)
