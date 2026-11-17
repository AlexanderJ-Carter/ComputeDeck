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

    def test_reconcile_removes_resolved_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notices.json"
            with patch.object(notices_util, "NOTICES_FILE", path):
                notices_util.upsert_notices(
                    [
                        {"id": "node:offline:a", "severity": "error", "title": "a", "detail": ""},
                        {"id": "snapshot:stale:b", "severity": "warn", "title": "b", "detail": ""},
                        {"id": "manual:keep", "severity": "warn", "title": "keep", "detail": ""},
                    ]
                )
                # 只剩 a 仍成立；b 应被清除；manual 不受影响
                notices_util.reconcile_notices(
                    [{"id": "node:offline:a"}],
                )
                ids = {row["id"] for row in notices_util.list_notices()["items"]}
                self.assertIn("node:offline:a", ids)
                self.assertNotIn("snapshot:stale:b", ids)
                self.assertIn("manual:keep", ids)
                # 没有可清除项时不改写文件
                before = path.read_text(encoding="utf-8")
                notices_util.reconcile_notices([{"id": "node:offline:a"}])
                self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
