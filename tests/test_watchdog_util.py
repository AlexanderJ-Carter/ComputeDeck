"""watchdog：Ansible PLAY RECAP 解析与失败节点筛选。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import watchdog  # noqa: E402


class WatchdogRecapTests(unittest.TestCase):
    def test_failed_hosts_only(self) -> None:
        output = """
PLAY RECAP *********************************************************************
deepseek-1                 : ok=4    changed=0    unreachable=0    failed=1    skipped=11   rescued=0    ignored=0
deepseek-4                 : ok=6    changed=0    unreachable=0    failed=0    skipped=11   rescued=0    ignored=0
deepseek-9                 : ok=0    changed=0    unreachable=1    failed=0    skipped=0    rescued=0    ignored=0
"""
        self.assertEqual(watchdog.failed_hosts_from_output(output), ["deepseek-1"])

    def test_no_false_unreachable_from_zero(self) -> None:
        output = "deepseek-1 : ok=4 changed=0 unreachable=0 failed=0 skipped=11 rescued=0 ignored=0"
        self.assertEqual(watchdog.failed_hosts_from_output(output), [])


if __name__ == "__main__":
    unittest.main()
