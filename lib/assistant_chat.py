"""控制面小助手：OpenAI 兼容上游 + 无模型降级。

只读工具可自动执行；写/破坏性工具仅返回待确认建议，由 UI 调现有 /api。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from assistant_policy import TEACHING_SUMMARY, TOOL_RISKS, policy_payload, risk_for
from paths import cluster_file, expand_path

QuickAction = dict[str, Any]
ToolRunner = Callable[[dict[str, Any]], Any]


def _read_cluster() -> dict[str, Any]:
    path = cluster_file()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_assistant_config(cluster: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取 ``cluster.control.assistant`` 并解析密钥。"""
    data = cluster if cluster is not None else _read_cluster()
    control = data.get("control") if isinstance(data.get("control"), dict) else {}
    raw = control.get("assistant") if isinstance(control.get("assistant"), dict) else {}
    enabled = bool(raw.get("enabled", True))
    base_url = str(raw.get("base_url") or os.environ.get("GPU_CONTROL_ASSISTANT_BASE_URL") or "").rstrip("/")
    model = str(raw.get("model") or os.environ.get("GPU_CONTROL_ASSISTANT_MODEL") or "").strip()
    key_env = str(raw.get("api_key_env") or "GPU_CONTROL_ASSISTANT_API_KEY").strip()
    api_key = (os.environ.get(key_env) or "").strip()
    key_file = str(raw.get("api_key_file") or "").strip()
    if not api_key and key_file:
        path = expand_path(key_file)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if path.is_file():
            api_key = path.read_text(encoding="utf-8").strip()
    timeout_sec = int(raw.get("timeout_sec") or 60)
    return {
        "enabled": enabled,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "api_key_env": key_env,
        "api_key_configured": bool(api_key),
        "timeout_sec": max(5, min(timeout_sec, 180)),
        "ready": bool(enabled and base_url and model),
    }


def quick_actions() -> list[QuickAction]:
    """无模型或开场时的快捷动作。"""
    return [
        {
            "id": "overview",
            "label": "查看集群总览",
            "prompt": "现在集群状态怎么样？请根据 overview 简要说明。",
            "hint": "读 overview",
        },
        {
            "id": "doctor",
            "label": "跑一次自检",
            "prompt": "请运行 doctor 并总结有没有配置问题。",
            "hint": "读 doctor",
        },
        {
            "id": "jobs",
            "label": "有哪些 Job",
            "prompt": "当前安装了哪些 Job？默认 Job 是什么？",
            "hint": "列 Job",
        },
        {
            "id": "mcp",
            "label": "怎么接 MCP",
            "prompt": "怎样给 Cursor / Claude Code / Codex 接入 ComputeDeck MCP？",
            "hint": "讲解",
        },
        {
            "id": "free",
            "label": "如何腾空节点",
            "prompt": "腾空一张卡 / 一个节点的标准流程是什么？要注意什么？",
            "hint": "讲解",
        },
    ]


def assistant_status(cluster: dict[str, Any] | None = None) -> dict[str, Any]:
    """供 UI 展示助手是否可用。"""
    cfg = load_assistant_config(cluster)
    return {
        "enabled": cfg["enabled"],
        "ready": cfg["ready"],
        "model": cfg["model"] or None,
        "base_url": cfg["base_url"] or None,
        "api_key_configured": cfg["api_key_configured"],
        "mode": "llm" if cfg["ready"] else "fallback",
        "quick_actions": quick_actions(),
        "policy": policy_payload(),
    }


