"""控制面公告：规则告警写入 ``state/notices.json``，供 UI 铃铛与 AI 解读。

公告由 diagnose / watchdog / 健康信号确定性生成；LLM 只做只读解读，不写本文件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from paths import STATE_DIR
from state_util import read_json, update_json

NOTICES_FILE = STATE_DIR / "notices.json"
_MAX_ITEMS = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty() -> dict[str, Any]:
    return {"updated_at": None, "items": []}


def load_notices() -> dict[str, Any]:
    """读取公告文档。"""
    data = read_json(NOTICES_FILE, _empty())
    if not isinstance(data, dict):
        return _empty()
    items = data.get("items")
    if not isinstance(items, list):
        data["items"] = []
    return data


def list_notices(*, include_dismissed: bool = False, limit: int = 50) -> dict[str, Any]:
    """列出公告（默认隐藏已清除项）。"""
    data = load_notices()
    items = [x for x in data.get("items") or [] if isinstance(x, dict)]
    if not include_dismissed:
        items = [x for x in items if not x.get("dismissed")]
    items = sorted(
        items,
        key=lambda x: (0 if not x.get("read") else 1, str(x.get("updated_at") or ""), str(x.get("id") or "")),
        reverse=False,
    )
    # 未读优先：再按 updated_at 倒序
    unread = [x for x in items if not x.get("read")]
    read = [x for x in items if x.get("read")]
    unread.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    read.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    ordered = (unread + read)[: max(1, min(limit, _MAX_ITEMS))]
    return {
        "updated_at": data.get("updated_at"),
        "unread": sum(1 for x in items if not x.get("read")),
        "count": len(items),
        "items": ordered,
    }


def upsert_notices(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按稳定 ``id`` 插入或刷新公告；已清除项若再次出现则重新打开。"""

    def mutator(data: dict[str, Any] | None) -> dict[str, Any]:
        doc = data if isinstance(data, dict) else _empty()
        items = [x for x in (doc.get("items") or []) if isinstance(x, dict)]
        by_id = {str(x.get("id")): x for x in items if x.get("id")}
        now = _now()
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            nid = str(row["id"])
            prev = by_id.get(nid)
            if prev:
                prev["title"] = str(row.get("title") or prev.get("title") or nid)
                prev["detail"] = str(row.get("detail") or prev.get("detail") or "")
                prev["severity"] = str(row.get("severity") or prev.get("severity") or "info")
                prev["source"] = str(row.get("source") or prev.get("source") or "system")
                if row.get("fix") is not None:
                    prev["fix"] = row.get("fix")
                prev["updated_at"] = now
                if prev.get("dismissed"):
                    prev["dismissed"] = False
                    prev["read"] = False
            else:
                by_id[nid] = {
                    "id": nid,
                    "source": str(row.get("source") or "system"),
                    "severity": str(row.get("severity") or "info"),
                    "title": str(row.get("title") or nid),
                    "detail": str(row.get("detail") or ""),
                    "fix": row.get("fix"),
                    "created_at": now,
                    "updated_at": now,
                    "read": False,
                    "dismissed": False,
                }
        merged = list(by_id.values())
        merged.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        doc["items"] = merged[:_MAX_ITEMS]
        doc["updated_at"] = now
        return doc

    update_json(NOTICES_FILE, mutator, default=_empty())
    return list_notices()


def mark_notices(*, ids: list[str] | None = None, read: bool | None = None, dismissed: bool | None = None) -> dict[str, Any]:
    """标记已读或清除；``ids`` 为空则作用于全部未清除项。"""
    wanted = {str(x) for x in (ids or []) if str(x).strip()}

    def mutator(data: dict[str, Any] | None) -> dict[str, Any]:
        doc = data if isinstance(data, dict) else _empty()
        now = _now()
        for item in doc.get("items") or []:
            if not isinstance(item, dict):
                continue
            if wanted and str(item.get("id")) not in wanted:
                continue
            if not wanted and item.get("dismissed"):
                continue
            if read is not None:
                item["read"] = bool(read)
            if dismissed is not None:
                item["dismissed"] = bool(dismissed)
                if dismissed:
                    item["read"] = True
            item["updated_at"] = now
        doc["updated_at"] = now
        return doc

    update_json(NOTICES_FILE, mutator, default=_empty())
    return list_notices()


