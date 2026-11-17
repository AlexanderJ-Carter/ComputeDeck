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
from state_util import atomic_write_json, read_json

SESSION_DIR = STATE_DIR / "assistant_sessions"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_MAX_SESSIONS = 200
_MAX_AGE_SEC = 7 * 86400
_MAX_MESSAGES = 120


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


def save(doc: dict[str, Any]) -> dict[str, Any]:
    """原子保存并裁剪会话。"""
    cid = valid_id((doc or {}).get("conversation_id"))
    if not cid:
        raise ValueError("conversation_id 不合法")
    doc = dict(doc)
    doc["conversation_id"] = cid
    doc["updated_at"] = _now()
    messages = doc.get("messages")
    if isinstance(messages, list) and len(messages) > _MAX_MESSAGES:
        doc["messages"] = messages[-_MAX_MESSAGES:]
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(cid)
    atomic_write_json(path, doc)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    prune()
    return doc


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


def put_pending(doc: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    pending = doc.setdefault("pending", {})
    if isinstance(pending, dict):
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
    """返回给前端的安全字段（不包含一次性 token 的明文）。"""
    keys = (
        "id",
        "tool",
        "display_args",
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
    now = time.time()
    for action_id, row in list(pending.items()):
        if not isinstance(row, dict):
            pending.pop(action_id, None)
            continue
        if str(row.get("status") or "pending") not in {"pending", "armed"}:
            continue
        created = row.get("created_epoch")
        if created is not None and now - float(created) > ttl_sec:
            row["status"] = "expired"
    return doc


def action_expired(action: dict[str, Any], *, ttl_sec: int = 900) -> bool:
    created = action.get("created_epoch")
    if created is None:
        return False
    return time.time() - float(created) > ttl_sec


def dump_for_debug(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2)[:4000]
