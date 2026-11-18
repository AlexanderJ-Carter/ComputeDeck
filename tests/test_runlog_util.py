from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import runlog_util  # noqa: E402


class RunlogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.log = self.state / "runlog.jsonl"
        self.patches = [
            mock.patch.object(runlog_util, "STATE_DIR", self.state),
            mock.patch.object(runlog_util, "RUNLOG_FILE", self.log),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def test_append_and_list(self) -> None:
        runlog_util.append_runlog("cli", ok=True, summary="doctor")
        runlog_util.append_runlog("oneclick", ok=False, summary="repair")
        payload = runlog_util.list_runlog(limit=10)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["items"][0]["summary"], "repair")
        self.assertEqual(payload["skipped"], 0)

    def test_malformed_lines_are_skipped(self) -> None:
        runlog_util.append_runlog("cli", ok=True, summary="ok")
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write("{broken\n")
            fh.write("[]\n")
        payload = runlog_util.list_runlog(limit=10)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["skipped"], 2)

    def test_filter_kind(self) -> None:
        runlog_util.append_runlog("cli", ok=True, summary="a")
        runlog_util.append_runlog("oneclick", ok=True, summary="b")
        payload = runlog_util.list_runlog(limit=10, kind="oneclick")
        self.assertEqual([x["summary"] for x in payload["items"]], ["b"])

    def test_actor_and_request_id(self) -> None:
        runlog_util.set_audit_context(actor="session:admin", request_id="req_abc")
        entry = runlog_util.append_runlog("cli", ok=True, summary="deploy")
        self.assertEqual(entry["actor"], "session:admin")
        self.assertEqual(entry["request_id"], "req_abc")
        runlog_util.clear_audit_context()

    def test_clear_leaves_audit_row(self) -> None:
        runlog_util.append_runlog("cli", ok=True, summary="old")
        result = runlog_util.clear_runlog(actor="session:admin", source="api")
        self.assertTrue(result["ok"])
        payload = runlog_util.list_runlog(limit=10)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["kind"], "runlog_clear")
        self.assertEqual(payload["items"][0]["actor"], "session:admin")


if __name__ == "__main__":
    unittest.main()
