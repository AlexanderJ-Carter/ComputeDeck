"""exec 结果成功判定与失败信息（防止回复出现“操作失败：None”）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402


class ExecResultTests(unittest.TestCase):
    def test_row_success_uses_rc_when_ok_missing(self) -> None:
        self.assertTrue(server.exec_row_ok({"node": "a", "rc": 0, "stdout": "hi"}))
        self.assertFalse(server.exec_row_ok({"node": "a", "rc": 1, "stderr": "boom"}))
        self.assertTrue(server.exec_row_ok({"node": "a", "ok": True}))
        self.assertFalse(server.exec_row_ok({"node": "a", "ok": False}))
        self.assertFalse(server.exec_row_ok("bad"))

    def test_failure_detail_never_none(self) -> None:
        detail = server.exec_failure_detail(
            {"results": [{"node": "deepseek-4", "rc": 1, "stderr": "command not found"}]}
        )
        self.assertIn("deepseek-4", detail)
        self.assertIn("command not found", detail)
        self.assertNotEqual(detail.strip(), "None")
        self.assertEqual(
            server.exec_failure_detail({}, {"error": "ssh failed"}),
            "ssh failed",
        )
        self.assertTrue(server.exec_failure_detail({}).strip())


if __name__ == "__main__":
    unittest.main()
