"""metrics_history 采样与短窗缓冲。"""

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
    def test_sample_distinguishes_alloc_and_sm(self) -> None:
        overview = {
            "summary": {
                "gpus_used": 4,
                "gpus_free": 4,
                "gpus_total": 8,
                "api_ok": 8,
                "api_fail": 2,
                "api_total": 10,
                "leases": 1,
                "queue_pending": 3,
                "nodes": 2,
                "nodes_online": 1,
            },
            "quality": {"score": 88, "grade": "good"},
            "nodes": [
                {
                    "cpu_percent": 20,
                    "gpus": [
                        {
                            "utilization_percent": 10,
                            "memory_used_mib": 1024,
                            "memory_total_mib": 4096,
                            "status": "occupied",
                        },
                        {
                            "utilization_percent": 90,
                            "memory_used_mib": 3072,
                            "memory_total_mib": 4096,
                            "status": "occupied",
                        },
                    ],
                },
                {
                    "cpu_percent": 40,
                    "gpus": [
                        {
                            "utilization_percent": 0,
                            "memory_used_mib": 0,
                            "memory_total_mib": 4096,
                            "status": "free",
                        },
                        {
                            "utilization_percent": 0,
                            "memory_used_mib": 0,
                            "memory_total_mib": 4096,
                            "status": "free",
                        },
                    ],
                },
            ],
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
        self.assertEqual(out["retain_sec"], 3600)
        last = out["points"][-1]
        self.assertEqual(last["api_ok_pct"], 80.0)
        self.assertEqual(last["gpu_alloc_pct"], 50.0)
        self.assertEqual(last["gpu_sm_util_pct"], 25.0)
        self.assertEqual(last["gpu_mem_util_pct"], 25.0)
        self.assertEqual(last["cpu_avg_pct"], 30.0)
        self.assertEqual(last["edge_upstreams"], 6)
        self.assertEqual(last["queue_pending"], 3)
        self.assertEqual(last["nodes_online_pct"], 50.0)
        self.assertEqual(last["quality_score"], 88)
        self.assertNotIn("gpu_util_pct", last)
        series = {row["id"] for row in out["series"]}
        self.assertIn("nodes_online_pct", series)
        self.assertIn("queue_pending", series)

    def test_prune_old_points(self) -> None:
        overview = {
            "summary": {"gpus_used": 1, "gpus_free": 1, "gpus_total": 2, "api_ok": 1, "api_total": 1},
            "nodes": [],
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "metrics_history.json"
            with patch.object(metrics_history, "HISTORY_FILE", path):
                with patch.object(metrics_history, "_MIN_INTERVAL_SEC", 0):
                    with patch.object(metrics_history, "_RETAIN_SEC", 30):
                        now = 1_700_000_000
                        with patch.object(metrics_history.time, "time", return_value=now - 120):
                            metrics_history.record_overview_sample(overview, force=True)
                        with patch.object(metrics_history.time, "time", return_value=now):
                            metrics_history.record_overview_sample(overview, force=True)
                            out = metrics_history.list_history(limit=50)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["points"][0]["epoch"], 1_700_000_000)


if __name__ == "__main__":
    unittest.main()
