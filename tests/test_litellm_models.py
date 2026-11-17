"""控制面 LiteLLM 模型管理：URL 校验、payload 与分组。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import server  # noqa: E402


class SafeBaseTests(unittest.TestCase):
    def test_accepts_private_node(self) -> None:
        self.assertEqual(
            server._safe_inference_base("http://10.166.195.21:30000/v1"),
            "http://10.166.195.21:30000/v1",
        )

    def test_rejects_metadata_and_bad_scheme(self) -> None:
        for value in (
            "http://169.254.169.254/v1",
            "http://metadata.google.internal/v1",
            "http://100.100.100.200/v1",
            "ftp://10.0.0.1/v1",
            "",
        ):
            with self.assertRaises(ValueError, msg=value):
                server._safe_inference_base(value)


class ModelPayloadTests(unittest.TestCase):
    def test_add_payload(self) -> None:
        captured = {}

        def fake(method, path, body=None, **kwargs):
            captured.update({"method": method, "path": path, "body": body})
            return {"model_id": "ui-my-model"}

        with patch.object(server, "_litellm_admin_request", side_effect=fake), patch.object(
            server, "_read_json", return_value={"gateway": {"litellm_model": "openai/decider"}}
        ):
            out = server.add_litellm_model(
                {
                    "model_name": "my-model",
                    "api_base": "http://10.0.0.1:30000/v1",
                    "key_env": "INFERENCE_API_KEY",
                }
            )
        self.assertEqual(out["model_id"], "ui-my-model")
        self.assertEqual(captured["path"], "/model/new")
        body = captured["body"]
        self.assertEqual(body["model_name"], "my-model")
        self.assertEqual(body["litellm_params"]["model"], "openai/decider")
        self.assertEqual(
            body["litellm_params"]["api_key"], "os.environ/INFERENCE_API_KEY"
        )

    def test_add_rejects_bad_fields(self) -> None:
        base = {"api_base": "http://10.0.0.1:30000/v1"}
        for extra in (
            {"model_name": "bad name"},
            {"model_name": "ok", "model": "anthropic/x"},
            {"model_name": "ok", "key_env": "lower"},
            {"model_name": "ok", "api_base": "http://169.254.169.254/v1"},
        ):
            with self.assertRaises(ValueError, msg=extra):
                server.add_litellm_model({**base, **extra})

    def test_models_grouping(self) -> None:
        fake = {
            "data": [
                {
                    "model_name": "pool",
                    "litellm_params": {"model": "openai/x", "api_base": "http://a/v1"},
                    "model_info": {"id": "a-gpu0", "db_model": False},
                },
                {
                    "model_name": "pool",
                    "litellm_params": {"model": "openai/x", "api_base": "http://b/v1"},
                    "model_info": {"id": "b-gpu0", "db_model": False},
                },
                {
                    "model_name": "alias",
                    "litellm_params": {"model": "openai/x", "api_base": "http://c/v1"},
                    "model_info": {"id": "ui-alias", "db_model": True},
                },
            ]
        }
        with patch.object(server, "_litellm_admin_request", return_value=fake):
            out = server.litellm_models_payload()
        by_name = {m["model_name"]: m for m in out["models"]}
        self.assertEqual(by_name["pool"]["deployments"], 2)
        self.assertFalse(by_name["pool"]["db_model"])
        self.assertTrue(by_name["alias"]["db_model"])
        self.assertEqual(by_name["alias"]["ids"], ["ui-alias"])


if __name__ == "__main__":
    unittest.main()
