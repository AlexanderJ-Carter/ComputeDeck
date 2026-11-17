"""助手工具注册表、调用级风险与目标校验。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from assistant_tools import public_specs, risk_for_call, route_for, tool_names  # noqa: E402
from assistant_validate import validate_call  # noqa: E402

NODES = {"a": {"ansible_host": "10.0.0.1"}, "b": {"ansible_host": "10.0.0.2"}}
JOBS = {"vllm", "decider"}
LIVE = {
    "nodes": {
        "a": {"name": "a", "state": "online", "last_seen": "2026-11-17T12:00:00Z", "snapshot": {"age_text": "10s"}},
        "b": {"name": "b", "state": "offline", "last_seen": "2026-11-16T12:00:00Z", "snapshot": {"age_text": "1d"}},
    }
}


class RiskTests(unittest.TestCase):
    def test_public_specs_include_all_tools(self) -> None:
        names = [row["function"]["name"] for row in public_specs()]
        self.assertEqual(sorted(names), sorted(tool_names()))
        self.assertIn("gc_deploy", names)
        self.assertIn("gc_free_node", names)
        self.assertIn("gc_remote", names)

    def test_action_level_risk(self) -> None:
        self.assertEqual(risk_for_call("gc_queue", {"action": "list"}), "read")
        self.assertEqual(risk_for_call("gc_queue", {"action": "submit", "job": "vllm"}), "write")
        self.assertEqual(risk_for_call("gc_lease", {"action": "list"}), "read")
        self.assertEqual(risk_for_call("gc_lease", {"action": "release", "lease_id": "x"}), "write")
        self.assertEqual(risk_for_call("gc_gateway", {"action": "status"}), "read")
        self.assertEqual(risk_for_call("gc_gateway", {"action": "stop"}), "write")
        self.assertEqual(risk_for_call("gc_liveness", {"action": "check"}), "read")
        self.assertEqual(risk_for_call("gc_free_node", {"node": "a"}), "destructive")
        self.assertEqual(risk_for_call("gc_exec", {"command": "uptime", "node": "a"}), "destructive")


class RouteTests(unittest.TestCase):
    def test_routes_map_to_existing_api(self) -> None:
        self.assertEqual(route_for("gc_deploy", {"job": "vllm"}), {
            "method": "POST",
            "path": "/api/deploy",
            "body": {"job": "vllm"},
        })
        self.assertEqual(route_for("gc_node", {"action": "cordon", "name": "a"})["path"], "/api/node/cordon")
        self.assertEqual(route_for("gc_node", {"action": "render"})["path"], "/api/setup/render")
        self.assertEqual(route_for("gc_queue", {"action": "cancel", "workload_id": "w1"})["path"], "/api/queue/w1/cancel")
        self.assertEqual(route_for("gc_lease", {"action": "release", "nodes": "a"})["path"], "/api/leases/release")
        self.assertEqual(route_for("gc_gateway", {"action": "status"})["method"], "GET")

    def test_write_tools_have_routes(self) -> None:
        sample = {"gc_put_config": {"kind": "cluster", "data": {}}}
        for row in public_specs():
            name = row["function"]["name"]
            if risk_for_call(name, {}) == "read":
                continue
            self.assertIsNotNone(
                route_for(name, sample.get(name, {})),
                f"{name} 是写工具但没有路由/执行器",
            )


class ValidationTests(unittest.TestCase):
    def test_unknown_node_rejected(self) -> None:
        out = validate_call("gc_free_node", {"node": "ghost"}, nodes=NODES, jobs=JOBS, liveness_view=LIVE)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unknown_node")

    def test_unknown_job_rejected(self) -> None:
        out = validate_call("gc_deploy", {"job": "nope"}, nodes=NODES, jobs=JOBS, liveness_view=LIVE)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unknown_job")

    def test_offline_target_rejected_for_destructive(self) -> None:
        out = validate_call("gc_free_node", {"node": "b"}, nodes=NODES, jobs=JOBS, liveness_view=LIVE)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "offline_target")
        # dry-run 允许
        dry = validate_call(
            "gc_free_node",
            {"node": "b", "dry_run": True},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertTrue(dry["ok"])
        self.assertEqual(dry["confirm_level"], "confirm_twice")

    def test_cordon_offline_allowed(self) -> None:
        out = validate_call(
            "gc_node",
            {"action": "cordon", "name": "b"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["risk"], "write")

    def test_exec_requires_single_node(self) -> None:
        out = validate_call(
            "gc_exec",
            {"command": "uptime", "nodes": "all"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertFalse(out["ok"])
        out = validate_call(
            "gc_exec",
            {"command": "uptime"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertFalse(out["ok"])

    def test_procs_kill_requires_selector(self) -> None:
        out = validate_call(
            "gc_procs",
            {"action": "kill", "node": "a"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertFalse(out["ok"])
        out = validate_call(
            "gc_procs",
            {"action": "kill", "node": "a", "all": True},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["confirm_level"], "confirm_twice")

    def test_put_config_redacts_display_but_keeps_exec_args(self) -> None:
        data = {"control": {"auth": {"password": "secret"}}, "api": {"token": "abc"}}
        out = validate_call(
            "gc_put_config",
            {"kind": "cluster", "data": data},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["display_args"]["data"]["api"]["token"], "***")
        self.assertEqual(out["args"]["data"]["api"]["token"], "abc")
        self.assertEqual(out["confirm_level"], "confirm_twice")

    def test_queue_submit_and_cancel(self) -> None:
        out = validate_call(
            "gc_queue",
            {"action": "submit", "job": "vllm", "schedule": True},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["confirm_level"], "confirm")
        out = validate_call(
            "gc_queue",
            {"action": "cancel"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertFalse(out["ok"])

    def test_job_action_requires_action(self) -> None:
        out = validate_call(
            "gc_job_action",
            {"name": "vllm"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "missing_arg")

    def test_node_add_must_be_new(self) -> None:
        out = validate_call(
            "gc_node",
            {"action": "add", "name": "a", "host": "10.0.0.9"},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "node_exists")
        out = validate_call(
            "gc_node",
            {"action": "add", "name": "c", "host": "10.0.0.9", "gpus": 4},
            nodes=NODES,
            jobs=JOBS,
            liveness_view=LIVE,
        )
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
