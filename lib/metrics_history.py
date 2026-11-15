"""控制面指标环形历史：从 overview 采样，供看板曲线。

采样来自已有探测/资源快照（GPU、API 健康、主机 CPU、互通、入口上游），
不是业务网关 QPS；UI 需标明「控制面采样」。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from paths import STATE_DIR
from state_util import read_json, update_json

HISTORY_FILE = STATE_DIR / "metrics_history.json"
_MAX_POINTS = 180
_MIN_INTERVAL_SEC = 8.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty() -> dict[str, Any]:
    return {"updated_at": None, "points": []}


def _avg_cpu(overview: dict[str, Any]) -> float | None:
    nodes = overview.get("nodes") if isinstance(overview.get("nodes"), list) else []
    vals: list[float] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw = node.get("cpu_percent")
        try:
            if raw is not None:
                vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def sample_from_overview(overview: dict[str, Any]) -> dict[str, Any]:
    """从 overview 抽出一个可序列化采样点。"""
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    quality = overview.get("quality") if isinstance(overview.get("quality"), dict) else {}
    ingress = overview.get("ingress") if isinstance(overview.get("ingress"), dict) else {}
    edge = ingress.get("edge") if isinstance(ingress.get("edge"), dict) else {}
    pool = edge.get("pool") if isinstance(edge.get("pool"), dict) else {}
    api_ok = int(summary.get("api_ok") or 0)
    api_fail = int(summary.get("api_fail") or 0)
    api_total = int(summary.get("api_total") or (api_ok + api_fail) or 0)
    gpus_used = int(summary.get("gpus_used") or 0)
    gpus_free = int(summary.get("gpus_free") or 0)
    gpus_total = int(summary.get("gpus_total") or (gpus_used + gpus_free) or 0)
    probe_ok = summary.get("probe_ok")
    probe_fail = summary.get("probe_fail")
    try:
        score = float(quality.get("score")) if quality.get("score") is not None else None
    except (TypeError, ValueError):
        score = None
    api_rate = round(100.0 * api_ok / api_total, 2) if api_total else None
    gpu_rate = round(100.0 * gpus_used / gpus_total, 2) if gpus_total else None
    upstream = pool.get("available")
    if upstream is None:
        upstream = pool.get("upstream_count")
    return {
        "ts": _now(),
        "epoch": int(time.time()),
        "gpus_used": gpus_used,
        "gpus_free": gpus_free,
        "gpus_total": gpus_total,
        "gpu_util_pct": gpu_rate,
        "api_ok": api_ok,
        "api_fail": api_fail,
        "api_total": api_total,
        "api_ok_pct": api_rate,
        "quality_score": score,
        "cpu_avg_pct": _avg_cpu(overview),
        "probe_ok": int(probe_ok) if probe_ok is not None else None,
        "probe_fail": int(probe_fail) if probe_fail is not None else None,
        "edge_upstreams": int(upstream) if upstream is not None else None,
        "leases": int(summary.get("leases") or 0),
        "queue_pending": int(summary.get("queue_pending") or 0),
    }


def record_overview_sample(overview: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """写入环形历史；默认节流，避免 light 轮询写爆盘。"""
    point = sample_from_overview(overview)

    def mutator(data: dict[str, Any] | None) -> dict[str, Any]:
        doc = data if isinstance(data, dict) else _empty()
        points = [p for p in (doc.get("points") or []) if isinstance(p, dict)]
        if not force and points:
            last = points[-1]
            try:
                last_epoch = int(last.get("epoch") or 0)
            except (TypeError, ValueError):
                last_epoch = 0
            if point["epoch"] - last_epoch < _MIN_INTERVAL_SEC:
                return doc
        points.append(point)
        doc["points"] = points[-_MAX_POINTS:]
        doc["updated_at"] = point["ts"]
        return doc

    update_json(HISTORY_FILE, mutator, default=_empty())
    return point


def list_history(*, limit: int = 60) -> dict[str, Any]:
    """返回最近采样点。"""
    data = read_json(HISTORY_FILE, _empty())
    if not isinstance(data, dict):
        data = _empty()
    points = [p for p in (data.get("points") or []) if isinstance(p, dict)]
    lim = max(1, min(int(limit or 60), _MAX_POINTS))
    sliced = points[-lim:]
    return {
        "updated_at": data.get("updated_at"),
        "count": len(sliced),
        "points": sliced,
        "note": "控制面探测/资源采样，非业务网关 QPS",
        "series": [
            {"id": "api_ok_pct", "label": "API 连通率 %"},
            {"id": "gpu_util_pct", "label": "GPU 占用 %"},
            {"id": "cpu_avg_pct", "label": "节点 CPU 均载 %"},
            {"id": "quality_score", "label": "健康分"},
            {"id": "edge_upstreams", "label": "入口上游数"},
            {"id": "api_total", "label": "API 探测总数"},
        ],
    }
