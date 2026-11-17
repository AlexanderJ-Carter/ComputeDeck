"""自动巡检阈值：必须在各信号 TTL 之前刷新。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402


class AutoOpsConfigTests(unittest.TestCase):
    def test_refreshes_before_ttl(self) -> None:
        cfg = server._auto_ops_cfg(
            {
                "health": {
                    "snapshot_ttl_sec": 900,
                    "ssh_ttl_sec": 300,
                    "host_ttl_sec": 300,
                    "api_ttl_sec": 300,
                    "mesh_ttl_sec": 900,
                }
            }
        )
        self.assertTrue(cfg["enabled"])
        self.assertLess(cfg["gpu_stale_sec"], 900)
        self.assertLess(cfg["mesh_stale_sec"], 900)
        self.assertLess(cfg["ssh_stale_sec"], 300)
        self.assertLess(cfg["host_stale_sec"], 300)
        self.assertLess(cfg["api_stale_sec"], 300)

    def test_explicit_override_and_disable(self) -> None:
        cfg = server._auto_ops_cfg(
            {"health": {"auto_refresh": {"gpu_stale_sec": 120, "api_stale_sec": 90}}}
        )
        self.assertEqual(cfg["gpu_stale_sec"], 120)
        self.assertEqual(cfg["api_stale_sec"], 90)
        self.assertFalse(server._auto_ops_cfg({"health": {"auto_refresh": False}})["enabled"])


if __name__ == "__main__":
    unittest.main()