_MANAGED_PREFIXES = (
    "node:offline:",
    "node:degraded:",
    "snapshot:stale:",
    "quality:low-score",
    "endpoints:api-fail",
    "watchdog:not-running",
    "liveness:",
)


def reconcile_notices(
    active_rows: list[dict[str, Any]],
    *,
    prefixes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """清除已恢复的规则公告（条件消失后不再留在列表里）。"""
    active_ids = {
        str(row.get("id"))
        for row in (active_rows or [])
        if isinstance(row, dict) and row.get("id")
    }
    managed = prefixes or _MANAGED_PREFIXES
    current = load_notices()
    resolved = [
        str(item.get("id"))
        for item in current.get("items") or []
        if isinstance(item, dict)
        and item.get("id")
        and any(str(item["id"]).startswith(prefix) for prefix in managed)
        and str(item["id"]) not in active_ids
    ]
    if not resolved:
        return list_notices()
    drop = set(resolved)

    def mutator(data: dict[str, Any] | None) -> dict[str, Any]:
        doc = data if isinstance(data, dict) else _empty()
        doc["items"] = [
            item
            for item in doc.get("items") or []
            if isinstance(item, dict) and str(item.get("id") or "") not in drop
        ]
        doc["updated_at"] = _now()
        return doc

    update_json(NOTICES_FILE, mutator, default=_empty())
    return list_notices()


def findings_to_notices(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 diagnose findings 转为公告行。"""
    rows: list[dict[str, Any]] = []
    for row in findings or []:
        if not isinstance(row, dict):
            continue
        fid = str(row.get("id") or "").strip()
        if not fid:
            continue
        rows.append(
            {
                "id": f"diagnose:{fid}",
                "source": "diagnose",
                "severity": str(row.get("severity") or "warn"),
                "title": str(row.get("title") or fid),
                "detail": str(row.get("detail") or ""),
                "fix": row.get("fix"),
            }
        )
    return rows


def overview_to_notices(overview: dict[str, Any]) -> list[dict[str, Any]]:
    """从 overview 抽取 watchdog / 健康类规则公告。"""
    rows: list[dict[str, Any]] = []
    if not isinstance(overview, dict):
        return rows
    wd = overview.get("watchdog") if isinstance(overview.get("watchdog"), dict) else {}
    if wd.get("enabled") and not wd.get("running"):
        rows.append(
            {
                "id": "watchdog:not-running",
                "source": "watchdog",
                "severity": "warn",
                "title": "Watchdog 未在运行",
                "detail": "cluster.watchdog 已启用，但进程未检测到。可用看板一键启动或 CLI ``gpu-control watchdog --background``。",
                "fix": "gpu-control watchdog --background",
            }
        )
    live = overview.get("liveness") if isinstance(overview.get("liveness"), dict) else None
    if live:
        from liveness import offline_notices, stale_notices

        rows.extend(offline_notices(live))
        rows.extend(stale_notices(live))

    quality = overview.get("quality") if isinstance(overview.get("quality"), dict) else {}
    score = quality.get("score")
    try:
        score_n = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_n = None
    known = quality.get("known")
    if known is False:
        score_n = None
    if score_n is not None and score_n < 60:
        rows.append(
            {
                "id": "quality:low-score",
                "source": "quality",
                "severity": "error" if score_n < 40 else "warn",
                "title": f"集群健康评分偏低（{int(score_n)}）",
                "detail": str(quality.get("summary") or quality.get("hint") or "请检查 SSH / API / 互通与 GPU 占用。"),
                "fix": "gpu-control doctor && gpu-control diagnose check",
            }
        )
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    api_fail = summary.get("api_fail")
    try:
        fail_n = int(api_fail) if api_fail is not None else 0
    except (TypeError, ValueError):
        fail_n = 0
    if fail_n > 0:
        rows.append(
            {
                "id": "endpoints:api-fail",
                "source": "endpoints",
                "severity": "warn",
                "title": f"有 {fail_n} 个推理 API 探测失败",
                "detail": "端点健康缓存显示失败；可在看板点「探测 API」或检查 Job / 网关。",
                "fix": "gpu-control endpoints health",
            }
        )
    return rows
