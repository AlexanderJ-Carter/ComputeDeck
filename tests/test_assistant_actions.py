"""助手工具循环：只读自动执行、写操作生成确认卡、历史保留。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import assistant_chat  # noqa: E402
import assistant_session  # noqa: E402

CFG = {
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

NODES = {"a": {"ansible_host": "10.0.0.1"}}
JOBS = {"vllm"}


def _tool_reply(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
            }
        ]
    }


def _text_reply(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class AssistantLoopTests(unittest.TestCase):
    def _chat(self, replies: list[dict], *, runners=None, history=None, messages=None):
        calls = {"n": 0}

        def fake_http(method, url, *, headers, body=None, timeout=60):
            idx = calls["n"]
            calls["n"] += 1
            return replies[min(idx, len(replies) - 1)]

        with patch.object(assistant_chat, "load_assistant_config", return_value=CFG), patch.object(
            assistant_chat, "_http_json", side_effect=fake_http
        ):
            result = assistant_chat.handle_chat(
                messages=messages,
                history=history,
                context={"cluster": {}, "jobs": [{"name": "vllm"}]},
                runners=runners or {},
                known_nodes=NODES,
                known_jobs=JOBS,
                liveness_view={"nodes": {"a": {"state": "online"}}},
            )
        return result, calls["n"]

    def test_read_tool_auto_executes(self) -> None:
        seen = []

        def runner(args):
            seen.append(args)
            return {"ok": True, "workloads": []}

        result, n = self._chat(
            [_tool_reply("gc_queue", {"action": "list"}), _text_reply("队列为空。")],
            runners={"gc_queue": runner},
            messages=[{"role": "user", "content": "看队列"}],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "队列为空。")
        self.assertEqual(seen, [{"action": "list"}])
        self.assertEqual(result["pending_actions"], [])
        self.assertEqual(n, 2)
        self.assertEqual(result["tool_traces"][0]["risk"], "read")
        self.assertEqual(result["history"][-1], {"role": "assistant", "content": "队列为空。"})

    def test_write_tool_creates_pending_action(self) -> None:
        result, _ = self._chat(
            [
                _tool_reply("gc_deploy", {"job": "vllm", "nodes": "a"}),
                _text_reply("已生成部署确认卡。"),
            ],
            messages=[{"role": "user", "content": "部署 vllm"}],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["pending_actions"]), 1)
        action = result["pending_actions"][0]
        self.assertEqual(action["tool"], "gc_deploy")
        self.assertEqual(action["risk"], "write")
        self.assertIn("部署", action["label"])
        self.assertNotIn("args", action)
        self.assertEqual(len(result["pending_actions_full"]), 1)
        self.assertEqual(result["pending_actions_full"][0]["args"]["job"], "vllm")
        # assistant.tool_calls 与 tool 消息应保留在历史里
        roles = [row["role"] for row in result["history"]]
        self.assertIn("assistant", roles)
        self.assertIn("tool", roles)
        self.assertEqual(result["history"][-1]["role"], "assistant")
        self.assertEqual(result["history"][-1]["content"], "已生成部署确认卡。")

    def test_unknown_node_is_rejected_without_pending(self) -> None:
        result, _ = self._chat(
            [
                _tool_reply("gc_free_node", {"node": "ghost"}),
                _text_reply("没有这个节点。"),
            ],
            messages=[{"role": "user", "content": "腾空 ghost"}],
        )
        self.assertEqual(result["pending_actions"], [])
        trace = result["tool_traces"][0]
        self.assertEqual(trace["result"]["error"], "unknown_node")

    def test_offline_destructive_target_rejected(self) -> None:
        def fake_http(method, url, *, headers, body=None, timeout=60):
            if fake_http.n == 0:
                fake_http.n += 1
                return _tool_reply("gc_free_node", {"node": "a"})
            return _text_reply("节点离线，请先恢复。")

        fake_http.n = 0
        with patch.object(assistant_chat, "load_assistant_config", return_value=CFG), patch.object(
            assistant_chat, "_http_json", side_effect=fake_http
        ):
            result = assistant_chat.handle_chat(
                messages=[{"role": "user", "content": "腾空 a"}],
                context={"cluster": {}, "jobs": []},
                runners={},
                known_nodes=NODES,
                known_jobs=JOBS,
                liveness_view={"nodes": {"a": {"state": "offline"}}},
            )
        self.assertEqual(result["pending_actions"], [])
        self.assertEqual(result["tool_traces"][0]["result"]["error"], "offline_target")

    def test_history_roundtrip_preserves_tool_messages(self) -> None:
        history = [
            {"role": "user", "content": "看队列"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "gc_queue", "arguments": "{\"action\":\"list\"}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\":true}"},
        ]
        clean = assistant_chat._sanitize_history(history)
        self.assertEqual([row["role"] for row in clean], ["user", "assistant", "tool"])
        # 孤立 tool 消息应被丢弃
        clean2 = assistant_chat._sanitize_history([{"role": "tool", "tool_call_id": "x", "content": "{}"}])
        self.assertEqual(clean2, [])


class AssistantStreamTests(unittest.TestCase):
    def test_stream_delta_and_done(self) -> None:
        def fake_stream(method, url, *, headers, body, timeout=60):
            yield {"choices": [{"delta": {"content": "你好"}}]}
            yield {"choices": [{"delta": {"content": "，集群在线。"}}]}

        state: dict = {}
        with patch.object(assistant_chat, "load_assistant_config", return_value=CFG), patch.object(
            assistant_chat, "_http_stream", side_effect=fake_stream
        ):
            events = list(
                assistant_chat.stream_chat(
                    history=[{"role": "user", "content": "状态"}],
                    context={"cluster": {}, "jobs": []},
                    runners={},
                    state=state,
                )
            )
        kinds = [event for event, _ in events]
        self.assertEqual(kinds, ["delta", "delta", "done"])
        self.assertEqual(events[-1][1]["reply"], "你好，集群在线。")
        self.assertEqual(state["history"][-1]["role"], "assistant")
        self.assertEqual(state["history"][-1]["content"], "你好，集群在线。")

    def test_stream_tool_calls_create_pending(self) -> None:
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
                yield {"choices": [{"delta": {"content": "确认卡已生成。"}}]}

        state: dict = {}
        with patch.object(assistant_chat, "load_assistant_config", return_value=CFG), patch.object(
            assistant_chat, "_http_stream", side_effect=fake_stream
        ):
            events = list(
                assistant_chat.stream_chat(
                    history=[{"role": "user", "content": "部署"}],
                    context={"cluster": {}, "jobs": [{"name": "vllm"}]},
                    runners={},
                    known_nodes=NODES,
                    known_jobs=JOBS,
                    liveness_view={"nodes": {"a": {"state": "online"}}},
                    state=state,
                )
            )
        kinds = [event for event, _ in events]
        self.assertIn("tool", kinds)
        done = events[-1][1]
        self.assertEqual(len(done["pending_actions"]), 1)
        self.assertEqual(done["reply"], "确认卡已生成。")
        self.assertEqual(state["pending_full"][0]["tool"], "gc_deploy")


class SessionTests(unittest.TestCase):
    def test_pending_roundtrip_and_redaction(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            with patch.object(assistant_session, "SESSION_DIR", Path(td)):
                cid = assistant_session.new_id()
                doc = assistant_session.load(cid)
                assistant_session.put_pending(
                    doc,
                    {
                        "id": "act_1",
                        "tool": "gc_put_config",
                        "args": {"data": {"token": "secret"}},
                        "display_args": {"data": {"token": "***"}},
                        "risk": "destructive",
                        "status": "pending",
                    },
                )
                assistant_session.save(doc)
                loaded = assistant_session.load(cid)
                action = assistant_session.get_pending(loaded, "act_1")
                self.assertIsNotNone(action)
                public = assistant_session.public_pending(action)
                self.assertNotIn("args", public)
                self.assertEqual(public["display_args"]["data"]["token"], "***")


if __name__ == "__main__":
    unittest.main()
