from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import static_util  # noqa: E402


class ResolveHelpTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.static = Path(self.tmp.name)
        help_root = self.static / "help"
        (help_root / "手册").mkdir(parents=True)
        (help_root / "index.html").write_text("home", encoding="utf-8")
        (help_root / "手册" / "index.html").write_text("manual", encoding="utf-8")
        (self.static / "secret.txt").write_text("secret", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_root_and_missing_slash(self) -> None:
        for path in ("/help", "/help/"):
            with self.subTest(path=path):
                target = static_util.resolve_help_target(self.static, path)
                self.assertEqual(target, (self.static / "help" / "index.html").resolve())

    def test_percent_encoded_chinese_page(self) -> None:
        target = static_util.resolve_help_target(self.static, "/help/%E6%89%8B%E5%86%8C/")
        self.assertEqual(target, (self.static / "help" / "手册" / "index.html").resolve())

    def test_plain_chinese_page(self) -> None:
        target = static_util.resolve_help_target(self.static, "/help/手册/")
        self.assertEqual(target, (self.static / "help" / "手册" / "index.html").resolve())

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(static_util.resolve_help_target(self.static, "/help/nope/"))
        self.assertIsNone(static_util.resolve_help_target(self.static, "/help/%E4%B8%8D%E5%AD%98%E5%9C%A8/"))

    def test_traversal_is_rejected(self) -> None:
        for path in ("/help/../secret.txt", "/help/%2e%2e/secret.txt", "/help/%2E%2E%2Fsecret.txt"):
            with self.subTest(path=path):
                self.assertIsNone(static_util.resolve_help_target(self.static, path))


if __name__ == "__main__":
    unittest.main()
