"""校验 api_catalog 与 mcp_server 工具列表同步。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


class ApiCatalogMcpSyncTests(unittest.TestCase):
    def test_mcp_tools_summary_matches_server(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "lib"))
        from api_catalog import http_endpoints, mcp_tools_summary

        catalog_names = [row["name"] for row in mcp_tools_summary()]
        source_names = _mcp_tool_names_from_source()
        self.assertEqual(
            sorted(catalog_names),
            sorted(source_names),
            "mcp_tools_summary 与 mcp_server @mcp.tool 不一致",
        )
        self.assertEqual(
            len(catalog_names),
            len(set(catalog_names)),
            "mcp_tools_summary 有重复 name",
        )

    def test_http_endpoint_paths_unique_method_path(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "lib"))
        from api_catalog import http_endpoints

        seen: set[tuple[str, str]] = set()
        for ep in http_endpoints():
            key = (ep["method"], ep["path"])
            self.assertNotIn(key, seen, f"重复端点 {key}")
            seen.add(key)

    def test_body_keys_resolve(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "lib"))
        from api_catalog import _BODY, http_endpoints

        for ep in http_endpoints():
            key = ep.get("body")
            if key:
                self.assertIn(key, _BODY, f"{ep['method']} {ep['path']} body={key}")


if __name__ == "__main__":
    unittest.main()
