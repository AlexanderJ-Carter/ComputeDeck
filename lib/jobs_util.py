"""已注册 Job、默认任务与可安装模板。

Job 为 ``jobs/*.json`` 插件；内置仅 ``shell-job``，其余见 ``examples/``。
CLI：``gpu-control jobs``；HTTP：``/api/jobs*``。
"""

from __future__ import annotations

import json
from pathlib import Path

from paths import (
    CONFIG_DIR,
    JOBS_DIR,
    LOCAL_JOBS_DIR,
    PROJECT_DIR,
    cluster_file,
    list_job_files,
)
from state_util import atomic_write_json


EXAMPLES_DIR = CONFIG_DIR / "examples"


def registered_job_names() -> list[str]:
    """已注册 Job 名称列表（stem）。"""
    return [path.stem for path in list_job_files()]


def job_source(path: Path) -> str:
    """判断 Job 来源：``local`` / ``builtin`` / ``other``。"""
    try:
        path.resolve().relative_to(LOCAL_JOBS_DIR.resolve())
        return "local"
    except ValueError:
        pass
    try:
        path.resolve().relative_to(JOBS_DIR.resolve())
        return "builtin"
    except ValueError:
        return "other"


def load_job_summaries() -> list[dict]:
    """加载已注册 Job 摘要列表（供 ``/api/jobs``）。"""
    rows = []
    for path in list_job_files():
        job = json.loads(path.read_text(encoding="utf-8"))
        name = job.get("name") or path.stem
        rows.append({
            "name": name,
            "description": job.get("description") or "",
            "kind": job.get("kind") or "service",
            "gpus_per_node": job.get("gpus_per_node"),
            "distributed": bool(job.get("distributed")),
            "runtime": job.get("runtime") or "process",
            "model": job.get("model") or "",
            "python": job.get("python") or "",
            "script": job.get("script") or "",
            "extra_args": job.get("extra_args") or [],
            "docker": job.get("docker") or None,
            "actions": job.get("actions") or ["start", "stop", "restart", "status", "wait"],
            "path": str(path),
            "source": job_source(path),
        })
    return rows


def default_job_name(fallback: str | None = None) -> str | None:
    """解析默认 Job：优先 ``cluster.default_job``，再 watchdog，再 ``shell-job``。

    :param fallback: 无已注册 Job 时的回落名。
    """
    names = registered_job_names()
    if not names:
        return fallback
    path = cluster_file()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        preferred = (data.get("default_job") or "").strip()
        if preferred and preferred in names:
            return preferred
        watchdog_jobs = ((data.get("watchdog") or {}).get("jobs")) or []
        for name in watchdog_jobs:
            if name in names:
                return name
    # 默认优先通用壳；业务 Job 在 local，不假定 http-serve/train。
    if "shell-job" in names:
        return "shell-job"
    return names[0]


def _template_stem(path: Path) -> str:
    name = path.name
    if name.endswith(".example.json"):
        return name[: -len(".example.json")]
    if name.endswith(".json"):
        return path.stem
    return path.stem


def list_job_templates() -> list[dict]:
    """列出可安装模板（``config/examples`` 与内置 ``jobs/``）。"""
    registered = set(registered_job_names())
    found: dict[str, Path] = {}
    if EXAMPLES_DIR.is_dir():
        for path in sorted(EXAMPLES_DIR.glob("*.json")):
            stem = _template_stem(path)
            # Skip non-job examples.
            if stem in {"cluster", "nodes", "models", "mcp.cursor"} or stem.startswith("mcp"):
                continue
            if stem.endswith(".cursor"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if "script" not in data and "docker" not in data and "runtime" not in data:
                continue
            found[stem] = path
    # Also expose builtins as templates for copying into local.
    if JOBS_DIR.is_dir():
        for path in sorted(JOBS_DIR.glob("*.json")):
            found.setdefault(path.stem, path)

    rows = []
    for stem, path in sorted(found.items()):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "id": stem,
            "name": data.get("name") or stem,
            "description": data.get("description") or "",
            "gpus_per_node": data.get("gpus_per_node"),
            "runtime": data.get("runtime") or "process",
            "path": str(path),
            "installed": stem in registered or (data.get("name") or stem) in registered,
        })
    return rows


def install_job_template(template_id: str, *, name: str | None = None) -> dict:
    """将模板复制到 ``config/local/jobs/<name>.json``。

    :param template_id: 模板 id。
    :param name: 目标 Job 名；默认沿用模板名。
    :returns: 含 ``ok`` / ``path`` 等字段的结果。
    :raises FileNotFoundError: 未知模板。
    :raises ValueError: 名称为空。
    """
    templates = {t["id"]: t for t in list_job_templates()}
    if template_id not in templates:
        raise FileNotFoundError(f"unknown template: {template_id}")
    src = Path(templates[template_id]["path"])
    data = json.loads(src.read_text(encoding="utf-8"))
    old_name = str(data.get("name") or template_id).strip()
    dest_name = (name or old_name or template_id).strip()
    if not dest_name:
        raise ValueError("job name required")
    data["name"] = dest_name
    env = data.get("env")
    if isinstance(env, dict):
        if env.get("GPU_CONTROL_JOB_NAME") == old_name:
            env["GPU_CONTROL_JOB_NAME"] = dest_name
        runtime = env.get("GPU_CONTROL_JOB_RUNTIME")
        if isinstance(runtime, str) and old_name and old_name in runtime:
            env["GPU_CONTROL_JOB_RUNTIME"] = runtime.replace(old_name, dest_name)
    LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_JOBS_DIR / f"{dest_name}.json"
    atomic_write_json(dest, data)
    try:
        relative = str(dest.relative_to(PROJECT_DIR))
    except ValueError:
        relative = str(dest)
    return {
        "ok": True,
        "name": dest_name,
        "path": str(dest),
        "from": str(src),
        "relative": relative,
    }


def ensure_builtin_jobs_visible() -> None:
    """确保内置 ``jobs/`` 目录存在（内置 Job 会自动列入清单）。"""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
