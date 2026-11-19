from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from render_gateway import render_nginx  # noqa: E402


class RenderNginxTests(unittest.TestCase):
    def test_node_lb_has_health_and_streaming_settings(self) -> None:
        text = render_nginx(
            [
                {"host": "10.0.0.1", "port": 8001},
                {"host": "10.0.0.2", "port": 8002},
            ],
            listen=18080,
        )
        self.assertIn("least_conn;", text)
        self.assertIn("server 10.0.0.1:8001 max_fails=2 fail_timeout=5s;", text)
        self.assertIn("keepalive 32;", text)
        self.assertIn("proxy_buffering off;", text)
        self.assertIn("proxy_request_buffering off;", text)
        self.assertIn("proxy_next_upstream_tries 2;", text)
        self.assertIn("X-Forwarded-Proto", text)

    def test_litellm_front_has_single_upstream(self) -> None:
        text = render_nginx([], listen=18443, litellm_url="http://127.0.0.1:4000")
        self.assertIn("upstream gpu_control_litellm", text)
        self.assertIn("server 127.0.0.1:4000;", text)
        self.assertNotIn("least_conn;", text)

    def test_tls_requires_cert_and_key(self) -> None:
        text = render_nginx(
            [{"host": "10.0.0.1", "port": 8001}],
            listen=18443,
            tls={"enabled": True, "cert_file": "/tmp/a.crt", "key_file": "/tmp/a.key"},
        )
        self.assertIn("listen 18443 ssl;", text)
        self.assertIn("ssl_certificate     /tmp/a.crt;", text)


if __name__ == "__main__":
    unittest.main()
