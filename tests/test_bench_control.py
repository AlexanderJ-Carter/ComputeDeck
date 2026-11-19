from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_control import _percentiles  # noqa: E402


class PercentileTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(_percentiles([])["p50"], 0.0)

    def test_ordered(self) -> None:
        values = [float(i) for i in range(1, 101)]
        row = _percentiles(values)
        self.assertEqual(row["p50"], 51.0)
        self.assertEqual(row["p95"], 95.0)
        self.assertEqual(row["p99"], 99.0)


if __name__ == "__main__":
    unittest.main()
