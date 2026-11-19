from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from nginx_ops import apply_text, normalize_settings, status_for  # noqa: E402


class NormalizeSettingsTests(unittest.TestCase):
    def test_nested_and_flat_keys(self) -> None:
        nested = normalize_settings(
            {
                "nginx": {
                    "apply_path": "/etc/nginx/conf.d/computedeck.conf",
                    "test_cmd": "sudo nginx -t",
                    "reload_cmd": "sudo nginx -s reload",
                    "auto_sync": True,
                    "sync_interval_sec": 30,
                }
            }
        )
        self.assertEqual(nested["apply_path"], "/etc/nginx/conf.d/computedeck.conf")
        self.assertEqual(nested["test_cmd"], "sudo nginx -t")
        self.assertTrue(nested["auto_sync"])
        self.assertEqual(nested["interval_sec"], 30)

        flat = normalize_settings({"nginx_apply_path": "/tmp/x.conf", "nginx_sync_interval_sec": 8})
        self.assertEqual(flat["apply_path"], "/tmp/x.conf")
        self.assertEqual(flat["interval_sec"], 8)
        self.assertFalse(flat["auto_sync"])

    def test_interval_floor(self) -> None:
        settings = normalize_settings({"nginx": {"sync_interval_sec": 1}})
        self.assertEqual(settings["interval_sec"], 5)


class ApplyTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "nginx" / "computedeck.conf"
        self.settings = {
            "test_cmd": "true",
            "reload_cmd": "true",
            "backup": True,
            "timeout_sec": 5,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_write_validate_reload(self) -> None:
        result = apply_text(self.target, "server { listen 18080; }\n", self.settings)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["reloaded"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "server { listen 18080; }\n")

    def test_unchanged_does_not_reload(self) -> None:
        apply_text(self.target, "same\n", self.settings)
        result = apply_text(self.target, "same\n", self.settings)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["applied"])
        self.assertIsNone(result["reload"])

    def test_dry_run_keeps_file(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text("old\n", encoding="utf-8")
        result = apply_text(self.target, "new\n", self.settings, dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "old\n")

    def test_failed_test_rolls_back(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text("old\n", encoding="utf-8")
        settings = {**self.settings, "test_cmd": "false"}
        result = apply_text(self.target, "new\n", settings)
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "old\n")

    def test_failed_reload_keeps_valid_config(self) -> None:
        settings = {**self.settings, "reload_cmd": "false"}
        result = apply_text(self.target, "new\n", settings)
        self.assertFalse(result["ok"])
        self.assertTrue(result["applied"])
        self.assertFalse(result["reloaded"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "new\n")

    def test_backup_created(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text("old\n", encoding="utf-8")
        result = apply_text(self.target, "new\n", self.settings)
        self.assertTrue(result["backup"])
        self.assertTrue(Path(result["backup"]).is_file())

    def test_status_compares_rendered_text(self) -> None:
        apply_text(self.target, "new\n", self.settings)
        in_sync = status_for(self.target, "new\n")
        self.assertTrue(in_sync["in_sync"])
        drift = status_for(self.target, "changed\n")
        self.assertFalse(drift["in_sync"])


if __name__ == "__main__":
    unittest.main()
