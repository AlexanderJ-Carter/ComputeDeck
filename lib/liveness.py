"""节点存活与数据新鲜度：把多份健康信号收敛成每节点一个状态。

控制面里的“在线”不能只看上一次 GPU 快照：节点掉线后 ``resources.json`` 仍会保留
旧数据。本模块给 UI / CLI / watchdog / 调度提供统一口径：

- ``online``    最近一次可达性检查成功；
- ``degraded``  最近一次检查失败但尚未达到离线阈值（避免单次抖动误报）；
- ``offline``   连续失败达到阈值，或最近一次失败是“路由不可达”这类确定性错误；
- ``unknown``   从未成功检查过。

所有阈值都可以在 ``cluster.health`` 覆盖；默认值面向 60 秒一次的探测。
纯标准库，不依赖 Ansible，便于单元测试。
"""

from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TTLS: dict[str, Any] = {
    "probe_ttl_sec": 180,  # liveness.json 多久内视为可信
    "ssh_ttl_sec": 300,  # ssh_quality.json 多久内可信
    "host_ttl_sec": 300,  # host_metrics.json 多久内可信
    "api_ttl_sec": 300,  # endpoint_health.json 多久内可信
    "mesh_ttl_sec": 900,  # probe.json 多久内可信
    "snapshot_ttl_sec": 900,  # resources.json collected_at 多久后算过期
    "offline_after_failures": 2,
    "offline_after_sec": 300,  # 超过这么久没有成功过 → offline
    "check_interval_sec": 60,
    "tcp_timeout_sec": 3,
    "api_probe_timeout_sec": 2,
}

