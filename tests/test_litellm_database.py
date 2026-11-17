"""LiteLLM 管理数据库：URL 来源、脱敏与 Admin UI 可用性判断。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))
SPEC = importlib.util.spec_from_file_location("gateway_ops", ROOT / "scripts" / "gateway_ops.py")
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["gateway_ops"] = MOD
SPEC.loader.exec_module(MOD)


class LitellmDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(MOD.DATABASE_URL_ENV, None)

    def test_missing_database(self) -> None:
        st = MOD.litellm_database({})
        self.assertFalse(st["configured"])
        self.assertFalse(st["admin_ui"])
        self.assertIsNone(st["source"])
        self.assertIn("Master Key", st["hint"])

    def test_config_url_masks_password(self) -> None:
        url = "postgresql://litellm:secret-pass@127.0.0.1:5432/litellm"
        st = MOD.litellm_database({"litellm_database_url": url})
        self.assertTrue(st["configured"])
        self.assertTrue(st["admin_ui"])
        self.assertEqual(st["source"], "config")
        self.assertNotIn("secret-pass", st["url_masked"])
        self.assertIn("127.0.0.1:5432/litellm", st["url_masked"])

    def test_file_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "db_url"
            path.write_text("postgres://u:p@db:5432/ll\n", encoding="utf-8")
            st = MOD.litellm_database({"litellm_database_url_file": str(path)})
            self.assertTrue(st["configured"])
            self.assertEqual(st["source"], "file")

    def test_env_fallback(self) -> None:
        os.environ[MOD.DATABASE_URL_ENV] = "postgres://u:p@db:5432/ll"
        st = MOD.litellm_database({})
        self.assertTrue(st["configured"])
        self.assertEqual(st["source"], "env")

    def test_rejects_non_postgres(self) -> None:
        st = MOD.litellm_database({"litellm_database_url": "sqlite:///tmp/litellm.db"})
        self.assertFalse(st["configured"])
        self.assertFalse(st["scheme_ok"])
        self.assertIn("postgres", st["hint"])

    def test_embedded_url_detection(self) -> None:
        url = f"postgresql://litellm:pw@127.0.0.1:{MOD.LITELLM_PG_PORT}/litellm"
        self.assertTrue(MOD._is_embedded_db_url(url))
        self.assertFalse(MOD._is_embedded_db_url("postgresql://litellm:pw@db:5432/litellm"))
        self.assertFalse(MOD._is_embedded_db_url("postgresql://litellm:pw@127.0.0.1:5432/litellm"))

    def test_embedded_mode_status(self) -> None:
        url = f"postgresql://litellm:pw@127.0.0.1:{MOD.LITELLM_PG_PORT}/litellm"
        st = MOD.litellm_database({"litellm_database_url": url, "litellm_database_mode": "embedded"})
        self.assertEqual(st["mode"], "embedded")
        self.assertTrue(st["embedded"])
        self.assertIn("server_running", st)
        self.assertTrue(st["configured"])

    def test_url_alone_does_not_enable_embedded(self) -> None:
        url = f"postgresql://litellm:pw@127.0.0.1:{MOD.LITELLM_PG_PORT}/litellm"
        st = MOD.litellm_database({"litellm_database_url": url})
        self.assertEqual(st["mode"], "external")
        self.assertFalse(st["embedded"])

    def test_pg_bin_dir_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            for name in ("pg_ctl", "postgres"):
                path = base / name
                path.write_text("#!/bin/sh\n", encoding="utf-8")
                path.chmod(0o755)
            os.environ["GPU_CONTROL_PG_BIN"] = str(base)
            try:
                self.assertEqual(MOD._pg_bin_dir(), base)
            finally:
                os.environ.pop("GPU_CONTROL_PG_BIN", None)


if __name__ == "__main__":
    unittest.main()