def _openai_tools() -> list[dict[str, Any]]:
    """暴露给模型的工具（与 gc_* 对齐的精简集）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "gc_overview",
                "description": "集群总览（节点、GPU、租约摘要）",
                "parameters": {
                    "type": "object",
                    "properties": {"refresh": {"type": "boolean"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_doctor",
                "description": "配置自检",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_get_jobs",
                "description": "列出已安装 Job",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_api_catalog",
                "description": "HTTP/CLI/MCP 能力目录摘要",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_lease",
                "description": "列出当前租约",
                "parameters": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["list"]}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_queue",
                "description": "查看队列",
                "parameters": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["list"]}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_gateway",
                "description": "统一入口状态",
                "parameters": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["status"]}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_deploy",
                "description": "建议部署 Job（需用户确认后由 UI 执行）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job": {"type": "string"},
                        "nodes": {"type": "string"},
                        "force": {"type": "boolean"},
                    },
                    "required": ["job"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_free_node",
                "description": "建议腾空节点（需用户确认）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string"},
                        "cordon": {"type": "boolean"},
                    },
                    "required": ["node"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gc_job_action",
                "description": "建议启停 Job（需用户确认）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "action": {"type": "string", "enum": ["start", "stop", "restart", "status"]},
                        "node": {"type": "string"},
                    },
                    "required": ["name", "action"],
                },
            },
        },
    ]


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _fallback_reply(user_text: str, context: dict[str, Any]) -> dict[str, Any]:
    text = (user_text or "").strip().lower()
    overview = context.get("overview") or {}
    jobs = context.get("jobs") or []
    lines: list[str] = []
    if any(k in text for k in ("overview", "总览", "状态", "健康", "集群")):
        health = overview.get("health") or overview.get("score") or overview.get("summary") or {}
        lines.append("当前未配置可用的 OpenAI 兼容上游，下面是控制面只读快照：")
        lines.append(f"- 节点数：{len(overview.get('nodes') or overview.get('workers') or [])}")
        if health:
            lines.append(f"- 健康摘要：{json.dumps(health, ensure_ascii=False)[:240]}")
        else:
            lines.append("- 可在看板点「刷新」或让我在配置模型后再查 overview。")
    elif any(k in text for k in ("doctor", "自检", "配置")):
        lines.append("自检请用看板「自检」或 CLI `gpu-control doctor`。")
        lines.append("改完 nodes/cluster 后记得 `node render` 再 doctor。")
    elif any(k in text for k in ("job", "任务", "模板")):
        names = [j.get("name") for j in jobs if isinstance(j, dict) and j.get("name")]
        if names:
            lines.append("已安装 Job：" + "、".join(names[:20]))
        else:
            lines.append("还没有读到 Job 列表。可执行 `gpu-control jobs install --recommended`。")
    elif any(k in text for k in ("mcp", "cursor", "claude", "codex", "agent")):
        lines.append(
            "MCP 入口是 `bin/gpu-control-mcp`。"
            "示例：`config/examples/mcp.cursor.json` · `mcp.claude-code.json` · `mcp.codex.toml` · `mcp.continue.json` · `mcp.windsurf.json`。"
            "设 `GPU_CONTROL_API_BASE` + `GPU_CONTROL_API_TOKEN` 即可给 Cursor / Claude Code / Codex 用。"
        )
    elif any(k in text for k in ("free", "腾空", "清卡", "cordon")):
        lines.append(
            "腾空：`gc_free_node` / `POST /api/free`（可带 cordon）。"
            "只隔离调度用 `gc_node(action='cordon')`。"
            "破坏性操作前先 overview 确认目标。"
        )
    else:
        lines.append(
            "助手上游未就绪，我仍可讲解与给快捷动作。"
            "在 `cluster.control.assistant` 配置 `base_url` + `model`（及 API Key）后可对话调只读工具。"
        )
        lines.append(TEACHING_SUMMARY)
    return {
        "ok": True,
        "mode": "fallback",
        "reply": "\n".join(lines),
        "quick_actions": quick_actions(),
        "pending_actions": [],
        "tool_traces": [],
    }


def _system_prompt(context: dict[str, Any]) -> str:
    snap = {
        "cluster_name": (context.get("cluster") or {}).get("name"),
        "default_job": (context.get("cluster") or {}).get("default_job"),
        "job_names": [
            j.get("name") for j in (context.get("jobs") or []) if isinstance(j, dict) and j.get("name")
        ][:30],
        "overview_light": context.get("overview_light"),
    }
    return (
        f"{TEACHING_SUMMARY}\n"
        "你是 ComputeDeck 控制面小助手。用简洁中文回答。"
        "需要实时数据时调用只读工具；写配置/部署/腾空等只提出建议，等待用户在 UI 确认。"
        "不要编造不存在的 Job 名或节点名。\n"
        f"当前快照：{json.dumps(snap, ensure_ascii=False)}"
    )


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in messages[-16:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip()
        content = str(row.get("content") or "").strip()
        if role not in {"user", "assistant", "system"} or not content:
            continue
        out.append({"role": role, "content": content[:4000]})
    return out


def handle_chat(
    *,
    messages: list[dict[str, Any]],
    context: dict[str, Any],
    runners: dict[str, ToolRunner],
    confirm: bool = False,
) -> dict[str, Any]:
    """处理一轮对话。

    :param messages: 前端历史消息
    :param context: overview/jobs/cluster 等只读上下文
    :param runners: 工具名 → 可调用执行器（仅只读应传入）
    :param confirm: 预留；破坏性执行仍由 UI 走原 API
    """
    del confirm  # UI 确认后走原有 /api，不在此直接破坏
    cfg = load_assistant_config(context.get("cluster"))
    normalized = _normalize_messages(messages)
    if not normalized:
        return {
            "ok": False,
            "error": "messages 不能为空",
            "mode": "fallback",
            "quick_actions": quick_actions(),
        }
    user_text = next((m["content"] for m in reversed(normalized) if m["role"] == "user"), "")
    if not cfg["ready"]:
        return _fallback_reply(user_text, context)

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(context)},
        *normalized,
    ]
    tool_traces: list[dict[str, Any]] = []
    pending_actions: list[dict[str, Any]] = []
    url = f"{cfg['base_url']}/chat/completions"

    try:
        for _ in range(3):
            payload = {
                "model": cfg["model"],
                "messages": openai_messages,
                "tools": _openai_tools(),
                "tool_choice": "auto",
                "temperature": 0.2,
            }
            data = _http_json(
                "POST",
                url,
                headers=headers,
                body=payload,
                timeout=cfg["timeout_sec"],
            )
            choice = ((data.get("choices") or [{}])[0]).get("message") or {}
            tool_calls = choice.get("tool_calls") or []
            if not tool_calls:
                reply = str(choice.get("content") or "").strip() or "（模型未返回文本）"
                return {
                    "ok": True,
                    "mode": "llm",
                    "reply": reply,
                    "quick_actions": quick_actions(),
                    "pending_actions": pending_actions,
                    "tool_traces": tool_traces,
                    "model": cfg["model"],
                }

            openai_messages.append(
                {
                    "role": "assistant",
                    "content": choice.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                risk = risk_for(name)
                call_id = str(call.get("id") or name)
                if risk != "read" or name not in runners:
                    pending_actions.append(
                        {
                            "tool": name,
                            "args": args,
                            "risk": risk,
                            "label": TOOL_RISKS.get(name, risk),
                        }
                    )
                    tool_result = {
                        "ok": False,
                        "deferred": True,
                        "message": "该操作需在 UI 确认后执行，尚未执行。",
                    }
                else:
                    try:
                        tool_result = runners[name](args if isinstance(args, dict) else {})
                    except Exception as exc:  # noqa: BLE001 — 工具失败回传模型
                        tool_result = {"ok": False, "error": str(exc)}
                tool_traces.append({"tool": name, "args": args, "risk": risk, "result": tool_result})
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(tool_result, ensure_ascii=False)[:12000],
                    }
                )
        return {
            "ok": True,
            "mode": "llm",
            "reply": "工具调用轮次过多，已停止。请缩小问题或直接用看板操作。",
            "quick_actions": quick_actions(),
            "pending_actions": pending_actions,
            "tool_traces": tool_traces,
            "model": cfg["model"],
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        fb = _fallback_reply(user_text, context)
        fb["warning"] = f"上游 HTTP {exc.code}：{detail}"
        return fb
    except Exception as exc:  # noqa: BLE001 — 上游不可用时降级
        fb = _fallback_reply(user_text, context)
        fb["warning"] = f"上游不可用：{exc}"
        return fb


def compact_overview(overview: dict[str, Any]) -> dict[str, Any]:
    """压缩 overview 供 system prompt / fallback。"""
    if not isinstance(overview, dict):
        return {}
    nodes = overview.get("nodes") or overview.get("workers") or []
    return {
        "health": overview.get("health") or overview.get("score"),
        "node_count": len(nodes) if isinstance(nodes, list) else None,
        "leases": overview.get("leases") or overview.get("lease_summary"),
        "gateway": overview.get("gateway") or overview.get("endpoints"),
    }
