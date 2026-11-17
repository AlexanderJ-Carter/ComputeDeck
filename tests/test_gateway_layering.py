"""入口分层：LiteLLM 经 Edge、重复节点 LB 检测与 nginx 前端。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import render_gateway as rg  # noqa: E402


def _pool(**extra):
    pool = {
        "model_name": "gpu-pool",
        "litellm_model": "openai/decider",
        "openai_api_base_path": "/v1",
        "auth_env": "INFERENCE_API_KEY",
        "routing_strategy": "least-busy",
        "edge_url": "http://127.0.0.1:18080",
        "litellm_api_base": "http://127.0.0.1:18080/v1",
        "litellm_deployment_id": "edge-lb",
    }
    pool.update(extra)
    return pool


UPSTREAMS = [
    {"node": "deepseek-1", "gpu": 0, "url": "http://10.0.0.1:30000"},
    {"node": "deepseek-2", "gpu": 0, "url": "http://10.0.0.2:30000"},
]


class LiteLLMLayeringTests(unittest.TestCase):
    def test_edge_mode_single_deployment(self) -> None:
        yaml = rg.render_litellm_yaml(UPSTREAMS, _pool(litellm_upstream="edge"))
        self.assertEqual(yaml.count("- model_name:"), 1)
        self.assertIn("api_base: http://127.0.0.1:18080/v1", yaml)
        self.assertNotIn("10.0.0.1", yaml)
        self.assertNotIn("10.0.0.2", yaml)
        self.assertIn("num_retries: 0", yaml)

    def test_nodes_mode_keeps_per_node(self) -> None:
        yaml = rg.render_litellm_yaml(UPSTREAMS, _pool(litellm_upstream="nodes"))
        self.assertEqual(yaml.count("- model_name:"), 2)
        self.assertIn("10.0.0.1:30000", yaml)
        self.assertIn("num_retries: 2", yaml)


class LayeringDetectionTests(unittest.TestCase):
    def _topo(self, litellm_upstream: str):
        cluster = {
            "default_job": "decider",
            "gateway": {
                "ingress": {"enabled": True, "kind": "edge", "port": 18080},
                "openai_proxy_dir": "/tmp/gateway",
                "litellm_upstream": litellm_upstream,
                "model_name": "gpu-pool",
                "litellm_model": "openai/decider",
            },
        }
        catalog = {"by_node": {}, "endpoints": [], "count": 0, "job": "decider"}
        return rg.build_topology(cluster, catalog, {})

    def test_duplicate_node_lb_detected(self) -> None:
        topo = self._topo("nodes")
        self.assertTrue(topo["layering"]["duplicate_node_lb"])
        self.assertFalse(topo["layering"]["single_node_lb"])
        self.assertEqual(topo["layering"]["node_lb_owner"], "litellm")

    def test_edge_upstream_single_layer(self) -> None:
        topo = self._topo("edge")
        self.assertFalse(topo["layering"]["duplicate_node_lb"])
        self.assertTrue(topo["layering"]["single_node_lb"])
        self.assertEqual(topo["layering"]["node_lb_owner"], "edge")
        self.assertTrue(
            str(topo["layers"]["ai_gateway"]["api_base"]).endswith("18080/v1")
        )

    def test_proxy_to_removes_duplicate_warning(self) -> None:
        cluster = {
            "default_job": "decider",
            "gateway": {
                "ingress": {
                    "enabled": True,
                    "kind": "edge",
                    "port": 18080,
                    "proxy_to": "http://127.0.0.1:4000",
                },
                "openai_proxy_dir": "/tmp/gateway",
                "litellm_upstream": "nodes",
                "model_name": "gpu-pool",
                "litellm_model": "openai/decider",
            },
        }
        topo = rg.build_topology(cluster, {"by_node": {}, "endpoints": [], "count": 0}, {})
        self.assertFalse(topo["layering"]["duplicate_node_lb"])
        self.assertTrue(topo["layering"]["edge_front"])
        self.assertEqual(topo["layers"]["edge_lb"]["upstream"], "litellm")
        self.assertIn("LiteLLM 是唯一节点 LB", topo["layering"]["summary"])


class NginxLayeringTests(unittest.TestCase):
    def test_edge_front_has_single_upstream(self) -> None:
        conf = rg.render_nginx(UPSTREAMS, listen=443, edge_url="http://127.0.0.1:18080")
        self.assertIn("server 127.0.0.1:18080;", conf)
        self.assertNotIn("least_conn", conf)
        self.assertNotIn("10.0.0.1", conf)
        self.assertIn("proxy_buffering off", conf)
        self.assertIn("/_gpu_control/health", conf)

    def test_nodes_mode_still_lbs(self) -> None:
        conf = rg.render_nginx(UPSTREAMS, listen=18080)
        self.assertIn("least_conn", conf)
        self.assertIn("10.0.0.1:30000", conf)

    def test_litellm_front_has_single_upstream(self) -> None:
        conf = rg.render_nginx(UPSTREAMS, listen=18080, litellm_url="http://127.0.0.1:4000")
        self.assertIn("server 127.0.0.1:4000;", conf)
        self.assertNotIn("10.0.0.1", conf)
        self.assertNotIn("least_conn", conf)
        self.assertIn("/health/readiness", conf)
        self.assertIn("LiteLLM owns the node LB", conf)


if __name__ == "__main__":
    unittest.main()
