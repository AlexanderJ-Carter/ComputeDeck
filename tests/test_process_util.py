"""跨平台 pid 工具与安全终止。"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from process_util import pid_alive, pid_cmdline, pid_matches, terminate_pid  # noqa: E402


class ProcessUtilTests(unittest.TestCase):
    def test_current_process_is_alive(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))
        self.assertIn(Path(sys.executable).name, pid_cmdline(os.getpid()))

    def test_missing_pid_is_not_alive(self) -> None:
        # 取一个几乎不可能存在的 pid；即使存在也只跳过该断言。
        missing = 2**31 - 1
        self.assertFalse(pid_alive(missing))
        self.assertEqual(pid_cmdline(missing), "")

    def test_mismatched_pid_is_not_killed(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            result = terminate_pid(
                child.pid, expected=("definitely-not-this-process",), group=False
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result.get("stale"))
            self.assertTrue(pid_alive(child.pid))
            self.assertTrue(pid_matches(child.pid, ("-c",)))
        finally:
            if pid_alive(child.pid):
                child.kill()
            child.wait(timeout=5)

    def test_terminate_target_process(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result = terminate_pid(child.pid, expected=("-c",), group=False, timeout=2)
        self.assertTrue(result["ok"], result)
        self.assertFalse(pid_alive(child.pid))
        child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
