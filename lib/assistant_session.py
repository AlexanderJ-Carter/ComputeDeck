"""UI 助手会话与待确认动作的持久化（``state/assistant_sessions/``）。

服务端保存 OpenAI 形状的消息（含 assistant.tool_calls 与 role=tool），
执行结果回填后模型能看到完整历史，从而实现“提议 → 确认 → 执行 → 总结”。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import STATE_DIR
from state_util import atomic_write_json, read_json, update_json

SESSION_DIR = STATE_DIR / "assistant_sessions"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_MAX_SESSIONS = 200
_MAX_AGE_SEC = 7 * 86400
_MAX_MESSAGES = 120
_TERMINAL_PENDING = frozenset({"executed", "failed", "rejected", "expired"})
_ACTIVE_PENDING = frozenset({"pending", "armed", "executing"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: Any) -> float | None:
    """ISO8601 → epoch 秒；无法解析时返回 ``None``。"""
    if value is None or value == "":
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def new_id() -> str:
    return "conv_" + uuid.uuid4().hex[:12]


def valid_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _ID_RE.match(text) else None


def _path(conversation_id: str) -> Path:
    cid = valid_id(conversation_id)
    if not cid:
        raise ValueError("conversation_id 不合法")
    return SESSION_DIR / f"{cid}.json"


def _empty(conversation_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "version": 1,
        "conversation_id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "pending": {},
        "last_tool_result": None,
    }


def load(conversation_id: str) -> dict[str, Any]:
    """读取会话；不存在或损坏时返回空会话。"""
    cid = valid_id(conversation_id)
    if not cid:
        raise ValueError("conversation_id 不合法")
    path = _path(cid)
    if not path.is_file():
        return _empty(cid)
    data = read_json(path, _empty(cid))
    if not isinstance(data, dict):
        return _empty(cid)
    data.setdefault("messages", [])
    data.setdefault("pending", {})
    data["conversation_id"] = cid
    return data


def _prepare_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """规范化会话文档并裁剪消息。"""
    cid = valid_id((doc or {}).get("conversation_id"))
    if not cid:
        raise ValueError("conversation_id 不合法")
    prepared = dict(doc)
    prepared["conversation_id"] = cid
    prepared["updated_at"] = _now()
    messages = prepared.get("messages")
    if isinstance(messages, list) and len(messages) > _MAX_MESSAGES:
        prepared["messages"] = messages[-_MAX_MESSAGES:]
    prepared.setdefault("pending", {})
    return prepared


def merge_pending(base: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    """合并 pending：终态优先，避免 chat 用旧内存覆盖已执行确认卡。"""
    out: dict[str, Any] = {}
    for source in (base or {}, incoming or {}):
        if not isinstance(source, dict):
            continue
        for action_id, row in source.items():
            if not isinstance(row, dict):
                continue
            key = str(action_id)
            prev = out.get(key)
            if prev is None:
                out[key] = dict(row)
                continue
            prev_status = str(prev.get("status") or "pending")
            next_status = str(row.get("status") or "pending")
            if prev_status in _TERMINAL_PENDING and next_status in _ACTIVE_PENDING:
                continue
            if next_status in _TERMINAL_PENDING and prev_status in _ACTIVE_PENDING:
                out[key] = dict(row)
                continue
            if prev_status in _TERMINAL_PENDING and next_status in _TERMINAL_PENDING:
                # 同为终态时保留更新更晚的；无法比较则保留 executed 优先于其它终态。
                prev_ts = parse_time(prev.get("updated_at")) or parse_time(prev.get("executed_at")) or 0.0
                next_ts = parse_time(row.get("updated_at")) or parse_time(row.get("executed_at")) or 0.0
                if next_ts > prev_ts:
                    out[key] = dict(row)
                elif next_ts == prev_ts and next_status == "executed" and prev_status != "executed":
                    out[key] = dict(row)
                continue
            out[key] = dict(row)
    return out


def save(doc: dict[str, Any]) -> dict[str, Any]:
    """原子保存并裁剪会话（无锁；并发写请用 ``save_merged`` / ``update``）。"""
    prepared = _prepare_doc(doc)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(prepared["conversation_id"])
    atomic_write_json(path, prepared, mode=0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    prune()
    return prepared


def save_merged(doc: dict[str, Any]) -> dict[str, Any]:
    """带锁保存：磁盘上的终态 pending 优先于本次写入中的 stale pending。"""
    prepared = _prepare_doc(doc)
    cid = prepared["conversation_id"]
    path = _path(cid)

    def mutator(current: Any) -> dict[str, Any]:
        base = current if isinstance(current, dict) else _empty(cid)
        merged = dict(prepared)
        merged["pending"] = merge_pending(base.get("pending") or {}, prepared.get("pending") or {})
        if not merged.get("created_at"):
            merged["created_at"] = base.get("created_at") or _now()
        return _prepare_doc(merged)

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    result = update_json(path, mutator, default=_empty(cid))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    prune()
    return result


def update(conversation_id: str, mutator) -> dict[str, Any]:
    """跨进程锁下读改写会话。"""
    cid = valid_id(conversation_id)
    if not cid:
        raise ValueError("conversation_id 不合法")
    path = _path(cid)

    def _wrap(current: Any) -> dict[str, Any]:
        doc = current if isinstance(current, dict) else _empty(cid)
        doc.setdefault("messages", [])
        doc.setdefault("pending", {})
        doc["conversation_id"] = cid
        result = mutator(doc)
        prepared = _prepare_doc(doc if result is None else result)
        return prepared

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    result = update_json(path, _wrap, default=_empty(cid))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    prune()
    return result


def prune() -> None:
    """删除过期会话；异常不影响主流程。"""
    if not SESSION_DIR.is_dir():
        return
    cutoff = time.time() - _MAX_AGE_SEC
    files = sorted(
        SESSION_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
    )
    for path in files:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
    # 超过上限时删最旧
    files = sorted(
        SESSION_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
    )
    for path in files[: max(0, len(files) - _MAX_SESSIONS)]:
        try:
            path.unlink()
        except OSError:
            pass


def append_message(doc: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    messages = doc.setdefault("messages", [])
    if isinstance(messages, list):
        messages.append(message)
    return doc


def find_active_by_signature(
    doc: dict[str, Any],
    *,
    tool: str,
    args_hash: str,
) -> dict[str, Any] | None:
    """查找同工具 + args_hash 的活跃确认卡（pending/armed）。"""
    pending = doc.get("pending") or {}
    if not isinstance(pending, dict):
        return None
    tool_key = str(tool or "")
    hash_key = str(args_hash or "")
    if not tool_key or not hash_key:
        return None
    for row in pending.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "pending") not in {"pending", "armed"}:
            continue
        if str(row.get("tool") or "") == tool_key and str(row.get("args_hash") or "") == hash_key:
            return row
    return None


def put_pending(doc: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """写入确认卡；同 tool+args_hash 的活跃卡去重并复用已有 id。"""
    pending = doc.setdefault("pending", {})
    if not isinstance(pending, dict):
        return doc
    existing = find_active_by_signature(
        doc,
        tool=str(action.get("tool") or ""),
        args_hash=str(action.get("args_hash") or ""),
    )
    if existing is not None:
        # 复用已有卡：刷新 call_id / 展示字段，但保留原 id 与过期时间。
        keep_id = str(existing.get("id") or "")
        merged = dict(existing)
        for key in (
            "call_id",
            "display_args",
            "label",
            "summary",
            "target",
            "warnings",
            "risk",
            "confirm_level",
            "args",
        ):
            if key in action and action.get(key) is not None:
                merged[key] = action[key]
        merged["id"] = keep_id
        merged["updated_at"] = _now()
        pending[keep_id] = merged
        action.clear()
        action.update(merged)
        return doc
    pending[str(action["id"])] = action
    return doc


def get_pending(doc: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    pending = doc.get("pending") or {}
    row = pending.get(str(action_id)) if isinstance(pending, dict) else None
    return row if isinstance(row, dict) else None


def update_pending(doc: dict[str, Any], action_id: str, **fields: Any) -> dict[str, Any] | None:
    row = get_pending(doc, action_id)
    if row is None:
        return None
    row.update(fields)
    row["updated_at"] = _now()
    return row


def public_pending(action: dict[str, Any]) -> dict[str, Any]:
    """返回给前端的安全字段（不含完整 args 明文）。"""
    keys = (
        "id",
        "tool",
        "display_args",
        "args_hash",
        "risk",
        "confirm_level",
        "label",
        "summary",
        "target",
        "warnings",
        "call_id",
        "status",
        "created_at",
        "expires_at",
    )
    return {key: action.get(key) for key in keys if key in action}


def record_tool_result(
    doc: dict[str, Any],
    *,
    call_id: str,
    result: Any,
    limit: int = 12000,
) -> dict[str, Any]:
    payload = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
    return append_message(
        doc,
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": payload[:limit],
        },
    )


def clean_expired_pending(doc: dict[str, Any], *, ttl_sec: int = 900) -> dict[str, Any]:
    pending = doc.get("pending") or {}
    if not isinstance(pending, dict):
        return doc
    for action_id, row in list(pending.items()):
        if not isinstance(row, dict):
            pending.pop(action_id, None)
            continue
        if str(row.get("status") or "pending") not in {"pending", "armed"}:
            continue
        if action_expired(row, ttl_sec=ttl_sec):
            row["status"] = "expired"
    return doc


def action_expired(action: dict[str, Any], *, ttl_sec: int = 900) -> bool:
    """确认卡是否过期：优先 ``expires_at``，其次 ``created_epoch``；皆无则视为过期。"""
    expires_at = parse_time(action.get("expires_at"))
    if expires_at is not None:
        return time.time() >= expires_at
    created = action.get("created_epoch")
    if created is not None:
        try:
            return time.time() - float(created) > ttl_sec
        except (TypeError, ValueError):
            return True
    created_at = parse_time(action.get("created_at"))
    if created_at is not None:
        return time.time() - created_at > ttl_sec
    return True


def dump_for_debug(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2)[:4000]
