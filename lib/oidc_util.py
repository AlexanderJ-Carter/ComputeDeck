"""OIDC 授权码 + PKCE（控制面第三方登录）。

配置 ``cluster.control.auth.oidc``：

- ``issuer`` / ``client_id`` / ``client_secret`` 或 ``client_secret_file``
- ``redirect_uri`` 或 ``redirect_path``（拼到 UI 公网地址）
- 可选：``scopes`` · ``username_claim`` · ``allowed_emails`` · ``allowed_subjects`` · ``allowed_groups``
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from paths import STATE_DIR, expand_path

DISCOVERY_CACHE = STATE_DIR / "oidc_discovery.json"
STATE_DIR_OIDC = STATE_DIR / "oidc_pending"
STATE_TTL_SEC = 600


class OidcError(Exception):
    """OIDC 流程错误。"""


def normalize_oidc(raw: dict | None, *, ui_base: str = "") -> dict[str, Any]:
    """规范化 OIDC 配置（读 secret 文件、拼 redirect_uri 等）。

    :param raw: ``cluster.control.auth.oidc`` 原始对象。
    :param ui_base: UI 公网基址，用于拼 ``redirect_path``。
    """
    raw = raw if isinstance(raw, dict) else {}
    secret = str(raw.get("client_secret") or "").strip()
    secret_file = str(raw.get("client_secret_file") or "").strip()
    if not secret and secret_file:
        path = Path(expand_path(secret_file))
        if path.is_file():
            secret = path.read_text(encoding="utf-8").strip()
    redirect = str(raw.get("redirect_uri") or "").strip()
    if not redirect:
        path = str(raw.get("redirect_path") or "/api/auth/oidc/callback").strip() or "/api/auth/oidc/callback"
        base = (ui_base or "").rstrip("/")
        redirect = f"{base}{path}" if base else path
    scopes = raw.get("scopes") or ["openid", "profile", "email"]
    if isinstance(scopes, str):
        scopes = [s for s in scopes.replace(",", " ").split() if s]
    return {
        "issuer": str(raw.get("issuer") or "").rstrip("/"),
        "client_id": str(raw.get("client_id") or "").strip(),
        "client_secret": secret,
        "client_secret_file": secret_file,
        "redirect_uri": redirect,
        "scopes": list(scopes),
        "username_claim": str(raw.get("username_claim") or "email"),
        "allowed_emails": [str(x).lower() for x in (raw.get("allowed_emails") or []) if x],
        "allowed_subjects": [str(x) for x in (raw.get("allowed_subjects") or []) if x],
        "allowed_groups": [str(x) for x in (raw.get("allowed_groups") or []) if x],
        "groups_claim": str(raw.get("groups_claim") or "groups"),
        "allow_password_fallback": bool(raw.get("allow_password_fallback", False)),
        "require_userinfo": bool(raw.get("require_userinfo", True)),
        "authorization_endpoint": str(raw.get("authorization_endpoint") or "").strip(),
        "token_endpoint": str(raw.get("token_endpoint") or "").strip(),
        "userinfo_endpoint": str(raw.get("userinfo_endpoint") or "").strip(),
    }


def oidc_ready(cfg: dict[str, Any]) -> bool:
    """是否具备发起登录所需的最小字段。"""
    return bool(cfg.get("issuer") and cfg.get("client_id") and cfg.get("redirect_uri"))


def discover(cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """拉取 OpenID Provider Metadata；支持本地覆盖 endpoint。

    :param force: 为真时忽略本地缓存。
    :raises OidcError: 缺少 issuer 或 HTTP/JSON 失败。
    """
    if cfg.get("authorization_endpoint") and cfg.get("token_endpoint"):
        return {
            "authorization_endpoint": cfg["authorization_endpoint"],
            "token_endpoint": cfg["token_endpoint"],
            "userinfo_endpoint": cfg.get("userinfo_endpoint") or "",
            "issuer": cfg.get("issuer") or "",
        }
    issuer = cfg.get("issuer") or ""
    if not issuer:
        raise OidcError("缺少 oidc.issuer")
    if not force and DISCOVERY_CACHE.is_file():
        try:
            cached = json.loads(DISCOVERY_CACHE.read_text(encoding="utf-8"))
            if cached.get("issuer") == issuer and cached.get("fetched_at", 0) > time.time() - 3600:
                return cached["doc"]
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    url = f"{issuer}/.well-known/openid-configuration"
    doc = _http_json("GET", url)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DISCOVERY_CACHE.write_text(
        json.dumps({"issuer": issuer, "fetched_at": int(time.time()), "doc": doc}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return doc


def begin_login(cfg: dict[str, Any]) -> dict[str, str]:
    """生成授权 URL 与 pending state。

    :returns: ``authorize_url`` 与 ``state``。
    :raises OidcError: 配置不完整或缺少 authorization_endpoint。
    """
    if not oidc_ready(cfg):
        raise OidcError("OIDC 未配置完整（需要 issuer / client_id / redirect_uri）")
    meta = discover(cfg)
    authorize = meta.get("authorization_endpoint")
    if not authorize:
        raise OidcError("discovery 缺少 authorization_endpoint")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    _save_pending(state, {
        "code_verifier": verifier,
        "created_at": int(time.time()),
        "redirect_uri": cfg["redirect_uri"],
    })
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": " ".join(cfg.get("scopes") or ["openid"]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = authorize + ("&" if "?" in authorize else "?") + urllib.parse.urlencode(params)
    return {"authorize_url": url, "state": state}


def finish_login(cfg: dict[str, Any], *, code: str, state: str) -> dict[str, Any]:
    """用授权码换 token，拉取 userinfo，返回主体信息。

    :param code: 授权码。
    :param state: 与 ``begin_login`` 对应的 state。
    :raises OidcError: state 无效、端点缺失或用户不在白名单。
    """
    pending = _load_pending(state)
    if not pending:
        raise OidcError("state 无效或已过期，请重新登录")
    _delete_pending(state)
    meta = discover(cfg)
    token_url = meta.get("token_endpoint")
    if not token_url:
        raise OidcError("discovery 缺少 token_endpoint")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending.get("redirect_uri") or cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "code_verifier": pending["code_verifier"],
    }
    if cfg.get("client_secret"):
        form["client_secret"] = cfg["client_secret"]
    token = _http_json("POST", token_url, form=form)
    access = token.get("access_token")
    claims: dict[str, Any] = {}
    userinfo_url = meta.get("userinfo_endpoint") or cfg.get("userinfo_endpoint")
    if userinfo_url and access:
        claims = _http_json("GET", userinfo_url, bearer=str(access))
    elif token.get("id_token"):
        if cfg.get("require_userinfo", True):
            raise OidcError(
                "缺少 userinfo_endpoint/access_token；"
                "默认 require_userinfo=true（避免仅解码未验签 id_token）。"
                "仅在可信 IdP 且确认无 userinfo 时设 require_userinfo=false"
            )
        claims = _decode_id_token_claims(str(token["id_token"]), cfg=cfg)
    else:
        raise OidcError("无法取得用户信息（无 userinfo 且无 id_token）")
    subject = _pick_subject(cfg, claims)
    _assert_allowed(cfg, claims, subject)
    return {
        "subject": subject,
        "claims": {
            "sub": claims.get("sub"),
            "email": claims.get("email"),
            "name": claims.get("name") or claims.get("preferred_username"),
        },
        "raw_keys": sorted(claims.keys()),
    }


def probe_oidc(cfg: dict[str, Any]) -> dict[str, Any]:
    """配置自检（不发起登录）。"""
    reasons: list[str] = []
    if not cfg.get("issuer"):
        reasons.append("缺 issuer")
    if not cfg.get("client_id"):
        reasons.append("缺 client_id")
    if not cfg.get("redirect_uri"):
        reasons.append("缺 redirect_uri")
    meta = None
    if not reasons:
        try:
            meta = discover(cfg, force=True)
            if not meta.get("authorization_endpoint"):
                reasons.append("discovery 无 authorization_endpoint")
            if not meta.get("token_endpoint"):
                reasons.append("discovery 无 token_endpoint")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"discovery 失败: {exc}")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "issuer": cfg.get("issuer") or "",
        "client_id": cfg.get("client_id") or "",
        "redirect_uri": cfg.get("redirect_uri") or "",
        "has_client_secret": bool(cfg.get("client_secret")),
        "authorization_endpoint": (meta or {}).get("authorization_endpoint"),
        "token_endpoint": (meta or {}).get("token_endpoint"),
        "userinfo_endpoint": (meta or {}).get("userinfo_endpoint"),
    }


def _pick_subject(cfg: dict[str, Any], claims: dict[str, Any]) -> str:
    """按 ``username_claim`` 选取登录主体字符串。"""
    claim = cfg.get("username_claim") or "email"
    value = claims.get(claim) or claims.get("email") or claims.get("preferred_username") or claims.get("sub")
    if not value:
        raise OidcError(f"claims 中无 {claim}/email/sub")
    return str(value)


def _assert_allowed(cfg: dict[str, Any], claims: dict[str, Any], subject: str) -> None:
    """按邮箱 / sub / 组白名单校验；不通过则抛 ``OidcError``。"""
    emails = cfg.get("allowed_emails") or []
    subs = cfg.get("allowed_subjects") or []
    groups_allow = cfg.get("allowed_groups") or []
    if emails:
        email = str(claims.get("email") or "").lower()
        if email not in emails and subject.lower() not in emails:
            raise OidcError("该邮箱不在 allowed_emails 白名单")
    if subs and str(claims.get("sub") or "") not in subs and subject not in subs:
        raise OidcError("该主体不在 allowed_subjects 白名单")
    if groups_allow:
        claim = cfg.get("groups_claim") or "groups"
        groups = claims.get(claim) or []
        if isinstance(groups, str):
            groups = [groups]
        if not set(str(g) for g in groups) & set(groups_allow):
            raise OidcError("不在 allowed_groups 白名单")


def _save_pending(state: str, payload: dict[str, Any]) -> None:
    STATE_DIR_OIDC.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_DIR_OIDC, 0o700)
    except OSError:
        pass
    _purge_pending()
    path = STATE_DIR_OIDC / f"{state}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_pending(state: str) -> dict[str, Any] | None:
    if not state or "/" in state or ".." in state:
        return None
    path = STATE_DIR_OIDC / f"{state}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if int(data.get("created_at") or 0) < int(time.time()) - STATE_TTL_SEC:
        path.unlink(missing_ok=True)
        return None
    return data


def _delete_pending(state: str) -> None:
    path = STATE_DIR_OIDC / f"{state}.json"
    if path.is_file():
        path.unlink(missing_ok=True)


def _purge_pending() -> None:
    if not STATE_DIR_OIDC.is_dir():
        return
    now = int(time.time())
    for path in STATE_DIR_OIDC.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("created_at") or 0) < now - STATE_TTL_SEC:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError, TypeError):
            path.unlink(missing_ok=True)


def _http_json(
    method: str,
    url: str,
    *,
    form: dict[str, str] | None = None,
    bearer: str | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "gpu-control-oidc/1"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise OidcError(f"HTTP {exc.code} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OidcError(f"请求失败 {url}: {exc}") from exc
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OidcError(f"非 JSON 响应: {body[:200]}") from exc
    if not isinstance(parsed, dict):
        raise OidcError("JSON 根须为 object")
    return parsed


def _decode_id_token_claims(token: str, *, cfg: dict[str, Any]) -> dict[str, Any]:
    """解码 id_token payload 并校验 iss/aud/exp（仍不验签；优先 userinfo）。"""
    parts = token.split(".")
    if len(parts) < 2:
        raise OidcError("id_token 格式错误")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise OidcError("id_token payload 无法解码") from exc
    if not isinstance(payload, dict):
        raise OidcError("id_token payload 无效")
    now = int(time.time())
    exp = int(payload.get("exp") or 0)
    if exp and exp < now - 30:
        raise OidcError("id_token 已过期")
    issuer = str(cfg.get("issuer") or "").rstrip("/")
    token_iss = str(payload.get("iss") or "").rstrip("/")
    if issuer and token_iss and token_iss != issuer:
        raise OidcError("id_token iss 与配置 issuer 不一致")
    aud = payload.get("aud")
    client_id = str(cfg.get("client_id") or "")
    if client_id and aud is not None:
        audiences = aud if isinstance(aud, list) else [aud]
        if client_id not in {str(a) for a in audiences}:
            raise OidcError("id_token aud 不包含 client_id")
    return payload


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)