# 确定性失败：路由 / DNS / 主机不存在。单次即可判离线。
_DEFINITIVE_MARKERS = (
    "no route to host",
    "network is unreachable",
    "host is unreachable",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "cannot assign requested address",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime | None = None) -> str:
    return (ts or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> float | None:
    """解析 ISO8601（可带 Z）或 epoch 秒；失败返回 ``None``。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def format_age(seconds: float | int | None) -> str:
    """人类可读时长：``42s`` / ``3m`` / ``2h`` / ``3d``。"""
    if seconds is None:
        return "-"
    try:
        value = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        return "-"
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m"
    if value < 86400:
        return f"{value // 3600}h"
    return f"{value // 86400}d"


def load_ttls(cluster: dict[str, Any] | None = None) -> dict[str, Any]:
    """合并 ``cluster.health`` 覆盖项，非法值忽略。"""
    ttls = dict(DEFAULT_TTLS)
    raw = ((cluster or {}).get("health") or {}) if isinstance(cluster, dict) else {}
    if not isinstance(raw, dict):
        return ttls
    for key, default in DEFAULT_TTLS.items():
        if key not in raw:
            continue
        try:
            if isinstance(default, float):
                ttls[key] = float(raw[key])
            else:
                ttls[key] = int(raw[key])
        except (TypeError, ValueError):
            continue
    ttls["offline_after_failures"] = max(1, int(ttls["offline_after_failures"]))
    ttls["check_interval_sec"] = max(10, int(ttls["check_interval_sec"]))
    ttls["tcp_timeout_sec"] = max(1.0, min(15.0, float(ttls["tcp_timeout_sec"])))
    return ttls


def _definitive(error: str | None) -> bool:
    text = (error or "").lower()
    return any(marker in text for marker in _DEFINITIVE_MARKERS)


def tcp_probe(host: str, port: int, *, timeout: float = 3.0) -> dict[str, Any]:
    """TCP 连通性探测；返回 ok/latency/error，不抛异常。"""
    started = time.time()
    if not host:
        return {
            "ok": False,
            "latency_ms": None,
            "error": "缺少 ansible_host",
            "checked_at": iso(),
            "checked_epoch": started,
        }
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            latency_ms = int((time.time() - started) * 1000)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "error": None,
                "checked_at": iso(),
                "checked_epoch": started,
            }
    except OSError as exc:
        return {
            "ok": False,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "checked_at": iso(),
            "checked_epoch": started,
        }


def _node_host(meta: dict[str, Any]) -> str:
    return str((meta or {}).get("ansible_host") or (meta or {}).get("host") or "").strip()


def _node_port(meta: dict[str, Any], defaults: dict[str, Any], key: str = "ansible_port") -> int:
    raw = (meta or {}).get(key, (defaults or {}).get(key, 22))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 22


def _api_port(name: str, meta: dict[str, Any], cluster: dict[str, Any]) -> int | None:
    api = (cluster or {}).get("api") or {}
    for source in (meta or {}, api, (cluster or {}).get("defaults") or {}):
        for key in ("base_port", "api_port", "port"):
            if key in source and str(source[key]).strip().isdigit():
                return int(source[key])
    return None


def check_nodes(
    nodes_doc: dict[str, Any],
    *,
    cluster: dict[str, Any] | None = None,
    timeout: float | None = None,
    api_timeout: float | None = None,
) -> list[dict[str, Any]]:
    """并发 TCP 探测所有节点；返回逐节点结果（含 SSH 与可选 API 端口）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cluster = cluster or {}
    ttls = load_ttls(cluster)
    timeout = float(timeout if timeout is not None else ttls["tcp_timeout_sec"])
    api_timeout = float(api_timeout if api_timeout is not None else ttls["api_probe_timeout_sec"])
    defaults = (nodes_doc.get("defaults") or {}) if isinstance(nodes_doc, dict) else {}
    nodes = (nodes_doc.get("nodes") or {}) if isinstance(nodes_doc, dict) else {}

    def one(name: str, meta: dict[str, Any]) -> dict[str, Any]:
        host = _node_host(meta)
        port = _node_port(meta, defaults, "ansible_port")
        ssh = tcp_probe(host, port, timeout=timeout)
        api_port = _api_port(name, meta, cluster)
        api = tcp_probe(host, api_port, timeout=api_timeout) if api_port else None
        return {
            "node": name,
            "host": host,
            "ssh_port": port,
            "api_port": api_port,
            "ssh": ssh,
            "api": api,
            "checked_at": ssh.get("checked_at"),
            "checked_epoch": ssh.get("checked_epoch"),
        }

    results: list[dict[str, Any]] = []
    if not nodes:
        return results
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(nodes)))) as pool:
        futures = [pool.submit(one, name, meta) for name, meta in sorted(nodes.items())]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001 — 单个节点探测失败不能拖垮整批
                results.append(
                    {
                        "node": "?",
                        "ssh": {"ok": False, "error": str(exc), "checked_at": iso()},
                        "api": None,
                    }
                )
    results.sort(key=lambda row: str(row.get("node") or ""))
    return results


def _empty_node(name: str) -> dict[str, Any]:
    return {
        "node": name,
        "state": "unknown",
        "reachable": False,
        "last_checked": None,
        "last_checked_epoch": None,
        "last_seen": None,
        "last_seen_epoch": None,
        "consecutive_failures": 0,
        "source": None,
        "checks": {},
        "error": None,
        "latency_ms": None,
    }


