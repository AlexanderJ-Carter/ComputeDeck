"""LiteLLM Master Key 密钥文件读写。"""

from __future__ import annotations

import importlib.util
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


class LitellmMasterKeyTests(unittest.TestCase):
    def test_write_read_clear(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "litellm_master_key"
            with patch.object(MOD, "LITELLM_MASTER_KEY_FILE", path):
                self.assertIsNone(MOD.read_litellm_master_key())
                MOD.write_litellm_master_key("sk-test-key")
                self.assertEqual(MOD.read_litellm_master_key(), "sk-test-key")
                st = MOD.litellm_master_key_status()
                self.assertTrue(st["configured"])
                self.assertTrue(MOD.clear_litellm_master_key())
                self.assertIsNone(MOD.read_litellm_master_key())

    def test_generate_prefix(self) -> None:
        key = MOD.generate_litellm_master_key()
        self.assertTrue(key.startswith("sk-cd-"))
        self.assertGreater(len(key), 16)


if __name__ == "__main__":
    unittest.main()
