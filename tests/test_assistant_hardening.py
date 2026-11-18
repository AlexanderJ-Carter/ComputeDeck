"""小助手产品硬化：会话 merge、历史尾部裁剪、过期与确认卡完整性。"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import assistant_chat  # noqa: E402
import assistant_session  # noqa: E402
import assistant_tools  # noqa: E402


class PendingMergeTests(unittest.TestCase):
    def test_terminal_status_wins_over_stale_pending(self) -> None:
        base = {
            "act_1": {"id": "act_1", "status": "executed", "tool": "gc_free_node"},
            "act_2": {"id": "act_2", "status": "pending", "tool": "gc_deploy"},
        }
        incoming = {
            "act_1": {"id": "act_1", "status": "pending", "tool": "gc_free_node"},
            "act_3": {"id": "act_3", "status": "pending", "tool": "gc_queue"},
        }
        merged = assistant_session.merge_pending(base, incoming)
        self.assertEqual(merged["act_1"]["status"], "executed")
        self.assertEqual(merged["act_2"]["status"], "pending")
        self.assertEqual(merged["act_3"]["status"], "pending")

    def test_executed_wins_over_stale_failed_same_timestamp(self) -> None:
        base = {"act_1": {"id": "act_1", "status": "executed", "updated_at": "2026-01-01T00:00:00Z"}}
        incoming = {"act_1": {"id": "act_1", "status": "failed", "updated_at": "2026-01-01T00:00:00Z"}}
        merged = assistant_session.merge_pending(base, incoming)
        self.assertEqual(merged["act_1"]["status"], "executed")

    def test_locked_update_preserves_executed_against_stale_save(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.object(assistant_session, "SESSION_DIR", Path(td)):
                cid = assistant_session.new_id()
                doc = assistant_session.load(cid)
                assistant_session.put_pending(
                    doc,
                    {"id": "act_1", "tool": "gc_free_node", "status": "pending"},
                )
                assistant_session.save(doc)

                stale = assistant_session.load(cid)

                def mark_executed(current: dict) -> dict:
                    assistant_session.update_pending(current, "act_1", status="executed")
                    return current

                assistant_session.update(cid, mark_executed)

                assistant_session.put_pending(
                    stale,
                    {"id": "act_1", "tool": "gc_free_node", "status": "pending"},
                )
                assistant_session.save_merged(stale)

                loaded = assistant_session.load(cid)
                self.assertEqual(
                    assistant_session.get_pending(loaded, "act_1")["status"],
                    "executed",
                )


class HistorySanitizeTests(unittest.TestCase):
    def test_keeps_latest_messages_under_char_budget(self) -> None:
        history = [
            {"role": "user", "content": "early " + ("x" * 2000)},
            {"role": "assistant", "content": "old reply " + ("y" * 2000)},
            {"role": "user", "content": "latest question"},
            {"role": "assistant", "content": "latest answer"},
        ]
        clean = assistant_chat._sanitize_history(history, max_messages=40, max_chars=2500)
        roles = [row["role"] for row in clean]
        self.assertEqual(roles[-2:], ["user", "assistant"])
        self.assertEqual(clean[-1]["content"], "latest answer")
        self.assertEqual(clean[-2]["content"], "latest question")

    def test_keeps_tool_call_pairing_when_trimming_from_tail(self) -> None:
        history = [
            {"role": "user", "content": "old " + ("z" * 3000)},
            {"role": "user", "content": "deploy"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "gc_deploy", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"deferred\":true}"},
        ]
        clean = assistant_chat._sanitize_history(history, max_messages=40, max_chars=2000)
        self.assertEqual([row["role"] for row in clean], ["user", "assistant", "tool"])
        self.assertEqual(clean[0]["content"], "deploy")


class ExpiryTests(unittest.TestCase):
    def test_expires_at_takes_priority(self) -> None:
        past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10))
        action = {"expires_at": past, "created_epoch": time.time()}
        self.assertTrue(assistant_session.action_expired(action))

    def test_missing_timestamps_treat_as_expired(self) -> None:
        self.assertTrue(assistant_session.action_expired({"id": "act_x", "status": "pending"}))

    def test_clean_expired_uses_expires_at(self) -> None:
        past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
        doc = {
            "pending": {
                "act_1": {"id": "act_1", "status": "pending", "expires_at": past},
            }
        }
        assistant_session.clean_expired_pending(doc)
        self.assertEqual(doc["pending"]["act_1"]["status"], "expired")


class PublicPendingTests(unittest.TestCase):
    def test_public_pending_includes_args_hash(self) -> None:
        public = assistant_session.public_pending(
            {
                "id": "act_1",
                "tool": "gc_deploy",
                "args_hash": "abc123",
                "status": "pending",
                "args": {"secret": "no"},
            }
        )
        self.assertEqual(public["args_hash"], "abc123")
        self.assertNotIn("args", public)


class PendingDedupTests(unittest.TestCase):
    def test_put_pending_reuses_active_signature(self) -> None:
        doc = assistant_session.load("conv_testdedup01")
        first = {
            "id": "act_old",
            "tool": "gc_exec",
            "args_hash": "hash_curl",
            "status": "pending",
            "label": "危险命令",
            "call_id": "call_1",
        }
        second = {
            "id": "act_new",
            "tool": "gc_exec",
            "args_hash": "hash_curl",
            "status": "pending",
            "label": "危险命令",
            "call_id": "call_2",
            "summary": "再次提议",
        }
        assistant_session.put_pending(doc, first)
        assistant_session.put_pending(doc, second)
        pending = doc["pending"]
        self.assertEqual(list(pending.keys()), ["act_old"])
        self.assertEqual(second["id"], "act_old")
        self.assertEqual(pending["act_old"]["call_id"], "call_2")
        self.assertEqual(pending["act_old"]["summary"], "再次提议")


class NodeAddValidationTests(unittest.TestCase):
    def test_node_add_requires_gpus(self) -> None:
        route = assistant_tools.route_for("gc_node", {"action": "add", "name": "n1", "host": "10.0.0.1"})
        self.assertIsNone(route)

    def test_node_add_with_gpus_routes(self) -> None:
        route = assistant_tools.route_for(
            "gc_node",
            {"action": "add", "name": "n1", "host": "10.0.0.1", "gpus": 8},
        )
        self.assertIsNotNone(route)
        self.assertEqual(route["body"]["gpus"], 8)


class StreamPendingPreservedTests(unittest.TestCase):
    def test_stream_error_after_pending_keeps_state(self) -> None:
        rounds = {"n": 0}

        def fake_stream(method, url, *, headers, body, timeout=60):
            idx = rounds["n"]
            rounds["n"] += 1
            if idx == 0:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_x",
                                        "function": {
                                            "name": "gc_deploy",
                                            "arguments": '{"job":"vllm","nodes":"a"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            else:
                raise TimeoutError("upstream timeout")

        state: dict = {}
        cfg = {
            "enabled": True,
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "api_key": "",
            "api_key_env": "X",
            "api_key_file": "X",
            "api_key_configured": False,
            "timeout_sec": 5,
            "ready": True,
        }
        with patch.object(assistant_chat, "load_assistant_config", return_value=cfg), patch.object(
            assistant_chat, "_http_stream", side_effect=fake_stream
        ):
            events = list(
                assistant_chat.stream_chat(
                    history=[{"role": "user", "content": "部署"}],
                    context={"cluster": {}, "jobs": [{"name": "vllm"}]},
                    runners={},
                    known_nodes={"a": {"ansible_host": "10.0.0.1"}},
                    known_jobs={"vllm"},
                    liveness_view={"nodes": {"a": {"state": "online"}}},
                    state=state,
                )
            )
        kinds = [event for event, _ in events]
        self.assertIn("tool", kinds)
        self.assertIn("error", kinds)
        self.assertEqual(len(state.get("pending_full") or []), 1)
        self.assertTrue(state.get("history"))


if __name__ == "__main__":
    unittest.main()
