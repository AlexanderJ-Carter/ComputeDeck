"""Edge 根路径兜底页。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ingress_edge",
    ROOT / "scripts" / "ingress_edge.py",
)
assert SPEC and SPEC.loader
# scripts/ingress_edge 依赖 _bootstrap；把 scripts 与 lib 放进 path。
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["ingress_edge"] = MOD
SPEC.loader.exec_module(MOD)


class EdgeLandingTests(unittest.TestCase):
    def test_landing_mentions_edge_not_nginx(self) -> None:
        html = MOD.landing_html(
            snap={"available": 2, "upstream_count": 4, "strategy": "round_robin"},
            listen="0.0.0.0:18080",
            public_base_url="http://10.0.0.1:18080",
        )
        self.assertIn("ComputeDeck", html)
        self.assertIn("统一推理入口", html)
        self.assertIn("不是 nginx", html)
        self.assertIn("/_gpu_control/health", html)
        self.assertIn("2/4", html)
        self.assertIn("http://10.0.0.1:18080/v1/chat/completions", html)

    def test_landing_empty_pool_hint(self) -> None:
        html = MOD.landing_html(snap={"available": 0, "upstream_count": 0}, listen="127.0.0.1:18080")
        self.assertIn("暂无可用上游", html)
        self.assertIn("没有健康上游", html)


if __name__ == "__main__":
    unittest.main()
