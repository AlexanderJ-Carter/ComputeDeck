from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_site import check  # noqa: E402


class CheckSiteTests(unittest.TestCase):
    def test_detects_missing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(
                '<a href="./docs/">docs</a><img src="assets/logo.svg">', encoding="utf-8"
            )
            (root / "docs").mkdir()
            (root / "docs" / "index.html").write_text("ok", encoding="utf-8")
            (root / "assets").mkdir()
            (root / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
            self.assertTrue(check(root)["ok"])
            (root / "assets" / "logo.svg").unlink()
            self.assertFalse(check(root)["ok"])


if __name__ == "__main__":
    unittest.main()
