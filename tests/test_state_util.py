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


if __name__ == "__main__":
    unittest.main()
