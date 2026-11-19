from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from plan_job import build_plan  # noqa: E402

NODES = {
    "defaults": {},
    "nodes": {
        "gpu-a": {"ansible_host": "10.0.0.1", "gpus_per_node": 4},
        "gpu-b": {"ansible_host": "10.0.0.2", "gpus_per_node": 4},
        "gpu-c": {"ansible_host": "10.0.0.3", "gpus_per_node": 8},
    },
}
JOB = {"name": "train-job", "distributed": True, "gpus_per_node": 4, "master_port": 29500}


class PlanTests(unittest.TestCase):
    def test_two_4gpu_nodes_join_as_8(self) -> None:
        plan = build_plan(requested_gpus=8, job=JOB, selected=["gpu-a", "gpu-b"], nodes_meta=NODES)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["selected_gpus"], 8)
        self.assertEqual(plan["distributed"]["formula"], "2 节点 × 4 进程 = 8 卡")
        self.assertEqual(plan["distributed"]["master_addr"], "10.0.0.1")
        self.assertEqual(plan["distributed"]["node_rank"], {"gpu-a": 0, "gpu-b": 1})
        self.assertEqual(plan["distributed"]["hostfile"], ["10.0.0.1 slots=4", "10.0.0.2 slots=4"])

    def test_mixed_gpu_counts_warns(self) -> None:
        plan = build_plan(requested_gpus=12, job=JOB, selected=["gpu-a", "gpu-c"], nodes_meta=NODES)
        self.assertTrue(any("卡数不一致" in w for w in plan["warnings"]))

    def test_non_distributed_multi_node_not_ok(self) -> None:
        job = {**JOB, "distributed": False}
        plan = build_plan(requested_gpus=8, job=job, selected=["gpu-a", "gpu-b"], nodes_meta=NODES)
        self.assertFalse(plan["ok"])
        self.assertTrue(any("distributed=true" in w for w in plan["warnings"]))

    def test_insufficient_capacity(self) -> None:
        plan = build_plan(requested_gpus=16, job=JOB, selected=["gpu-a", "gpu-b"], nodes_meta=NODES)
        self.assertFalse(plan["ok"])
        self.assertTrue(any("少于请求" in w for w in plan["warnings"]))


if __name__ == "__main__":
    unittest.main()
