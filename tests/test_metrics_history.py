"""metrics_history 采样与环形缓冲。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import metrics_history  # noqa: E402


class TestMetricsHistory(unittest.TestCase):
    def test_sample_and_list(self) -> None:
        overview = {
            "summary": {
                "gpus_used": 4,
                "gpus_free": 4,
                "gpus_total": 8,
                "api_ok": 8,
                "api_fail": 2,
                "api_total": 10,
                "probe_ok": 3,
                "probe_fail": 1,
                "leases": 1,
                "queue_pending": 0,
            },
            "quality": {"score": 77},
            "nodes": [{"cpu_percent": 20}, {"cpu_percent": 40}],
            "ingress": {"edge": {"pool": {"available": 6, "upstream_count": 8}}},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "metrics_history.json"
            with patch.object(metrics_history, "HISTORY_FILE", path):
                with patch.object(metrics_history, "_MIN_INTERVAL_SEC", 0):
                    metrics_history.record_overview_sample(overview, force=True)
                    metrics_history.record_overview_sample(overview, force=True)
                    out = metrics_history.list_history(limit=10)
        self.assertGreaterEqual(out["count"], 1)
        last = out["points"][-1]
        self.assertEqual(last["api_ok_pct"], 80.0)
        self.assertEqual(last["gpu_util_pct"], 50.0)
        self.assertEqual(last["cpu_avg_pct"], 30.0)
        self.assertEqual(last["edge_upstreams"], 6)


if __name__ == "__main__":
    unittest.main()
