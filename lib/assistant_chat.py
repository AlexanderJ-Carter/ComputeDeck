"""控制面小助手：OpenAI 兼容上游（设置面板可配）+ 未配置时引导设置。

只读工具可自动执行；写/破坏性工具仅返回待确认建议，由 UI 调现有 /api。
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from assistant_policy import TEACHING_SUMMARY, TOOL_RISKS, policy_payload, risk_for
from paths import PROJECT_DIR, cluster_file, expand_path
from state_util import atomic_write_json

QuickAction = dict[str, Any]
ToolRunner = Callable[[dict[str, Any]], Any]

DEFAULT_API_KEY_FILE = "config/local/secrets/assistant_api_key"
DEFAULT_KEY_ENV = "GPU_CONTROL_ASSISTANT_API_KEY"
_OPENAI_COMPAT_ROOTS = frozenset({"api.openai.com", "api.groq.com", "api.deepseek.com"})


def _read_cluster() -> dict[str, Any]:
    path = cluster_file()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_key_file(raw_path: str) -> Path:
    path = Path(expand_path(raw_path or DEFAULT_API_KEY_FILE))
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()
    return path


def _read_api_key(key_env: str, key_file: str) -> str:
    api_key = (os.environ.get(key_env) or "").strip()
    if api_key:
        return api_key
    path = _resolve_key_file(key_file)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _normalize_base_url(value: str, *, strict: bool = False) -> str:
    """规范化 OpenAI 兼容根 URL。

    :param strict: 为真时校验 scheme/host；非法则 ``ValueError``。
    """
    url = str(value or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if strict:
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Base URL 须为 http:// 或 https://")
        if not host:
            raise ValueError("Base URL 缺少主机名（例如 https://api.openai.com/v1）")
    path = (parsed.path or "").rstrip("/")
    if host in _OPENAI_COMPAT_ROOTS and path in {"", "/"}:
        url = f"{parsed.scheme}://{parsed.netloc}/v1"
    return url.rstrip("/")


def format_upstream_error(exc: BaseException, *, base_url: str = "") -> dict[str, str]:
    """把 urllib / 网络异常转成面向用户的 ``error`` + ``hint``。"""
    host = ""
    if base_url:
        host = (urlparse(base_url).hostname or "").strip()
    reason = getattr(exc, "reason", None)
    text = str(exc)
    # DNS
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "nodename nor servname" in text:
        return {
            "error": f"无法解析域名{f' {host}' if host else ''}（DNS）",
            "hint": (
                "请求由**控制面主机**发出，不是你的笔记本。"
                "内网机器常解析不了 api.openai.com：请改用内网可达的 OpenAI 兼容网关 Base URL，"
                "或在控制面配置可解析外网的 DNS / HTTP 代理（环境变量 HTTPS_PROXY）。"
            ),
        }
    if isinstance(reason, TimeoutError) or "timed out" in text.lower():
        return {
            "error": f"连接上游超时{f'（{host}）' if host else ''}",
            "hint": "检查 Base URL、防火墙与控制面出网；可先点「连通探测」。",
        }
    if isinstance(reason, ConnectionRefusedError) or "Connection refused" in text:
        return {
            "error": f"上游拒绝连接{f'（{host}）' if host else ''}",
            "hint": "确认兼容服务已在该地址监听，且路径含 /v1（若适用）。",
        }
    if isinstance(exc, urllib.error.HTTPError):
        return {
            "error": f"上游 HTTP {exc.code}",
            "hint": "核对 API Key、模型名与 Base URL 是否匹配该网关。",
        }
    if isinstance(exc, urllib.error.URLError):
        return {
            "error": f"无法连接上游{f' {host}' if host else ''}",
            "hint": str(reason or text)[:300],
        }
    return {"error": text[:400], "hint": "请检查 Base URL / 网络后重试。"}


def load_assistant_config(cluster: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取 ``cluster.control.assistant`` 并解析密钥。"""
    data = cluster if cluster is not None else _read_cluster()
    control = data.get("control") if isinstance(data.get("control"), dict) else {}
    raw = control.get("assistant") if isinstance(control.get("assistant"), dict) else {}
    enabled = bool(raw.get("enabled", True))
    base_url = _normalize_base_url(
        str(raw.get("base_url") or os.environ.get("GPU_CONTROL_ASSISTANT_BASE_URL") or ""),
        strict=False,
    )
    model = str(raw.get("model") or os.environ.get("GPU_CONTROL_ASSISTANT_MODEL") or "").strip()
    key_env = str(raw.get("api_key_env") or DEFAULT_KEY_ENV).strip() or DEFAULT_KEY_ENV
    key_file = str(raw.get("api_key_file") or DEFAULT_API_KEY_FILE).strip() or DEFAULT_API_KEY_FILE
    api_key = _read_api_key(key_env, key_file)
    timeout_sec = int(raw.get("timeout_sec") or 60)
    ready = bool(enabled and base_url and model)
    return {
        "enabled": enabled,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "api_key_env": key_env,
        "api_key_file": key_file,
        "api_key_configured": bool(api_key),
        "timeout_sec": max(5, min(timeout_sec, 180)),
        "ready": ready,
    }


