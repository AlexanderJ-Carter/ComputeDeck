"""宽松 JSON 与写接口 Pydantic 模型。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from json_util import loads_object, loads_relaxed  # noqa: E402
from schemas import AuthTokenRequest, LiteLLMModelRequest  # noqa: E402
from assistant_chat import _parse_call_args  # noqa: E402


class JsonRelaxedTests(unittest.TestCase):
    def test_valid_json(self) -> None:
        self.assertEqual(loads_relaxed('{"a": 1}'), {"a": 1})

    def test_repairs_trailing_comma(self) -> None:
        self.assertEqual(loads_object('{"a": 1, "b": [1, 2,],}'), {"a": 1, "b": [1, 2]})

    def test_invalid_returns_default(self) -> None:
        self.assertEqual(loads_relaxed("not json", default={"ok": False}), {"ok": False})
        self.assertEqual(loads_object("not json"), {})

    def test_non_object(self) -> None:
        self.assertEqual(loads_object("[1, 2]"), {})

    def test_model_tool_args_are_repaired(self) -> None:
        self.assertEqual(_parse_call_args('{"node": "gpu-1",}'), {"node": "gpu-1"})
        self.assertEqual(_parse_call_args("bad"), {})


class SchemaTests(unittest.TestCase):
    def test_auth_token_action(self) -> None:
        self.assertEqual(AuthTokenRequest.model_validate({}).action, "issue")
        self.assertEqual(AuthTokenRequest.model_validate({"action": "clear"}).action, "clear")
        with self.assertRaises(ValidationError):
            AuthTokenRequest.model_validate({"action": "rotate"})

    def test_litellm_model_defaults(self) -> None:
        req = LiteLLMModelRequest.model_validate(
            {"model_name": "m", "api_base": "http://10.0.0.1:30000/v1"}
        )
        self.assertEqual(req.action, "add")
        self.assertEqual(req.key_env, "INFERENCE_API_KEY")
        with self.assertRaises(ValidationError):
            LiteLLMModelRequest.model_validate({"action": "drop"})


if __name__ == "__main__":
    unittest.main()
