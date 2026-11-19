from __future__ import annotations

import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from tls_ops import _san_entries, cmd_init  # noqa: E402


class SanTests(unittest.TestCase):
    def test_always_has_loopback(self) -> None:
        entries = _san_entries("api.example.com", "10.0.0.10")
        self.assertIn("DNS:localhost", entries)
        self.assertIn("IP:127.0.0.1", entries)
        self.assertIn("DNS:api.example.com", entries)
        self.assertIn("IP:10.0.0.10", entries)

    def test_dedup(self) -> None:
        self.assertEqual(len(_san_entries("localhost", "127.0.0.1")), len(set(_san_entries("localhost", "127.0.0.1"))))


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class InitTests(unittest.TestCase):
    def test_generates_cert_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cert = root / "ingress.crt"
            key = root / "ingress.key"
            args = Namespace(
                domain="test.local",
                days=1,
                cert=str(cert),
                key=str(key),
                force=False,
            )
            with mock.patch("tls_ops._load_cluster", return_value={}), mock.patch(
                "tls_ops.LOCAL_DIR", root
            ), mock.patch("tls_ops._write_cluster", return_value=root / "cluster.json"), redirect_stdout(io.StringIO()):
                code = cmd_init(args)
            self.assertEqual(code, 0)
            self.assertTrue(cert.is_file())
            self.assertTrue(key.is_file())
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
