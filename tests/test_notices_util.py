"""notices_util：upsert / 已读 / diagnose 映射。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import notices_util  # noqa: E402


class TestNoticesUtil(unittest.TestCase):
    def test_upsert_and_mark(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notices.json"
            with patch.object(notices_util, "NOTICES_FILE", path):
                notices_util.upsert_notices(
                    [
                        {
                            "id": "diagnose:x",
                            "source": "diagnose",
                            "severity": "warn",
                            "title": "缺配置",
                            "detail": "d",
                        }
                    ]
                )
                listed = notices_util.list_notices()
                self.assertEqual(listed["unread"], 1)
                self.assertEqual(listed["items"][0]["id"], "diagnose:x")
                notices_util.mark_notices(ids=["diagnose:x"], read=True)
                self.assertEqual(notices_util.list_notices()["unread"], 0)
                notices_util.mark_notices(ids=["diagnose:x"], dismissed=True)
                self.assertEqual(notices_util.list_notices()["count"], 0)

    def test_findings_and_overview_rows(self) -> None:
        rows = notices_util.findings_to_notices(
            [{"id": "missing-cluster", "severity": "error", "title": "缺 cluster", "detail": "x"}]
        )
        self.assertEqual(rows[0]["id"], "diagnose:missing-cluster")
        ov = notices_util.overview_to_notices(
            {
                "watchdog": {"enabled": True, "running": False},
                "quality": {"score": 30, "summary": "差"},
                "summary": {"api_fail": 2},
            }
        )
        ids = {r["id"] for r in ov}
        self.assertIn("watchdog:not-running", ids)
        self.assertIn("quality:low-score", ids)
        self.assertIn("endpoints:api-fail", ids)


if __name__ == "__main__":
    unittest.main()
