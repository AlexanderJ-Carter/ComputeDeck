#!/usr/bin/env python3
"""控制面 UI 鉴权：密码 / Token / OIDC。"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys

import _bootstrap  # noqa: F401
from auth_util import (
    doctor_auth_notes,
    issue_api_token,
    load_auth_config,
    oidc_config,
    password_configured,
    relative_secret,
    save_auth_mode,
    set_password,
)
from oidc_util import oidc_ready, probe_oidc


def cmd_status(_: argparse.Namespace) -> int:
    cfg = load_auth_config()
    issues, notes = doctor_auth_notes()
    oc = oidc_config(cfg)
    payload = {
        "mode": cfg.mode,
        "enabled": cfg.enabled,
        "password_configured": password_configured(cfg),
        "password_hash_file": relative_secret(cfg.password_hash_file),
        "session_secret_file": relative_secret(cfg.session_secret_file),
        "api_token_file": relative_secret(cfg.api_token_file),
        "api_token_configured": cfg.api_token_file.is_file()
        and bool(cfg.api_token_file.read_text(encoding="utf-8").strip())
        if cfg.api_token_file.is_file()
        else False,
        "session_ttl_hours": cfg.session_ttl_hours,
        "oidc": {
            "ready": oidc_ready(oc),
            "issuer": oc.get("issuer") or "",
            "client_id": oc.get("client_id") or "",
            "redirect_uri": oc.get("redirect_uri") or "",
            "has_client_secret": bool(oc.get("client_secret")),
            "allow_password_fallback": bool(oc.get("allow_password_fallback")),
        },
        "issues": issues,
        "notes": notes,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if issues and cfg.enabled else 0


def cmd_set_password(args: argparse.Namespace) -> int:
    if args.generate and not args.password:
        password = secrets.token_urlsafe(18)
        print(f"generated_password={password}")
    elif args.password:
        password = args.password
    else:
        password = getpass.getpass("New UI password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("passwords do not match", file=sys.stderr)
            return 2
    try:
        path = set_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"wrote {relative_secret(path)}")
    if args.enable:
        save_auth_mode("password")
        print("control.auth.mode=password")
    return 0


def cmd_set_mode(args: argparse.Namespace) -> int:
    path = save_auth_mode(args.mode)
    print(f"mode={args.mode} → {relative_secret(path)}")
    if args.mode == "password" and not password_configured():
        print("warning: password hash missing; run: gpu-control auth set-password", file=sys.stderr)
        return 1
    if args.mode == "oidc":
        oc = oidc_config()
        if not oidc_ready(oc):
            print(
                "warning: OIDC 未配齐 issuer/client_id/redirect；见 cluster.control.auth.oidc",
                file=sys.stderr,
            )
            return 1
    return 0


def cmd_issue_token(args: argparse.Namespace) -> int:
    token, path = issue_api_token()
    print(f"wrote {relative_secret(path)}")
    print(f"token={token}")
    print("Use: Authorization: Bearer <token>")
    print("Or:  GPU_CONTROL_API_TOKEN / MCP Authorization header")
    if args.enable and load_auth_config().mode == "none":
        save_auth_mode("password")
        print("control.auth.mode=password (token alone still needs mode != none)")
    return 0


def cmd_oidc_check(_: argparse.Namespace) -> int:
    cfg = load_auth_config()
    oc = oidc_config(cfg)
    probe = probe_oidc(oc)
    print(json.dumps({"mode": cfg.mode, **probe}, ensure_ascii=False, indent=2))
    return 0 if probe.get("ready") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_status = sub.add_parser("status", help="Show auth configuration")
    p_status.set_defaults(func=cmd_status)

    p_pw = sub.add_parser("set-password", help="Set / rotate UI password hash")
    p_pw.add_argument("--password", help="Password (omit to prompt)")
    p_pw.add_argument("--generate", action="store_true", help="Generate a random password")
    p_pw.add_argument("--enable", action="store_true", help="Also set mode=password")
    p_pw.set_defaults(func=cmd_set_password)

    p_mode = sub.add_parser("set-mode", help="Set control.auth.mode")
    p_mode.add_argument("mode", choices=["none", "password", "oidc"])
    p_mode.set_defaults(func=cmd_set_mode)

    p_tok = sub.add_parser("issue-token", help="Create API bearer token file")
    p_tok.add_argument("--enable", action="store_true", help="Ensure mode=password")
    p_tok.set_defaults(func=cmd_issue_token)

    p_oidc = sub.add_parser("oidc-check", help="探测 OIDC discovery / 配置完整性")
    p_oidc.set_defaults(func=cmd_oidc_check)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
