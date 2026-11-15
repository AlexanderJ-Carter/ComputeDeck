"""鉴权与 OIDC 关键路径测试（不依赖外网 IdP）。"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


class AuthSessionTests(unittest.TestCase):
    def test_session_roundtrip(self) -> None:
        from auth_util import AuthConfig, issue_session, verify_session

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = AuthConfig(
                mode="password",
                session_ttl_hours=1,
                password_hash_file=base / "hash",
                session_secret_file=base / "secret",
                api_token_file=base / "token",
                oidc={},
            )
            token = issue_session(subject="alice", cfg=cfg)
            principal = verify_session(token, cfg=cfg)
            self.assertIsNotNone(principal)
            assert principal is not None
            self.assertEqual(principal.subject, "alice")
            self.assertEqual(principal.method, "session")
            self.assertIsNone(verify_session(token + "x", cfg=cfg))

    def test_login_rate_limit(self) -> None:
        from auth_util import (
            clear_login_failures,
            login_rate_limited,
            record_login_failure,
        )

        key = f"test-{time.time()}"
        clear_login_failures(key)
        self.assertFalse(login_rate_limited(key))
        for _ in range(8):
            record_login_failure(key)
        self.assertTrue(login_rate_limited(key))
        clear_login_failures(key)
        self.assertFalse(login_rate_limited(key))


class OidcUtilTests(unittest.TestCase):
    def test_normalize_and_ready(self) -> None:
        from oidc_util import normalize_oidc, oidc_ready

        cfg = normalize_oidc(
            {
                "issuer": "https://idp.example",
                "client_id": "gpu-control",
                "redirect_path": "/api/auth/oidc/callback",
            },
            ui_base="https://ui.example:8787",
        )
        self.assertTrue(oidc_ready(cfg))
        self.assertTrue(cfg["require_userinfo"])
        self.assertEqual(cfg["redirect_uri"], "https://ui.example:8787/api/auth/oidc/callback")

    def test_id_token_claim_checks(self) -> None:
        import base64
        import json

        from oidc_util import OidcError, _decode_id_token_claims

        def mint(payload: dict) -> str:
            head = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
            body = base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            ).decode().rstrip("=")
            return f"{head}.{body}.x"

        cfg = {
            "issuer": "https://idp.example",
            "client_id": "gpu-control",
        }
        good = mint(
            {
                "iss": "https://idp.example",
                "aud": "gpu-control",
                "exp": int(time.time()) + 600,
                "email": "a@example.com",
                "sub": "1",
            }
        )
        claims = _decode_id_token_claims(good, cfg=cfg)
        self.assertEqual(claims["email"], "a@example.com")

        expired = mint(
            {
                "iss": "https://idp.example",
                "aud": "gpu-control",
                "exp": int(time.time()) - 120,
                "sub": "1",
            }
        )
        with self.assertRaises(OidcError):
            _decode_id_token_claims(expired, cfg=cfg)

        bad_aud = mint(
            {
                "iss": "https://idp.example",
                "aud": "other",
                "exp": int(time.time()) + 600,
                "sub": "1",
            }
        )
        with self.assertRaises(OidcError):
            _decode_id_token_claims(bad_aud, cfg=cfg)

    def test_pending_state_file_mode(self) -> None:
        from oidc_util import STATE_DIR_OIDC, _delete_pending, _save_pending, normalize_oidc

        cfg = normalize_oidc(
            {"issuer": "https://idp.example", "client_id": "c", "redirect_uri": "https://x/cb"}
        )
        self.assertTrue(cfg["require_userinfo"])
        state = f"unittest-{int(time.time())}"
        _save_pending(state, {"code_verifier": "v", "created_at": int(time.time())})
        path = STATE_DIR_OIDC / f"{state}.json"
        self.assertTrue(path.is_file())
        mode = path.stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0)
        _delete_pending(state)


if __name__ == "__main__":
    unittest.main()