def quick_actions(*, ready: bool = True) -> list[QuickAction]:
    """开场快捷动作；未就绪时引导去设置。"""
    if not ready:
        return [
            {
                "id": "setup",
                "label": "去配置上游",
                "prompt": "__open_settings__",
                "hint": "打开设置",
            }
        ]
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
        "api_key_file": cfg["api_key_file"],
        "timeout_sec": cfg["timeout_sec"],
        "mode": "llm" if cfg["ready"] else "setup",
        "presets": [
            {
                "id": "custom",
                "label": "自定义 OpenAI 兼容（内网推荐）",
                "base_url": "",
            },
            {
                "id": "openai",
                "label": "OpenAI 官方（需控制面能解析外网）",
                "base_url": "https://api.openai.com/v1",
            },
        ],
        "network_note": (
            "拉取模型与对话由**控制面主机**访问 Base URL，不是浏览器本机。"
            "内网常见无法解析 api.openai.com：请填可达的兼容网关（含 /v1），或先点「连通探测」。"
        ),
        "quick_actions": quick_actions(ready=cfg["ready"]),
        "policy": policy_payload(),
        "setup_hint": None
        if cfg["ready"]
        else "请在助手设置中填写控制面可达的 Base URL 与模型；可先「连通探测」再拉取模型。",
    }


