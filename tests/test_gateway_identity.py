"""网关端口身份与日志尾窗口。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))
SPEC = importlib.util.spec_from_file_location("gateway_ops", ROOT / "scripts" / "gateway_ops.py")
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["gateway_ops"] = MOD
SPEC.loader.exec_module(MOD)


def _serve(payload: dict):
    body = json.dumps(payload).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


class GatewayIdentityTests(unittest.TestCase):
    def test_edge_health_matches_service_identity(self) -> None:
        server, port = _serve({"ok": True, "service": "gpu-control-edge"})
        try:
            self.assertTrue(MOD._edge_health_ok({"port": port}))
        finally:
            server.shutdown()
            server.server_close()

    def test_edge_health_rejects_other_listener(self) -> None:
        server, port = _serve({"ok": True, "service": "ssh-tunnel"})
        try:
            self.assertFalse(MOD._edge_health_ok({"port": port}))
        finally:
            server.shutdown()
            server.server_close()

    def test_edge_stale_pidfile_is_not_owned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "edge.pid"
            status_file = Path(td) / "edge.json"
            pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
            status_file.write_text("{}", encoding="utf-8")
            with (
                patch.object(MOD, "EDGE_PID", pid_file),
                patch.object(MOD, "EDGE_STATUS", status_file),
                patch.object(MOD, "_port_open", return_value=False),
            ):
                running, found, edge_status = MOD._edge_runtime(
                    {"bind": "127.0.0.1", "port": 18080}
                )
            self.assertFalse(running)
            self.assertIsNone(found)
            self.assertFalse(edge_status.get("pid_owned"))

    def test_stop_pidfile_keeps_file_when_kill_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "edge.pid"
            pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
            with patch.object(
                MOD, "terminate_pid", return_value={"ok": False, "error": "permission"}
            ):
                result = MOD._stop_pidfile(pid_file, "edge", expected=("ingress_edge.py",))
            self.assertFalse(result["ok"])
            self.assertTrue(pid_file.is_file())

    def test_stop_pidfile_clears_stale_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "edge.pid"
            pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
            with patch.object(
                MOD,
                "terminate_pid",
                return_value={"ok": False, "stale": True, "reason": "pid mismatch"},
            ):
                result = MOD._stop_pidfile(pid_file, "edge", expected=("ingress_edge.py",))
            self.assertFalse(result["ok"])
            self.assertFalse(pid_file.exists())

    def test_log_tail_reads_bounded_window(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "litellm.log"
            path.write_text("x" * 200_000 + "\nlast-1\nlast-2\n", encoding="utf-8")
            tail = MOD._log_tail(path, n=2, max_bytes=1024)
            self.assertIn("last-2", tail)
            self.assertLess(len(tail), 1200)


if __name__ == "__main__":
    unittest.main()