def update_snapshot(
    previous: dict[str, Any] | None,
    results: list[dict[str, Any]],
    *,
    now: float | None = None,
    ttls: dict[str, Any] | None = None,
    node_names: list[str] | None = None,
) -> dict[str, Any]:
    """把一次探测结果合并进持久化快照，返回新的快照文档。

    :param previous: 旧 ``liveness.json`` 文档（可空）。
    :param results: ``check_nodes`` 的输出。
    :param node_names: 本轮应覆盖的节点名（用于判断未探测节点）。
    """
    ttls = ttls or DEFAULT_TTLS
    now_epoch = float(now if now is not None else time.time())
    now_iso = iso(datetime.fromtimestamp(now_epoch, tz=timezone.utc))
    prev_nodes = {}
    if isinstance(previous, dict) and isinstance(previous.get("nodes"), dict):
        prev_nodes = previous["nodes"]
    by_name = {str(row.get("node")): row for row in results if isinstance(row, dict)}
    names = list(node_names) if node_names is not None else sorted(by_name)
    for name in by_name:
        if name not in names:
            names.append(name)

    out_nodes: dict[str, Any] = {}
    for name in names:
        prev = prev_nodes.get(name) if isinstance(prev_nodes.get(name), dict) else _empty_node(name)
        row = by_name.get(name)
        if not row:
            node = dict(prev)
            node["state"] = "unknown"
            node["reachable"] = False
            node["error"] = node.get("error") or "本轮未探测"
            out_nodes[name] = node
            continue

        ssh = row.get("ssh") or {}
        api = row.get("api") or None
        ok = bool(ssh.get("ok"))
        failures = 0 if ok else int(prev.get("consecutive_failures") or 0) + 1
        last_seen_epoch = now_epoch if ok else parse_ts(prev.get("last_seen"))
        last_seen = now_iso if ok else prev.get("last_seen")
        offline = False
        if not ok:
            definitive = _definitive(ssh.get("error"))
            stale_seen = (
                last_seen_epoch is not None
                and now_epoch - last_seen_epoch > float(ttls["offline_after_sec"])
            )
            offline = definitive or failures >= int(ttls["offline_after_failures"]) or stale_seen

        checks: dict[str, Any] = {
            "ssh": {
                **ssh,
                "port": row.get("ssh_port"),
                "host": row.get("host"),
            }
        }
        if api is not None:
            checks["api"] = {**api, "port": row.get("api_port")}

        node = {
            "node": name,
            "host": row.get("host"),
            "state": "online" if ok else ("offline" if offline else "degraded"),
            "reachable": ok,
            "last_checked": row.get("checked_at") or now_iso,
            "last_checked_epoch": row.get("checked_epoch") or now_epoch,
            "last_seen": last_seen,
            "last_seen_epoch": last_seen_epoch,
            "consecutive_failures": failures,
            "source": "tcp",
            "checks": checks,
            "error": ssh.get("error"),
            "latency_ms": ssh.get("latency_ms"),
        }
        out_nodes[name] = node

    summary = _summarize(out_nodes)
    return {
        "version": 1,
        "generated_at": now_iso,
        "generated_epoch": now_epoch,
        "ttls": ttls,
        "source": "tcp",
        "nodes": out_nodes,
        "summary": summary,
    }


