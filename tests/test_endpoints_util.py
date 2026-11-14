from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from endpoints_util import build_endpoint_catalog, resolve_node_ports  # noqa: E402


class EndpointTests(unittest.TestCase):
    def test_precedence_node_override(self) -> None:
        base, ports = resolve_node_ports(
            {"base_port": 9000, "ports_per_node": 2, "gpus_per_node": 8},
            defaults={"gpus_per_node": 4},
            api={"base_port": 8000, "ports_per_node": 1},
        )
        self.assertEqual(base, 9000)
        self.assertEqual(ports, [9000, 9001])

    def test_gpu_count_fallback(self) -> None:
        base, ports = resolve_node_ports({"gpus_per_node": 3}, api={"base_port": 7000})
        self.assertEqual(base, 7000)
        self.assertEqual(ports, [7000, 7001, 7002])

    def test_catalog_skips_control(self) -> None:
        nodes = {
            "defaults": {"gpus_per_node": 2},
            "nodes": {
                "control": {"ansible_host": "10.0.0.1", "role": "control", "gpus_per_node": 0},
                "gpu-1": {"ansible_host": "10.0.0.2", "gpus_per_node": 2, "labels": ["a100"]},
            },
        }
        catalog = build_endpoint_catalog(nodes, {"api": {"base_port": 8000, "health_path": "/health"}})
        self.assertEqual(catalog["count"], 2)
        self.assertIn("gpu-1", catalog["by_node"])
        self.assertNotIn("control", catalog["by_node"])
        self.assertEqual(catalog["urls"], ["http://10.0.0.2:8000", "http://10.0.0.2:8001"])
        self.assertTrue(all(ep["health"].endswith("/health") for ep in catalog["endpoints"]))


if __name__ == "__main__":
    unittest.main()
