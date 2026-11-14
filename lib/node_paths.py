"""节点路径合并与 ``${paths.*}`` 占位符展开。

供 Job / models 解析；配置见 ``nodes.json`` 的 ``defaults.paths``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from paths import expand_path, nodes_file

_PATH_TOKEN = re.compile(r"\$\{paths\.([A-Za-z0-9_]+)\}")


def load_nodes_data() -> dict:
    path = nodes_file()
    if not path.is_file():
        return {"defaults": {}, "nodes": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def default_paths(data: dict | None = None) -> dict[str, str]:
    data = data if data is not None else load_nodes_data()
    defaults = data.get("defaults") or {}
    raw = defaults.get("paths") or {}
    return {str(k): expand_path(str(v)) for k, v in raw.items() if v}


def node_paths(name: str, data: dict | None = None) -> dict[str, str]:
    data = data if data is not None else load_nodes_data()
    merged = dict(default_paths(data))
    nodes = data.get("nodes") or {}
    meta = nodes.get(name) or {}
    override = meta.get("paths") or {}
    for key, value in override.items():
        if value is None or value == "":
            continue
        merged[str(key)] = expand_path(str(value))
    return merged


def substitute(template: str, paths: dict[str, str]) -> str:
    if not template:
        return template

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in paths:
            raise KeyError(f"未定义 paths.{key}（检查 nodes.json defaults.paths / 节点 paths）")
        return paths[key]

    return _PATH_TOKEN.sub(repl, expand_path(template))


def substitute_list(items: list[Any] | None, paths: dict[str, str]) -> list[str]:
    if not items:
        return []
    return [substitute(str(item), paths) for item in items]


def substitute_mapping(mapping: dict | None, paths: dict[str, str]) -> dict[str, str]:
    if not mapping:
        return {}
    return {str(k): substitute(str(v), paths) for k, v in mapping.items()}


def resolve_job(job: dict, node: str, data: dict | None = None) -> dict:
    """Return a shallow copy of job with paths / env / docker fields resolved for one node."""
    paths = node_paths(node, data)
    out = dict(job)
    for key in ("python", "script", "workdir", "model_path"):
        if key in out and isinstance(out[key], str):
            out[key] = substitute(out[key], paths)
    if "extra_args" in out:
        out["extra_args"] = substitute_list(out.get("extra_args"), paths)
    if "env" in out and isinstance(out["env"], dict):
        out["env"] = substitute_mapping(out["env"], paths)
    docker = out.get("docker")
    if isinstance(docker, dict):
        d = dict(docker)
        for key in ("image", "name", "gpus", "network", "command_string"):
            if key in d and isinstance(d[key], str):
                d[key] = substitute(d[key], paths)
        if "command" in d and isinstance(d["command"], list):
            d["command"] = substitute_list(d["command"], paths)
        if "ports" in d and isinstance(d["ports"], list):
            d["ports"] = [str(p) for p in d["ports"]]
        if "volumes" in d and isinstance(d["volumes"], list):
            d["volumes"] = substitute_list(d["volumes"], paths)
        if "env" in d and isinstance(d["env"], dict):
            d["env"] = substitute_mapping(d["env"], paths)
        out["docker"] = d
    out["_paths"] = paths
    out["_node"] = node
    return out


def fingerprint_job(resolved: dict) -> tuple:
    docker = resolved.get("docker") or {}
    return (
        resolved.get("runtime") or "process",
        resolved.get("python") or "",
        resolved.get("script") or "",
        tuple(resolved.get("extra_args") or []),
        tuple(sorted((resolved.get("env") or {}).items())),
        docker.get("image") or "",
        docker.get("name") or "",
        docker.get("gpus") or "",
        tuple(docker.get("ports") or []),
        tuple(docker.get("volumes") or []),
        tuple(docker.get("command") or []),
        tuple(sorted((docker.get("env") or {}).items())),
    )


def group_nodes_by_job(job: dict, nodes: list[str], data: dict | None = None) -> list[tuple[list[str], dict]]:
    data = data if data is not None else load_nodes_data()
    groups: dict[tuple, list[str]] = {}
    resolved_map: dict[tuple, dict] = {}
    for name in nodes:
        resolved = resolve_job(job, name, data)
        key = fingerprint_job(resolved)
        groups.setdefault(key, []).append(name)
        resolved_map[key] = resolved
    return [(names, resolved_map[key]) for key, names in groups.items()]


def inventory_path_kv(paths: dict[str, str]) -> str:
    parts = []
    for key in sorted(paths):
        value = paths[key].replace(" ", "\\ ")
        parts.append(f"path_{key}={value}")
    return " ".join(parts)
