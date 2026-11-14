from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import jobs_util  # noqa: E402


class JobInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.local_jobs = Path(self.tmp.name) / "jobs"
        self.local_jobs.mkdir(parents=True)
        self.patches = [mock.patch.object(jobs_util, "LOCAL_JOBS_DIR", self.local_jobs)]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def test_install_renames_env_and_runtime(self) -> None:
        result = jobs_util.install_job_template("train-job", name="sft")
        data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "sft")
        self.assertEqual(data["env"]["GPU_CONTROL_JOB_NAME"], "sft")
        self.assertIn("sft", data["env"]["GPU_CONTROL_JOB_RUNTIME"])
        self.assertNotIn("train-job", data["env"]["GPU_CONTROL_JOB_RUNTIME"])

    def test_install_defaults_to_template_name(self) -> None:
        result = jobs_util.install_job_template("shell-job")
        self.assertEqual(result["name"], "shell-job")


if __name__ == "__main__":
    unittest.main()
