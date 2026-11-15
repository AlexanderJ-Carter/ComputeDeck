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
import paths  # noqa: E402


class JobInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.local_jobs = Path(self.tmp.name) / "jobs"
        self.local_jobs.mkdir(parents=True)
        # list_job_files() 读 paths.LOCAL_JOBS_DIR，两处都要隔离，
        # 否则主控上已有的业务 Job 会让“推荐模板安装”测试误判为已安装。
        self.patches = [
            mock.patch.object(jobs_util, "LOCAL_JOBS_DIR", self.local_jobs),
            mock.patch.object(paths, "LOCAL_JOBS_DIR", self.local_jobs),
        ]
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

    def test_list_templates_marks_recommended(self) -> None:
        rows = {t["id"]: t for t in jobs_util.list_job_templates()}
        self.assertIn("vllm-serve", rows)
        self.assertTrue(rows["vllm-serve"]["recommended"])
        self.assertEqual(rows["vllm-serve"]["category"], "推理")
        self.assertIn("domain-job", rows)
        self.assertFalse(rows["domain-job"]["recommended"])

    def test_install_recommended_skips_existing(self) -> None:
        jobs_util.install_job_template("shell-job")
        result = jobs_util.install_recommended_templates()
        names = {item["name"] for item in result["installed"]}
        self.assertNotIn("shell-job", names)
        self.assertIn("shell-job", result["skipped"])
        self.assertIn("http-serve", names)

    def test_install_recommended_force_overwrites(self) -> None:
        jobs_util.install_job_template("shell-job")
        result = jobs_util.install_recommended_templates(force=True)
        names = {item["name"] for item in result["installed"]}
        self.assertIn("shell-job", names)
        self.assertEqual(result["skipped"], [])

    def test_validate_job_dict_process_requires_script(self) -> None:
        issues = jobs_util.validate_job_dict(
            {"name": "x", "runtime": "process", "python": "python3"},
            label="x.json",
        )
        self.assertTrue(any("script" in item for item in issues))

    def test_validate_job_dict_docker_requires_image(self) -> None:
        issues = jobs_util.validate_job_dict(
            {"name": "x", "runtime": "docker", "docker": {}},
            label="x.json",
        )
        self.assertTrue(any("image" in item for item in issues))

    def test_validate_job_dict_ok(self) -> None:
        self.assertEqual(
            jobs_util.validate_job_dict({
                "name": "ok",
                "runtime": "process",
                "python": "python3",
                "script": "/tmp/m.py",
            }),
            [],
        )

    def test_list_job_files_skips_appledouble(self) -> None:
        builtin = Path(self.tmp.name) / "builtin"
        builtin.mkdir()
        (builtin / "shell-job.json").write_text(
            json.dumps({
                "name": "shell-job",
                "runtime": "process",
                "python": "python3",
                "script": "/tmp/m.py",
                "actions": ["start", "stop", "status", "wait"],
            }),
            encoding="utf-8",
        )
        (builtin / "._shell-job.json").write_bytes(b"\x00\x05\x16\x07Mac OS X")
        with mock.patch.object(paths, "JOBS_DIR", builtin), mock.patch.object(
            paths, "LOCAL_JOBS_DIR", self.local_jobs
        ):
            names = [p.name for p in paths.list_job_files()]
        self.assertEqual(names, ["shell-job.json"])
        # 摘要加载也不应因 AppleDouble 崩掉
        with mock.patch.object(paths, "JOBS_DIR", builtin), mock.patch.object(
            paths, "LOCAL_JOBS_DIR", self.local_jobs
        ), mock.patch.object(jobs_util, "list_job_files", paths.list_job_files):
            rows = jobs_util.load_job_summaries()
        self.assertEqual([r["name"] for r in rows], ["shell-job"])


if __name__ == "__main__":
    unittest.main()
