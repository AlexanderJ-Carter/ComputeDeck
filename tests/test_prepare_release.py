"""prepare_release 版本折叠逻辑。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_release",
    ROOT / "scripts" / "prepare_release.py",
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["prepare_release"] = MOD
SPEC.loader.exec_module(MOD)


class PrepareReleaseTests(unittest.TestCase):
    def test_bump_patch(self) -> None:
        self.assertEqual(MOD._bump("0.21.0", "patch"), "0.21.1")
        self.assertEqual(MOD._bump("0.21.0", "minor"), "0.22.0")
        self.assertEqual(MOD._bump("0.21.0", "major"), "1.0.0")

    def test_fold_changelog(self) -> None:
        text = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Added\n\n"
            "- feature A\n\n"
            "### Changed\n\n"
            "- tweak\n\n"
            "---\n\n"
            "## [0.21.0] - 2026-11-01\n\n"
            "- old\n"
        )
        out = MOD._fold_changelog(text, "0.22.0", "2026-11-15")
        self.assertIn("## [0.22.0] - 2026-11-15", out)
        self.assertIn("- feature A", out)
        self.assertIn("## [Unreleased]", out)
        self.assertLess(out.index("## [Unreleased]"), out.index("## [0.22.0]"))
        self.assertIn("## [0.21.0]", out)


if __name__ == "__main__":
    unittest.main()
