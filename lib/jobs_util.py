"""已注册 Job、默认任务与可安装模板。

Job 为 ``jobs/*.json`` 插件；内置 ``shell-job``，常用能力见 ``config/examples`` 模板。
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

# 一键安装的推荐模板（不含仅作示例的 domain-job）。
RECOMMENDED_JOB_TEMPLATES: tuple[str, ...] = (
    "shell-job",
    "http-serve",
    "vllm-serve",
    "vllm-docker",
    "train-job",
    "llamafactory-train",
    "deepspeed-train",
    "torchrun-train",
    "ray-cluster",
    "docker-job",
)

_TEMPLATE_CATEGORY: dict[str, str] = {
    "shell-job": "通用",
    "http-serve": "推理",
    "vllm-serve": "推理",
    "vllm-docker": "推理",
    "docker-job": "通用",
    "domain-job": "示例",
    "train-job": "训练",
    "llamafactory-train": "训练",
    "deepspeed-train": "训练",
    "torchrun-train": "训练",
    "ray-cluster": "分布式",
}

JOB_RUNTIMES = frozenset({"process", "docker"})
JOB_KINDS = frozenset({"service", "train"})
REQUIRED_JOB_ACTIONS = ("start", "stop", "status", "wait")


def validate_job_dict(data: dict, *, label: str | None = None) -> list[str]:
    """校验 Job JSON 最小契约；返回问题列表（空表示通过）。

    :param data: Job 对象。
    :param label: 问题前缀（文件名或 Job 名）。
    """
    prefix = f"{label}: " if label else ""
    issues: list[str] = []
    if not isinstance(data, dict):
        return [f"{prefix}Job 必须是 JSON 对象"]
    name = str(data.get("name") or "").strip()
    if not name:
        issues.append(f"{prefix}缺少 name")
    runtime = str(data.get("runtime") or "process").strip() or "process"
    if runtime not in JOB_RUNTIMES:
        issues.append(f"{prefix}runtime 须为 process|docker（当前 {runtime}）")
    kind = str(data.get("kind") or "service").strip() or "service"
    if kind not in JOB_KINDS:
        issues.append(f"{prefix}kind 须为 service|train（当前 {kind}）")
    if runtime == "process":
        if not str(data.get("python") or "").strip():
            issues.append(f"{prefix}process 运行时需要 python")
        if not str(data.get("script") or "").strip():
            issues.append(f"{prefix}process 运行时需要 script")
    elif runtime == "docker":
        docker = data.get("docker")
        if not isinstance(docker, dict):
            issues.append(f"{prefix}docker 运行时需要 docker 对象")
        elif not str(docker.get("image") or "").strip():
            issues.append(f"{prefix}docker.image 必填")
    actions = data.get("actions")
    if actions is not None:
        if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
            issues.append(f"{prefix}actions 须为字符串列表")
        else:
            missing = [a for a in REQUIRED_JOB_ACTIONS if a not in actions]
            if missing:
                issues.append(f"{prefix}actions 缺少 {','.join(missing)}")
    return issues


def validate_registered_jobs() -> list[str]:
    """校验全部已注册 Job 文件。"""
    issues: list[str] = []
    for path in list_job_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(f"{path.name}: 无法解析 JSON（{exc}）")
            continue
        issues.extend(validate_job_dict(data, label=path.name))
    return issues


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
        runtime = job.get("runtime") or "process"
        rows.append({
            "name": name,
            "description": job.get("description") or "",
            "kind": job.get("kind") or "service",
            "gpus_per_node": job.get("gpus_per_node"),
            "distributed": bool(job.get("distributed")),
            "runtime": runtime,
            "wait_semantics": (
                "container_running" if runtime == "docker" else "manager_script"
            ),
            "model": job.get("model") or "",
            "python": job.get("python") or "",
            "script": job.get("script") or "",
            "extra_args": job.get("extra_args") or [],
            "docker": job.get("docker") or None,
            "actions": job.get("actions") or ["start", "stop", "restart", "status", "wait"],
            "path": str(path),
            "source": job_source(path),
            "valid": not validate_job_dict(job, label=name),
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
            if stem in {"cluster", "nodes", "models", "toolkits", "mcp.cursor"} or stem.startswith("mcp"):
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
        kind = str(data.get("kind") or "service")
        category = _TEMPLATE_CATEGORY.get(stem) or ("训练" if kind == "train" else "通用")
        rows.append({
            "id": stem,
            "name": data.get("name") or stem,
            "description": data.get("description") or "",
            "kind": kind,
            "category": category,
            "distributed": bool(data.get("distributed")),
            "gpus_per_node": data.get("gpus_per_node"),
            "runtime": data.get("runtime") or "process",
            "path": str(path),
            "recommended": stem in RECOMMENDED_JOB_TEMPLATES,
            "installed": stem in registered or (data.get("name") or stem) in registered,
        })
    return rows


def install_recommended_templates(*, force: bool = False) -> dict:
    """安装推荐 Job 模板到 ``config/local/jobs/``。

    :param force: 为真时覆盖已安装同名 Job。
    :returns: ``installed`` / ``skipped`` / ``templates`` 汇总。
    """
    registered = set(registered_job_names())
    installed: list[dict] = []
    skipped: list[str] = []
    for template_id in RECOMMENDED_JOB_TEMPLATES:
        if template_id in registered and not force:
            skipped.append(template_id)
            continue
        try:
            installed.append(install_job_template(template_id))
        except FileNotFoundError:
            skipped.append(template_id)
    return {
        "ok": True,
        "installed": installed,
        "skipped": skipped,
        "recommended": list(RECOMMENDED_JOB_TEMPLATES),
        "jobs": load_job_summaries(),
        "templates": list_job_templates(),
    }


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
    problems = validate_job_dict(data, label=dest_name)
    if problems:
        raise ValueError("; ".join(problems))
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
