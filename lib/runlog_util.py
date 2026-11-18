"""控制面运行日志（JSONL）。

记录 CLI / 一键动作 / 检测修复 / 助手执行；文件 ``state/runlog.jsonl``。
CLI：``gpu-control logs``；HTTP：``GET /api/runlog``。
"""

from __future__ import annotations

import contextvars
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from paths import STATE_DIR
from state_util import atomic_write_text

RUNLOG_FILE = STATE_DIR / "runlog.jsonl"
DEFAULT_LIMIT = 80
MAX_BYTES = 2_000_000

_actor_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("runlog_actor", default=None)
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "runlog_request_id", default=None
)
_source_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("runlog_source", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_audit_context(
    *,
    actor: str | None = None,
    request_id: str | None = None,
    source: str | None = None,
) -> None:
    """设置当前请求的审计上下文（线程/协程本地）。"""
    if actor is not None:
        _actor_var.set(str(actor) or None)
    if request_id is not None:
        _request_id_var.set(str(request_id) or None)
    if source is not None:
        _source_var.set(str(source) or None)


def clear_audit_context() -> None:
    """清空审计上下文。"""
    _actor_var.set(None)
    _request_id_var.set(None)
    _source_var.set(None)


def new_request_id() -> str:
    """生成短 request_id。"""
    return "req_" + uuid.uuid4().hex[:12]


def append_runlog(
    kind: str,
    *,
    ok: bool | None = None,
    summary: str = "",
    detail: dict[str, Any] | None = None,
    source: str | None = None,
    actor: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """追加一条运行日志，必要时截断文件。

    :param kind: 类别，如 ``cli`` / ``oneclick`` / ``diagnose`` / ``assistant_execute``。
    :param ok: 是否成功；未知传 ``None``。
    :param summary: 一行摘要。
    :param detail: 可选结构化细节。
    :param source: 来源标签；默认取上下文或 ``control``。
    :param actor: 操作者；默认取上下文或 ``anonymous``。
    :param request_id: 请求关联 ID；默认取上下文。
    :returns: 写入的条目。
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now(),
        "kind": kind,
        "ok": ok,
        "summary": summary,
        "source": source or _source_var.get() or "control",
        "actor": actor or _actor_var.get() or "anonymous",
        "request_id": request_id or _request_id_var.get() or "",
        "detail": detail or {},
    }
    with RUNLOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if RUNLOG_FILE.stat().st_size > MAX_BYTES:
        _trim_file()
    return entry


def _trim_file() -> None:
    lines = RUNLOG_FILE.read_text(encoding="utf-8").splitlines()
    keep = lines[-max(200, len(lines) // 2) :]
    atomic_write_text(RUNLOG_FILE, "\n".join(keep) + ("\n" if keep else ""))


def list_runlog(*, limit: int = DEFAULT_LIMIT, kind: str | None = None) -> dict[str, Any]:
    """读取运行日志（新→旧）。

    :param limit: 返回条数上限（1–500）。
    :param kind: 按类别过滤；``None`` 表示全部。
    """
    limit = max(1, min(int(limit), 500))
    if not RUNLOG_FILE.is_file():
        return {"items": [], "path": str(RUNLOG_FILE), "total": 0}
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in RUNLOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(item, dict):
            skipped += 1
            continue
        if kind and item.get("kind") != kind:
            continue
        rows.append(item)
    items = rows[-limit:]
    items.reverse()
    return {
        "items": items,
        "path": str(RUNLOG_FILE),
        "total": len(rows),
        "limit": limit,
        "skipped": skipped,
    }


def clear_runlog(
    *,
    actor: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """清空运行日志并留下一条清算审计记录。"""
    if RUNLOG_FILE.is_file():
        RUNLOG_FILE.unlink()
    entry = append_runlog(
        "runlog_clear",
        ok=True,
        summary="清空运行日志",
        actor=actor,
        source=source or "api",
        detail={"note": "此前条目已删除；本条为清空事件本身"},
    )
    return {"ok": True, "path": str(RUNLOG_FILE), "entry": entry}


def record_cli(
    args: list[str],
    result: dict[str, Any],
    *,
    source: str | None = None,
    actor: str | None = None,
) -> None:
    """把一次 CLI 调用结果记入运行日志。"""
    rc = int(result.get("rc") or 0)
    summary = " ".join(str(a) for a in args[:8])
    append_runlog(
        "cli",
        ok=rc == 0,
        summary=summary,
        source=source,
        actor=actor,
        detail={
            "rc": rc,
            "args": args,
            "stdout_tail": (result.get("stdout") or "")[-800:],
            "stderr_tail": (result.get("stderr") or "")[-400:],
        },
    )
