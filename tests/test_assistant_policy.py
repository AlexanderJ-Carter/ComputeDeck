"""assistant_policy 与 MCP 工具列表对齐。"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _mcp_tool_names_from_source() -> list[str]:
    tree = ast.parse((ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("gc_"):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "tool":
                names.append(node.name)
                break
            if isinstance(dec, ast.Attribute) and dec.attr == "tool":
                names.append(node.name)
                break
    return names


class AssistantPolicyTests(unittest.TestCase):
    def test_every_mcp_tool_has_risk(self) -> None:
        from assistant_policy import TOOL_RISKS

        for name in _mcp_tool_names_from_source():
            self.assertIn(name, TOOL_RISKS, f"缺少风险分级: {name}")

    def test_mcp_example_files_exist(self) -> None:
        from assistant_policy import MCP_CLIENT_EXAMPLES

        for key, rel in MCP_CLIENT_EXAMPLES.items():
            path = ROOT / rel
            self.assertTrue(path.is_file(), f"{key} 示例缺失: {rel}")

    def test_policy_payload_shape(self) -> None:
        from assistant_policy import policy_payload

        payload = policy_payload()
        self.assertIn("teaching", payload)
        self.assertIn("read", payload["by_risk"])
        self.assertIn("destructive", payload["by_risk"])


if __name__ == "__main__":
    unittest.main()
