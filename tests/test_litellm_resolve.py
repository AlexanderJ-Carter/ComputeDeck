"""LiteLLM 二进制解析与 phase 判定。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))
SPEC = importlib.util.spec_from_file_location("gateway_ops", ROOT / "scripts" / "gateway_ops.py")
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["gateway_ops"] = MOD
SPEC.loader.exec_module(MOD)


class LitellmResolveTests(unittest.TestCase):
    def test_resolve_home_local_when_which_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            bin_dir = home / ".local" / "bin"
            bin_dir.mkdir(parents=True)
            litellm = bin_dir / "litellm"
            litellm.write_text("#!/bin/sh\n", encoding="utf-8")
            litellm.chmod(0o755)
            with (
                patch.object(MOD.shutil, "which", return_value=None),
                patch.object(MOD.Path, "home", return_value=home),
            ):
                found = MOD._resolve_litellm_bin({})
            self.assertEqual(found, str(litellm))

    def test_resolve_explicit_litellm_bin(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "custom-litellm"
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)
            with patch.object(MOD.shutil, "which", return_value=None):
                found = MOD._resolve_litellm_bin({"litellm_bin": str(path)})
            self.assertEqual(found, str(path))

    def test_phase_running_requires_owned_process_and_listen(self) -> None:
        probe = {"ready": True}
        self.assertEqual(
            MOD._litellm_phase(probe=probe, pid=123, listen_ok=True, pid_owned=True),
            "running",
        )
        self.assertEqual(
            MOD._litellm_phase(probe=probe, pid=123, listen_ok=False, pid_owned=True),
            "starting",
        )
        self.assertEqual(
            MOD._litellm_phase(probe=probe, pid=None, listen_ok=False),
            "ready",
        )
        self.assertEqual(
            MOD._litellm_phase(probe={"ready": False}, pid=None, listen_ok=False),
            "missing",
        )

    def test_phase_does_not_trust_unknown_listener(self) -> None:
        # SSH 隧道 / 其他服务占端口时，不能当成 LiteLLM running。
        self.assertEqual(
            MOD._litellm_phase(
                probe={"ready": True}, pid=None, listen_ok=True, pid_owned=False
            ),
            "unmanaged",
        )
        self.assertEqual(
            MOD._litellm_phase(
                probe={"ready": False}, pid=None, listen_ok=True, pid_owned=False
            ),
            "port_conflict",
        )
        self.assertEqual(
            MOD._litellm_phase(
                probe={"ready": True}, pid=999, listen_ok=True, pid_owned=False
            ),
            "unmanaged",
        )

    def test_probe_prefers_binary_over_script_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proxy = Path(td) / "Gateway"
            proxy.mkdir()
            (proxy / "start_litellm.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (proxy / ".env").write_text("X=1\n", encoding="utf-8")
            fake_bin = Path(td) / "litellm"
            fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_bin.chmod(0o755)
            with patch.object(MOD, "_resolve_litellm_bin", return_value=str(fake_bin)):
                probe = MOD._litellm_probe({"openai_proxy_dir": str(proxy)})
            self.assertEqual(probe["mode"], "binary")
            self.assertTrue(probe["ready"])
            self.assertEqual(probe["binary"], str(fake_bin))


if __name__ == "__main__":
    unittest.main()
