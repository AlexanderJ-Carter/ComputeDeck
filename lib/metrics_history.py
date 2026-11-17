"""控制面指标短窗历史：从 overview 采样，供看板曲线。

语义区分：
- GPU 算力 / 显存：nvidia-smi 实测均值
- GPU 槽位占用：被判定 occupied 的卡占比（进程/显存/利用率启发式）
- API 连通：端点探测成功率

只保留最近约 1 小时；不是业务网关 QPS，也不是长期日志。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from paths import STATE_DIR
from state_util import read_json, update_json

HISTORY_FILE = STATE_DIR / "metrics_history.json"
_RETAIN_SEC = 3600
_MAX_POINTS = 240
_MIN_INTERVAL_SEC = 15.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty() -> dict[str, Any]:
    return {"updated_at": None, "points": []}


def _avg(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _avg_cpu(overview: dict[str, Any]) -> float | None:
    nodes = overview.get("nodes") if isinstance(overview.get("nodes"), list) else []
    vals: list[float] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("counted") is False:
            continue
        raw = node.get("cpu_percent")
        try:
            if raw is not None:
                vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    return _avg(vals)


def _gpu_real_utils(overview: dict[str, Any]) -> tuple[float | None, float | None]:
    """返回 (算力利用率均值 %, 显存利用率均值 %)。"""
    nodes = overview.get("nodes") if isinstance(overview.get("nodes"), list) else []
    sm: list[float] = []
    mem: list[float] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("counted") is False:
            continue
        gpus = node.get("gpus") if isinstance(node.get("gpus"), list) else []
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            util = gpu.get("utilization_percent")
            try:
                if util is not None:
                    sm.append(float(util))
            except (TypeError, ValueError):
                pass
            used = gpu.get("memory_used_mib")
            total = gpu.get("memory_total_mib")
            try:
                if used is not None and total is not None and float(total) > 0:
                    mem.append(100.0 * float(used) / float(total))
            except (TypeError, ValueError):
                pass
    return _avg(sm), _avg(mem)


def sample_from_overview(overview: dict[str, Any]) -> dict[str, Any]:
    """从 overview 抽出一个可序列化采样点。"""
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    ingress = overview.get("ingress") if isinstance(overview.get("ingress"), dict) else {}
    edge = ingress.get("edge") if isinstance(ingress.get("edge"), dict) else {}
    pool = edge.get("pool") if isinstance(edge.get("pool"), dict) else {}
    api_ok = int(summary.get("api_ok") or 0)
    api_fail = int(summary.get("api_fail") or 0)
    api_total = int(summary.get("api_total") or (api_ok + api_fail) or 0)
    gpus_used = int(summary.get("gpus_used") or 0)
    gpus_free = int(summary.get("gpus_free") or 0)
    gpus_total = int(summary.get("gpus_total") or (gpus_used + gpus_free) or 0)
    api_rate = round(100.0 * api_ok / api_total, 2) if api_total else None
    alloc_rate = round(100.0 * gpus_used / gpus_total, 2) if gpus_total else None
    nodes_total = int(summary.get("nodes") or 0)
    nodes_online = int(summary.get("nodes_online") or 0)
    nodes_online_pct = (
        round(100.0 * nodes_online / nodes_total, 2) if nodes_total else None
    )
    quality = overview.get("quality") if isinstance(overview.get("quality"), dict) else {}
    quality_score = quality.get("score")
    sm_util, mem_util = _gpu_real_utils(overview)
    upstream = pool.get("available")
    if upstream is None:
        upstream = pool.get("upstream_count")
    return {
        "ts": _now(),
        "epoch": int(time.time()),
        "gpus_used": gpus_used,
        "gpus_free": gpus_free,
        "gpus_total": gpus_total,
        "gpu_alloc_pct": alloc_rate,
        "gpu_sm_util_pct": sm_util,
        "gpu_mem_util_pct": round(mem_util, 2) if mem_util is not None else None,
        "api_ok": api_ok,
        "api_fail": api_fail,
        "api_total": api_total,
        "api_ok_pct": api_rate,
        "cpu_avg_pct": _avg_cpu(overview),
        "edge_upstreams": int(upstream) if upstream is not None else None,
        "leases": int(summary.get("leases") or 0),
        "queue_pending": int(summary.get("queue_pending") or 0),
        "nodes_online": nodes_online,
        "nodes_total": nodes_total,
        "nodes_online_pct": nodes_online_pct,
        "quality_score": quality_score,
    }


def _prune(points: list[dict[str, Any]], *, now_epoch: int) -> list[dict[str, Any]]:
    cutoff = now_epoch - _RETAIN_SEC
    kept = []
    for point in points:
        try:
            epoch = int(point.get("epoch") or 0)
        except (TypeError, ValueError):
            continue
        if epoch >= cutoff:
            kept.append(point)
    return kept[-_MAX_POINTS:]


def record_overview_sample(overview: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """写入短窗历史；默认节流，并按时间裁掉 1 小时以外的点。"""
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
                doc["points"] = _prune(points, now_epoch=point["epoch"])
                return doc
        points.append(point)
        doc["points"] = _prune(points, now_epoch=point["epoch"])
        doc["updated_at"] = point["ts"]
        return doc

    update_json(HISTORY_FILE, mutator, default=_empty())
    return point


def list_history(*, limit: int = 120) -> dict[str, Any]:
    """返回最近约 1 小时内的采样点。"""
    data = read_json(HISTORY_FILE, _empty())
    if not isinstance(data, dict):
        data = _empty()
    now_epoch = int(time.time())
    points = _prune(
        [p for p in (data.get("points") or []) if isinstance(p, dict)],
        now_epoch=now_epoch,
    )
    lim = max(1, min(int(limit or 120), _MAX_POINTS))
    sliced = points[-lim:]
    return {
        "updated_at": data.get("updated_at"),
        "count": len(sliced),
        "points": sliced,
        "retain_sec": _RETAIN_SEC,
        "note": "最近约 1 小时控制面快照：算力/显存= nvidia-smi 实测；槽位= occupied 占比；非业务 QPS",
        "series": [
            {"id": "api_ok_pct", "label": "API 连通率 %"},
            {"id": "gpu_sm_util_pct", "label": "GPU 算力利用率 %"},
            {"id": "gpu_mem_util_pct", "label": "GPU 显存利用率 %"},
            {"id": "gpu_alloc_pct", "label": "GPU 槽位占用 %"},
            {"id": "cpu_avg_pct", "label": "节点 CPU 均载 %"},
            {"id": "edge_upstreams", "label": "入口上游数"},
            {"id": "nodes_online_pct", "label": "节点在线率 %"},
            {"id": "queue_pending", "label": "队列等待数"},
            {"id": "quality_score", "label": "健康评分"},
        ],
    }