def _summarize(nodes: dict[str, Any]) -> dict[str, Any]:
    counts = {"online": 0, "degraded": 0, "offline": 0, "unknown": 0}
    offline: list[str] = []
    degraded: list[str] = []
    for name, node in sorted(nodes.items()):
        state = str((node or {}).get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        if state == "offline":
            offline.append(name)
        elif state == "degraded":
            degraded.append(name)
    return {
        "nodes": len(nodes),
        **counts,
        "offline_nodes": offline,
        "degraded_nodes": degraded,
    }


def read_snapshot(path: Path) -> dict[str, Any]:
    """读取 liveness 快照；坏文件返回空文档。"""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _artifact_signal(
    *,
    name: str,
    now: float,
    ttls: dict[str, Any],
    ssh_quality: dict[str, Any],
    host_metrics: dict[str, Any],
    probe: dict[str, Any],
    endpoint_health: dict[str, Any],
) -> dict[str, Any] | None:
    """从既有状态文件推导一个节点信号；没有新鲜信号时返回 ``None``。"""
    checks: dict[str, Any] = {}
    last_seen = None
    last_seen_epoch = None
    reachable: bool | None = None
    error = None
    source = None

    ssh_at = parse_ts(ssh_quality.get("checked_at"))
    if ssh_at is not None and now - ssh_at <= float(ttls["ssh_ttl_sec"]):
        for row in ssh_quality.get("results") or []:
            if str(row.get("node") or "") != name:
                continue
            ok = bool(row.get("ok"))
            checks["ssh"] = {
                "ok": ok,
                "at": ssh_quality.get("checked_at"),
                "age_sec": int(now - ssh_at),
                "fresh": True,
                "latency_ms": row.get("latency_ms"),
                "detail": row.get("detail"),
            }
            if ok:
                reachable = True
                last_seen_epoch = ssh_at
                last_seen = ssh_quality.get("checked_at")
                source = "ssh_quality"
            else:
                reachable = False if reachable is None else reachable
                error = row.get("detail")
                source = source or "ssh_quality"
            break

    host_at = parse_ts(host_metrics.get("generated_at"))
    if host_at is not None and now - host_at <= float(ttls["host_ttl_sec"]):
        entry = ((host_metrics.get("nodes") or {}).get(name)) or {}
        if entry:
            ok = bool(entry.get("ok"))
            checks["host"] = {
                "ok": ok,
                "at": host_metrics.get("generated_at"),
                "age_sec": int(now - host_at),
                "fresh": True,
                "latency_ms": entry.get("latency_ms"),
                "detail": entry.get("error"),
            }
            if ok:
                reachable = True
                if last_seen_epoch is None or host_at > last_seen_epoch:
                    last_seen_epoch = host_at
                    last_seen = host_metrics.get("generated_at")
                source = source or "host_metrics"
            elif reachable is None:
                reachable = False
                error = entry.get("error") or error
                source = source or "host_metrics"

    probe_at = parse_ts(probe.get("generated_at")) or parse_ts(probe.get("checked_at"))
    if probe_at is not None and now - probe_at <= float(ttls["mesh_ttl_sec"]):
        matrix = probe.get("matrix") or {}
        incoming = []
        for src, row in matrix.items():
            if src == name or not isinstance(row, dict):
                continue
            cell = row.get(name)
            if isinstance(cell, dict):
                incoming.append(bool(cell.get("ok")))
        if incoming:
            ok = any(incoming)
            checks["mesh_in"] = {
                "ok": ok,
                "at": probe.get("generated_at"),
                "age_sec": int(now - probe_at),
                "fresh": True,
                "peers": len(incoming),
                "peers_ok": sum(1 for x in incoming if x),
            }
            if not ok and reachable is None:
                reachable = False
                error = "所有节点到该节点的互通探测均失败"
                source = source or "probe"
            elif ok and reachable is None:
                reachable = True
                last_seen_epoch = probe_at
                last_seen = probe.get("generated_at")
                source = source or "probe"

    api_at = parse_ts(endpoint_health.get("generated_at"))
    if api_at is not None and now - api_at <= float(ttls["api_ttl_sec"]):
        rows = [
            row
            for row in endpoint_health.get("results") or []
            if str(row.get("node") or "") == name
        ]
        if rows:
            ok_count = sum(1 for row in rows if row.get("ok"))
            checks["api"] = {
                "ok": ok_count > 0,
                "at": endpoint_health.get("generated_at"),
                "age_sec": int(now - api_at),
                "fresh": True,
                "ok_count": ok_count,
                "total": len(rows),
            }

    if reachable is None and not checks:
        return None
    return {
        "reachable": bool(reachable),
        "last_seen": last_seen,
        "last_seen_epoch": last_seen_epoch,
        "checks": checks,
        "error": error,
        "source": source,
        "definitive": _definitive(error) if reachable is False else False,
    }


def cluster_view(
    *,
    node_names: list[str],
    resources: dict[str, Any] | None = None,
    ssh_quality: dict[str, Any] | None = None,
    host_metrics: dict[str, Any] | None = None,
    probe: dict[str, Any] | None = None,
    endpoint_health: dict[str, Any] | None = None,
    persisted: dict[str, Any] | None = None,
    cluster: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """生成 UI 使用的节点存活视图。

    优先使用新鲜的 ``liveness.json``（后台 TCP 探测）；没有时回落到
    ``ssh_quality`` / ``host_metrics`` / ``probe`` / ``endpoint_health``，
    这样即使后台线程没跑也能立即识别掉线节点。
    """
    ttls = load_ttls(cluster)
    now_epoch = float(now if now is not None else time.time())
    resources = resources or {}
    ssh_quality = ssh_quality or {}
    host_metrics = host_metrics or {}
    probe = probe or {}
    endpoint_health = endpoint_health or {}
    persisted = persisted or {}

    persisted_at = parse_ts(persisted.get("generated_at"))
    persisted_fresh = (
        persisted_at is not None and now_epoch - persisted_at <= float(ttls["probe_ttl_sec"])
    )
    persisted_nodes = (
        persisted.get("nodes") if isinstance(persisted.get("nodes"), dict) else {}
    )

    nodes: dict[str, Any] = {}
    for name in node_names:
        snap = resources.get(name) if isinstance(resources.get(name), dict) else {}
        collected_at = snap.get("collected_at")
        collected_epoch = parse_ts(collected_at)
        snap_age = (
            int(now_epoch - collected_epoch) if collected_epoch is not None else None
        )
        snapshot_fresh = bool(
            collected_epoch is not None
            and now_epoch - collected_epoch <= float(ttls["snapshot_ttl_sec"])
        )

        if persisted_fresh:
            row = persisted_nodes.get(name)
            if isinstance(row, dict):
                node = {
                    **row,
                    "state": str(row.get("state") or "unknown"),
                    "source": row.get("source") or "tcp",
                }
            else:
                node = _empty_node(name)
        else:
            signal = _artifact_signal(
                name=name,
                now=now_epoch,
                ttls=ttls,
                ssh_quality=ssh_quality,
                host_metrics=host_metrics,
                probe=probe,
                endpoint_health=endpoint_health,
            )
            if signal is None:
                node = _empty_node(name)
            else:
                failures = 0 if signal["reachable"] else int(ttls["offline_after_failures"])
                offline = not signal["reachable"] and (
                    signal.get("definitive") or failures >= int(ttls["offline_after_failures"])
                )
                node = {
                    **_empty_node(name),
                    "host": _node_host({}),
                    "state": "online" if signal["reachable"] else ("offline" if offline else "degraded"),
                    "reachable": signal["reachable"],
                    "last_seen": signal["last_seen"],
                    "last_seen_epoch": signal["last_seen_epoch"],
                    "last_checked": None,
                    "last_checked_epoch": None,
                    "consecutive_failures": failures,
                    "source": signal["source"],
                    "checks": signal["checks"],
                    "error": signal["error"],
                    "latency_ms": None,
                }
                # 用最近的检查时间作为 last_checked
                stamps = [
                    parse_ts((check or {}).get("at"))
                    for check in signal["checks"].values()
                ]
                stamps = [s for s in stamps if s is not None]
                if stamps:
                    latest = max(stamps)
                    node["last_checked_epoch"] = latest
                    node["last_checked"] = iso(datetime.fromtimestamp(latest, tz=timezone.utc))

        node["snapshot"] = {
            "collected_at": collected_at,
            "age_sec": snap_age,
            "age_text": format_age(snap_age),
            "ttl_sec": ttls["snapshot_ttl_sec"],
            "fresh": snapshot_fresh,
            "stale": not snapshot_fresh,
        }
        node["stale"] = not snapshot_fresh
        node["stale_reasons"] = [] if snapshot_fresh else ["snapshot"]
        node["last_seen_age_sec"] = (
            int(now_epoch - node["last_seen_epoch"])
            if node.get("last_seen_epoch") is not None
            else None
        )
        node["reachable"] = node.get("state") in {"online", "degraded"}
        nodes[name] = node

    summary = _summarize(nodes)
    summary["snapshot_stale"] = [
        {"node": name, "age_sec": node["snapshot"]["age_sec"], "age_text": node["snapshot"]["age_text"]}
        for name, node in sorted(nodes.items())
        if node.get("stale")
    ]
    summary["snapshot_stale_count"] = len(summary["snapshot_stale"])
    return {
        "version": 1,
        "generated_at": persisted.get("generated_at") if persisted_fresh else iso(),
        "generated_epoch": now_epoch,
        "source": "tcp" if persisted_fresh else "artifacts",
        "ttls": ttls,
        "nodes": nodes,
        "summary": summary,
    }


def run_check(
    nodes_doc: dict[str, Any],
    *,
    cluster: dict[str, Any] | None = None,
    snapshot_path: Path | None = None,
    timeout: float | None = None,
    now: float | None = None,
    only: list[str] | None = None,
) -> dict[str, Any]:
    """执行一次存活探测并合并旧快照；返回新快照（同时原子写盘）。

    :param nodes_doc: ``nodes.json`` 内容。
    :param cluster: ``cluster.json`` 内容（读取 ``health`` 覆盖项）。
    :param snapshot_path: 持久化路径；``None`` 时不写盘。
    :param only: 只探测这些节点（其余节点沿用旧状态）。
    """
    cluster = cluster or {}
    ttls = load_ttls(cluster)
    names = list((nodes_doc.get("nodes") or {}).keys())
    if only:
        wanted = {str(x).strip() for x in only if str(x).strip()}
        nodes_doc = dict(nodes_doc)
        nodes_doc["nodes"] = {
            name: meta for name, meta in (nodes_doc.get("nodes") or {}).items() if name in wanted
        }
        names = [name for name in names if name in wanted]
    results = check_nodes(nodes_doc, cluster=cluster, timeout=timeout)
    previous = read_snapshot(snapshot_path) if snapshot_path else {}
    snapshot = update_snapshot(
        previous,
        results,
        now=now,
        ttls=ttls,
        node_names=names,
    )
    if snapshot_path is not None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(snapshot_path)
    return snapshot


def offline_nodes(view: dict[str, Any]) -> set[str]:
    """从视图或 ``liveness.json`` 里取离线节点名集合。"""
    nodes = (view or {}).get("nodes") or {}
    return {
        name
        for name, node in nodes.items()
        if isinstance(node, dict) and str(node.get("state") or "") == "offline"
    }


def unreachable_nodes(view: dict[str, Any]) -> set[str]:
    """离线 + 未探测到（unknown）节点；调度应避开这两类。"""
    nodes = (view or {}).get("nodes") or {}
    out = set()
    for name, node in nodes.items():
        if not isinstance(node, dict):
            continue
        if str(node.get("state") or "") in {"offline", "unknown"}:
            out.add(name)
    return out


def offline_notices(view: dict[str, Any]) -> list[dict[str, Any]]:
    """把离线 / 降级节点转成公告行。"""
    rows: list[dict[str, Any]] = []
    nodes = (view or {}).get("nodes") or {}
    for name, node in sorted(nodes.items()):
        state = str((node or {}).get("state") or "unknown")
        if state == "offline":
            snapshot = node.get("snapshot") or {}
            last = node.get("last_seen")
            if not last:
                age_text = snapshot.get("age_text")
                last = f"快照 {age_text} 前" if age_text and age_text != "-" else "从未成功探测"
            rows.append(
                {
                    "id": f"node:offline:{name}",
                    "source": "liveness",
                    "severity": "error",
                    "title": f"节点离线：{name}",
                    "detail": (
                        f"连续失败 {node.get('consecutive_failures') or 0} 次"
                        f"；最后成功：{last}"
                        f"；错误：{node.get('error') or '不可达'}"
                    ),
                    "fix": f"gpu-control liveness check --node {name}",
                }
            )
        elif state == "degraded":
            rows.append(
                {
                    "id": f"node:degraded:{name}",
                    "source": "liveness",
                    "severity": "warn",
                    "title": f"节点探活不稳定：{name}",
                    "detail": (
                        f"最近一次探测失败（{node.get('error') or '不可达'}），"
                        f"连续失败 {node.get('consecutive_failures') or 0} 次；暂未判定离线。"
                    ),
                    "fix": f"gpu-control liveness check --node {name}",
                }
            )
    return rows


def stale_notices(view: dict[str, Any]) -> list[dict[str, Any]]:
    """资源快照过期的公告行。"""
    rows: list[dict[str, Any]] = []
    for entry in ((view or {}).get("summary") or {}).get("snapshot_stale") or []:
        age_text = entry.get("age_text")
        if not age_text or age_text == "-":
            detail = "没有可用的 GPU 快照（节点可能从未采集成功或已掉线）；总览不会计入空闲/占用统计。"
        else:
            detail = f"最近一次 GPU 采集在 {age_text} 前；总览不会把过期数据计入空闲/占用统计。"
        rows.append(
            {
                "id": f"snapshot:stale:{entry.get('node')}",
                "source": "resources",
                "severity": "warn",
                "title": f"节点快照过期：{entry.get('node')}",
                "detail": detail,
                "fix": "gpu-control gpu refresh",
            }
        )
    return rows
