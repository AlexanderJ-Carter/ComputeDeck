"""推理池端点端口解析（支持节点异构）。

供 Gateway / LB / health probe；节点级覆盖见 ``nodes.json``。
"""

from __future__ import annotations


def resolve_node_ports(
    meta: dict | None,
    *,
    defaults: dict | None = None,
    api: dict | None = None,
) -> tuple[int, list[int]]:
    """解析单节点推理端口区间。

    端口数量优先级：
    ``node.ports_per_node`` → ``node.api.ports_per_node`` → ``node.gpus_per_node``
    → ``defaults.gpus_per_node`` → ``api.ports_per_node`` → 1。

    起始端口优先级：
    ``node.base_port`` → ``node.api.base_port`` → ``api.base_port`` → 8000。

    :param meta: 节点元数据（``nodes.json`` 单节点）。
    :param defaults: ``nodes.defaults``。
    :param api: ``cluster.api``。
    :returns: ``(base_port, ports)``。
    """
    meta = meta or {}
    defaults = defaults or {}
    api = api or {}
    node_api = meta.get("api") if isinstance(meta.get("api"), dict) else {}

    base = int(
        meta.get("base_port")
        or node_api.get("base_port")
        or api.get("base_port")
        or 8000
    )
    count = int(
        meta.get("ports_per_node")
        or node_api.get("ports_per_node")
        or meta.get("gpus_per_node")
        or defaults.get("gpus_per_node")
        or api.get("ports_per_node")
        or 1
    )
    if count < 1:
        count = 1
    return base, list(range(base, base + count))


def build_endpoint_catalog(nodes_doc: dict, cluster: dict) -> dict:
    """由 ``nodes.json`` 与 ``cluster.json`` 构建端点目录。

    :param nodes_doc: 节点文档。
    :param cluster: 集群文档。
    :returns: 含 ``endpoints`` / ``by_node`` / ``urls`` 等字段的字典。
    """
    api = cluster.get("api") or {}
    defaults = nodes_doc.get("defaults") or {}
    key_file = api.get("api_key_file") or ""

    endpoints: list[dict] = []
    by_node: dict[str, dict] = {}
    for name, meta in sorted((nodes_doc.get("nodes") or {}).items()):
        meta = meta or {}
        if meta.get("role") == "control":
            continue
        host = meta.get("ansible_host")
        if not host:
            continue
        base, ports = resolve_node_ports(meta, defaults=defaults, api=api)
        by_node[name] = {
            "host": host,
            "base_port": base,
            "ports_per_node": len(ports),
            "ports": ports,
            "gpus_per_node": meta.get("gpus_per_node", defaults.get("gpus_per_node")),
        }
        for i, port in enumerate(ports):
            endpoints.append({
                "node": name,
                "host": host,
                "gpu": i,
                "port": port,
                "url": f"http://{host}:{port}",
                "health": f"http://{host}:{port}{api.get('health_path', '/health')}",
                "role": meta.get("role") or defaults.get("role") or "serving",
                "labels": meta.get("labels") or [],
            })

    default_base = int(api.get("base_port", 8000))
    default_count = int(api.get("ports_per_node", 1))
    return {
        "job": cluster.get("default_job") or "",
        "strategy": cluster.get("strategy"),
        "api_key_file": key_file,
        "api_defaults": {
            "base_port": default_base,
            "ports_per_node": default_count,
            "port_range": list(range(default_base, default_base + default_count)),
        },
        "by_node": by_node,
        "count": len(endpoints),
        "endpoints": endpoints,
        "urls": [e["url"] for e in endpoints],
    }
