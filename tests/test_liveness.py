"""节点存活状态机、快照新鲜度与公告规则。"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import liveness  # noqa: E402


def _result(name: str, ok: bool, *, error: str | None = None, now: float | None = None) -> dict:
    now = now or time.time()
    return {
        "node": name,
        "host": "10.0.0.1",
        "ssh_port": 22,
        "ssh": {
            "ok": ok,
            "latency_ms": 12,
            "error": error,
            "checked_at": liveness.iso(),
            "checked_epoch": now,
        },
        "api": None,
        "checked_at": liveness.iso(),
        "checked_epoch": now,
    }


class LivenessStateTests(unittest.TestCase):
    def test_first_success_is_online(self) -> None:
        snap = liveness.update_snapshot(None, [_result("a", True)], node_names=["a"])
        self.assertEqual(snap["nodes"]["a"]["state"], "online")
        self.assertTrue(snap["nodes"]["a"]["reachable"])
        self.assertEqual(snap["summary"]["online"], 1)

    def test_single_failure_is_degraded(self) -> None:
        snap = liveness.update_snapshot(None, [_result("a", False, error="timed out")], node_names=["a"])
        self.assertEqual(snap["nodes"]["a"]["state"], "degraded")
        self.assertEqual(snap["nodes"]["a"]["consecutive_failures"], 1)

    def test_second_failure_is_offline(self) -> None:
        snap = liveness.update_snapshot(None, [_result("a", True)], node_names=["a"])
        snap = liveness.update_snapshot(snap, [_result("a", False, error="timed out")], node_names=["a"])
        self.assertEqual(snap["nodes"]["a"]["state"], "degraded")
        snap = liveness.update_snapshot(snap, [_result("a", False, error="timed out")], node_names=["a"])
        self.assertEqual(snap["nodes"]["a"]["state"], "offline")
        self.assertEqual(snap["summary"]["offline_nodes"], ["a"])

    def test_definitive_failure_is_immediately_offline(self) -> None:
        snap = liveness.update_snapshot(
            None,
            [_result("a", False, error="[Errno 113] No route to host")],
            node_names=["a"],
        )
        self.assertEqual(snap["nodes"]["a"]["state"], "offline")
        self.assertEqual(snap["summary"]["offline"], 1)

    def test_missing_node_is_unknown(self) -> None:
        snap = liveness.update_snapshot(None, [], node_names=["a"])
        self.assertEqual(snap["nodes"]["a"]["state"], "unknown")

    def test_success_resets_failures(self) -> None:
        snap = liveness.update_snapshot(None, [_result("a", True)], node_names=["a"])
        snap = liveness.update_snapshot(snap, [_result("a", False, error="timed out")], node_names=["a"])
        snap = liveness.update_snapshot(snap, [_result("a", True)], node_names=["a"])
        self.assertEqual(snap["nodes"]["a"]["state"], "online")
        self.assertEqual(snap["nodes"]["a"]["consecutive_failures"], 0)


class LivenessViewTests(unittest.TestCase):
    def test_persisted_snapshot_wins(self) -> None:
        now = time.time()
        snap = liveness.update_snapshot(None, [_result("a", True, now=now)], node_names=["a"], now=now)
        view = liveness.cluster_view(
            node_names=["a"],
            resources={"a": {"collected_at": liveness.iso()}},
            persisted=snap,
            now=now,
        )
        self.assertEqual(view["nodes"]["a"]["state"], "online")
        self.assertFalse(view["nodes"]["a"]["snapshot"]["stale"])

    def test_stale_snapshot_excluded(self) -> None:
        now = time.time()
        snap = liveness.update_snapshot(None, [_result("a", True, now=now)], node_names=["a"], now=now)
        view = liveness.cluster_view(
            node_names=["a"],
            resources={"a": {"collected_at": "2026-01-01T00:00:00Z"}},
            persisted=snap,
            now=now,
        )
        self.assertEqual(view["nodes"]["a"]["state"], "online")
        self.assertTrue(view["nodes"]["a"]["snapshot"]["stale"])
        self.assertEqual(view["summary"]["snapshot_stale_count"], 1)

    def test_ssh_quality_fallback_marks_no_route_offline(self) -> None:
        now = time.time()
        checked = liveness.iso()
        view = liveness.cluster_view(
            node_names=["a", "b"],
            resources={},
            ssh_quality={
                "checked_at": checked,
                "results": [
                    {"node": "a", "ok": True, "latency_ms": 5, "detail": "host"},
                    {"node": "b", "ok": False, "latency_ms": 900, "detail": "No route to host"},
                ],
            },
            now=now,
        )
        self.assertEqual(view["nodes"]["a"]["state"], "online")
        self.assertEqual(view["nodes"]["b"]["state"], "offline")
        self.assertEqual(view["summary"]["offline_nodes"], ["b"])

    def test_stale_ssh_quality_is_ignored(self) -> None:
        now = time.time()
        view = liveness.cluster_view(
            node_names=["a"],
            resources={},
            ssh_quality={
                "checked_at": "2026-01-01T00:00:00Z",
                "results": [{"node": "a", "ok": False, "detail": "No route to host"}],
            },
            now=now,
        )
        self.assertEqual(view["nodes"]["a"]["state"], "unknown")

    def test_notices_for_offline_and_stale(self) -> None:
        now = time.time()
        snap = liveness.update_snapshot(
            None,
            [_result("a", False, error="No route to host", now=now)],
            node_names=["a"],
            now=now,
        )
        view = liveness.cluster_view(
            node_names=["a"],
            resources={"a": {"collected_at": "2026-01-01T00:00:00Z"}},
            persisted=snap,
            now=now,
        )
        ids = [row["id"] for row in liveness.offline_notices(view)]
        self.assertIn("node:offline:a", ids)
        stale_ids = [row["id"] for row in liveness.stale_notices(view)]
        self.assertIn("snapshot:stale:a", stale_ids)

    def test_format_age(self) -> None:
        self.assertEqual(liveness.format_age(None), "-")
        self.assertEqual(liveness.format_age(42), "42s")
        self.assertEqual(liveness.format_age(180), "3m")
        self.assertEqual(liveness.format_age(7200), "2h")
        self.assertEqual(liveness.format_age(90000), "1d")


if __name__ == "__main__":
    unittest.main()
