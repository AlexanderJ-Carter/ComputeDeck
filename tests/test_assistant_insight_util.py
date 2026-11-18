from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import assistant_insight_util as insight  # noqa: E402


class InsightStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.file = self.state / "assistant_insights.jsonl"
        self.patches = [
            mock.patch.object(insight, "STATE_DIR", self.state),
            mock.patch.object(insight, "INSIGHT_FILE", self.file),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def test_append_and_list(self) -> None:
        insight.append_insight(kind="analyze", ok=True, reply="集群健康", actor="session:a")
        insight.append_insight(kind="analyze", ok=False, reply="失败", actor="session:b")
        payload = insight.list_insights(limit=10, kind="analyze")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["items"][0]["reply"], "失败")
        self.assertEqual(payload["items"][0]["actor"], "session:b")


if __name__ == "__main__":
    unittest.main()
