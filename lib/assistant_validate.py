"""助手动作校验：模型不能凭空编造节点 / Job，也不能绕开离线与风险限制。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from assistant_tools import (
    confirm_level,
    risk_for_call,
    summary_for,
    tool_spec,
)

_SECRET_MARKERS = ("key", "token", "secret", "password", "master_key", "passwd")
_ACTION_ENUMS: dict[str, set[str]] = {
    "gc_diagnose": {"check", "repair"},
    "gc_gateway": {"status", "get", "render", "start", "stop"},
    "gc_job_logs": {"tail"},
    "gc_lease": {"list", "acquire", "release"},
    "gc_liveness": {"status", "check"},
    "gc_models": {"list", "show", "deps", "check", "verify", "paths", "pull"},
    "gc_node": {"render", "cordon", "uncordon", "add", "remove"},
    "gc_notices": {"list", "mark"},
    "gc_procs": {"list", "kill"},
    "gc_queue": {"list", "submit", "schedule", "cancel"},
    "gc_remote": {"status", "plan", "up", "down"},
    "gc_toolkits": {"list", "probe", "install"},
}


def args_hash(args: dict[str, Any]) -> str:
    raw = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def redact(value: Any) -> Any:
    """对配置里的密钥字段打码，用于确认卡。"""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SECRET_MARKERS):
                out[key] = "***"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _split_nodes(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        raw = [str(x) for x in value]
    else:
        raw = str(value).split(",")
    return [x.strip() for x in raw if x.strip()]


def _known_nodes(nodes: dict[str, Any]) -> set[str]:
    return {str(name) for name in (nodes or {})}


def _node_state(liveness_view: dict[str, Any] | None, name: str) -> str:
    node = ((liveness_view or {}).get("nodes") or {}).get(name) or {}
    return str(node.get("state") or "unknown")


def _target_details(
    names: list[str],
    *,
    nodes: dict[str, Any],
    liveness_view: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    out = []
    for name in names:
        meta = nodes.get(name) or {}
        live = ((liveness_view or {}).get("nodes") or {}).get(name) or {}
        out.append(
            {
                "name": name,
                "host": meta.get("ansible_host") or live.get("host"),
                "state": live.get("state") or "unknown",
                "last_seen": live.get("last_seen"),
                "snapshot_age_text": (live.get("snapshot") or {}).get("age_text"),
            }
        )
    return out


def _error(code: str, detail: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "error": code, "detail": detail, "args": args or {}}


def validate_call(
    name: str,
    raw_args: dict[str, Any] | None,
    *,
    nodes: dict[str, Any] | None = None,
    jobs: set[str] | None = None,
    liveness_view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """校验并规范化一次工具调用。

    :returns: ``{ok, args, risk, confirm_level, target, label, summary, warnings}``
        或 ``{ok: False, error, detail}``。
    """
    spec = tool_spec(name)
    if spec is None:
        return _error("unknown_tool", f"未注册的工具：{name}")
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return _error("bad_args", "工具参数必须是对象")
    args = dict(raw_args)
    known_nodes = _known_nodes(nodes or {})
    known_jobs = {str(x) for x in (jobs or set())}
    action = str(args.get("action") or "").strip().lower()
    if action:
        args["action"] = action
        allowed = _ACTION_ENUMS.get(name)
        if allowed and action not in allowed:
            return _error(
                "bad_action",
                f"{name} 的 action 须为 {', '.join(sorted(allowed))}",
                args,
            )

    # ---- 必填字段 -------------------------------------------------------
    def need(key: str, label: str | None = None) -> str | None:
        value = args.get(key)
        if value in (None, "") or (isinstance(value, str) and not value.strip()):
            return key
        return None

    required: list[tuple[str, str]] = []
    if name == "gc_deploy":
        required = [("job", "Job 名")]
    elif name == "gc_job_action":
        required = [("name", "Job 名"), ("action", "action")]
    elif name == "gc_job_logs":
        required = [("name", "Job 名")]
    elif name == "gc_free_node":
        required = [("node", "节点名")]
    elif name == "gc_exec":
        required = [("command", "命令"), ("node", "节点名")]
    elif name == "gc_put_config":
        required = [("kind", "kind"), ("data", "data")]
    elif name == "gc_queue":
        if action == "submit":
            required = [("job", "Job 名")]
        elif action == "cancel":
            required = [("workload_id", "workload_id")]
    elif name == "gc_lease":
        if action == "acquire":
            required = [("job", "Job 名"), ("nodes", "节点名")]
        elif action == "release" and not args.get("lease_id") and not args.get("nodes"):
            required = [("lease_id", "lease_id 或 nodes")]
    elif name == "gc_models":
        if action == "pull" and not args.get("name") and not args.get("model"):
            required = [("name", "仓库 id 或 model")]
    elif name == "gc_node":
        if action == "render":
            required = []
        elif action == "add":
            required = [("name", "节点名"), ("host", "主机地址")]
        else:
            required = [("name", "节点名")]
    elif name == "gc_remote":
        if action in {"up", "down"}:
            required = [("mode", "接入模式")]
    for key, label in required:
        if need(key):
            return _error("missing_arg", f"缺少 {label}（{key}）", args)

    # ---- 节点校验 -------------------------------------------------------
    target_nodes: list[str] = []
    if name in {"gc_deploy", "gc_job_action", "gc_models", "gc_toolkits", "gc_queue"}:
        target_nodes += _split_nodes(args.get("nodes"))
    if name in {"gc_free_node", "gc_procs", "gc_exec"}:
        target_nodes += _split_nodes(args.get("node"))
    if name == "gc_node" and action in {"cordon", "uncordon", "remove", "add"}:
        if args.get("name"):
            target_nodes.append(str(args["name"]))
    target_nodes = list(dict.fromkeys(target_nodes))

    if name == "gc_node" and action == "add":
        add_name = str(args.get("name") or "")
        if add_name in known_nodes:
            return _error("node_exists", f"节点 {add_name} 已存在", args)
    else:
        for node in target_nodes:
            if node == "all":
                if risk_for_call(name, args) != "read":
                    return _error("ambiguous_target", "写/破坏性操作不允许 nodes=all，请指定节点", args)
                continue
            if known_nodes and node not in known_nodes:
                return _error("unknown_node", f"未知节点：{node}", args)

    # ---- Job 校验 -------------------------------------------------------
    target_jobs: list[str] = []
    if name in {"gc_deploy", "gc_job_action", "gc_job_logs"}:
        job = str(args.get("job") or args.get("name") or "")
        if job:
            target_jobs.append(job)
    if name in {"gc_lease", "gc_queue"}:
        job = str(args.get("job") or "")
        if job:
            target_jobs.append(job)
    target_jobs = list(dict.fromkeys(target_jobs))
    for job in target_jobs:
        if known_jobs and job not in known_jobs:
            return _error("unknown_job", f"未知 Job：{job}", args)

    # ---- 破坏性 / 离线限制 ---------------------------------------------
    warnings: list[str] = []
    destructive = risk_for_call(name, args) == "destructive"
    dry_run = bool(args.get("dry_run"))
    offline = [
        node
        for node in target_nodes
        if node != "all" and _node_state(liveness_view, node) == "offline"
    ]
    if offline and not dry_run and name != "gc_node":
        warnings.append(f"目标节点离线：{', '.join(offline)}；操作可能失败。")
        if name in {"gc_free_node", "gc_exec", "gc_procs", "gc_deploy", "gc_job_action"}:
            return _error(
                "offline_target",
                f"目标节点离线：{', '.join(offline)}。请先恢复节点，或只做 cordon / dry-run。",
                args,
            )
    if name == "gc_node" and action == "remove" and offline:
        warnings.append(f"节点离线：{', '.join(offline)}；删除配置不会影响机器本身。")

    if name == "gc_exec":
        if str(args.get("nodes") or "").strip().lower() == "all":
            return _error("ambiguous_target", "gc_exec 禁止 nodes=all", args)
        if not str(args.get("node") or "").strip():
            return _error("missing_arg", "gc_exec 必须指定单个 node", args)
        command = str(args.get("command") or "")
        if "\x00" in command:
            return _error("bad_args", "命令包含 NUL 字符", args)
        destructive = True

    if name == "gc_procs" and action == "kill":
        if not args.get("all") and not args.get("pids") and not args.get("match"):
            return _error("missing_arg", "gc_procs kill 需要 all / pids / match 之一", args)
        destructive = True

    display_args = args
    if name == "gc_put_config":
        if not isinstance(args.get("data"), dict):
            return _error("bad_args", "data 必须是对象", args)
        display_args = {**args, "data": redact(args["data"])}

    if name == "gc_models" and action == "pull":
        warnings.append("拉取会占用磁盘与网络，可能持续数分钟。")
    if name == "gc_resources_refresh":
        warnings.append("Ansible 采集可能持续数十秒；离线节点会被标记为快照过期。")

    # ---- 生成确认卡 -----------------------------------------------------
    target = {
        "nodes": [n for n in target_nodes if n != "all"],
        "jobs": target_jobs,
        "node_details": _target_details(
            [n for n in target_nodes if n != "all"],
            nodes=nodes or {},
            liveness_view=liveness_view,
        ),
    }
    card = summary_for(name, args, target)
    risk = risk_for_call(name, args)
    level = confirm_level(name, args)
    if destructive and level != "confirm_twice":
        level = "confirm_twice"
    return {
        "ok": True,
        "tool": name,
        "args": args,
        "display_args": display_args,
        "args_hash": args_hash(args),
        "risk": risk,
        "confirm_level": level,
        "target": target,
        "label": card["label"],
        "summary": card["summary"],
        "warnings": warnings,
    }
