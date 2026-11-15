"""assistant_chat：配置就绪、保存密钥、未配置引导。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from assistant_chat import (  # noqa: E402
    _normalize_base_url,
    assistant_status,
    format_upstream_error,
    handle_chat,
    list_upstream_models,
    load_assistant_config,
    save_assistant_config,
)
import socket  # noqa: E402
import urllib.error  # noqa: E402


class TestAssistantChat(unittest.TestCase):
    def test_status_setup_when_empty(self) -> None:
        status = assistant_status({"control": {"assistant": {"enabled": True}}})
        self.assertFalse(status["ready"])
        self.assertEqual(status["mode"], "setup")
        self.assertTrue(any(a["id"] == "setup" for a in status["quick_actions"]))

    def test_handle_chat_requires_setup(self) -> None:
        out = handle_chat(
            messages=[{"role": "user", "content": "你好"}],
            context={"cluster": {}},
            runners={},
        )
        self.assertEqual(out["error"], "assistant_not_configured")
        self.assertTrue(out.get("open_settings"))

    def test_save_config_writes_secret(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cluster_path = root / "cluster.json"
            secret = root / "assistant_api_key"
            cluster_path.write_text(
                json.dumps(
                    {
                        "control": {
                            "assistant": {
                                "api_key_file": str(secret),
                                "api_key_env": "GPU_CONTROL_ASSISTANT_API_KEY_TEST_UNUSED",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("assistant_chat.cluster_file", return_value=cluster_path):
                status = save_assistant_config(
                    base_url="https://api.openai.com/v1",
                    model="gpt-4o-mini",
                    api_key="sk-test",
                )
            self.assertTrue(status["ready"])
            self.assertEqual(status["model"], "gpt-4o-mini")
            self.assertTrue(secret.is_file())
            self.assertEqual(secret.read_text(encoding="utf-8").strip(), "sk-test")
            saved = json.loads(cluster_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["control"]["assistant"]["base_url"], "https://api.openai.com/v1")

    def test_list_models_parses_ids(self) -> None:
        with patch(
            "assistant_chat._http_json",
            return_value={"data": [{"id": "b"}, {"id": "a"}, "c"]},
        ):
            out = list_upstream_models(
                base_url="https://example.test/v1",
                api_key="k",
                cluster={"control": {"assistant": {}}},
            )
        self.assertEqual(out["models"], ["a", "b", "c"])
        self.assertEqual(out["count"], 3)

    def test_normalize_appends_v1_for_openai(self) -> None:
        self.assertEqual(_normalize_base_url("https://api.openai.com", strict=True), "https://api.openai.com/v1")
        self.assertEqual(
            _normalize_base_url("api.openai.com/v1", strict=True),
            "https://api.openai.com/v1",
        )

    def test_format_dns_error(self) -> None:
        exc = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        info = format_upstream_error(exc, base_url="https://api.openai.com/v1")
        self.assertIn("DNS", info["error"])
        self.assertIn("控制面", info["hint"])

    def test_load_ready_without_key(self) -> None:
        cfg = load_assistant_config(
            {
                "control": {
                    "assistant": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:11434/v1",
                        "model": "llama3",
                        "api_key_env": "GPU_CONTROL_ASSISTANT_API_KEY_MISSING",
                        "api_key_file": "/tmp/gpu-control-assistant-key-missing",
                    }
                }
            }
        )
        self.assertTrue(cfg["ready"])
        self.assertFalse(cfg["api_key_configured"])


if __name__ == "__main__":
    unittest.main()
