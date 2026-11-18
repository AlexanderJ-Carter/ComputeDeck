"""助手 AI 解读 / 自动巡检结果短窗归档。

文件：``state/assistant_insights.jsonl``；供 UI 回看最近 N 条。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from paths import STATE_DIR
from state_util import atomic_write_text

INSIGHT_FILE = STATE_DIR / "assistant_insights.jsonl"
MAX_BYTES = 1_500_000
DEFAULT_LIMIT = 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_insight(
    *,
    kind: str,
    ok: bool,
    reply: str,
    actor: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加一条解读结果。

    :param kind: ``analyze`` / ``auto_insight`` / ``chat_summary``。
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now(),
        "kind": kind,
        "ok": ok,
        "reply": str(reply or "")[:8000],
        "actor": actor or "anonymous",
        "meta": meta or {},
    }
    with INSIGHT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if INSIGHT_FILE.is_file() and INSIGHT_FILE.stat().st_size > MAX_BYTES:
        lines = INSIGHT_FILE.read_text(encoding="utf-8").splitlines()
        keep = lines[-300:]
        atomic_write_text(INSIGHT_FILE, "\n".join(keep) + ("\n" if keep else ""))
    return entry


def list_insights(*, limit: int = DEFAULT_LIMIT, kind: str | None = None) -> dict[str, Any]:
    """读取解读历史（新→旧）。"""
    limit = max(1, min(int(limit), 100))
    if not INSIGHT_FILE.is_file():
        return {"items": [], "path": str(INSIGHT_FILE), "total": 0}
    rows: list[dict[str, Any]] = []
    for line in INSIGHT_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if kind and item.get("kind") != kind:
            continue
        rows.append(item)
    items = rows[-limit:]
    items.reverse()
    return {
        "items": items,
        "path": str(INSIGHT_FILE),
        "total": len(rows),
        "limit": limit,
    }
