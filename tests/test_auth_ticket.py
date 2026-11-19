from __future__ import annotations

import sys
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from auth_util import consume_login_ticket, issue_login_ticket  # noqa: E402


class LoginTicketTests(unittest.TestCase):
    def test_single_use(self) -> None:
        code, ttl = issue_login_ticket(subject="admin", target="/#/assistant")
        self.assertGreaterEqual(ttl, 1)
        row = consume_login_ticket(code)
        self.assertEqual(row["subject"], "admin")
        self.assertEqual(row["target"], "/#/assistant")
        self.assertIsNone(consume_login_ticket(code))

    def test_expired(self) -> None:
        code, _ = issue_login_ticket(ttl=1)
        with mock.patch("auth_util.time.time", return_value=time.time() + 10):
            self.assertIsNone(consume_login_ticket(code))

    def test_rejects_external_redirect(self) -> None:
        code, _ = issue_login_ticket(target="https://evil.example/")
        row = consume_login_ticket(code)
        self.assertEqual(row["target"], "/")
        code, _ = issue_login_ticket(target="//evil.example/path")
        self.assertEqual(consume_login_ticket(code)["target"], "/")

    def test_empty_code(self) -> None:
        self.assertIsNone(consume_login_ticket(""))


if __name__ == "__main__":
    unittest.main()