def save_assistant_config(
    *,
    base_url: str,
    model: str,
    enabled: bool = True,
    timeout_sec: int | None = None,
    api_key: str | None = None,
    clear_api_key: bool = False,
    cluster: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入 ``cluster.control.assistant``；密钥写入 secrets 文件（0600）。

    :param api_key: 非 ``None`` 时覆盖密钥文件；空字符串且 ``clear_api_key`` 为真则删除密钥。
    """
    data = cluster if cluster is not None else _read_cluster()
    if not isinstance(data, dict):
        raise ValueError("cluster.json 无效")
    control = data.setdefault("control", {})
    if not isinstance(control, dict):
        raise ValueError("cluster.control 须为对象")
    raw = control.get("assistant") if isinstance(control.get("assistant"), dict) else {}
    key_file = str(raw.get("api_key_file") or DEFAULT_API_KEY_FILE).strip() or DEFAULT_API_KEY_FILE
    key_env = str(raw.get("api_key_env") or DEFAULT_KEY_ENV).strip() or DEFAULT_KEY_ENV
    normalized_base = _normalize_base_url(base_url, strict=bool(str(base_url or "").strip()))
    normalized_model = str(model or "").strip()
    timeout = int(timeout_sec if timeout_sec is not None else raw.get("timeout_sec") or 60)
    timeout = max(5, min(timeout, 180))
    control["assistant"] = {
        "enabled": bool(enabled),
        "base_url": normalized_base,
        "model": normalized_model,
        "api_key_env": key_env,
        "api_key_file": key_file,
        "timeout_sec": timeout,
        "note": "OpenAI 兼容 Chat Completions；UI 设置面板可改",
    }
    path = cluster_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)

    secret_path = _resolve_key_file(key_file)
    if clear_api_key or api_key == "":
        if secret_path.is_file():
            secret_path.unlink()
    elif api_key is not None:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(api_key.strip() + "\n", encoding="utf-8")
        os.chmod(secret_path, 0o600)

    return assistant_status(data)


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


def list_upstream_models(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    cluster: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从 OpenAI 兼容 ``GET {base}/models`` 拉取模型 id 列表。"""
    cfg = load_assistant_config(cluster)
    raw_base = base_url if base_url is not None else cfg["base_url"]
    url_base = _normalize_base_url(raw_base, strict=True)
    if not url_base:
        raise ValueError("请先填写 Base URL（内网请填可达的兼容网关，例如 http://10.x.x.x:8000/v1）")
    key = (api_key if api_key is not None else cfg["api_key"]).strip()
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        data = _http_json(
            "GET",
            f"{url_base}/models",
            headers=headers,
            timeout=min(30, cfg["timeout_sec"]),
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        info = format_upstream_error(exc, base_url=url_base)
        raise ValueError(f"{info['error']}。{info['hint']}") from exc
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(
            f"上游 {url_base}/models 返回格式无法识别（需要 OpenAI 风格 {{data:[{{id}}]}}）。"
            "若网关路径不对，可试在 Base URL 末尾加 /v1。"
        )
    models: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            models.append(str(row["id"]))
        elif isinstance(row, str) and row.strip():
            models.append(row.strip())
    models = sorted(set(models), key=str.lower)
    return {
        "ok": True,
        "base_url": url_base,
        "models": models,
        "count": len(models),
        "selected": cfg["model"] if cfg["model"] in models else None,
    }


def probe_upstream(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    cluster: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """探测控制面到上游的 DNS / HTTP 可达性（``GET /models``）。"""
    cfg = load_assistant_config(cluster)
    raw_base = base_url if base_url is not None else cfg["base_url"]
    url_base = _normalize_base_url(raw_base, strict=True)
    if not url_base:
        raise ValueError("请先填写 Base URL")
    host = urlparse(url_base).hostname or ""
    dns_ok = False
    if host:
        try:
            socket.getaddrinfo(host, None)
            dns_ok = True
        except OSError as exc:
            info = format_upstream_error(exc, base_url=url_base)
            return {
                "ok": False,
                "base_url": url_base,
                "host": host,
                "dns_ok": False,
                "http_ok": False,
                "error": info["error"],
                "hint": info["hint"],
            }
    try:
        out = list_upstream_models(base_url=url_base, api_key=api_key, cluster=cluster)
    except ValueError as exc:
        return {
            "ok": False,
            "base_url": url_base,
            "host": host,
            "dns_ok": dns_ok,
            "http_ok": False,
            "error": str(exc),
            "hint": "DNS 已通但 HTTP 失败时，核对 Key、路径 /v1 与网关是否要求鉴权。",
        }
    return {
        "ok": True,
        "base_url": out["base_url"],
        "host": host,
        "dns_ok": True,
        "http_ok": True,
        "count": out["count"],
        "models_sample": (out["models"] or [])[:8],
        "hint": "控制面可达该上游，可拉取模型并保存。",
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


def _setup_required_reply() -> dict[str, Any]:
    """未配置上游时不再机械答题，引导打开设置。"""
    return {
        "ok": False,
        "error": "assistant_not_configured",
        "mode": "setup",
        "reply": (
            "还没有配置可用的 AI 上游。请点右上角「设置」，填写 Base URL（OpenAI 官方或任意兼容网关）"
            "与模型；需要密钥的上游再填 API Key。可点「拉取模型」后从下拉框选择。"
        ),
        "open_settings": True,
        "quick_actions": quick_actions(ready=False),
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
    if not cfg["ready"]:
        return _setup_required_reply()

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
                    "quick_actions": quick_actions(ready=True),
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
            "quick_actions": quick_actions(ready=True),
            "pending_actions": pending_actions,
            "tool_traces": tool_traces,
            "model": cfg["model"],
        }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        info = format_upstream_error(exc, base_url=cfg["base_url"])
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {
            "ok": False,
            "error": "upstream_unavailable",
            "mode": "llm",
            "reply": f"{info['error']}。{info['hint']}",
            "warning": detail or None,
            "quick_actions": quick_actions(ready=True),
            "pending_actions": [],
            "tool_traces": tool_traces,
            "open_settings": True,
        }
    except Exception as exc:  # noqa: BLE001 — 上游不可用时明确报错
        info = format_upstream_error(exc, base_url=cfg["base_url"])
        return {
            "ok": False,
            "error": "upstream_unavailable",
            "mode": "llm",
            "reply": f"{info['error']}。{info['hint']}",
            "quick_actions": quick_actions(ready=True),
            "pending_actions": [],
            "tool_traces": tool_traces,
            "open_settings": True,
        }


def compact_overview(overview: dict[str, Any]) -> dict[str, Any]:
    """压缩 overview 供 system prompt / fallback。"""
    if not isinstance(overview, dict):
        return {}
    nodes = overview.get("nodes") or overview.get("workers") or []
    quality = overview.get("quality") if isinstance(overview.get("quality"), dict) else {}
    return {
        "summary": overview.get("summary"),
        "quality_score": quality.get("score"),
        "node_count": len(nodes) if isinstance(nodes, list) else None,
        "watchdog": overview.get("watchdog"),
        "leases": overview.get("leases") or overview.get("lease_summary"),
        "ingress": overview.get("ingress"),
    }


def analyze_dashboard(
    *,
    overview: dict[str, Any],
    notices: list[dict[str, Any]],
    findings: list[dict[str, Any]] | None = None,
    cluster: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """只读解读看板状态（无工具调用）。"""
    cfg = load_assistant_config(cluster)
    if not cfg["ready"]:
        return _setup_required_reply()
    payload_snap = {
        "overview": compact_overview(overview),
        "notices": [
            {
                "severity": n.get("severity"),
                "title": n.get("title"),
                "detail": n.get("detail"),
                "source": n.get("source"),
            }
            for n in (notices or [])[:20]
            if isinstance(n, dict)
        ],
        "diagnose": [
            {
                "severity": f.get("severity"),
                "title": f.get("title"),
                "detail": f.get("detail"),
                "fix": f.get("fix"),
            }
            for f in (findings or [])[:20]
            if isinstance(f, dict)
        ],
    }
    system = (
        f"{TEACHING_SUMMARY}\n"
        "你是 ComputeDeck 监管助手。根据给定 JSON 快照用简洁中文输出："
        "1) 整体判断（健康/需关注）；2) 最重要的 3 条问题；3) 建议的下一步（只建议只读或确认后再执行的操作）。"
        "不要编造节点名或 Job 名；不要要求用户改仓库外的文件。"
    )
    user = (
        "请解读以下控制面快照并给出监管建议：\n"
        + json.dumps(payload_snap, ensure_ascii=False)[:14000]
    )
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        data = _http_json(
            "POST",
            f"{cfg['base_url']}/chat/completions",
            headers=headers,
            body={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            timeout=cfg["timeout_sec"],
        )
        choice = ((data.get("choices") or [{}])[0]).get("message") or {}
        reply = str(choice.get("content") or "").strip() or "（模型未返回文本）"
        return {
            "ok": True,
            "mode": "llm",
            "reply": reply,
            "model": cfg["model"],
        }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        info = format_upstream_error(exc, base_url=cfg["base_url"])
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {
            "ok": False,
            "error": "upstream_unavailable",
            "mode": "llm",
            "reply": f"{info['error']}。{info['hint']}",
            "warning": detail or None,
            "open_settings": True,
        }
    except Exception as exc:  # noqa: BLE001 — 上游失败明确返回
        info = format_upstream_error(exc, base_url=cfg["base_url"])
        return {
            "ok": False,
            "error": "upstream_unavailable",
            "mode": "llm",
            "reply": f"{info['error']}。{info['hint']}",
            "open_settings": True,
        }
