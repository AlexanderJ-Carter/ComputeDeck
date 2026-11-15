from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from state_util import atomic_write_json, file_lock, read_json, update_json  # noqa: E402


class StateUtilTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_roundtrip(self) -> None:
        atomic_write_json(self.path, {"a": 1, "中文": "值"})
        self.assertEqual(read_json(self.path, {}), {"a": 1, "中文": "值"})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_missing_returns_default_copy(self) -> None:
        default = {"leases": []}
        result = read_json(self.path, default)
        self.assertEqual(result, default)
        self.assertIsNot(result, default)

    def test_corrupt_file_is_moved_aside(self) -> None:
        self.path.write_text("{not-json", encoding="utf-8")
        self.assertEqual(read_json(self.path, {"ok": True}), {"ok": True})
        self.assertFalse(self.path.exists())
        backups = list(self.path.parent.glob("state.json.corrupt-*"))
        self.assertEqual(len(backups), 1)

    def test_update_json_is_serialised(self) -> None:
        atomic_write_json(self.path, {"n": 0})

        def bump(data):
            data["n"] += 1

        for _ in range(5):
            update_json(self.path, bump, default={"n": 0})
        self.assertEqual(read_json(self.path, {})["n"], 5)
        # Lock file is advisory and may remain on disk, but must not leak data.
        self.assertTrue((self.path.parent / "state.json.lock").exists())

    def test_file_lock_is_reentrant_free(self) -> None:
        with file_lock(self.path):
            pass
        with file_lock(self.path):
            pass

    def test_queue_style_append_under_lock(self) -> None:
        """模拟 queue submit：两次 mutate 不丢条目。"""
        atomic_write_json(self.path, {"workloads": []})

        def append_one(data):
            data.setdefault("workloads", []).append({"id": f"wl-{len(data['workloads'])}"})

        update_json(self.path, append_one, default={"workloads": []})
        update_json(self.path, append_one, default={"workloads": []})
        rows = read_json(self.path, {})["workloads"]
        self.assertEqual([r["id"] for r in rows], ["wl-0", "wl-1"])

    def test_lease_style_conflict_detect(self) -> None:
        """模拟 lease acquire：同节点二次占用应被 mutator 看见。"""
        atomic_write_json(self.path, {"leases": [{"id": "a", "nodes": ["n1"]}]})
        conflicts: list[str] = []

        def try_acquire(data):
            occupied = {n for lease in data.get("leases", []) for n in lease.get("nodes", [])}
            hit = sorted({"n1"} & occupied)
            if hit:
                conflicts.extend(hit)
                return
            data.setdefault("leases", []).append({"id": "b", "nodes": ["n1"]})

        update_json(self.path, try_acquire, default={"leases": []})
        self.assertEqual(conflicts, ["n1"])
        self.assertEqual(len(read_json(self.path, {})["leases"]), 1)


if __name__ == "__main__":
    unittest.main()
